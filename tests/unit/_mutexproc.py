"""Real-subprocess drivers for watch/start mutex tests. Contention MUST cross
a process boundary: flock conflicts are per open-file-description, and
filelock within one process can behave reentrantly — in-process 'contention'
would pass vacuously."""

from __future__ import annotations

import fcntl
import subprocess
import sys
import time

_WATCH_DRIVER = (
    "import sys\n"
    "from omc.config.schema import Config\n"
    "from omc.toolctx import ToolContext\n"
    "from omc.watch import run_watch\n"
    "sys.exit(run_watch(ToolContext.from_env(), Config(),\n"
    "    interval=float(sys.argv[1]), once=sys.argv[2] == '1',\n"
    "    clear_mutex=sys.argv[3] == '1'))\n"
)


def spawn_watch(repo, env, *, once=False, clear=False, interval=1, stderr_path):
    """Launch a REAL `run_watch` in a child process; stderr streams to a file
    the test polls (pipes would deadlock a long-lived loop)."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WATCH_DRIVER,
            str(interval),
            "1" if once else "0",
            "1" if clear else "0",
        ],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=open(stderr_path, "w"),
    )


def run_watch_once(repo, env, *, clear=False):
    """A single-tick watch run to completion; captures output."""
    return subprocess.run(
        [sys.executable, "-c", _WATCH_DRIVER, "1", "1", "1" if clear else "0"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def install_slow_hook(repo, marker, sleep_s):
    """post-watch hook that proves it started (marker file) then sleeps —
    keeps the watch BUSY deterministically after an action tick."""
    hook = repo / ".omc" / "hooks" / "post-watch.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/bash\ntouch {marker}\nsleep {sleep_s}\n")


def wait_for(pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s: {pred}")


def flock_free(path) -> bool:
    """True when nothing holds an exclusive flock on `path` (probe from THIS
    process — flock conflicts apply across fds, so this sees other holders)."""
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(f, fcntl.LOCK_UN)
        return True
