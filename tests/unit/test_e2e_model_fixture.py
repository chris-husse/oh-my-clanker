"""Keep live E2E task dispatch on the suite's chosen full model ID."""

import json
from types import SimpleNamespace

import pytest

from tests.e2e import harness
from tests.e2e.variations import test_from_start


class _Container:
    def get_wrapped_container(self):
        return self

    def exec_run(self, argv):
        assert argv[:2] == ["bash", "/repo/docker/setup-plugins.sh"]
        return SimpleNamespace(exit_code=0, output=b"")


@pytest.mark.parametrize(
    ("provider", "model", "env_name"),
    [
        ("claude", "claude-sonnet-5-5", "CLAUDE_E2E_MODEL"),
        ("codex", "gpt-6-astra", "CODEX_E2E_MODEL"),
    ],
)
def test_shared_fixture_pins_all_seven_choices(monkeypatch, provider, model, env_name):
    monkeypatch.setenv(env_name, model)
    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-test-auth")
    seen = []

    def run(_container, argv, **_kwargs):
        seen.append(argv)
        return 0, ""

    monkeypatch.setattr(harness, "run_in", run)
    monkeypatch.setattr(harness, "set_codex_container_policy", lambda *_args: None)
    assert harness.configure_omc(_Container(), provider) == model
    leaves = (
        "model",
        "tasks.design",
        "tasks.plan",
        "tasks.review",
        "tasks.simple",
        "tasks.medium",
        "tasks.high",
    )
    # One configure run for all seven pins: one validation probe per container.
    assert seen == [
        ["omc", "configure", "--set", f"llm.default={provider}"],
        [
            "omc",
            "configure",
            *[
                arg
                for leaf in leaves
                for arg in ("--set", f"llm.providers.{provider}.{leaf}={model}")
            ],
        ],
    ]


def test_claude_alias_is_resolved_before_fixture_is_saved(monkeypatch):
    monkeypatch.setenv("CLAUDE_E2E_MODEL", "sonnet")
    seen = []

    def run(_container, argv, **_kwargs):
        seen.append(argv)
        if argv[0] == "claude":
            return 0, "claude-sonnet-5-5\n"
        return 0, ""

    monkeypatch.setattr(harness, "run_in", run)
    assert harness.configure_omc(_Container(), "claude") == "claude-sonnet-5-5"
    assert seen[1][:3] == ["claude", "-p", "Reply with only your exact model id"]
    pins = [
        arg
        for argv in seen
        if argv[:2] == ["omc", "configure"]
        for arg in argv
        if arg.startswith("llm.providers.claude.")
    ]
    assert len(pins) == 7 and all(arg.endswith("=claude-sonnet-5-5") for arg in pins)


def test_seeded_model_variation_uses_resumed_session(monkeypatch):
    model = "claude-sonnet-5-5"
    manifest = {"model": model, "worktree": "/work/feature", "slug": "greeting-fix"}
    sent = []

    class Session:
        def send(self, prompt):
            sent.append(prompt)

        def wait_turn(self, _timeout):
            return {"session_id": manifest["slug"], "text": "done"}

    def read_artifact(_container, argv, **kwargs):
        assert argv[0] == "cat", "model command must run inside the resumed Claude session"
        assert not kwargs.get("env"), "the test must observe the session's own provider env"
        verdict = {
            "ok": True,
            "provider": "claude",
            "tasks": {
                task: {"model": model, "effort": ""}
                for task in ("orchestrator", "design", "plan", "review", "simple", "medium", "high")
            },
        }
        return 0, "OMC_MODELS " + json.dumps(verdict) + "\n"

    monkeypatch.setattr(test_from_start, "run_in", read_artifact)
    test_from_start.test_seeded_session_reports_pinned_task_models((object(), Session(), manifest))
    assert len(sent) == 1 and "omc internal models" in sent[0]
