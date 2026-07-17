"""`omc start <context>`: probe -> slug -> worktree -> seeded handoff."""

from __future__ import annotations

import os
import shlex
import sys

from . import worktree
from .config.schema import Config
from .errors import OmcError
from .probe import require_tools
from .providers.registry import get_provider
from .shells.registry import detect_shell
from .slug import fetch_slug
from .terminals import detect_terminal
from .toolctx import ToolContext


def _print_plan(branch, base, wt_argv, title_seq, session_argv, shell_argv):
    print("omc start — plan (dry run, no changes made):")
    print(f"  branch:       {branch}")
    print(f"  fetch:        git fetch origin {base}")
    print(f"  worktree cmd: {shlex.join(wt_argv)}")
    print(f"  title seq:    {title_seq!r}")
    print(f"  session argv: {session_argv}")
    print(f"  shell argv:   {shell_argv}")


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


def run_start(
    ctx: ToolContext,
    cfg: Config,
    context: str,
    *,
    dry_run: bool = False,
    headless: bool = False,
) -> int:
    require_tools(ctx, cfg)

    slug = fetch_slug(ctx, cfg, context)  # raises Refusal with the skill's message
    branch = f"{cfg.worktree.branch_prefix}{slug}"
    base = cfg.worktree.base_branch

    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    seed = f"/omc:start {context}"
    session_argv = provider.session_argv(session_name=slug, model=model, seed=seed)
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
        _print_plan(branch, base, wt_argv, title_seq, session_argv, shell_argv)
        return 0

    worktree.sync_base(ctx, base)
    path = worktree.create_worktree(ctx, branch, base=f"origin/{base}")
    if path is None:
        raise OmcError(f"could not create or switch to the worktree for {branch}")

    if headless:
        return _run_headless(ctx, cfg, seed, path, slug)

    os.environ.update({**provider.title_env(), "OMC_SLUG": slug})  # pragma: no cover
    shell = detect_shell(ctx.env)  # pragma: no cover
    shell.exec_interactive(  # pragma: no cover
        cwd=path, title=slug, startup_argv=session_argv, title_seq=title_seq
    )
    return 0  # pragma: no cover - unreachable after execvp
