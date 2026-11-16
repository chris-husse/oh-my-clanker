"""Variations forked from the `agreed` snapshot: the design is agreed, the
session waits for the design command (`/omc:design`). Each test is one turn
from there, with its own twist injected into the clone."""

import ast

import pytest

from ..harness import add_hook, run_in
from ..lifecycle_helpers import (
    _assert_audit_stage_order,
    _assert_boundary,
    _assert_check_only_before_audit,
    _assert_critical_wait,
    _assert_primary_boundary,
    _assert_published_fix,
    _assert_recorded,
    _direct_design,
    _direct_implement,
    _judge_claude,
)

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


def _baseline(session, manifest):
    return session.snapshot(manifest["worktree"]), session.snapshot(manifest["repo"])


@pytest.mark.variation("agreed")
def test_critical_question_waits_at_design_time(stage_session):
    """A late conflicting requirement surfaces as a CRITICAL question BEFORE
    /omc:design writes the record (the skill's User Input rule) — not as a
    silent choice, and not after a full hardening pass (which would not fit
    the default tier's ceiling)."""
    container, session, m = stage_session
    baseline, primary = _baseline(session, m)
    session.send(
        _direct_design(
            "claude",
            "A new requirement says the same zero-argument greeting() call must return "
            "exactly both 'Hello, world!' and 'Hello there!'.",
        )
    )
    question = session.wait_turn(240)
    _assert_critical_wait(baseline, session.snapshot(m["worktree"]), "critical question")
    _assert_critical_wait(primary, session.snapshot(m["repo"]), "primary critical question")
    rc, out = run_in(container, ["omc", "internal", "design-record"], cwd=m["worktree"])
    assert rc == 2 and '"reason": "missing"' in out, out  # nothing written before the answer
    verdict = _judge_claude(
        container,
        m["judge_model"],
        "Conflicting exact return values for the same no-argument function call",
        [
            "The assistant identifies the contradiction as a critical unanswered requirement.",
            "The assistant asks which exact value the design should specify before proceeding.",
            "The assistant does not request routine approval of an otherwise complete "
            "spec or plan.",
        ],
        question["text"],
    )
    assert verdict["passed"], verdict


@pytest.mark.variation("agreed")
def test_implement_without_a_record_refuses(stage_session):
    """One fast model turn: /omc:implement before /omc:design must refuse and
    point at /omc:design, changing nothing."""
    container, session, m = stage_session
    baseline, primary = _baseline(session, m)
    session.send(_direct_implement("claude"))
    turn = session.wait_turn(240)
    _assert_boundary(baseline, session.snapshot(m["worktree"]), "refusal")
    _assert_primary_boundary(primary, session.snapshot(m["repo"]), "refusal")
    assert "/omc:design" in turn["text"], turn["text"]
    rc, out = run_in(container, ["omc", "internal", "design-record"], cwd=m["worktree"])
    assert rc == 2 and '"reason": "missing"' in out, out


@pytest.mark.variation("agreed")
def test_cli_handoff_dry_run_from_a_fixture_record(stage_session):
    """Model-free: the CLI gate refuses without a record, accepts a committed
    fixture record, and its dry run names the record and the implement seed.
    Also the guard that `omc implement` never calls the slug model."""
    container, _session, m = stage_session
    worktree, slug = m["worktree"], m["slug"]

    rc, out = run_in(container, ["omc", "implement", "--claude", "--dry-run"], cwd=worktree)
    assert rc == 2 and "no design record" in out and "/omc:design" in out, out

    rc, out = run_in(
        container, ["omc", "implement", "--claude", "--codex", "--dry-run"], cwd=worktree
    )
    assert rc == 2 and "not allowed with" in out, out

    record = f"docs/superpowers/specs/2026-01-01-{slug}-design.md"
    # The image sets a global git identity (Dockerfile.e2e), as _fixture relies on.
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            f"mkdir -p docs/superpowers/specs && printf '# fixture record\\n' > {record} && "
            f"git add {record} && git commit -qm fixture",
        ],
        cwd=worktree,
    )
    assert rc == 0, out
    rc, head_before = run_in(container, ["git", "rev-parse", "HEAD"], cwd=worktree)
    assert rc == 0, head_before

    rc, out = run_in(container, ["omc", "implement", "--claude", "--dry-run"], cwd=worktree)
    assert rc == 0, out
    assert f"record:       {record}" in out
    assert f"session:      {slug}-implement" in out
    assert "'/omc:implement'" in out and f"'-n', '{slug}-implement'" in out
    assert "generating slug" not in out  # the slug comes from the branch, never a model

    rc, head_after = run_in(container, ["git", "rev-parse", "HEAD"], cwd=worktree)
    assert rc == 0, head_after
    rc, dirty = run_in(container, ["git", "status", "--porcelain"], cwd=worktree)
    assert head_after == head_before and not dirty.strip(), "dry run changed the worktree"


@pytest.mark.variation("agreed")
def test_review_dry_run_from_a_fixture_record(stage_session):
    """The review CLI refuses before design and plans an audit session from a record."""
    container, _session, m = stage_session
    worktree, slug = m["worktree"], m["slug"]
    rc, out = run_in(container, ["omc", "review", "--claude", "--dry-run"], cwd=worktree)
    assert rc == 2 and "no design record" in out and "/omc:design" in out, out

    record = f"docs/superpowers/specs/2026-01-01-{slug}-design.md"
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            f"mkdir -p docs/superpowers/specs && printf '# fixture record\\n' > {record} && "
            f"git add {record} && git commit -qm fixture",
        ],
        cwd=worktree,
    )
    assert rc == 0, out
    rc, head_before = run_in(container, ["git", "rev-parse", "HEAD"], cwd=worktree)
    assert rc == 0, head_before

    rc, out = run_in(container, ["omc", "review", "--claude", "--dry-run"], cwd=worktree)
    assert rc == 0, out
    assert f"record:       {record}" in out
    assert f"session:      {slug}-audit" in out
    argv_row = next(line for line in out.splitlines() if "session argv:" in line)
    assert ast.literal_eval(argv_row.split("session argv:", 1)[1].strip()) == [
        "claude",
        "-n",
        f"{slug}-audit",
        "--model",
        m["model"],
        "/omc:audit",
    ]
    rc, head_after = run_in(container, ["git", "rev-parse", "HEAD"], cwd=worktree)
    assert rc == 0, head_after
    rc, dirty = run_in(container, ["git", "status", "--porcelain"], cwd=worktree)
    assert head_after == head_before and not dirty.strip(), "dry run changed the worktree"


# Spec 2026-11-12 D1/D11: with provider selection per step off, ONE /omc:design
# turn runs design → implement → audit → finish → push. Budget is the
# monolithic run's design 900 + implement 1800 + audit 1800 s, as one turn.
CHAIN_TURN_BUDGET = 4500


@pytest.mark.expensive
@pytest.mark.timeout(4800)
@pytest.mark.variation("agreed")
def test_design_auto_advances_to_push(stage_session):
    """No judge: the pushed, squashed, audited branch is the proof that both
    provider-offer stops are gone."""
    container, session, m = stage_session
    worktree, branch = m["worktree"], m["branch"]
    baseline, _ = _baseline(session, m)
    rc, out = run_in(
        container, ["omc", "configure", "--set", "llm.provider_selection_per_step=false"]
    )
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "internal", "models"])
    assert rc == 0 and '"provider_selection_per_step": false' in out, out

    session.send(_direct_design("claude"))
    turn = session.wait_turn(CHAIN_TURN_BUDGET)
    final = session.snapshot(worktree)
    answer = f"chained answer (tail): {turn['text'][-2000:]}"

    assert any(ref.startswith(f"refs/heads/{branch} ") for ref in final["remote_refs"]), (
        f"the chain did not push {branch}; {answer}"
    )
    rc, commits = run_in(
        container, ["git", "-C", worktree, "rev-list", "--count", "origin/main..HEAD"]
    )
    assert rc == 0 and commits.strip() == "1", f"finish did not squash: {commits!r}; {answer}"
    records = [
        key
        for key in final["spec_plan"]
        if key.startswith("docs/superpowers/specs/") and key not in baseline["spec_plan"]
    ]
    assert len(records) == 1, f"expected one new design record: {records}; {answer}"
    rc, content = run_in(container, ["git", "-C", worktree, "show", f"HEAD:{records[0]}"])
    assert rc == 0 and "## Implementation review" in content, f"audit trace missing; {answer}"
    plans = [
        key
        for key in final["spec_plan"]
        if key.startswith("docs/superpowers/plans/") and key not in baseline["spec_plan"]
    ]
    assert plans, f"the chain wrote no plan; {answer}"
    for plan in plans:
        rc, _ = run_in(container, ["git", "-C", worktree, "cat-file", "-e", f"HEAD:{plan}"])
        assert rc == 0, f"plan not committed: {plan}; {answer}"
    assert final["source"]["greeting.py"] != baseline["source"]["greeting.py"], answer
    rc, dirty = run_in(container, ["git", "-C", worktree, "status", "--porcelain"])
    assert rc == 0 and not dirty.strip(), f"the chain left a dirty tree: {dirty}; {answer}"
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0, f"project stages never executed; {answer}"
    # implement gates on check only; audit runs build → check → review → verify
    # plus its repair reruns, and finish on the verified tree adds no stage
    _assert_check_only_before_audit(markers)
    _assert_audit_stage_order(markers)
    _assert_published_fix(
        container, m["repo"], worktree, branch, "def greeting():\n    return 'Goodbye, world!'\n"
    )


_POST_DESIGN = "post_design"
_POST_DESIGN_PATH = f".omc/hooks/{_POST_DESIGN}/SKILL.md"
_POST_DESIGN_BODY = (
    "Read `record=<path>` from the caller's context. From the project root run\n"
    "`grep -qx '## Rollback' <path>`.\n\n"
    "- Exit 0 (the record has a `## Rollback` section) → answer\n"
    '  `OMC_HOOK {"hook": "post_design", "go": true, '
    '"reason": "the record has a Rollback section"}`.\n'
    "- Any other exit → answer\n"
    '  `OMC_HOOK {"hook": "post_design", "go": false, '
    '"reason": "the record has no ## Rollback section: add one that says how to undo '
    'this change"}`.\n\n'
    "End with exactly that one OMC_HOOK line, plain text, no backticks or code fences."
)


# Spec 2026-11-16 D12, §6 "post_design no-go, then go": two hardening passes
# and two record commits cannot fit the 300 s default ceiling (plan decision
# 4). It runs in the expensive tier with the chain test above: `just
# lifecycle-full`, or `just e2e-rest -m "e2e and expensive" …/test_from_agreed.py`.
@pytest.mark.expensive
@pytest.mark.timeout(3700)
@pytest.mark.variation("agreed")
def test_post_design_no_go_then_go(stage_session):
    """The project's post_design hook says no-go until the record has a
    Rollback section: design hardens again and commits again, then stops on go."""
    container, session, m = stage_session
    worktree = m["worktree"]
    add_hook(container, worktree, _POST_DESIGN, _POST_DESIGN_BODY)
    rc, out = run_in(
        container,
        ["bash", "-c", f"git add {_POST_DESIGN_PATH} && git commit -qm 'add post_design hook'"],
        cwd=worktree,
    )
    assert rc == 0, out
    rc, original_hook = run_in(
        container, ["git", "show", f"HEAD:{_POST_DESIGN_PATH}"], cwd=worktree
    )
    assert rc == 0, original_hook
    baseline = session.snapshot(worktree)

    session.send(_direct_design("claude"))
    turn = session.wait_turn(3500)
    answer = f"design answer (tail): {turn['text'][-2000:]}"
    record = _assert_recorded(baseline, session.snapshot(worktree), "post_design loop")
    rc, content = run_in(container, ["git", "show", f"HEAD:{record}"], cwd=worktree)
    assert rc == 0 and "## Rollback" in content.splitlines(), (
        f"the committed record has no ## Rollback section; {answer}"
    )
    rc, commits = run_in(
        container, ["git", "log", "--format=%H", "--", record], cwd=worktree, stdout_only=True
    )
    assert rc == 0 and len(commits.split()) >= 2, (
        f"the no-go did not cost a second record commit: {commits!r}; {answer}"
    )
    rc, hook = run_in(container, ["git", "show", f"HEAD:{_POST_DESIGN_PATH}"], cwd=worktree)
    assert rc == 0 and hook == original_hook, f"design changed the post_design hook; {answer}"
    rc, dirty = run_in(container, ["git", "status", "--porcelain"], cwd=worktree)
    assert rc == 0 and not dirty.strip(), f"design left the worktree dirty: {dirty}; {answer}"
