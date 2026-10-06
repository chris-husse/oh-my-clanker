"""Pure guards for the design-only audit refusal evidence."""

import pytest

from tests.e2e import lifecycle_helpers


@pytest.fixture
def unchanged():
    return {
        "source": {"greeting.py": "old"},
        "spec_plan": {"docs/superpowers/specs/design.md": "old"},
        "index": "index-entry",
        "head": "head-sha",
        "remote_refs": ["refs/heads/main sha"],
    }


def test_design_only_audit_refusal_accepts_clean_unchanged_state(unchanged):
    assert callable(getattr(lifecycle_helpers, "_assert_audit_refusal", None))
    lifecycle_helpers._assert_audit_refusal(
        unchanged,
        unchanged.copy(),
        "",
        "There is nothing to audit — run /omc:implement first.",
    )


@pytest.mark.parametrize("key", ["source", "spec_plan", "index", "head", "remote_refs"])
def test_design_only_audit_refusal_rejects_mutation(unchanged, key):
    after = unchanged.copy()
    after[key] = "changed"
    with pytest.raises(AssertionError, match=key):
        lifecycle_helpers._assert_audit_refusal(
            unchanged, after, "", "nothing to audit — run /omc:implement first"
        )


@pytest.mark.parametrize(
    "status, answer",
    [
        ("?? draft.md", "nothing to audit — run /omc:implement first"),
        ("", "Unknown command: /omc:audit"),
        ("", "May I proceed with implementation?"),
        ("", "nothing to audit"),
    ],
)
def test_design_only_audit_refusal_rejects_dirty_or_wrong_reason(unchanged, status, answer):
    with pytest.raises(AssertionError):
        lifecycle_helpers._assert_audit_refusal(unchanged, unchanged.copy(), status, answer)
