"""`omc title`: quiet, config-free, exit codes 0/1/2, marker cooldown, serialized apply."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from omc.cli import main
from omc.toolctx import ToolContext

UUID = "38B11221-B7E1-4F36-8A3B-50D549172632"
WORKER = [sys.executable, "-m", "omc.iterm2_title", "--session-id", UUID]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "omc home"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ITERM_SESSION_ID", f"w0t1p0:{UUID}")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    return home


@pytest.fixture
def worker(monkeypatch):
    """Fake SDK worker: records (argv, timeout); driven by `worker.rc` / `worker.raises`."""
    state = type("Worker", (), {"calls": [], "rc": 0, "raises": None})()

    def fake(self, argv, *, timeout, cwd=None, extra_env=None):
        state.calls.append((list(argv), timeout))
        if state.raises is not None:
            raise state.raises
        return subprocess.CompletedProcess(list(argv), state.rc, "", "secret-auth-value")

    monkeypatch.setattr(ToolContext, "run_bounded", fake)
    return state


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Fake clock + sleep for omc.title: sleeping advances the clock, never the test."""
    import omc.title as title_mod

    state = type("Clock", (), {"offset": 0.0, "sleeps": [], "on_sleep": None})()
    real = time.time
    monkeypatch.setattr(title_mod, "_clock", lambda: real() + state.offset)

    def fake_sleep(seconds):
        state.sleeps.append(seconds)
        if state.on_sleep is not None:
            state.on_sleep()
        state.offset += seconds

    monkeypatch.setattr(title_mod, "_sleep", fake_sleep)
    return state


def _marker(home):
    return home / "title-failed" / UUID


def test_set_runs_exact_worker_quietly(home, worker, capsys):
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert worker.calls == [([*WORKER, "--", "feature/name"], 5.0)]
    assert capsys.readouterr() == ("", "")  # no banner, no stdout, nothing on success
    assert not _marker(home).exists()


def test_set_passes_hostile_title_literally(home, worker):
    hostile = "feature/cost$USD(parent)\\(session.name); rm -rf"
    assert main(["title", "set", "--", hostile]) == 0
    assert worker.calls[0][0][-1] == hostile


def test_release_runs_exact_worker_quietly(home, worker, capsys):
    assert main(["title", "release"]) == 0
    assert worker.calls == [([*WORKER, "--release"], 5.0)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("title", ["bad\007title", "bad\x9dtitle", ""])
def test_bad_titles_are_refused_before_any_worker(home, worker, capsys, title):
    assert main(["title", "set", "--", title]) == 2
    assert worker.calls == []
    err = capsys.readouterr().err
    assert err.startswith("error: omc title:") and "Oh My Clanker" not in err
    assert not _marker(home).exists()


@pytest.mark.parametrize("session", [None, "", "bad", f"unrelated:{UUID}"])
def test_missing_or_malformed_session_is_refused(home, worker, capsys, monkeypatch, session):
    if session is None:
        monkeypatch.delenv("ITERM_SESSION_ID")
    else:
        monkeypatch.setenv("ITERM_SESSION_ID", session)
    assert main(["title", "set", "--", "feature/name"]) == 2
    assert main(["title", "release"]) == 2
    assert worker.calls == []
    err = capsys.readouterr().err
    assert "ITERM_SESSION_ID" in err and "Traceback" not in err


def test_worker_failure_writes_marker_and_one_line(home, worker, capsys):
    worker.rc = 3
    assert main(["title", "set", "--", "feature/name"]) == 1
    marker = _marker(home)
    lines = marker.read_text().splitlines()
    assert len(lines) == 2
    assert lines[0] == (
        "omc: iTerm2 tab title update failed: worker exit 3 (retry: omc title set -- <branch>)"
    )
    assert lines[1].startswith("at 20")  # human-readable timestamp, never parsed
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == lines[0] + "\n"
    assert "secret" not in captured.err  # the worker's stderr is never surfaced


def test_worker_timeout_writes_marker(home, worker, capsys):
    worker.raises = TimeoutError("command timed out after 5s")
    assert main(["title", "release"]) == 1
    assert "timed out after 5s" in _marker(home).read_text().splitlines()[0]
    assert capsys.readouterr().err.count("\n") == 1


def _fresh_marker(home):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("omc: iTerm2 tab title update failed: worker exit 1 (retry: ...)\nat now\n")
    return marker


def _aged_marker(home, age):
    marker = _fresh_marker(home)
    then = time.time() - age
    os.utime(marker, (then, then))
    return marker


def test_throttled_apply_waits_out_the_cooldown_then_applies(home, worker, clock, capsys):
    from omc.title import COOLDOWN_SECONDS

    marker = _aged_marker(home, 20)
    request = _request(home, "set feature/name\n")
    assert main(["title", "apply", str(request)]) == 0
    assert len(clock.sleeps) == 1
    assert COOLDOWN_SECONDS - 20 - 2 < clock.sleeps[0] <= COOLDOWN_SECONDS - 20
    assert worker.calls == [([*WORKER, "--", "feature/name"], 5.0)]
    assert Path(str(request) + ".applied").read_text() == "set feature/name\n"
    assert not marker.exists()  # the retry succeeded
    assert capsys.readouterr() == ("", "")


def test_throttled_apply_sleep_is_bounded_by_the_cooldown(home, worker, clock):
    from omc.title import COOLDOWN_SECONDS

    _aged_marker(home, 0)
    request = _request(home, "release\n")
    assert main(["title", "apply", str(request)]) == 0
    assert len(clock.sleeps) == 1 and 0 < clock.sleeps[0] <= COOLDOWN_SECONDS
    assert worker.calls == [([*WORKER, "--release"], 5.0)]


def test_throttled_apply_applies_the_request_rewritten_while_it_slept(home, worker, clock):
    _aged_marker(home, 30)
    request = _request(home, "set first\n")
    clock.on_sleep = lambda: request.write_text("set second\n")
    assert main(["title", "apply", str(request)]) == 0
    assert worker.calls == [([*WORKER, "--", "second"], 5.0)]
    assert Path(str(request) + ".applied").read_text() == "set second\n"


def test_throttled_apply_skips_a_request_another_helper_applied_meanwhile(home, worker, clock):
    _aged_marker(home, 30)
    request = _request(home, "set feature/x\n")
    applied = Path(str(request) + ".applied")
    clock.on_sleep = lambda: applied.write_text("set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert len(clock.sleeps) == 1 and worker.calls == []


def test_throttled_apply_does_not_hold_the_lock_while_sleeping(home, worker, clock):
    import fcntl

    _aged_marker(home, 30)
    request = _request(home, "set feature/x\n")
    taken = []

    def try_lock():
        with (home / "title-lock" / UUID).open("a+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if the sleeper holds it
            taken.append(True)
            fcntl.flock(fh, fcntl.LOCK_UN)

    clock.on_sleep = try_lock
    assert main(["title", "apply", str(request)]) == 0
    assert taken == [True] and len(worker.calls) == 1


def test_set_bypasses_cooldown_and_success_removes_marker(home, worker, capsys):
    marker = _fresh_marker(home)
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert worker.calls == [([*WORKER, "--", "feature/name"], 5.0)]
    assert capsys.readouterr() == ("", "")
    assert not marker.exists()


def test_set_in_cooldown_still_reports_worker_failure(home, worker, capsys):
    _fresh_marker(home)
    worker.rc = 1
    assert main(["title", "set", "--", "feature/name"]) == 1
    assert len(worker.calls) == 1
    assert "worker exit 1" in capsys.readouterr().err


def test_release_bypasses_cooldown(home, worker):
    marker = _fresh_marker(home)
    assert main(["title", "release"]) == 0
    assert worker.calls == [([*WORKER, "--release"], 5.0)]
    assert not marker.exists()


def test_stale_marker_retries_and_success_removes_it(home, worker):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("old\nat then\n")
    old = time.time() - 61
    os.utime(marker, (old, old))
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert len(worker.calls) == 1
    assert not marker.exists()


def test_future_marker_mtime_never_throttles(home, worker):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("skewed\nat later\n")
    future = time.time() + 3600
    os.utime(marker, (future, future))
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert len(worker.calls) == 1
    assert not marker.exists()


def test_unwritable_home_reports_one_line_and_exits_one(home, worker, capsys):
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_text("a file where the omc home should be\n")
    worker.rc = 1
    assert main(["title", "set", "--", "feature/name"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "worker exit 1" in captured.err and "marker not written" in captured.err
    assert "Traceback" not in captured.err


def _request(home, text):
    path = home / "title-request" / f"{UUID}-4242"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_apply_performs_request_and_records_applied(home, worker, capsys):
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert worker.calls == [([*WORKER, "--", "feature/x"], 5.0)]
    assert Path(str(request) + ".applied").read_text() == "set feature/x\n"
    assert (home / "title-lock" / UUID).exists()
    assert capsys.readouterr() == ("", "")


def test_apply_release_request(home, worker):
    request = _request(home, "release\n")
    assert main(["title", "apply", str(request)]) == 0
    assert worker.calls == [([*WORKER, "--release"], 5.0)]


def test_two_helpers_on_one_request_perform_one_write(home, worker):
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert main(["title", "apply", str(request)]) == 0
    assert len(worker.calls) == 1


def test_request_rewritten_before_lock_is_applied_by_holder_not_newcomer(home, worker, monkeypatch):
    import fcntl

    import omc.title as title_mod

    request = _request(home, "set first\n")
    real_flock = fcntl.flock
    rewritten = []

    def flock_then_rewrite(fh, op):
        real_flock(fh, op)
        if not rewritten:  # the holder was launched for "first"; "second" lands before it re-reads
            request.write_text("set second\n")
            rewritten.append(True)

    monkeypatch.setattr(title_mod.fcntl, "flock", flock_then_rewrite)
    assert main(["title", "apply", str(request)]) == 0  # holder
    assert main(["title", "apply", str(request)]) == 0  # newcomer launched for "second"
    assert worker.calls == [([*WORKER, "--", "second"], 5.0)]
    assert Path(str(request) + ".applied").read_text() == "set second\n"


def test_lock_is_held_across_the_worker(home, worker, monkeypatch):
    request = _request(home, "set feature/x\n")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking(self, argv, *, timeout, cwd=None, extra_env=None):
        calls.append(list(argv))
        entered.set()
        assert release.wait(10)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    monkeypatch.setattr(ToolContext, "run_bounded", blocking)
    results = {}
    holder = threading.Thread(
        target=lambda: results.update(holder=main(["title", "apply", str(request)]))
    )
    holder.start()
    assert entered.wait(10)
    newcomer = threading.Thread(
        target=lambda: results.update(newcomer=main(["title", "apply", str(request)]))
    )
    newcomer.start()
    time.sleep(0.5)
    assert newcomer.is_alive() and len(calls) == 1  # blocked on the lock while the worker runs
    release.set()
    holder.join(10)
    newcomer.join(10)
    assert results == {"holder": 0, "newcomer": 0}
    assert len(calls) == 1  # the newcomer found .applied == request and wrote nothing


def test_apply_still_throttled_after_its_one_wait_gives_up_quietly(home, worker, clock, capsys):
    marker = _aged_marker(home, 30)

    def another_helper_failed_meanwhile():
        now = time.time() + clock.offset + clock.sleeps[-1]
        os.utime(marker, (now, now))

    clock.on_sleep = another_helper_failed_meanwhile
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert len(clock.sleeps) == 1  # one wait, never a second: bounded by cooldown + deadline
    assert worker.calls == []
    assert not Path(str(request) + ".applied").exists()  # a later retry must re-apply
    assert marker.exists()  # untouched: the hook judges retries by mtime
    assert capsys.readouterr() == ("", "")


def test_apply_failure_does_not_record_applied(home, worker):
    worker.rc = 1
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 1
    assert not Path(str(request) + ".applied").exists()
    assert _marker(home).exists()


@pytest.mark.parametrize("text", ["", "set \n", "set\n", "pin feature/x\n", "set bad\007\n", "\n"])
def test_apply_refuses_malformed_request_without_marker(home, worker, capsys, text):
    request = _request(home, text)
    assert main(["title", "apply", str(request)]) == 2
    assert worker.calls == []
    assert not _marker(home).exists()
    err = capsys.readouterr().err
    assert err.startswith("error: omc title:") and "Traceback" not in err


def test_apply_refuses_missing_request_file(home, worker, capsys):
    assert main(["title", "apply", str(home / "nope")]) == 2
    assert worker.calls == []
    assert "request file" in capsys.readouterr().err


def test_parse_request_contract():
    from omc.errors import Refusal
    from omc.title import parse_request

    assert parse_request("set feature/x\n") == ("set", "feature/x")
    assert parse_request("set a b\nignored second line\n") == ("set", "a b")
    assert parse_request("release\n") == ("release", None)
    with pytest.raises(Refusal):
        parse_request("release please\n")


def test_title_never_loads_configuration(home, worker, monkeypatch):
    import omc.config.resolve as resolve

    monkeypatch.setattr(
        resolve, "load_effective", lambda ctx: pytest.fail("omc title loaded configuration")
    )
    assert main(["title", "set", "--", "feature/name"]) == 0


def test_apply_refuses_non_utf8_request(home, worker, capsys):
    request = home / "title-request" / f"{UUID}-4242"
    request.parent.mkdir(parents=True)
    request.write_bytes(b"set feature/\xff\xfe\n")
    assert main(["title", "apply", str(request)]) == 2
    assert worker.calls == []
    assert not _marker(home).exists()
    assert not Path(str(request) + ".applied").exists()
    err = capsys.readouterr().err
    assert err.startswith("error: omc title:") and err.count("\n") == 1


def test_apply_keeps_carriage_return_and_refuses_it(home, worker):
    request = _request(home, "")
    request.write_bytes(b"set feature/a\rb\n")
    assert main(["title", "apply", str(request)]) == 2
    assert worker.calls == []


def test_apply_with_unwritable_home_reports_one_error_line(home, worker, capsys, tmp_path):
    request = tmp_path / "request"
    request.write_text("set feature/x\n")
    home.write_text("a file where the omc home should be\n")
    assert main(["title", "apply", str(request)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: omc title:") and err.count("\n") == 1 and worker.calls == []
