"""`omc review [--<provider>]`: open a fresh audit session for a design record."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import notify
from .agentsmd import ensure_global_section
from .config.schema import Config
from .errors import OmcError, Refusal
from .implement import IMPLEMENT_ALLOWED_TOOLS
from .plugin import ensure_plugin
from .probe import require_tools
from .providers.registry import get_provider
from .session import SessionPlan, run_headless, session_plan
from .shells.registry import detect_shell
from .toolctx import ToolContext
from .wtconfig import branch_for, repo_root, resolve_design_record

REVIEW_SEED = "/omc:audit"


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _print_plan(branch: str, record: str, session_name: str, plan: SessionPlan, notify_desc: str):
    print("omc review — plan (dry run, no changes made):")
    print(f"  branch:       {branch}")
    print(f"  record:       {record}")
    print(f"  session:      {session_name}")
    print(f"  session argv: {plan.session_argv}")
    print(f"  shell argv:   {plan.shell_argv}")
    print(f"  notify:       {notify_desc}")


def run_review(
    ctx: ToolContext, cfg: Config, *, dry_run: bool = False, headless: bool = False
) -> int:
    # This read-only gate must precede probing and plugin repair.
    verdict = resolve_design_record(ctx, cfg)
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
    if not dry_run:
        try:
            ensure_global_section(ctx, name)
        except (OmcError, OSError) as exc:
            _say(f"✗ {name} global instructions: {exc}")
    session_name = f"{slug}-audit"

    provider = get_provider(name)
    plan = session_plan(
        ctx, cfg, seed=REVIEW_SEED, slug=slug, session_name=session_name, title=branch, cwd=root
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
        _say(f"→ running headless {name} session seeded with {REVIEW_SEED}")
        return run_headless(
            ctx,
            cfg,
            REVIEW_SEED,
            root,
            slug,
            session_name=session_name,
            allowed_tools=IMPLEMENT_ALLOWED_TOOLS,
        )
    _say(f'→ launching {name} session "{session_name}" seeded with {REVIEW_SEED}')
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
