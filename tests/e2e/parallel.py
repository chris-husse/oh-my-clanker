"""Parallel-execution helpers shared by the E2E conftest and the Codex lock."""

from __future__ import annotations

import fcntl
import time

CODEX_GROUP = "codex-account"


def item_provider(item) -> str | None:
    """The provider a collected test declares: a `provider` param wins, then
    the `e2e_provider` marker, else None (generic container)."""
    callspec = getattr(item, "callspec", None)
    params = getattr(callspec, "params", {}) if callspec is not None else {}
    if "provider" in params:
        return params["provider"]
    marker = item.get_closest_marker("e2e_provider")
    return marker.args[0] if marker else None


def wait_for_lock(fd, timeout: float, poll: float = 1.0) -> None:
    """Take an exclusive flock, waiting up to `timeout` seconds. Polling with
    LOCK_NB keeps the wait interruptible by pytest-timeout's SIGALRM."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"lock still held after {timeout:.0f}s") from None
            time.sleep(poll)
