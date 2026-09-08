"""Plugin self-heal: the seeded /omc:* commands must actually resolve.

`omc start` seeds the session with `/omc:start`; if the omc plugin is missing
— or installed but refused by Claude ("failed to load") — the session opens on
"Unknown command", the worst possible first-run. So start/update/configure
ensure the plugin is installed AND healthy, installing or reinstalling it
automatically. No consent prompt: the plugin is omc's own pinned repo, the
same trust model as the CLI install itself (chicken precedent for
pinned-source dependencies).

This module only EXECUTES; each provider adapter decides. It runs the
adapter's probe argvs, hands the output back for parsing, then runs the repair
plan it returns — so no harness-specific command or id appears here, and a new
provider is taught to self-heal by implementing those three members alone.

superpowers is part of the same self-heal (claude's adapter plans its install).
omc's start skill hands off to it, but the manifest deliberately declares NO
plugin dependency: Claude Code resolves a
dependency by its exact marketplace-qualified id, so a manifest pinned to
`superpowers@superpowers-marketplace` made Claude refuse to load omc on every
machine whose superpowers came from `claude-plugins-official` — and Claude
never installs a dependency for you anyway. Verified 2026-09-02 against
claude 2.1.x: with the dependency dropped and superpowers installed from the
official marketplace, `claude plugin list --json` reports omc with no errors.
"""

from __future__ import annotations

import re
import sys
from dataclasses import replace

from .errors import OmcError
from .installsrc import install_source
from .providers.base import PluginFacts, Provider
from .providers.registry import get_provider
from .toolctx import ToolContext

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


def _probe(ctx: ToolContext, provider: Provider, argvs: list[list[str]]) -> PluginFacts:
    """Run the probe commands and parse them into facts. Any failure is an
    OmcError carrying the harness's own text — a probe we can't read is never
    silently treated as "healthy"."""
    stdouts = []
    for argv in argvs:
        try:
            cp = ctx.run(argv)
        except OSError as exc:
            raise OmcError(f"could not run `{' '.join(argv)}`: {exc}") from exc
        if cp.returncode != 0:
            detail = (cp.stderr or cp.stdout or "").strip()
            raise OmcError(f"`{' '.join(argv)}` failed (exit {cp.returncode}): {detail}")
        stdouts.append(cp.stdout or "")
    try:
        return provider.parse_plugin_facts(stdouts)
    except (ValueError, KeyError, TypeError) as exc:
        # Parsers raise terse, provider-local errors (json's JSONDecodeError is
        # a ValueError). parse_plugin_facts consumes ALL the probe outputs in
        # ONE call, so which command produced the unreadable one is genuinely
        # unknowable here — naming every probe and quoting every output is the
        # honest report. Blaming argvs[0] (as this did until codex became the
        # first multi-probe provider) names the WRONG command and quotes a
        # HEALTHY payload whenever a later probe is the broken one.
        commands = ", ".join(f"`{' '.join(a)}`" for a in argvs)
        # 200-char cap per output so one large payload can't flood the error.
        quoted = "\n".join(
            f"  `{' '.join(a)}` printed: {o.strip()[:200]!r}"
            for a, o in zip(argvs, stdouts, strict=True)
        )
        raise OmcError(f"could not parse the output of {commands}: {exc}\n{quoted}") from exc


def _describe(provider: Provider, facts: PluginFacts, source: str) -> str:
    """The --dry-run status: what `omc start` WOULD do, without doing it.

    Whether anything WILL run is decided by the provider's own plan, not by a
    second reading of the facts here. That duplication drifted the moment a
    provider grew a third input: codex also repairs a marketplace registered
    from a different source, so a facts-only description printed "ok" for a
    machine the real `omc start` re-registers and reinstalls. An empty plan is
    the only definition of "nothing to do" that cannot disagree with the run.

    Still mutates nothing: plugin_repair_argvs is state-in/commands-out (pure),
    and the probes that produced ``facts`` are contractually side-effect-free.
    """
    if not provider.plugin_repair_argvs(facts, source=source, update=False):
        return "ok"
    if facts.omc is None:
        return "missing (omc start will install it)"
    if facts.omc.problems:
        return f"failed to load: {facts.omc.problems[0]} (omc start will reinstall it)"
    # A plan with a healthy omc means either superpowers is missing or the
    # provider found a reason of its own. Ask it again with superpowers
    # pretended present to tell the two apart — re-deriving "is superpowers
    # the whole story?" from the facts is exactly the duplication above.
    if not provider.plugin_repair_argvs(
        replace(facts, superpowers=facts.omc), source=source, update=False
    ):
        return "ok; superpowers missing (omc start will install it)"
    # codex's third input: the registered marketplace source differs from the
    # one omc resolves, which `omc start` fixes by re-registering the
    # marketplace (that also drops its plugins) and reinstalling omc.
    registered = facts.marketplace_source or "another source"
    return f"registered from {registered} (omc start will re-register it from {source})"


def ensure_plugin(
    ctx: ToolContext, provider: str, *, check_only: bool = False, update: bool = False
) -> str:
    """Ensure the omc plugin (and superpowers) is installed and healthy for
    ``provider``.

    Missing → install. Installed but failed to load / disabled → refresh the
    marketplace snapshot and reinstall. Healthy and ``update`` → run the
    provider's update sequence. Every mutating path re-probes afterwards and
    raises ``OmcError`` (carrying the harness's own error text) if the plugin
    still doesn't load — a broken plugin must never be reported as "ok".

    Returns a short status string for the progress/plan output. Providers with
    no scriptable probe (``plugin_probe_argvs() == []``) are left alone.
    ``check_only`` (the --dry-run path) never installs anything.
    """
    p = get_provider(provider)
    argvs = p.plugin_probe_argvs()
    if not argvs:
        # The "unverified" PREFIX is a display contract: configure.py marks
        # such a provider with "·" instead of "✓".
        return "unverified (no scriptable check for this provider yet)"

    facts = _probe(ctx, p, argvs)
    # Before the check_only return: the description is derived from the same
    # plan the real run would execute, and the plan needs the source.
    source = marketplace_source(ctx.env)
    if check_only:
        return _describe(p, facts, source)

    plan = p.plugin_repair_argvs(facts, source=source, update=update)
    if not plan:
        return "ok"

    for step in plan:
        if step.label:
            _say(step.label)
        cp = ctx.run(step.argv)
        if cp.returncode != 0 and step.fatal:
            detail = (cp.stderr or cp.stdout or "").strip()
            raise OmcError(
                f"could not run `{' '.join(step.argv)}` automatically.\n"
                f"  {detail}\n"
                f"  fix manually: {step.manual_fix}"
            )

    facts = _probe(ctx, p, argvs)
    if facts.omc is None:
        raise OmcError(
            "the omc plugin is still missing after an apparently successful install — "
            "check the harness's plugin list and the plugin's Status line"
        )
    if facts.omc.problems:
        joined = "\n  ".join(facts.omc.problems)
        raise OmcError(
            "the omc plugin still fails to load after reinstalling it:\n"
            f"  {joined}\n"
            "  check the harness's plugin list; if the error names a missing dependency, "
            "your marketplace snapshot may predate the fix — retry after `omc update`"
        )
    # A plan whose steps carry no action ran only best-effort self-heal.
    actions = [s.action for s in plan if s.action]
    return actions[-1] if actions else "ok"
