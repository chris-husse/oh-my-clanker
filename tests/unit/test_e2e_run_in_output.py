"""The Docker E2E harness can read a provider's JSON stdout without stderr warnings."""

from types import SimpleNamespace

from tests.e2e.harness import run_in


class _Container:
    def __init__(self):
        self.demux = None

    def get_wrapped_container(self):
        return self

    def exec_run(self, _argv, *, environment, demux=False):
        self.demux = demux
        stdout = b'{"models": []}\n'
        stderr = b"Warning: cached metadata expired\n"
        return SimpleNamespace(exit_code=0, output=(stdout, stderr) if demux else stdout + stderr)


def test_run_in_can_return_only_stdout_for_json_catalog():
    container = _Container()
    rc, output = run_in(container, ["codex", "debug", "models"], stdout_only=True)
    assert rc == 0
    assert output == '{"models": []}\n'
    assert container.demux is True


def test_run_in_keeps_combined_output_by_default():
    container = _Container()
    rc, output = run_in(container, ["codex", "debug", "models"])
    assert rc == 0
    assert output == '{"models": []}\nWarning: cached metadata expired\n'
    assert container.demux is False
