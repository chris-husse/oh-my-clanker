"""Stage placement within the full session's cumulative marker history:
implement runs check only; audit, modelled from the last review, runs
build → check → review → verify, and its repair loops (fix → build → check →
rerun) may repeat stages; a review whose amend changed the tree is followed by
build → check before the verify; a verify fix never returns to review. Finish on the
audited, unchanged tree adds no stage; trailing checks are tolerated."""

import pytest

from tests.e2e.lifecycle_helpers import (
    _assert_audit_stage_order,
    _assert_check_only_before_audit,
    _assert_implement_check_only,
    _assert_recorded,
    _assert_reviewed_record,
)


@pytest.mark.parametrize(
    "markers",
    [
        "check\n",
        "check\ncheck\nreview\ncheck\n",
    ],
    ids=[
        "implement-only",
        "implement-checks-and-review",
    ],
)
def test_implement_markers_accept_checks_only(markers):
    _assert_implement_check_only(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "",
        "review\n",
        "check\nbuild\n",
        "check\nverify\n",
        "check\nbuild\ncheck\nreview\nverify\n",
    ],
    ids=[
        "empty",
        "no-check",
        "build",
        "verify",
        "audit-block",
    ],
)
def test_implement_markers_reject_missing_check_build_or_verify(markers):
    with pytest.raises(AssertionError):
        _assert_implement_check_only(markers)


_AUDIT = "check\nbuild\ncheck\nreview\nverify\n"


@pytest.mark.parametrize(
    "markers",
    [
        "check\ncheck\nbuild\ncheck\nreview\nverify\n",
        "check\nreview\ncheck\nbuild\ncheck\nreview\nverify\n",
        "check\nbuild\ncheck\nbuild\ncheck\nreview\nverify\n",
        "check\nbuild\ncheck\nreview\nverify\nbuild\ncheck\nverify\n",
    ],
    ids=[
        "checks-then-audit",
        "implementation-review-then-audit",
        "red-check-restart",
        "red-verify-repair",
    ],
)
def test_no_verify_before_the_first_build_accepts_audit_repairs(markers):
    _assert_check_only_before_audit(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "check\nverify\ncheck\nbuild\ncheck\nreview\nverify\n",
        "verify\ncheck\nbuild\ncheck\nreview\nverify\n",
    ],
    ids=[
        "implement-verify-before-audit",
        "verify-first",
    ],
)
def test_no_verify_before_the_first_build_rejects_early_verify(markers):
    with pytest.raises(AssertionError):
        _assert_check_only_before_audit(markers)


@pytest.mark.parametrize(
    "markers",
    [
        _AUDIT,
        "check\ncheck\nreview\ncheck\nbuild\ncheck\nreview\nverify\n",
        "check\nbuild\ncheck\nbuild\ncheck\nreview\nverify\n",
        "check\nbuild\ncheck\nreview\nbuild\ncheck\nreview\nverify\n",
        _AUDIT + "build\ncheck\nverify\n",
        _AUDIT + "build\ncheck\nverify\nbuild\ncheck\nverify\n",
        _AUDIT + "verify\n",
        _AUDIT + "check\n",
        _AUDIT.replace("review\nverify", "review\ncheck\nverify"),
        _AUDIT.replace("review\nverify", "review\nbuild\ncheck\nverify"),
        _AUDIT.replace("review\nverify", "review\ncheck\nbuild\ncheck\nverify"),
        _AUDIT.replace("review\nverify", "review\nbuild\ncheck\nbuild\ncheck\nverify"),
    ],
    ids=[
        "audit",
        "implementation-review-before-audit",
        "red-check-repaired",
        "red-review-repaired",
        "red-verify-repaired",
        "red-verify-repaired-twice",
        "retried-verify-collapses",
        "trailing-check",
        "lens-fix-check-before-verify",
        "review-changed-tree-build-check-before-verify",
        "lens-fix-check-then-build-check-before-verify",
        "red-post-review-build-repaired",
    ],
)
def test_audit_stages_accept_the_fixed_order_and_repair_loops(markers):
    _assert_audit_stage_order(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "",
        "check\nbuild\nverify\nreview\n",
        "check\nbuild\nreview\nverify\n",
        "check\nreview\nverify\n",
        "check\nbuild\ncheck\nreview\n",
        _AUDIT + "review\n",
        _AUDIT + "check\nverify\n",
        _AUDIT + "build\nverify\n",
        _AUDIT + "build\ncheck\n",
        _AUDIT.replace("review\nverify", "review\nbuild\nverify"),
        _AUDIT.replace("review\nverify", "review\ncheck\nbuild\nverify"),
    ],
    ids=[
        "empty",
        "old-finish-order",
        "no-check-after-build",
        "check-before-build-only",
        "no-verify",
        "review-after-verify",
        "verify-repair-without-build",
        "verify-repair-without-check",
        "trailing-build",
        "post-review-build-without-check",
        "lens-fix-check-then-build-without-check",
    ],
)
def test_audit_stages_reject_out_of_order_or_unrepaired_tails(markers):
    with pytest.raises(AssertionError):
        _assert_audit_stage_order(markers)


BEFORE = {
    "source": {"greeting.py": "a", "test_greeting.py": "b"},
    "index": "i1",
    "head": "h1",
    "remote_refs": ["refs/heads/main x"],
    "spec_plan": {},
}


def test_recorded_requires_a_new_committed_spec_and_untouched_product():
    after = {
        **BEFORE,
        "index": "i2",
        "head": "h2",
        "spec_plan": {"docs/superpowers/specs/2026-10-02-s-design.md": "d"},
    }
    assert _assert_recorded(BEFORE, after, "x") == "docs/superpowers/specs/2026-10-02-s-design.md"


@pytest.mark.parametrize(
    "mutation",
    [
        {"head": "h1"},  # nothing committed
        {"remote_refs": ["refs/heads/main y"]},  # something pushed
        {"source": {"greeting.py": "CHANGED", "test_greeting.py": "b"}},  # product edited
        {"spec_plan": {}},  # no record
        {"spec_plan": {"docs/superpowers/plans/2026-10-02-s-plan.md": "p"}},  # a plan, not a record
    ],
)
def test_recorded_rejects(mutation):
    after = {
        **BEFORE,
        "head": "h2",
        "spec_plan": {"docs/superpowers/specs/2026-10-02-s-design.md": "d"},
        **mutation,
    }
    with pytest.raises(AssertionError):
        _assert_recorded(BEFORE, after, "x")


RECORD = "docs/superpowers/specs/2026-10-02-s-design.md"
OTHER = "docs/superpowers/specs/2026-10-02-other-design.md"


def test_reviewed_record_allows_only_named_spec_change_and_ignores_plans():
    before = {RECORD: "old", OTHER: "same", "docs/superpowers/plans/p.md": "p1"}
    after = {RECORD: "new", OTHER: "same", "docs/superpowers/plans/p.md": "p2"}
    _assert_reviewed_record({"spec_plan": before}, {"spec_plan": after}, RECORD)


@pytest.mark.parametrize(
    "change",
    [
        {RECORD: "old", OTHER: "same"},
        {OTHER: "same"},
        {RECORD: "new", OTHER: "same", "docs/superpowers/specs/new.md": "x"},
        {RECORD: "new"},
        {RECORD: "new", OTHER: "changed"},
    ],
    ids=["unchanged", "missing-record", "added-spec", "deleted-spec", "changed-other"],
)
def test_reviewed_record_rejects_other_spec_diffs(change):
    with pytest.raises(AssertionError):
        _assert_reviewed_record(
            {"spec_plan": {RECORD: "old", OTHER: "same"}}, {"spec_plan": change}, RECORD
        )
