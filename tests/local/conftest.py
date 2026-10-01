"""tests/local: marker guard (spec §7) and the session-scoped private iTerm2 instance."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.local.private_iterm import (
    PrivateITerm,
    ProbeError,
    close_reporting,
    require_sdk,
    verify_launch,
)

LOCAL_DIR = Path(__file__).resolve().parent


def unmarked_local_items(items, local_dir: Path = LOCAL_DIR) -> list[str]:
    """Node ids of collected tests under tests/local lacking the local_iterm2 marker."""
    missing = []
    for item in items:
        path = Path(str(item.path)).resolve()
        if local_dir != path.parent and local_dir not in path.parents:
            continue
        if item.get_closest_marker("local_iterm2") is None:
            missing.append(item.nodeid)
    return missing


def pytest_collection_modifyitems(session, config, items):
    missing = unmarked_local_items(items)
    if missing:
        raise pytest.UsageError(
            "every module under tests/local must set `pytestmark = pytest.mark.local_iterm2` "
            "(`just check` collects tests/ on Linux CI); unmarked: " + ", ".join(missing)
        )


@pytest.fixture(scope="session")
def private_iterm():
    """Owns identity only (PID, suite, socket, copy, home); each test connects anew."""
    iterm2 = require_sdk()
    try:
        instance = PrivateITerm.start(LOCAL_DIR.parents[1])
    except ProbeError as exc:
        pytest.fail(f"private iTerm2 setup: {exc}")
    pending: BaseException | None = None
    try:
        try:
            asyncio.run(verify_launch(iterm2, instance))
        except ProbeError as exc:
            pytest.fail(f"private iTerm2 launch flags: {exc}")
        yield instance
    except GeneratorExit:
        raise
    except BaseException as exc:  # pytest.fail is an OutcomeException, not an Exception
        pending = exc
        raise
    finally:
        close_reporting(instance, pending)  # a teardown error never masks `pending`
