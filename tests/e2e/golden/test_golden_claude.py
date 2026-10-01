"""The Claude golden path: the real lifecycle once, one or two turns per
stage, snapshotted after each stage so variations can fork from it.

Sequential by design: scripts/e2e.sh runs this directory with `-n 0` before
everything else. Each stage requires the previous one to have passed; a
failed stage fails every later one loudly instead of skipping.
"""

from __future__ import annotations

import pytest

from ..conversation import ClaudeConversation
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_discussion_boundary,
    _assert_successful_implementation,
    _configure_claude_conversation,
    _direct_implement,
    _fixture,
    _judge_claude,
    _launch_start,
    _record_phase,
    _require_complete_design,
    _worktree,
)
from ..stages import snapshot

pytestmark = [pytest.mark.e2e, pytest.mark.golden, pytest.mark.e2e_provider("claude")]

CONTEXT = (
    "Please discuss correcting greeting.py: greeting() currently returns "
    "'Goodbye, world!' but must return exactly 'Hello, world!' with its "
    "zero-argument signature. Add an exact-value unittest."
)


class Flow:
    def __init__(self, container):
        self.container = container
        self.model, self.judge_model, self.metadata = _configure_claude_conversation(container)
        self.repo = _fixture(container)
        self.session = ClaudeConversation(container, self.model).__enter__()
        self.evidence = {"provider": "claude", "model_requested": self.model, "turns": []}
        self.reached = None
        self.worktree = self.branch = self.baseline = self.primary_start = None

    def manifest(self, stage: str) -> dict:
        return {
            "provider": "claude",
            "stage": stage,
            "slug": self.session.slug,
            "repo": self.repo,
            "worktree": self.worktree,
            "branch": self.branch,
            "model": self.model,
            "judge_model": self.judge_model,
        }

    def done(self, stage: str) -> str:
        # Snapshot first: a stage counts as reached only once its image exists,
        # so a failed commit never lets the next stage run on unsaved state.
        tag = snapshot(self.container, self.manifest(stage))
        self.reached = stage
        return tag


@pytest.fixture(scope="module")
def flow(module_container):
    f = Flow(module_container)
    yield f
    f.session.close()


def _require(flow: Flow, stage: str) -> None:
    assert flow.reached == stage, f"prior stage {stage!r} did not pass (reached {flow.reached!r})"


def test_stage_start(flow):
    initial = flow.session.snapshot(flow.repo)
    _launch_start(flow.session, flow.repo, "claude", CONTEXT)
    first = flow.session.wait_turn(300)
    flow.worktree, flow.branch = _worktree(flow.container, flow.repo)
    flow.baseline, flow.primary_start = _record_phase(
        flow.session, flow.repo, flow.worktree, flow.evidence, "start", first
    )
    for product_file in ("greeting.py", "test_greeting.py"):
        assert flow.baseline["source"][product_file] == initial["source"][product_file], (
            f"start changed feature {product_file}"
        )
        assert flow.primary_start["source"][product_file] == initial["source"][product_file], (
            f"slug/start changed primary {product_file}"
        )
    primer = _judge_claude(
        flow.container,
        flow.judge_model,
        "OMC start received a greeting correction context",
        [
            "The answer presents project context or a primer about the greeting change.",
            "The answer asks the user for their seed or intended direction.",
            "The answer has not claimed implementation or publication.",
        ],
        first["text"],
    )
    flow.evidence["primer_judge"] = primer
    assert primer["passed"], primer
    flow.done("start")


def test_stage_design(flow):
    _require(flow, "start")
    flow.session.send(
        "My seed: change greeting() to return exactly 'Hello, world!'. "
        "Keep its zero-argument signature and add a unittest for the exact value. "
        "Please present the complete solution."
    )
    design = flow.session.wait_turn(300)
    feature, primary = _record_phase(
        flow.session, flow.repo, flow.worktree, flow.evidence, "seed", design
    )
    _assert_discussion_boundary(
        flow.baseline, feature, flow.primary_start, primary, "seed discussion"
    )
    _require_complete_design(
        flow.container,
        _judge_claude,
        flow.judge_model,
        flow.session,
        flow.repo,
        flow.worktree,
        flow.baseline,
        flow.evidence,
        design,
    )
    flow.done("design")


def test_stage_agreed(flow):
    _require(flow, "design")
    flow.session.send(
        "One detail: preserve the exact capitalization and punctuation in "
        "'Hello, world!'. Please incorporate that into the design."
    )
    detail = flow.session.wait_turn(300)
    feature, primary = _record_phase(
        flow.session, flow.repo, flow.worktree, flow.evidence, "detail", detail
    )
    _assert_discussion_boundary(
        flow.baseline, feature, flow.primary_start, primary, "design detail"
    )
    flow.session.send("ok")
    agreed = flow.session.wait_turn(300)
    feature, primary = _record_phase(
        flow.session, flow.repo, flow.worktree, flow.evidence, "ok", agreed
    )
    _assert_discussion_boundary(flow.baseline, feature, flow.primary_start, primary, "agreement")
    flow.done("agreed")


# omc's own /omc:implement (spec hardening with explain passes, plan, subagent
# build, finish) exceeded the 300 s ceiling on a one-line fixture (measured
# 2026-10-01: start 53 s, design 30 s, agreement 13 s, implement >300 s on the
# standard tier). Until omc's implement flow is faster, this stage is evidence
# only: expensive tier, never a gate. The default golden path ends at `agreed`.
@pytest.mark.expensive
@pytest.mark.timeout(1800)
def test_stage_implemented(flow):
    _require(flow, "agreed")
    flow.session.send(_direct_implement("claude"))
    implemented = flow.session.wait_turn(IMPLEMENT_TURN_BUDGET)
    _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "implement", implemented)
    _assert_successful_implementation(
        flow.container,
        "claude",
        flow.session,
        flow.repo,
        flow.worktree,
        flow.branch,
        flow.evidence,
        flow.baseline,
    )
    flow.done("implemented")
