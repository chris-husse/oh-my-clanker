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

# Where watch mirrors the generated wiki inside a root. Fixed relative path —
# clear_docs_mirror deletes ONLY this, by construction.
DOCS_MIRROR_REL = Path(".omc/docs/gitnexus/docs")


def clear_docs_mirror(root: Path) -> bool:
    """Delete the generated-docs mirror under ``root``; True when removed.

    Used by the watch heal: docs generated from an inverted (frozen) graph
    cite deleted files as current — stale docs are worse than absent docs.
    """
    target = Path(root) / DOCS_MIRROR_REL
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


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
