"""The run lifecycle (design 6.4-6.6, A1, A5): kill, supersede, died recovery,
watch-gone and worktree-gone liveness, prune and the foreground run.

The same real runs as test_stagerun.py: `start_run` spawns the supervisor, which
runs a `#!/bin/sh` hook in its own process group. Sleeping hooks take their pid
file as `$1`, so two runs of one hook never share it."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from omc import stagerun
from omc.buildprogress import sentinel_line
from omc.errors import OmcError
from omc.stageid import project_id, worktree_id
from omc.stagerun import kill_run, prune, run_foreground, start_run, wait_run

from . import _mutexproc, _stagekit
from ._stubs import host_bin

DAY = 86400.0


def _sleeper():
    """A hook that writes its pid to `$1` and sleeps."""
    return f'echo $$ > "$1"\nexec {_stagekit.sleep_line(60)}'


def _holder():
    """A verify hook that writes `failed`, its pid to `$1`, and stays up: held."""
    return f'echo failed > "$OMC_STAGE_RESULT"\necho $$ > "$1"\nexec {_stagekit.sleep_line(60)}'


@pytest.fixture(autouse=True)
def _fast_foreground_poll(monkeypatch):
    """run_foreground polls every POLL_S; the supervisor (its own process) keeps its own."""
    monkeypatch.setattr(stagerun, "POLL_S", 0.1)


def _supervisor(s, root=None):
    """The supervisor Popen of the run recorded now, so a test can reap it."""
    pid = s.record(root)["pid"]
    procs = [p for p in stagerun._SUPERVISORS if p.pid == pid]
    assert len(procs) == 1, (pid, stagerun._SUPERVISORS)
    return procs[0]


def _exited(proc, timeout=5.0):
    _mutexproc.wait_for(lambda: proc.poll() is not None, timeout)


def _state(s, stage_name):
    return _stagekit.wait(s.ctx, stage_name, 0).state


def _held(s, pidfile):
    _stagekit.run_hook(s, "verify", _holder(), args=(str(pidfile),))
    _mutexproc.wait_for(lambda: _state(s, "verify") == "held", 10)
    return s.read_pid(pidfile)


def test_kill_ends_held_verify(stage):
    pid = _held(stage, stage.pidfile())
    supervisor = _supervisor(stage)
    answer = kill_run(stage.ctx, "verify")
    assert answer.state == "killed"
    assert answer.reason == "kill: ended by omc internal stage kill"
    assert answer.reason.startswith("kill")
    stage.gone(pid)
    _exited(supervisor)
    assert stage.record()["state"] == "killed"


def test_kill_without_live_run_answers_last_state(stage):
    passed = _stagekit.finished(stage, "check", "exit 0")
    omc = stage.repo / ".cache" / "omc"
    before = {p.name: p.read_bytes() for p in omc.iterdir()}
    answer = kill_run(stage.ctx, "check")
    assert (answer.state, answer.run) == ("passed", passed.run)
    assert {p.name: p.read_bytes() for p in omc.iterdir()} == before
    with pytest.raises(OmcError, match="^no build run in this worktree$"):
        kill_run(stage.ctx, "build")


def test_second_start_supersedes_first(stage):
    first = _stagekit.run_hook(stage, "check", _sleeper(), args=(str(stage.pidfile("one.pid")),))
    pid = stage.read_pid(stage.pidfile("one.pid"))
    pgid = stage.record()["pgid"]
    second = start_run(stage.ctx, "check", [str(stage.pidfile("two.pid"))])
    assert second.run and second.run != first.run
    assert not stage.ctx.signal_group(pgid, 0)
    stage.gone(pid)
    assert _stagekit.wait(stage.ctx, "check", 0).run == second.run


def test_superseded_other_stage_answers_from_last(stage):
    verify = _stagekit.run_hook(stage, "verify", _sleeper(), args=(str(stage.pidfile()),))
    pid = stage.read_pid(stage.pidfile())
    _stagekit.run_hook(stage, "check", "exit 0")
    answer = _stagekit.wait(stage.ctx, "verify", 10)
    assert answer.state == "killed"
    assert answer.reason == "superseded: a newer start in this worktree"
    assert answer.run == verify.run
    stage.gone(pid)


def test_wait_does_not_follow_a_superseding_run_of_its_stage(stage, monkeypatch):
    """A `wait` that saw run A running and then finds run B of the same stage answers at
    once for A; it never waits on B (whose outcome is not A's)."""
    first = _stagekit.run_hook(stage, "check", _sleeper(), args=(str(stage.pidfile("one.pid")),))
    real, seen, replaced = stagerun._require, threading.Event(), threading.Event()

    def gated(*args):
        found = real(*args)
        if not seen.is_set():  # hold the wait between its first look and its next one
            seen.set()
            replaced.wait(30)
        return found

    monkeypatch.setattr(stagerun, "_require", gated)
    answers = []
    waiter = threading.Thread(
        target=lambda: answers.append(wait_run(stage.ctx, "check")), daemon=True
    )
    waiter.start()
    assert seen.wait(10)
    second = start_run(stage.ctx, "check", [str(stage.pidfile("two.pid"))])
    t0 = time.monotonic()
    replaced.set()
    waiter.join(timeout=30)
    assert answers, "wait did not answer"
    assert time.monotonic() - t0 < 5
    (answer,) = answers
    assert answer.run == first.run != second.run
    assert answer.state == "killed"
    assert answer.reason == "superseded: a newer start in this worktree"


@pytest.mark.parametrize(
    ("stage_name", "body", "state"), [("check", _sleeper, "running"), ("verify", _holder, "held")]
)
def test_watch_release_kills_run_and_hold(stage, stage_name, body, state):
    _stagekit.run_hook(stage, stage_name, body(), args=(str(stage.pidfile()),))
    _mutexproc.wait_for(lambda: _state(stage, stage_name) == state, 10)
    pid = stage.read_pid(stage.pidfile())
    pgid = stage.record()["pgid"]
    stage.watch.kill()
    stage.watch.wait()
    _mutexproc.wait_for(lambda: _state(stage, stage_name) == "killed", 5)
    answer = _stagekit.wait(stage.ctx, stage_name, 0)
    assert answer.reason == "watch-gone: the project's omc watch stopped"
    assert not stage.ctx.signal_group(pgid, 0)
    stage.gone(pid)


def test_worktree_removed_kills_group_and_supervisor(stage, monkeypatch):
    other = _stagekit.add_worktree(stage.repo, stage.tmp / "wt2", "b")
    stage.roots.append(other)
    monkeypatch.chdir(other)
    _stagekit.write_hook(other, "check", body=_sleeper())
    start_run(stage.ctx, "check", [str(stage.pidfile())])
    pid = stage.read_pid(stage.pidfile())
    supervisor = _supervisor(stage, other)
    shutil.rmtree(other)
    stage.gone(pid)
    _exited(supervisor)
    assert not other.exists()  # the supervisor's last write did not bring it back


def test_died_run_group_is_ended_by_next_start(stage):
    _stagekit.run_hook(stage, "check", _sleeper(), args=(str(stage.pidfile("one.pid")),))
    pid = stage.read_pid(stage.pidfile("one.pid"))
    pgid = stage.record()["pgid"]
    supervisor = _supervisor(stage)
    assert stage.ctx.signal_pid(supervisor.pid, signal.SIGKILL)
    _exited(supervisor)
    assert _state(stage, "check") == "died"
    assert stage.ctx.signal_pid(pid, 0)  # the hook outlived its supervisor
    start_run(stage.ctx, "check", [str(stage.pidfile("two.pid"))])
    assert not stage.ctx.signal_group(pgid, 0)
    stage.gone(pid)


def test_died_record_from_another_boot_is_not_signalled(stage):
    helper = _Helper(stage)
    try:
        _forge_died(stage, helper, boot=0)
        assert _state(stage, "verify") == "died"
        _stagekit.run_hook(stage, "check", "exit 0")
        assert helper.proc.poll() is None
        assert stage.ctx.signal_group(helper.proc.pid, 0)
    finally:
        helper.close()


class _Helper:
    """A sleeping process group the test owns, reaped by a thread so it does not linger
    as a zombie leader (a zombie keeps its group signalable)."""

    def __init__(self, s):
        self.proc = s.ctx.spawn_logged(
            [host_bin("sleep"), "60"], cwd=s.tmp, log_path=s.tmp / "helper.log"
        )
        self.reaper = threading.Thread(target=self.proc.wait, daemon=True)
        self.reaper.start()

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.reaper.join(timeout=10)


def _forge_died(s, helper, **fields):
    """A held verify record in `run.json` for `helper`'s group, from this boot, whose
    supervisor is gone."""
    forged = {
        "run": "forged",
        "stage": "verify",
        "state": "held",
        "pid": helper.proc.pid,
        "pgid": helper.proc.pid,
        "boot": s.ctx.boot_time(),
        "worktree_id": worktree_id(s.repo),
        "log": "",
        **fields,
    }
    omc = s.repo / ".cache" / "omc"
    omc.mkdir(parents=True, exist_ok=True)
    (omc / "run.json").write_text(json.dumps(forged) + "\n")


def test_died_record_whose_leader_start_differs_is_not_signalled(stage):
    """A pid recycled within one boot, or a container restarted (CLOCK_BOOTTIME is not
    namespaced): the boot matches, the leader's start time does not."""
    helper = _Helper(stage)
    try:
        start = stage.ctx.process_start(helper.proc.pid)
        assert isinstance(start, int)
        _forge_died(stage, helper, leader_start=start + 1)
        answer = kill_run(stage.ctx, "verify")
        assert answer.state == "died"
        assert helper.proc.poll() is None
        assert stage.ctx.signal_group(helper.proc.pid, 0)
    finally:
        helper.close()


def test_died_record_whose_leader_start_matches_is_ended_once(stage, monkeypatch):
    helper = _Helper(stage)
    try:
        _forge_died(stage, helper, leader_start=stage.ctx.process_start(helper.proc.pid))
        assert kill_run(stage.ctx, "verify").state == "died"
        _mutexproc.wait_for(lambda: not stage.ctx.signal_group(helper.proc.pid, 0), 10)
        # The record still says held; a second kill must not signal whatever has the
        # number now.
        sent = []
        monkeypatch.setattr(stage.ctx, "signal_group", lambda *a: sent.append(a) or False)
        assert kill_run(stage.ctx, "verify").state == "died"
        assert sent == []
    finally:
        helper.close()


def test_term_ignoring_hook_gets_sigkill(stage, monkeypatch):
    log = stage.tmp / "trap.log"
    proc = stage.ctx.spawn_logged(
        ["/bin/sh", "-c", 'trap "" TERM; echo ready; exec "$SLEEP" 60'],
        cwd=stage.tmp,
        log_path=log,
        extra_env={"SLEEP": host_bin("sleep")},
    )
    try:
        _mutexproc.wait_for(lambda: "ready" in log.read_text(), 10)
        t0 = time.monotonic()
        monkeypatch.setattr(stagerun, "KILL_GRACE_S", 1)
        stagerun._end_group(stage.ctx, proc, proc.pid)
        assert time.monotonic() - t0 < 3
        assert not stage.ctx.signal_group(proc.pid, 0)
        assert proc.poll() == -signal.SIGKILL
    finally:
        stage.ctx.signal_group(proc.pid, signal.SIGKILL)
        proc.wait()


def test_concurrent_starts_one_wins(stage):
    """Two real `omc internal stage start` processes race; the hook's --show-instructions
    holds both until both are in it, so both look for a current run before either has one."""
    arrived, gate = stage.tmp / "arrived", stage.tmp / "gate"
    arrived.mkdir()
    hook = stage.repo / ".omc" / "bin" / "check"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--show-instructions" ]; then\n'
        f'    : > {shlex.quote(str(arrived))}/"$$"\n'
        f"    while [ ! -e {shlex.quote(str(gate))} ]; do {_stagekit.sleep_line(0.02)}; done\n"
        f"    printf '%s' {shlex.quote(_stagekit.VALID_INSTRUCTIONS)}\n"
        "    exit 0\n"
        "fi\n"
        f"exec {_stagekit.sleep_line(60)}\n"
    )
    hook.chmod(0o755)
    argv = [sys.executable, "-m", "omc", "internal", "stage", "start", "check"]
    env = dict(stage.ctx.env)
    procs = [
        subprocess.Popen(
            argv, cwd=stage.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(2)
    ]
    try:
        _mutexproc.wait_for(lambda: len(list(arrived.iterdir())) == 2, 10)
        gate.touch()
        outs = [p.communicate(timeout=60) for p in procs]
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()
    won = [out for (out, _), p in zip(outs, procs, strict=True) if p.returncode == 0]
    lost = [err for (_, err), p in zip(outs, procs, strict=True) if p.returncode == 1]
    assert len(won) == 1 and len(lost) == 1, (outs, [p.returncode for p in procs])
    answer = json.loads(won[0].removeprefix("OMC_STAGE_RUN "))
    assert answer["state"] == "running"
    assert lost[0] == f"error: another start won: run {answer['run']} (check)\n"
    assert stage.record()["run"] == answer["run"]


def _entry(path: Path, age: float, now: float, *, directory=False) -> Path:
    if directory:
        path.mkdir(parents=True)
        (path / "inner.txt").write_text("x\n")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    os.utime(path, (now - age, now - age))
    return path


def test_prune_removes_only_old_entries(tmp_path):
    """Prune judges a run's files as one group: one young member keeps them all. It walks
    every project under the stage root: a project ID watch yielded to the committed one
    has no runs left to start, and its logs would otherwise stay forever."""
    root, now = tmp_path / "stages", time.time()
    prune(root, now=now)  # no stage logs yet: nothing to do
    old = [
        _entry(root / "p1" / "w1" / "check-20260101T000000-a.log", 8 * DAY, now),
        _entry(root / "p1" / "w1" / "check-20260101T000000-a.instructions.md", 8 * DAY, now),
        _entry(root / "p1" / "w2" / "verify-20260101T000000-b.d", 8 * DAY, now, directory=True),
        _entry(root / "p1" / "w2" / "verify-20260101T000000-b.result", 8 * DAY, now),
        _entry(root / "yielded" / "w1" / "check-20260101T000000-e.log", 8 * DAY, now),
    ]
    kept = [
        _entry(root / "p1" / "w1" / "check-20261115T000000-c.log", 1 * DAY, now),
        _entry(root / "p1" / "w1" / "check-20260101T000000-d.log", 8 * DAY, now),
        _entry(root / "p1" / "w1" / "check-20260101T000000-d.d", 1 * DAY, now, directory=True),
        _entry(root / "p2" / "w1" / "check-20261115T000000-f.log", 1 * DAY, now),
    ]
    prune(root, now=now)
    assert [p for p in old if p.exists()] == []
    assert [p for p in kept if p.exists()] == kept
    assert not (root / "p1" / "w2").exists()  # emptied worktree dirs go too
    assert not (root / "yielded" / "w1").exists()


def test_prune_keeps_live_held_run_of_another_worktree(stage, monkeypatch):
    """A hold has no maximum lifetime (A1): its supervisor keeps the run's stage dir and
    instructions fresh (never the log, whose mtime is `quiet`), so prune keeps the group."""
    monkeypatch.setattr(stagerun, "_TOUCH_S", 0.5)
    _held(stage, stage.pidfile())
    answer = _stagekit.wait(stage.ctx, "verify", 0)
    log, base = Path(answer.log), answer.log[: -len(".log")]
    members = [log, Path(answer.instructions), Path(f"{base}.d"), Path(f"{base}.result")]
    now = time.time()
    for member in members:
        os.utime(member, (now - 8 * DAY, now - 8 * DAY))
    ended = [
        _entry(log.parent / "check-20260101T000000-ended.log", 8 * DAY, now),
        _entry(log.parent / "check-20260101T000000-ended.instructions.md", 8 * DAY, now),
    ]
    _mutexproc.wait_for(lambda: Path(answer.instructions).stat().st_mtime > time.time() - 60, 5)
    assert Path(f"{base}.d").stat().st_mtime > time.time() - 60
    assert log.stat().st_mtime < now - 7 * DAY
    other = _stagekit.add_worktree(stage.repo, stage.tmp / "wt2", "b")
    stage.roots.append(other)
    monkeypatch.chdir(other)
    _stagekit.write_hook(other, "check", body="exit 0")
    assert start_run(stage.ctx, "check", []).run
    assert [m for m in members if not m.exists()] == []
    assert [e for e in ended if e.exists()] == []
    assert stage.record(stage.repo)["state"] == "held"


def test_start_prunes_old_logs(stage):
    project = project_id(stage.ctx, stage.repo)
    now = time.time()
    old = _entry(stage.tmp / "stages" / project / "gone-worktree" / "check-x.log", 8 * DAY, now)
    _stagekit.run_hook(stage, "check", "exit 0")
    assert not old.exists()


def test_run_foreground_streams_lines(stage):
    _stagekit.write_hook(stage.repo, "check", body="echo one; echo two; exit 0")
    lines: list[str] = []
    started = []
    answer = run_foreground(stage.ctx, "check", on_line=lines.append, on_start=started.append)
    assert answer.state == "passed"
    assert lines == ["one", "two", sentinel_line(0)]
    # The start answer, before any line: how a caller announces the log up front.
    assert [(a.run, a.log) for a in started] == [(answer.run, answer.log)]


def test_run_foreground_on_start_is_not_called_without_a_run(stage):
    started = []
    answer = run_foreground(stage.ctx, "build", on_line=lambda _: None, on_start=started.append)
    assert answer.state == "unconfigured"
    assert started == []


def test_run_foreground_returns_held(stage):
    _stagekit.write_hook(stage.repo, "verify", body=_holder().replace('"$1"', "/dev/null"))
    answer = run_foreground(stage.ctx, "verify", on_line=lambda _: None)
    assert answer.state == "held"


def test_run_foreground_kills_on_interrupt(stage):
    marker = stage.tmp / "hook.pid"
    _stagekit.write_hook(
        stage.repo,
        "check",
        body=f"echo $$ > {shlex.quote(str(marker))}\necho first\nexec {_stagekit.sleep_line(60)}",
    )

    def interrupt(line):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_foreground(stage.ctx, "check", on_line=interrupt)
    answer = _stagekit.wait(stage.ctx, "check", 0)
    assert answer.state == "killed"
    assert answer.reason.startswith("kill")
    stage.gone(stage.read_pid(marker))


def test_run_foreground_interrupt_survives_a_failing_kill(stage, monkeypatch):
    _stagekit.write_hook(stage.repo, "check", body=f"echo first\nexec {_stagekit.sleep_line(60)}")
    calls = []

    def failing_kill(ctx, stage_name, **kw):
        calls.append(stage_name)
        raise OmcError("no check run in this worktree")

    def interrupt(line):
        raise KeyboardInterrupt

    monkeypatch.setattr(stagerun, "kill_run", failing_kill)
    with pytest.raises(KeyboardInterrupt):
        run_foreground(stage.ctx, "check", on_line=interrupt)
    assert calls == ["check"]


def test_kill_that_does_not_end_in_time_errors(stage, monkeypatch):
    """A hook ignoring SIGTERM keeps its supervisor in the 30 s grace; a short wait errors."""
    body = f'trap "" TERM\necho $$ > "$1"\nexec {_stagekit.sleep_line(60)}'
    run = _stagekit.run_hook(stage, "check", body, args=(str(stage.pidfile()),))
    stage.read_pid(stage.pidfile())
    monkeypatch.setattr(stagerun, "STOP_WAIT_S", 1)
    with pytest.raises(OmcError, match=f"^the check run {run.run} did not end within 1s"):
        kill_run(stage.ctx, "check")
