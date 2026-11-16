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
    "test_codex_review_handoff_from_claude_implementation",
    "test_design_auto_advances_to_push",
    "test_post_design_no_go_then_go",
    # the expensive variations: no other recipe collects them
    # (`expensive-e2e-tests` excludes `variation`)
    "test_failing_build_blocks_publication",
    "test_unrelated_untracked_draft_blocks_publication",
    "test_audit_repairs_committed_greeting_drift",
    "test_implement_commits_without_publication",
    "test_audit_refuses_design_only_branch",
}
HOOKS = {
    "test_docs_only_change_skips_verify_by_needs_verify",
    "test_product_change_runs_verify_by_needs_verify",
    "test_red_pre_push_is_fixed_then_pushed",
    "test_post_rebase_before_implementation_skips_the_check",
}
GOLDEN = {
    "test_stage_start",
    "test_stage_design",
    "test_stage_agreed",
    "test_stage_recorded",
    "test_stage_implemented",
}


_COLLECT_ENV = {"CODEX_AUTH_VOLUME": "omc-e2e-codex-auth", "OMC_E2E_NO_DOCKER": "1"}


def _collect(recipe: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["just", recipe, *args, "--collect-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **_COLLECT_ENV},
    )


def _collected(recipe: str, *args: str) -> set[str]:
    run = _collect(recipe, *args)
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


def test_default_golden_collects_through_implemented_without_audited():
    assert _collected("golden") == GOLDEN


def test_default_e2e_collects_implement_never_runs_verify_variation():
    assert "test_implement_never_runs_verify" in _collected("e2e-tests")


def test_default_e2e_collects_write_plan_variation():
    assert "test_write_plan_commits_plan_only" in _collected("e2e-tests")


def test_default_e2e_never_collects_the_auto_advance_chain():
    # one /omc:design turn that runs to the push: expensive tier only
    assert "test_design_auto_advances_to_push" not in _collected("e2e-tests")


def test_default_e2e_never_collects_the_post_design_loop():
    # two hardening passes cannot fit the 300 s ceiling: expensive tier only
    assert "test_post_design_no_go_then_go" not in _collected("e2e-tests")


def test_default_e2e_collects_the_hook_scenarios():
    collected = _collected("e2e-tests")
    assert HOOKS <= collected, HOOKS - collected


def test_e2e_tests_subset_runs_a_golden_node_id():
    # the parallel pass collects nothing for a golden node id; that is not a failure
    assert (
        _collected("e2e-tests", "tests/e2e/golden/test_golden_claude.py::test_stage_recorded")
        == GOLDEN
    )


def test_e2e_tests_subset_runs_a_variation_after_the_golden_path():
    # the golden path always runs in full first: the variation forks its snapshots
    assert _collected(
        "e2e-tests", "tests/e2e/variations/test_from_recorded.py::test_write_plan_commits_plan_only"
    ) == GOLDEN | {"test_write_plan_commits_plan_only"}


def test_e2e_tests_subset_still_fails_when_nothing_matches():
    run = _collect("e2e-tests", "tests/e2e/variations/test_from_recorded.py::test_no_such_test")
    assert run.returncode != 0, run.stdout + run.stderr
