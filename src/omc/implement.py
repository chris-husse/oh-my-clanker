"""`omc implement [--<provider>]`: continue an existing worktree's committed
design record in a FRESH session — the provider handoff. Deterministic half
of the lifecycle split (spec 2026-10-02 §3.6): validate the record, then
launch; the implement skill does the rest."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import notify
from .config.schema import Config
from .errors import Refusal
from .plugin import ensure_plugin
from .probe import require_tools
from .providers.registry import get_provider
from .session import SessionPlan, run_headless, session_plan
from .shells.registry import detect_shell
from .slug import MCP_TOOL_PATTERNS
from .toolctx import ToolContext
from .wtconfig import branch_for, repo_root, resolve_design_record

# A print-mode implement run must write, edit, dispatch subagents and run
# skills; design's investigation list (START_ALLOWED_TOOLS) would stall it at
# the first write. Codex ignores allow-lists. Live-verified 2026-10-02 against
# claude 2.1.x: `-p --allowed-tools <this list>` wrote a file headless, and an
# unknown token is ignored, not rejected. Whether subagents inherit the grant
# is what the E2E `implemented` stage shows (spec §3.10 names the fallback).
IMPLEMENT_ALLOWED_TOOLS = [
    *MCP_TOOL_PATTERNS,
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Agent",
    "Task",
    "Skill",
    "TodoWrite",
]

# One line. The launcher has already validated the record and the skill
# recovers it through `omc internal design-record`, so the seed carries no
# data. A `/omc:` seed positional drives the plugin skill on Codex too
# (docker/PLUGIN-NOTES.md, 2026-09-24 rows).
IMPLEMENT_SEED = "/omc:implement"


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _print_plan(branch: str, record: str, session_name: str, plan: SessionPlan, notify_desc: str):
    print("omc implement — plan (dry run, no changes made):")
    print(f"  branch:       {branch}")
    print(f"  record:       {record}")
    print(f"  session:      {session_name}")
    print(f"  session argv: {plan.session_argv}")
    print(f"  shell argv:   {plan.shell_argv}")
    print(f"  notify:       {notify_desc}")


def run_implement(
    ctx: ToolContext, cfg: Config, *, dry_run: bool = False, headless: bool = False
) -> int:
    # The record gate FIRST: it is read-only and answers in milliseconds, while
    # ensure_plugin may install or repair a plugin — never do that for a run
    # that is about to be refused.
    verdict = resolve_design_record(ctx, cfg)  # raises OmcError outside a repo
    if not verdict.ok:
        raise Refusal(verdict.message)
    root = repo_root(ctx)
    assert root is not None  # resolve_design_record raised otherwise
    slug = verdict.slug
    branch = branch_for(cfg, slug)
    _say(f"✓ design record: {verdict.path}")

    name = cfg.llm.default
    _say(f"→ probing tools (git, wt, {name})")
    require_tools(ctx, cfg)
    plugin_status = ensure_plugin(ctx, name, check_only=dry_run)
    _say(f"→ omc plugin for {name}: {plugin_status}")
    # A second `claude -n <slug>` silently forks a new session and makes
    # `--resume <slug>` ambiguous (verified live 2026-10-02); the design
    # session keeps the slug, this one gets its own name. OMC_SLUG stays the
    # slug so the skill's own lookups agree with the verb. Caveat: a second
    # `omc implement` in the same worktree creates another `<slug>-implement`
    # session, so `claude --resume <slug>-implement` is ambiguous then; resume
    # by session id.
    session_name = f"{slug}-implement"

    provider = get_provider(name)
    seed = IMPLEMENT_SEED
    plan = session_plan(
        ctx, cfg, seed=seed, slug=slug, session_name=session_name, title=branch, cwd=root
    )

    if dry_run:
        if cfg.notifications.enabled:
            files = provider.notification_setup(notify.sink_argv(name))
            what = ", ".join(files) or "none (argv only)"
            notify_desc = f"backend {cfg.notifications.backend}; files: {what}"
        else:
            notify_desc = "disabled"
        _print_plan(branch, verdict.path, session_name, plan, notify_desc)
        return 0

    if cfg.notifications.enabled:
        wired = notify.wire_worktree(provider, Path(root))
        if wired:
            _say(f"✓ notification wiring: {', '.join(wired)}")

    if headless:
        _say(f"→ running headless {name} session seeded with {seed}")
        return run_headless(
            ctx,
            cfg,
            seed,
            root,
            slug,
            session_name=session_name,
            allowed_tools=IMPLEMENT_ALLOWED_TOOLS,
        )
    _say(f'→ launching {name} session "{session_name}" seeded with {seed}')
    os.environ.update(plan.env)  # pragma: no cover
    shell = detect_shell(ctx.env)  # pragma: no cover
    shell.exec_interactive(  # pragma: no cover
        cwd=root,
        title=branch,
        startup_argv=plan.session_argv,
        title_seq=plan.title_seq,
        title_argv=plan.title_argv,
    )
    return 0  # pragma: no cover - unreachable after execvp
