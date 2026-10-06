"""The Codex global-instructions oracle keeps diagnostics distinct from tool use."""

import json

import pytest

from tests.e2e import test_e2e_global_instructions as global_instructions

HEADING = "# omc behavior layer (ships with the omc install)"
KNOWN_WARNING = "Ignoring unknown `features` requirement `ultrafast_mode` from requirements.toml"


def _stream(*items):
    return "\n".join(json.dumps({"type": "item.completed", "item": item}) for item in items) + "\n"


def _check(monkeypatch, output):
    monkeypatch.setattr(global_instructions, "require_token", lambda _provider: None)
    monkeypatch.setattr(global_instructions, "configure_omc", lambda *_args: None)
    monkeypatch.setattr(global_instructions, "make_work_repo", lambda *_args: "/tmp/test-repo")
    monkeypatch.setattr(global_instructions, "_codex_model", lambda: "test-model")
    monkeypatch.setattr(
        global_instructions,
        "_run",
        lambda _container, argv, **_kwargs: output if argv[0] == "codex" else "",
    )
    global_instructions.test_codex_reads_global_section_in_headless_session(object())


def test_codex_global_heading_accepts_known_nonfatal_feature_warning(monkeypatch):
    _check(
        monkeypatch,
        _stream(
            {"type": "error", "message": KNOWN_WARNING},
            {"type": "reasoning", "text": ""},
            {"type": "agent_message", "text": HEADING},
        ),
    )


@pytest.mark.parametrize(
    "items",
    [
        (),
        ({"type": "agent_message", "text": "# unrelated heading"},),
        ({"type": "error", "message": KNOWN_WARNING}, {"type": "reasoning", "text": ""}),
        (
            {"type": "error", "message": "Authentication failed"},
            {"type": "agent_message", "text": HEADING},
        ),
        (
            {"type": "error", "message": "Ignoring unknown `features` requirement `other_mode`"},
            {"type": "agent_message", "text": HEADING},
        ),
        (
            {"type": "command_execution", "command": "cat AGENTS.md"},
            {"type": "agent_message", "text": HEADING},
        ),
    ],
    ids=[
        "no-items",
        "wrong-heading",
        "missing-heading",
        "arbitrary-error",
        "other-feature-warning",
        "tool-item",
    ],
)
def test_codex_global_heading_rejects_invalid_item_streams(monkeypatch, items):
    with pytest.raises(AssertionError):
        _check(monkeypatch, _stream(*items))
