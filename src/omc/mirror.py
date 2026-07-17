"""Deterministic directory mirroring (rsync --delete semantics, no shell).

The snapshot model copies the primary root's `.gitnexus/` and `.omc/docs/`
into worktrees; refreshing that snapshot must delete extraneous files. An LLM
is never trusted with that operation — this is the unit-tested Python it
calls instead (via `omc internal rebase-main`).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .errors import OmcError

# The ONLY directories the snapshot mirror will ever touch, relative to a root.
SNAPSHOT_DIRS = (".gitnexus", ".omc/docs")


def mirror_dir(src: Path, dst: Path) -> None:
    """Make ``dst`` an exact copy of ``src`` (extraneous files deleted)."""
    src = Path(src)
    dst = Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=True)


def mirror_snapshot(primary_root: Path, worktree_root: Path) -> list[str]:
    """Mirror the knowledge snapshot from the primary root into a worktree.

    Only ``SNAPSHOT_DIRS`` are touched; absent sources are skipped. Refuses to
    operate when both roots resolve to the same directory (that would delete
    the primary's own snapshot).
    """
    primary_root = Path(primary_root).resolve()
    worktree_root = Path(worktree_root).resolve()
    if primary_root == worktree_root:
        raise OmcError("refuse: primary and worktree are the same directory")
    synced: list[str] = []
    for rel in SNAPSHOT_DIRS:
        src = primary_root / rel
        if not src.is_dir():
            continue
        mirror_dir(src, worktree_root / rel)
        synced.append(rel)
    return synced
