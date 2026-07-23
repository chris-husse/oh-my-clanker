"""The spec's REQUIRED subprocess tests: restart-after-SIGKILL always wipes
the lock; a second live watch bails with the exact message; --clear-mutex
bypasses. Real processes, real flock — no mocks."""

import os
import signal

from ._mutexproc import install_slow_hook, run_watch_once, spawn_watch, wait_for
from .test_watch import _ctx_with_node_stub, _push_remote_commit, _repo_with_origin

BAIL = "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"


def _sub_env(env):
    """Env for driver subprocesses: the stub env layered over the real one."""
    return {**os.environ, **env}


def test_restart_after_sigkill_mid_tick_acquires_cleanly(tmp_path):
    """REQUIRED: a watch restart always wipes the lock — even after SIGKILL
    mid-hook (the kernel releases flock with the process; no --clear-mutex)."""
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    marker = tmp_path / "hook-started"
    install_slow_hook(repo, marker, 60)
    err1 = tmp_path / "watch1.err"
    p1 = spawn_watch(repo, _sub_env(ctx.env), stderr_path=err1)
    try:
        wait_for(marker.exists)  # p1 is now BUSY, both locks held
        os.kill(p1.pid, signal.SIGKILL)
        p1.wait()
        install_slow_hook(repo, tmp_path / "m2", 0)  # restart must not re-sleep 60s
        cp = run_watch_once(repo, _sub_env(ctx.env))
        assert cp.returncode == 0, cp.stderr
        assert BAIL not in cp.stderr
    finally:
        if p1.poll() is None:
            p1.kill()
            p1.wait()


def test_second_watch_bails_with_exact_message_and_clear_mutex_bypasses(tmp_path):
    origin, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    err1 = tmp_path / "watch1.err"
    p1 = spawn_watch(repo, _sub_env(ctx.env), interval=60, stderr_path=err1)
    try:
        # idle proof: the loop announced itself and finished its first tick
        wait_for(lambda: "waiting for changes" in err1.read_text() if err1.exists() else False)
        cp = run_watch_once(repo, _sub_env(ctx.env))
        assert cp.returncode == 1
        assert BAIL in cp.stderr
        cp = run_watch_once(repo, _sub_env(ctx.env), clear=True)
        assert cp.returncode == 0, cp.stderr
        assert BAIL not in cp.stderr
    finally:
        p1.kill()
        p1.wait()
