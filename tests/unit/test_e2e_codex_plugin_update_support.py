"""Readiness waits must stay bounded even when Docker probes are slow."""

from types import SimpleNamespace

import pytest

from tests.e2e import codex_plugin_update as support


@pytest.fixture
def readiness(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[], probes=[], stopped=False, ready_at=None)

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    def run(container, argv, *, env, timeout=600):
        if "/__stop" in argv[-1]:
            clock.stopped = True
            return 0, ""
        clock.probes.append((clock.now, timeout))
        clock.now += min(clock.probe_duration, timeout)
        return (0 if clock.ready_at == len(clock.probes) else 1), ""

    monkeypatch.setattr(support.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(support.time, "sleep", sleep)
    monkeypatch.setattr(support, "run_in", run)
    raw = SimpleNamespace(exec_run=lambda *args, **kwargs: SimpleNamespace(exit_code=0))
    clock.container = SimpleNamespace(get_wrapped_container=lambda: raw)
    return clock


def test_readiness_deadline_bounds_slow_probes(readiness):
    readiness.probe_duration = 1.0
    with pytest.raises(pytest.fail.Exception, match="did not start"):
        with support.git_http_server(readiness.container):
            pytest.fail("unready server must not enter the scenario")

    assert readiness.now <= 3.0
    assert all(start < 3.0 for start, _ in readiness.probes)
    assert all(0 < timeout <= min(1.0, 3.0 - start) for start, timeout in readiness.probes)


def test_readiness_retries_use_capped_backoff(readiness):
    readiness.probe_duration = 0.0
    with pytest.raises(pytest.fail.Exception, match="did not start"):
        with support.git_http_server(readiness.container):
            pytest.fail("unready server must not enter the scenario")

    assert readiness.sleeps[0] < readiness.sleeps[1] < readiness.sleeps[2]
    assert max(readiness.sleeps) <= 0.5
    assert readiness.now == pytest.approx(3.0)


def test_readiness_success_enters_scenario_and_stops_server(readiness):
    readiness.probe_duration = 0.0
    readiness.ready_at = 2
    with support.git_http_server(readiness.container):
        assert not readiness.stopped
        assert len(readiness.probes) == 2
    assert readiness.stopped
