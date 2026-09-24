from __future__ import annotations

import json
import re

from .base import PluginEntry, PluginFacts, Provider, RepairStep

CODEX_PLUGIN_REF = "omc@oh-my-clanker"
CODEX_MARKETPLACE_NAME = "oh-my-clanker"
# codex's own curated superpowers entry is admin-blocked on managed machines
# (`marketplace add` exits 1), so omc registers obra's marketplace directly —
# the route verified to work on a locked-down host.
SUPERPOWERS_SOURCE = "obra/superpowers-marketplace"
CODEX_SUPERPOWERS_REF = "superpowers@superpowers-marketplace"


# codex's client-side warning for an unknown model slug, quoted verbatim from
# a 0.153.4 run: "warning: Model metadata for `astra` not found. Defaulting to
# fallback metadata; this can degrade performance and cause issues." Backticks
# are codex's own quoting, not markdown.
_UNKNOWN_MODEL_RE = re.compile(r"Model metadata for `(?P<model>[^`]+)` not found")


def _is_local_source(source: str) -> bool:
    """A filesystem path, not an owner/repo or git URL. Local installs are
    COPIES into a version-pinned dir, so an unchanged version keeps serving
    stale skills — the update path removes before re-adding."""
    return source.startswith(("/", "./", "~"))


_GITHUB_REPO = re.compile(
    r"^(?:(?:https?|ssh|git\+ssh)://)?(?:git@)?github\.com[:/](?P<repo>[^/]+/[^/]+)$"
)


def _canonical_source(source: str) -> str:
    """A marketplace source reduced to a form two spellings can be compared in.

    MEASURED (codex-cli 0.153.4, isolated CODEX_HOME): codex does NOT echo back
    the source you give it — it normalises owner/repo to a full git URL.

        $ codex plugin marketplace add chris-husse/oh-my-clanker
        Added marketplace `oh-my-clanker` from
          https://github.com/chris-husse/oh-my-clanker.git.
        $ codex plugin marketplace list --json
        {"marketplaces":[{"name":"oh-my-clanker",
          "root":"<CODEX_HOME>/.tmp/marketplaces/oh-my-clanker",
          "marketplaceSource":{"sourceType":"git",
            "source":"https://github.com/chris-husse/oh-my-clanker.git"}}]}

    marketplace_source() computes `owner/repo`, so a raw `!=` against the stored
    string is ALWAYS true for the default GitHub-installed user: every
    `omc start` would remove the marketplace, re-add it and reinstall the
    plugin, reporting "repaired" — silently, forever. That is worse than the
    "Unknown command" bug this whole branch exists to fix.

    Local paths ARE echoed verbatim (measured separately), so they compare
    as-is. This normalisation is invisible in the CLI's own output shape — do
    not "simplify" this helper away.
    """
    trimmed = source.strip().removesuffix("/").removesuffix(".git")
    if _is_local_source(trimmed):
        return trimmed
    m = _GITHUB_REPO.match(trimmed)
    if m:
        # GitHub owner/repo is case-insensitive, and the case omc reads out of
        # the uv receipt need not match what the user typed into codex — a
        # case-only difference would be one more false conflict.
        return m.group("repo").lower()
    if re.fullmatch(r"[^/:]+/[^/:]+", trimmed):
        return trimmed.lower()  # bare owner/repo: codex's GitHub shorthand
    return trimmed


def _codex_json(raw: str) -> dict:
    """One codex `--json` payload as a mapping.

    The whole point of this function is ONE distinction:

    * a well-formed payload reporting nothing installed (``{}``, an absent key,
      a ``null``, an empty array) is a FRESH MACHINE — exactly the machine that
      needs the install — so it must parse cleanly to all-``None`` facts.
      plugin.py:_probe makes any parse exception fatal, and on the `omc start`
      path a raise here would block the session instead of fixing it.
    * empty stdout, or anything that isn't a JSON object, is a BROKEN PROBE —
      the CLI printed nothing, or the contract moved. Raising is right: _probe
      wraps it into an OmcError that names the argv and quotes the output, and
      omc mutates nothing. Masking it instead makes codex read as "nothing
      installed", so omc runs four mutating commands and only THEN fails with
      the far more confusing "still missing after an apparently successful
      install" (measured — see task-4-report.md).
    """
    data = json.loads(raw)  # "" is not well-formed; let JSONDecodeError fly
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _codex_entry(installed: list[dict], name: str) -> PluginEntry | None:
    raw = next((e for e in installed if e.get("name") == name), None)
    if raw is None:
        return None
    # No `errors` field exists in codex's JSON (verified 0.153.4) — there is
    # no load-error channel, so `installed`/`enabled` are the whole signal.
    # Absent `enabled` counts as enabled: only an explicit False disables.
    enabled = raw.get("enabled") is not False
    problems = [] if enabled else ["the plugin is disabled"]
    if raw.get("installed") is False:
        problems.append("the plugin is not installed")
    return PluginEntry(
        id=str(raw.get("pluginId") or f"{name}@?"), enabled=enabled, problems=tuple(problems)
    )


class CodexProvider(Provider):
    name = "codex"

    def models(self):
        # No static list: codex model slugs move fast, and the real catalog is
        # obtainable at runtime — see model_catalog_argv. This stays [] as the
        # last-resort fallback when `codex debug models` cannot be run.
        return []

    def model_catalog_argv(self):
        # `codex debug models` — "Render the raw model catalog as JSON"
        # (verified 0.153.4: exit 0, clean JSON on stdout). It refreshes from
        # the network and falls back to the catalog bundled in the binary, so
        # it works offline too; `--bundled` would skip the refresh entirely
        # but would also hide newly released models, so we take the refresh.
        return ["codex", "debug", "models"]

    def parse_model_catalog(self, stdout):
        # {"models": [{slug, display_name, visibility, priority, …}]}.
        # `visibility` separates the 5 user-selectable models from internal
        # ones (gpt-reserve, codex-auto-review are "hide") — offering those
        # in the picker would hand the user a model that cannot serve them.
        models = json.loads(stdout)["models"]
        listable = [m for m in models if m.get("visibility") == "list"]
        # `priority` orders the catalog as codex's own UI presents it; a
        # missing priority sorts last rather than crashing the picker.
        listable.sort(key=lambda m: m.get("priority") if m.get("priority") is not None else 1e9)
        return [str(m["slug"]) for m in listable if m.get("slug")]

    def explain_failure(self, output):
        # codex does NOT reject an unknown model: it warns, then issues a real
        # API call that comes back as a generic 400 blaming "an organization or
        # application policy, or a configuration issue" (measured 0.153.4).
        # The warning is the only honest signal, so match it and say the true
        # cause — otherwise a typo'd slug reads as an outage or a quota problem.
        m = _UNKNOWN_MODEL_RE.search(output)
        if m is None:
            return None
        return (
            f"codex does not know the model {m.group('model')!r} — it warned "
            "'Model metadata not found' and then failed the request with a "
            "generic 400.\n"
            "  fix: run `omc configure` and pick a model from the list, or\n"
            "       `codex debug models` to see the valid slugs"
        )

    def headless_argv(self, prompt, *, model, allowed_tools=None, session_name=""):
        # `codex exec` is the non-interactive entry point; prompt is the trailing
        # positional; -m is the model flag. allowed_tools has no codex equivalent.
        # --skip-git-repo-check: verified against codex 0.144 — without it, exec
        # refuses to run in a directory the user hasn't interactively trusted,
        # which a headless one-shot call can never satisfy.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        argv.append(prompt)
        return argv

    def session_argv(self, *, session_name, model, seed, notify_sink_argv=None):
        # No session-name flag exists — codex names sessions internally; omc's
        # terminal title carries the slug instead.
        argv = ["codex"]
        if model:
            argv += ["-m", model]
        if notify_sink_argv:
            # -c overrides one config.toml key for THIS session only (the global
            # config is never touched). The value is TOML — a JSON array of
            # strings happens to be valid TOML array syntax. Must precede the
            # seed: the prompt is a trailing positional.
            argv += ["-c", f"notify={json.dumps(notify_sink_argv)}"]
        argv.append(seed)
        return argv

    def title_env(self):
        return {}  # no suppression env exists; our OSC write happens after codex starts

    def install_hint(self):
        return "npm install -g @openai/codex"

    def plugin_probe_argvs(self):
        # Two probes: the marketplace list carries the REGISTERED SOURCE, which
        # the plugin list does not. Both take --json (verified 0.153.4).
        # Order matters — parse_plugin_facts reads them positionally. Both are
        # read-only listings, as plugin_probe_argvs' contract requires: --dry-run
        # runs them before it decides to do nothing.
        return [
            ["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "list", "--json"],
        ]

    def parse_plugin_facts(self, stdouts):
        # _codex_json draws the line this parser lives or dies by: a payload
        # that REPORTS nothing installed parses to all-None facts (a fresh
        # machine must reach the install, not an error), while EMPTY STDOUT
        # raises (a silent CLI is a broken probe — never mutate on it).
        markets = _codex_json(stdouts[0]).get("marketplaces") or []
        markets = [m for m in markets if isinstance(m, dict)]
        ours = next((m for m in markets if m.get("name") == CODEX_MARKETPLACE_NAME), None)
        source = None
        if ours:
            # NO `root` fallback. For a git marketplace `root` is a local CACHE
            # path (measured: "<CODEX_HOME>/.tmp/marketplaces/oh-my-clanker"),
            # never the source — comparing it would manufacture a conflict on
            # every start. An absent source means UNKNOWN, and unknown claims
            # no conflict (see plugin_repair_argvs).
            source = (ours.get("marketplaceSource") or {}).get("source")
        # `installed` only; `available` was empty even with uninstalled remote
        # plugins present, and path/git marketplaces are omitted from the JSON
        # entirely until their plugin is installed — so "absent" is the only
        # readable pre-install state.
        installed = _codex_json(stdouts[1]).get("installed") or []
        installed = [e for e in installed if isinstance(e, dict)]
        return PluginFacts(
            omc=_codex_entry(installed, "omc"),
            superpowers=_codex_entry(installed, "superpowers"),
            marketplace_source=str(source) if source else None,
        )

    def plugin_repair_argvs(self, facts, *, source, update):
        omc_fix = f"codex plugin marketplace add {source} && codex plugin add {CODEX_PLUGIN_REF}"
        # A same-named marketplace from a DIFFERENT source is refused with
        # exit 1 ("remove it before adding this source"), so re-register.
        #
        # Compare CANONICALISED sources, and when canonicalisation cannot prove
        # a difference claim no conflict. The asymmetry is deliberate: a false
        # positive churns the user's config on every single start, silently and
        # forever, while a false negative costs one loud `marketplace add`
        # exit 1 carrying an actionable manual_fix. Loud beats silent churn.
        # `marketplace_source is None` (unknown) therefore also means "no
        # conflict" rather than "re-register to be safe".
        conflict = facts.marketplace_source is not None and _canonical_source(
            facts.marketplace_source
        ) != _canonical_source(source)
        needs_omc = facts.omc is None or facts.omc.problems or conflict
        steps: list[RepairStep] = []
        # superpowers FIRST, matching claude: ensure_plugin reports the LAST
        # non-empty `action`, so planning it after the omc block made a fresh
        # codex machine report "installed superpowers" — as though omc's own
        # plugin never installed. omc's action has to be the last one.
        if facts.superpowers is None:
            steps += [
                RepairStep(
                    ["codex", "plugin", "marketplace", "add", SUPERPOWERS_SOURCE],
                    "installing superpowers (omc's start skill hands off to it)…",
                    # Best-effort: it may already be registered from an earlier run.
                    fatal=False,
                ),
                RepairStep(
                    ["codex", "plugin", "add", CODEX_SUPERPOWERS_REF],
                    "",
                    fatal=True,
                    action="installed superpowers",
                    manual_fix=(
                        f"codex plugin marketplace add {SUPERPOWERS_SOURCE} && "
                        f"codex plugin add {CODEX_SUPERPOWERS_REF}"
                    ),
                ),
            ]
        elif facts.superpowers.problems:
            # Present but not serving skills. codex has no `enable` verb, and
            # re-adding an installed plugin re-enables it (verified live at
            # 0.153.4 against omc@oh-my-clanker: enabled False -> True).
            # Re-add by its OWN id, not CODEX_SUPERPOWERS_REF: a superpowers
            # from any marketplace satisfies the check, and adding obra's copy
            # over a curated-catalog one would install a second source.
            steps.append(
                RepairStep(
                    ["codex", "plugin", "add", facts.superpowers.id],
                    f"superpowers is installed but not serving skills "
                    f"({facts.superpowers.problems[0]}) — re-enabling…",
                    fatal=True,
                    action="enabled superpowers",
                    manual_fix=f"codex plugin add {facts.superpowers.id}",
                )
            )
        if conflict:
            steps.append(
                RepairStep(
                    ["codex", "plugin", "marketplace", "remove", CODEX_MARKETPLACE_NAME],
                    f"the {CODEX_MARKETPLACE_NAME} marketplace points at "
                    f"{facts.marketplace_source} — re-registering from {source}…",
                    fatal=False,
                )
            )
        if needs_omc:
            steps.append(
                RepairStep(
                    ["codex", "plugin", "marketplace", "add", source],
                    f"installing the omc plugin from {source}…",
                    # Fatal: a same-source re-add exits 0 ("already added"), so a
                    # failure here is a real one — a bad source or a blocked host.
                    fatal=True,
                    manual_fix=omc_fix,
                )
            )
            if update:
                # `omc update` has just upgraded the CLI, so the reinstall below
                # must not come off a stale snapshot. It would: a same-source
                # `marketplace add` exits 0 without fetching ("already added"),
                # and codex's install root is VERSION-PINNED — re-adding an
                # unchanged version re-enables the old copy verbatim. Without
                # this the unhealthy-plugin path reports "repaired" while
                # leaving CLI 0.1.8 running against 0.1.7 skills. Gated on
                # `update` because it fetches: a fresh `omc start` must not pay
                # for the network round-trip. (claude does the equivalent in
                # its own repair branch.)
                steps.append(
                    RepairStep(["codex", "plugin", "marketplace", "upgrade"], "", fatal=False)
                )
            # `or conflict` in needs_omc is load-bearing: `marketplace remove`
            # also removes that marketplace's installed plugins (verified — no
            # zombie state), and `facts` predates the removal, so omc reads as
            # "present" while actually being gone. Without it codex is left with
            # NO omc plugin at all.
            steps.append(
                RepairStep(
                    ["codex", "plugin", "add", CODEX_PLUGIN_REF],
                    "",
                    fatal=True,
                    action="repaired" if facts.omc is not None else "installed",
                    manual_fix=omc_fix,
                )
            )
        if update and not needs_omc:
            if _is_local_source(source):
                # `codex plugin add` is idempotent, which for a local source
                # means it is also a no-op: the install COPIED the checkout into
                # a version-pinned dir, so an unchanged version keeps serving
                # stale skills. Remove first to force a fresh copy.
                steps.append(
                    RepairStep(
                        ["codex", "plugin", "remove", CODEX_PLUGIN_REF],
                        "refreshing the omc plugin copy…",
                        fatal=False,
                    )
                )
            steps += [
                # Refreshes ALL git marketplace snapshots (no per-marketplace
                # filter exists); a no-op for a local source, hence best-effort.
                RepairStep(["codex", "plugin", "marketplace", "upgrade"], "", fatal=False),
                RepairStep(
                    ["codex", "plugin", "add", CODEX_PLUGIN_REF],
                    "",
                    fatal=True,
                    action="updated",
                    manual_fix=omc_fix,
                ),
            ]
        return steps
