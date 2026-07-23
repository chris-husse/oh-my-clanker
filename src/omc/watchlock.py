"""Advisory watch locks: `omc watch` mutual exclusion + busy/idle signaling.

Two flock-based locks (via filelock) live in the repo's SHARED .git dir
(`git rev-parse --git-common-dir` — the primary's, even from a worktree):

- omc-watch.lock (INSTANCE): held by `omc watch` for its entire lifetime.
  Forbids parallel watches on one primary; `--clear-mutex` bypasses.
- omc-watch-busy.lock (BUSY): held only while a watch tick is doing work —
  free means any running watch is idle. `omc start` probes it before cutting
  a worktree so it never snapshots a half-updated primary.

The kernel releases flock locks when their holder dies, so a crashed or
SIGKILLed watch never wedges anything and a watch RESTART always finds the
locks takeable. Caveat: flock is unreliable on NFS mounts — acceptable for a
local dev tool.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from filelock import FileLock, Timeout

from .toolctx import ToolContext

INSTANCE_LOCK = "omc-watch.lock"
BUSY_LOCK = "omc-watch-busy.lock"

WATCH_BAIL_MSG = "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"
START_WAIT_MSG = "→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass"


def locks_dir(ctx: ToolContext, cwd: str | None = None) -> Path | None:
    """The repo's SHARED .git dir, or None outside a repo. --git-common-dir
    may print a path relative to the asking directory — resolve against it."""
    try:
        cp = ctx.run([ctx.git_bin, "rev-parse", "--git-common-dir"], cwd=cwd)
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    raw = (cp.stdout or "").strip()
    if not raw:
        return None
    return (Path(cwd or ".") / raw).resolve()


def watch_locks(ctx: ToolContext, cwd: str | None = None) -> tuple[FileLock, FileLock] | None:
    """(instance, busy) for `omc watch`; None outside a repo."""
    d = locks_dir(ctx, cwd)
    if d is None:
        return None
    return FileLock(str(d / INSTANCE_LOCK)), FileLock(str(d / BUSY_LOCK))


def busy_lock(ctx: ToolContext, cwd: str | None = None) -> FileLock | None:
    """The busy lock alone — all `omc start` ever probes; None outside a repo."""
    d = locks_dir(ctx, cwd)
    return None if d is None else FileLock(str(d / BUSY_LOCK))


def acquire_instance(lock: FileLock, *, clear: bool) -> bool:
    """Take the watch instance lock, non-blocking. `clear` unlinks the lock
    file first — a fresh inode, deliberately breaking mutual exclusion with a
    live holder (that is what the --clear-mutex bypass means). False = held
    elsewhere: the caller prints WATCH_BAIL_MSG and exits 1."""
    if clear:
        Path(lock.lock_file).unlink(missing_ok=True)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return False
    return True


def wait_until_idle(lock: FileLock, *, say: Callable[[str], None] | None = None) -> None:
    """Block until the busy lock is free, then LEAVE IT FREE: a momentary
    acquire-and-release is the probe — an advisory lock cannot be observed
    without touching it, and it is never carried into any work. Held → narrate
    once via `say`, then wait forever (Ctrl-C aborts)."""
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if say is not None:
            say(START_WAIT_MSG)
        lock.acquire()  # filelock default: block indefinitely
    lock.release()
