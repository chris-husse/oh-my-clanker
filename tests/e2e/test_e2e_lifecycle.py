"""Provider integration cases: Codex (gate) and Claude session protocol. One or two turns each."""

from __future__ import annotations

import os

import pytest

from .codex_auth import require_codex_ready
from .conversation import Conversation, save_evidence
from .harness import configure_omc, make_work_repo, require_token, run_in
from .lifecycle_helpers import (
    _assert_skill_reads,
    _codex_fixture_trust_override,
    _codex_model,
    _configure_claude_conversation,
    _fixture,
    _image_provenance,
    _installed_skill_paths,
    _set_write_capability,
)

pytestmark = pytest.mark.e2e


@pytest.mark.codex_gate
@pytest.mark.e2e_provider("codex")
def test_codex_conversation_capabilities(container):
    require_token("codex")
    configure_omc(container, "codex")
    metadata = require_codex_ready(container)
    repo = make_work_repo(container, "/work/capability")
    model = _codex_model()
    skill_paths = _installed_skill_paths(container, "codex")
    _set_write_capability(container)
    prompt = (
        "Use a real collaboration subagent to write CHILD-READY into child-ready.txt "
        "in this repository. "
        "Wait for the child and verify the file. Also load and inspect the installed omc:start "
        "and superpowers:brainstorming skill instructions by reading their actual skill files. "
        "Use a file-reading tool to read at least the frontmatter of the exact installed files "
        f"{skill_paths['omc:start']} and {skill_paths['superpowers:brainstorming']}. "
        "Report whether both were loaded."
    )
    evidence = {
        "provider": "codex",
        "model_requested": model,
        "metadata": metadata,
        "installed_skill_paths": skill_paths,
        "image": _image_provenance(container),
    }
    try:
        with Conversation(container) as session:
            session.start(
                [
                    "codex",
                    "--no-alt-screen",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-m",
                    model,
                    "-c",
                    "tui.terminal_title=[]",
                    "-c",
                    _codex_fixture_trust_override(repo),
                    prompt,
                ],
                repo,
                "codex",
                skill_paths=skill_paths,
            )
            turn = session.wait_turn(float(os.environ.get("OMC_E2E_CAPABILITY_TIMEOUT", "300")))
            evidence["turn"] = turn
            evidence["events"] = session.events()
        rc, content = run_in(container, ["cat", f"{repo}/child-ready.txt"])
        evidence["child_file"] = content if rc == 0 else None
        assert rc == 0 and content.strip() == "CHILD-READY", turn["text"]
        assert any(
            e.get("type") == "function_call" and e.get("name") == "spawn_agent"
            for e in evidence["events"]["events"]
        ), evidence["events"]
        _assert_skill_reads(evidence["events"]["events"], skill_paths)
    finally:
        save_evidence("codex-capability", evidence)


@pytest.mark.codex_gate
@pytest.mark.e2e_provider("codex")
def test_codex_native_skill_mention_is_submitted(container):
    """A direct Codex skill mention must start a real turn."""
    require_token("codex")
    configure_omc(container, "codex")
    require_codex_ready(container)
    repo = make_work_repo(container, "/work/native-slash")
    model = _codex_model()
    with Conversation(container) as session:
        session.start(
            [
                "codex",
                "--no-alt-screen",
                "--dangerously-bypass-approvals-and-sandbox",
                "-m",
                model,
                "-c",
                "tui.terminal_title=[]",
                "Reply exactly READY.",
            ],
            repo,
            "codex",
        )
        first = session.wait_turn(120)
        assert "READY" in first["text"]
        session.send("$omc:slug")
        session.wait_started(12)
        command = session.wait_turn(120)
        assert command["turn_id"], command
        assert "OMC_SLUG" in command["text"], command


def test_claude_conversation_capabilities(container):
    from .conversation import ClaudeConversation, parse_claude_stream

    model, _, metadata = _configure_claude_conversation(container)
    repo = make_work_repo(container, "/work/claude-capability")
    skill_paths = _installed_skill_paths(container, "claude")
    prompt = (
        "Use a real child agent to write CHILD-READY into child-ready.txt. "
        "Wait for it and verify the file. Use the Read tool to read at least the frontmatter "
        f"of these exact installed skill files: {skill_paths['omc:start']} and "
        f"{skill_paths['superpowers:brainstorming']}."
    )
    evidence = {
        "provider": "claude",
        "model_requested": model,
        "metadata": metadata,
        "installed_skill_paths": skill_paths,
        "image": _image_provenance(container),
    }
    try:
        with ClaudeConversation(container, model) as session:
            output = session.run_raw(
                [
                    "claude",
                    "-p",
                    prompt,
                    "--model",
                    model,
                    "--output-format",
                    "stream-json",
                    "--verbose",
                    "--dangerously-skip-permissions",
                ],
                repo,
                300,
            )
        turn = parse_claude_stream(output, skill_paths)
        evidence["turn"] = turn
        assert turn["model_observed"] == model, turn
        rc, content = run_in(container, ["cat", f"{repo}/child-ready.txt"])
        evidence["child_file"] = content if rc == 0 else None
        assert rc == 0 and content.strip() == "CHILD-READY", turn["text"]
        assert any(event.get("name") in ("Task", "Agent") for event in turn["events"]), turn
        _assert_skill_reads(turn["events"], skill_paths)
    finally:
        save_evidence("claude-capability", evidence)


def test_claude_named_session_resume_protocol(container):
    """Exercise omc's real named session and two stream-json resumed turns."""
    from .conversation import ClaudeConversation

    model, _, metadata = _configure_claude_conversation(container)
    repo = _fixture(container)
    evidence = {
        "provider": "claude",
        "model_requested": model,
        "metadata": metadata,
        "image": _image_provenance(container),
        "turns": [],
    }
    try:
        with ClaudeConversation(container, model) as session:
            session.start(
                ["omc", "start", "Please orient me to the greeting module.", "--headless"],
                repo,
                "claude",
            )
            first = session.wait_turn(300)
            evidence["turns"].append(first)
            assert first["session_id"]
            session.send("Reply with exactly RESUME-OK. Do no repository work.")
            second = session.wait_turn(300)
            evidence["turns"].append(second)
            assert "RESUME-OK" in second["text"]
            assert second["model_observed"] == model
            assert second["provider_session_id"]
            assert second["session_id"] == first["session_id"]
            session.send("Reply with exactly SECOND-OK. Do no repository work.")
            third = session.wait_turn(300)
            evidence["turns"].append(third)
            assert "SECOND-OK" in third["text"]
            assert third["session_id"] == first["session_id"]
            assert third["provider_session_id"] == second["provider_session_id"]
    finally:
        save_evidence("claude-named-resume", evidence)
