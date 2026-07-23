"""start-side mutex: probe-only semantics against REAL locks and repos.
(The stock test_start.py suite stubs git with 'not a repo', so its runs
self-skip the probe — these tests build real repos instead.)"""

import json
import os
import stat
import subprocess
import sys
import threading

import pytest

from omc.config.schema import Config
from omc.start import run_start
from omc.toolctx import ToolContext

from ._mutexproc import install_slow_hook, spawn_watch, wait_for
from ._stubs import make_stub
from .test_watch import _ctx_with_node_stub, _push_remote_commit, _repo_with_origin

OK_VERDICT = 'OMC_SLUG {"ok": true, "slug": "proj-1-fix-login"}'
WAIT_LINE = "→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass"

_HOLDER = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    # Bind the lock: an unreferenced FileLock is GC'd right after acquire() and
    # filelock's __del__ releases the flock before the sleep, so the child would
    # never actually hold it for `seconds`.
    "_lock = FileLock(sys.argv[1])\n"
    "_lock.acquire()\n"
    "print('held', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


@pytest.fixture(autouse=True)
def _no_agents_chain(monkeypatch):
    """run_start calls ensure_agents_chain unguarded when cwd is a real repo;
    the AGENTS.md distribution asset may be absent in a dev venv and is
    orthogonal to the mutex under test — neutralize it."""
    monkeypatch.setattr("omc.start.ensure_agents_chain", lambda ctx, root: "ok")


def _hold(path, seconds):
    p = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p.stdout.readline().strip() == "held"
    return p


def _start_env(tmp_path, repo, *, wt_probes_lock=False):
    """claude + wt stubs ahead of the REAL PATH (real git!), cwd-able repo.
    With wt_probes_lock the wt stub FAILS if the busy lock is held when wt
    runs — proving start released the probe before creating the worktree."""
    bindir = tmp_path / "startbin"
    make_stub(bindir, "claude", stdout=f"omc@oh-my-clanker\n{OK_VERDICT}")
    wtree = tmp_path / "wtree"
    wtree.mkdir(exist_ok=True)
    wt = bindir / "wt"
    payload = json.dumps({"path": str(wtree)})
    if wt_probes_lock:
        lock_path = repo / ".git" / "omc-watch-busy.lock"
        wt.write_text(
            "#!/bin/sh\n"
            f"python3 -c \"import fcntl; f = open('{lock_path}', 'a'); "
            'fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)" || exit 7\n'
            f"echo '{payload}'\n"
        )
    else:
        wt.write_text(f"#!/bin/sh\necho '{payload}'\n")
    wt.chmod(wt.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "HOME": str(tmp_path),
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "SHELL": "/bin/bash",
    }


def test_probe_is_released_before_the_worktree_is_created(tmp_path, capsys, monkeypatch):
    _, repo = _repo_with_origin(tmp_path)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo, wt_probes_lock=True)
    rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True)
    assert rc == 0  # wt exits 7 (→ rc != 0 path) if the probe were still held
    assert (repo / ".git" / "omc-watch-busy.lock").exists()  # probe touched it


def test_start_waits_for_a_held_busy_lock_and_narrates(tmp_path, capsys, monkeypatch):
    _, repo = _repo_with_origin(tmp_path)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    holder = _hold(repo / ".git" / "omc-watch-busy.lock", 1.5)  # self-releases
    try:
        rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True)
    finally:
        holder.wait()
    assert rc == 0
    assert WAIT_LINE in capsys.readouterr().err


def test_no_mutex_skips_the_probe_entirely(tmp_path, capsys, monkeypatch):
    _, repo = _repo_with_origin(tmp_path)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    holder = _hold(repo / ".git" / "omc-watch-busy.lock", 60)  # held THROUGHOUT
    try:
        rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True, no_mutex=True)
    finally:
        holder.kill()
        holder.wait()
    assert rc == 0  # would hang forever if the probe ran
    assert WAIT_LINE not in capsys.readouterr().err


def test_parallel_starts_all_work_while_watch_runs(tmp_path, capsys, monkeypatch):
    """REQUIRED: multiple `omc start` in parallel must all work while
    `omc watch` is running (start never holds the lock, so they cannot
    exclude each other)."""
    origin, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    err1 = tmp_path / "watch.err"
    p1 = spawn_watch(repo, {**os.environ, **ctx.env}, interval=60, stderr_path=err1)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    try:
        wait_for(lambda: "waiting for changes" in err1.read_text() if err1.exists() else False)
        results = [None, None, None]

        def one(i):
            results[i] = run_start(ToolContext.from_env(env), Config(), f"PROJ-{i}", headless=True)

        threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert results == [0, 0, 0]
    finally:
        p1.kill()
        p1.wait()


def test_start_waits_out_a_busy_watch_then_proceeds(tmp_path, capsys, monkeypatch):
    """End-to-end contention against a REAL busy watch (slow post-watch hook)."""
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    marker = tmp_path / "hook-started"
    install_slow_hook(repo, marker, 3)
    err1 = tmp_path / "watch.err"
    p1 = spawn_watch(repo, {**os.environ, **ctx.env}, interval=60, stderr_path=err1)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    try:
        wait_for(marker.exists)  # watch is now BUSY in the hook
        rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True)
        assert rc == 0
        assert WAIT_LINE in capsys.readouterr().err
    finally:
        p1.kill()
        p1.wait()


def test_start_no_mutex_flag_parses():
    from omc.cli import build_parser

    args = build_parser().parse_args(["start", "ctx", "--no-mutex"])
    assert args.no_mutex is True
    assert build_parser().parse_args(["start", "ctx"]).no_mutex is False
