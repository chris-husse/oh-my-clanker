"""The watch alive lock: how a stage run tells that an `omc watch` is running
for its project (design 6.4, A1). Watch only holds it; the run probes it.
Holders are real processes — flock conflicts are per open-file-description,
so an in-process holder would test the wrong thing."""

import os
import subprocess

import pytest

from omc.toolctx import ToolContext
from omc.watchlock import ALIVE_LOCK, INSTANCE_LOCK, WATCH_BAIL_MSG, alive_lock_path, lock_held

from ._mutexproc import (
    ctx_with_node_stub,
    flock_granted,
    hold_alive_lock,
    repo_with_origin,
    run_watch_once,
    spawn_watch,
    wait_for,
)


def _sub_env(env):
    """Env for driver subprocesses: the stub env layered over the real one."""
    return {**os.environ, **env}


def _alive_path(repo):
    path = alive_lock_path(ToolContext.from_env(dict(os.environ)), cwd=str(repo))
    assert path == (repo / ".git" / ALIVE_LOCK).resolve()
    return path


def _stop(p):
    if p.poll() is None:
        p.kill()
    p.wait()


def test_no_watch_means_not_alive(tmp_path):
    _, repo = repo_with_origin(tmp_path)
    path = _alive_path(repo)
    assert lock_held(path) is False
    assert lock_held(path) is False  # the probe itself leaves nothing held


def test_helper_holding_alive_lock_reads_alive_until_killed(tmp_path):
    _, repo = repo_with_origin(tmp_path)
    path = _alive_path(repo)
    holder = hold_alive_lock(repo)
    try:
        assert lock_held(path) is True
    finally:
        _stop(holder)
    assert lock_held(path) is False  # the kernel released it with the process


def test_real_watch_holds_alive_lock_while_running(tmp_path):
    _, repo = repo_with_origin(tmp_path)
    path = _alive_path(repo)
    ctx, _ = ctx_with_node_stub(tmp_path, tmp_path / "home")
    watch = spawn_watch(repo, _sub_env(ctx.env), interval=60, stderr_path=tmp_path / "w.err")
    try:
        wait_for(lambda: lock_held(path))
    finally:
        _stop(watch)
    # Not at once: a child the watch was forking when killed shares the open
    # file until its exec closes the (close-on-exec) fd.
    wait_for(lambda: not lock_held(path))


def test_two_concurrent_probes_both_read_not_alive(tmp_path):
    """A probe takes the lock SHARED, so a second probe in flight never reads
    the first one as a running watch."""
    _, repo = repo_with_origin(tmp_path)
    path = _alive_path(repo)
    probe = hold_alive_lock(repo, shared=True)
    try:
        assert lock_held(path) is False
    finally:
        _stop(probe)


def test_flock_granted_sees_a_holder_and_its_release(tmp_path):
    _, repo = repo_with_origin(tmp_path)
    path = _alive_path(repo)
    holder = hold_alive_lock(repo)
    try:
        assert flock_granted(path) is True
    finally:
        _stop(holder)
    wait_for(lambda: not flock_granted(path))


def test_probe_never_makes_starting_watch_bail(tmp_path):
    """Watch takes the alive lock BLOCKING after the instance lock: a probe in
    flight delays it, never turns it into WATCH_BAIL_MSG."""
    _, repo = repo_with_origin(tmp_path)
    ctx, _ = ctx_with_node_stub(tmp_path, tmp_path / "home")
    instance = repo / ".git" / INSTANCE_LOCK
    err = tmp_path / "w.err"
    probe = hold_alive_lock(repo, shared=True)
    watch = None
    try:
        watch = spawn_watch(repo, _sub_env(ctx.env), once=True, stderr_path=err)
        # past acquire_instance, seen without taking the instance lock: a locking probe
        # can win the lock for an instant, and the watch then bails
        wait_for(lambda: instance.exists() and flock_granted(instance))
        with pytest.raises(subprocess.TimeoutExpired):
            watch.wait(timeout=2)  # blocked on the alive lock
        text = err.read_text()
        assert WATCH_BAIL_MSG not in text
        assert "→ watching" not in text  # still waiting for the alive lock
        _stop(probe)
        assert watch.wait(timeout=60) == 0, err.read_text()
        assert WATCH_BAIL_MSG not in err.read_text()
    finally:
        _stop(probe)
        if watch is not None:
            _stop(watch)


def test_clear_mutex_takes_a_fresh_alive_lock(tmp_path):
    """--clear-mutex unlinks the alive file as it does the instance file: the
    new watch locks a fresh inode instead of waiting behind the old holder."""
    _, repo = repo_with_origin(tmp_path)
    path = _alive_path(repo)
    ctx, _ = ctx_with_node_stub(tmp_path, tmp_path / "home")
    old = hold_alive_lock(repo)
    try:
        cp = run_watch_once(repo, _sub_env(ctx.env), clear=True, timeout=60)
        assert cp.returncode == 0, cp.stderr
        assert WATCH_BAIL_MSG not in cp.stderr
        assert lock_held(path) is False  # the path now names the new, released inode
    finally:
        _stop(old)


def test_lock_held_reads_a_filelock_holder_as_held(tmp_path):
    """The stage supervisor's stage.lock is a filelock FileLock: an exclusive flock."""
    from filelock import FileLock

    path = tmp_path / "stage.lock"
    lock = FileLock(str(path))
    lock.acquire(timeout=0)
    try:
        assert lock_held(path) is True
    finally:
        lock.release()
    assert lock_held(path) is False


def test_lock_held_without_its_directory_is_not_held(tmp_path):
    path = tmp_path / "gone" / "stage.lock"
    assert lock_held(path) is False
    assert not path.parent.exists()  # the probe makes no directory


def test_lock_held_under_a_path_that_is_no_longer_a_directory_is_not_held(tmp_path):
    (tmp_path / "gone").write_text("a file where the git dir was\n")
    assert lock_held(tmp_path / "gone" / "stage.lock") is False


def test_lock_held_raises_when_the_lock_cannot_be_opened(tmp_path):
    """Unreadable is not absent: a PermissionError must never read as "no holder"."""
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "stage.lock").write_text("")
    (locked / "stage.lock").chmod(0)
    try:
        with pytest.raises(PermissionError):
            lock_held(locked / "stage.lock")
    finally:
        (locked / "stage.lock").chmod(0o644)
