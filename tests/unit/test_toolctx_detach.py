import os
import shlex
import signal
import sys
import time

from omc.toolctx import ToolContext

from . import _mutexproc
from ._stubs import host_bin


def _ctx(tmp_path):
    return ToolContext(
        home=tmp_path / "home", env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    )


def test_spawn_detached_runs_in_own_session(tmp_path):
    out = tmp_path / "ids"
    code = "import os,sys; open(sys.argv[1],'w').write(f'{os.getsid(0)} {os.getpgrp()}')"
    proc = _ctx(tmp_path).spawn_detached([sys.executable, "-c", code, str(out)], cwd=tmp_path)
    assert proc.wait(timeout=10) == 0
    sid, pgid = (int(x) for x in out.read_text().split())
    assert sid != os.getsid(0)
    assert sid == pgid == proc.pid


def test_spawn_detached_passes_extra_env(tmp_path):
    out = tmp_path / "env"
    code = "import os,sys; e=os.environ; open(sys.argv[1],'w').write(e['X_OMC'] + ' ' + e['HOME'])"
    proc = _ctx(tmp_path).spawn_detached(
        [sys.executable, "-c", code, str(out)], cwd=tmp_path, extra_env={"X_OMC": "detached"}
    )
    assert proc.wait(timeout=10) == 0
    assert out.read_text() == f"detached {tmp_path}"


def test_spawn_logged_appends_both_streams(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("old\n")
    code = "import sys; print('to-out', flush=True); print('to-err', file=sys.stderr, flush=True)"
    proc = _ctx(tmp_path).spawn_logged([sys.executable, "-c", code], cwd=tmp_path, log_path=log)
    assert proc.wait(timeout=10) == 0
    assert log.read_text() == "old\nto-out\nto-err\n"


def test_signal_group_kills_whole_group(tmp_path):
    ctx = _ctx(tmp_path)
    pidfile = tmp_path / "grandchild.pid"
    script = tmp_path / "group.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"{shlex.quote(host_bin('sleep'))} 60 &\n"
        f"echo $! > {shlex.quote(str(pidfile))}\n"
        "wait\n"
    )
    script.chmod(0o755)
    proc = ctx.spawn_logged([str(script)], cwd=tmp_path, log_path=tmp_path / "group.log")
    try:
        _mutexproc.wait_for(lambda: pidfile.exists() and pidfile.read_text().strip(), 5.0)
        grandchild = int(pidfile.read_text())
        assert ctx.signal_pid(grandchild, 0) is True
        assert ctx.signal_group(proc.pid, signal.SIGKILL) is True
        assert proc.wait(timeout=10) == -signal.SIGKILL
        # The reparented grandchild must also be reaped (by init or a subreaper) to vanish.
        _mutexproc.wait_for(lambda: not ctx.signal_pid(grandchild, 0), 5.0)
    finally:
        if proc.poll() is None:
            ctx.signal_group(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)


def test_signal_missing_pid_is_false(tmp_path):
    ctx = _ctx(tmp_path)
    proc = ctx.spawn_detached([sys.executable, "-c", "pass"], cwd=tmp_path)
    proc.wait(timeout=10)
    assert ctx.signal_pid(proc.pid, 0) is False
    assert ctx.signal_group(proc.pid, 0) is False


def test_signal_refuses_pids_at_or_below_one(tmp_path, monkeypatch):
    # 0, -1 and killpg(1) would signal our own group or every process the user owns.
    calls = []
    monkeypatch.setattr(os, "kill", lambda *a: calls.append(("kill", *a)))
    monkeypatch.setattr(os, "killpg", lambda *a: calls.append(("killpg", *a)))
    ctx = _ctx(tmp_path)
    for target in (0, 1, -1):
        assert ctx.signal_pid(target, 0) is False
        assert ctx.signal_group(target, 0) is False
    assert calls == []


def test_boot_time_stable(tmp_path):
    ctx = _ctx(tmp_path)
    first = ctx.boot_time()
    time.sleep(0.1)
    assert abs(ctx.boot_time() - first) < 1.0
    assert 0 < first <= time.time()


def test_process_start_reads_proc_stat_field_22(tmp_path):
    """The start time a recycled pid cannot share; None once the process is gone, and
    always None without /proc (macOS)."""
    ctx = _ctx(tmp_path)
    proc = ctx.spawn_detached([host_bin("sleep"), "60"], cwd=tmp_path)
    try:
        start = ctx.process_start(proc.pid)
        if ctx.procfs():
            stat = open(f"/proc/{proc.pid}/stat").read()
            assert start == int(stat.rsplit(")", 1)[1].split()[19])
            assert start == ctx.process_start(proc.pid)  # stable for the process's life
        else:
            assert start is None
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert ctx.process_start(proc.pid) is None
