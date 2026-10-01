import fcntl
import threading
import time
from types import SimpleNamespace

import pytest

from tests.e2e.parallel import CODEX_GROUP, item_provider, wait_for_lock


class _Node:
    def __init__(self, params=None, marker=None):
        self.callspec = SimpleNamespace(params=params) if params is not None else None
        self._marker = marker

    def get_closest_marker(self, name):
        return self._marker if name == "e2e_provider" else None


def test_item_provider_prefers_param_then_marker():
    assert item_provider(_Node(params={"provider": "codex"})) == "codex"
    assert item_provider(_Node(marker=SimpleNamespace(args=("claude",)))) == "claude"
    assert item_provider(_Node()) is None


def test_codex_group_is_one_constant():
    assert CODEX_GROUP == "codex-account"


def test_wait_for_lock_blocks_until_released(tmp_path):
    path = tmp_path / "lock"
    holder = path.open("a+b")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    waiter = path.open("a+b")
    released = []

    def release():
        time.sleep(0.3)
        fcntl.flock(holder, fcntl.LOCK_UN)
        released.append(time.monotonic())

    threading.Thread(target=release).start()
    started = time.monotonic()
    wait_for_lock(waiter, timeout=5, poll=0.05)
    assert released and time.monotonic() - started >= 0.3


def test_wait_for_lock_times_out(tmp_path):
    path = tmp_path / "lock"
    holder = path.open("a+b")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(TimeoutError, match="still held after"):
        wait_for_lock(path.open("a+b"), timeout=0.2, poll=0.05)
