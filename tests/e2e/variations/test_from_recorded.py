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
