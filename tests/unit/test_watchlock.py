"""watchlock: lock identities + acquire idioms. Contention cases that need a
SECOND holder use a real subprocess — flock conflicts are per
open-file-description, and filelock instances within one process can behave
reentrantly, so in-process 'contention' would test the wrong thing."""

import subprocess
import sys

from omc.toolctx import ToolContext
from omc.watchlock import (
    BUSY_LOCK,
    INSTANCE_LOCK,
    START_WAIT_MSG,
    WATCH_BAIL_MSG,
    acquire_instance,
    busy_lock,
    locks_dir,
    wait_until_idle,
    watch_locks,
)

_HOLDER = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    # Bind the lock: an unreferenced FileLock is GC'd right after acquire() and
    # filelock's __del__ releases the flock before the sleep, so the child would
    # never actually hold it. The whole point (see module docstring) is a real
    # holder for the duration of `seconds`.
    "_lock = FileLock(sys.argv[1])\n"
    "_lock.acquire()\n"
    "print('held', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


def _hold_in_subprocess(path, seconds):
    """Acquire `path` in a child process; returns after the child confirms."""
    p = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p.stdout.readline().strip() == "held"
    return p


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo


def _ctx():
    import os

    return ToolContext.from_env(dict(os.environ))


def test_locks_dir_is_git_dir_in_primary(tmp_path):
    repo = _make_repo(tmp_path)
    d = locks_dir(_ctx(), cwd=str(repo))
    assert d == (repo / ".git").resolve()


def test_locks_dir_from_worktree_is_the_shared_git_dir(tmp_path):
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "feat", cwd=repo)
    d = locks_dir(_ctx(), cwd=str(wt))
    assert d == (repo / ".git").resolve()  # SHARED dir, not the worktree's


def test_locks_dir_outside_a_repo_is_none(tmp_path):
    outside = tmp_path / "empty"
    outside.mkdir()
    assert locks_dir(_ctx(), cwd=str(outside)) is None
    assert watch_locks(_ctx(), cwd=str(outside)) is None
    assert busy_lock(_ctx(), cwd=str(outside)) is None


def test_lock_paths_use_the_exact_filenames(tmp_path):
    repo = _make_repo(tmp_path)
    instance, busy = watch_locks(_ctx(), cwd=str(repo))
    assert instance.lock_file.endswith(INSTANCE_LOCK)
    assert busy.lock_file.endswith(BUSY_LOCK)
    assert busy_lock(_ctx(), cwd=str(repo)).lock_file == busy.lock_file


def test_acquire_instance_free_then_taken(tmp_path):
    repo = _make_repo(tmp_path)
    instance, _ = watch_locks(_ctx(), cwd=str(repo))
    assert acquire_instance(instance, clear=False) is True
    try:
        holder_view, _ = watch_locks(_ctx(), cwd=str(repo))
        # a SECOND process must be refused
        p = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "from filelock import FileLock, Timeout\n"
                "try:\n"
                "    FileLock(sys.argv[1]).acquire(timeout=0)\n"
                "except Timeout:\n"
                "    sys.exit(3)\n"
                "sys.exit(0)\n",
                instance.lock_file,
            ],
        )
        assert p.returncode == 3
    finally:
        instance.release()


def test_acquire_instance_refused_when_subprocess_holds(tmp_path):
    repo = _make_repo(tmp_path)
    instance, _ = watch_locks(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(instance.lock_file, 30)
    try:
        assert acquire_instance(instance, clear=False) is False
    finally:
        p.kill()
        p.wait()


def test_acquire_instance_clear_steals_from_a_live_holder(tmp_path):
    repo = _make_repo(tmp_path)
    instance, _ = watch_locks(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(instance.lock_file, 30)
    try:
        assert acquire_instance(instance, clear=True) is True  # unlink + fresh file
        instance.release()
    finally:
        p.kill()
        p.wait()


def test_wait_until_idle_free_is_silent_and_leaves_lock_free(tmp_path):
    repo = _make_repo(tmp_path)
    lock = busy_lock(_ctx(), cwd=str(repo))
    said = []
    wait_until_idle(lock, say=said.append)
    assert said == []
    assert lock.is_locked is False


def test_wait_until_idle_blocks_until_holder_exits_and_narrates_once(tmp_path):
    repo = _make_repo(tmp_path)
    lock = busy_lock(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(lock.lock_file, 1.5)  # holder self-releases
    said = []
    wait_until_idle(lock, say=said.append)  # must block ~1.5s then return
    p.wait()
    assert said == [START_WAIT_MSG]
    assert lock.is_locked is False


def test_message_constants_are_exact():
    assert WATCH_BAIL_MSG == (
        "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"
    )
    assert START_WAIT_MSG == (
        "→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass"
    )
