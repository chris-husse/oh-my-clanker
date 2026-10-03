"""Variations forked from the `recorded` snapshot: the design record is committed,
the design session is open and waiting. Each test types the in-session implement
command."""

import pytest

from ..harness import run_in
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_successful_implementation,
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
def test_implement_publishes(stage_session):
    container, session, m = stage_session
    baseline, _ = _baseline(session, m)
    evidence = {"turns": []}
    session.send(_direct_implement("claude"))
    turn = session.wait_turn(IMPLEMENT_TURN_BUDGET)
    _record_phase(session, m["repo"], m["worktree"], evidence, "implement", turn)
    _assert_successful_implementation(
        container, "claude", session, m["repo"], m["worktree"], m["branch"], evidence, baseline
    )


# Triggers /omc:implement, which exceeds the 300 s ceiling today (see the
# golden path's implemented stage): expensive tier until omc is faster.
@pytest.mark.expensive
@pytest.mark.timeout(1800)
@pytest.mark.variation("recorded")
def test_failing_build_blocks_publication(stage_session):
    container, session, m = stage_session
    # Inject the failure into the clone only: the external build gate goes away.
    sabotage = (
        f"printf '\\ntest ! -e /tmp/omc-external-build-unavailable\\n' "
        f">> {m['worktree']}/.omc/stage-scripts/build.sh && "
        f"cp {m['worktree']}/.omc/stage-scripts/build.sh {m['repo']}/.omc/stage-scripts/build.sh "
        "&& touch /tmp/omc-external-build-unavailable"
    )
    rc, out = run_in(container, ["bash", "-c", sabotage])
    assert rc == 0, out
    baseline, _ = _baseline(session, m)
    session.send(_direct_implement("claude"))
    session.wait_turn(IMPLEMENT_TURN_BUDGET)
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0 and "build" in markers.splitlines(), "failing build never executed"
    after = session.snapshot(m["worktree"])
    assert after["remote_refs"] == baseline["remote_refs"], "failing stage published"
    rc, _ = run_in(
        container, ["git", "-C", f"{m['repo']}-origin", "rev-parse", "--verify", m["branch"]]
    )
    assert rc != 0, "failing stage published feature branch"
