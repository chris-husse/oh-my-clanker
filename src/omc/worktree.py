"""Thin `wt` wrapper through ToolContext + best-effort base fetch."""

from __future__ import annotations

import json
import sys

from .errors import OmcError
from .toolctx import ToolContext


def switch_command(ctx: ToolContext, branch: str, base: str | None) -> tuple[list[str], bool]:
    """Select the single switch command from the branch's pre-existing ref."""
    try:
        ref = ctx.run([ctx.git_bin, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
    except OSError as exc:
        raise OmcError(f"could not probe branch {branch}: {exc}") from exc
    if ref.returncode not in (0, 1):
        detail = (ref.stderr or ref.stdout or "").strip()
        raise OmcError(f"could not probe branch {branch} (exit {ref.returncode}): {detail}")
    existed = ref.returncode == 0
    args = [ctx.wt_bin, "switch"]
    if not existed:
        args.append("--create")
    args.append(branch)
    if not existed and base:
        args.extend(["--base", base])
    args.extend(["--no-cd", "--yes", "--format=json"])
    return args, existed


def _worktree_path_from_porcelain(porcelain: str, branch: str) -> str | None:
    target = f"branch refs/heads/{branch}"
    for block in porcelain.split("\n\n"):
        lines = block.splitlines()
        if target in lines:
            path = next(
                (line.removeprefix("worktree ") for line in lines if line.startswith("worktree ")),
                "",
            )
            if not path:
                raise OmcError(f"could not determine worktree path for {branch}")
            return path
    return None


def _worktree_path(ctx: ToolContext, branch: str) -> str | None:
    try:
        listed = ctx.run([ctx.git_bin, "worktree", "list", "--porcelain"])
    except OSError as exc:
        raise OmcError(f"could not list worktrees for {branch}: {exc}") from exc
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "").strip()
        raise OmcError(
            f"could not list worktrees for {branch} (exit {listed.returncode}): {detail}"
        )
    return _worktree_path_from_porcelain(listed.stdout or "", branch)


def _cleanup_failed_switch(
    ctx: ToolContext,
    branch: str,
    *,
    branch_existed: bool,
    worktree_existed: bool,
) -> None:
    """Remove exactly what appeared since the pre-`wt` probes, each only if it exists now."""
    if not worktree_existed:
        try:
            path = _worktree_path(ctx, branch)
        except OmcError as exc:
            print(f"✗ could not remove worktree for {branch}: {exc}", file=sys.stderr)
            path = None
        if path is not None:
            try:
                removed = ctx.run([ctx.git_bin, "worktree", "remove", "--force", path])
            except OSError as exc:
                print(f"✗ could not remove worktree {path}: {exc}", file=sys.stderr)
            else:
                if removed.returncode != 0:
                    detail = (removed.stderr or removed.stdout or "").strip()
                    print(f"✗ could not remove worktree {path}: {detail}", file=sys.stderr)

    if branch_existed:
        return
    try:
        ref = ctx.run([ctx.git_bin, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
    except OSError as exc:
        print(f"✗ could not remove branch {branch}: {exc}", file=sys.stderr)
        return
    if ref.returncode == 1:
        return
    if ref.returncode != 0:
        detail = (ref.stderr or ref.stdout or "").strip()
        print(f"✗ could not remove branch {branch}: {detail}", file=sys.stderr)
        return
    try:
        deleted = ctx.run([ctx.git_bin, "branch", "-D", branch])
    except OSError as exc:
        print(f"✗ could not remove branch {branch}: {exc}", file=sys.stderr)
    else:
        if deleted.returncode != 0:
            detail = (deleted.stderr or deleted.stdout or "").strip()
            print(f"✗ could not remove branch {branch}: {detail}", file=sys.stderr)


def sync_base(ctx: ToolContext, base: str) -> bool:
    """Fetch origin/<base> so the worktree is cut from CURRENT upstream.

    Best-effort (the start skill's freshness gate is the backstop) but LOUD on
    failure so a stale cut is never silent.
    """
    try:
        cp = ctx.run([ctx.git_bin, "fetch", "origin", base])
    except OSError as exc:
        print(f"warning: could not fetch origin/{base}: {exc}", file=sys.stderr)
        return False
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        print(f"warning: 'git fetch origin {base}' failed: {detail}", file=sys.stderr)
        return False
    return True


def create_worktree(ctx: ToolContext, branch: str, base: str | None = None) -> str:
    """Create or re-enter one worktree, removing only resources this attempt created."""
    argv, branch_existed = switch_command(ctx, branch, base)
    worktree_existed = _worktree_path(ctx, branch) is not None

    try:
        result = ctx.run(argv)
    except OSError as exc:
        error = OmcError(f"wt switch for {branch} failed: {exc}; see wt config state logs")
    else:
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout or "")
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("path"), str) and data["path"]:
                return data["path"]
            detail = (result.stderr or "").strip()[-2000:]
            suffix = f": {detail}" if detail else ""
            error = OmcError(
                f"wt switch for {branch} returned no valid path (exit 0){suffix}; "
                "see wt config state logs"
            )
        else:
            detail = (result.stderr or result.stdout or "").strip()[-2000:]
            error = OmcError(
                f"wt switch for {branch} failed (exit {result.returncode}): {detail}; "
                "see wt config state logs"
            )

    _cleanup_failed_switch(
        ctx,
        branch,
        branch_existed=branch_existed,
        worktree_existed=worktree_existed,
    )
    raise error
