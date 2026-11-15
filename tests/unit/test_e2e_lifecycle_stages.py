"""Stage placement within the full session's cumulative marker history:
implement runs check only; finish, modelled from the last review, runs
check → build → verify → review and may add a final verify, and its bounded
repair loops (fix → check → restart) may repeat stages; trailing checks after
finish are tolerated."""

import pytest

from tests.e2e.lifecycle_helpers import (
    _assert_check_only_before_finish,
    _assert_finish_stage_order,
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
        "check\nbuild\nverify\nreview\n",
    ],
    ids=[
        "empty",
        "no-check",
        "build",
        "verify",
        "finish-block",
    ],
)
def test_implement_markers_reject_missing_check_build_or_verify(markers):
    with pytest.raises(AssertionError):
        _assert_implement_check_only(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "check\ncheck\nbuild\nverify\nreview\n",
        "check\nreview\ncheck\nbuild\nverify\nreview\nverify\n",
        "check\nbuild\ncheck\nbuild\nverify\nreview\nverify\n",
        "check\nbuild\nverify\ncheck\nverify\nbuild\nverify\nreview\nverify\n",
    ],
    ids=[
        "checks-then-finish",
        "review-then-finish-final-verify",
        "red-build-restart",
        "red-verify-repair",
    ],
)
def test_no_verify_before_the_first_build_accepts_finish_repairs(markers):
    _assert_check_only_before_finish(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "check\nverify\ncheck\nbuild\nverify\nreview\n",
        "verify\ncheck\nbuild\nverify\nreview\n",
    ],
    ids=[
        "audit-verify-before-finish",
        "verify-first",
    ],
)
def test_no_verify_before_the_first_build_rejects_early_verify(markers):
    with pytest.raises(AssertionError):
        _assert_check_only_before_finish(markers)


@pytest.mark.parametrize(
    "markers",
    [
        "check\nbuild\nverify\nreview\n",
        "check\ncheck\nreview\ncheck\nbuild\nverify\nreview\n",
        "check\nbuild\ncheck\nreview\nverify\ncheck\ncheck\ncheck\nbuild\nbuild\nverify\nverify\nreview\nreview\n",
        "check\nbuild\nverify\nreview\nverify\n",
        "check\nbuild\nverify\nverify\nreview\nverify\nverify\n",
        "check\nbuild\nverify\ncheck\nverify\nbuild\nverify\nreview\nverify\n",
        "check\nbuild\nverify\nreview\nverify\ncheck\nverify\nbuild\nverify\n",
        "check\nbuild\ncheck\nbuild\nverify\nreview\nverify\n",
        "check\nbuild\nverify\nreview\ncheck\nbuild\nverify\nreview\nverify\n",
        "check\nbuild\nverify\nreview\ncheck\n",
        "check\nbuild\nverify\nreview\nverify\ncheck\n",
        "check\nbuild\nverify\ncheck\nbuild\nverify\nreview\n",
        "check\nbuild\nverify\nreview\nverify\ncheck\nbuild\nverify\n",
        "check\nbuild\nverify\nreview\nverify\ncheck\nbuild\nverify\ncheck\nbuild\nverify\n",
        "check\nbuild\ncheck\nbuild\nverify\nreview\n",
        "check\nbuild\nverify\nreview\ncheck\nbuild\nverify\nreview\n",
    ],
    ids=[
        "finish",
        "implementation-review-before-finish",
        "observed-repeated-finish-stages",
        "final-verify",
        "retried-final-verify",
        "red-pre-review-verify-repaired",
        "red-final-verify-repaired",
        "red-build-repaired",
        "red-review-repaired",
        "supplemental-check-after-finish",
        "final-verify-then-supplemental-check",
        "red-pre-review-verify-no-subset",
        "red-final-verify-no-subset",
        "red-final-verify-no-subset-twice",
        "red-build-repaired-no-final-verify",
        "red-review-repaired-no-final-verify",
    ],
)
def test_finish_stages_accept_final_verify_and_repair_loops(markers):
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
        "check\nbuild\nverify\nreview\nverify\nreview\n",
        "check\nbuild\nverify\nreview\nverify\nbuild\nverify\n",
        "check\nbuild\nverify\nreview\nverify\ncheck\nbuild\n",
        "check\nbuild\nverify\nreview\ncheck\nreview\n",
        "check\nbuild\nverify\ncheck\nverify\nreview\n",
        "check\nbuild\nverify\nreview\nverify\ncheck\nverify\n",
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
        "review-after-final-verify",
        "build-after-final-verify-without-fix-check",
        "trailing-build-after-check",
        "trailing-review-after-check",
        "pre-review-targeted-rerun-without-build-and-full-verify",
        "final-targeted-rerun-without-build-and-full-verify",
    ],
)
def test_finish_stages_reject_out_of_order_or_unrepaired_tails(markers):
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
