# Watch Locks (`omc start` waits for `omc watch` idle) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `omc start` blocks (by default) until `omc watch` re-enters its idle
pause before cutting a worktree, and two `omc watch` instances can never run
against one primary.

**Architecture:** Two kernel advisory locks (via `filelock`) in the repo's
shared `.git` dir, owned by a new `src/omc/watchlock.py` module. The INSTANCE
lock is held for `omc watch`'s whole lifetime (parallel watch → bail,
`--clear-mutex` bypass); the BUSY lock is held only during each tick's busy
portion, so lock-free ⇔ watch-idle. `omc start` probes the busy lock
(momentary acquire-and-release — it never holds anything) before
`worktree.sync_base`/`create_worktree`; `--no-mutex` skips. Kernel auto-release
on process death means a crashed/SIGKILLed watch never wedges anything.

**Tech Stack:** Python 3.11+, `filelock>=3,<4` (new runtime dep), pytest,
real subprocesses for contention tests (flock semantics are per
open-file-description — same-process fd tricks are not equivalent).

**Spec:** `docs/superpowers/specs/2026-07-23-omc-start-wait-for-watch-idle-design.md`

## Global Constraints

- New runtime dependency EXACTLY `filelock>=3,<4` in `pyproject.toml`; no other new deps.
- Lock filenames EXACTLY: `omc-watch.lock` (instance), `omc-watch-busy.lock` (busy), in the dir printed by `git rev-parse --git-common-dir`.
- Watch bail message EXACTLY (one line, stderr, exit 1): ``Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass``
- Start wait message EXACTLY (one line, stderr): ``→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass``
- `omc start` NEVER holds a lock across any work — probe only. `--dry-run` never locks.
- Watch doctrine: foreground-only, a tick failure warns and skips, the loop never crashes; lock trouble must warn, never wedge.
- Gate before every commit: `uvx ruff format .` then `just build` (ruff format --check + ruff check + `uv run pytest -m "not e2e" -q`) passes.
- This worktree's `.venv` may be a copied snapshot — run `uv sync --reinstall` once in Task 1 before trusting test runs (see Task 1 Step 0).
- Model-tier policy: every task carries a `Model:` line naming a tier (top / heavy coding / standard coding). Cheap/fast tier never used.

---

### Task 1: `watchlock` module + `filelock` dependency

**Model:** heavy coding tier

**Files:**
- Modify: `pyproject.toml` (dependencies list, line ~[project])
- Create: `src/omc/watchlock.py`
- Test: `tests/unit/test_watchlock.py`

**Interfaces:**
- Consumes: `omc.toolctx.ToolContext` (`.run`, `.git_bin`).
- Produces (later tasks rely on these exact names):
  - `INSTANCE_LOCK = "omc-watch.lock"`, `BUSY_LOCK = "omc-watch-busy.lock"`
  - `WATCH_BAIL_MSG: str`, `START_WAIT_MSG: str` (exact strings from Global Constraints)
  - `locks_dir(ctx: ToolContext, cwd: str | None = None) -> Path | None`
  - `watch_locks(ctx: ToolContext, cwd: str | None = None) -> tuple[FileLock, FileLock] | None` — (instance, busy)
  - `busy_lock(ctx: ToolContext, cwd: str | None = None) -> FileLock | None`
  - `acquire_instance(lock: FileLock, *, clear: bool) -> bool`
  - `wait_until_idle(lock: FileLock, *, say: Callable[[str], None] | None = None) -> None`

- [ ] **Step 0: Refresh the venv and add the dependency**

In `pyproject.toml` change:

```toml
dependencies = ["questionary>=2.0,<3", "pyyaml>=6,<7"]
```

to:

```toml
dependencies = ["questionary>=2.0,<3", "pyyaml>=6,<7", "filelock>=3,<4"]
```

Run: `uv sync --reinstall` (updates `uv.lock`, installs `filelock`, and heals a
possibly-copied worktree venv).
Expected: exits 0; `uv run python -c "import filelock; print(filelock.__version__)"` prints a 3.x version.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_watchlock.py`:

```python
"""watchlock: lock identities + acquire idioms. Contention cases that need a
SECOND holder use a real subprocess — flock conflicts are per
open-file-description, and filelock instances within one process can behave
reentrantly, so in-process 'contention' would test the wrong thing."""

import subprocess
import sys

from omc.toolctx import ToolContext
from omc.watchlock import (
    BUSY_LOCK,
    INSTANCE_LOCK,
    START_WAIT_MSG,
    WATCH_BAIL_MSG,
    acquire_instance,
    busy_lock,
    locks_dir,
    wait_until_idle,
    watch_locks,
)

_HOLDER = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    "FileLock(sys.argv[1]).acquire()\n"
    "print('held', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


def _hold_in_subprocess(path, seconds):
    """Acquire `path` in a child process; returns after the child confirms."""
    p = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p.stdout.readline().strip() == "held"
    return p


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo


def _ctx():
    import os

    return ToolContext.from_env(dict(os.environ))


def test_locks_dir_is_git_dir_in_primary(tmp_path):
    repo = _make_repo(tmp_path)
    d = locks_dir(_ctx(), cwd=str(repo))
    assert d == (repo / ".git").resolve()


def test_locks_dir_from_worktree_is_the_shared_git_dir(tmp_path):
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "feat", cwd=repo)
    d = locks_dir(_ctx(), cwd=str(wt))
    assert d == (repo / ".git").resolve()  # SHARED dir, not the worktree's


def test_locks_dir_outside_a_repo_is_none(tmp_path):
    outside = tmp_path / "empty"
    outside.mkdir()
    assert locks_dir(_ctx(), cwd=str(outside)) is None
    assert watch_locks(_ctx(), cwd=str(outside)) is None
    assert busy_lock(_ctx(), cwd=str(outside)) is None


def test_lock_paths_use_the_exact_filenames(tmp_path):
    repo = _make_repo(tmp_path)
    instance, busy = watch_locks(_ctx(), cwd=str(repo))
    assert instance.lock_file.endswith(INSTANCE_LOCK)
    assert busy.lock_file.endswith(BUSY_LOCK)
    assert busy_lock(_ctx(), cwd=str(repo)).lock_file == busy.lock_file


def test_acquire_instance_free_then_taken(tmp_path):
    repo = _make_repo(tmp_path)
    instance, _ = watch_locks(_ctx(), cwd=str(repo))
    assert acquire_instance(instance, clear=False) is True
    try:
        holder_view, _ = watch_locks(_ctx(), cwd=str(repo))
        # a SECOND process must be refused
        p = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "from filelock import FileLock, Timeout\n"
                "try:\n"
                "    FileLock(sys.argv[1]).acquire(timeout=0)\n"
                "except Timeout:\n"
                "    sys.exit(3)\n"
                "sys.exit(0)\n",
                instance.lock_file,
            ],
        )
        assert p.returncode == 3
    finally:
        instance.release()


def test_acquire_instance_refused_when_subprocess_holds(tmp_path):
    repo = _make_repo(tmp_path)
    instance, _ = watch_locks(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(instance.lock_file, 30)
    try:
        assert acquire_instance(instance, clear=False) is False
    finally:
        p.kill()
        p.wait()


def test_acquire_instance_clear_steals_from_a_live_holder(tmp_path):
    repo = _make_repo(tmp_path)
    instance, _ = watch_locks(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(instance.lock_file, 30)
    try:
        assert acquire_instance(instance, clear=True) is True  # unlink + fresh file
        instance.release()
    finally:
        p.kill()
        p.wait()


def test_wait_until_idle_free_is_silent_and_leaves_lock_free(tmp_path):
    repo = _make_repo(tmp_path)
    lock = busy_lock(_ctx(), cwd=str(repo))
    said = []
    wait_until_idle(lock, say=said.append)
    assert said == []
    assert lock.is_locked is False


def test_wait_until_idle_blocks_until_holder_exits_and_narrates_once(tmp_path):
    repo = _make_repo(tmp_path)
    lock = busy_lock(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(lock.lock_file, 1.5)  # holder self-releases
    said = []
    wait_until_idle(lock, say=said.append)  # must block ~1.5s then return
    p.wait()
    assert said == [START_WAIT_MSG]
    assert lock.is_locked is False


def test_message_constants_are_exact():
    assert WATCH_BAIL_MSG == (
        "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"
    )
    assert START_WAIT_MSG == (
        "→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watchlock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'omc.watchlock'`

- [ ] **Step 3: Write the implementation**

Create `src/omc/watchlock.py`:

```python
"""Advisory watch locks: `omc watch` mutual exclusion + busy/idle signaling.

Two flock-based locks (via filelock) live in the repo's SHARED .git dir
(`git rev-parse --git-common-dir` — the primary's, even from a worktree):

- omc-watch.lock (INSTANCE): held by `omc watch` for its entire lifetime.
  Forbids parallel watches on one primary; `--clear-mutex` bypasses.
- omc-watch-busy.lock (BUSY): held only while a watch tick is doing work —
  free means any running watch is idle. `omc start` probes it before cutting
  a worktree so it never snapshots a half-updated primary.

The kernel releases flock locks when their holder dies, so a crashed or
SIGKILLed watch never wedges anything and a watch RESTART always finds the
locks takeable. Caveat: flock is unreliable on NFS mounts — acceptable for a
local dev tool.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from filelock import FileLock, Timeout

from .toolctx import ToolContext

INSTANCE_LOCK = "omc-watch.lock"
BUSY_LOCK = "omc-watch-busy.lock"

WATCH_BAIL_MSG = "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"
START_WAIT_MSG = "→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass"


def locks_dir(ctx: ToolContext, cwd: str | None = None) -> Path | None:
    """The repo's SHARED .git dir, or None outside a repo. --git-common-dir
    may print a path relative to the asking directory — resolve against it."""
    try:
        cp = ctx.run([ctx.git_bin, "rev-parse", "--git-common-dir"], cwd=cwd)
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    raw = (cp.stdout or "").strip()
    if not raw:
        return None
    return (Path(cwd or ".") / raw).resolve()


def watch_locks(ctx: ToolContext, cwd: str | None = None) -> tuple[FileLock, FileLock] | None:
    """(instance, busy) for `omc watch`; None outside a repo."""
    d = locks_dir(ctx, cwd)
    if d is None:
        return None
    return FileLock(str(d / INSTANCE_LOCK)), FileLock(str(d / BUSY_LOCK))


def busy_lock(ctx: ToolContext, cwd: str | None = None) -> FileLock | None:
    """The busy lock alone — all `omc start` ever probes; None outside a repo."""
    d = locks_dir(ctx, cwd)
    return None if d is None else FileLock(str(d / BUSY_LOCK))


def acquire_instance(lock: FileLock, *, clear: bool) -> bool:
    """Take the watch instance lock, non-blocking. `clear` unlinks the lock
    file first — a fresh inode, deliberately breaking mutual exclusion with a
    live holder (that is what the --clear-mutex bypass means). False = held
    elsewhere: the caller prints WATCH_BAIL_MSG and exits 1."""
    if clear:
        Path(lock.lock_file).unlink(missing_ok=True)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return False
    return True


def wait_until_idle(lock: FileLock, *, say: Callable[[str], None] | None = None) -> None:
    """Block until the busy lock is free, then LEAVE IT FREE: a momentary
    acquire-and-release is the probe — an advisory lock cannot be observed
    without touching it, and it is never carried into any work. Held → narrate
    once via `say`, then wait forever (Ctrl-C aborts)."""
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if say is not None:
            say(START_WAIT_MSG)
        lock.acquire()  # filelock default: block indefinitely
    lock.release()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_watchlock.py -q`
Expected: all PASS (the blocking test takes ~1.5s by design).

- [ ] **Step 5: Format, gate, commit**

```bash
uvx ruff format . && just build
git add pyproject.toml uv.lock src/omc/watchlock.py tests/unit/test_watchlock.py
git commit -m "feat: watchlock module — instance + busy advisory locks (filelock)"
```

---

### Task 2: watch side — instance lifetime lock, busy tick lock, `--clear-mutex`

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/watch.py` (imports ~line 12-33; `run_watch`, lines 406-463)
- Modify: `src/omc/cli.py` (watch subparser ~line 63-68; watch dispatch ~line 150-158)
- Create: `tests/unit/_mutexproc.py` (subprocess drivers shared with Task 3)
- Test: modify `tests/unit/test_watch.py` (in-process lock-state tests), create `tests/unit/test_watch_mutex.py` (subprocess contention tests)

**Interfaces:**
- Consumes (Task 1): `watch_locks(ctx, cwd)`, `acquire_instance(lock, clear=...)`, `WATCH_BAIL_MSG`.
- Produces:
  - `run_watch(..., clear_mutex: bool = False)` keyword param; `omc watch --clear-mutex` CLI flag.
  - `tests/unit/_mutexproc.py` helpers (exact signatures):
    - `spawn_watch(repo, env, *, once=False, clear=False, interval=1, stderr_path) -> subprocess.Popen`
    - `run_watch_once(repo, env, *, clear=False) -> subprocess.CompletedProcess`
    - `install_slow_hook(repo, marker, sleep_s) -> None`
    - `wait_for(pred, timeout=15.0) -> None` (raises AssertionError on timeout)
    - `flock_free(path) -> bool`

- [ ] **Step 1: Write the subprocess helper module**

Create `tests/unit/_mutexproc.py`:

```python
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
        [sys.executable, "-c", _WATCH_DRIVER, str(interval), "1" if once else "0", "1" if clear else "0"],
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
```

- [ ] **Step 2: Write the failing in-process lock-state tests**

Append to `tests/unit/test_watch.py` (reuses its `_repo_with_origin`,
`_push_remote_commit`, `_ctx_with_node_stub`, `_run_once`, `_run_loop`):

```python
def test_busy_lock_held_while_hook_runs_and_instance_held_while_idle(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    busy_path = repo / ".git" / "omc-watch-busy.lock"
    instance_path = repo / ".git" / "omc-watch.lock"
    probe_out = tmp_path / "probe.out"
    hook = repo / ".omc" / "hooks" / "post-watch.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    # the hook runs INSIDE the busy window: probe both locks from there
    hook.write_text(
        "#!/bin/bash\n"
        f'python3 -c "\n'
        f"import fcntl\n"
        f"out = open('{probe_out}', 'w')\n"
        f"for p in ('{busy_path}', '{instance_path}'):\n"
        f"    f = open(p, 'a')\n"
        f"    try:\n"
        f"        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        f"        out.write('free\\n')\n"
        f"    except OSError:\n"
        f"        out.write('locked\\n')\n"
        f'"\n'
    )
    rc = _run_once(repo, ctx)
    assert rc == 0
    assert probe_out.read_text() == "locked\nlocked\n"  # busy AND instance held mid-hook


def test_busy_lock_free_but_instance_held_during_idle_sleep(tmp_path, capsys):
    from ._mutexproc import flock_free

    _, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    seen = {}

    def between(i):
        # fake_sleep runs BETWEEN ticks — the idle window
        seen["busy_free"] = flock_free(repo / ".git" / "omc-watch-busy.lock")
        seen["instance_free"] = flock_free(repo / ".git" / "omc-watch.lock")

    rc = _run_loop(repo, ctx, ticks=1, between=between)
    assert rc == 0
    assert seen == {"busy_free": True, "instance_free": False}


def test_instance_lock_released_after_clean_stop(tmp_path, capsys):
    from ._mutexproc import flock_free

    _, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_loop(repo, ctx, ticks=1) == 0  # KeyboardInterrupt path
    assert flock_free(repo / ".git" / "omc-watch.lock")
    assert flock_free(repo / ".git" / "omc-watch-busy.lock")


def test_watch_clear_mutex_flag_parses():
    from omc.cli import build_parser

    args = build_parser().parse_args(["watch", "--clear-mutex"])
    assert args.clear_mutex is True
    assert build_parser().parse_args(["watch"]).clear_mutex is False
```

- [ ] **Step 3: Write the failing subprocess contention tests**

Create `tests/unit/test_watch_mutex.py`:

```python
"""The spec's REQUIRED subprocess tests: restart-after-SIGKILL always wipes
the lock; a second live watch bails with the exact message; --clear-mutex
bypasses. Real processes, real flock — no mocks."""

import os
import signal

from ._mutexproc import install_slow_hook, run_watch_once, spawn_watch, wait_for
from .test_watch import _ctx_with_node_stub, _push_remote_commit, _repo_with_origin

BAIL = "Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass"


def _sub_env(env):
    """Env for driver subprocesses: the stub env layered over the real one."""
    return {**os.environ, **env}


def test_restart_after_sigkill_mid_tick_acquires_cleanly(tmp_path):
    """REQUIRED: a watch restart always wipes the lock — even after SIGKILL
    mid-hook (the kernel releases flock with the process; no --clear-mutex)."""
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    marker = tmp_path / "hook-started"
    install_slow_hook(repo, marker, 60)
    err1 = tmp_path / "watch1.err"
    p1 = spawn_watch(repo, _sub_env(ctx.env), stderr_path=err1)
    try:
        wait_for(marker.exists)  # p1 is now BUSY, both locks held
        os.kill(p1.pid, signal.SIGKILL)
        p1.wait()
        install_slow_hook(repo, tmp_path / "m2", 0)  # restart must not re-sleep 60s
        cp = run_watch_once(repo, _sub_env(ctx.env))
        assert cp.returncode == 0, cp.stderr
        assert BAIL not in cp.stderr
    finally:
        if p1.poll() is None:
            p1.kill()
            p1.wait()


def test_second_watch_bails_with_exact_message_and_clear_mutex_bypasses(tmp_path):
    origin, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    err1 = tmp_path / "watch1.err"
    p1 = spawn_watch(repo, _sub_env(ctx.env), interval=60, stderr_path=err1)
    try:
        # idle proof: the loop announced itself and finished its first tick
        wait_for(lambda: "waiting for changes" in err1.read_text() if err1.exists() else False)
        cp = run_watch_once(repo, _sub_env(ctx.env))
        assert cp.returncode == 1
        assert BAIL in cp.stderr
        cp = run_watch_once(repo, _sub_env(ctx.env), clear=True)
        assert cp.returncode == 0, cp.stderr
        assert BAIL not in cp.stderr
    finally:
        p1.kill()
        p1.wait()
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -k "lock or clear_mutex" tests/unit/test_watch_mutex.py -q`
Expected: FAIL — `run_watch` has no `clear_mutex` kwarg / no lock files created / `--clear-mutex` unrecognized.

- [ ] **Step 5: Implement the watch side**

In `src/omc/watch.py` add imports (after the existing `from .buildprogress import ...` block, keeping alphabetical grouping):

```python
from contextlib import nullcontext
```

and (with the other `.`-relative imports):

```python
from .watchlock import WATCH_BAIL_MSG, acquire_instance, watch_locks
```

Replace `run_watch` (lines 406-463) with:

```python
def run_watch(
    ctx: ToolContext,
    cfg: Config,
    *,
    interval: int = 30,
    once: bool = False,
    enable_documentation: bool = False,
    auto_build: bool = False,
    rebase: bool = False,
    clear_mutex: bool = False,
) -> int:
    root = repo_root(ctx)
    if root is None:
        print("error: omc watch must run inside a git repository", file=sys.stderr)
        return 1
    primary = primary_root(ctx)
    if primary and Path(primary).resolve() != Path(root).resolve():
        print(
            f"error: omc watch runs in the PRIMARY checkout ({primary}), not a worktree — "
            "worktrees refresh via /omc:rebase-main.",
            file=sys.stderr,
        )
        return 1
    if not gitnexus_cli(ctx).is_file():
        print(
            "error: GitNexus is not installed yet — run /omc:index once in a session "
            "first (it installs GitNexus), then start omc watch.",
            file=sys.stderr,
        )
        return 1
    ensure_wt_config(ctx, root)
    locks = watch_locks(ctx, cwd=root)
    if locks is None:
        # Unreachable inside a repo in practice; doctrine says warn, never wedge.
        _say("✗ could not resolve the watch lock dir — running without the mutex")
        instance = busy = None
    else:
        instance, busy = locks
        if not acquire_instance(instance, clear=clear_mutex):
            print(WATCH_BAIL_MSG, file=sys.stderr)
            return 1
    _say(
        f"→ watching {root} (base {cfg.worktree.base_branch}, every {interval}s"
        f"{', documentation enabled' if enable_documentation else ''}) — Ctrl-C stops"
    )
    last: str | None = None
    chain_last: str | None = None
    try:
        while True:
            # Busy lock held for the WHOLE busy portion (tick + hooks): free ⇔ idle.
            with busy if busy is not None else nullcontext():
                chain_last = _chain_tick(ctx, root, chain_last)
                last = _tick(
                    ctx,
                    cfg,
                    root,
                    enable_documentation=enable_documentation,
                    force_refresh=once,
                    last=last,
                    rebase=rebase,
                )
                if last in ("synced", "refreshed"):
                    _post_watch_hook(ctx, root, last)
                    if auto_build:
                        _auto_build(ctx, cfg, root)
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        _say("· stopped")
        return 0
    finally:
        if instance is not None and instance.is_locked:
            instance.release()
```

In `src/omc/cli.py` after the `--rebase` argument (line 63-68) add:

```python
    p_watch.add_argument(
        "--clear-mutex",
        action="store_true",
        help="Remove a leftover watch mutex and run anyway (bypasses the "
        "single-instance guard)",
    )
```

and in `_dispatch`'s watch branch (line 150-158) add the kwarg:

```python
        return run_watch(
            ctx,
            cfg,
            interval=args.interval,
            once=args.once,
            enable_documentation=args.enable_documentation,
            auto_build=args.auto_build,
            rebase=args.rebase,
            clear_mutex=args.clear_mutex,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch.py tests/unit/test_watch_mutex.py tests/unit/test_watchlock.py -q`
Expected: all PASS (subprocess tests take a few seconds each — real processes and a real SIGKILL).

- [ ] **Step 7: Format, gate, commit**

```bash
uvx ruff format . && just build
git add src/omc/watch.py src/omc/cli.py tests/unit/_mutexproc.py tests/unit/test_watch.py tests/unit/test_watch_mutex.py
git commit -m "feat: omc watch takes instance+busy locks; --clear-mutex bypass"
```

---

### Task 3: start side — probe the busy lock, `--no-mutex`

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/start.py` (imports ~line 10-21; `run_start`, the block before line 113 `_say("→ creating worktree ...")`)
- Modify: `src/omc/cli.py` (start subparser ~line 46; start dispatch ~line 143)
- Test: create `tests/unit/test_start_mutex.py`

**Interfaces:**
- Consumes (Task 1): `busy_lock(ctx)`, `wait_until_idle(lock, say=...)`, `START_WAIT_MSG`. (Task 2): `tests/unit/_mutexproc.py` helpers, `run_watch` subprocess drivers.
- Produces: `run_start(..., no_mutex: bool = False)` keyword param; `omc start --no-mutex` CLI flag.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_start_mutex.py`:

```python
"""start-side mutex: probe-only semantics against REAL locks and repos.
(The stock test_start.py suite stubs git with 'not a repo', so its runs
self-skip the probe — these tests build real repos instead.)"""

import json
import os
import stat
import subprocess
import sys
import threading

import pytest

from omc.config.schema import Config
from omc.start import run_start
from omc.toolctx import ToolContext

from ._mutexproc import install_slow_hook, spawn_watch, wait_for
from ._stubs import make_stub
from .test_watch import _ctx_with_node_stub, _push_remote_commit, _repo_with_origin

OK_VERDICT = 'OMC_SLUG {"ok": true, "slug": "proj-1-fix-login"}'
WAIT_LINE = "→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass"

_HOLDER = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    "FileLock(sys.argv[1]).acquire()\n"
    "print('held', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


@pytest.fixture(autouse=True)
def _no_agents_chain(monkeypatch):
    """run_start calls ensure_agents_chain unguarded when cwd is a real repo;
    the AGENTS.md distribution asset may be absent in a dev venv and is
    orthogonal to the mutex under test — neutralize it."""
    monkeypatch.setattr("omc.start.ensure_agents_chain", lambda ctx, root: "ok")


def _hold(path, seconds):
    p = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p.stdout.readline().strip() == "held"
    return p


def _start_env(tmp_path, repo, *, wt_probes_lock=False):
    """claude + wt stubs ahead of the REAL PATH (real git!), cwd-able repo.
    With wt_probes_lock the wt stub FAILS if the busy lock is held when wt
    runs — proving start released the probe before creating the worktree."""
    bindir = tmp_path / "startbin"
    make_stub(bindir, "claude", stdout=f"omc@oh-my-clanker\n{OK_VERDICT}")
    wtree = tmp_path / "wtree"
    wtree.mkdir(exist_ok=True)
    wt = bindir / "wt"
    payload = json.dumps({"path": str(wtree)})
    if wt_probes_lock:
        lock_path = repo / ".git" / "omc-watch-busy.lock"
        wt.write_text(
            "#!/bin/sh\n"
            f"python3 -c \"import fcntl; f = open('{lock_path}', 'a'); "
            'fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)" || exit 7\n'
            f"echo '{payload}'\n"
        )
    else:
        wt.write_text(f"#!/bin/sh\necho '{payload}'\n")
    wt.chmod(wt.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "HOME": str(tmp_path),
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "SHELL": "/bin/bash",
    }


def test_probe_is_released_before_the_worktree_is_created(tmp_path, capsys, monkeypatch):
    _, repo = _repo_with_origin(tmp_path)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo, wt_probes_lock=True)
    rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True)
    assert rc == 0  # wt exits 7 (→ rc != 0 path) if the probe were still held
    assert (repo / ".git" / "omc-watch-busy.lock").exists()  # probe touched it


def test_start_waits_for_a_held_busy_lock_and_narrates(tmp_path, capsys, monkeypatch):
    _, repo = _repo_with_origin(tmp_path)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    holder = _hold(repo / ".git" / "omc-watch-busy.lock", 1.5)  # self-releases
    try:
        rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True)
    finally:
        holder.wait()
    assert rc == 0
    assert WAIT_LINE in capsys.readouterr().err


def test_no_mutex_skips_the_probe_entirely(tmp_path, capsys, monkeypatch):
    _, repo = _repo_with_origin(tmp_path)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    holder = _hold(repo / ".git" / "omc-watch-busy.lock", 60)  # held THROUGHOUT
    try:
        rc = run_start(
            ToolContext.from_env(env), Config(), "PROJ-1", headless=True, no_mutex=True
        )
    finally:
        holder.kill()
        holder.wait()
    assert rc == 0  # would hang forever if the probe ran
    assert WAIT_LINE not in capsys.readouterr().err


def test_parallel_starts_all_work_while_watch_runs(tmp_path, capsys, monkeypatch):
    """REQUIRED: multiple `omc start` in parallel must all work while
    `omc watch` is running (start never holds the lock, so they cannot
    exclude each other)."""
    origin, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    err1 = tmp_path / "watch.err"
    p1 = spawn_watch(repo, {**os.environ, **ctx.env}, interval=60, stderr_path=err1)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    try:
        wait_for(lambda: "waiting for changes" in err1.read_text() if err1.exists() else False)
        results = [None, None, None]

        def one(i):
            results[i] = run_start(
                ToolContext.from_env(env), Config(), f"PROJ-{i}", headless=True
            )

        threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert results == [0, 0, 0]
    finally:
        p1.kill()
        p1.wait()


def test_start_waits_out_a_busy_watch_then_proceeds(tmp_path, capsys, monkeypatch):
    """End-to-end contention against a REAL busy watch (slow post-watch hook)."""
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    marker = tmp_path / "hook-started"
    install_slow_hook(repo, marker, 3)
    err1 = tmp_path / "watch.err"
    p1 = spawn_watch(repo, {**os.environ, **ctx.env}, interval=60, stderr_path=err1)
    monkeypatch.chdir(repo)
    env = _start_env(tmp_path, repo)
    try:
        wait_for(marker.exists)  # watch is now BUSY in the hook
        rc = run_start(ToolContext.from_env(env), Config(), "PROJ-1", headless=True)
        assert rc == 0
        assert WAIT_LINE in capsys.readouterr().err
    finally:
        p1.kill()
        p1.wait()


def test_start_no_mutex_flag_parses():
    from omc.cli import build_parser

    args = build_parser().parse_args(["start", "ctx", "--no-mutex"])
    assert args.no_mutex is True
    assert build_parser().parse_args(["start", "ctx"]).no_mutex is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_start_mutex.py -q`
Expected: FAIL — `run_start` has no `no_mutex` kwarg; no wait line; `--no-mutex` unrecognized.

- [ ] **Step 3: Implement the start side**

In `src/omc/start.py` add to the `.`-relative imports (line 10-21):

```python
from .watchlock import busy_lock, wait_until_idle
```

Change the `run_start` signature (line 60-67):

```python
def run_start(
    ctx: ToolContext,
    cfg: Config,
    context: str,
    *,
    dry_run: bool = False,
    headless: bool = False,
    no_mutex: bool = False,
) -> int:
```

Immediately before `_say(f"→ creating worktree {branch} (base origin/{base})")`
(line 113; AFTER the `if dry_run:` block — dry-run never locks) insert:

```python
    if not no_mutex:
        # Never HOLD the lock — verify it is free (momentary acquire-and-release)
        # so we never snapshot a primary that `omc watch` is mid-way through
        # updating. None = not in a repo: nothing to guard.
        lock = busy_lock(ctx)
        if lock is not None:
            wait_until_idle(lock, say=_say)
```

In `src/omc/cli.py` after the `--headless` argument (line 46) add:

```python
    p_start.add_argument(
        "--no-mutex",
        action="store_true",
        help="Do not wait for an in-flight `omc watch` update before creating "
        "the worktree",
    )
```

and change the start dispatch (line 143):

```python
        return run_start(
            ctx,
            cfg,
            args.context,
            dry_run=args.dry_run,
            headless=args.headless,
            no_mutex=args.no_mutex,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_start_mutex.py tests/unit/test_start.py -q`
Expected: all PASS — including the untouched stock `test_start.py` (its git
stub reports "not a repo", so the probe self-skips there by construction).

- [ ] **Step 5: Format, gate, commit**

```bash
uvx ruff format . && just build
git add src/omc/start.py src/omc/cli.py tests/unit/test_start_mutex.py
git commit -m "feat: omc start waits for watch idle before cutting a worktree; --no-mutex"
```

---

### Task 4: E2E assertion, README, full gate

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/test_e2e_start.py` (`test_start_headless_creates_worktree_and_seeds`, lines 9-36)
- Modify: `README.md` (the `omc start` / `omc watch` usage sections — locate by searching for `--headless` and `--interval`)

**Interfaces:**
- Consumes: lock filenames `omc-watch.lock` / `omc-watch-busy.lock` under the primary's `.git` (Tasks 1-3 behavior).
- Produces: nothing new — documentation + E2E coverage.

- [ ] **Step 1: Extend the E2E start test**

In `test_start_headless_creates_worktree_and_seeds`, directly after the
`wt list` assertion (`assert rc2 == 0 and "feature/proj-1" in wtout, wtout`),
add:

```python
    # the busy-lock probe ran: filelock touched the lock file in the shared .git
    rc3, lockout = run_in(container, ["test", "-f", ".git/omc-watch-busy.lock"], cwd=repo)
    assert rc3 == 0, f"busy-lock file missing in primary .git: {lockout}"
```

- [ ] **Step 2: Run the E2E test (token-gated)**

Run: `uv run pytest tests/e2e/test_e2e_start.py -m e2e -q -k headless`
Expected: PASS. If provider tokens are absent locally the suite FAILS LOUD by
design — in that case report the failure output verbatim to the main session
and let the reviewer decide; do not mark this step done silently.

- [ ] **Step 3: Update the README**

Search `README.md` for the `omc watch` and `omc start` sections. Add one line
each, matching the surrounding style:

- `omc start`: default waits for an in-flight `omc watch` update to finish
  before cutting the worktree (never snapshots a half-updated primary);
  `--no-mutex` skips the wait.
- `omc watch`: single-instance per primary — a second watch exits with
  ``Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass``;
  a crashed watch never leaves a stale lock (kernel-released flock).

- [ ] **Step 4: Full gate and commit**

```bash
uvx ruff format . && just build
git add tests/e2e/test_e2e_start.py README.md
git commit -m "test+docs: E2E lock-file assertion; README mutex notes"
```

---

## Self-Review Notes

- Spec coverage: filelock dep (T1), watchlock module + `.git`-common-dir
  resolution (T1), instance lifetime lock + bail + `--clear-mutex` (T2), busy
  lock around tick incl. hooks (T2), start probe + wait line + `--no-mutex` +
  dry-run exemption (T3), REQUIRED tests — SIGKILL restart (T2), parallel
  starts during watch (T3) — second-watch bail exact message (T2), E2E
  lock-file assertion + docs (T4). No-repo self-skip covered by stock
  test_start.py + `test_locks_dir_outside_a_repo_is_none` (T1).
- Out of scope per spec: `omc internal rebase-main` probe (recorded follow-up).
