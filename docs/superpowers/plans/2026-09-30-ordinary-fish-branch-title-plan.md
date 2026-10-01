# Ordinary Fish Branch Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any interactive fish shell in iTerm2 pins its tab title to the literal Git branch through a fire-and-forget hook and a serialized `omc title` helper, with an `omc start` branch that reuses the same hook, automatic macOS provisioning, and a native test tier that owns a private iTerm2 instance.

**Architecture:** A packaged fish hook (`src/omc/assets/omc-title.fish`) decides `set <branch>` / `release` with one `git symbolic-ref` per prompt, writes the decision to `<omc home>/title-request/<uuid>-<pid>` and disowns `omc title apply <file>`. The helper (`src/omc/title.py`) takes a per-session `flock`, re-reads the request, dedupes against `<file>.applied`, drives the existing `iterm2_title` SDK worker under `ToolContext.run_bounded` (5 s), and throttles failures through an mtime-judged marker. `fish_integration.py` provisions the hook into `$XDG_CONFIG_HOME/fish/conf.d`, the installer runs the fresh CLI's `reconcile` non-abortingly, `FishShell.build_invocation` gains an `if`/`else` that sources the packaged hook inside iTerm2, and `tests/local/private_iterm.py` launches a copied iTerm2 bundle under a private `-suite`/HOME so no test ever touches the user's app.

**Tech Stack:** Python 3.12, fish 3.7+ syntax (host fish 4.9.3, CI fish 3.7), iTerm2 Python SDK 2.24, uv, pytest, `fcntl.flock`, macOS `open`/`pgrep`/`ps`/`defaults`/`ditto`.

**Spec:** `docs/superpowers/specs/2026-09-30-ordinary-fish-branch-title-design.md` — every task below argues from it; executors read both. Reusable code lives on the git ref `backup/ordinary-fish-pre-squash` (read with `git show 'backup/ordinary-fish-pre-squash:<path>'`); spec §8 lists what is reused and what is dropped.

## Global Constraints

- Product code budget: "roughly 500 lines" of new product code across `title.py`, `fish_integration.py`, the hook, and the installer/CLI/shell changes (spec §1 rule 2). Native fixture: "a few hundred lines"; "growth past about 600 is a stop-and-redesign signal" (§1 rule 3). No SDK monkeypatch anywhere in `tests/local/`.
- The hook is written to **fish 3.7 syntax** (`$last_pid`, `path`, `string` exist there); the packaged file is `src/omc/assets/omc-title.fish`, provisioned to `$XDG_CONFIG_HOME/fish/conf.d/omc-title.fish` (default `~/.config/fish/conf.d/`), first line starting with `# omc-managed fish title integration` followed by a version suffix (§2).
- `<omc home>` is `$OMC_HOME` when set, else `$HOME/.omc`, exactly as `ToolContext.from_env` resolves it (§1). Paths under it: `title-request/<uuid>-<pid>`, `title-lock/<uuid>`, `title-failed/<uuid>`, `integrations/fish-title.disabled`.
- Activation predicate (§2, §6): `status is-interactive`; `ITERM_SESSION_ID` matches `^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$`; `TERM_PROGRAM` is `iTerm.app` or `LC_TERMINAL` is `iTerm2`; neither `TMUX` nor `STY` set; `OMC_FISH_TITLE_DISABLE` not `1`. Never define or replace `fish_title`; never touch `config.fish`.
- Helper contract (§3): no stdout ever; nothing on success; one omc-authored stderr line on failure; exit 0 success, 1 failed/timed-out API write, 2 `Refusal` (control characters — Unicode category `Cc` — empty title, missing/malformed `ITERM_SESSION_ID`, unreadable request file). Worker deadline 5 s; failure cooldown 60 s by marker mtime; installer post-step deadline 30 s.
- `ToolContext` (`src/omc/toolctx.py`) is the only subprocess/env boundary in product code; argv lists only; exit codes 0/1/2/3 (`.omc/config/AGENTS.md`).
- Testing policy (`.omc/config/AGENTS.md`): red → green for every change; **never** `pytest.skip`/`skipif` — a missing prerequisite is `pytest.fail` naming the exact command; stub scripts live on a restricted PATH and use builtins or absolute paths; assert on artifacts; exact-argv assertions.
- **Never run `omc install` or `uv tool install` on your own initiative.** The single carve-out is `tests/unit/test_installed_wheel.py` (Task 8), which installs a wheel it built into `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`/`OMC_HOME`/`HOME`/`XDG_*` all asserted under pytest's tmp.
- Every module under `tests/local/` carries `pytestmark = pytest.mark.local_iterm2` (§7); `just check` (`uv run pytest -m "not e2e and not local_iterm2" -q`) collects `tests/` on Linux CI, so nothing under `tests/local/` may import `iterm2` at module level.
- The native tier never attaches to, reads from, or writes to the user's running iTerm2; never activates or selects anything in the private instance; never reads or writes `com.googlecode.iterm2` (§7).
- Commit messages: conventional prefix, and the trailer line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Do not launch iTerm2 or any GUI app in unit tasks; only `just iterm2-tests` (Tasks 1 and 9) launches the private instance.
- Model tiers (from the repo `CLAUDE.md`): every task names its tier; the cheap/fast tier is never used.

## Review Focus

1. Branch names with characters special to fish or iTerm2 (`$`, `(`, `\(`, `;`) must reach the tab literally and never be re-parsed — pinned in Task 4 (`test_special_characters_stay_literal`) and Task 3 (`test_set_passes_hostile_title_literally`).
2. A request file with an unexpected first line (`set ` with nothing after it, garbage, empty file — a shell that died mid-write) must exit 2 and write no marker, never a traceback — pinned in Task 3 (`test_apply_refuses_malformed_request_without_marker`).
3. A failure marker whose mtime is in the future (clock skew, restored backup) must not throttle forever — pinned in Task 3 (`test_future_marker_mtime_never_throttles`).
4. A shell that starts detached with no remembered branch releases, and pins as soon as a branch is checked out; git absent from PATH changes nothing — pinned in Task 4 (`test_detached_start_without_known_branch_releases_then_pins`, `test_missing_git_changes_nothing`).
5. `<omc home>` that cannot be created (a file sits where the directory should be) must yield the single stderr line and exit 1, never a traceback — pinned in Task 3 (`test_unwritable_home_reports_one_line_and_exits_one`).

## File Structure

New product files:
- `src/omc/__main__.py` — `python -m omc` == `omc`.
- `src/omc/title.py` — `omc title set|release|apply`; marker, lock, `.applied` dedupe.
- `src/omc/fish_integration.py` — hook provisioning (`enable|disable|status|reconcile`), ownership by prefix, non-blocking `remove_owned_hook`.
- `src/omc/assets/omc-title.fish` — the hook.

Modified product files:
- `src/omc/terminals.py` — `iterm2_session_id(env)` extracted; `Iterm2Terminal.set_title` calls it.
- `src/omc/terminal_title.py` — `validate_title(title)` shared.
- `src/omc/iterm2_title.py` — `--release`, `release_title`, `worker_argv`.
- `src/omc/cli/__init__.py` — `title` and `shell-integration` parsers, banner exemption, lazy dispatch.
- `src/omc/installer.py` — macOS post-step (`post_install`), distinct final report, owned-only uninstall.
- `src/omc/shells/fish.py` — `if`/`else` generated command.

Tests:
- `tests/local/__init__.py`, `tests/local/conftest.py` (marker guard + session fixture), `tests/local/private_iterm.py` (pure helpers + `PrivateITerm`), `tests/local/test_private_iterm_probe.py` (native smoke), `tests/local/test_iterm2_title.py` (migrated), `tests/local/test_ordinary_fish_title.py` (native hook + providers).
- `tests/unit/test_private_iterm.py`, `tests/unit/test_title.py`, `tests/unit/test_fish_hook.py`, `tests/unit/_fishpty.py`, `tests/unit/test_fish_integration.py`, `tests/unit/test_installed_wheel.py`; edits to `tests/unit/{test_terminals,test_terminal_title,test_iterm2_title,test_cli,test_installer,test_shells,test_fish_title,test_local_iterm2_harness}.py`.

Docs/config: `README.md`, `justfile`, `pyproject.toml` (marker text), `.omc/config/AGENTS.md` (one sentence).

## Conventions for every task

**Formatting gate applies to every commit.** `just build` runs `uvx ruff format --check .`
and `uvx ruff check .` with `line-length = 100` and `select = ["E", "F", "I", "UP", "B"]`.
Some code blocks in this plan exceed 100 columns as written; before each task's commit
step run `uvx ruff format <changed files> && uvx ruff check <changed files>` and wrap or
split the offending lines. Never widen the line length or add `noqa` to pass.

- Run everything from the worktree root `/Users/chriphus/OpenSource-Projects/oh-my-clanker.feature-iterm2-tab-title-fish-integration`; never `cd` elsewhere.
- Test runner: `uv run pytest <selector> -v` for single tests; `just check` as the gate at the end of every task; `just build` (ruff format/check + package build) before committing.
- Commit shape:

```bash
git add <files>
git commit -m "<type>: <subject>" -m "<one paragraph body>" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: Private iTerm2 launch probe as a verified fixture precondition

**Model:** heavy coding tier

**Files:**
- Create: `tests/local/__init__.py` (empty)
- Create: `tests/local/private_iterm.py`
- Create: `tests/local/conftest.py`
- Create: `tests/local/test_private_iterm_probe.py`
- Test: `tests/unit/test_private_iterm.py`

**Interfaces:**
- Consumes: `/Applications/iTerm.app` (3.7.3), `iterm2` SDK 2.24 from `.venv`, `/usr/bin/open`, `/usr/bin/pgrep`, `/bin/ps`, `/usr/bin/defaults`, `/usr/bin/ditto`.
- Produces (used by Task 9):
  - `Identity(checkout: Path, suite: str, root: Path, copy: Path, home: Path, socket: Path)` frozen dataclass; `new_identity(checkout: Path, token: str) -> Identity`.
  - `open_argv(identity: Identity) -> list[str]`; `sdk_env(identity: Identity) -> dict[str, str]`.
  - `parse_pids(text: str) -> list[int]`; `instance_pid(commands: dict[int, str], copy: Path) -> int` (raises `ProbeError` unless exactly one); `pids_with_env(ps_output: str, needle: str) -> list[int]`; `server_processes(ps_output: str, identity: Identity) -> list[int]`; `leftover_domains(domains_output: str, suite: str) -> list[str]`.
  - `scoped_env(overrides: Mapping[str, str], *, scrub: tuple[str, ...] = ())` context manager; `SDK_AUTH_VARS = ("ITERM2_COOKIE", "ITERM2_KEY")` (what `connect` scrubs).
  - `class PrivateITerm` with `identity: Identity`, `pid: int`, `classmethod start(checkout: Path) -> PrivateITerm`, `close() -> list[str]` (leftover notes).
  - `async connect(iterm2, instance: PrivateITerm) -> tuple[Connection, App]`; `async ensure_window(iterm2, connection, app, fish: str) -> Window`; `async until(description: str, predicate, *, timeout: float = 30)`; `require_sdk()`; `require_tool(name: str, hint: str) -> str`.
  - `tests/local/conftest.py`: `unmarked_local_items(items, local_dir=LOCAL_DIR) -> list[str]`; session fixture `private_iterm`.

- [ ] **Step 1: Write the failing unit tests for the pure helpers**

Create `tests/unit/test_private_iterm.py`:

```python
"""Pure helpers of the private iTerm2 fixture: argv, parsing, env scoping (no app launched)."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.local.conftest import unmarked_local_items
from tests.local.private_iterm import (
    Identity,
    ProbeError,
    instance_pid,
    leftover_domains,
    new_identity,
    open_argv,
    parse_pids,
    pids_with_env,
    scoped_env,
    sdk_env,
    server_processes,
)

CHECKOUT = Path("/Users/me/omc")


def test_identity_paths_are_short_and_private():
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert identity.suite == "omc-0123456789ab"
    assert identity.root == Path("/private/tmp/omc-0123456789ab")
    assert identity.copy == identity.root / "iTerm.app"
    assert identity.home == identity.root / "home"
    assert identity.socket == (
        identity.home / "Library" / "Application Support" / identity.suite / "private" / "socket"
    )
    assert len(str(identity.socket).encode()) <= 103  # sockaddr_un limit on macOS


def test_identity_refuses_socket_paths_over_sockaddr_un():
    with pytest.raises(ProbeError, match="sockaddr_un"):
        new_identity(CHECKOUT, "x" * 60)


def test_open_argv_is_exact():
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert open_argv(identity) == [
        "/usr/bin/open", "-n", "-g", "-j", "-a", "/private/tmp/omc-0123456789ab/iTerm.app",
        "--env", "HOME=/private/tmp/omc-0123456789ab/home",
        "--env", "XDG_CONFIG_HOME=/private/tmp/omc-0123456789ab/home/.config",
        "--env", "OMC_HOME=/private/tmp/omc-0123456789ab/home/.omc",
        "--env", "PATH=/Users/me/omc/.venv/bin:/usr/bin:/bin",
        "--env", "IT2_SUITE=omc-0123456789ab",
        "--env", "IT2_APP_PATH=/private/tmp/omc-0123456789ab/iTerm.app",
        "--args", "-suite", "omc-0123456789ab", "-EnableAPIServer", "YES",
        "-OpenNoWindowsAtStartup", "YES", "-openNewWindowAtStartup", "NO",
        "-runJobsInServers", "NO", "-SUEnableAutomaticChecks", "NO",
        "-SUHasLaunchedBefore", "YES", "-PromptOnQuit", "NO",
    ]  # fmt: skip


def test_sdk_env_scopes_only_the_three_sdk_variables():
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert sdk_env(identity) == {
        "HOME": "/private/tmp/omc-0123456789ab/home",
        "IT2_SUITE": "omc-0123456789ab",
        "IT2_APP_PATH": "/private/tmp/omc-0123456789ab/iTerm.app",
    }


def test_parse_pids_ignores_blank_lines():
    assert parse_pids("123\n\n 456 \n") == [123, 456]
    assert parse_pids("") == []


def test_instance_pid_needs_exactly_one_process_inside_the_copy():
    copy = Path("/private/tmp/omc-0123456789ab/iTerm.app")
    inside = "/private/tmp/omc-0123456789ab/iTerm.app/Contents/MacOS/iTerm2 -suite omc-0123456789ab"
    outside = "/Applications/iTerm.app/Contents/MacOS/iTerm2 -suite omc-0123456789ab"
    assert instance_pid({7: inside, 8: outside}, copy) == 7
    with pytest.raises(ProbeError, match="no private iTerm2 process"):
        instance_pid({8: outside}, copy)
    with pytest.raises(ProbeError, match="2 processes"):
        instance_pid({7: inside, 9: inside}, copy)


def test_pids_with_env_matches_the_private_home_only():
    output = (
        "10 /bin/fish -i HOME=/private/tmp/omc-0123456789ab/home PATH=/usr/bin\n"
        "11 /bin/zsh HOME=/Users/me PATH=/usr/bin\n"
        "12 /usr/bin/env HOME=/private/tmp/omc-0123456789abcdef/home\n"
    )
    assert pids_with_env(output, "HOME=/private/tmp/omc-0123456789ab/home") == [10]


def test_server_processes_finds_the_private_daemonized_iterm_server():
    identity = new_identity(CHECKOUT, "0123456789ab")
    output = (
        "20 /private/tmp/omc-0123456789ab/iTerm.app/Contents/MacOS/iTerm2 -suite omc-0123456789ab\n"
        "21 /private/tmp/omc-0123456789ab/home/Library/Application Support/omc-0123456789ab/"
        "iTermServer-3.7.3 /private/tmp/omc-0123456789ab/home/Library/Application Support/"
        "omc-0123456789ab/iterm2-daemon-1.socket\n"
        "22 /opt/homebrew/bin/fish -i\n"
        "23 /Users/me/Library/Application Support/iTerm2/iTermServer-3.6.8\n"
    )
    assert server_processes(output, identity) == [21]
    assert server_processes("", identity) == []


def test_leftover_domains_reads_defaults_domains_output():
    output = "com.apple.Terminal, omc-0123456789ab, omc-0123456789ab.private, com.googlecode.iterm2\n"
    assert leftover_domains(output, "omc-0123456789ab") == [
        "omc-0123456789ab",
        "omc-0123456789ab.private",
    ]
    assert leftover_domains("com.apple.Terminal", "omc-0123456789ab") == []


def test_scoped_env_restores_and_unsets(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/me")
    monkeypatch.delenv("IT2_SUITE", raising=False)
    with scoped_env({"HOME": "/private/tmp/x/home", "IT2_SUITE": "omc-x"}):
        assert os.environ["HOME"] == "/private/tmp/x/home"
        assert os.environ["IT2_SUITE"] == "omc-x"
    assert os.environ["HOME"] == "/Users/me"
    assert "IT2_SUITE" not in os.environ


def test_scoped_env_restores_after_an_exception(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/me")
    with pytest.raises(RuntimeError):
        with scoped_env({"HOME": "/elsewhere"}):
            raise RuntimeError("boom")
    assert os.environ["HOME"] == "/Users/me"


def test_scoped_env_scrubs_variables_written_inside_the_block(monkeypatch):
    monkeypatch.delenv("ITERM2_COOKIE", raising=False)
    with scoped_env({"HOME": "/private/tmp/x/home"}, scrub=("ITERM2_COOKIE",)):
        os.environ["ITERM2_COOKIE"] = "issued-by-the-private-instance"
    assert "ITERM2_COOKIE" not in os.environ


def _item(path, nodeid, marked):
    return SimpleNamespace(
        path=path,
        nodeid=nodeid,
        get_closest_marker=lambda name: object() if marked and name == "local_iterm2" else None,
    )


def test_unmarked_local_items_names_only_unmarked_tests_under_tests_local(tmp_path):
    local = tmp_path / "tests" / "local"
    local.mkdir(parents=True)
    items = [
        _item(local / "test_a.py", "tests/local/test_a.py::test_ok", True),
        _item(local / "test_b.py", "tests/local/test_b.py::test_bad", False),
        _item(tmp_path / "tests" / "unit" / "test_c.py", "tests/unit/test_c.py::test_c", False),
    ]
    assert unmarked_local_items(items, local) == ["tests/local/test_b.py::test_bad"]


def test_identity_is_frozen():
    identity = new_identity(CHECKOUT, "0123456789ab")
    with pytest.raises(AttributeError):
        identity.suite = "other"  # type: ignore[misc]
    assert isinstance(identity, Identity)
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `uv run pytest tests/unit/test_private_iterm.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'tests.local.conftest'` (or `.private_iterm`).

- [ ] **Step 3: Create the package marker and the pure helpers**

Create `tests/local/__init__.py` as an empty file.

Create `tests/local/private_iterm.py`:

```python
"""A private iTerm2 instance for the native tier (spec §7, native steps 1–6).

Everything here is test code: it may call subprocess directly. The instance is a
COPY of /Applications/iTerm.app launched under a random `-suite` with a private,
short HOME so the SDK socket fits sockaddr_un. Nothing in this module imports
`iterm2` at module level — Linux CI collects tests/local (deselected by marker).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_APP = Path("/Applications/iTerm.app")
SOCKADDR_UN_MAX = 103


class ProbeError(RuntimeError):
    """A precondition of the private instance did not hold; the message is the setup hint."""


@dataclass(frozen=True)
class Identity:
    checkout: Path
    suite: str
    root: Path
    copy: Path
    home: Path
    socket: Path


def new_identity(checkout: Path, token: str) -> Identity:
    suite = f"omc-{token}"
    root = Path("/private/tmp") / suite
    home = root / "home"
    socket = home / "Library" / "Application Support" / suite / "private" / "socket"
    if len(str(socket).encode()) > SOCKADDR_UN_MAX:
        raise ProbeError(f"socket path {socket} exceeds the sockaddr_un limit; shorten the token")
    return Identity(checkout, suite, root, root / "iTerm.app", home, socket)


def open_argv(identity: Identity) -> list[str]:
    env = {
        "HOME": str(identity.home),
        "XDG_CONFIG_HOME": str(identity.home / ".config"),
        "OMC_HOME": str(identity.home / ".omc"),
        "PATH": f"{identity.checkout / '.venv' / 'bin'}:/usr/bin:/bin",
        "IT2_SUITE": identity.suite,
        "IT2_APP_PATH": str(identity.copy),
    }
    argv = ["/usr/bin/open", "-n", "-g", "-j", "-a", str(identity.copy)]
    for key, value in env.items():
        argv += ["--env", f"{key}={value}"]
    # Argument-domain preferences (NSUserDefaults keys are case-sensitive; advanced settings
    # are camelCase in iTerm2 3.7.3): no windows, no daemonized iTermServer (it would outlive
    # the app), no Sparkle prompts, no quit confirmation.
    argv += [
        "--args", "-suite", identity.suite, "-EnableAPIServer", "YES",
        "-OpenNoWindowsAtStartup", "YES", "-openNewWindowAtStartup", "NO",
        "-runJobsInServers", "NO", "-SUEnableAutomaticChecks", "NO",
        "-SUHasLaunchedBefore", "YES", "-PromptOnQuit", "NO",
    ]  # fmt: skip
    return argv


def sdk_env(identity: Identity) -> dict[str, str]:
    """What the stock SDK reads in the CONNECTING process: socket dir and cookie target."""
    return {"HOME": str(identity.home), "IT2_SUITE": identity.suite, "IT2_APP_PATH": str(identity.copy)}


def parse_pids(text: str) -> list[int]:
    return [int(line) for line in text.split("\n") if line.strip()]


def instance_pid(commands: Mapping[int, str], copy: Path) -> int:
    """Exactly one candidate whose executable lives inside the copied bundle."""
    executable = str(copy / "Contents" / "MacOS" / "iTerm2")
    inside = [pid for pid, command in commands.items() if command.startswith(executable)]
    if not inside:
        raise ProbeError(f"no private iTerm2 process runs from {copy}")
    if len(inside) > 1:
        raise ProbeError(f"{len(inside)} processes run from {copy}: {sorted(inside)}")
    return inside[0]


def pids_with_env(ps_output: str, needle: str) -> list[int]:
    """`ps -E` lines are `<pid> <command> <ENV=...>`; match a whole `KEY=value` token."""
    found = []
    for line in ps_output.split("\n"):
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and needle in parts[1:]:
            found.append(int(parts[0]))
    return found


def server_processes(ps_output: str, identity: Identity) -> list[int]:
    """`ps -axo pid=,command=` lines. iTerm2 copies iTermServer to
    `<HOME>/Library/Application Support/<suite>/iTermServer-<version>` and daemonizes it
    (it re-parents to launchd), so match the private HOME path, never a ppid."""
    support = identity.home / "Library" / "Application Support" / identity.suite
    prefix = str(support / "iTermServer")
    found = []
    for line in ps_output.split("\n"):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith(prefix):
            found.append(int(parts[0]))
    return found


def leftover_domains(domains_output: str, suite: str) -> list[str]:
    present = {name.strip() for name in domains_output.replace("\n", ",").split(",")}
    return [name for name in (suite, f"{suite}.private") if name in present]


SDK_AUTH_VARS = ("ITERM2_COOKIE", "ITERM2_KEY")


@contextlib.contextmanager
def scoped_env(overrides: Mapping[str, str], *, scrub: tuple[str, ...] = ()) -> Iterator[None]:
    """Apply env overrides only around the SDK connect; never process-wide.

    `scrub` names variables the SDK WRITES inside the block (iterm2.auth.authenticate sets
    ITERM2_COOKIE/ITERM2_KEY and Connection.async_create never removes them on success);
    they return to their prior state - normally absent - on exit.
    """
    saved = {key: os.environ.get(key) for key in (*overrides, *scrub)}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def require_tool(name: str, hint: str) -> str:
    found = shutil.which(name)
    if found is None:
        pytest.fail(f"the native tier requires {name}: {hint}")
    return found


def require_sdk():
    if sys.platform != "darwin":
        pytest.fail("the native tier runs only on macOS (just iterm2-tests)")
    try:
        import iterm2
    except ImportError:
        pytest.fail("the native tier requires the iTerm2 Python SDK: uv sync")
    return iterm2


def _ps(*columns: str, pids: list[int] | None = None) -> str:
    argv = ["/bin/ps", "-axo", ",".join(f"{c}=" for c in columns)]
    if pids is not None:
        argv = ["/bin/ps", "-o", ",".join(f"{c}=" for c in columns), "-p", ",".join(map(str, pids))]
    return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False).stdout


def _pgrep(pattern: str) -> list[int]:
    cp = subprocess.run(
        ["/usr/bin/pgrep", "-f", "--", pattern], capture_output=True, text=True, timeout=10, check=False
    )
    return parse_pids(cp.stdout)


def _find_pid(identity: Identity) -> int | None:
    pids = _pgrep(f"-suite {identity.suite}")
    if not pids:
        return None
    commands = {}
    for line in _ps("pid", "command", pids=pids).split("\n"):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            commands[int(parts[0])] = parts[1]
    return instance_pid(commands, identity.copy)


def _wait_for(probe: Callable[[], object], description: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.2)
    raise ProbeError(f"timed out after {timeout:g}s waiting for {description}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)


@dataclass
class PrivateITerm:
    identity: Identity
    pid: int

    @classmethod
    def start(cls, checkout: Path, *, source: Path = SOURCE_APP) -> PrivateITerm:
        if sys.platform != "darwin":
            raise ProbeError("the native tier runs only on macOS")
        if not (source / "Contents" / "MacOS" / "iTerm2").is_file():
            raise ProbeError(f"install iTerm2 at {source}: brew install --cask iterm2")
        identity = new_identity(checkout, secrets.token_hex(6))
        identity.root.mkdir(mode=0o700)
        try:
            (identity.home / ".config" / "fish" / "conf.d").mkdir(parents=True)
            (identity.home / ".omc").mkdir()
            (identity.home / "Library" / "Application Support").mkdir(parents=True)
            # ditto keeps the code signature, bundle id and xattrs, so the existing
            # Automation consent for iTerm2 carries over to the copy (spec §7 step 1).
            subprocess.run(
                ["/usr/bin/ditto", str(source), str(identity.copy)], check=True, timeout=300
            )
            subprocess.run(open_argv(identity), check=True, timeout=60)
            pid = _wait_for(lambda: _find_pid(identity), "the private iTerm2 process", 30)
            _wait_for(lambda: identity.socket.exists() or None, "the private API socket", 60)
        except BaseException:
            cls(identity, 0).close()
            raise
        return cls(identity, pid)

    def matching_pids(self) -> list[int]:
        by_argv = set(_pgrep(f"-suite {self.identity.suite}"))
        by_env = set(pids_with_env(_ps_env(), f"HOME={self.identity.home}"))
        return sorted((by_argv | by_env) - {os.getpid()})

    def close(self) -> list[str]:
        """Spec §7 step 6. Returns leftover notes; empty means a clean teardown."""
        if self.pid:
            _kill(self.pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while _alive(self.pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if _alive(self.pid):
                _kill(self.pid, signal.SIGKILL)
        for pid in server_processes(_ps("pid", "command"), self.identity):
            _kill(pid, signal.SIGKILL)
        for pid in self.matching_pids():
            _kill(pid, signal.SIGKILL)
        # Only after the PID is gone: cfprefsd could otherwise re-flush the domain.
        for domain in (self.identity.suite, f"{self.identity.suite}.private"):
            # macOS writes suite prefs to the REAL ~/Library/Preferences regardless of HOME.
            subprocess.run(
                ["/usr/bin/defaults", "delete", domain], capture_output=True, timeout=10, check=False
            )
        shutil.rmtree(self.identity.root, ignore_errors=True)
        time.sleep(0.2)
        leftovers = []
        if self.identity.root.exists():
            leftovers.append(f"path {self.identity.root}")
        for pid in self.matching_pids():
            leftovers.append(f"pid {pid}")
        leftovers += [
            f"iTermServer {pid} (runJobsInServers ignored)"
            for pid in server_processes(_ps("pid", "command"), self.identity)
        ]
        domains = subprocess.run(
            ["/usr/bin/defaults", "domains"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
        leftovers += [f"domain {name}" for name in leftover_domains(domains, self.identity.suite)]
        return leftovers


def _ps_env() -> str:
    # `-E` appends each (own-user) process's environment to its command line.
    return subprocess.run(
        ["/bin/ps", "-E", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10, check=False
    ).stdout


async def connect(iterm2, instance: PrivateITerm):
    """Stock SDK connect under the scoped env; a TCP fallback is a failure (§7 step 4)."""
    if not instance.identity.socket.exists():
        pytest.fail(
            f"private socket missing at {instance.identity.socket}; the SDK would fall back to "
            "TCP localhost:1912 (the user's iTerm2) — refusing"
        )
    iterm2.app.invalidate_app()  # SDK 2.24 caches App process-wide across closed loops
    with scoped_env(sdk_env(instance.identity), scrub=SDK_AUTH_VARS):
        connection = await asyncio.wait_for(iterm2.Connection.async_create(), 20)
    peer = getattr(connection.websocket, "remote_address", None)
    if isinstance(peer, tuple):
        pytest.fail(f"SDK connected over TCP {peer}, not the private socket — refusing")
    app = await asyncio.wait_for(iterm2.async_get_app(connection), 20)
    if app is None:
        pytest.fail("iTerm2 SDK returned no App for the private instance")
    return connection, app


async def ensure_window(iterm2, connection, app, fish: str):
    """The private instance starts with no window; create one running `fish -i`."""
    if app.terminal_windows:
        return app.terminal_windows[0]
    window = await asyncio.wait_for(
        iterm2.Window.async_create(connection, command=shlex.join([fish, "-i"])), 30
    )
    if window is None:
        pytest.fail("iTerm2 did not create the private window")
    return window


async def until(description: str, predicate, *, timeout: float = 30):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(0.35)
    pytest.fail(f"timed out waiting for {description}")


async def verify_launch(iterm2, instance: PrivateITerm) -> None:
    """Spec §7 step 3: the argument-domain flags took effect."""
    _connection, app = await connect(iterm2, instance)
    if app.terminal_windows:
        raise ProbeError("-OpenNoWindowsAtStartup YES did not take effect: the instance has windows")
```

Create `tests/local/conftest.py`:

```python
"""tests/local: marker guard (spec §7) and the session-scoped private iTerm2 instance."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.local.private_iterm import PrivateITerm, ProbeError, require_sdk, verify_launch

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
    try:
        try:
            asyncio.run(verify_launch(iterm2, instance))
        except ProbeError as exc:
            pytest.fail(f"private iTerm2 launch flags: {exc}")
        yield instance
    finally:
        leftovers = instance.close()
        if leftovers:
            pytest.fail("private iTerm2 teardown left: " + "; ".join(leftovers))
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `uv run pytest tests/unit/test_private_iterm.py -q`
Expected: `14 passed`.

- [ ] **Step 5: Write the native smoke test (spec §7 steps 1–6 as a verified precondition)**

Create `tests/local/test_private_iterm_probe.py`:

```python
"""Native smoke: launch the private iTerm2 copy, connect over its socket, run this checkout's omc."""

import asyncio
import shlex
from pathlib import Path

import pytest

from tests.local.private_iterm import (
    _ps,
    connect,
    require_sdk,
    require_tool,
    server_processes,
    until,
)

pytestmark = pytest.mark.local_iterm2

CHECKOUT = Path(__file__).resolve().parents[2]


def test_probe_tab_runs_this_checkouts_omc(private_iterm):
    iterm2 = require_sdk()
    fish = require_tool("fish", "brew install fish")
    home = private_iterm.identity.home
    out = home / "probe-install-path"
    done = home / "probe-done"

    async def run():
        connection, app = await connect(iterm2, private_iterm)
        window = await asyncio.wait_for(
            iterm2.Window.async_create(connection, command=shlex.join([fish, "-i"])), 30
        )
        assert window is not None, "iTerm2 did not create the private window"
        tab = window.current_tab
        try:
            session = tab.current_session
            await session.async_send_text(
                f"omc print-install-path > {shlex.quote(str(out))}; "
                f"echo ok > {shlex.quote(str(done))}\n"
            )

            async def finished():
                return done.exists()

            await until("the probe tab to run omc", finished, timeout=30)
            await app.async_refresh()
            assert len(app.terminal_windows) == 1
            assert server_processes(_ps("pid", "command"), private_iterm.identity) == [], (
                "runJobsInServers NO did not take effect"
            )
        finally:
            await asyncio.wait_for(tab.async_close(force=True), 10)

    asyncio.run(run())
    # PATH inside the app is <checkout>/.venv/bin:/usr/bin:/bin (spec §7 step 3), so
    # `omc` is this checkout's console script and the package is src/omc.
    assert Path(out.read_text().strip()).resolve() == (CHECKOUT / "src" / "omc").resolve()
    # iTerm2 writes two harmless keys to com.googlecode.iterm2 on launch; the probe
    # records that fact here and never reads or repairs that domain.
```

- [ ] **Step 6: Run the native smoke test**

Run: `just iterm2-tests -k probe`
Expected: `1 passed`; afterwards `ls /private/tmp | grep omc-` shows no `omc-<12 hex>` directory, `defaults domains | tr ',' '\n' | grep omc-` prints nothing, and `pgrep -f -- '-suite omc-'` prints nothing. If the test fails on the cookie request, the message names the AppleScript target; check System Settings → Privacy & Security → Automation allows your terminal to control iTerm2 — do not change product code to work around it.

Also confirm `just check` still passes (the marker guard must be silent for the marked module and the unit test for it green):

Run: `just check`
Expected: all passed, `tests/local/test_private_iterm_probe.py` deselected.

- [ ] **Step 7: Commit**

```bash
git add tests/local/__init__.py tests/local/private_iterm.py tests/local/conftest.py tests/local/test_private_iterm_probe.py tests/unit/test_private_iterm.py
git commit -m "test: launch a private iTerm2 instance for the native tier" -m "Copies /Applications/iTerm.app under /private/tmp/omc-<hex>, launches it with a random -suite and a short private HOME, connects the stock SDK under a scoped env and proves a tab runs this checkout's omc. tests/local/conftest.py fails collection when a module lacks the local_iterm2 marker." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Shared primitives — `python -m omc`, `iterm2_session_id`, `validate_title`, worker `--release`

**Model:** standard coding tier

**Files:**
- Create: `src/omc/__main__.py`
- Modify: `src/omc/terminals.py:45-91`
- Modify: `src/omc/terminal_title.py:17-21`
- Modify: `src/omc/iterm2_title.py`
- Test: `tests/unit/test_terminals.py`, `tests/unit/test_terminal_title.py`, `tests/unit/test_iterm2_title.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Tasks 3, 6, 7):
  - `omc.terminals.iterm2_session_id(env: Mapping[str, str]) -> str | None` — bare UUID from `ITERM_SESSION_ID` (`w<n>t<n>p<n>:<uuid>` or `<uuid>`), `None` when missing or malformed.
  - `omc.terminal_title.validate_title(title: str) -> str | None` — `None` when valid, else the reason `"control characters are not allowed"`.
  - `omc.iterm2_title.worker_argv(session_id: str, *, title: str | None = None, release: bool = False) -> list[str]` — `[sys.executable, "-m", "omc.iterm2_title", "--session-id", <id>, "--release"]` or `[..., "--", <title>]`.
  - `omc.iterm2_title.release_title(connection, session_id: str, sdk) -> None` (async).
  - `python -m omc <args>` behaves exactly like `omc <args>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_terminals.py`:

```python
def test_iterm2_session_id_accepts_bare_and_prefixed_uuid():
    from omc.terminals import iterm2_session_id

    uuid = "38B11221-B7E1-4F36-8A3B-50D549172632"
    assert iterm2_session_id({"ITERM_SESSION_ID": uuid}) == uuid
    assert iterm2_session_id({"ITERM_SESSION_ID": f"w0t1p0:{uuid}"}) == uuid
    assert iterm2_session_id({"ITERM_SESSION_ID": uuid.lower()}) == uuid.lower()


def test_iterm2_session_id_rejects_missing_and_malformed():
    from omc.terminals import iterm2_session_id

    uuid = "38B11221-B7E1-4F36-8A3B-50D549172632"
    for env in (
        {},
        {"ITERM_SESSION_ID": ""},
        {"ITERM_SESSION_ID": "bad"},
        {"ITERM_SESSION_ID": f"unrelated:{uuid}"},
        {"ITERM_SESSION_ID": f"w0t1p0:{uuid}:extra"},
        {"ITERM_SESSION_ID": "38B11221B7E14F368A3B50D549172632"},  # no dashes: not a round trip
        {"ITERM_SESSION_ID": "w0t1p0:"},
    ):
        assert iterm2_session_id(env) is None, env
```

Append to `tests/unit/test_terminal_title.py`:

```python
def test_validate_title_names_control_characters_only():
    from omc.terminal_title import validate_title

    assert validate_title("feature/name") is None
    assert validate_title("") is None  # emptiness is the helper's rule, not this one's
    assert validate_title("bad\007title") == "control characters are not allowed"
    assert validate_title("bad\x9dtitle") == "control characters are not allowed"
    assert validate_title("tab\there") == "control characters are not allowed"
```

Append to `tests/unit/test_iterm2_title.py`:

```python
def test_release_resets_title_to_empty_before_clearing_variable():
    from omc.iterm2_title import release_title

    events = []
    caller = object()

    class Tab:
        async def async_set_variable(self, name, value):
            events.append(("variable", name, value))

        async def async_set_title(self, expression):
            events.append(("title", expression))

    class App:
        def get_session_by_id(self, identity):
            events.append(("lookup", identity))
            return caller

        def get_window_and_tab_for_session(self, session):
            return object(), Tab()

    class SDK:
        async def async_get_app(self, connection):
            return App()

    asyncio.run(release_title(object(), "caller-id", SDK()))
    # "" restores the live default title (SDK docs); the variable is cleared AFTER so a
    # concurrent evaluation of \(user.omc_title) never shows an empty pin.
    assert events == [
        ("lookup", "caller-id"),
        ("title", ""),
        ("variable", "user.omc_title", None),
    ]


def test_worker_argv_forms():
    import sys

    from omc.iterm2_title import worker_argv

    base = [sys.executable, "-m", "omc.iterm2_title", "--session-id", "abc"]
    assert worker_argv("abc", title="feature/x") == [*base, "--", "feature/x"]
    assert worker_argv("abc", release=True) == [*base, "--release"]
    assert worker_argv("abc", title="-weird") == [*base, "--", "-weird"]


def test_main_swallows_sdk_errors_with_the_fixed_stderr_line(monkeypatch, capsys):
    import sys
    import types

    fake = types.ModuleType("iterm2")

    class Connection:
        @staticmethod
        async def async_create():
            raise RuntimeError("cookie=SECRET-COOKIE key=SECRET-KEY")

    fake.Connection = Connection
    monkeypatch.setitem(sys.modules, "iterm2", fake)
    from omc.iterm2_title import main

    assert main(["--session-id", "38B11221-B7E1-4F36-8A3B-50D549172632", "--", "t"]) == 1
    assert main(["--session-id", "38B11221-B7E1-4F36-8A3B-50D549172632", "--release"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "omc: iTerm2 title update failed\nomc: iTerm2 title update failed\n"
    assert "SECRET" not in captured.err


def test_main_requires_exactly_one_of_release_or_title(capsys):
    from omc.iterm2_title import main

    assert main(["--session-id", "abc"]) == 2
    assert main(["--session-id", "abc", "--release", "--", "title"]) == 2
    assert capsys.readouterr().out == ""
```

Append to `tests/unit/test_cli.py`:

```python
def test_python_dash_m_omc_is_the_cli():
    import subprocess
    import sys

    cp = subprocess.run(
        [sys.executable, "-m", "omc", "--version"], capture_output=True, text=True, timeout=30
    )
    assert cp.returncode == 0
    assert cp.stdout.startswith("omc ")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_terminals.py tests/unit/test_terminal_title.py tests/unit/test_iterm2_title.py tests/unit/test_cli.py::test_python_dash_m_omc_is_the_cli -q`
Expected: FAIL — `ImportError: cannot import name 'iterm2_session_id'`, `cannot import name 'validate_title'`, `cannot import name 'release_title'`, and `No module named omc.__main__`.

- [ ] **Step 3: Implement the primitives**

Create `src/omc/__main__.py`:

```python
"""`python -m omc` is `omc`: the fish hook's `omc start` preset and tests use it PATH-free."""

from .cli import main

raise SystemExit(main())
```

Replace `src/omc/terminals.py` lines 45–91 (the `Iterm2Terminal` class) with:

```python
_SESSION_PREFIX = re.compile(r"w\d+t\d+p\d+")


def iterm2_session_id(env: Mapping[str, str]) -> str | None:
    """The bare session UUID from ITERM_SESSION_ID (`w0t1p0:<uuid>` or `<uuid>`), else None.

    UUID round trip plus the optional window/tab/pane prefix: the value names the
    caller's own tab to the API worker, so anything looser is refused.
    """
    raw = env.get("ITERM_SESSION_ID", "")
    prefix, separator, candidate = raw.partition(":")
    if not separator:
        candidate = raw
    elif _SESSION_PREFIX.fullmatch(prefix) is None:
        return None
    try:
        parsed = uuid.UUID(candidate)
    except ValueError:
        return None
    return candidate if str(parsed).lower() == candidate.lower() else None


class Iterm2Terminal(OscTerminal):
    name = "iterm2"

    @classmethod
    def detect(cls, env):
        return env.get("TERM_PROGRAM") == "iTerm.app" or env.get("LC_TERMINAL") == "iTerm2"

    def set_title(self, ctx: ToolContext, title: str) -> bool:
        import sys

        from .iterm2_title import worker_argv

        session_id = iterm2_session_id(ctx.env)
        if session_id is not None:
            try:
                result = ctx.run_bounded(worker_argv(session_id, title=title), timeout=5)
                if result.returncode == 0:
                    return True
            except (TimeoutError, OSError):
                pass
        print(
            "omc: iTerm2 API could not pin this tab title; check Python API authorization "
            "and ITERM_SESSION_ID. Using OSC fallback (cannot pin title).",
            file=sys.stderr,
        )
        super().set_title(ctx, title)
        return False
```

and add `import re` and `import uuid` to the module imports (keep `from collections.abc import Mapping`).

In `src/omc/terminal_title.py` replace `run_title`:

```python
def validate_title(title: str) -> str | None:
    """None when `title` may be sent to a terminal, else why not (C0/C1 control characters)."""
    if any(unicodedata.category(char) == "Cc" for char in title):
        return "control characters are not allowed"
    return None


def run_title(ctx: ToolContext, title: str) -> int:
    reason = validate_title(title)
    if reason is not None:
        print(f"omc: invalid title: {reason}", file=sys.stderr)
        return 1
    return 0 if detect_terminal(ctx.env).set_title(ctx, title) else 1
```

Replace `src/omc/iterm2_title.py` entirely:

```python
"""Short-lived iTerm2 Python API worker for an explicitly identified session.

argv: --session-id <id> (--release | -- <title>). Stdout is never used; SDK errors are
swallowed into one fixed stderr line because they may carry authorization secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

USAGE = "usage: omc.iterm2_title --session-id ID (--release | -- TITLE)"


class TargetNotFound(Exception):
    """The launching session or its tab no longer exists."""


def worker_argv(session_id: str, *, title: str | None = None, release: bool = False) -> list[str]:
    argv = [sys.executable, "-m", "omc.iterm2_title", "--session-id", session_id]
    if release:
        return [*argv, "--release"]
    if title is None:
        raise ValueError("worker_argv needs a title or release=True")
    return [*argv, "--", title]


async def _target_tab(connection: object, session_id: str, sdk: object):
    app = await sdk.async_get_app(connection)
    if app is None:
        raise TargetNotFound
    session = app.get_session_by_id(session_id)
    if session is None:
        raise TargetNotFound
    _window, tab = app.get_window_and_tab_for_session(session)
    if tab is None:
        raise TargetNotFound
    return tab


async def set_title(connection: object, session_id: str, title: str, sdk: object) -> None:
    tab = await _target_tab(connection, session_id, sdk)
    await tab.async_set_variable("user.omc_title", title)
    await tab.async_set_title(r"\(user.omc_title)")


async def release_title(connection: object, session_id: str, sdk: object) -> None:
    tab = await _target_tab(connection, session_id, sdk)
    # "" restores iTerm2's live default title (SDK: Tab.async_set_title). Clear the
    # variable afterwards: the SDK sends json "null", which iTerm2's API handler maps to
    # nil and iTermVariables removes the key (verified against iTermAPIHelper.m).
    await tab.async_set_title("")
    await tab.async_set_variable("user.omc_title", None)


async def _run(session_id: str, title: str | None, release: bool) -> None:
    import iterm2  # lazy: macOS-only dependency, isolated from other terminals

    connection = await iterm2.Connection.async_create()
    if release:
        await release_title(connection, session_id, iterm2)
    else:
        await set_title(connection, session_id, title, iterm2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set or release an explicit iTerm2 tab title")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("title", nargs="?")
    args = parser.parse_args(argv)
    if args.release == (args.title is not None):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(args.session_id, args.title, args.release))
    except Exception:  # noqa: BLE001 - SDK errors may contain authorization secrets
        print("omc: iTerm2 title update failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_terminals.py tests/unit/test_terminal_title.py tests/unit/test_iterm2_title.py tests/unit/test_cli.py tests/unit/test_start.py -q`
Expected: all passed (the pre-existing `test_iterm_adapter_invokes_exact_worker_and_falls_back` still sees the identical argv).

- [ ] **Step 5: Gate and commit**

Run: `just check` then `just build`
Expected: both green.

`uv.lock` rides along (the lock's own-package version is stale at 0.1.9 vs pyproject 0.1.11; every `uv run` rewrites it, so it rides with the first product commit).

```bash
git add uv.lock src/omc/__main__.py src/omc/terminals.py src/omc/terminal_title.py src/omc/iterm2_title.py tests/unit/test_terminals.py tests/unit/test_terminal_title.py tests/unit/test_iterm2_title.py tests/unit/test_cli.py
git commit -m "refactor: share the iTerm2 session id and title validation, add worker --release" -m "iterm2_session_id and validate_title become module-level so the title helper and the start-time adapter use one rule each; the SDK worker learns --release (async_set_title(\"\") then clear user.omc_title) and python -m omc runs the CLI." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `omc title set|release|apply` (`src/omc/title.py`)

**Model:** heavy coding tier

**Files:**
- Create: `src/omc/title.py`
- Modify: `src/omc/cli/__init__.py:126-133` (parser), `:176` (banner tuple), `:181` (dispatch)
- Test: `tests/unit/test_title.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `omc.terminals.iterm2_session_id`, `omc.terminal_title.validate_title`, `omc.iterm2_title.worker_argv` (Task 2); `ToolContext.run_bounded(argv, *, timeout, cwd=None, extra_env=None)`; `omc.errors.Refusal`/`OmcError`.
- Produces (used by Tasks 4, 9, README):
  - CLI: `omc title set -- <title>`, `omc title release`, `omc title apply <request file>`; banner-exempt; configuration-independent; exit 0/1/2 as spec §3.
  - `omc.title.WORKER_TIMEOUT = 5.0`, `COOLDOWN_SECONDS = 60.0`.
  - `omc.title.failure_marker(home: Path, session_id: str) -> Path` (= `home/"title-failed"/session_id`), `lock_path(home, session_id) -> Path` (= `home/"title-lock"/session_id`).
  - `omc.title.parse_request(text: str) -> tuple[str, str | None]` — `("set", title)` or `("release", None)`; raises `Refusal`.
  - `omc.title.run_title_command(ctx: ToolContext, args: argparse.Namespace) -> int`.
  - Marker file: line 1 `omc: iTerm2 tab title update failed: <reason> (retry: omc title set -- <branch>)`, line 2 `at <timestamp>`; the hook prints line 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_title.py`:

```python
"""`omc title`: quiet, config-free, exit codes 0/1/2, marker cooldown, serialized apply."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from omc.cli import main
from omc.toolctx import ToolContext

UUID = "38B11221-B7E1-4F36-8A3B-50D549172632"
WORKER = [sys.executable, "-m", "omc.iterm2_title", "--session-id", UUID]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "omc home"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ITERM_SESSION_ID", f"w0t1p0:{UUID}")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    return home


@pytest.fixture
def worker(monkeypatch):
    """Fake SDK worker: records (argv, timeout); driven by `worker.rc` / `worker.raises`."""
    state = type("Worker", (), {"calls": [], "rc": 0, "raises": None})()

    def fake(self, argv, *, timeout, cwd=None, extra_env=None):
        state.calls.append((list(argv), timeout))
        if state.raises is not None:
            raise state.raises
        return subprocess.CompletedProcess(list(argv), state.rc, "", "secret-auth-value")

    monkeypatch.setattr(ToolContext, "run_bounded", fake)
    return state


def _marker(home):
    return home / "title-failed" / UUID


def test_set_runs_exact_worker_quietly(home, worker, capsys):
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert worker.calls == [([*WORKER, "--", "feature/name"], 5.0)]
    assert capsys.readouterr() == ("", "")  # no banner, no stdout, nothing on success
    assert not _marker(home).exists()


def test_set_passes_hostile_title_literally(home, worker):
    hostile = "feature/cost$USD(parent)\\(session.name); rm -rf"
    assert main(["title", "set", "--", hostile]) == 0
    assert worker.calls[0][0][-1] == hostile


def test_release_runs_exact_worker_quietly(home, worker, capsys):
    assert main(["title", "release"]) == 0
    assert worker.calls == [([*WORKER, "--release"], 5.0)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("title", ["bad\007title", "bad\x9dtitle", ""])
def test_bad_titles_are_refused_before_any_worker(home, worker, capsys, title):
    assert main(["title", "set", "--", title]) == 2
    assert worker.calls == []
    err = capsys.readouterr().err
    assert err.startswith("error: omc title:") and "Oh My Clanker" not in err
    assert not _marker(home).exists()


@pytest.mark.parametrize("session", [None, "", "bad", f"unrelated:{UUID}"])
def test_missing_or_malformed_session_is_refused(home, worker, capsys, monkeypatch, session):
    if session is None:
        monkeypatch.delenv("ITERM_SESSION_ID")
    else:
        monkeypatch.setenv("ITERM_SESSION_ID", session)
    assert main(["title", "set", "--", "feature/name"]) == 2
    assert main(["title", "release"]) == 2
    assert worker.calls == []
    err = capsys.readouterr().err
    assert "ITERM_SESSION_ID" in err and "Traceback" not in err


def test_worker_failure_writes_marker_and_one_line(home, worker, capsys):
    worker.rc = 3
    assert main(["title", "set", "--", "feature/name"]) == 1
    marker = _marker(home)
    lines = marker.read_text().splitlines()
    assert len(lines) == 2
    assert lines[0] == (
        "omc: iTerm2 tab title update failed: worker exit 3 (retry: omc title set -- <branch>)"
    )
    assert lines[1].startswith("at 20")  # human-readable timestamp, never parsed
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == lines[0] + "\n"
    assert "secret" not in captured.err  # the worker's stderr is never surfaced


def test_worker_timeout_writes_marker(home, worker, capsys):
    worker.raises = TimeoutError("command timed out after 5s")
    assert main(["title", "release"]) == 1
    assert "timed out after 5s" in _marker(home).read_text().splitlines()[0]
    assert capsys.readouterr().err.count("\n") == 1


def test_fresh_marker_throttles_silently_without_spawning(home, worker, capsys):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("omc: iTerm2 tab title update failed: worker exit 1 (retry: ...)\nat now\n")
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert worker.calls == []
    assert capsys.readouterr() == ("", "")
    assert marker.exists()  # untouched: the hook judges retries by mtime


def test_stale_marker_retries_and_success_removes_it(home, worker):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("old\nat then\n")
    old = time.time() - 61
    os.utime(marker, (old, old))
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert len(worker.calls) == 1
    assert not marker.exists()


def test_future_marker_mtime_never_throttles(home, worker):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("skewed\nat later\n")
    future = time.time() + 3600
    os.utime(marker, (future, future))
    assert main(["title", "set", "--", "feature/name"]) == 0
    assert len(worker.calls) == 1
    assert not marker.exists()


def test_unwritable_home_reports_one_line_and_exits_one(home, worker, capsys):
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_text("a file where the omc home should be\n")
    worker.rc = 1
    assert main(["title", "set", "--", "feature/name"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "worker exit 1" in captured.err and "marker not written" in captured.err
    assert "Traceback" not in captured.err


def _request(home, text):
    path = home / "title-request" / f"{UUID}-4242"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_apply_performs_request_and_records_applied(home, worker, capsys):
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert worker.calls == [([*WORKER, "--", "feature/x"], 5.0)]
    assert Path(str(request) + ".applied").read_text() == "set feature/x\n"
    assert (home / "title-lock" / UUID).exists()
    assert capsys.readouterr() == ("", "")


def test_apply_release_request(home, worker):
    request = _request(home, "release\n")
    assert main(["title", "apply", str(request)]) == 0
    assert worker.calls == [([*WORKER, "--release"], 5.0)]


def test_two_helpers_on_one_request_perform_one_write(home, worker):
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert main(["title", "apply", str(request)]) == 0
    assert len(worker.calls) == 1


def test_request_rewritten_before_lock_is_applied_by_holder_not_newcomer(home, worker, monkeypatch):
    import fcntl

    import omc.title as title_mod

    request = _request(home, "set first\n")
    real_flock = fcntl.flock
    rewritten = []

    def flock_then_rewrite(fh, op):
        real_flock(fh, op)
        if not rewritten:  # the holder was launched for "first"; "second" lands before it re-reads
            request.write_text("set second\n")
            rewritten.append(True)

    monkeypatch.setattr(title_mod.fcntl, "flock", flock_then_rewrite)
    assert main(["title", "apply", str(request)]) == 0  # holder
    assert main(["title", "apply", str(request)]) == 0  # newcomer launched for "second"
    assert worker.calls == [([*WORKER, "--", "second"], 5.0)]
    assert Path(str(request) + ".applied").read_text() == "set second\n"


def test_lock_is_held_across_the_worker(home, worker, monkeypatch):
    request = _request(home, "set feature/x\n")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking(self, argv, *, timeout, cwd=None, extra_env=None):
        calls.append(list(argv))
        entered.set()
        assert release.wait(10)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    monkeypatch.setattr(ToolContext, "run_bounded", blocking)
    results = {}
    holder = threading.Thread(
        target=lambda: results.update(holder=main(["title", "apply", str(request)]))
    )
    holder.start()
    assert entered.wait(10)
    newcomer = threading.Thread(
        target=lambda: results.update(newcomer=main(["title", "apply", str(request)]))
    )
    newcomer.start()
    time.sleep(0.5)
    assert newcomer.is_alive() and len(calls) == 1  # blocked on the lock while the worker runs
    release.set()
    holder.join(10)
    newcomer.join(10)
    assert results == {"holder": 0, "newcomer": 0}
    assert len(calls) == 1  # the newcomer found .applied == request and wrote nothing


def test_apply_in_cooldown_does_not_record_applied(home, worker):
    marker = _marker(home)
    marker.parent.mkdir(parents=True)
    marker.write_text("fresh\nat now\n")
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 0
    assert worker.calls == []
    assert not Path(str(request) + ".applied").exists()  # a later retry must re-apply


def test_apply_failure_does_not_record_applied(home, worker):
    worker.rc = 1
    request = _request(home, "set feature/x\n")
    assert main(["title", "apply", str(request)]) == 1
    assert not Path(str(request) + ".applied").exists()
    assert _marker(home).exists()


@pytest.mark.parametrize("text", ["", "set \n", "set\n", "pin feature/x\n", "set bad\007\n", "\n"])
def test_apply_refuses_malformed_request_without_marker(home, worker, capsys, text):
    request = _request(home, text)
    assert main(["title", "apply", str(request)]) == 2
    assert worker.calls == []
    assert not _marker(home).exists()
    err = capsys.readouterr().err
    assert err.startswith("error: omc title:") and "Traceback" not in err


def test_apply_refuses_missing_request_file(home, worker, capsys):
    assert main(["title", "apply", str(home / "nope")]) == 2
    assert worker.calls == []
    assert "request file" in capsys.readouterr().err


def test_parse_request_contract():
    from omc.errors import Refusal
    from omc.title import parse_request

    assert parse_request("set feature/x\n") == ("set", "feature/x")
    assert parse_request("set a b\nignored second line\n") == ("set", "a b")
    assert parse_request("release\n") == ("release", None)
    with pytest.raises(Refusal):
        parse_request("release please\n")


def test_title_never_loads_configuration(home, worker, monkeypatch):
    import omc.config.resolve as resolve

    monkeypatch.setattr(
        resolve, "load_effective", lambda ctx: pytest.fail("omc title loaded configuration")
    )
    assert main(["title", "set", "--", "feature/name"]) == 0
```

Append to `tests/unit/test_cli.py`:

```python
def test_title_and_shell_integration_are_banner_exempt():
    import inspect

    from omc.cli import _run  # the banner tuple lives in _run; both names must be exempt

    source = inspect.getsource(_run)
    assert '"title"' in source and '"shell-integration"' in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_title.py tests/unit/test_cli.py -q`
Expected: FAIL — `main(["title", ...])` raises `SystemExit(2)` out of `cli.main` (argparse `invalid choice: 'title'` is not an `OmcError`, so nothing catches it); pytest reports each `test_title.py` test as failed on that `SystemExit`, and `test_title_and_shell_integration_are_banner_exempt` fails on the source check.

- [ ] **Step 3: Implement `src/omc/title.py`**

```python
"""`omc title set|release|apply`: quiet helpers that pin or release the CALLER's iTerm2 tab.

The fish hook (assets/omc-title.fish) dispatches `apply <request file>` fire-and-forget;
`set` and `release` are the manual, foreground forms. Contract (spec §3): no stdout ever;
nothing on success; one omc-authored stderr line on failure; exit 0 ok, 1 failed or
timed-out API write, 2 refusal for bad input. Never calls detect_terminal, never emits OSC.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path

from .errors import OmcError, Refusal
from .iterm2_title import worker_argv
from .terminal_title import validate_title
from .terminals import iterm2_session_id
from .toolctx import ToolContext

WORKER_TIMEOUT = 5.0
COOLDOWN_SECONDS = 60.0
RETRY_HINT = "retry: omc title set -- <branch>"

APPLIED = "applied"
FAILED = "failed"
THROTTLED = "throttled"


def failure_marker(home: Path, session_id: str) -> Path:
    return home / "title-failed" / session_id


def lock_path(home: Path, session_id: str) -> Path:
    return home / "title-lock" / session_id


def _title_problem(title: str) -> str | None:
    if title == "":
        return "empty title"
    return validate_title(title)


def parse_request(text: str) -> tuple[str, str | None]:
    """First line of a request file -> ("set", title) | ("release", None); Refusal otherwise."""
    line = text.split("\n", 1)[0]
    if line == "release":
        return "release", None
    if line.startswith("set "):
        title = line[4:]
        reason = _title_problem(title)
        if reason is None:
            return "set", title
        raise Refusal(f"omc title: request file: {reason}")
    raise Refusal("omc title: request file holds neither `set <title>` nor `release`")


def _session(ctx: ToolContext) -> str:
    session_id = iterm2_session_id(ctx.env)
    if session_id is None:
        raise Refusal(
            "omc title: ITERM_SESSION_ID is missing or malformed (run this inside an iTerm2 tab)"
        )
    return session_id


def _write_atomic(path: Path, text: str) -> None:
    """Temp sibling + os.replace, as dependency.py and awscreds.py do."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _throttled(marker: Path, now: float) -> bool:
    try:
        age = now - marker.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < COOLDOWN_SECONDS  # a future mtime (clock skew) never throttles


def _fail(marker: Path, reason: str) -> str:
    line = f"omc: iTerm2 tab title update failed: {reason} ({RETRY_HINT})"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    try:
        _write_atomic(marker, f"{line}\nat {stamp}\n")
    except OSError as exc:
        line += f"; marker not written ({type(exc).__name__})"
    print(line, file=sys.stderr)
    return FAILED


def _perform(ctx: ToolContext, session_id: str, action: str, title: str | None) -> str:
    marker = failure_marker(ctx.home, session_id)
    if _throttled(marker, time.time()):
        return THROTTLED  # bounds background work AND the AppleScript cookie prompt to 1/min
    argv = worker_argv(session_id, title=title, release=(action == "release"))
    try:
        cp = ctx.run_bounded(argv, timeout=WORKER_TIMEOUT)
    except TimeoutError:
        return _fail(marker, f"timed out after {WORKER_TIMEOUT:g}s")
    except OSError as exc:
        return _fail(marker, f"worker did not start ({type(exc).__name__})")
    if cp.returncode != 0:
        # The worker's own stderr is never surfaced: it may carry authorization secrets.
        return _fail(marker, f"worker exit {cp.returncode}")
    with contextlib.suppress(OSError):
        marker.unlink()
    return APPLIED


def run_title_set(ctx: ToolContext, title: str) -> int:
    reason = _title_problem(title)
    if reason is not None:
        raise Refusal(f"omc title: {reason}")
    return 1 if _perform(ctx, _session(ctx), "set", title) == FAILED else 0


def run_title_release(ctx: ToolContext) -> int:
    return 1 if _perform(ctx, _session(ctx), "release", None) == FAILED else 0


def run_title_apply(ctx: ToolContext, request_file: str) -> int:
    session_id = _session(ctx)
    request = Path(request_file)
    applied = request.with_name(request.name + ".applied")
    lock = lock_path(ctx.home, session_id)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = lock.open("a+b")
    except OSError as exc:
        raise OmcError(f"omc title: cannot open lock {lock} ({type(exc).__name__})") from exc
    with handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # held across the worker; its deadline bounds this
        try:
            text = request.read_text()  # re-read AFTER the lock: the newest request wins
        except OSError as exc:
            raise Refusal(
                f"omc title: cannot read request file {request} ({type(exc).__name__})"
            ) from exc
        try:
            if applied.read_text() == text:
                return 0  # an earlier helper already applied this exact request
        except OSError:
            pass
        action, title = parse_request(text)
        outcome = _perform(ctx, session_id, action, title)
        if outcome == APPLIED:
            try:
                _write_atomic(applied, text)
            except OSError as exc:
                raise OmcError(
                    f"omc title: cannot record {applied} ({type(exc).__name__})"
                ) from exc
        return 1 if outcome == FAILED else 0


def run_title_command(ctx: ToolContext, args: argparse.Namespace) -> int:
    if args.title_command == "set":
        return run_title_set(ctx, args.title)
    if args.title_command == "release":
        return run_title_release(ctx)
    return run_title_apply(ctx, args.request_file)
```

- [ ] **Step 4: Wire the CLI**

In `src/omc/cli/__init__.py`, before `p_install = sub.add_parser("install", ...)` add:

```python
    p_title = sub.add_parser(
        "title", help="Pin or release this iTerm2 tab's title (quiet; used by the fish hook)"
    )
    title_sub = p_title.add_subparsers(dest="title_command", required=True)
    p_title_set = title_sub.add_parser(
        "set", help="Pin the caller's tab title: omc title set -- <title>"
    )
    p_title_set.add_argument("title")
    title_sub.add_parser("release", help="Restore iTerm2's live default title for the caller's tab")
    p_title_apply = title_sub.add_parser(
        "apply", help="Apply a hook-written request file (serialized)"
    )
    p_title_apply.add_argument("request_file")
```

Replace the banner condition in `_run`:

```python
    # version/print-install-path: stdout is a one-line machine contract.
    # aws-credential-process: stdout is the credential_process JSON contract, and it
    # must work on a machine that never ran `omc configure`.
    # title/shell-integration: quiet helpers driven by the fish hook and the installer.
    if args.command not in (
        "version",
        "print-install-path",
        "aws-credential-process",
        "title",
        "shell-integration",
    ):
        print(f"Oh My Clanker! v{__version__}", file=sys.stderr)
```

At the top of `_dispatch` (before the `version` branch) add:

```python
    if args.command == "title":
        from ..title import run_title_command  # lazy, never loads configuration

        return run_title_command(ctx, args)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_title.py tests/unit/test_cli.py -q`
Expected: all passed (`test_lock_is_held_across_the_worker` takes ~0.6 s).

- [ ] **Step 6: Gate and commit**

Run: `just check` then `just build`
Expected: green.

```bash
git add src/omc/title.py src/omc/cli/__init__.py tests/unit/test_title.py tests/unit/test_cli.py
git commit -m "feat: add omc title set|release|apply for the caller's iTerm2 tab" -m "Quiet, configuration-free helper: set/release run the SDK worker under run_bounded (5s); apply takes a per-session flock, re-reads the hook's request file, dedupes against .applied and performs it. Failures write an mtime-judged marker (60s cooldown) and one stderr line; bad input is a Refusal (exit 2)." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The fish hook `src/omc/assets/omc-title.fish` and its real-fish tests

**Model:** heavy coding tier

**Files:**
- Create: `src/omc/assets/omc-title.fish`
- Create: `tests/unit/_fishpty.py`
- Test: `tests/unit/test_fish_hook.py`

**Interfaces:**
- Consumes: `omc title apply <file>` (Task 3) — the hook only ever runs `$helper title apply <request path>`; `git symbolic-ref --quiet HEAD` exits 0 on a branch (prints `refs/heads/<name>`), 1 detached, 128 outside a repository; `git rev-parse --absolute-git-dir` for the worktree key.
- Produces (used by Tasks 5, 7, 8, 9):
  - The asset with first line `# omc-managed fish title integration v2`.
  - fish functions `__omc_title_refresh` (called explicitly by the `omc start` `-C` command), `__omc_title_dispatch`, `__omc_title_remember`, handlers `__omc_title_prompt`, `__omc_title_preexec`, `__omc_title_pwd`.
  - Configuration variable `__omc_title_helper` (list; preserved across re-sourcing; when empty the hook resolves `command -s omc` at refresh time).
  - State variables reset on every sourcing: `__omc_title_last_request`, `__omc_title_keys`, `__omc_title_branches`, `__omc_title_hinted`, `__omc_title_marker_seen`, `__omc_title_started`; derived `__omc_title_uuid`, `__omc_title_home`.
  - Request file `<omc home>/title-request/<uuid>-<fish pid>` holding one line `set <branch>` or `release`.
  - `tests/unit/_fishpty.drive_fish(fish: str, env: dict[str, str], commands: list[str], prompt_log: Path, *, cwd: Path | None = None, timeout: float = 20) -> str` — types each command at a real prompt (prompts counted through `OMC_PROMPT_LOG`), returns the transcript.
  - Stderr hint texts (exact): `omc: fish title hook found no `omc` on PATH; `omc shell-integration fish disable` removes the hook`.

Spec refinement recorded here: §2 says the worktree key is computed "only in the detached case", but the remembered-branch map needs the key when a branch is *recorded*. The hook runs `git rev-parse --absolute-git-dir` in the detached case and on a **branch change** only; an unchanged branch costs exactly one `git symbolic-ref` per prompt.

- [ ] **Step 1: Write the PTY prompt driver (reused by Task 8)**

Create `tests/unit/_fishpty.py` (adapted from the backup's `_interactive`):

```python
"""Type commands at a real `fish -i` prompt under a PTY; prompts are counted via OMC_PROMPT_LOG."""

from __future__ import annotations

import os
import pty
import select
import signal
import time
from pathlib import Path


def drive_fish(
    fish: str,
    env: dict[str, str],
    commands: list[str],
    prompt_log: Path,
    *,
    cwd: Path | None = None,
    timeout: float = 20,
) -> str:
    """env must make fish_prompt append one line to $OMC_PROMPT_LOG per prompt."""
    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - child
        if cwd is not None:
            os.chdir(cwd)
        os.execve(fish, [fish, "-i"], env)
    transcript = bytearray()

    def prompts() -> int:
        return len(prompt_log.read_text().splitlines()) if prompt_log.exists() else 0

    try:
        expected = 1
        for command in [*commands, "exit"]:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and prompts() < expected:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        transcript.extend(os.read(master, 65536))
                    except OSError:
                        break
            assert prompts() >= expected, (
                f"prompt {expected} never came:\n{transcript.decode(errors='replace')[-800:]}"
            )
            os.write(master, (command + "\n").encode())
            expected += 1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG)[0]:
                pid = 0
                break
            if select.select([master], [], [], 0.05)[0]:
                try:
                    transcript.extend(os.read(master, 65536))
                except OSError:
                    pass
        assert pid == 0, f"fish did not exit:\n{transcript.decode(errors='replace')[-800:]}"
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not os.waitpid(pid, os.WNOHANG)[0]:
                time.sleep(0.02)
        os.close(master)
    return transcript.decode(errors="replace")
```

- [ ] **Step 2: Write the failing hook tests**

Create `tests/unit/test_fish_hook.py`:

```python
"""The packaged hook in a real fish: restricted PATH, stub `omc`, scrubbed iTerm2 env."""

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ._fishpty import drive_fish

HOOK = Path(__file__).resolve().parents[2] / "src" / "omc" / "assets" / "omc-title.fish"
SESSION = "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632"
UUID = "38B11221-B7E1-4F36-8A3B-50D549172632"
# Scrubbed so `just check` inside iTerm2 never sees the developer's tab (spec §7).
SCRUB = (
    "ITERM_SESSION_ID", "TERM_PROGRAM", "LC_TERMINAL", "TMUX", "STY",
    "OMC_FISH_TITLE_DISABLE", "OMC_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "HOME",
)  # fmt: skip
HINT_NO_OMC = (
    "omc: fish title hook found no `omc` on PATH; "
    "`omc shell-integration fish disable` removes the hook"
)
# Stubs run on a PATH holding only the stub dir: builtins and redirections only.
STUB_OMC = """#!/bin/sh
printf '%s\\n' "$*" >> "$OMC_TEST_LOG"
if [ "$1" = title ] && [ "$2" = apply ] && [ -r "$3" ]; then
  IFS= read -r line < "$3"
  printf 'request: %s\\n' "$line" >> "$OMC_TEST_LOG"
fi
exit 0
"""
STUB_HELPER = """#!/bin/sh
printf 'via-helper %s\\n' "$*" >> "$OMC_TEST_LOG"
exit 0
"""
# In-fish bounded wait: the helper is disowned and the pipe session ends with `exit`.
WAIT_FN = """function __wait_log --argument-names pattern count
    set -l tries 0
    while test $tries -lt 200
        if test -e "$OMC_TEST_LOG"
            if test (count (string match -r -- $pattern <$OMC_TEST_LOG)) -ge $count
                return 0
            end
        end
        set tries (math $tries + 1)
        /bin/sleep 0.05
    end
    echo "__wait_log: $pattern x$count not reached" >&2
    return 1
end
"""


def _tool(name, hint):
    found = shutil.which(name)
    if found is None:
        pytest.fail(f"{name} is required for fish hook tests: {hint}")
    return found


def _init_repo(path, branch):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    git = ["git", "-C", str(path)]
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run([*git, "config", key, value], check=True)
    (path / "file").write_text("initial\n")
    subprocess.run([*git, "add", "file"], check=True)
    subprocess.run([*git, "commit", "-qm", "initial"], check=True)
    return path


class HookShell:
    def __init__(self, tmp_path):
        self.fish = _tool("fish", "brew install fish (macOS) or sudo apt-get install fish (Ubuntu)")
        git = _tool("git", "install git")
        self.tmp = tmp_path
        self.repo = _init_repo(tmp_path / "repo's space", "feature/first")
        self.stubs = tmp_path / "stubs"
        self.stubs.mkdir()
        (self.stubs / "omc").write_text(STUB_OMC)
        (self.stubs / "omc").chmod(0o755)
        (self.stubs / "git").symlink_to(git)  # the hook runs `command git`
        self.config = tmp_path / "config"
        confd = self.config / "fish" / "conf.d"
        confd.mkdir(parents=True)
        self.hook = confd / "omc-title.fish"
        shutil.copyfile(HOOK, self.hook)
        self.user_config = self.config / "fish" / "config.fish"
        self.user_config.write_text(
            'echo USER_CONFIG >> "$OMC_TEST_LOG"\nfunction fish_title; echo USER_TITLE; end\n'
        )
        self.helpers = tmp_path / "helpers.fish"
        self.helpers.write_text(WAIT_FN)
        self.omc_home = tmp_path / "omc home"
        self.log = tmp_path / "omc.log"
        (tmp_path / "home").mkdir()
        # fish's own interactive init runs a bare `mkdir -p …/fish/generated_completions`
        # (fish 4: $XDG_CACHE_HOME, fish 3.7: $XDG_DATA_HOME) unless it already exists; on the
        # restricted PATH that is "fish: Unknown command: mkdir" on stderr. Pre-create both.
        for base in ("cache", "data"):
            (tmp_path / base / "fish" / "generated_completions").mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        self.env.update(
            HOME=str(tmp_path / "home"),
            PATH=str(self.stubs),
            XDG_CONFIG_HOME=str(self.config),
            XDG_DATA_HOME=str(tmp_path / "data"),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            OMC_HOME=str(self.omc_home),
            OMC_TEST_LOG=str(self.log),
            ITERM_SESSION_ID=SESSION,
            TERM_PROGRAM="iTerm.app",
        )

    def run(self, script, *, env=None, cwd=None, timeout=25):
        merged = {**self.env, **(env or {})}
        merged = {k: v for k, v in merged.items() if v is not None}
        body = f"source {shlex.quote(str(self.helpers))}; {script}; exit"
        return subprocess.run(
            [self.fish, "-i", "-C", body],
            cwd=cwd or self.repo,
            env=merged,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def requests(self):
        return [line.removeprefix("request: ") for line in self.lines() if line.startswith("request: ")]

    def applies(self):
        return [line for line in self.lines() if line.startswith("title apply ")]


@pytest.fixture
def sh(tmp_path):
    return HookShell(tmp_path)


def test_branch_detached_outside_branch_yields_set_nothing_release_set(sh):
    proc = sh.run(
        "emit fish_prompt; __wait_log '^request:' 1; "
        "git switch --detach -q; emit fish_prompt; "
        "cd /; emit fish_prompt; __wait_log '^request:' 2; "
        f"cd {shlex.quote(str(sh.repo))}; emit fish_prompt; __wait_log '^request:' 3"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert sh.lines()[0] == "USER_CONFIG"
    assert sh.requests() == ["set feature/first", "release", "set feature/first"]
    assert len(sh.applies()) == 3
    files = list((sh.omc_home / "title-request").iterdir())
    assert len(files) == 1 and files[0].name.startswith(f"{UUID}-")
    assert files[0].read_text() == "set feature/first\n"


def test_detached_start_without_known_branch_releases_then_pins(sh):
    subprocess.run(["git", "-C", str(sh.repo), "switch", "--detach", "-q"], check=True)
    proc = sh.run(
        "emit fish_prompt; __wait_log '^request:' 1; "
        "git switch -q feature/first; emit fish_prompt; __wait_log '^request:' 2"
    )
    assert proc.returncode == 0, proc.stderr
    assert sh.requests() == ["release", "set feature/first"]


def test_rapid_branch_changes_keep_one_request_file_holding_the_latest(sh):
    proc = sh.run(
        "emit fish_prompt; git switch -qc feature/second; emit fish_prompt; "
        "git switch -qc feature/third; emit fish_prompt; __wait_log 'title apply' 3"
    )
    assert proc.returncode == 0, proc.stderr
    applies = sh.applies()
    assert len(applies) == 3
    paths = {line.split(" ", 2)[2] for line in applies}
    assert len(paths) == 1  # the same <uuid>-<pid> file every time
    request = Path(paths.pop())
    assert request.parent == sh.omc_home / "title-request"
    assert request.name.startswith(f"{UUID}-")
    assert request.read_text() == "set feature/third\n"


def test_special_characters_stay_literal(sh):
    proc = sh.run(
        "git switch -qc 'feature/cost$USD(parent)'; emit fish_prompt; __wait_log '^request:' 1"
    )
    assert proc.returncode == 0, proc.stderr
    assert sh.requests() == ["set feature/cost$USD(parent)"]


def test_directory_change_to_another_repository_pins_its_branch(sh):
    other = _init_repo(sh.tmp / "other repo", "feature/elsewhere")
    proc = sh.run(
        "emit fish_prompt; __wait_log '^request:' 1; "
        f"cd {shlex.quote(str(other))}; __wait_log '^request:' 2"
    )
    assert proc.returncode == 0, proc.stderr
    assert sh.requests() == ["set feature/first", "set feature/elsewhere"]


def test_user_fish_title_and_config_untouched_one_handler_after_resourcing(sh):
    before = sh.user_config.read_bytes()
    hook = shlex.quote(str(sh.hook))
    proc = sh.run(
        f"source {hook}; source {hook}; emit fish_prompt; __wait_log '^request:' 1; "
        "emit fish_prompt; fish_title; functions --handlers"
    )
    assert proc.returncode == 0, proc.stderr
    assert sh.requests() == ["set feature/first"]  # one dispatch despite three sourcings
    assert "USER_TITLE" in proc.stdout
    assert sh.user_config.read_bytes() == before
    for handler in ("fish_prompt __omc_title_prompt", "fish_preexec __omc_title_preexec", "PWD __omc_title_pwd"):
        assert proc.stdout.count(handler) == 1, proc.stdout
    assert "fish_title" not in HOOK.read_text()  # the hook never defines or replaces fish_title


@pytest.mark.parametrize(
    "env",
    [
        {"TERM_PROGRAM": None, "LC_TERMINAL": None},
        {"TMUX": "/tmp/tmux-501/default,1,0"},
        {"STY": "1234.pts-0.host"},
        {"ITERM_SESSION_ID": "not-a-session"},
        {"ITERM_SESSION_ID": f"unrelated:{UUID}"},
        {"OMC_FISH_TITLE_DISABLE": "1"},
    ],
    ids=["not-iterm", "tmux", "screen", "bad-session", "bad-prefix", "disabled"],
)
def test_inert_shells_record_nothing(sh, env):
    proc = sh.run(
        "emit fish_prompt; git switch -qc feature/second; emit fish_prompt; "
        "cd /; emit fish_prompt; /bin/sleep 0.3; functions --handlers",
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert sh.lines() == ["USER_CONFIG"]
    registered = "__omc_title_prompt" in proc.stdout
    assert registered == (env.get("OMC_FISH_TITLE_DISABLE") == "1")  # disabled: armed but silent


def test_lc_terminal_alone_activates(sh):
    proc = sh.run(
        "emit fish_prompt; __wait_log '^request:' 1",
        env={"TERM_PROGRAM": None, "LC_TERMINAL": "iTerm2"},
    )
    assert proc.returncode == 0, proc.stderr
    assert sh.requests() == ["set feature/first"]


def test_failure_marker_hints_once_and_retries_once_per_failure(sh):
    marker = sh.omc_home / "title-failed" / UUID
    marker.parent.mkdir(parents=True)
    first = "omc: iTerm2 tab title update failed: worker exit 1 (retry: omc title set -- <branch>)"
    marker.write_text(f"{first}\nat earlier\n")
    old = time.time() - 120  # `path mtime` is whole seconds: make the rewrite below strictly newer
    os.utime(marker, (old, old))
    second = "omc: iTerm2 tab title update failed: timed out after 5s (retry: omc title set -- <branch>)"
    proc = sh.run(
        "emit fish_prompt; __wait_log '^request:' 1; emit fish_prompt; emit fish_prompt; "
        f"echo {shlex.quote(second)} > {shlex.quote(str(marker))}; "
        "emit fish_prompt; __wait_log '^request:' 2; emit fish_prompt; /bin/sleep 0.3"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.splitlines() == [first, second]
    assert sh.requests() == ["set feature/first", "set feature/first"]


def test_missing_omc_hints_once_and_records_nothing(sh):
    (sh.stubs / "omc").unlink()
    proc = sh.run(
        "emit fish_prompt; emit fish_prompt; git switch -qc feature/second; emit fish_prompt"
    )
    assert proc.returncode == 0
    assert proc.stderr.splitlines() == [HINT_NO_OMC]
    assert sh.lines() == ["USER_CONFIG"]


def test_omc_added_to_path_later_is_picked_up(sh):
    hidden = sh.tmp / "later"
    hidden.mkdir()
    shutil.move(str(sh.stubs / "omc"), str(hidden / "omc"))
    proc = sh.run(
        f"emit fish_prompt; set -gx PATH {shlex.quote(str(hidden))} $PATH; "
        "emit fish_prompt; __wait_log '^request:' 1"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.splitlines() == [HINT_NO_OMC]
    assert sh.requests() == ["set feature/first"]


def test_missing_git_changes_nothing(sh):
    (sh.stubs / "git").unlink()
    proc = sh.run("emit fish_prompt; emit fish_prompt; /bin/sleep 0.2")
    assert proc.returncode == 0
    assert proc.stderr == ""
    assert sh.lines() == ["USER_CONFIG"]


def test_preset_helper_variable_wins_over_path_omc(sh):
    helper = sh.tmp / "helper dir" / "python"
    helper.parent.mkdir()
    helper.write_text(STUB_HELPER)
    helper.chmod(0o755)
    proc = sh.run(
        f"set -g __omc_title_helper {shlex.quote(str(helper))} -m omc; "
        f"source {shlex.quote(str(sh.hook))}; emit fish_prompt; __wait_log via-helper 1"
    )
    assert proc.returncode == 0, proc.stderr
    lines = sh.lines()
    assert len(lines) == 2 and lines[1].startswith("via-helper -m omc title apply ")
    assert sh.applies() == []  # the PATH omc was never used


def test_pipestatus_survives_a_prompt_handler_that_dispatches(sh):
    prompt_log = sh.tmp / "prompts"
    sh.user_config.write_text(
        'set -g fish_greeting\n'
        'function fish_prompt; echo "P:$pipestatus" >> "$OMC_PROMPT_LOG"; printf "READY> "; end\n'
    )
    env = {**sh.env, "OMC_PROMPT_LOG": str(prompt_log), "TERM": "dumb"}
    drive_fish(
        sh.fish,
        env,
        [
            f"source {shlex.quote(str(sh.helpers))}",
            "git switch -qc feature/second; true | false | true",
            "__wait_log '^request:' 2",
        ],
        prompt_log,
        cwd=sh.repo,
    )
    prompts = prompt_log.read_text().splitlines()
    assert prompts[2] == "P:0 1 0", prompts  # the prompt right after the pipeline
    assert sh.requests() == ["set feature/first", "set feature/second"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_fish_hook.py -q`
Expected: FAIL — `FileNotFoundError` copying the missing asset in `HookShell.__init__`. After Step 4, any `fish: Unknown command: mkdir` or `Unknown command: git` text in a stderr assertion means the completion-cache dirs are not pre-created or the hook lacks the `command -s git` guard — fix those, never loosen the stderr assertions.

- [ ] **Step 4: Write the hook**

Create `src/omc/assets/omc-title.fish`:

```fish
# omc-managed fish title integration v2
# Pins this iTerm2 tab's title to the current Git branch (README: "Tab titles").
# Provisioned by `omc shell-integration fish`, removed by `omc uninstall`. The first
# line is omc's ownership marker: keep it, or omc refuses to manage this file.
# fish 3.7 syntax (Ubuntu CI); the prompt never waits on Python.

status is-interactive; or return
string match -rq -- '^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$' "$ITERM_SESSION_ID"; or return
if test "$TERM_PROGRAM" != iTerm.app; and test "$LC_TERMINAL" != iTerm2
    return
end
# Every shell inside a multiplexer inherits ONE tab's session id.
set -q TMUX; and return
set -q STY; and return

# Re-sourcing (conf.d, then an `omc start` -C command) must leave one handler per
# event and nothing from an earlier copy: erase by enumeration, not by a fixed list.
for __omc_name in (functions -a | string match -r '^__omc_title_.*')
    functions -e $__omc_name
end
set -e __omc_name
# State is reset; the configuration variable __omc_title_helper is preserved.
set -e __omc_title_last_request
set -e __omc_title_keys
set -e __omc_title_branches
set -e __omc_title_hinted
set -e __omc_title_marker_seen
set -e __omc_title_started
set -g __omc_title_uuid (string replace -r '^w[0-9]+t[0-9]+p[0-9]+:' '' -- $ITERM_SESSION_ID)
if set -q OMC_HOME; and test -n "$OMC_HOME"
    set -g __omc_title_home $OMC_HOME
else
    set -g __omc_title_home $HOME/.omc
end

function __omc_title_remember --argument-names name
    set -l key (command git rev-parse --absolute-git-dir 2>/dev/null)
    test -n "$key"; or return 0
    set -l index (contains -i -- $key $__omc_title_keys)
    if test -n "$index"
        set -g __omc_title_branches[$index] $name
    else
        set -ga __omc_title_keys $key
        set -ga __omc_title_branches $name
    end
end

function __omc_title_dispatch --argument-names outcome
    set -l helper $__omc_title_helper
    if test (count $helper) -eq 0
        # Resolved at refresh time so a PATH fixed later in the session is picked up.
        set helper (command -s omc)
        if test -z "$helper"
            if not set -q __omc_title_hinted
                set -g __omc_title_hinted 1
                echo 'omc: fish title hook found no `omc` on PATH; `omc shell-integration fish disable` removes the hook' >&2
            end
            set -e __omc_title_last_request
            return 0
        end
    end
    set -l dir $__omc_title_home/title-request
    test -d $dir; or command /bin/mkdir -p $dir 2>/dev/null; or return 0
    set -l request $dir/$__omc_title_uuid-$fish_pid
    # Builtin echo + redirection only; the helper serializes and re-reads (omc title apply).
    echo $outcome >$request; or return 0
    $helper title apply $request >/dev/null 2>&1 &
    disown $last_pid 2>/dev/null
end

function __omc_title_refresh
    set -g __omc_title_started 1
    test "$OMC_FISH_TITLE_DISABLE" = 1; and return 0
    set -l marker $__omc_title_home/title-failed/$__omc_title_uuid
    if test -e $marker
        set -l mtime (path mtime -- $marker)
        set -q __omc_title_marker_seen; or set -g __omc_title_marker_seen 0
        if test -n "$mtime"; and test $mtime -gt $__omc_title_marker_seen
            # One hint and one retry per failure, not one per prompt during the cooldown.
            set -g __omc_title_marker_seen $mtime
            read -l line <$marker
            test -n "$line"; and echo $line >&2
            set -e __omc_title_last_request
        end
    end
    # git absent from PATH changes nothing (spec §2). Without this guard fish itself prints
    # "fish: Unknown command: git" to stderr; a 2>/dev/null on the command cannot silence it.
    command -s git >/dev/null 2>&1; or return 0
    set -l ref (command git symbolic-ref --quiet HEAD 2>/dev/null)
    set -l code $status
    set -l desired
    switch $code
        case 0
            # Full symbolic ref, refs/heads/ stripped literally: a tag never collides.
            set -l name (string replace -r '^refs/heads/' '' -- $ref)
            set desired "set $name"
            test "$desired" != "$__omc_title_last_request"; and __omc_title_remember $name
        case 1
            set -l key (command git rev-parse --absolute-git-dir 2>/dev/null)
            set -l index
            test -n "$key"; and set index (contains -i -- $key $__omc_title_keys)
            if test -n "$index"
                set desired "set $__omc_title_branches[$index]"
            else
                set desired release
            end
        case 128
            set desired release
        case '*'
            return 0
    end
    test "$desired" = "$__omc_title_last_request"; and return 0
    set -g __omc_title_last_request $desired
    __omc_title_dispatch $desired
end

# The first prompt performs the first refresh; PWD events before it are ignored so
# config.fish PATH changes take effect first. `omc start` calls __omc_title_refresh
# itself because a -C command emits neither fish_preexec nor fish_prompt.
function __omc_title_prompt --on-event fish_prompt
    __omc_title_refresh
end

function __omc_title_preexec --on-event fish_preexec
    set -q __omc_title_started; and __omc_title_refresh
end

function __omc_title_pwd --on-variable PWD
    set -q __omc_title_started; and __omc_title_refresh
end
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_fish_hook.py -v`
Expected: all passed (about 20 cases, ~15 s). If `test_pipestatus_survives_a_prompt_handler_that_dispatches` reports `P:0` instead of `P:0 1 0`, the hook's handler is clobbering `$pipestatus`; that is a hook bug to fix, not a test to loosen.

- [ ] **Step 6: Gate and commit**

Run: `just check` then `just build`
Expected: green (the asset is not Python; ruff ignores it).

```bash
git add src/omc/assets/omc-title.fish tests/unit/_fishpty.py tests/unit/test_fish_hook.py
git commit -m "feat: add the fish title hook that pins iTerm2 tabs to the branch" -m "One git symbolic-ref per prompt decides set/release; the decision is written to <omc home>/title-request/<uuid>-<pid> with the echo builtin and \`omc title apply\` is disowned. Activation, marker hint, remembered branches per worktree and handler replacement are proven in a real fish over pipes and one PTY case for \$pipestatus." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Provisioning — `src/omc/fish_integration.py` and `omc shell-integration fish`

**Model:** standard coding tier

**Files:**
- Create: `src/omc/fish_integration.py` (reuse `git show 'backup/ordinary-fish-pre-squash:src/omc/fish_integration.py'` with the §5 changes below)
- Modify: `src/omc/cli/__init__.py` (parser after the `title` parser; dispatch)
- Test: `tests/unit/test_fish_integration.py` (port of the backup's file), `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `src/omc/assets/omc-title.fish` (Task 4); `Refusal`/`OmcError`; `ToolContext.home`/`.env`.
- Produces (used by Tasks 6, 7, 8, 9):
  - `omc.fish_integration.HOOK_PREFIX = "# omc-managed fish title integration"`.
  - `fish_hook_path() -> Path` — the packaged asset via `importlib.resources.files("omc") / "assets" / "omc-title.fish"`.
  - `managed_fish_path(ctx: ToolContext) -> Path` — `$XDG_CONFIG_HOME/fish/conf.d/omc-title.fish` when `XDG_CONFIG_HOME` is absolute, else `$HOME/.config/...`, else `Path.home()/.config/...`.
  - `is_owned(path: Path) -> bool` — regular file (not symlink) whose first line starts with `HOOK_PREFIX`.
  - `run_fish_integration(ctx: ToolContext, action: Literal["enable","disable","status","reconcile"]) -> int` — raises `Refusal` (exit 2) on symlink target, symlinked parent, or unowned existing file; `status` prints one JSON line `{"enabled","installed","path","owned"}`.
  - `remove_owned_hook(ctx: ToolContext) -> str | None` — uninstall's non-blocking removal; `None` when removed or absent, else a one-line note.
  - CLI: `omc shell-integration fish enable|disable|status|reconcile`, banner-exempt, configuration-free.
  - `fish_integration` imports only stdlib, `toolctx`, `errors`, and `importlib.resources`; it never imports `omc.shells` (Task 7 imports `fish_hook_path` from it, so a cycle would break).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fish_integration.py`:

```python
"""Managed fish hook lifecycle: owned by prefix, refuses foreign files, never touches config.fish."""

import json
from pathlib import Path

import pytest

from omc.cli import main
from omc.errors import Refusal
from omc.fish_integration import (
    HOOK_PREFIX,
    fish_hook_path,
    is_owned,
    managed_fish_path,
    remove_owned_hook,
    run_fish_integration,
)
from omc.toolctx import ToolContext


def _ctx(tmp_path, **extra):
    return ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc"), **extra})


def test_packaged_asset_is_the_hook_and_carries_the_prefix():
    asset = fish_hook_path()
    assert asset.name == "omc-title.fish" and asset.parent.name == "assets"
    assert asset.read_text().splitlines()[0].startswith(HOOK_PREFIX + " v")


def test_managed_hook_lifecycle(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    assert target == tmp_path / ".config" / "fish" / "conf.d" / "omc-title.fish"
    config = target.parent.parent / "config.fish"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"# my fish settings\n")
    assert run_fish_integration(ctx, "enable") == 0
    first = target.read_bytes()
    assert first == fish_hook_path().read_bytes()
    assert run_fish_integration(ctx, "reconcile") == 0
    assert target.read_bytes() == first  # byte-idempotent
    assert config.read_bytes() == b"# my fish settings\n"
    assert run_fish_integration(ctx, "status") == 0
    status = json.loads(capsys.readouterr().out)
    assert status == {"enabled": True, "installed": True, "path": str(target), "owned": True}
    assert run_fish_integration(ctx, "disable") == 0
    assert not target.exists()
    assert (ctx.home / "integrations" / "fish-title.disabled").exists()
    assert run_fish_integration(ctx, "reconcile") == 0
    assert not target.exists()  # reconcile honors the opt-out
    assert run_fish_integration(ctx, "status") == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    assert run_fish_integration(ctx, "enable") == 0
    assert target.read_bytes() == first
    assert not (ctx.home / "integrations" / "fish-title.disabled").exists()


def test_header_bump_never_orphans_an_earlier_copy(tmp_path):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"# omc-managed fish title integration v1\nfunction old; end\n")
    assert is_owned(target)
    assert run_fish_integration(ctx, "reconcile") == 0
    assert target.read_bytes() == fish_hook_path().read_bytes()


def test_xdg_config_home_absolute_only(tmp_path):
    assert managed_fish_path(_ctx(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "my config"))) == (
        tmp_path / "my config" / "fish" / "conf.d" / "omc-title.fish"
    )
    assert managed_fish_path(_ctx(tmp_path, XDG_CONFIG_HOME="relative/config")) == (
        tmp_path / ".config" / "fish" / "conf.d" / "omc-title.fish"
    )
    no_home = ToolContext.from_env({"OMC_HOME": str(tmp_path / "omc")})
    assert managed_fish_path(no_home) == Path.home() / ".config" / "fish" / "conf.d" / "omc-title.fish"


@pytest.mark.parametrize("action", ["enable", "disable", "reconcile"])
def test_unowned_file_and_symlink_are_refused_untouched(tmp_path, action):
    ctx = _ctx(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "my config"))
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my hook\n")
    with pytest.raises(Refusal, match=str(target)):
        run_fish_integration(ctx, action)
    assert target.read_bytes() == b"my hook\n"
    target.unlink()
    target.symlink_to(tmp_path / "absent")
    with pytest.raises(Refusal, match="symlink"):
        run_fish_integration(ctx, action)
    assert target.is_symlink()
    assert not is_owned(target)


def test_parent_symlink_is_refused(tmp_path):
    ctx = _ctx(tmp_path)
    fish_dir = tmp_path / ".config" / "fish"
    fish_dir.parent.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    fish_dir.symlink_to(outside)
    with pytest.raises(Refusal, match="symlink"):
        run_fish_integration(ctx, "enable")
    assert not (outside / "conf.d" / "omc-title.fish").exists()


def test_status_never_refuses(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my hook\n")
    assert run_fish_integration(ctx, "status") == 0
    status = json.loads(capsys.readouterr().out)
    assert status["installed"] is True and status["owned"] is False


def test_remove_owned_hook_is_non_blocking(tmp_path):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    assert remove_owned_hook(ctx) is None  # absent
    assert run_fish_integration(ctx, "enable") == 0
    assert remove_owned_hook(ctx) is None
    assert not target.exists()
    target.write_bytes(b"my hook\n")
    note = remove_owned_hook(ctx)
    assert note is not None and str(target) in note and "not an omc-owned file" in note
    assert target.read_bytes() == b"my hook\n"
    target.unlink()
    target.symlink_to(tmp_path / "absent")
    assert remove_owned_hook(ctx) is not None
    assert target.is_symlink()


def test_owned_hook_removal_permission_error_is_an_error(tmp_path, monkeypatch, capsys):
    ctx = _ctx(tmp_path)
    assert run_fish_integration(ctx, "enable") == 0
    target = managed_fish_path(ctx)
    original = target.read_bytes()
    real_unlink = Path.unlink

    def deny_target(path, *args, **kwargs):
        if path == target:
            raise PermissionError("simulated read-only fish directory")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_target)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(ctx.home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert main(["shell-integration", "fish", "disable"]) == 1
    assert target.read_bytes() == original
    assert "fish integration removal failed" in capsys.readouterr().err
    assert remove_owned_hook(ctx) is not None  # uninstall path: a note, not an exception


def test_enable_flag_removal_permission_error_is_reported(tmp_path, monkeypatch, capsys):
    ctx = _ctx(tmp_path)
    assert run_fish_integration(ctx, "disable") == 0
    flag = ctx.home / "integrations" / "fish-title.disabled"
    real_unlink = Path.unlink

    def deny_flag(path, *args, **kwargs):
        if path == flag:
            raise PermissionError("simulated read-only omc integrations directory")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_flag)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(ctx.home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert main(["shell-integration", "fish", "enable"]) == 1
    assert managed_fish_path(ctx).exists()
    assert flag.exists()
    assert "could not clear fish disable" in capsys.readouterr().err
```

Append to `tests/unit/test_cli.py`:

```python
def test_shell_integration_is_quiet_unconfigured_and_refuses_with_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "omc"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert main(["shell-integration", "fish", "enable"]) == 0
    assert capsys.readouterr() == ("", "")  # no banner, nothing on success
    assert main(["shell-integration", "fish", "status"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["owned"] is True and captured.err == ""
    assert main(["shell-integration", "fish", "disable"]) == 0
    hook = tmp_path / "config" / "fish" / "conf.d" / "omc-title.fish"
    assert not hook.exists()
    hook.write_bytes(b"user content\n")
    assert main(["shell-integration", "fish", "enable"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and str(hook) in err and "Traceback" not in err
    assert hook.read_bytes() == b"user content\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_fish_integration.py tests/unit/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'omc.fish_integration'`; the CLI test fails on `invalid choice: 'shell-integration'`.

- [ ] **Step 3: Implement `src/omc/fish_integration.py`**

```python
"""The packaged fish hook and its omc-owned copy in the user's fish conf.d (spec §5)."""

from __future__ import annotations

import json
import os
import tempfile
from importlib import resources
from pathlib import Path
from typing import Literal

from .errors import OmcError, Refusal
from .toolctx import ToolContext

HOOK_PREFIX = "# omc-managed fish title integration"
Action = Literal["enable", "disable", "status", "reconcile"]


def fish_hook_path() -> Path:
    # Same packaged-asset convention as skills_source.py; a real path in wheel and checkout.
    return Path(str(resources.files("omc") / "assets" / "omc-title.fish"))


def managed_fish_path(ctx: ToolContext) -> Path:
    xdg = ctx.env.get("XDG_CONFIG_HOME", "")
    if xdg and Path(xdg).is_absolute():
        base = Path(xdg)
    else:
        home = ctx.env.get("HOME")
        base = (Path(home) if home else Path.home()) / ".config"
    return base / "fish" / "conf.d" / "omc-title.fish"


def _disabled_path(ctx: ToolContext) -> Path:
    return ctx.home / "integrations" / "fish-title.disabled"


def is_owned(path: Path) -> bool:
    """A regular file whose first line starts with HOOK_PREFIX, any version suffix."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith(HOOK_PREFIX.encode())


def _refuse_unless_manageable(target: Path) -> None:
    if any(parent.is_symlink() for parent in target.parents):
        raise Refusal(f"fish integration: a parent of {target} is a symlink; leaving it alone")
    if target.is_symlink():
        raise Refusal(f"fish integration: {target} is a symlink; leaving it alone")
    if target.exists() and not is_owned(target):
        raise Refusal(f"fish integration: {target} exists and is not omc-owned; move it aside first")


def _install(ctx: ToolContext) -> None:
    target = managed_fish_path(ctx)
    _refuse_unless_manageable(target)
    content = fish_hook_path().read_bytes()
    if not content.startswith(HOOK_PREFIX.encode()):
        raise OmcError("packaged fish hook has no ownership header (broken install?)")
    target.parent.mkdir(parents=True, exist_ok=True)
    _refuse_unless_manageable(target)  # re-check: the path may have changed during mkdir
    if target.exists() and target.read_bytes() == content:
        return
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".omc-title.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _remove(ctx: ToolContext) -> None:
    target = managed_fish_path(ctx)
    _refuse_unless_manageable(target)
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OmcError(f"fish integration removal failed: {exc}") from exc


def remove_owned_hook(ctx: ToolContext) -> str | None:
    """Uninstall's removal: delete only an owned file; otherwise return why it stays."""
    target = managed_fish_path(ctx)
    if not target.exists() and not target.is_symlink():
        return None
    if not is_owned(target):
        return f"left {target} in place: not an omc-owned file"
    try:
        target.unlink()
    except OSError as exc:
        return f"could not remove {target}: {exc}"
    return None


def run_fish_integration(ctx: ToolContext, action: Action) -> int:
    target = managed_fish_path(ctx)
    disabled = _disabled_path(ctx)
    if action == "status":
        print(
            json.dumps(
                {
                    "enabled": not disabled.exists(),
                    "installed": target.exists() or target.is_symlink(),
                    "path": str(target),
                    "owned": is_owned(target),
                }
            )
        )
        return 0
    if action == "disable":
        _remove(ctx)
        try:
            disabled.parent.mkdir(parents=True, exist_ok=True)
            disabled.touch()
        except OSError as exc:
            raise OmcError(f"could not persist fish disable: {exc}") from exc
        return 0
    if action == "reconcile" and disabled.exists():
        _remove(ctx)
        return 0
    if action in ("enable", "reconcile"):
        try:
            _install(ctx)
        except OSError as exc:
            raise OmcError(f"fish integration setup failed: {exc}") from exc
        if action == "enable":
            try:
                disabled.unlink(missing_ok=True)
            except OSError as exc:
                raise OmcError(f"could not clear fish disable: {exc}") from exc
        return 0
    raise ValueError(f"unknown fish integration action: {action}")
```

- [ ] **Step 4: Wire the CLI**

In `src/omc/cli/__init__.py` after the `title` parser block add:

```python
    p_shell = sub.add_parser(
        "shell-integration", help="Manage the fish tab-title hook (enable|disable|status|reconcile)"
    )
    shell_sub = p_shell.add_subparsers(dest="shell", required=True)
    p_fish = shell_sub.add_parser("fish")
    p_fish.add_argument("action", choices=("enable", "disable", "status", "reconcile"))
```

and in `_dispatch`, right after the `title` branch:

```python
    if args.command == "shell-integration":
        from ..fish_integration import run_fish_integration  # lazy, never loads configuration

        return run_fish_integration(ctx, args.action)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_fish_integration.py tests/unit/test_cli.py -q`
Expected: all passed.

- [ ] **Step 6: Gate and commit**

Run: `just check` then `just build`
Expected: green; `uv build` output lists `omc/assets/omc-title.fish` inside the wheel (`unzip -Z1 dist/omc-*.whl | grep omc-title.fish`).

```bash
git add src/omc/fish_integration.py src/omc/cli/__init__.py tests/unit/test_fish_integration.py tests/unit/test_cli.py
git commit -m "feat: provision the fish hook with omc shell-integration fish" -m "enable|disable|status|reconcile copy the packaged asset atomically into \$XDG_CONFIG_HOME/fish/conf.d, own it by header prefix so a version bump never orphans a copy, refuse symlinks and foreign files with exit 2, and honor a persistent opt-out under <omc home>/integrations." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Installer post-step (non-aborting) and owned-only uninstall

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/installer.py` (whole `run_install`, `run_update`, `run_uninstall`; new helpers)
- Test: `tests/unit/test_installer.py`

**Interfaces:**
- Consumes: `omc.fish_integration.remove_owned_hook(ctx) -> str | None` (Task 5); the installed CLI's `shell-integration fish reconcile` (Task 5); `ToolContext.run`, `ToolContext.run_bounded`, `ToolContext.uv_argv`.
- Produces:
  - `omc.installer._is_macos() -> bool` (module attribute; tests monkeypatch it).
  - `omc.installer._fresh_cli(ctx) -> Path | None` — `<uv tool dir --bin>/omc`, falling back to `<uv tool dir>/omc/bin/omc`; `None` when neither is a file.
  - `omc.installer.post_install(ctx) -> PostInstall` with `PostInstall(version: str | None, fish_failure: str | None)`; non-macOS returns `PostInstall(None, None)` without running anything.
  - Narration: `→ provisioning fish integration` on stderr before the step; the reconcile's stderr relayed verbatim.
  - Final stderr line: `✓ omc <v> installed · ✓ fish integration` / `✓ omc <v> updated · ✗ fish integration: <reason>` on macOS; `✓ omc installed` / `✓ omc updated` elsewhere (`<v>` comes from the fresh CLI's `--version`; omitted when unavailable).
  - Exit code: `run_update` returns the dependency refresh's rc when nonzero, else 1 when the fish step failed, else 0; `run_install` returns 1 when the fish step failed, else 0. `require_tools` still raises before either.
  - `run_uninstall`: on macOS removes only an owned hook, prints `· fish integration: <note>` when it stays, continues to data removal and `uv tool uninstall`, then prints `· fish shells already running keep the loaded title hook until they exit`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_installer.py` extend the autouse fixture so legacy cases stay on the non-macOS path:

```python
@pytest.fixture(autouse=True)
def _no_real_gitnexus(monkeypatch):
    # Installer tests exercise require_tools + the plugin loop, not the real
    # clone/build. Keep update_gitnexus a no-op success here. A per-test
    # monkeypatch.setattr overrides this autouse default.
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 0)
    # Legacy cases model uv/dependency behaviour only; the fish cases below opt
    # back into the macOS provisioning path explicitly.
    monkeypatch.setattr("omc.installer._is_macos", lambda: False)
```

Add to the import block at the top of `tests/unit/test_installer.py` (ruff E402; `os` and `stat` are already there, so the stdlib group becomes):

```python
import os
import stat
import subprocess
from types import SimpleNamespace
```

Then append:

```python
def _fresh_ctx(tmp_path, monkeypatch, *, bin_link=True, reconcile_rc=0, reconcile_err="", timeout=False):
    """A ctx whose uv answers `tool dir --bin`/`tool dir` and whose fresh omc is recorded."""
    from omc import installer

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc")})
    bin_dir = tmp_path / "uv bin"
    tool_dir = tmp_path / "uv tools"
    (tool_dir / "omc" / "bin").mkdir(parents=True)
    (tool_dir / "omc" / "bin" / "omc").write_text("#!/bin/sh\n")
    bin_dir.mkdir()
    if bin_link:
        (bin_dir / "omc").write_text("#!/bin/sh\n")
    calls = []

    def run(argv, **kwargs):
        calls.append(("run", list(argv)))
        if argv[:3] == ["uv", "tool", "dir"]:
            out = str(bin_dir) if "--bin" in argv else str(tool_dir)
            return SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")
        if argv[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="omc 9.9.9\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run_bounded(argv, *, timeout, cwd=None, extra_env=None):
        calls.append(("bounded", list(argv), timeout))
        if timeout_flag[0]:
            raise TimeoutError("command timed out after 30s")
        return subprocess.CompletedProcess(list(argv), reconcile_rc, "", reconcile_err)

    timeout_flag = [timeout]
    monkeypatch.setattr(ctx, "run", run)
    monkeypatch.setattr(ctx, "run_bounded", run_bounded)
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: calls.append(("uv", a)) or 0)
    monkeypatch.setattr(installer, "_is_macos", lambda: True)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: calls.append(("dep",)) or 0)
    expected_exe = bin_dir / "omc" if bin_link else tool_dir / "omc" / "bin" / "omc"
    return ctx, calls, expected_exe


def test_update_runs_fresh_cli_reconcile_between_uv_and_gates(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch)
    assert installer.run_update(ctx) == 0
    assert calls[0] == ("uv", ("tool", "upgrade", "omc"))
    assert calls[1] == ("run", ["uv", "tool", "dir", "--bin"])
    assert calls[2] == ("run", [str(exe), "--version"])
    assert calls[3] == ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30)
    assert ("dep",) in calls and calls.index(("dep",)) > 3
    err = capsys.readouterr().err
    assert err.index("→ provisioning fish integration") < err.index("✓ omc 9.9.9 updated · ✓ fish integration")


def test_update_falls_back_to_tool_dir_when_bin_link_missing(tmp_path, monkeypatch):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch, bin_link=False)
    assert installer.run_update(ctx) == 0
    assert ("run", ["uv", "tool", "dir"]) in calls
    assert ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30) in calls


def test_fish_failure_never_aborts_update_and_is_reported_distinctly(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(
        tmp_path, monkeypatch, reconcile_rc=2, reconcile_err="error: fish integration: /x is not omc-owned\n"
    )
    assert installer.run_update(ctx) == 1  # every later gate succeeded → the fish outcome decides
    assert ("dep",) in calls  # the remaining gates ran
    err = capsys.readouterr().err
    assert "error: fish integration: /x is not omc-owned" in err  # relayed verbatim
    assert err.rstrip().splitlines()[-1] == "✓ omc 9.9.9 updated · ✗ fish integration: reconcile exit 2"


def test_fish_timeout_is_a_reason_not_an_abort(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, timeout=True)
    assert installer.run_update(ctx) == 1
    assert ("dep",) in calls
    assert "✗ fish integration: timed out after 30s" in capsys.readouterr().err


def test_dependency_failure_outranks_fish_outcome(tmp_path, monkeypatch):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, reconcile_rc=1)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 3)
    assert installer.run_update(ctx) == 3


def test_missing_fresh_cli_is_a_reason(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, bin_link=False)
    (tmp_path / "uv tools" / "omc" / "bin" / "omc").unlink()
    assert installer.run_update(ctx) == 1
    assert not any(c[0] == "bounded" for c in calls)
    assert "✗ fish integration: installed omc executable not found" in capsys.readouterr().err


def test_non_macos_skips_the_fish_step_entirely(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "_is_macos", lambda: False)
    assert installer.run_update(ctx) == 0
    assert not any(c[0] in ("run", "bounded") for c in calls)
    err = capsys.readouterr().err
    assert "provisioning fish" not in err and err.rstrip().splitlines()[-1] == "✓ omc updated"


def test_install_runs_post_step_and_reports(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch, reconcile_rc=1)
    assert installer.run_install(ctx, str(_checkout(tmp_path))) == 1
    assert calls[0][0] == "uv" and calls[0][1][:3] == ("tool", "install", "--reinstall")
    assert ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30) in calls
    captured = capsys.readouterr()
    assert "re-rooted future `omc update`s" in captured.out
    assert captured.err.rstrip().splitlines()[-1] == "✓ omc 9.9.9 installed · ✗ fish integration: reconcile exit 1"


def test_uninstall_removes_only_an_owned_hook_and_never_blocks(tmp_path, monkeypatch, capsys):
    from omc import installer
    from omc.fish_integration import managed_fish_path, run_fish_integration

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc"), "SHELL": "/bin/zsh"})
    monkeypatch.setattr(installer, "_is_macos", lambda: True)
    uv_calls = []
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: uv_calls.append(a) or 0)
    ctx.home.mkdir()
    assert run_fish_integration(ctx, "enable") == 0
    hook = managed_fish_path(ctx)
    assert installer.run_uninstall(ctx) == 0
    assert not hook.exists() and not ctx.home.exists()
    assert uv_calls == [("tool", "uninstall", "omc")]
    err = capsys.readouterr().err
    assert "fish shells already running keep the loaded title hook" in err
    # unowned file: left in place with one note; uninstall still completes
    hook.write_bytes(b"user content\n")
    ctx.home.mkdir()
    assert installer.run_uninstall(ctx) == 0
    assert hook.read_bytes() == b"user content\n" and not ctx.home.exists()
    err = capsys.readouterr().err
    assert err.count("· fish integration: left") == 1
    # symlink: same
    hook.unlink()
    hook.symlink_to(tmp_path / "absent")
    assert installer.run_uninstall(ctx) == 0
    assert hook.is_symlink()


def test_uninstall_on_non_macos_leaves_the_hook_path_alone(tmp_path, monkeypatch, capsys):
    from omc import installer
    from omc.fish_integration import managed_fish_path, run_fish_integration

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc")})
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: 0)
    assert run_fish_integration(ctx, "enable") == 0
    assert installer.run_uninstall(ctx) == 0
    assert managed_fish_path(ctx).exists()
    assert "fish" not in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_installer.py -q`
Expected: the autouse fixture fails every test with `AttributeError: omc.installer has no attribute '_is_macos'`.

- [ ] **Step 3: Implement the installer changes**

In `src/omc/installer.py` add the imports to the existing import block (ruff E402/I001 run in `just build`), so it reads:

```python
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import store
from .errors import OmcError
from .fish_integration import remove_owned_hook
from .plugin import ensure_plugin, marketplace_source
from .probe import require_tools
from .providers.registry import get_provider
from .toolctx import ToolContext
```

then add the helpers after `_PLUGIN_REMOVAL`:

```python
def _is_macos() -> bool:
    return sys.platform == "darwin"


@dataclass(frozen=True)
class PostInstall:
    version: str | None  # the FRESH on-disk omc's version, when its executable was found
    fish_failure: str | None  # None = provisioned (or the step was skipped off macOS)


def _fresh_cli(ctx: ToolContext) -> Path | None:
    """The just-installed on-disk omc: <uv tool dir --bin>/omc, else <uv tool dir>/omc/bin/omc.

    Resolved through ctx.uv_argv so UV_TOOL_BIN_DIR/UV_TOOL_DIR are honored. The running
    (old) process never imports the new package's fish modules.
    """
    for args, tail in ((("tool", "dir", "--bin"), ("omc",)), (("tool", "dir"), ("omc", "bin", "omc"))):
        try:
            cp = ctx.run(ctx.uv_argv(*args))
        except OSError:
            return None
        if cp.returncode == 0 and cp.stdout.strip():
            candidate = Path(cp.stdout.strip()).joinpath(*tail)
            if candidate.is_file():
                return candidate
    return None


def post_install(ctx: ToolContext) -> PostInstall:
    """macOS-only: run the fresh CLI's `shell-integration fish reconcile`. Never raises."""
    if not _is_macos():
        return PostInstall(None, None)
    print("→ provisioning fish integration", file=sys.stderr)
    exe = _fresh_cli(ctx)
    if exe is None:
        return PostInstall(None, "installed omc executable not found via `uv tool dir`")
    version = None
    try:
        cp = ctx.run([str(exe), "--version"])
        if cp.returncode == 0 and cp.stdout.startswith("omc "):
            version = cp.stdout.split()[1]
    except OSError:
        pass
    try:
        cp = ctx.run_bounded([str(exe), "shell-integration", "fish", "reconcile"], timeout=30)
    except TimeoutError:
        return PostInstall(version, "timed out after 30s")
    except OSError as exc:
        return PostInstall(version, f"{exe} not runnable ({type(exc).__name__})")
    if cp.stderr:
        sys.stderr.write(cp.stderr if cp.stderr.endswith("\n") else cp.stderr + "\n")
    if cp.returncode != 0:
        return PostInstall(version, f"reconcile exit {cp.returncode}")
    return PostInstall(version, None)


def _report(verb: str, post: PostInstall) -> str:
    omc = f"✓ omc {post.version} {verb}" if post.version else f"✓ omc {verb}"
    if not _is_macos():
        return omc
    fish = "✓ fish integration" if post.fish_failure is None else f"✗ fish integration: {post.fish_failure}"
    return f"{omc} · {fish}"
```

Rewrite `run_install`, `run_update`'s ends, and `run_uninstall`:

```python
def run_install(ctx: ToolContext, path: str) -> int:
    abspath = str(Path(path).resolve())
    err = validate_checkout(abspath)
    if err is not None:
        print(err, file=sys.stderr)
        return 1
    rc = _uv(ctx, "tool", "install", "--reinstall", abspath)
    if rc != 0:
        return rc
    post = post_install(ctx)
    print(f"Installed omc (re-rooted future `omc update`s at {abspath}).")
    print(_report("installed", post), file=sys.stderr)
    return 1 if post.fish_failure else 0


def _finish_update(post: PostInstall, dep_rc: int) -> int:
    print(_report("updated", post), file=sys.stderr)
    if dep_rc:
        return dep_rc
    return 1 if post.fish_failure else 0
```

In `run_update`: after `if rc != 0: return rc` insert `post = post_install(ctx)` (before `cfg = store.load_global(ctx.home)`); replace the two `return dep_rc` with `return _finish_update(post, dep_rc)`.

```python
def run_uninstall(ctx: ToolContext) -> int:
    if _is_macos():
        note = remove_owned_hook(ctx)  # owned file only; anything else stays, with a note
        if note:
            print(f"· fish integration: {note}", file=sys.stderr)
    if _is_unsafe_home(ctx.home, ctx.env):
        print(
            f"refuse: OMC_HOME ({ctx.home}) is unsafe to delete; skipping data removal",
            file=sys.stderr,
        )
    elif ctx.home.exists():
        shutil.rmtree(ctx.home, ignore_errors=True)
        print(f"Removed {ctx.home}")
    rc = _uv(ctx, "tool", "uninstall", "omc")
    print(_PLUGIN_REMOVAL)
    if _is_macos():
        print("· fish shells already running keep the loaded title hook until they exit", file=sys.stderr)
    return 0 if rc == 0 else 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_installer.py -q`
Expected: all passed, including the pre-existing `test_update_isolates_provider_failures` (`err.count("✗") == 1` still holds because the non-macOS report carries no ✗).

- [ ] **Step 5: Gate and commit**

Run: `just check` then `just build`
Expected: green.

```bash
git add src/omc/installer.py tests/unit/test_installer.py
git commit -m "feat: provision the fish hook from the freshly installed omc on install and update" -m "After uv succeeds on macOS the installer runs <uv tool dir --bin>/omc shell-integration fish reconcile under a 30s deadline, relays its stderr, never aborts the update on failure and ends with one line naming both outcomes; the exit code is the fish outcome only when every later gate passed. Uninstall removes an owned hook only and never blocks on a foreign file." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `omc start` on fish — `FishShell.build_invocation` `if`/`else`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/shells/fish.py:17-50`
- Test: `tests/unit/test_shells.py`, `tests/unit/test_fish_title.py`

**Interfaces:**
- Consumes: `omc.fish_integration.fish_hook_path()` (Task 5); the hook's `__omc_title_refresh` and `__omc_title_helper` contract (Task 4); `sys.executable`.
- Produces: `FishShell.build_invocation(*, cwd, title, startup_argv, title_seq, title_argv=None) -> tuple[list[str], dict[str, str]]` unchanged in signature. With `title_argv` the `-C` body is `if <predicate>; set -g __omc_title_helper <sys.executable> -m omc; source <asset>; cd <cwd>; __omc_title_refresh; else; <today's inline code, byte for byte>; end; <startup>`. Without `title_argv` the body is unchanged. bash/zsh/sh untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_shells.py`:

```python
import sys

from omc.fish_integration import fish_hook_path

FISH_TITLE_ARGV = ["/usr/bin/python3", "-m", "omc.terminal_title"]


def _inline_else(helper, title, cwd):
    """Today's inline fish title code, frozen byte for byte (spec §6)."""
    return "; ".join(
        [
            f"set -g __omc_desired_title {shlex.quote(title)}",
            "set -g __omc_last_attempt ''",
            'function fish_title; printf "%s\\n" "$__omc_desired_title"; end',
            "function __omc_refresh_title",
            "set -l saved_status $status",
            "set -l branch (command git branch --show-current 2>/dev/null)",
            'if test $status -eq 0; and test -n "$branch"; '
            'set -g __omc_desired_title "$branch"; end',
            'if test "$__omc_last_attempt" != "$__omc_desired_title"',
            'set -g __omc_last_attempt "$__omc_desired_title"',
            f'{helper} "$__omc_desired_title"; or true',
            "end",
            "return $saved_status",
            "end",
            "function __omc_title_preexec --on-event fish_preexec; __omc_refresh_title; end",
            "function __omc_title_prompt --on-event fish_prompt; __omc_refresh_title; end",
            "function __omc_title_pwd --on-variable PWD; __omc_refresh_title; end",
            f"cd {shlex.quote(cwd)}",
            "__omc_refresh_title",
        ]
    )


def test_fish_generated_command_is_one_if_else_around_the_hook(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python3")
    argv, files = detect_shell({"SHELL": "fish"}).build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    assert files == {} and argv[:3] == ["fish", "-i", "-C"]
    body = argv[3]
    assert body.count("; else; ") == 1
    if_branch, rest = body.split("; else; ", 1)
    else_branch, startup = rest.rsplit("; end; ", 1)
    assert startup == shlex.join(ARGS["startup_argv"])
    predicate = if_branch.split("; set -g __omc_title_helper", 1)[0]
    assert predicate == (
        "if status is-interactive; and string match -rq -- "
        "'^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$' \"$ITERM_SESSION_ID\"; "
        'and begin; test "$TERM_PROGRAM" = iTerm.app; or test "$LC_TERMINAL" = iTerm2; end; '
        'and not set -q TMUX; and not set -q STY; and test "$OMC_FISH_TITLE_DISABLE" != 1'
    )
    steps = [
        "set -g __omc_title_helper /opt/py/bin/python3 -m omc",
        f"source {shlex.quote(str(fish_hook_path()))}",
        "cd /w/tree",
        "__omc_title_refresh",
    ]
    positions = [if_branch.index(step) for step in steps]
    assert positions == sorted(positions) and if_branch.endswith("__omc_title_refresh")
    assert else_branch == _inline_else(shlex.join(FISH_TITLE_ARGV), "proj-1-fix", "/w/tree")
    assert '/usr/bin/python3 -m omc.terminal_title "$__omc_desired_title"' in else_branch


def test_fish_without_title_argv_is_unchanged():
    argv, _ = detect_shell({"SHELL": "fish"}).build_invocation(**ARGS)
    assert "; else; " not in argv[3] and "__omc_title_helper" not in argv[3]
    assert argv[3] == (
        "function fish_title; echo proj-1-fix; end; cd /w/tree; "
        f"printf '%s' {shlex.quote(ARGS['title_seq'])}; {shlex.join(ARGS['startup_argv'])}"
    )


def test_fish_dry_run_output_is_terminal_independent():
    # The builder never reads the environment: identical output whatever the terminal.
    shell = detect_shell({"SHELL": "fish"})
    first, _ = shell.build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    second, _ = shell.build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    assert first == second
```

In `tests/unit/test_fish_title.py` replace the `fish_session` fixture's `env` line and `run()` signature so the developer's iTerm2 is scrubbed and cases may add env:

```python
    scrub = (
        "ITERM_SESSION_ID", "TERM_PROGRAM", "LC_TERMINAL", "TMUX", "STY", "OMC_FISH_TITLE_DISABLE",
        "XDG_CACHE_HOME", "XDG_DATA_HOME",
    )  # fmt: skip
    base = {k: v for k, v in os.environ.items() if k not in scrub}
    # Pre-created so fish's interactive init never runs a bare `mkdir` on a restricted PATH
    # (the double-sourcing case below runs with PATH = stub dir).
    for base_dir in ("cache", "data"):
        (tmp_path / base_dir / "fish" / "generated_completions").mkdir(parents=True)
    env = {
        **base,
        "OMC_TITLE_LOG": str(log),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }

    def run(extra="", *, title_argv=None, startup=None, env_extra=None):
        argv, files = FishShell().build_invocation(
            cwd=str(repo),
            title="feature/first",
            startup_argv=startup or ["/bin/sh", "-c", 'echo STARTUP >> "$OMC_TITLE_LOG"'],
            title_seq="\033]0;feature/first\007",
            title_argv=[str(recorder)] if title_argv is None else title_argv,
        )
        assert not files
        argv[0] = fish
        argv[3] += "; " + extra + "; exit"
        proc = subprocess.run(
            argv,
            cwd=tmp_path,
            env={**env, **(env_extra or {})},
            capture_output=True,
            text=True,
            timeout=25,
        )
        return proc, log.read_text().splitlines() if log.exists() else []
```

and append the generated-start iTerm2 case:

```python
def test_generated_start_in_iterm2_dispatches_exactly_one_set_despite_double_sourcing(
    fish_session, tmp_path, monkeypatch
):
    import sys

    from omc.fish_integration import fish_hook_path

    repo, run = fish_session
    # The builder embeds sys.executable as the hook's helper; point it at a recorder that
    # accepts `-m omc title apply <file>` so no real SDK worker can run.
    fake_python = tmp_path / "fake python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$OMC_TITLE_LOG"\n'
        'if [ "$4" = apply ] && [ -r "$5" ]; then IFS= read -r l < "$5"; '
        'printf \'request: %s\\n\' "$l" >> "$OMC_TITLE_LOG"; fi\n'
        "exit 0\n"
    )
    fake_python.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake_python))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "git").symlink_to(shutil.which("git"))
    (stubs / "omc").write_text('#!/bin/sh\nprintf \'PATH-OMC %s\\n\' "$*" >> "$OMC_TITLE_LOG"\nexit 0\n')
    (stubs / "omc").chmod(0o755)
    confd = tmp_path / "config" / "fish" / "conf.d"
    confd.mkdir(parents=True)
    shutil.copyfile(fish_hook_path(), confd / "omc-title.fish")  # conf.d copy: the first sourcing
    wait = (
        "set -l t 0; while test $t -lt 200; "
        "and test (count (string match -r '^request:' <$OMC_TITLE_LOG)) -lt 1; "
        "set t (math $t + 1); /bin/sleep 0.05; end"
    )
    proc, lines = run(
        f"emit fish_prompt; emit fish_prompt; {wait}; /bin/sleep 0.2",
        env_extra={
            "ITERM_SESSION_ID": "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632",
            "TERM_PROGRAM": "iTerm.app",
            "PATH": str(stubs),
            "OMC_HOME": str(tmp_path / "omc-home"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "USER_CONFIG" in lines and "STARTUP" in lines
    assert len([l for l in lines if l.startswith("-m omc title apply ")]) == 1
    assert [l for l in lines if l.startswith("request: ")] == ["request: set feature/first"]
    assert not any(l.startswith("PATH-OMC") for l in lines)  # the preset helper is never overridden
    assert "feature/first" not in lines  # the inline recorder (else branch) did not run


def test_generated_start_with_session_disable_uses_inline_path(fish_session):
    _, run = fish_session
    proc, lines = run(
        env_extra={
            "ITERM_SESSION_ID": "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632",
            "TERM_PROGRAM": "iTerm.app",
            "OMC_FISH_TITLE_DISABLE": "1",
        }
    )
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_shells.py tests/unit/test_fish_title.py -q`
Expected: the three new `test_shells` cases and the double-sourcing case FAIL (`"; else; "` absent; the generated command has no `if`); every pre-existing case passes.

- [ ] **Step 3: Implement the builder**

Replace `src/omc/shells/fish.py`:

```python
from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping

from ..fish_integration import fish_hook_path
from .base import Shell, joined_startup

# The spec §2 activation predicate plus the per-session opt-out, evaluated by fish at
# startup. The builder itself never reads the environment (pure; --dry-run identical
# in every terminal).
_ITERM2_HOOK_PREDICATE = (
    "status is-interactive; and string match -rq -- "
    "'^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$' \"$ITERM_SESSION_ID\"; "
    'and begin; test "$TERM_PROGRAM" = iTerm.app; or test "$LC_TERMINAL" = iTerm2; end; '
    'and not set -q TMUX; and not set -q STY; and test "$OMC_FISH_TITLE_DISABLE" != 1'
)


class FishShell(Shell):
    name = "fish"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "fish"

    def build_invocation(self, *, cwd, title, startup_argv, title_seq, title_argv=None):
        if title_argv:
            helper = shlex.join(title_argv)
            inline = [
                f"set -g __omc_desired_title {shlex.quote(title)}",
                "set -g __omc_last_attempt ''",
                'function fish_title; printf "%s\\n" "$__omc_desired_title"; end',
                "function __omc_refresh_title",
                "set -l saved_status $status",
                "set -l branch (command git branch --show-current 2>/dev/null)",
                'if test $status -eq 0; and test -n "$branch"; '
                'set -g __omc_desired_title "$branch"; end',
                'if test "$__omc_last_attempt" != "$__omc_desired_title"',
                'set -g __omc_last_attempt "$__omc_desired_title"',
                f'{helper} "$__omc_desired_title"; or true',
                "end",
                "return $saved_status",
                "end",
                "function __omc_title_preexec --on-event fish_preexec; __omc_refresh_title; end",
                "function __omc_title_prompt --on-event fish_prompt; __omc_refresh_title; end",
                "function __omc_title_pwd --on-variable PWD; __omc_refresh_title; end",
                f"cd {shlex.quote(cwd)}",
                "__omc_refresh_title",  # -C startup need not emit fish_preexec.
            ]
            # In iTerm2 the packaged hook (sourced here even when conf.d never got it or
            # was disabled) owns the title; the explicit refresh is the shell's first,
            # so the cd's PWD event is ignored and the first prompt after the provider
            # exits is a no-op on an unchanged branch (spec §6).
            hooked = [
                f"set -g __omc_title_helper {shlex.join([sys.executable, '-m', 'omc'])}",
                f"source {shlex.quote(str(fish_hook_path()))}",
                f"cd {shlex.quote(cwd)}",
                "__omc_title_refresh",
            ]
            parts = [f"if {_ITERM2_HOOK_PREDICATE}", *hooked, "else", *inline, "end"]
        else:
            parts = [
                f"function fish_title; echo {shlex.quote(title)}; end",
                f"cd {shlex.quote(cwd)}",
                f"printf '%s' {shlex.quote(title_seq)}",
            ]
        startup = joined_startup(startup_argv)
        if startup:
            parts.append(startup)
        return ["fish", "-i", "-C", "; ".join(parts)], {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_shells.py tests/unit/test_fish_title.py tests/unit/test_start.py tests/unit/test_local_iterm2_harness.py -q`
Expected: all passed (`test_dry_run_uses_full_branch_title_but_slug_session` still finds `omc.terminal_title` in the plan because `title argv` is printed separately and the else branch contains it).

- [ ] **Step 5: Gate and commit**

Run: `just check` then `just build`
Expected: green.

```bash
git add src/omc/shells/fish.py tests/unit/test_shells.py tests/unit/test_fish_title.py
git commit -m "feat: make omc start source the fish title hook inside iTerm2" -m "The generated -C command becomes one if/else: inside iTerm2 it presets __omc_title_helper to <python> -m omc, sources the packaged hook, cds and refreshes once; elsewhere (or with OMC_FISH_TITLE_DISABLE=1) today's inline code runs byte for byte. The fish_session fixture scrubs the developer's iTerm2 variables." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Installed-artifact test `tests/unit/test_installed_wheel.py`

**Model:** standard coding tier

**Files:**
- Create: `tests/unit/test_installed_wheel.py`
- Modify: `.omc/config/AGENTS.md` (the "Never run `omc install` / `uv tool install`" bullet gains one sentence)

**Interfaces:**
- Consumes: `tests/unit/_fishpty.drive_fish` (Task 4); `omc shell-integration fish reconcile|status` (Task 5); the hook's `command -s omc` resolution (Task 4).
- Produces: session fixture `wheel` (built once with `uv build --wheel`), fixture `installed` returning `Installed(env: dict[str, str], omc: Path, hook: Path)`; proof that the wheel ships `omc/assets/omc-title.fish` next to `omc/assets/skills/...`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_installed_wheel.py`:

```python
"""The wheel, installed into disposable uv directories, provisions and runs the fish hook.

This is the ONE sanctioned `uv tool install` outside a user decision (.omc/config/AGENTS.md):
every install location is asserted to sit under pytest's tmp BEFORE uv runs.
"""

import json
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from ._fishpty import drive_fish

ROOT = Path(__file__).resolve().parents[2]
SESSION = "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632"
STUB_OMC = """#!/bin/sh
printf '%s\\n' "$*" >> "$OMC_TEST_LOG"
if [ "$1" = title ] && [ "$2" = apply ] && [ -r "$3" ]; then
  IFS= read -r line < "$3"
  printf 'request: %s\\n' "$line" >> "$OMC_TEST_LOG"
fi
exit 0
"""


def _tool(name, hint):
    found = shutil.which(name)
    if found is None:
        pytest.fail(f"the installed-wheel test requires {name}: {hint}")
    return found


def _host_uv_dir(uv, verb):
    """Resolve a host uv directory BEFORE HOME/XDG_* are redirected (spec §7)."""
    cp = subprocess.run([uv, *verb], capture_output=True, text=True, timeout=30)
    assert cp.returncode == 0 and cp.stdout.strip(), (
        f"uv {' '.join(verb)} failed:\n{cp.stderr[-500:]}"
    )
    return cp.stdout.strip()


def _base_env():
    """Process essentials plus the host's uv cache and managed interpreters; no credentials.

    HOME and XDG_CACHE_HOME/XDG_DATA_HOME are redirected by `installed`, which would move
    uv's cache and managed-Python directories into tmp and re-download every wheel per
    session; pin both to the host's locations explicitly.
    """
    uv = _tool("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")
    keep = ("PATH", "TMPDIR", "LANG", "TERM", "USER", "LOGNAME", "UV_PYTHON")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR") or _host_uv_dir(uv, ["cache", "dir"])
    env["UV_PYTHON_INSTALL_DIR"] = os.environ.get("UV_PYTHON_INSTALL_DIR") or _host_uv_dir(
        uv, ["python", "dir"]
    )
    env["UV_NO_PROGRESS"] = "1"
    return env


def _run(argv, env, timeout=300):
    cp = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    assert cp.returncode == 0, f"{argv[:4]!r} failed:\n{cp.stderr[-2000:]}"
    return cp


@pytest.fixture(scope="session")
def wheel(tmp_path_factory):
    uv = _tool("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")
    out = tmp_path_factory.mktemp("omc-wheel")
    _run([uv, "build", "--wheel", "--out-dir", str(out), str(ROOT)], _base_env())
    wheels = list(out.glob("omc-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


@dataclass(frozen=True)
class Installed:
    env: dict[str, str]
    omc: Path
    hook: Path


@pytest.fixture
def installed(tmp_path, wheel):
    uv = _tool("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")
    _tool("fish", "brew install fish (macOS) or sudo apt-get install fish (Ubuntu)")
    private = {
        "HOME": tmp_path / "home",
        "XDG_CONFIG_HOME": tmp_path / "config with $ and spaces",
        "XDG_DATA_HOME": tmp_path / "xdg-data",
        "XDG_CACHE_HOME": tmp_path / "xdg-cache",
        "XDG_STATE_HOME": tmp_path / "xdg-state",
        "OMC_HOME": tmp_path / "omc-home",
        "UV_TOOL_DIR": tmp_path / "uv-tools",
        "UV_TOOL_BIN_DIR": tmp_path / "uv-bin",
    }
    for name, path in private.items():
        assert path.is_relative_to(tmp_path), f"refusing a non-disposable {name}: {path}"
        path.mkdir(parents=True, exist_ok=True)
    env = {**_base_env(), **{name: str(path) for name, path in private.items()}}
    _run([uv, "tool", "install", "--reinstall", str(wheel)], env)
    omc = private["UV_TOOL_BIN_DIR"] / "omc"
    assert omc.is_file(), f"uv did not link {omc}"
    _run([str(omc), "shell-integration", "fish", "reconcile"], env)
    hook = private["XDG_CONFIG_HOME"] / "fish" / "conf.d" / "omc-title.fish"
    assert hook.is_file(), f"the installed CLI did not provision {hook}"
    return Installed(env, omc, hook)


def test_wheel_ships_the_hook_next_to_the_skills(wheel):
    names = zipfile.ZipFile(wheel).namelist()
    assert "omc/assets/omc-title.fish" in names
    assert any(name.startswith("omc/assets/skills/") for name in names)


def test_installed_status_is_owned_and_enabled(installed):
    cp = _run([str(installed.omc), "shell-integration", "fish", "status"], installed.env)
    status = json.loads(cp.stdout)
    assert status["owned"] is True and status["enabled"] is True
    assert status["path"] == str(installed.hook)
    assert installed.hook.read_bytes().startswith(b"# omc-managed fish title integration")


def test_ordinary_fish_prompt_loads_the_installed_hook(installed, tmp_path):
    fish = _tool("fish", "brew install fish")
    git = _tool("git", "install git")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([git, "init", "-q", "-b", "feature/wheel", str(repo)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        subprocess.run([git, "-C", str(repo), "config", key, value], check=True)
    (repo / "f").write_text("x\n")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-qm", "init"], check=True)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "omc").write_text(STUB_OMC)
    (stubs / "omc").chmod(0o755)
    (stubs / "git").symlink_to(git)
    config = Path(installed.env["XDG_CONFIG_HOME"]) / "fish" / "config.fish"
    config.write_text(
        "set -g fish_greeting\n"
        'function fish_prompt; echo P >> "$OMC_PROMPT_LOG"; printf "READY> "; end\n'
    )
    original = config.read_bytes()
    log = tmp_path / "omc.log"
    prompt_log = tmp_path / "prompts"
    env = {
        **installed.env,
        "PATH": str(stubs),  # the installed omc is NOT here: the hook must resolve the stub
        "OMC_TEST_LOG": str(log),
        "OMC_PROMPT_LOG": str(prompt_log),
        "ITERM_SESSION_ID": SESSION,
        "TERM_PROGRAM": "iTerm.app",
        "TERM": "dumb",
    }
    drive_fish(fish, env, ["true"], prompt_log, cwd=repo)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "request: set feature/wheel" not in (
        log.read_text() if log.exists() else ""
    ):
        time.sleep(0.05)
    lines = log.read_text().splitlines()
    applies = [line for line in lines if line.startswith("title apply ")]
    assert len(applies) == 1, lines  # the first prompt dispatched exactly once
    assert applies[0].split(" ", 2)[2].startswith(installed.env["OMC_HOME"] + "/title-request/")
    assert "request: set feature/wheel" in lines
    assert config.read_bytes() == original
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_installed_wheel.py -q`
Expected: PASS is possible already if Tasks 4–5 are in place — so first prove the test bites: temporarily rename `src/omc/assets/omc-title.fish` to `omc-title.fish.off`, run again, expect `test_wheel_ships_the_hook_next_to_the_skills` and `installed` FAIL (`the installed CLI did not provision`), then restore with `git checkout -- src/omc/assets/omc-title.fish && rm src/omc/assets/omc-title.fish.off` (the rename leaves an untracked `.off` file that `git checkout` does not remove).

- [ ] **Step 3: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_installed_wheel.py -q`
Expected: `3 passed` (first run ~30–60 s for the build and install; the host uv cache is reused).

- [ ] **Step 4: Record the carve-out in `.omc/config/AGENTS.md`**

Append this sentence to the "**Never run `omc install` / `uv tool install` on your own initiative.**" bullet, after "...instead of running it.":

```
  The one exception is `tests/unit/test_installed_wheel.py`: it installs a wheel
  it just built (never a checkout path, so no `omc update` is re-rooted) into a
  private `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, with `OMC_HOME`, `HOME` and every
  `XDG_*` asserted to lie under pytest's tmp before uv runs; only the host uv
  cache is shared, so on a cold cache this one `just check` test needs network.
```

In the same file, edit the "Tier *selection*" bullet: replace `\`just check\` (fast gate: unit tests, no LLM/network/Docker)` (wrapped across two lines there) with `\`just check\` (fast gate: unit tests, no LLM/Docker; network only for the installed-wheel test on a cold uv cache)`.

- [ ] **Step 5: Gate and commit**

Run: `just check` then `just build`
Expected: green (Linux CI has uv and fish installed by `.github/workflows/ci.yml`).

```bash
git add tests/unit/test_installed_wheel.py .omc/config/AGENTS.md
git commit -m "test: install the built wheel into disposable uv dirs and load its fish hook" -m "Builds the wheel once per session, uv tool installs it with UV_TOOL_DIR/UV_TOOL_BIN_DIR/OMC_HOME/HOME/XDG_* under tmp, runs the installed reconcile and drives an ordinary fish -i under a PTY whose first prompt calls a stub omc. Records the single sanctioned uv tool install in AGENTS.md." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Native tier on the private instance — migration, branch transitions, ordinary providers

**Model:** heavy coding tier

**Files:**
- Modify: `tests/local/private_iterm.py` (add `provision_hook`, `init_repo`, `claude_private_auth`)
- Modify: `tests/local/test_iterm2_title.py` (migrate off the user's app)
- Create: `tests/local/test_ordinary_fish_title.py`
- Modify: `tests/unit/test_local_iterm2_harness.py`

**Interfaces:**
- Consumes: `private_iterm` fixture, `connect`, `ensure_window`, `until`, `require_sdk`, `require_tool`, `scoped_env` (Task 1); `fish_hook_path()` (Task 5); `omc title` (Task 3); the hook (Task 4); `FishShell.build_invocation` (Task 7); existing `_codex_account`, `_exit_provider_tui`, `_screen`, `_wait_screen`, `_provider_env_file`, `_provider_tab_command` in `tests/local/test_iterm2_title.py`.
- Produces (test-only):
  - `private_iterm.provision_hook(home: Path) -> Path` — copies the packaged asset to `<home>/.config/fish/conf.d/omc-title.fish` (idempotent).
  - `private_iterm.init_repo(path: Path, branch: str) -> Path` — disposable git repo with one commit on `branch`.
  - `private_iterm.claude_private_auth(home: Path, repo: Path, claude: str) -> Path | None` — writes `<home>/.claude.json` (onboarding complete, trust for `repo`); returns `None` when `claude auth status --json` under the private HOME is already logged in via `claude.ai`, else a 0600 fish file `<home>/.claude-auth.fish` exporting `CLAUDE_CODE_OAUTH_TOKEN` read from the Keychain item `Claude Code-credentials` (never printed; caller unlinks it).
  - `test_iterm2_title._connect(iterm2, instance, fish) -> (connection, app, window)`; `_tab(window, tabs, command)`; `_cleanup(tabs) -> list[str]`; `_title(tab, expected)`; `_provider_env_file(tmp_path, provider, adapter, config_root=None)`; `_provider_tab_command(shell_argv, private_env, returned, auth_file=None)`.

- [ ] **Step 1: Update the deterministic harness unit tests first (they pin the new shapes)**

In `tests/unit/test_local_iterm2_harness.py`:

Replace `test_iterm2_connections_do_not_reuse_app_from_closed_event_loop` with:

```python
def test_private_connect_invalidates_app_and_scopes_env_per_call(tmp_path):
    from dataclasses import replace

    from tests.local.private_iterm import PrivateITerm, connect, new_identity

    identity = new_identity(tmp_path, "0123456789ab")
    fake_socket = tmp_path / "socket"
    fake_socket.touch()
    instance = PrivateITerm(replace(identity, socket=fake_socket), 1)
    state = SimpleNamespace(created=0, invalidations=0, homes=[])

    class Connection:
        websocket = SimpleNamespace(remote_address="/private/socket")

        @staticmethod
        async def async_create():
            os.environ["ITERM2_COOKIE"] = "private-cookie"
            state.created += 1
            state.homes.append(
                (os.environ["HOME"], os.environ["IT2_SUITE"], os.environ["IT2_APP_PATH"])
            )
            return Connection()

    async def get_app(connection):
        return SimpleNamespace(terminal_windows=[])

    def invalidate_app():
        state.invalidations += 1

    sdk = SimpleNamespace(
        Connection=Connection,
        async_get_app=get_app,
        app=SimpleNamespace(invalidate_app=invalidate_app),
    )
    before_home = os.environ.get("HOME")
    asyncio.run(connect(sdk, instance))
    asyncio.run(connect(sdk, instance))
    assert state.created == 2 and state.invalidations == 2
    assert state.homes == [(str(identity.home), identity.suite, str(identity.copy))] * 2
    assert os.environ.get("HOME") == before_home  # scoped around async_create, never process-wide
    assert "ITERM2_COOKIE" not in os.environ  # SDK auth writes are scrubbed with the scope


def test_private_connect_refuses_tcp_fallback(tmp_path):
    from dataclasses import replace

    from tests.local.private_iterm import PrivateITerm, connect, new_identity

    identity = new_identity(tmp_path, "0123456789ab")
    fake_socket = tmp_path / "socket"
    fake_socket.touch()
    instance = PrivateITerm(replace(identity, socket=fake_socket), 1)

    class Connection:
        websocket = SimpleNamespace(remote_address=("127.0.0.1", 1912))

        @staticmethod
        async def async_create():
            return Connection()

    sdk = SimpleNamespace(
        Connection=Connection,
        async_get_app=None,
        app=SimpleNamespace(invalidate_app=lambda: None),
    )
    with pytest.raises(pytest.fail.Exception, match="TCP"):
        asyncio.run(connect(sdk, instance))


def test_private_connect_refuses_missing_socket(tmp_path):
    from tests.local.private_iterm import PrivateITerm, connect, new_identity

    instance = PrivateITerm(new_identity(tmp_path, "0123456789ab"), 1)
    with pytest.raises(pytest.fail.Exception, match="socket missing"):
        asyncio.run(connect(SimpleNamespace(app=SimpleNamespace(invalidate_app=lambda: None)), instance))
```

Add `import os` and `import shlex` at the module top of the harness test.

Delete `test_native_claude_checkout_requires_preexisting_trust` and `test_native_claude_branch_uses_branch_show_current` (the functions they test go away). Replace `test_cleanup_error_keeps_original_provider_failure` with:

```python
def test_cleanup_error_keeps_original_provider_failure():
    from tests.local.test_iterm2_title import _cleanup, _report_cleanup_errors

    class ClosedTab:
        tab_id = "disposable"

        async def async_close(self, force):
            raise RuntimeError("already closed")

    async def fail_and_cleanup():
        try:
            raise ValueError("provider failed")
        finally:
            _report_cleanup_errors(await _cleanup([("disposable", ClosedTab())]))

    with pytest.raises(ValueError, match="provider failed") as raised:
        asyncio.run(fail_and_cleanup())
    assert any("RuntimeError" in note for note in raised.value.__notes__)
```

Add:

```python
def test_provider_env_file_can_place_codex_home_under_a_private_root(tmp_path):
    from omc.providers.codex import CodexProvider
    from tests.local.test_iterm2_title import _provider_env_file

    root = tmp_path / "private home"
    root.mkdir()
    private_env, config = _provider_env_file(tmp_path, "codex", CodexProvider(), config_root=root)
    assert config == root / "codex-config" and config.is_dir()
    assert f"set -gx CODEX_HOME {shlex.quote(str(config))}\n" in private_env.read_text()


def test_provider_tab_command_sources_auth_after_private_env(tmp_path):
    from tests.local.test_iterm2_title import _provider_tab_command

    command = _provider_tab_command(
        ["fish", "-i", "-C", "true"], tmp_path / "env.fish", tmp_path / "ret", auth_file=tmp_path / "auth.fish"
    )
    body = shlex.split(command)[3]
    assert body.index("env.fish") < body.index("auth.fish") < body.index("true")
    assert "printf returned" in body


def test_claude_private_auth_writes_trust_and_returns_none_when_logged_in(tmp_path, monkeypatch):
    import json

    from tests.local import private_iterm

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]["HOME"]))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))

    monkeypatch.setattr(private_iterm.subprocess, "run", run)
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    assert private_iterm.claude_private_auth(home, repo, "/usr/local/bin/claude") is None
    profile = json.loads((home / ".claude.json").read_text())
    assert profile["hasCompletedOnboarding"] is True
    assert profile["projects"][str(repo)]["hasTrustDialogAccepted"] is True
    assert calls == [(["/usr/local/bin/claude", "auth", "status", "--json"], str(home))]
```

- [ ] **Step 2: Run the harness unit tests to verify they fail**

Run: `uv run pytest tests/unit/test_local_iterm2_harness.py -q`
Expected: the three `test_private_connect_*` tests PASS already (they pin Task 1 behavior; the scrub assertion is the only new claim and Task 1 already satisfies it); the rest FAIL — `_provider_env_file() got an unexpected keyword argument 'config_root'`, `_cleanup() missing 1 required positional argument: 'original'`, `module 'tests.local.private_iterm' has no attribute 'claude_private_auth'`; `ImportError` for `_native_claude_env`/`_trusted_claude_checkout`/`_current_branch` once those tests are deleted is expected and correct.

- [ ] **Step 3: Extend `tests/local/private_iterm.py`**

Append:

```python
def provision_hook(home: Path) -> Path:
    """The private HOME's conf.d receives the packaged hook (spec §7 step 2)."""
    from omc.fish_integration import fish_hook_path

    target = home / ".config" / "fish" / "conf.d" / "omc-title.fish"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fish_hook_path(), target)
    return target


def init_repo(path: Path, branch: str) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    git = ["git", "-C", str(path)]
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        subprocess.run([*git, "config", key, value], check=True)
    (path / "README").write_text("disposable\n")
    subprocess.run([*git, "add", "README"], check=True)
    subprocess.run([*git, "commit", "-qm", "initial"], check=True)
    return path


def _exclusive_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def claude_private_auth(home: Path, repo: Path, claude: str) -> Path | None:
    """~/.claude.json is HOME-relative: generate onboarding + trust in the private HOME.

    Returns None when the first-party Keychain login is still visible from that HOME;
    otherwise a 0600 fish file exporting CLAUDE_CODE_OAUTH_TOKEN (the caller sources it
    inside the tab and unlinks it on teardown). The token never appears in argv or logs.
    """
    import json

    profile = home / ".claude.json"
    profile.write_text(
        json.dumps(
            {"hasCompletedOnboarding": True, "projects": {str(repo): {"hasTrustDialogAccepted": True}}}
        )
    )
    profile.chmod(0o600)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
    }
    env["HOME"] = str(home)
    status = subprocess.run(
        [claude, "auth", "status", "--json"], env=env, capture_output=True, text=True, timeout=15, check=False
    )
    try:
        parsed = json.loads(status.stdout)
    except json.JSONDecodeError:
        parsed = {}
    if status.returncode == 0 and parsed.get("loggedIn") and parsed.get("authMethod") == "claude.ai":
        return None
    keychain = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True, timeout=10, check=False,
    )  # fmt: skip
    try:
        token = json.loads(keychain.stdout)["claudeAiOauth"]["accessToken"]
    except (ValueError, KeyError, TypeError):
        token = ""
    if keychain.returncode or not isinstance(token, str) or not token:
        pytest.fail("native Claude profile needs first-party login: run claude auth login")
    auth_file = home / ".claude-auth.fish"
    _exclusive_text(auth_file, f"set -gx CLAUDE_CODE_OAUTH_TOKEN {shlex.quote(token)}\n")
    return auth_file
```

- [ ] **Step 4: Migrate `tests/local/test_iterm2_title.py` off the user's app**

Apply these edits (keep everything else):

1. Imports: add `from tests.local.private_iterm import (claude_private_auth, connect, ensure_window, init_repo, provision_hook, require_sdk, require_tool, until)`; drop `import platform` AND `import json` (both become unused: `_requirements` no longer checks the platform and `_native_claude_repo` was the only `json` user; ruff F401 would fail `just build`); keep `sys` (`_report_cleanup_errors` uses `sys.exception()`), `os`, `re`, `subprocess`, `tempfile` (all still used by `_codex_account`). After the new import block add `_until = until  # one bounded poller; test_ordinary_fish_title imports until directly` and delete the local `async def _until(...)` definition (identical body to `private_iterm.until`).

2. Replace `_requirements`:

```python
def _requirements(provider=None):
    iterm2 = require_sdk()
    for tool in ("fish", "git", provider):
        if tool:
            require_tool(tool, f"install {tool} and put it on PATH")
    return iterm2
```

3. Delete `_trusted_claude_checkout`, `_native_claude_env`, `_current_branch`, `_native_claude_repo`.

4. `_provider_env_file(tmp_path, provider, adapter, config_root=None)`: the codex branch uses `config = (config_root or tmp_path) / f"{provider}-config"`.

5. `_provider_tab_command(shell_argv, private_env, returned, auth_file=None)`: prefix becomes `source <private_env>; ` plus `source <auth_file>; ` when given, then the body, then the `printf returned` marker.

6. Replace `_connect`:

```python
async def _connect(iterm2, instance, fish):
    connection, app = await connect(iterm2, instance)
    window = await ensure_window(iterm2, connection, app, fish)
    return connection, app, window
```

7. Replace `_cleanup` (no focus restore — the private instance is never selected):

```python
async def _cleanup(tabs):
    failures = []
    for tab_id, tab in reversed(tabs):
        try:
            assert tab.tab_id == tab_id
            await asyncio.wait_for(tab.async_close(force=True), 10)
        except Exception as exc:
            failures.append(f"tab {tab_id}: {type(exc).__name__}")
    return failures
```

8. Replace `_test_title` and its caller:

```python
def test_caller_target_split_pane_and_competing_osc(private_iterm):
    iterm2 = _requirements()
    fish = shutil.which("fish")

    async def run():
        _connection, _app, window = await _connect(iterm2, private_iterm, fish)
        tabs = []
        try:
            target = await _tab(window, tabs, shlex.join([fish, "-i"]))
            other = await _tab(window, tabs, shlex.join([fish, "-i"]))
            await other.async_set_title("omc-control")
            split = await target.current_session.async_split_pane(vertical=True)
            assert split is not None, "iTerm2 did not create a split test pane"
            hostile = "feature/cost$USD(parent)\\(session.name)"
            command = shlex.join(["omc", "title", "set", "--", hostile])
            await split.async_send_text(command + "; printf '\\nOMC_TITLE_DONE:%s\\n' $status\n")
            await _wait_screen(split, "OMC_TITLE_DONE:0")
            await _title(target, hostile)  # the split pane targets ITS tab, never the other one
            assert await other.async_get_variable("title") == "omc-control"
            for code in (0, 1, 2):
                await split.async_inject(f"\x1b]{code};competing-title\x07".encode())
            await asyncio.sleep(0.5)
            assert await target.async_get_variable("title") == hostile
            assert await other.async_get_variable("title") == "omc-control"
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    asyncio.run(run())
```

9. Replace the provider test so both providers use a disposable repo under the private HOME and the generated `omc start` fish command, on the private instance:

```python
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_provider_title_during_real_response_and_after_exit(private_iterm, tmp_path, provider):
    iterm2 = _requirements(provider)
    fish = shutil.which("fish")
    home = private_iterm.identity.home
    repo = init_repo(home / "repos" / f"start-{provider}", TITLE)
    adapter = get_provider(provider)
    provider_argv = adapter.session_argv(session_name="iterm2-host-acceptance", model="", seed=PROMPT)
    provider_argv[0] = shutil.which(provider)
    if provider == "claude":
        provider_argv[1:1] = ["--safe-mode", "--tools", "", "--strict-mcp-config", "--no-chrome"]
    else:
        provider_argv[1:1] = ["--no-alt-screen", "--sandbox", "read-only", "--ask-for-approval", "never"]
    # The same generated startup command as omc start; only seed and cwd differ.
    shell_argv, _ = FishShell().build_invocation(
        cwd=str(repo), title=TITLE, startup_argv=provider_argv,
        title_seq=f"\x1b]0;{TITLE}\x07", title_argv=terminal_title_argv(),
    )  # fmt: skip
    shell_argv[0] = fish
    private_env, config = _provider_env_file(tmp_path, provider, adapter, config_root=home)
    auth_file = claude_private_auth(home, repo, provider_argv[0]) if provider == "claude" else None
    returned = tmp_path / f"{provider}-shell-returned"
    command = _provider_tab_command(shell_argv, private_env, returned, auth_file=auth_file)

    async def run():
        _connection, _app, window = await _connect(iterm2, private_iterm, fish)
        tabs = []
        try:
            tab = await _tab(window, tabs, command)
            session = tab.current_session
            await _title(tab, TITLE)
            if provider == "codex":
                await _codex_trust_or_answer(session, repo)
            await _wait_screen(session, ANSWER, timeout=90)
            assert await tab.async_get_variable("title") == TITLE
            await _exit_provider_tui(session, provider)

            async def shell_returned():
                return returned.exists()

            await _until("provider exit back to fish", shell_returned, timeout=20)
            assert returned.read_text() == "returned"
            assert await tab.async_get_variable("title") == TITLE
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    try:
        with _codex_account(config) if provider == "codex" else nullcontext():
            asyncio.run(run())
    finally:
        private_env.unlink(missing_ok=True)
        if auth_file is not None:
            auth_file.unlink(missing_ok=True)
        if config is not None:
            shutil.rmtree(config, ignore_errors=True)


async def _codex_trust_or_answer(session, repo):
    async def answer_or_trust():
        screen = await _screen(session)
        if "API key login is disabled by this workspace" in screen:
            pytest.fail("Codex requires ChatGPT account auth in this workspace: run just codex-login")
        if str(repo) in screen and "Trust this folder?" in screen and "Trust and continue" in screen:
            return "trust"
        if ANSWER in screen:
            return "answer"
        return None

    state = await _until("Codex trust prompt or model answer", answer_or_trust, timeout=90)
    if state == "trust":
        await session.async_send_text("\r")
```

The `omc` inside the private tabs is this checkout's `.venv/bin/omc` (PATH from `open --env`), so `omc title set` above needs no absolute path.

- [ ] **Step 5: Write the ordinary-shell native cases**

Create `tests/local/test_ordinary_fish_title.py`:

```python
"""Ordinary fish prompts with the hook in the private iTerm2: transitions, codex, codex resume, claude."""

import asyncio
import shlex
import shutil
from contextlib import nullcontext
from pathlib import Path

import pytest

from tests.local.private_iterm import (
    claude_private_auth,
    connect,
    ensure_window,
    init_repo,
    provision_hook,
    require_sdk,
    require_tool,
    until,
)
from tests.local.test_iterm2_title import (
    ANSWER,
    PROMPT,
    _cleanup,
    _codex_account,
    _codex_trust_or_answer,
    _exit_provider_tui,
    _report_cleanup_errors,
    _screen,
    _tab,
    _title,
    _wait_screen,
)

pytestmark = pytest.mark.local_iterm2

BRANCH = "feature/ordinary-fish-provider"


def _fish_tab_command(fish, env):
    """Only environment and `fish -i`; everything else is typed at the prompt."""
    return shlex.join(["/usr/bin/env", *[f"{k}={v}" for k, v in env.items()], fish, "-i"])


async def _typed(session, text):
    await session.async_send_text(text + "\n")


async def _marker(session, path):
    """Type a marker write; wait for the file: the previous foreground command is done."""
    await _typed(session, f"echo done > {shlex.quote(str(path))}")

    async def exists():
        return path.exists()

    await until(f"marker {path.name}", exists, timeout=30)


def test_branch_transitions_and_release_restores_dynamic_default(private_iterm):
    iterm2 = require_sdk()
    fish = require_tool("fish", "brew install fish")
    home = private_iterm.identity.home
    provision_hook(home)
    repo = init_repo(home / "repos" / "transitions", BRANCH)

    async def run():
        connection, app = await connect(iterm2, private_iterm)
        window = await ensure_window(iterm2, connection, app, fish)
        tabs = []
        try:
            tab = await _tab(window, tabs, _fish_tab_command(fish, {}))
            session = tab.current_session
            await _typed(session, f"cd {shlex.quote(str(repo))}")
            await _title(tab, BRANCH)  # branch → set
            await _typed(session, "git switch --detach -q")
            await _marker(session, home / "m-detached")
            await asyncio.sleep(1.0)
            assert await tab.async_get_variable("title") == BRANCH  # detached → remembered branch
            await _typed(session, "cd /")
            await _marker(session, home / "m-outside")

            async def released():  # outside → release: the API override is GONE, so an OSC
                await session.async_inject(b"\x1b]0;osc-moved\x07")  # write moves the title again
                return await tab.async_get_variable("title") == "osc-moved"

            await until("release to restore the dynamic default", released, timeout=20)
            await _typed(session, f"cd {shlex.quote(str(repo))}")
            await _title(tab, BRANCH)  # back into the (detached) worktree → remembered branch
            for code in (0, 1, 2):
                await session.async_inject(f"\x1b]{code};competing\x07".encode())
            await asyncio.sleep(0.5)
            assert await tab.async_get_variable("title") == BRANCH  # pin active again
            await _typed(session, f"git switch -q {shlex.quote(BRANCH)}")
            await _marker(session, home / "m-branch")
            assert await tab.async_get_variable("title") == BRANCH
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    asyncio.run(run())


def _codex_session_id(codex_home: Path) -> str:
    rollouts = sorted(
        codex_home.glob("sessions/**/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime
    )
    assert rollouts, f"no Codex rollout under {codex_home}/sessions"
    return "-".join(rollouts[-1].stem.split("-")[-5:])  # rollout-<timestamp>-<uuid>


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_ordinary_prompt_keeps_branch_through_plain_provider_runs(private_iterm, provider):
    iterm2 = require_sdk()
    fish = require_tool("fish", "brew install fish")
    binary = require_tool(provider, f"install the {provider} CLI")
    home = private_iterm.identity.home
    provision_hook(home)
    repo = init_repo(home / "repos" / f"ordinary-{provider}", BRANCH)
    env = {"PATH": f"{Path(binary).parent}:{private_iterm.identity.checkout / '.venv' / 'bin'}:/usr/bin:/bin"}
    config = None
    auth_file = None
    if provider == "codex":
        config = home / "codex-config-ordinary"
        config.mkdir(mode=0o700)
        env["CODEX_HOME"] = str(config)
        flags = "--sandbox read-only --ask-for-approval never"
    else:
        auth_file = claude_private_auth(home, repo, binary)
        flags = '--safe-mode --tools "" --strict-mcp-config --no-chrome'

    async def run():
        connection, app = await connect(iterm2, private_iterm)
        window = await ensure_window(iterm2, connection, app, fish)
        tabs = []
        try:
            tab = await _tab(window, tabs, _fish_tab_command(fish, env))
            session = tab.current_session
            if auth_file is not None:
                await _typed(session, f"source {shlex.quote(str(auth_file))}")
            await _typed(session, f"cd {shlex.quote(str(repo))}")
            await _title(tab, BRANCH)
            # Plain provider, typed at the prompt; it writes its own activity titles.
            await _typed(session, f"{provider} {flags}")
            if provider == "codex":
                await _wait_screen(session, "OpenAI Codex", timeout=60)
            else:
                await _wait_screen(session, "Claude Code", timeout=60)
            await session.async_send_text(PROMPT)
            await asyncio.sleep(0.25)
            await session.async_send_text("\r")
            if provider == "codex":
                await _codex_trust_or_answer(session, repo)
            await _wait_screen(session, ANSWER, timeout=120)
            assert await tab.async_get_variable("title") == BRANCH  # during provider activity
            await _exit_provider_tui(session, provider)
            await _marker(session, home / f"m-{provider}-exit")
            assert await tab.async_get_variable("title") == BRANCH  # after exit
            if provider == "codex":
                session_id = _codex_session_id(config)
                await _typed(session, f"codex resume {shlex.quote(session_id)} {flags}")
                await _wait_screen(session, ANSWER, timeout=90)  # the resumed transcript
                assert await tab.async_get_variable("title") == BRANCH
                await _exit_provider_tui(session, provider)
                await _marker(session, home / "m-codex-resume-exit")
                assert await tab.async_get_variable("title") == BRANCH
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    try:
        with _codex_account(config) if provider == "codex" else nullcontext():
            asyncio.run(run())
    finally:
        if auth_file is not None:
            auth_file.unlink(missing_ok=True)
        if config is not None:
            shutil.rmtree(config, ignore_errors=True)
```

- [ ] **Step 6: Run the unit harness tests, then the native tier**

Run: `uv run pytest tests/unit/test_local_iterm2_harness.py tests/unit/test_private_iterm.py -q`
Expected: all passed.

Run: `just check`
Expected: green (`tests/local/*` collected, marked, deselected).

Run: `just iterm2-tests -k "probe or caller or transitions"`
Expected: `3 passed`; no `omc-<hex>` residue afterwards (see Task 1 step 6). Then with credentials available (`just codex-login` done once; Claude logged in with `claude auth login`):

Run: `just iterm2-tests`
Expected: all passed. A missing provider CLI or login fails with the exact hint, never skips. Confirm nothing connected to the user's iTerm2: its tab titles are unchanged and `defaults read com.googlecode.iterm2` was never invoked by the tests (`grep -rn googlecode tests/local` shows only comments).

- [ ] **Step 7: Commit**

```bash
git add tests/local/private_iterm.py tests/local/test_iterm2_title.py tests/local/test_ordinary_fish_title.py tests/unit/test_local_iterm2_harness.py
git commit -m "test: run the native tier on the private iTerm2 instance" -m "Migrates the caller-tab and provider cases off the user's app (no focus changes, disposable repo in the private HOME, generated .claude.json and a 0600 Keychain token file only when the private HOME is not logged in) and adds the ordinary-shell cases: branch→detached→outside→branch with an OSC proof that release restores the dynamic default, then plain codex, codex resume and claude keeping the branch title." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: README, justfile, pyproject marker text

**Model:** standard coding tier

**Files:**
- Modify: `README.md:59-61` (the two fish/iTerm2 paragraphs)
- Modify: `justfile:8-10`
- Modify: `pyproject.toml:42`

**Interfaces:**
- Consumes: the user-facing names fixed by Tasks 3–7: `omc shell-integration fish enable|disable|status|reconcile`, `omc title set -- <branch>`, `OMC_FISH_TITLE_DISABLE=1`, the hook path.
- Produces: documentation only.

- [ ] **Step 1: Write the failing doc test**

Append to `tests/unit/test_cli.py`:

```python
def test_readme_documents_every_user_facing_title_surface():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    for needle in (
        "omc shell-integration fish reconcile",
        "omc shell-integration fish disable",
        "omc shell-integration fish status",
        "OMC_FISH_TITLE_DISABLE=1",
        "omc title set -- <branch>",
        "conf.d/omc-title.fish",
        "last writer wins",
        "nested shell",
        "just iterm2-tests",
    ):
        assert needle in readme, needle
    assert "omc title reconcile" not in readme  # the dropped first attempt's command
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_cli.py::test_readme_documents_every_user_facing_title_surface -q`
Expected: FAIL on `omc shell-integration fish reconcile`.

- [ ] **Step 3: Rewrite the README paragraphs (spec §9)**

Replace README line 59 (the paragraph starting "On iTerm2, enable its Python API...") with:

```markdown
**Tab titles in iTerm2 (fish).** On macOS, `omc install` and `omc update` provision an omc-owned hook at `$XDG_CONFIG_HOME/fish/conf.d/omc-title.fish` (default `~/.config/fish/conf.d/omc-title.fish`). Every interactive fish shell in iTerm2 then pins its tab title to the full, literal Git branch: one `git symbolic-ref` per prompt decides, the title write runs in the background through `omc title apply`, and the prompt never waits on Python. Plain `codex`, `codex resume <id>`, and `claude` keep that title while they write their own activity titles and after they exit. A detached HEAD keeps the last branch seen in that worktree; leaving Git releases the pin and iTerm2's live default title returns. `omc start` sources the same hook (it works even when the hook was never provisioned or was disabled). The hook never defines `fish_title` and never edits `config.fish`. If you upgraded from a release that predates the hook, run `omc shell-integration fish reconcile` once: the old updater cannot run the new post-install step. Manage it with `omc shell-integration fish enable|disable|status|reconcile`: `omc shell-integration fish status` prints one JSON line (`enabled`, `installed`, `path`, `owned`), and `omc shell-integration fish disable` is persistent and affects ordinary shells only (`omc start` still titles its tab). `OMC_FISH_TITLE_DISABLE=1` disables the hook for one shell and makes `omc start` fall back to its inline title code. When the iTerm2 API write fails (for example while Python API access is unauthorized) the next prompt prints one hint and retries once per failure; `omc title set -- <branch>` is the manual retry and shows the diagnostic directly. Ownership is last writer wins per tab: two panes on different branches show whichever changed most recently, a `release` from any pane unpins the whole tab, and after a nested shell (plain `fish`, `su`, or an `omc start` session started from a hook-enabled fish) exits, the parent's next prompt is a no-op until its branch changes, it `cd`s into another repository, or you run `omc title set -- <branch>`. Outside iTerm2 (or with an unauthorized API) `omc start` warns and emits a best-effort OSC title. For local quick tests, install fish (`brew install fish` on macOS or `sudo apt-get install fish` on Ubuntu).
```

Replace README line 61 (the paragraph starting "To verify the iTerm2 behavior...") with:

```markdown
To verify the iTerm2 behavior natively, run `just iterm2-tests` on a macOS desktop with iTerm2 3.7.3 installed at `/Applications/iTerm.app`, fish, and your terminal allowed to control iTerm2 (System Settings → Privacy & Security → Automation). The tier copies the app bundle to `/private/tmp/omc-<hex>/iTerm.app`, launches it hidden under a private `-suite` and a private HOME, connects the stock Python SDK over that instance's socket, and never attaches to, reads from, or writes to your running iTerm2. It exercises a split pane with competing title writes, the branch→detached→outside→branch sequence, and ordinary prompts running plain `codex`, `codex resume`, and `claude`; Codex uses the dedicated `omc-e2e-codex-auth` Docker volume from `just codex-login`, Claude uses your existing `claude auth login` (a token is copied into a 0600 file inside the private HOME only when that HOME cannot see the Keychain login, and removed afterwards). Teardown quits the private instance, deletes its preference domains and the temporary directory, and reports any leftover. `just check` and Docker E2E never launch a native app.
```

- [ ] **Step 4: justfile and pyproject**

In `justfile` replace lines 8–10 with:

```just
# macOS native acceptance: a PRIVATE copy of iTerm2 (never your running app), fish, real provider TUIs.
iterm2-tests *args:
    uv run pytest -m local_iterm2 -q tests/local {{args}}
```

In `pyproject.toml` replace the `local_iterm2` marker line with:

```toml
    "local_iterm2: macOS native tier — launches a private iTerm2 instance and real provider TUIs (just iterm2-tests)",
```

- [ ] **Step 5: Run the doc test and the gates**

Run: `uv run pytest tests/unit/test_cli.py -q` then `just check` then `just build`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add README.md justfile pyproject.toml tests/unit/test_cli.py
git commit -m "docs: describe the fish title hook, its management commands and the private native tier" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Whole-branch review against the spec and AGENTS.md

**Model:** top tier

**Files:**
- Read: every file touched by Tasks 1–10; `docs/superpowers/specs/2026-09-30-ordinary-fish-branch-title-design.md`; `.omc/config/AGENTS.md`.
- Modify: only what the review finds (each fix lands with its own red→green test).

**Interfaces:**
- Consumes: the whole branch (`git log --oneline a16c6a4..HEAD`, `git diff a16c6a4..HEAD`).
- Produces: a review verdict in the final report; fixes committed with the same trailer.

- [ ] **Step 1: Spec walk**

For each spec section, name the file and test that implement it and check the literal values:
- §2: activation regex, `TMUX`/`STY`, `functions -a` enumeration, state reset vs `__omc_title_helper`, marker hint once per failure, git exit table (0/1/128/other), request file name `<uuid>-<pid>`, `echo` + redirection only, `>/dev/null 2>&1 &` + `disown`, `command -s omc` at refresh, one hint per shell, first prompt = first refresh.
- §3: banner exemption, lazy dispatch, `__main__.py`, exit codes, `apply` lock/re-read/`.applied`/atomic write, `validate_title` shared, `iterm2_session_id` shared, worker argv, `release` ordering, fixed worker stderr line, marker content and cooldown, success removes marker.
- §4: no `fish_exit` handler; documented limitations in README.
- §5: `importlib.resources`, atomic copy, prefix ownership, `Refusal` for symlink/parent symlink/unowned, XDG absolute-only + `Path.home()` fallback, opt-out file, installer ordering (`→ provisioning fish integration`, `uv tool dir --bin`, fallback, 30 s, verbatim stderr, non-aborting, distinct final line, exit-code rule, non-macOS skip), uninstall owned-only + running-shells note.
- §6: pure builder, byte-identical else branch, `set -g __omc_title_helper <python> -m omc`, `source <asset>`, `cd`, `__omc_title_refresh`, unchanged bash/zsh/sh.
- §7: no skips anywhere (`grep -rn "pytest.skip\|mark.skip\|skipif" tests/` must print nothing), `tests/local` marker guard, `fish_session` scrub, installed-wheel disposable dirs, native steps 1–6, cases list.
- §9: README needles.

- [ ] **Step 2: Budget and boundary checks**

Run and record:

```bash
wc -l src/omc/title.py src/omc/fish_integration.py src/omc/assets/omc-title.fish src/omc/__main__.py
git diff --stat a16c6a4..HEAD -- src/omc/installer.py src/omc/cli/__init__.py src/omc/shells/fish.py src/omc/terminals.py src/omc/terminal_title.py src/omc/iterm2_title.py
wc -l tests/local/private_iterm.py tests/local/conftest.py
grep -rn "import subprocess" src/omc | grep -v toolctx.py   # must be empty
grep -rn "pytest.skip\|mark.skip\|skipif" tests/            # must be empty
grep -rn "googlecode" tests/local/                           # comments only
grep -rn "monkeypatch" tests/local/                          # must be empty (no SDK monkeypatch)
```

Expected: new product code near 500 lines total; `tests/local/private_iterm.py` under 600 lines; the three greps empty (the `googlecode` one shows comments only).

- [ ] **Step 3: Full gates**

Run: `just check`, `just build`, `just iterm2-tests`
Expected: all green; the native run leaves no `omc-<hex>` residue (Task 1 step 6 checks).

- [ ] **Step 4: Fix and record**

For every finding: write the failing test, fix, run, commit (`fix: …` with the trailer). Then write the verdict: spec coverage table, budget numbers, gate results, and any deliberate deviations (this plan's known ones: the worktree key is also computed on a branch *change*, not only when detached; `-PromptOnQuit NO` added to the launch flags; the final installer line takes `<v>` from the fresh CLI's `--version` and omits it when unavailable; when `require_tools` aborts `omc update` after `post_install`, the fish outcome was already narrated (`→` line + relayed stderr) but the final `✓ … · ✗ …` line is not printed — the abort path preempts it).

---

## Self-review notes (run before handing this plan over)

- Spec coverage: §1 rules → Global Constraints and Task 11 step 2; §2 → Task 4; §3 → Tasks 2–3; §4 → Tasks 4 (no `fish_exit`) and 10 (README); §5 → Tasks 5–6; §6 → Task 7; §7 unit → Tasks 3–8, installed artifact → Task 8, native → Tasks 1 and 9; §8 reuse map → Tasks 4, 5, 6, 8 name their backup sources; §9 → Task 10.
- Type consistency: `iterm2_session_id`, `validate_title`, `worker_argv`, `release_title` (Task 2) are the names Tasks 3, 7 use; `fish_hook_path`, `managed_fish_path`, `is_owned`, `remove_owned_hook`, `run_fish_integration` (Task 5) are the names Tasks 6–9 use; `connect`, `ensure_window`, `until`, `require_sdk`, `require_tool`, `PrivateITerm`, `Identity`, `new_identity` (Task 1) are the names Task 9 uses; `drive_fish` (Task 4) is what Task 8 imports.
- Review Focus lines 1–5 each name the task and test that pins them.
