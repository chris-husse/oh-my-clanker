"""The local lifecycle recipe must collect both real provider columns."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_lifecycle_recipe_collects_both_providers_and_selected_reruns():
    codex = {
        "test_codex_conversation_capabilities",
        "test_codex_native_skill_mention_is_submitted",
        "test_start_context_waits_for_direct_implement[codex]",
        "test_critical_answer_resumes_authorized_implementation[codex]",
        "test_failing_build_blocks_publication[codex]",
    }
    claude = {
        "test_claude_conversation_capabilities",
        "test_claude_named_session_resume_protocol",
        "test_start_context_waits_for_direct_implement[claude]",
        "test_critical_answer_resumes_authorized_implementation[claude]",
        "test_failing_build_blocks_publication[claude]",
    }
    for selector, expected in (
        ((), codex | claude),
        (("-k", "codex"), codex),
        (("-k", "claude"), claude),
    ):
        run = subprocess.run(
            ["just", "lifecycle-tests", "--collect-only", *selector],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        nodes = {
            line.split("::", 1)[1]
            for line in run.stdout.splitlines()
            if line.startswith("tests/e2e/test_e2e_lifecycle.py::")
        }
        assert nodes == expected
