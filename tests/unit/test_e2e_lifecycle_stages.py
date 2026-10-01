"""Finish stage order within the full implementation session's marker history."""

import pytest

from tests.e2e.lifecycle_helpers import _assert_finish_stage_order


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
