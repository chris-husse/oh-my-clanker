"""The stage-hook unit tests' harness: real git repos and worktrees, hooks, and real
stage runs with their cleanup (the `nowatch` and `stage` fixtures live in conftest.py)."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
from pathlib import Path
from unittest import mock

from omc import stagerun
from omc.toolctx import ToolContext

from . import _mutexproc
from ._stubs import host_bin

ANSWER_KEYS = {
    "stage",
    "state",
    "run",
    "log",
    "instructions",
    "elapsed",
    "quiet",
    "tail",
    "reason",
    "fix_cycles",
    "warnings",
}


def hermetic_env() -> dict[str, str]:
    """The host environment without host git state, omc overrides or host git config.

    GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE from a hook context would redirect every git
    call, and OMC_*_BIN would swap the tools ToolContext.from_env picks; drop them all,
    as tests/unit/test_worktrunk_real.py does. Identity is set per repo by git_repo."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DIRENV_", "OMC_", "WORKTRUNK_", "GIT_"))
    }
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def stage_ctx(tmp_path: Path) -> ToolContext:
    """A ToolContext over os.environ whose stage logs and omc home live under `tmp_path`."""
    return ToolContext.from_env(
        {
            **os.environ,
            "OMC_STAGE_ROOT": str(tmp_path / "stages"),
            "OMC_HOME": str(tmp_path / "home"),
        }
    )


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [host_bin("git"), *args], cwd=cwd, env=hermetic_env(), check=True, capture_output=True
    )


def git_repo(path: Path) -> Path:
    """`path` as a git repo with one commit on `main`."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    _git("config", "user.email", "t@t", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    (path / "f.txt").write_text("x\n")
    _git("add", ".", cwd=path)
    _git("commit", "-qm", "init", cwd=path)
    return path


def commit_all(repo: Path, message: str = "wip") -> None:
    """Commit every change in `repo`, untracked files included."""
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)


def tracked_changes(repo: Path) -> str:
    """`git status --porcelain -uno`: what makes watch skip its sync."""
    return subprocess.run(
        [host_bin("git"), "status", "--porcelain", "-uno"],
        cwd=repo,
        env=hermetic_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def add_worktree(repo: Path, path: Path, branch: str) -> Path:
    """A linked worktree of `repo` at `path` on a new `branch`."""
    _git("worktree", "add", "-q", "-b", branch, str(path), cwd=repo)
    return path


# The §6.3 example with one-line bodies and both time limits off.
VALID_INSTRUCTIONS = """## Limits
Timeout: none
Quiet timeout: none

## Done when
The hook exits.

## Passed when
Every unit test passes.

## Fixing failures
Read the log; never edit the stage itself.

## Rerunning a subset
Pass test paths as arguments.
"""


def write_hook(
    root: Path,
    stage: str,
    *,
    body: str = "exit 0",
    instructions: str = VALID_INSTRUCTIONS,
    executable: bool = True,
    show_delay: float = 0,
) -> Path:
    """`<root>/.omc/bin/<stage>`: prints `instructions` on --show-instructions, else `body`.

    Builtins only (printf, [), plus sleep by absolute path, so it runs on any PATH."""
    hook = root / ".omc" / "bin" / stage
    hook.parent.mkdir(parents=True, exist_ok=True)
    delay = f"    {shlex.quote(host_bin('sleep'))} {show_delay}\n" if show_delay else ""
    hook.write_text(
        "#!/bin/sh\n"
        'if [ "$#" -eq 1 ] && [ "$1" = "--show-instructions" ]; then\n'
        f"{delay}"
        f"    printf '%s' {shlex.quote(instructions)}\n"
        "    exit 0\n"
        "fi\n"
        f"{body}\n"
    )
    hook.chmod(0o755 if executable else 0o644)
    return hook


def sleep_line(seconds) -> str:
    """A shell command that sleeps `seconds`, by absolute path (any PATH)."""
    return f"{shlex.quote(host_bin('sleep'))} {seconds}"


class Stage:
    """One test's repo, ctx and cleanup registry."""

    def __init__(self, tmp_path: Path, repo: Path) -> None:
        self.tmp = tmp_path
        self.repo = repo
        self.roots = [repo]
        self.ctx = stage_ctx(tmp_path)
        self.watch = None

    def pidfile(self, name="hook.pid") -> Path:
        return self.tmp / name

    def read_pid(self, path: Path, timeout=10.0) -> int:
        _mutexproc.wait_for(lambda: path.is_file() and path.read_text().strip(), timeout)
        return int(path.read_text().strip())

    def record(self, root: Path | None = None) -> dict:
        return json.loads((Path(root or self.repo) / ".cache" / "omc" / "run.json").read_text())

    def gone(self, pid: int, timeout=5.0) -> None:
        _mutexproc.wait_for(lambda: not self.ctx.signal_pid(pid, 0), timeout)

    def cleanup(self) -> None:
        """SIGKILL the hook group and supervisor of every record still live, then reap.

        A final record's ids are left alone: its group is gone and the pid may be reused."""
        killed: set[int] = set()
        for root in self.roots:
            for path in sorted((root / ".cache" / "omc").glob("*.json")):
                try:
                    record = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if not isinstance(record, dict) or record.get("state") not in stagerun.LIVE:
                    continue
                if isinstance(record.get("pgid"), int):
                    self.ctx.signal_group(record["pgid"], signal.SIGKILL)
                if isinstance(record.get("pid"), int):
                    self.ctx.signal_pid(record["pid"], signal.SIGKILL)
                    killed.add(record["pid"])
        for proc in list(stagerun._SUPERVISORS):  # this worker's own children
            if proc.pid in killed:
                proc.wait(timeout=10)
            proc.poll()
        if self.watch is not None:
            self.watch.kill()
            self.watch.wait()


def wait(ctx: ToolContext, stage_name: str, period: float) -> stagerun.RunAnswer:
    """`wait_run` with its period (WAIT_PERIOD_S) set to `period` for this one call."""
    with mock.patch.object(stagerun, "WAIT_PERIOD_S", period):
        return stagerun.wait_run(ctx, stage_name)


def run_hook(s: Stage, stage_name, body, *, instructions=VALID_INSTRUCTIONS, args=(), **kw):
    """Write the hook and start a run of it that is not refused or unconfigured."""
    write_hook(s.repo, stage_name, body=body, instructions=instructions)
    answer = stagerun.start_run(s.ctx, stage_name, list(args), **kw)
    # Any state of its own run: a quick hook may end before the supervisor's first pass.
    assert answer.run and answer.state not in ("refused", "unconfigured"), answer
    return answer


def finished(s: Stage, stage_name, body, **kw):
    """`run_hook`, then the answer once the run ended."""
    started = run_hook(s, stage_name, body, **kw)
    answer = wait(s.ctx, stage_name, 30)
    assert answer.run == started.run
    return answer
