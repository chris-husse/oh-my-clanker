"""Non-destructive tree copy for worktree cuts: `copy_missing` copies into a
new worktree what it lacks from the primary's `.omc/`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def copy_missing(src: Path, dst: Path) -> int:
    """Copy into ``dst`` every entry of ``src`` that ``dst`` lacks; return entries copied.

    Non-destructive: nothing already in dst is ever overwritten or deleted, so
    git-tracked files and whatever a repo's wt config copied win. A name exists
    when it ``lexists`` (a dangling link counts). Symlinks are recreated with
    the same target, never followed, and a dst entry that is not a real
    directory is never descended into.
    """
    src = Path(src)
    dst = Path(dst)
    if src.is_symlink() or not src.is_dir():
        if os.path.lexists(dst):
            return 0
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)
        return 1
    if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
        return 0
    dst.mkdir(exist_ok=True)
    return sum(copy_missing(entry, dst / entry.name) for entry in sorted(src.iterdir()))
