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
import sys

from .errors import OmcError
from .installsrc import install_source
from .providers.registry import get_provider
from .toolctx import ToolContext

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
    omc_fix = f"claude plugin marketplace add {source} && claude plugin install {PLUGIN_REF}"
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

    if omc is None:
        _say(f"installing the omc plugin from {source}…")
        # The marketplace may already be registered from an earlier attempt —
        # a failed add is fine as long as the install below succeeds.
        ctx.run(["claude", "plugin", "marketplace", "add", source])
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("installed")
    elif omc_problems:
        _say(
            f"the omc plugin is installed but failed to load ({omc_problems[0]}) "
            f"— reinstalling from {source}…"
        )
        # Refresh the marketplace snapshot first so the reinstall picks up a
        # fixed manifest; add/update/uninstall are best-effort self-heal steps.
        ctx.run(["claude", "plugin", "marketplace", "add", source])
        ctx.run(["claude", "plugin", "marketplace", "update", MARKETPLACE_NAME])
        ctx.run(["claude", "plugin", "uninstall", PLUGIN_REF])
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("repaired")
    elif update:
        argvs = get_provider("claude").plugin_update_argvs(source)
        for i, argv in enumerate(argvs):
            cp = ctx.run(argv)
            # Non-last (marketplace add/update) failures are benign self-heal
            # steps; only the final `plugin update` decides.
            if cp.returncode != 0 and i == len(argvs) - 1:
                detail = (cp.stderr or cp.stdout or "").strip()[:200]
                raise OmcError(f"`{' '.join(argv)}` failed: {detail}")
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
