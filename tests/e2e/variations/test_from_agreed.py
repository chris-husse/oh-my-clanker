"""Variations forked from the `agreed` snapshot: the design is agreed, the
session waits for the direct implementation command. Each test is one turn
from there, with its own twist injected into the clone."""

import pytest

from ..harness import run_in
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_critical_wait,
    _assert_successful_implementation,
    _direct_implement,
    _judge_claude,
    _record_phase,
)

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


def _baseline(session, manifest):
    return session.snapshot(manifest["worktree"]), session.snapshot(manifest["repo"])


# Triggers /omc:implement, which exceeds the 300 s ceiling today (see the
# golden path's implemented stage): expensive tier until omc is faster.
@pytest.mark.expensive
@pytest.mark.timeout(1800)
@pytest.mark.variation("agreed")
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


@pytest.mark.variation("agreed")
def test_critical_question_waits(stage_session):
    container, session, m = stage_session
    baseline, primary = _baseline(session, m)
    session.send(
        _direct_implement(
            "claude",
            "A new requirement says the same zero-argument greeting() call must return "
            "exactly both 'Hello, world!' and 'Hello there!'.",
        )
    )
    question = session.wait_turn(300)
    _assert_critical_wait(baseline, session.snapshot(m["worktree"]), "critical question")
    _assert_critical_wait(primary, session.snapshot(m["repo"]), "primary critical question")
    verdict = _judge_claude(
        container,
        m["judge_model"],
        "Conflicting exact return values for the same no-argument function call",
        [
            "The assistant identifies the contradiction as a critical unanswered requirement.",
            "The assistant asks which exact value to implement before proceeding.",
            "The assistant does not request routine approval of an otherwise complete "
            "spec or plan.",
        ],
        question["text"],
    )
    assert verdict["passed"], verdict


# Triggers /omc:implement, which exceeds the 300 s ceiling today (see the
# golden path's implemented stage): expensive tier until omc is faster.
@pytest.mark.expensive
@pytest.mark.timeout(1800)
@pytest.mark.variation("agreed")
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
