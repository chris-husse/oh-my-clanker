"""Finish stage order within the full implementation session's marker history."""

import pytest

from tests.e2e.lifecycle_helpers import _assert_finish_stage_order, _assert_recorded


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
