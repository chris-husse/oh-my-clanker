"""Plugin self-heal: the seeded /omc:* commands must actually resolve.

`omc start` seeds the session with `/omc:start`; if the omc plugin is missing
— or installed but refused by Claude ("failed to load") — the session opens on
"Unknown command", the worst possible first-run. So start/update/configure
ensure the plugin is installed AND healthy, installing or reinstalling it
automatically. No consent prompt: the plugin is omc's own pinned repo, the
same trust model as the CLI install itself (chicken precedent for
pinned-source dependencies).

superpowers is handled here too. omc's start skill hands off to it, but the
manifest deliberately declares NO plugin dependency: Claude Code resolves a
dependency by its exact marketplace-qualified id, so a manifest pinned to
`superpowers@superpowers-marketplace` made Claude refuse to load omc on every
machine whose superpowers came from `claude-plugins-official` — and Claude
never installs a dependency for you anyway. Verified 2026-09-02 against
claude 2.1.x: with the dependency dropped and superpowers installed from the
official marketplace, `claude plugin list --json` reports omc with no errors.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import OmcError
from .installsrc import install_source
from .toolctx import ToolContext
from .wtconfig import repo_root

PLUGIN_REF = "omc@oh-my-clanker"
MARKETPLACE_NAME = "oh-my-clanker"
SUPERPOWERS_REF = "superpowers@claude-plugins-official"
_OFFICIAL_MARKETPLACE = "anthropics/claude-plugins-official"
_FALLBACK_SOURCE = "chris-husse/oh-my-clanker"


def marketplace_source(env) -> str:
    """Where `claude plugin marketplace add` should pull omc from: the same
    place uv installed the CLI from — a directory checkout stays local, a
    GitHub install uses owner/repo, anything else falls back to the canonical
    public repo."""
    src, is_remote = install_source(env)
    if is_remote:
        m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", src)
        if m:
            return m.group(1)
    elif src != "unknown" and not src.endswith("(PyPI)"):
        return src
    return _FALLBACK_SOURCE


def _say(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def _list_plugins(ctx: ToolContext) -> list[dict]:
    """`claude plugin list --json`: one entry per installed plugin with ``id``,
    ``enabled`` and — for a plugin Claude refused to load — an ``errors`` list.
    The human listing only carries the same facts as prose; the JSON is the
    contract (verified 2026-09-02, claude 2.1.x)."""
    argv = ["claude", "plugin", "list", "--json"]
    try:
        cp = ctx.run(argv)
    except OSError as exc:
        raise OmcError(f"could not run `{' '.join(argv)}`: {exc}") from exc
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(f"`{' '.join(argv)}` failed (exit {cp.returncode}): {detail}")
    try:
        data = json.loads(cp.stdout or "")
    except json.JSONDecodeError as exc:
        raise OmcError(
            f"could not parse the output of `{' '.join(argv)}`: {exc}\n"
            f"  output was: {(cp.stdout or '').strip()[:200]!r}"
        ) from exc
    if not isinstance(data, list):
        raise OmcError(f"unexpected `{' '.join(argv)}` output (expected a JSON array)")
    return [e for e in data if isinstance(e, dict)]


def _find(entries: list[dict], name: str) -> dict | None:
    """The installed plugin named ``name`` from ANY marketplace (ids are
    ``name@marketplace``)."""
    return next((e for e in entries if str(e.get("id", "")).startswith(name + "@")), None)


def _problems(entry: dict) -> list[str]:
    """Why Claude won't serve this plugin's skills: load errors, or disabled."""
    problems = [str(e) for e in (entry.get("errors") or [])]
    if entry.get("enabled") is False:
        problems.append("the plugin is disabled")
    return problems


def _install(ctx: ToolContext, ref: str, *, manual_fix: str) -> None:
    cp = ctx.run(["claude", "plugin", "install", ref, "--scope", "user"])
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(
            f"could not install {ref} for claude automatically.\n"
            f"  {detail}\n"
            f"  fix manually: {manual_fix}"
        )


def _checked(ctx: ToolContext, argv: list[str]) -> str:
    try:
        cp = ctx.run(argv)
    except OSError as exc:
        raise OmcError(f"could not run `{shlex.join(argv)}`: {exc}") from exc
    if cp.returncode:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(f"`{shlex.join(argv)}` failed (exit {cp.returncode}): {detail}")
    return cp.stdout or ""


def _claude_json(ctx: ToolContext, argv: list[str]):
    output = _checked(ctx, argv)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise OmcError(f"could not parse `{shlex.join(argv)}` output as JSON") from exc


def _available(ctx: ToolContext) -> None:
    data = _claude_json(ctx, ["claude", "plugin", "list", "--available", "--json"])
    available = data.get("available") if isinstance(data, dict) else None
    if not isinstance(available, list) or not any(
        isinstance(p, dict) and p.get("pluginId") == PLUGIN_REF for p in available
    ):
        raise OmcError(f"refusing plugin replacement: marketplace does not offer {PLUGIN_REF}")


def _source_matches(entry: dict, source: str) -> bool:
    if entry.get("source") == "directory" and source.startswith("/"):
        return Path(str(entry.get("path", ""))).resolve() == Path(source).resolve()
    return entry.get("source") == "github" and entry.get("repo") == source


def _check_project_source(ctx: ToolContext, source: str) -> None:
    root = repo_root(ctx)
    if root is None:
        return
    declaration = None
    for name in ("settings.json", "settings.local.json"):
        path = Path(root) / ".claude" / name
        try:
            settings = json.loads(path.read_text())
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise OmcError(f"cannot inspect project marketplace settings at {path}: {exc}") from exc
        declarations = (
            settings.get("extraKnownMarketplaces", {}) if isinstance(settings, dict) else {}
        )
        entry = declarations.get(MARKETPLACE_NAME) if isinstance(declarations, dict) else None
        if entry is not None:
            declaration = (path, entry.get("source") if isinstance(entry, dict) else None)
    if declaration is not None:
        path, declared = declaration
        if not isinstance(declared, dict) or not _source_matches(declared, source):
            raise OmcError(
                f"project marketplace declaration in {path} conflicts with {source}; "
                "update that declaration before replacing the user marketplace"
            )


def _prepare_marketplace(ctx: ToolContext, source: str) -> bool:
    """Refresh the intended source; return whether removal also removed plugins.

    Claude 2.1.281 rejects `add` when settings declare a different source.
    `marketplace remove` also UNINSTALLS its plugins, so prove the replacement
    can be fetched and installed in a disposable config before doing that.
    Never edit Claude's registry/settings files ourselves.
    """
    prefix = ["claude", "plugin", "marketplace"]
    entries = _claude_json(ctx, [*prefix, "list", "--json"])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise OmcError("unexpected `claude plugin marketplace list --json` output")
    current = next((e for e in entries if e.get("name") == MARKETPLACE_NAME), None)
    replaced = current is not None and not _source_matches(current, source)
    if replaced:
        _say(f"validating replacement marketplace from {source}…")
        with TemporaryDirectory(prefix="omc-marketplace-") as scratch:
            # Isolate user settings/cache, but retain cwd: project declarations
            # must constrain the preflight just as they constrain the live add.
            probe = ToolContext.from_env({**ctx.child_env(), "CLAUDE_CONFIG_DIR": scratch})
            _checked(probe, [*prefix, "add", source])
            _available(probe)
            _checked(
                probe,
                ["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"],
            )
            plugins = _claude_json(probe, ["claude", "plugin", "list", "--json"])
            plugin = _find(plugins, "omc") if isinstance(plugins, list) else None
            if plugin is None or _problems(plugin):
                raise OmcError("replacement omc plugin failed its isolated load check")
        _say("replacing the stale oh-my-clanker marketplace registration…")
        _checked(ctx, [*prefix, "remove", MARKETPLACE_NAME, "--scope", "user"])
    if current is None or replaced:
        _checked(ctx, [*prefix, "add", source])
        actual = _claude_json(ctx, [*prefix, "list", "--json"])
        if not isinstance(actual, list) or not any(
            isinstance(e, dict) and e.get("name") == MARKETPLACE_NAME and _source_matches(e, source)
            for e in actual
        ):
            raise OmcError("oh-my-clanker marketplace still points at a different source")
    if not replaced:
        _checked(ctx, [*prefix, "update", MARKETPLACE_NAME])
    _available(ctx)
    return replaced


def ensure_plugin(
    ctx: ToolContext, provider: str, *, check_only: bool = False, update: bool = False
) -> str:
    """Ensure the omc plugin (and superpowers) is installed and healthy for
    ``provider``.

    Missing → install. Installed but failed to load / disabled → refresh the
    marketplace snapshot and reinstall. Healthy and ``update`` → run the
    provider's update sequence. Every mutating path re-probes afterwards and
    raises ``OmcError`` (carrying Claude's own error text) if the plugin still
    doesn't load — a broken plugin must never be reported as "ok".

    Returns a short status string for the progress/plan output. Only claude
    has a verified scriptable probe + install today; other providers are left
    alone. ``check_only`` (the --dry-run path) never installs anything.
    """
    if provider != "claude":
        return "unverified (no scriptable check for this provider yet)"

    entries = _list_plugins(ctx)
    omc = _find(entries, "omc")
    superpowers = _find(entries, "superpowers")
    omc_problems = _problems(omc) if omc is not None else []

    if check_only:
        if omc is None:
            return "missing (omc start will install it)"
        if omc_problems:
            return f"failed to load: {omc_problems[0]} (omc start will reinstall it)"
        if superpowers is None:
            return "ok; superpowers missing (omc start will install it)"
        return "ok"

    source = marketplace_source(ctx.env)
    if omc is None or omc_problems or update:
        _check_project_source(ctx, source)
    omc_fix = (
        f"claude plugin marketplace add {shlex.quote(source)} && "
        f"claude plugin install {PLUGIN_REF} --scope user"
    )
    actions: list[str] = []

    if superpowers is None:
        _say("installing superpowers (omc's start skill hands off to it)…")
        # Best-effort: the official marketplace is usually pre-registered.
        ctx.run(["claude", "plugin", "marketplace", "add", _OFFICIAL_MARKETPLACE])
        _install(
            ctx,
            SUPERPOWERS_REF,
            manual_fix=(
                f"claude plugin marketplace add {_OFFICIAL_MARKETPLACE} && "
                f"claude plugin install {SUPERPOWERS_REF}"
            ),
        )
        actions.append("installed superpowers")

    replaced = False
    if omc is None or omc_problems or update:
        try:
            replaced = _prepare_marketplace(ctx, source)
        except OmcError as exc:
            raise OmcError(
                f"{exc}\n  if the old marketplace is still registered, remove it first: "
                f"claude plugin marketplace remove {MARKETPLACE_NAME} --scope user\n"
                f"  fix manually: {omc_fix}"
            ) from exc

    if omc is None:
        _say(f"installing the omc plugin from {source}…")
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("installed")
    elif omc_problems:
        _say(
            f"the omc plugin is installed but failed to load ({omc_problems[0]}) "
            f"— reinstalling from {source}…"
        )
        if not replaced:
            _checked(ctx, ["claude", "plugin", "uninstall", PLUGIN_REF])
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("repaired")
    elif update:
        if replaced:
            _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        else:
            _checked(ctx, ["claude", "plugin", "update", PLUGIN_REF])
        actions.append("updated")

    if not actions:
        return "ok"

    omc = _find(_list_plugins(ctx), "omc")
    if omc is None:
        raise OmcError(
            "the omc plugin is still missing after an apparently successful install — "
            "check `claude plugin list` and the plugin's Status line"
        )
    problems = _problems(omc)
    if problems:
        joined = "\n  ".join(problems)
        raise OmcError(
            "the omc plugin still fails to load after reinstalling it:\n"
            f"  {joined}\n"
            "  check `claude plugin list`; if the error names a missing dependency, "
            "your marketplace snapshot may predate the fix — retry after `omc update`"
        )
    return actions[-1]
