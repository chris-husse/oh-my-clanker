"""Finish stage order within the full implementation session's marker history."""

import pytest

from tests.e2e import lifecycle_helpers
from tests.e2e.lifecycle_helpers import (
    _assert_finish_stage_order,
    _assert_recorded,
    _assert_reviewed_record,
)


@pytest.mark.parametrize(
    "markers",
    [
        "check\nbuild\nverify\n",
        "check\ncheck\nreview\ncheck\nbuild\nverify\n",
        "check\nbuild\nverify\ncheck\nbuild\nverify\nreview\n",
    ],
    ids=["milestone", "preceding-checks-and-review", "later-finish-block"],
)
def test_implementation_stages_require_ordered_milestone(markers):
    lifecycle_helpers._assert_implement_stage_order(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "",
        "build\nverify\n",
        "verify\ncheck\nbuild\n",
        "check\nverify\nbuild\n",
        "check\nbuild\nreview\nverify\n",
        "check\nbuild\ncheck\nverify\n",
        "check\nbuild\n",
    ],
    ids=[
        "empty",
        "no-check",
        "check-after-build",
        "reversed",
        "interleaved-review",
        "interleaved-check",
        "missing-verify",
    ],
)
def test_implementation_stages_reject_missing_or_separated_milestone(markers):
    with pytest.raises(AssertionError):
        lifecycle_helpers._assert_implement_stage_order(markers)


def test_audit_verify_count_only_applies_to_explicit_drift_repair():
    lifecycle_helpers._assert_audit_verify_count("check\nbuild\nverify\n", drift_repair=False)
    with pytest.raises(AssertionError):
        lifecycle_helpers._assert_audit_verify_count("verify\nverify\n", drift_repair=True)
    lifecycle_helpers._assert_audit_verify_count("verify\nverify\nverify\n", drift_repair=True)


@pytest.mark.parametrize(
    "markers",
    [
        "check\nbuild\nverify\nreview\n",
        "check\ncheck\nreview\ncheck\nbuild\nverify\nreview\n",
        "check\nbuild\nverify\nreview\ncheck\n",
        "check\nbuild\ncheck\nreview\nverify\ncheck\ncheck\ncheck\nbuild\nbuild\nverify\nverify\nreview\nreview\n",
    ],
    ids=[
        "finish",
        "implementation-review-before-finish",
        "supplemental-check-after-finish",
        "observed-repeated-finish-stages",
    ],
)
def test_finish_stages_allow_validation_outside_finish(markers):
    _assert_finish_stage_order(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "",
        "build\nverify\nreview\n",
        "check\nreview\ncheck\nbuild\nverify\n",
        "check\nreview\ncheck\nverify\nbuild\nreview\n",
        "check\nbuild\nreview\nverify\n",
        "check\nbuild\nreview\nverify\nreview\n",
        "check\nbuild\ncheck\nverify\nreview\n",
        "check\ncheck\nbuild\nbuild\nreview\nreview\nverify\nverify\n",
        "check\ncheck\nbuild\nbuild\ncheck\ncheck\nverify\nverify\nreview\nreview\n",
    ],
    ids=[
        "no-stages",
        "missing-check",
        "early-review-cannot-replace-finish-review",
        "build-after-verify",
        "review-before-verify",
        "interleaved-review",
        "interleaved-check",
        "duplicated-out-of-order",
        "duplicated-interleaved-check",
    ],
)
def test_finish_stages_require_a_complete_ordered_block(markers):
    with pytest.raises(AssertionError):
        _assert_finish_stage_order(markers)


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
