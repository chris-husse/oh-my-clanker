"""Audit variations forked from the committed, unpublished implementation."""

import pytest

from ..harness import run_in
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_audited_artifacts,
    _direct_audit,
    _record_phase,
)

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude"), pytest.mark.expensive]


@pytest.mark.timeout(1900)
@pytest.mark.variation("implemented")
def test_failing_build_blocks_publication(stage_session):
    container, session, m = stage_session
    session.bind(m["implement_session"], m["worktree"], m["repo"])
    before = session.snapshot(m["worktree"])
    rc, original_stage = run_in(container, ["cat", f"{m['worktree']}/.omc/stage-scripts/build.sh"])
    assert rc == 0 and "omc-external-build-unavailable" in original_stage
    rc, out = run_in(container, ["git", "status", "--porcelain"], cwd=m["worktree"])
    assert rc == 0 and not out.strip(), f"implemented fixture is dirty: {out}"
    rc, out = run_in(container, ["touch", "/tmp/omc-external-build-unavailable"])
    assert rc == 0, out
    session.send(_direct_audit("claude"))
    turn = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0 and "build" in markers.splitlines(), (
        f"failing build never executed; audit answer: {turn['text']}"
    )
    rc, stage = run_in(container, ["cat", f"{m['worktree']}/.omc/stage-scripts/build.sh"])
    assert rc == 0 and stage == original_stage, "audit changed external build gate"
    rc, _ = run_in(container, ["sh", ".omc/stage-scripts/build.sh"], cwd=m["worktree"])
    assert rc != 0, "build stage no longer fails under external condition"
    after = session.snapshot(m["worktree"])
    assert after["remote_refs"] == before["remote_refs"], "failing stage published"
    rc, _ = run_in(
        container, ["git", "-C", f"{m['repo']}-origin", "rev-parse", "--verify", m["branch"]]
    )
    assert rc != 0, "failing stage published feature branch"


# Two actor turns can each use 1800 s; leave 100 s for container and artifact checks.
@pytest.mark.timeout(3700)
@pytest.mark.variation("implemented")
def test_unrelated_untracked_draft_blocks_publication(stage_session):
    container, session, m = stage_session
    session.bind(m["implement_session"], m["worktree"], m["repo"])
    before = session.snapshot(m["worktree"])
    draft = "personal-draft.md"
    draft_bytes = "Private draft: unrelated meeting notes. Keep these bytes.\n"
    rc, out = run_in(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path; Path('personal-draft.md').write_bytes("
            "b'Private draft: unrelated meeting notes. Keep these bytes.\\n')",
        ],
        cwd=m["worktree"],
    )
    assert rc == 0, out
    session.send(_direct_audit("claude"))
    turn = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    after = session.snapshot(m["worktree"])
    rc, actual = run_in(container, ["cat", draft], cwd=m["worktree"])
    assert rc == 0 and actual == draft_bytes, "audit changed or removed personal draft"
    rc, status = run_in(container, ["git", "status", "--porcelain"], cwd=m["worktree"])
    assert rc == 0 and f"?? {draft}" in status.splitlines(), status
    assert after["remote_refs"] == before["remote_refs"], "audit published unrelated draft"
    rc, _ = run_in(
        container, ["git", "-C", f"{m['repo']}-origin", "rev-parse", "--verify", m["branch"]]
    )
    assert rc != 0, "audit published feature branch with unrelated draft"
    answer = turn["text"].lower()
    assert "critical" in answer and draft in answer and "?" in answer, (
        f"audit did not ask about unrelated draft: {turn['text']}"
    )

    session.send("Leave it untouched and outside the published change.")
    resumed = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    rc, actual = run_in(container, ["cat", draft], cwd=m["worktree"])
    assert rc == 0 and actual == draft_bytes, "audit changed draft after keep-out answer"
    rc, status = run_in(container, ["git", "status", "--porcelain"], cwd=m["worktree"])
    assert rc == 0 and f"?? {draft}" in status.splitlines(), status
    after_answer = session.snapshot(m["worktree"])
    assert after_answer["remote_refs"] == before["remote_refs"], "keep-out answer published"
    rc, _ = run_in(
        container, ["git", "-C", f"{m['repo']}-origin", "rev-parse", "--verify", m["branch"]]
    )
    assert rc != 0, "keep-out answer published feature branch"
    final_answer = resumed["text"].lower()
    assert (
        draft in final_answer
        and "finish" in final_answer
        and any(
            phrase in final_answer
            for phrase in ("cannot", "can't", "unable", "remain paused", "still paused", "blocked")
        )
    ), f"audit did not explain why it remains stopped: {resumed['text']}"


@pytest.mark.timeout(1900)
@pytest.mark.variation("implemented")
def test_audit_repairs_committed_greeting_drift(stage_session):
    container, session, m = stage_session
    session.bind(m["implement_session"], m["worktree"], m["repo"])
    rc, out = run_in(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path; "
            "Path('greeting.py').write_text(\"def greeting():\\n    return 'Goodbye, world!'\\n\")",
        ],
        cwd=m["worktree"],
    )
    assert rc == 0, out
    rc, out = run_in(container, ["git", "add", "greeting.py"], cwd=m["worktree"])
    assert rc == 0, out
    rc, out = run_in(
        container, ["git", "commit", "-m", "introduce greeting drift"], cwd=m["worktree"]
    )
    assert rc == 0, out
    drifted = session.snapshot(m["worktree"])
    evidence = {"turns": []}
    session.send(_direct_audit("claude"))
    turn = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    _record_phase(session, m["repo"], m["worktree"], evidence, "audit", turn)
    _assert_audited_artifacts(container, m["repo"], m["worktree"], m["branch"], evidence, drifted)
