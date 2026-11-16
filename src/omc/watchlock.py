"""Advisory watch locks: `omc watch` mutual exclusion, busy/idle and alive signaling.

Three flock-based locks live in the repo's SHARED .git dir
(`git rev-parse --git-common-dir` — the primary's, even from a worktree):

- omc-watch.lock (INSTANCE): held by `omc watch` for its entire lifetime.
  Forbids parallel watches on one primary; `--clear-mutex` bypasses.
- omc-watch-busy.lock (BUSY): held while ANYONE mutates the primary's
  knowledge — a watch tick or `omc internal gitnexus refresh`. Free ⇔ nobody
  is mutating. `omc design` probes it before cutting a worktree so it never
  snapshots a half-updated primary.
- omc-watch-alive.lock (ALIVE): held EXCLUSIVE by `omc watch` for its entire
  lifetime, taken right after the instance lock. A stage run probes it to tell
  that a watch is running for its project. Raw fcntl.flock, not filelock: the
  probe takes it SHARED and non-blocking, and watch takes it BLOCKING, so a
  probe in flight delays a starting watch instead of making it bail (the
  instance lock is non-blocking), and two probes never see each other as a
  watch.

The kernel releases flock locks when their holder dies, so a crashed or
SIGKILLed watch never wedges anything and a watch RESTART always finds the
locks takeable. Caveat: flock is unreliable on NFS mounts — acceptable for a
local dev tool.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from .toolctx import ToolContext

INSTANCE_LOCK = "omc-watch.lock"
BUSY_LOCK = "omc-watch-busy.lock"
ALIVE_LOCK = "omc-watch-alive.lock"

WATCH_BAIL_MSG = "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"
START_WAIT_MSG = (
    "→ waiting for omc watch or a knowledge refresh to finish. "
    "Pass `omc design --no-mutex` to bypass"
)


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
    """The busy lock alone — all `omc design` ever probes; None outside a repo."""
    d = locks_dir(ctx, cwd)
    return None if d is None else FileLock(str(d / BUSY_LOCK))


def alive_lock_path(ctx: ToolContext, cwd: str | None = None) -> Path | None:
    """The alive lock's path; None outside a repo."""
    d = locks_dir(ctx, cwd)
    return None if d is None else d / ALIVE_LOCK


def hold_alive(path: Path, *, clear: bool) -> int:
    """Take the alive lock EXCLUSIVE, blocking, and return its fd (watch holds
    it until exit). Blocking because a probe holds it shared for an instant.
    `clear` unlinks the file first, as acquire_instance does: a fresh inode
    beside an older holder's."""
    if clear:
        path.unlink(missing_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_alive(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def lock_held(path: Path) -> bool:
    """True while another open file holds an exclusive flock on `path`: the watch's
    alive lock, or a stage supervisor's stage.lock (filelock's exclusive flock). The
    probe takes it SHARED and non-blocking, then releases it at once, so two probes
    never see each other as a holder. A lock whose directory is gone (or no longer a
    directory) has no holder; any other failure to open it raises."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except (FileNotFoundError, NotADirectoryError):
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


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


BUSY_WAIT_MSG = "· waiting for another omc knowledge refresh to finish"


@contextmanager
def acquire_busy_narrated(
    lock: FileLock, say: Callable[[str], None] | None = None
) -> Iterator[None]:
    """HOLD the busy lock for a knowledge mutation (watch tick, internal refresh).
    Probe first; a holder elsewhere is narrated once, then we block. Not
    `with lock:` — filelock is reentrant, so entering an already-held lock
    would leave it held after exit."""
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if say is not None:
            say(BUSY_WAIT_MSG)
        lock.acquire()
    try:
        yield
    finally:
        lock.release()
