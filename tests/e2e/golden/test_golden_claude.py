"""The Claude golden path: the real lifecycle once, one or two turns per
stage, snapshotted after each stage so variations can fork from it.

Sequential by design: scripts/e2e.sh runs this directory with `-n 0` before
everything else. Each stage requires the previous one to have passed; a
failed stage fails every later one loudly instead of skipping.
"""

from __future__ import annotations

import time

import pytest

from ..conversation import ClaudeConversation
from ..harness import run_in
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_audited_artifacts,
    _assert_discussion_boundary,
    _assert_implemented_artifacts,
    _assert_primary_boundary,
    _assert_recorded,
    _configure_claude_conversation,
    _direct_design,
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
        self.recorded = None
        self.implemented = None
        self.implement_session = None

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
            "implement_session": self.implement_session,
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


def test_stage_recorded(flow):
    _require(flow, "agreed")
    started = time.monotonic()
    flow.session.send(_direct_design("claude"))
    # 240, not 300: the driver's own cancellation path must fire before
    # pytest's alarm (timeout_func_only covers the body; the judge needs time too).
    recorded = flow.session.wait_turn(240)
    flow.evidence["design_turn_seconds"] = round(time.monotonic() - started, 1)
    print(f"\n/omc:design turn: {flow.evidence['design_turn_seconds']} s")  # tier placement
    feature, primary = _record_phase(
        flow.session, flow.repo, flow.worktree, flow.evidence, "design", recorded
    )
    _assert_recorded(flow.baseline, feature, "design record")
    _assert_primary_boundary(flow.primary_start, primary, "design record")
    rc, dirty = run_in(flow.container, ["git", "-C", flow.worktree, "status", "--porcelain"])
    assert rc == 0 and not dirty.strip(), f"design left the worktree dirty: {dirty}"
    verdict = _judge_claude(
        flow.container,
        flow.judge_model,
        "OMC design committed the design record",
        [
            "The answer states that the design record was committed.",
            "The answer names both ways to continue: the implement command in this "
            "session, or `omc implement` with a provider flag from the shell.",
            "The answer does not ask for approval or permission to continue.",
        ],
        recorded["text"],
    )
    flow.evidence["recorded_judge"] = verdict
    assert verdict["passed"], verdict
    flow.recorded = feature
    flow.done("recorded")


# Implement took 57.5–141.4 s with the milestone gate (two passing runs on
# 2026-10-06), below the 240 s actor budget. The CLI handoff on Claude
# exercises the shared launch path; the in-session path is covered by
# variations/test_from_recorded.py.
def test_stage_implemented(flow):
    _require(flow, "recorded")
    flow.implement_session = f"{flow.session.slug}-implement"
    started = time.monotonic()
    rc, out = run_in(
        flow.container,
        ["omc", "implement", "--claude", "--headless"],
        cwd=flow.worktree,
        timeout=240,  # driver's timeout fires before pytest's default 300 s alarm
    )
    assert rc == 0, out
    flow.evidence["implement_turn_seconds"] = round(time.monotonic() - started, 1)
    print(f"\nomc implement turn: {flow.evidence['implement_turn_seconds']} s")
    feature, _ = _record_phase(
        flow.session, flow.repo, flow.worktree, flow.evidence, "implement", {"text": out}
    )
    _assert_implemented_artifacts(
        flow.container, flow.repo, flow.worktree, flow.branch, flow.evidence, flow.recorded
    )
    flow.implemented = feature
    flow.done("implemented")


@pytest.mark.expensive
@pytest.mark.timeout(1900)
def test_stage_audited(flow):
    _require(flow, "implemented")
    rc, out = run_in(
        flow.container,
        [
            "python3",
            "-c",
            "from pathlib import Path; "
            "Path('greeting.py').write_text(\"def greeting():\\n    return 'Goodbye, world!'\\n\")",
        ],
        cwd=flow.worktree,
    )
    assert rc == 0, out
    rc, out = run_in(flow.container, ["git", "add", "greeting.py"], cwd=flow.worktree)
    assert rc == 0, out
    rc, out = run_in(
        flow.container, ["git", "commit", "-m", "introduce greeting drift"], cwd=flow.worktree
    )
    assert rc == 0, out
    drifted = flow.session.snapshot(flow.worktree)
    rc, out = run_in(
        flow.container,
        ["omc", "review", "--claude", "--headless"],
        cwd=flow.worktree,
        timeout=IMPLEMENT_TURN_BUDGET,
    )
    assert rc == 0, out
    _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "audit", {"text": out})
    _assert_audited_artifacts(
        flow.container,
        flow.repo,
        flow.worktree,
        flow.branch,
        flow.evidence,
        drifted,
        drift_repair=True,
    )
    flow.done("audited")
