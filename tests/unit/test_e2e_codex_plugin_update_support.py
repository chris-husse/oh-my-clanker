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


def _update_script(monkeypatch) -> str:
    seen = []
    monkeypatch.setattr(
        support, "run_in", lambda container, argv, *, env: seen.append(argv) or (0, "")
    )
    support.update(None, "/tmp/source")
    return seen[0][2]


def test_update_script_stubs_accept_every_real_parameter(monkeypatch):
    """The in-container update script replaces real omc functions with lambdas.
    A real function that grows a parameter (update_gitnexus gained `cfg`) must
    fail here, not as a TypeError four Docker tests later."""
    import ast
    import importlib
    import inspect

    stubs = [
        node
        for node in ast.walk(ast.parse(_update_script(monkeypatch)))
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Lambda)
        and isinstance(node.targets[0], ast.Attribute)
    ]
    assert {ast.unparse(node.targets[0]) for node in stubs} >= {"gitnexus.update_gitnexus"}
    for node in stubs:
        target = node.targets[0]
        name = f"omc.{target.value.id}.{target.attr}"
        real = getattr(importlib.import_module(f"omc.{target.value.id}"), target.attr)
        args = node.value.args
        named = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        for param in inspect.signature(real).parameters.values():
            if param.kind is param.VAR_POSITIONAL:
                ok = args.vararg is not None
            elif param.kind is param.VAR_KEYWORD:
                ok = args.kwarg is not None
            else:
                ok = param.name in named or args.kwarg is not None
            assert ok, f"{ast.unparse(node)} does not accept {param} of {name}"
