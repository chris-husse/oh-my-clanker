"""The E2E recipes must select exactly their tiers: the Codex gate collects the
serial Codex integration cases, the expensive tier collects the monolithic
lifecycle runs, and neither leaks into the other."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CODEX_GATE = {
    "test_codex_conversation_capabilities",
    "test_codex_native_skill_mention_is_submitted",
}
FULL = {
    "test_start_context_waits_for_direct_implement[codex]",
    "test_start_context_waits_for_direct_implement[claude]",
    "test_critical_answer_resumes_authorized_implementation[codex]",
    "test_critical_answer_resumes_authorized_implementation[claude]",
    "test_failing_build_blocks_publication[codex]",
    "test_failing_build_blocks_publication[claude]",
    "test_codex_implement_handoff_from_claude_record",
}


def _collected(recipe: str) -> set[str]:
    run = subprocess.run(
        ["just", recipe, "--collect-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "CODEX_AUTH_VOLUME": "omc-e2e-codex-auth", "OMC_E2E_NO_DOCKER": "1"},
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return {m.group(1) for m in re.finditer(r"::(test_[^\s]+)$", run.stdout, re.M)}


def test_codex_gate_collects_every_codex_case_and_nothing_else():
    collected = _collected("codex-gate")
    assert CODEX_GATE <= collected, collected
    assert all("codex" in test_id for test_id in collected), collected
    # the hour-long monolithic runs never ride along with the gate
    assert not any("lifecycle_full" in test_id for test_id in collected), collected


def test_lifecycle_full_collects_the_monolithic_runs():
    assert _collected("lifecycle-full") == FULL
