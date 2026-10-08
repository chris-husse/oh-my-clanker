"""Thin `wt` wrapper through ToolContext + best-effort base fetch."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from .toolctx import ToolContext


def _switch(
    ctx: ToolContext, args: list[str], *, cwd: str | Path | None = None
) -> tuple[str | None, str]:
    directory_args = ["-C", str(cwd)] if cwd is not None else []
    try:
        cp = ctx.run([ctx.wt_bin, *directory_args, "switch", *args])
    except OSError as exc:
        return None, str(exc)
    if cp.returncode != 0:
        return None, (cp.stderr or cp.stdout or "").strip()
    try:
        data = json.loads(cp.stdout) if (cp.stdout or "").strip() else None
    except json.JSONDecodeError:
        return None, ""
    if isinstance(data, dict) and isinstance(data.get("path"), str) and data["path"]:
        return data["path"], ""
    return None, ""


def _say(message: str) -> None:
    print(message, file=sys.stderr)


def sync_base(
    ctx: ToolContext,
    base: str,
    *,
    cwd: str | Path | None = None,
    say: Callable[[str], None] = _say,
) -> bool:
    """Fetch origin/<base> so the worktree is cut from CURRENT upstream.

    Best-effort (the start skill's freshness gate is the backstop) but LOUD on
    failure so a stale cut is never silent.
    """
    try:
        cp = ctx.run(
            [ctx.git_bin, "fetch", "origin", base], cwd=str(cwd) if cwd is not None else None
        )
    except OSError as exc:
        say(f"warning: could not fetch origin/{base}: {exc}")
        return False
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        say(f"warning: 'git fetch origin {base}' failed: {detail}")
        return False
    return True


def create_worktree(
    ctx: ToolContext,
    branch: str,
    base: str | None = None,
    *,
    cwd: str | Path | None = None,
    say: Callable[[str], None] = _say,
) -> str | None:
    """Create (or re-enter) the worktree for `branch`; return its path or None.

    `wt switch --create` refuses when the branch already exists, so on that miss
    retry WITHOUT --create — re-running `omc start` for the same ticket is
    idempotent and lands in the same worktree.
    """
    create_args = ["--create", branch]
    if base:
        create_args += ["--base", base]
    create_args += ["--no-cd", "--yes", "--format=json"]
    path, _ = _switch(ctx, create_args, cwd=cwd)
    if path:
        return path
    path, err = _switch(ctx, [branch, "--no-cd", "--yes", "--format=json"], cwd=cwd)
    if path is None and err:
        say(f"wt switch failed: {err}")
    return path
