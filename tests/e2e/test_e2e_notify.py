"""File-backend notification E2E: the one backend a headless container can
assert on (macOS rendering gets a manual live check — see the build ledger)."""

import pytest

from .harness import run_in

pytestmark = pytest.mark.e2e


def test_internal_notify_appends_file_line(container):
    rc, out = run_in(container, ["omc", "configure", "--defaults"])
    assert rc == 0, out
    for pair in (
        "notifications.enabled=true",
        "notifications.backend=file:///tmp/omc-notifications.log",
    ):
        rc, out = run_in(container, ["omc", "configure", "--set", pair])
        assert rc == 0, out

    payload = '{"hook_event_name": "Notification", "message": "needs permission"}'
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            f"echo '{payload}' | OMC_SLUG=e2e-slug omc internal notify --provider claude",
        ],
    )
    assert rc == 0, out

    rc, out = run_in(container, ["cat", "/tmp/omc-notifications.log"])
    assert rc == 0, out
    cols = out.strip().split("\t")
    assert cols[1:] == ["e2e-slug", "claude", "Notification", "needs permission"]


def test_internal_notify_disabled_is_silent(container):
    rc, out = run_in(container, ["omc", "configure", "--defaults"])
    assert rc == 0, out  # notifications default OFF
    rc, out = run_in(
        container,
        ["bash", "-c", "echo '{}' | omc internal notify --provider claude"],
    )
    assert rc == 0, out
    rc, _ = run_in(container, ["test", "-e", "/tmp/omc-notifications.log"])
    assert rc != 0  # nothing written
