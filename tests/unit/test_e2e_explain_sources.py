"""Deterministic coverage of the live explain-source answer validator."""

import pytest

from tests.e2e.test_e2e_explain_sources import _assert_source_statuses

_ECHO = "Source echo: available (indexed commit 0123456789abcdef)"
_VANISHED = "Source vanished: unavailable — MCP server `vanished` is not registered"


def test_source_conflict_heading_is_not_a_status():
    _assert_source_statuses(
        "**Source conflict:** the `echo` finding contradicts the local code.\n"
        + _ECHO
        + "\n"
        + _VANISHED
    )


@pytest.mark.parametrize(
    "lines",
    [
        [_ECHO],
        [_ECHO, _ECHO, _VANISHED],
        [_VANISHED, _ECHO],
        ["Source other: available", _VANISHED],
        ["Source echo: unavailable", _VANISHED],
        [_ECHO, "Source vanished: available — not registered"],
        [_ECHO, "Source vanished: unavailable — no reason"],
        [_ECHO, _VANISHED, "Source other: available"],
    ],
)
def test_source_status_contract_rejects_invalid_reports(lines):
    with pytest.raises(AssertionError):
        _assert_source_statuses("\n".join(lines))
