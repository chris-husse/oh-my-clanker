"""Faithful-worktree wt configuration: create the starter when absent, sniff
existing configs and point at /omc:check-wt-config when they look off.

The starter has NO excludes on copy-ignored: cutting a worktree snapshots
main — .env, caches, AND the .gitnexus/.omc/docs knowledge dirs — refreshed
later by /omc:rebase-main.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from .toolctx import ToolContext

WT_TEMPLATE = """\
# Worktrunk project config, seeded by omc — faithful worktrees out of the box.
# Every gitignored file (.env, caches, the .gitnexus/.omc knowledge snapshot)
# is reflink-copied into new worktrees; refresh a worktree's snapshot later
# with /omc:rebase-main. Docs: https://worktrunk.dev

# Blocking setup so the worktree is usable the moment you land in it.
pre-start = "{ ! test -f .gitmodules || git submodule update --init --recursive; } && { ! command -v direnv >/dev/null || direnv allow .; }"

[post-start]
copy-ignored = "wt step copy-ignored"
"""


def repo_root(ctx: ToolContext) -> str | None:
    """The toplevel of the repo containing cwd, or None outside a repo."""
    try:
        cp = ctx.run([ctx.git_bin, "rev-parse", "--show-toplevel"])
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    return (cp.stdout or "").strip() or None


def primary_root(ctx: ToolContext) -> str | None:
    """First entry of `git worktree list --porcelain` = the primary checkout."""
    try:
        cp = ctx.run([ctx.git_bin, "worktree", "list", "--porcelain"])
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    for line in (cp.stdout or "").splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return None


def _has_copy_ignored(text: str) -> bool:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise
    post_start = data.get("post-start", {})
    if isinstance(post_start, dict):
        return any("copy-ignored" in str(v) for v in post_start.values())
    return False


def ensure_wt_config(ctx: ToolContext, root: str | Path) -> str:
    """Create-if-absent, sniff-if-present; NEVER edits an existing file.

    Returns "created" | "ok" | "suspicious". Notices go to stderr.
    """
    path = Path(root) / ".config" / "wt.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(WT_TEMPLATE)
        print(
            f"→ wrote {path} (faithful-worktree copy rules) — review and commit it",
            file=sys.stderr,
            flush=True,
        )
        return "created"
    try:
        if _has_copy_ignored(path.read_text()):
            return "ok"
        reason = "doesn't copy ignored files into worktrees"
    except tomllib.TOMLDecodeError:
        reason = "did not parse as TOML"
    print(
        f"→ existing {path} {reason} — run /omc:check-wt-config for an analysis",
        file=sys.stderr,
        flush=True,
    )
    return "suspicious"
