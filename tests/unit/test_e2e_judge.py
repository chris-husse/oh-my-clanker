"""Provider judge launch contracts at the container execution boundary."""

import pytest

from tests.e2e import judge as judge_module


@pytest.mark.parametrize("configured_model", [None, "configured-top-tier-model"])
def test_codex_judge_runs_read_only_outside_a_trusted_git_directory(monkeypatch, configured_model):
    if configured_model is None:
        monkeypatch.delenv("CODEX_E2E_MODEL", raising=False)
    else:
        monkeypatch.setenv("CODEX_E2E_MODEL", configured_model)
    calls = []

    def run_in(container, argv, *, timeout):
        calls.append((argv, timeout))
        # Observed Codex CLI behavior in the E2E container's /work directory.
        if "--skip-git-repo-check" not in argv:
            return 1, "Not inside a trusted directory and --skip-git-repo-check was not specified."
        return 0, '{"passed": true, "reasons": ["seeded ticket context is present"]}'

    monkeypatch.setattr(judge_module, "run_in", run_in)
    verdict = judge_module.judge(
        object(),
        "codex",
        "A seeded session started.",
        ["The session engages with the ticket."],
        "The session investigated the login timeout.",
    )
    assert verdict == {"passed": True, "reasons": ["seeded ticket context is present"]}
    assert len(calls) == 1
    argv, timeout = calls[0]
    assert argv[:-1] == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-m",
        configured_model or "gpt-6-astra",
    ]
    assert "The session investigated the login timeout." in argv[-1]
    assert timeout == 300


def test_codex_judge_rejects_empty_model_before_launch(monkeypatch):
    monkeypatch.setenv("CODEX_E2E_MODEL", "")

    def unexpected_launch(*args, **kwargs):
        pytest.fail("empty model must not fall back to an uncontrolled CLI default")

    monkeypatch.setattr(judge_module, "run_in", unexpected_launch)
    with pytest.raises(AssertionError, match="CODEX_E2E_MODEL"):
        judge_module.judge(object(), "codex", "scenario", ["rubric"], "artifact")


@pytest.mark.parametrize("output", ["no verdict", "{}", '{"passed": "yes"}'])
def test_codex_judge_still_rejects_unparseable_verdict(monkeypatch, output):
    monkeypatch.setenv("CODEX_E2E_MODEL", "gpt-6-astra")
    monkeypatch.setattr(judge_module, "run_in", lambda *args, **kwargs: (0, output))
    with pytest.raises(AssertionError, match="no parseable verdict"):
        judge_module.judge(object(), "codex", "scenario", ["rubric"], "artifact")
