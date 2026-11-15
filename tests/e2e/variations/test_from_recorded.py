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
def test_implement_never_runs_verify(stage_session):
    container, session, m = stage_session
    recorded, _ = _baseline(session, m)
    # The sentinel turns any verify run red, so a run that started verify
    # cannot reach a clean, check-only handoff by luck.
    rc, out = run_in(container, ["touch", "/tmp/omc-external-verify-unavailable"])
    assert rc == 0, out

    evidence = {"turns": []}
    session.send(_direct_implement("claude"))
    # Measured at 145.71 s on 2026-10-06 with the since-removed build → verify
    # milestone gate; cancel the actor before pytest's default 300 s ceiling
    # so artifact checks still have time to finish.
    turn = session.wait_turn(240)
    _record_phase(session, m["repo"], m["worktree"], evidence, "implement", turn)
    after = evidence["turns"][-1]["snapshot"]
    # New plan, product changed, record untouched, nothing pushed, clean tree,
    # and check-only stage markers: no verify marker.
    _assert_implemented_artifacts(
        container, m["repo"], m["worktree"], m["branch"], evidence, recorded
    )
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0, f"no stage markers after implementation: {markers!r}"
    # write-plan commits the plan before the build and the record commit is
    # already on the branch, so only commits touching paths outside
    # docs/superpowers/plans/ after the record prove a task commit landed.
    rc, commits = run_in(
        container,
        [
            "git",
            "rev-list",
            "--count",
            f"{recorded['head']}..HEAD",
            "--",
            ".",
            ":(exclude)docs/superpowers/plans",
        ],
        cwd=m["worktree"],
    )
    assert rc == 0 and int(commits.strip()) >= 1, (
        f"no new task commits (outside the plan) after record: {commits!r} (git rc={rc})\n"
        f"recorded head: {recorded['head']}\n"
        f"current head: {after['head']}\n"
        f"stage markers: {markers!r}\n"
        f"implementation answer: {turn['text']}"
    )


# Expected cost is the planning half of implement's measured 145 s run; it
# stays in the default tier with the neighbour's 240 s wait until a measured
# run demands `expensive`.
@pytest.mark.variation("recorded")
def test_write_plan_commits_plan_only(stage_session):
    container, session, m = stage_session
    recorded, _ = _baseline(session, m)
    rc, stages_before = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    session.send("/omc:write-plan")
    turn = session.wait_turn(240)  # planning half of implement's measured 145 s run
    after = session.snapshot(m["worktree"])
    answer = f"write-plan answer: {turn['text']}"

    plans = [
        path
        for path in after["spec_plan"]
        if "/plans/" in path and path not in recorded["spec_plan"]
    ]
    assert len(plans) == 1, f"expected exactly one new plan, got {plans}\n{answer}"
    rc_plan, _ = run_in(container, ["git", "cat-file", "-e", f"HEAD:{plans[0]}"], cwd=m["worktree"])
    assert rc_plan == 0, f"write-plan did not commit the plan: {plans[0]}\n{answer}"
    rc_diff, changed = run_in(
        container,
        ["git", "diff", "--name-only", f"{recorded['head']}..HEAD"],
        cwd=m["worktree"],
    )
    paths = [line for line in changed.splitlines() if line.strip()]
    assert rc_diff == 0 and paths, f"write-plan committed nothing: {changed!r}\n{answer}"
    assert all(path.startswith("docs/superpowers/plans/") for path in paths), (
        f"write-plan committed more than the plan: {paths}\n{answer}"
    )
    rc_status, status = run_in(container, ["git", "status", "--porcelain"], cwd=m["worktree"])
    assert rc_status == 0 and not status.strip(), (
        f"write-plan left the worktree dirty: {status!r}\n{answer}"
    )
    assert after["remote_refs"] == recorded["remote_refs"], f"write-plan published\n{answer}"

    def specs(snapshot):
        return {k: v for k, v in snapshot["spec_plan"].items() if "/specs/" in k}

    assert specs(after) == specs(recorded), f"write-plan changed the design record\n{answer}"
    rc_after, stages_after = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert (rc_after, stages_after) == (rc, stages_before), (
        f"write-plan ran a project stage: {stages_before!r} -> {stages_after!r}\n{answer}"
    )
