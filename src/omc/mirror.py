"""Deterministic directory mirroring (rsync --delete semantics, no shell).

The snapshot model copies the primary root's `.gitnexus/` and `.omc/docs/`
into worktrees; refreshing that snapshot must delete extraneous files. An LLM
is never trusted with that operation — this is the unit-tested Python it
calls instead (via `omc internal rebase-main`).

Some projects share one `.omc` across all checkouts via symlinks (the
chicken-era shared knowledge tree), so src and dst can be the SAME directory
reached through different paths. Sync is therefore in place (no
wipe-and-recreate window for concurrent readers) and alias-guarded.
"""

from __future__ import annotations

import filecmp
import os
import shutil
from pathlib import Path
from typing import NamedTuple

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


def mirror_dir(src: Path, dst: Path) -> bool:
    """Make ``dst`` an exact copy of ``src`` by syncing in place.

    Returns False — filesystem untouched — when both resolve to the same
    directory: with a shared `.omc`, dst already IS src, and the old
    delete-then-copy destroyed the source through the alias. Raises OmcError
    when one resolved path nests inside the other (a sync would delete its
    own source or recurse into itself).
    """
    src = Path(src)
    dst = Path(dst)
    src_r = src.resolve()
    dst_r = dst.resolve()
    if src_r == dst_r:
        return False
    if src_r in dst_r.parents or dst_r in src_r.parents:
        raise OmcError(f"refuse: {src} and {dst} nest within each other")
    _sync_tree(src, dst)
    return True


def _remove_entry(path: Path) -> None:
    """Remove one dst entry of any type; a symlink is unlinked, never followed."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _sync_tree(src: Path, dst: Path) -> None:
    """Recursively converge dst onto src without ever removing dst itself."""
    dst.mkdir(parents=True, exist_ok=True)
    names = set()
    for entry in sorted(src.iterdir()):
        names.add(entry.name)
        target = dst / entry.name
        # is_symlink() is checked before is_dir()/is_file() everywhere: both
        # follow links, and a link must never be traversed for type checks.
        if entry.is_symlink():
            link = os.readlink(entry)
            if target.is_symlink() and os.readlink(target) == link:
                continue
            if target.is_symlink() or target.exists():
                _remove_entry(target)
            os.symlink(link, target)
        elif entry.is_dir():
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                _remove_entry(target)
            _sync_tree(entry, target)
        else:
            if target.is_symlink() or (target.exists() and not target.is_file()):
                _remove_entry(target)
            elif target.is_file() and filecmp.cmp(entry, target, shallow=True):
                continue  # size+mtime match (copy2 preserves both): untouched
            shutil.copy2(entry, target)
    for leftover in list(dst.iterdir()):
        # On case-insensitive/normalizing filesystems (macOS APFS default), a
        # case or Unicode-normalization variant of a just-synced name is the
        # SAME on-disk file src just wrote through via copy2 — it must not be
        # treated as excess and deleted. os.path.lexists checks the src side
        # using the filesystem's own notion of name equality, so a genuinely
        # excess name (absent from src under any spelling) still gets deleted.
        if leftover.name not in names and not os.path.lexists(src / leftover.name):
            _remove_entry(leftover)


class SnapshotResult(NamedTuple):
    """What the snapshot mirror did, per SNAPSHOT_DIRS entry."""

    synced: list[str]  # a real sync ran
    shared: list[str]  # src and dst are the same dir (shared .omc) — untouched


def mirror_snapshot(primary_root: Path, worktree_root: Path) -> SnapshotResult:
    """Mirror the knowledge snapshot from the primary root into a worktree.

    Only ``SNAPSHOT_DIRS`` are touched; absent sources are skipped. Entries
    whose src and dst alias the same directory (shared `.omc`) are reported
    as shared, not synced. Refuses to operate when both roots resolve to the
    same directory.
    """
    primary_root = Path(primary_root).resolve()
    worktree_root = Path(worktree_root).resolve()
    if primary_root == worktree_root:
        raise OmcError("refuse: primary and worktree are the same directory")
    synced: list[str] = []
    shared: list[str] = []
    for rel in SNAPSHOT_DIRS:
        src = primary_root / rel
        if not src.is_dir():
            continue
        if mirror_dir(src, worktree_root / rel):
            synced.append(rel)
        else:
            shared.append(rel)
    return SnapshotResult(synced=synced, shared=shared)
