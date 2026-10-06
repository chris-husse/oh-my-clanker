"""In-session implement from the committed, unpublished design record."""

import pytest

from ..harness import run_in
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_audit_refusal,
    _assert_child_event,
    _assert_implemented_artifacts,
    _direct_audit,
    _direct_implement,
    _record_phase,
)

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


def _baseline(session, manifest):
    return session.snapshot(manifest["worktree"]), session.snapshot(manifest["repo"])


# Triggers /omc:implement, which exceeds the 300 s ceiling today (see the
# golden path's implemented stage): expensive tier until omc is faster.
@pytest.mark.expensive
@pytest.mark.timeout(1800)
@pytest.mark.variation("recorded")
def test_implement_commits_without_publication(stage_session):
    container, session, m = stage_session
    baseline, _ = _baseline(session, m)
    events_before = len(session.events()["events"])
    evidence = {"turns": []}
    session.send(_direct_implement("claude"))
    turn = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    _record_phase(session, m["repo"], m["worktree"], evidence, "implement", turn)
    _assert_child_event("claude", session.events()["events"][events_before:])
    _assert_implemented_artifacts(
        container, m["repo"], m["worktree"], m["branch"], evidence, baseline
    )


@pytest.mark.expensive
@pytest.mark.timeout(1900)
@pytest.mark.variation("recorded")
def test_audit_refuses_design_only_branch(stage_session):
    container, session, m = stage_session
    before, _ = _baseline(session, m)
    session.send(_direct_audit("claude"))
    turn = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    after = session.snapshot(m["worktree"])
    rc, status = run_in(container, ["git", "status", "--porcelain"], cwd=m["worktree"])
    assert rc == 0, status
    _assert_audit_refusal(before, after, status, turn["text"])


@pytest.mark.variation("recorded")
def test_failing_verify_blocks_implementation_handoff(stage_session):
    container, session, m = stage_session
    recorded, _ = _baseline(session, m)
    rc, out = run_in(container, ["touch", "/tmp/omc-external-verify-unavailable"])
    assert rc == 0, out

    session.send(_direct_implement("claude"))
    # Measured at 145.71 s on 2026-10-06; cancel the actor before pytest's
    # default 300 s ceiling so artifact checks still have time to finish.
    turn = session.wait_turn(240)
    after = session.snapshot(m["worktree"])

    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0 and "verify" in markers.splitlines(), (
        f"verify never ran; implementation answer: {turn['text']}"
    )
    rc, commits = run_in(
        container,
        ["git", "rev-list", "--count", f"{recorded['head']}..HEAD"],
        cwd=m["worktree"],
    )
    assert rc == 0 and int(commits.strip()) >= 1, f"no new task commits after record: {commits}"
    plans = [
        path
        for path in after["spec_plan"]
        if "/plans/" in path and path not in recorded["spec_plan"]
    ]
    assert plans, "implementation wrote no plan"
    for plan in plans:
        rc, _ = run_in(container, ["git", "cat-file", "-e", f"HEAD:{plan}"], cwd=m["worktree"])
        assert rc != 0, f"implementation committed plan before passing verify: {plan}"
    assert after["remote_refs"] == recorded["remote_refs"], "failing verify published branch"
    answer = turn["text"].lower()
    assert "verify" in answer and "?" in answer, (
        f"implementation did not ask a verify question: {turn['text']}"
    )
