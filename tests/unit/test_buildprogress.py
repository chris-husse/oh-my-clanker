import threading
import time as _time

from omc.buildprogress import SENTINEL_RE, ProgressTracker, follow_log, sentinel_line


def test_cargo_counter_sets_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("   Building [=====>                   ] 271/1288: foo, bar")
    assert t.percent == 21  # round(100*271/1288)


def test_cargo_parenthesized_counter():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("Compiling serde v1.0.200 (12/1288)")
    assert t.percent == 1


def test_pytest_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("........................ [ 28%]")
    assert t.percent == 28


def test_generic_bare_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("downloading 73% done")
    assert t.percent == 73


def test_latest_match_wins():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("step (1/10)")
    t.feed("step (9/10)")
    assert t.percent == 90


def test_total_zero_is_ignored():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("weird (3/0)")
    assert t.percent is None


def test_done_beyond_total_is_ignored():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("(1288/12)")  # reversed / nonsense counter
    assert t.percent is None


def test_generic_over_100_is_ignored():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("999% cpu")  # generic parser: values beyond 100 are noise, not progress
    assert t.percent is None


def test_no_match_is_indeterminate():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("Compiling serde v1.0.200")
    assert t.percent is None


def test_render_exact_bar_at_21_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 48802.0)  # 13h33m22s -> exercises HH:MM:SS
    t.feed("(271/1288)")
    assert t.render() == "[====>             ]  21% (13:33:22)"


def test_render_100_percent_has_no_arrow_overflow():
    t = ProgressTracker(start=0.0, clock=lambda: 61.0)
    t.feed("(10/10)")
    assert t.render() == "[==================] 100% (00:01:01)"


def test_render_indeterminate_bounces():
    t = ProgressTracker(start=0.0, clock=lambda: 5.0)
    first = t.render()
    second = t.render()
    assert " --% (00:00:05)" in first
    assert "<=>" in first and "<=>" in second
    assert first != second  # marker moved between redraws


def test_elapsed_uses_injected_clock():
    now = {"t": 100.0}
    t = ProgressTracker(start=100.0, clock=lambda: now["t"])
    now["t"] = 163.0
    assert t.elapsed() == 63.0


def test_sentinel_roundtrip():
    line = sentinel_line(3)
    assert line == "--- omc: stage finished (rc 3) ---"
    assert SENTINEL_RE.match(line)
    assert sentinel_line(None) == "--- omc: stage finished (rc ?) ---"
    assert SENTINEL_RE.match(sentinel_line(None))
    assert not SENTINEL_RE.match("prefix --- omc: stage finished (rc 3) ---")


def test_follow_log_reads_to_sentinel_and_exits_zero(tmp_path):
    log = tmp_path / "build.log"
    log.write_text("starting\n")

    def writer():
        _time.sleep(0.2)
        with log.open("a") as fh:
            fh.write("(5/10)\n")
            fh.write("--- omc: stage finished (rc 0) ---\n")

    t = threading.Thread(target=writer)
    t.start()
    rc = follow_log(str(log), poll=0.05)
    t.join()
    assert rc == 0


def test_follow_log_missing_file_is_usage_error(tmp_path, monkeypatch):
    monkeypatch.setattr("omc.buildprogress._GRACE", 0.05)
    rc = follow_log(str(tmp_path / "nope.log"), poll=0.01)
    assert rc == 2
