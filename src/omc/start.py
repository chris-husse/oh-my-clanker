"""`omc start <context>`: probe -> slug -> worktree -> seeded handoff."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import notify, worktree
from .agentsmd import ensure_agents_chain
from .config.schema import Config
from .errors import OmcError
from .plugin import ensure_plugin
from .probe import require_tools
from .providers.registry import get_provider
from .shells.registry import detect_shell
from .slug import fetch_slug
from .terminals import detect_terminal
from .toolctx import ToolContext
from .watchlock import busy_lock, wait_until_idle
from .wtconfig import repo_root


def _print_plan(branch, base, wt_argv, title_seq, session_argv, shell_argv, notify_desc):
    print("omc start — plan (dry run, no changes made):")
    print(f"  branch:       {branch}")
    print(f"  fetch:        git fetch origin {base}")
    print(f"  worktree cmd: {shlex.join(wt_argv)}")
    print(f"  title seq:    {title_seq!r}")
    print(f"  session argv: {session_argv}")
    print(f"  shell argv:   {shell_argv}")
    print(f"  notify:       {notify_desc}")


def _run_headless(ctx: ToolContext, cfg: Config, seed: str, cwd: str, slug: str) -> int:
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    # Name the headless session after the slug too (where the CLI supports it),
    # so seeded sessions are resumable by name exactly like interactive ones.
    argv = provider.headless_argv(seed, model=model, session_name=slug)
    try:
        cp = ctx.run(argv, cwd=cwd, extra_env={**provider.title_env(), "OMC_SLUG": slug})
    except OSError as exc:
        print(f"error: headless session failed to launch: {exc}", file=sys.stderr)
        return 1
    if cp.stdout:
        print(cp.stdout, end="" if cp.stdout.endswith("\n") else "\n")
    if cp.returncode != 0 and cp.stderr:
        print(cp.stderr, file=sys.stderr, end="")
    return cp.returncode


def _say(msg: str) -> None:
    """One progress line per phase, on stderr — a silent minute is a bug."""
    print(msg, file=sys.stderr, flush=True)


def run_start(
    ctx: ToolContext,
    cfg: Config,
    context: str,
    *,
    dry_run: bool = False,
    headless: bool = False,
    no_mutex: bool = False,
) -> int:
    name = cfg.llm.default
    _say(f"→ probing tools (git, wt, {name})")
    require_tools(ctx, cfg)
    plugin_status = ensure_plugin(ctx, cfg, check_only=dry_run)
    _say(f"→ omc plugin for {name}: {plugin_status}")

    root = repo_root(ctx)
    if root is not None:
        # Warn-but-proceed: a blocked chain is configure's fight, not start's.
        ensure_agents_chain(ctx, root)

    _say(f"→ generating slug via {name} (LLM call, typically 15–60s)…")
    slug = fetch_slug(ctx, cfg, context)  # raises Refusal with the skill's message
    _say(f"✓ slug: {slug}")
    branch = f"{cfg.worktree.branch_prefix}{slug}"
    base = cfg.worktree.base_branch

    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    seed = f"/omc:start {context}"
    notify_argv = notify.sink_argv(name) if cfg.notifications.enabled else None
    session_argv = provider.session_argv(
        session_name=slug, model=model, seed=seed, notify_sink_argv=notify_argv
    )
    title_seq = detect_terminal(ctx.env).title_sequence(slug)

    if dry_run:
        shell = detect_shell(ctx.env)
        shell_argv, _ = shell.build_invocation(
            cwd="<worktree>", title=slug, startup_argv=session_argv, title_seq=title_seq
        )
        wt_argv = [
            ctx.wt_bin, "switch", "--create", branch,
            "--base", f"origin/{base}", "--no-cd", "--yes", "--format=json",
        ]  # fmt: skip
        if cfg.notifications.enabled:
            files = provider.notification_setup(notify.sink_argv(name))
            what = ", ".join(files) or "none (argv only)"
            notify_desc = f"backend {cfg.notifications.backend}; files: {what}"
        else:
            notify_desc = "disabled"
        _print_plan(branch, base, wt_argv, title_seq, session_argv, shell_argv, notify_desc)
        return 0

    if not no_mutex:
        # Never HOLD the lock — verify it is free (momentary acquire-and-release)
        # so we never snapshot a primary that `omc watch` is mid-way through
        # updating. None = not in a repo: nothing to guard.
        lock = busy_lock(ctx)
        if lock is not None:
            wait_until_idle(lock, say=_say)

    _say(f"→ creating worktree {branch} (base origin/{base})")
    worktree.sync_base(ctx, base)
    path = worktree.create_worktree(ctx, branch, base=f"origin/{base}")
    if path is None:
        raise OmcError(f"could not create or switch to the worktree for {branch}")
    _say(f"✓ worktree: {path}")

    if cfg.notifications.enabled:
        wired = notify.wire_worktree(provider, Path(path))
        if wired:
            _say(f"✓ notification wiring: {', '.join(wired)}")

    if headless:
        _say(f"→ running headless {name} session seeded with /omc:start")
        return _run_headless(ctx, cfg, seed, path, slug)
    _say(f'→ launching {name} session "{slug}" seeded with /omc:start')

    os.environ.update({**provider.title_env(), "OMC_SLUG": slug})  # pragma: no cover
    shell = detect_shell(ctx.env)  # pragma: no cover
    shell.exec_interactive(  # pragma: no cover
        cwd=path, title=slug, startup_argv=session_argv, title_seq=title_seq
    )
    return 0  # pragma: no cover - unreachable after execvp
