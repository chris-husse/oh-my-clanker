"""Stage runs: a detached supervisor bound to one hook run (design 6.2-6.4).

`start_run` checks the stage definition and the watch, then spawns
`python -m omc.stagerun supervise <spec>`. The supervisor holds
`<root>/.cache/omc/stage.lock` from before it starts the hook until it exits,
so "is the run alive" is "is the lock held". It is the only writer of
`run.json` (mirrored to `last-<stage>.json`); `wait_run` only reads them, and
keeps its own log position in `wait.json`.

A run ends by itself, or by a signal to its supervisor: SIGTERM from `kill_run`,
SIGUSR1 from the next `start_run` in its worktree. A supervisor that is gone
(`died`) leaves its hook group behind; the next `kill_run` or `start_run` ends it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from filelock import FileLock, Timeout

from . import stagedef, wtconfig
from .buildprogress import sentinel_line
from .errors import OmcError, Refusal
from .fsutil import read_json_object, write_atomic
from .stagedef import StageBroken
from .stageid import ensure_cache, resolve_ids, worktree_id
from .toolctx import ToolContext
from .watchlock import alive_lock_path, lock_held

State = Literal[
    "running",
    "passed",
    "failed",
    "held",
    "timed-out",
    "quiet-timed-out",
    "killed",
    "died",
    "refused",
    "unconfigured",
]
LIVE = frozenset({"running", "held"})

WAIT_PERIOD_S = 540.0
POLL_S = 1.0
KILL_GRACE_S = 30.0
STOP_WAIT_S = KILL_GRACE_S + 10  # how long a kill or a newer start waits for a run to end
STARTUP_S = 10.0
TAIL_LINES = 40
PRUNE_AGE_S = 7 * 86400
STAGE_ROOT = "/tmp/omc-stages-{uid}"  # per user: /tmp is shared

_STARTUP_POLL_S = 0.1
_WAIT_POLL_S = 0.5
_GROUP_POLL_S = 0.1
_LOCK_TAKE_S = 2.0
_TAIL_NEW_MAX = 1 << 20  # more new log than this: read only the last _TAIL_READ bytes
_TAIL_READ = 64 << 10
_RESULT_READ = 4096
_REASON_QUOTE_MAX = 200
_TOUCH_S = 3600.0  # how often a supervisor marks its run's files as in use, for prune

# Supervisors this process spawned and has not reaped yet. Holding them keeps Popen's
# "still running" ResourceWarning away, and polling them at each start reaps the ones
# that ended, so a long-lived caller (watch) collects no zombies.
_SUPERVISORS: list[_Proc] = []

NO_WATCH_MSG = (
    "CRITICAL STOP: no `omc watch` running for this project — "
    "the user starts `omc watch` in the primary checkout ({primary})"
)
DIED_MSG = "the supervisor is gone (killed from outside, or the machine restarted)"
_HELD_MSG = 'result file says "failed"; the run is held until it is killed'
# Residual: any SIGTERM to a supervisor is recorded as `kill`, whoever sent it.
_STOP_REASONS = {
    signal.SIGTERM: "kill: ended by omc internal stage kill",
    signal.SIGUSR1: "superseded: a newer start in this worktree",
}
WATCH_GONE_MSG = "watch-gone: the project's omc watch stopped"
WORKTREE_GONE_MSG = "worktree-gone: the worktree was removed"


@dataclass(frozen=True)
class RunAnswer:
    stage: str
    state: State
    run: str
    log: str
    instructions: str
    elapsed: int
    quiet: int
    tail: tuple[str, ...]
    reason: str
    fix_cycles: int  # the stage's own `Fix cycles`; 0: no run, or the stage sets none
    warnings: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "stage": self.stage,
            "state": self.state,
            "run": self.run,
            "log": self.log,
            "instructions": self.instructions,
            "elapsed": self.elapsed,
            "quiet": self.quiet,
            "tail": list(self.tail),
            "reason": self.reason,
            "fix_cycles": self.fix_cycles,
            "warnings": list(self.warnings),
        }


def _blank(stage: str, state: State, reason: str = "") -> RunAnswer:
    """An answer for a run that never started."""
    return RunAnswer(stage, state, "", "", "", 0, 0, (), reason, 0, ())


def _answer(
    record: dict,
    *,
    state: State | None = None,
    reason: str | None = None,
    tail: tuple[str, ...] = (),
) -> RunAnswer:
    now = time.time()
    started = record.get("started") or now
    ended = record.get("ended")
    log = record.get("log") or ""
    try:
        quiet = max(0, int(now - Path(log).stat().st_mtime)) if log else 0
    except OSError:
        quiet = 0
    return RunAnswer(
        stage=record["stage"],
        state=state or record["state"],
        run=record["run"],
        log=log,
        instructions=record.get("instructions") or "",
        elapsed=max(0, int((ended or now) - started)),
        quiet=quiet,
        tail=tail,
        reason=(record.get("reason") or "") if reason is None else reason,
        fix_cycles=record.get("fix_cycles") or 0,
        warnings=tuple(record.get("warnings") or ()),
    )


# --- the files under <root>/.cache/omc ---


def _omc_dir(root: Path) -> Path:
    return root / ".cache" / "omc"


def _repo_root(ctx: ToolContext) -> Path:
    root = wtconfig.repo_root(ctx)
    if root is None:
        raise Refusal("not inside a git repository")
    return Path(root)


def _refresh_template(ctx: ToolContext, root: Path) -> None:
    """Refresh the template in a linked worktree, never in the primary checkout (A4):
    watch's --auto-build runs there, and a rewritten tracked file makes watch skip its
    sync from then on. An unknown primary is left alone too."""
    primary = wtconfig.primary_root(ctx)
    if primary is not None and Path(primary).resolve() != root.resolve():
        stagedef.refresh_template(root)


def _known_stage(stage: str) -> None:
    if stage not in stagedef.STAGES:
        raise OmcError(f"unknown stage {stage!r} (one of {', '.join(stagedef.STAGES)})")


# --- start ---


def _stage_root(ctx: ToolContext) -> Path:
    """Where runs keep their log, stage dir, instructions and result: `OMC_STAGE_ROOT`,
    else `/tmp/omc-stages-<uid>`."""
    return Path(ctx.env.get("OMC_STAGE_ROOT") or STAGE_ROOT.format(uid=os.getuid()))


def _private_stage_root(ctx: ToolContext) -> Path:
    """The stage root, made 0700 when missing. One another user owns or may write to
    (/tmp is shared) could have a run's files swapped under it: refused."""
    root = _stage_root(ctx)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = root.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise StageBroken(f"stage log root {root} is not private to this user")
    return root


def _git_state(ctx: ToolContext, root: Path) -> tuple[str, bool]:
    head = ctx.run([ctx.git_bin, "rev-parse", "HEAD"], cwd=root)
    status = ctx.run([ctx.git_bin, "status", "--porcelain"], cwd=root)
    return (
        head.stdout.strip() if head.returncode == 0 else "",
        bool(status.stdout.strip()),
    )


def start_run(
    ctx: ToolContext, stage: str, args: list[str], *, extra_env: dict[str, str] | None = None
) -> RunAnswer:
    """Check the stage and the watch, spawn the supervisor, answer once it holds the run."""
    _known_stage(stage)
    root = _repo_root(ctx)
    _refresh_template(ctx, root)
    if stagedef.hook_state(root, stage) == "absent":
        return _blank(stage, "unconfigured")
    try:
        stagedef.check_hook(root, stage)
        ids = resolve_ids(ctx)
        ensure_cache(root)
        stage_root = _private_stage_root(ctx)
        instructions = stagedef.show_instructions(ctx, root, stage)
    except StageBroken as exc:
        return _blank(stage, "refused", str(exc))
    alive = alive_lock_path(ctx, cwd=str(root))
    if alive is None or not lock_held(alive):
        return _blank(stage, "refused", NO_WATCH_MSG.format(primary=ids.primary))
    previous = _end_current(ctx, root, ids.worktree)
    prune(stage_root, now=time.time())
    run = uuid.uuid4().hex[:12]
    base = (
        stage_root / ids.project / ids.worktree / f"{stage}-{time.strftime('%Y%m%dT%H%M%S')}-{run}"
    )
    stage_dir = Path(f"{base}.d")
    stage_dir.mkdir(parents=True)
    instructions_path = Path(f"{base}.instructions.md")
    write_atomic(instructions_path, instructions.text)
    _omc_dir(root).mkdir(exist_ok=True)
    head, dirty = _git_state(ctx, root)
    spec = {
        "run": run,
        "stage": stage,
        "root": str(root),
        "argv": [str(stagedef.hook_path(root, stage)), *args],
        "log": f"{base}.log",
        "stage_dir": str(stage_dir),
        "result": f"{base}.result",
        "instructions": str(instructions_path),
        "timeout_s": instructions.limits.timeout_s,
        "quiet_s": instructions.limits.quiet_s,
        "fix_cycles": instructions.limits.fix_cycles,
        "warnings": list(instructions.warnings),
        "project_id": ids.project,
        "worktree_id": ids.worktree,
        "alive_lock": str(alive),
        "touch_s": _TOUCH_S,
        "head": head,
        "dirty": dirty,
    }
    proc = ctx.spawn_detached(
        # -P: no cwd on sys.path, so a project's own `omc/` (or `json.py`) cannot shadow ours.
        [sys.executable, "-P", "-m", "omc.stagerun", "supervise", json.dumps(spec)],
        cwd=root,
        extra_env=extra_env,
    )
    _SUPERVISORS[:] = [p for p in _SUPERVISORS if p.poll() is None]
    _SUPERVISORS.append(proc)
    return _await_start(ctx, root, spec, proc, previous)


def _end_current(ctx: ToolContext, root: Path, wid: str) -> str | None:
    """End this worktree's live run, whatever its stage (U6); the id of the run found.

    A live supervisor gets SIGUSR1 and is waited for; a `died` one's group is ended here."""
    found = _observe(root, None, wid)
    if found is None:
        return None
    record, state, _ = found
    if state in LIVE:
        _stop(ctx, root, record, signal.SIGUSR1, STOP_WAIT_S)
    elif state == "died":
        _recover_group(ctx, record)
    return record["run"]


def _stop(ctx: ToolContext, root: Path, record: dict, sig: int, within: float) -> None:
    """Signal a live run's supervisor and wait for it to end, or raise."""
    ctx.signal_pid(record["pid"], sig)
    if not _await_end(root, record["run"], within):
        raise OmcError(
            f"the {record['stage']} run {record['run']} did not end within {within:g}s "
            f"of {signal.Signals(sig).name} (supervisor pid {record['pid']})"
        )


def _await_end(root: Path, run: str, within: float) -> bool:
    """Wait until `run`'s supervisor frees the lock, or another start's run took over."""
    omc = _omc_dir(root)
    deadline = time.monotonic() + within
    while True:
        if not lock_held(omc / "stage.lock"):
            return True
        record = read_json_object(omc / "run.json")
        if record is not None and record.get("run") != run:
            return True  # a concurrent start's supervisor holds it now
        if time.monotonic() >= deadline:
            return False
        time.sleep(_GROUP_POLL_S)


def _recover_group(ctx: ToolContext, record: dict) -> None:
    """End what a `died` run left of its hook group, only while its leader is the
    process the supervisor started: `/proc/<pgid>/stat` exists and its start time is the
    recorded `leader_start`. A recycled pgid, a restarted container (CLOCK_BOOTTIME is not
    namespaced) or a group ended by an earlier kill is not signalled. Without /proc
    (macOS) the boot marker decides: a record from another boot is not signalled.

    Residuals: a group whose leader exited is left to itself. Without /proc, a died record
    stays in `run.json` until the next start, so a second kill signals its pgid again,
    and within one boot that number may by then name another group; and a wall-clock
    (NTP) step of more than 60 s moves the boot marker, which skips recovery, the safe
    direction."""
    pgid = record.get("pgid")
    if not isinstance(pgid, int):
        return
    start = record.get("leader_start")
    if ctx.procfs():
        if isinstance(start, int) and ctx.process_start(pgid) == start:
            _end_group(ctx, None, pgid)
        return
    boot = record.get("boot")
    if isinstance(boot, (int, float)) and abs(ctx.boot_time() - boot) < 60:
        _end_group(ctx, None, pgid)


def prune(stage_root: Path, *, now: float) -> None:
    """Remove every project's runs whose files (`<stage>-<time>-<run>` plus `.log`, `.d`,
    `.instructions.md`, `.result`) all changed `PRUNE_AGE_S` or more ago, then the
    worktree dirs left empty. Every project, not just the caller's: logs under a project
    ID watch yielded to the committed one are never started again. A live run keeps its
    stage dir and instructions fresh (`_TOUCH_S`). Best effort: a concurrent start
    prunes too."""
    worktrees: list[Path] = []
    try:
        projects = list(stage_root.iterdir())
    except OSError:
        return
    for project in projects:
        try:
            worktrees.extend(project.iterdir())
        except OSError:
            continue
    for worktree in worktrees:
        groups: dict[str, list[tuple[Path, os.stat_result]]] = {}
        try:
            for entry in worktree.iterdir():
                groups.setdefault(entry.name.split(".", 1)[0], []).append((entry, entry.lstat()))
        except OSError:
            continue
        for members in groups.values():
            if any(now - st.st_mtime < PRUNE_AGE_S for _, st in members):
                continue
            for entry, st in members:
                try:
                    if stat.S_ISDIR(st.st_mode):
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink()
                except OSError:
                    pass
        try:
            worktree.rmdir()  # only when empty
        except OSError:
            pass


def _await_start(
    ctx: ToolContext, root: Path, spec: dict, proc: _Proc, previous: str | None
) -> RunAnswer:
    """Poll until the supervisor has written this run and holds the lock (or already ended).

    A supervisor that lost the lock to a concurrent start exits 1; the run that won is
    the one in `run.json` that is neither this one nor the `previous` one it replaced."""
    run_json, lock = _omc_dir(root) / "run.json", _omc_dir(root) / "stage.lock"
    deadline = time.monotonic() + STARTUP_S
    while True:
        record = read_json_object(run_json)
        ours = record is not None and record.get("run") == spec["run"]
        if ours and (record.get("state") not in LIVE or lock_held(lock)):
            return _answer(record)
        rc = proc.poll()
        if rc is not None:
            record = read_json_object(run_json)  # it may have finished the run just now
            ours = record is not None and record.get("run") == spec["run"]
            if ours and record.get("state") not in LIVE:
                return _answer(record)
            if ours:
                raise OmcError(
                    f"the {spec['stage']} supervisor exited {rc} after it started run "
                    f"{spec['run']}; its hook may still be running as process group "
                    f"{record.get('pgid')} (log: {spec['log']})"
                )
            lost_the_lock = rc == 1
            winner = (
                record
                if record is not None
                and record.get("worktree_id") == spec["worktree_id"]
                and record.get("run") != previous
                else None
            )
            if lost_the_lock and winner is not None:
                raise OmcError(f"another start won: run {winner['run']} ({winner['stage']})")
            winner_starting = lost_the_lock and lock_held(lock)  # its record not yet written
            if winner_starting and time.monotonic() < deadline:
                time.sleep(_STARTUP_POLL_S)
                continue
            raise OmcError(
                f"the {spec['stage']} supervisor exited {rc} before it started run "
                f"{spec['run']} (log: {spec['log']})"
            )
        if time.monotonic() >= deadline:
            ctx.signal_pid(proc.pid, signal.SIGKILL)
            raise OmcError(
                f"the {spec['stage']} supervisor did not start run {spec['run']} "
                f"within {STARTUP_S:g}s (log: {spec['log']})"
            )
        time.sleep(_STARTUP_POLL_S)


# --- wait ---


def _load(root: Path, stage: str | None, wid: str) -> tuple[dict | None, bool]:
    """This worktree's record of `stage` (None: the current run, any stage), and
    whether it is the current `run.json`."""
    omc = _omc_dir(root)
    current = read_json_object(omc / "run.json")
    if current is not None and current.get("worktree_id") == wid:
        if stage is None or current.get("stage") == stage:
            return current, True
    if stage is None:
        return None, False
    last = read_json_object(omc / f"last-{stage}.json")
    if last is not None and last.get("worktree_id") == wid:
        return last, False
    return None, False


def _observe(root: Path, stage: str | None, wid: str) -> tuple[dict, State, bool] | None:
    """The record, its state and whether it is `run.json`; None without a record. A live
    record nobody holds the lock for is `died`.

    Read twice: the supervisor writes its final state before it frees the lock, so a
    record read just before that write and a lock probed just after it would look dead."""
    lock = _omc_dir(root) / "stage.lock"

    def unheld(record: dict, current: bool) -> bool:
        return record.get("state") in LIVE and not (current and lock_held(lock))

    record, current = _load(root, stage, wid)
    if record is not None and unheld(record, current):
        record, current = _load(root, stage, wid)
        if record is not None and unheld(record, current):
            return record, "died", current
    if record is None:
        return None
    return record, record.get("state"), current


def _require(root: Path, stage: str, wid: str) -> tuple[dict, State, bool]:
    found = _observe(root, stage, wid)
    if found is None:
        raise OmcError(f"no {stage} run in this worktree")
    return found


def _new_lines(root: Path, record: dict) -> tuple[str, ...]:
    """The log lines since the last `wait` of this run, the last TAIL_LINES of them."""
    pos_path = _omc_dir(root) / "wait.json"
    pos = read_json_object(pos_path) or {}
    offset = pos.get("offset") if pos.get("run") == record["run"] else 0
    data = b""
    try:
        with open(record["log"], "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if not isinstance(offset, int) or not 0 <= offset <= size:
                offset = 0
            start = size - _TAIL_READ if size - offset > _TAIL_NEW_MAX else offset
            fh.seek(start)
            data = fh.read(size - start)
            offset = start + len(data)
    except OSError:
        offset = 0
    write_atomic(pos_path, json.dumps({"run": record["run"], "offset": offset}) + "\n")
    return tuple(data.decode("utf-8", errors="replace").splitlines()[-TAIL_LINES:])


def wait_run(ctx: ToolContext, stage: str) -> RunAnswer:
    """Block until this worktree's `stage` run is no longer `running`, or WAIT_PERIOD_S passed.

    The run is the one found first: a newer start of the stage replaces it, and the
    answer is then the old run's last record at once, never the new run's progress."""
    _known_stage(stage)
    root = _repo_root(ctx)
    _refresh_template(ctx, root)
    wid = worktree_id(root)
    deadline = time.monotonic() + WAIT_PERIOD_S
    first: dict | None = None
    while True:
        record, state, _ = _require(root, stage, wid)
        if first is None:
            first = record
        elif record["run"] != first["run"]:
            return _replaced(root, stage, first)
        left = deadline - time.monotonic()
        if state != "running" or left <= 0:
            break
        time.sleep(min(_WAIT_POLL_S, left))
    return _answer(
        record,
        state=state,
        reason=DIED_MSG if state == "died" else None,
        tail=_new_lines(root, record),
    )


def _replaced(root: Path, stage: str, first: dict) -> RunAnswer:
    """The answer for `first`, a run a newer start of its stage replaced: its final record
    while `last-<stage>.json` still has it, else `first` as the newer start ended it."""
    last = read_json_object(_omc_dir(root) / f"last-{stage}.json")
    if last is not None and last.get("run") == first["run"]:
        return _answer(last, tail=_new_lines(root, last))
    return _answer(
        first, state="killed", reason=_STOP_REASONS[signal.SIGUSR1], tail=_new_lines(root, first)
    )


# --- kill and the foreground run ---


def kill_run(ctx: ToolContext, stage: str) -> RunAnswer:
    """End this worktree's live run of `stage`; without one, answer its last record."""
    _known_stage(stage)
    root = _repo_root(ctx)
    _refresh_template(ctx, root)
    record, state, current = _require(root, stage, worktree_id(root))
    if state in LIVE:
        _stop(ctx, root, record, signal.SIGTERM, STOP_WAIT_S)
        last = read_json_object(_omc_dir(root) / f"last-{stage}.json")
        return _answer(last if last is not None and last.get("run") == record["run"] else record)
    if state == "died":
        # Only `run.json`'s: an older record was replaced by a start, which ended its group.
        if current:
            _recover_group(ctx, record)
        return _answer(record, state="died", reason=DIED_MSG)
    return _answer(record)


def run_foreground(
    ctx: ToolContext,
    stage: str,
    *,
    on_line: Callable[[str], None],
    on_start: Callable[[RunAnswer], None] | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunAnswer:
    """Start `stage` in the process cwd's worktree and pass each new log line to `on_line`
    until the run is no longer `running`. Ctrl-C kills the run, then re-raises.
    `on_start` gets the start answer of a run that started, before any line.

    A `held` answer is returned as is: watch, the one caller, never runs verify."""
    here = _repo_root(ctx)
    answer: RunAnswer | None = None
    try:
        answer = start_run(ctx, stage, [], extra_env=extra_env)
        if not answer.run:  # refused or unconfigured
            return answer
        if on_start is not None:
            on_start(answer)
        wid, offset = worktree_id(here), 0
        while True:
            record, state, _ = _require(here, stage, wid)
            if record["run"] != answer.run:  # a newer start of this stage ended ours
                _feed(Path(answer.log), offset, on_line, final=True)
                return replace(answer, state="killed", reason=_STOP_REASONS[signal.SIGUSR1])
            offset = _feed(Path(answer.log), offset, on_line, final=state != "running")
            if state != "running":
                return _answer(record, state=state, reason=DIED_MSG if state == "died" else None)
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        current = read_json_object(_omc_dir(here) / "run.json")
        if answer is not None and answer.run and current and current.get("run") == answer.run:
            try:
                kill_run(ctx, stage)
            except OmcError:
                pass  # the Ctrl-C is what the caller has to see
        raise


def _feed(log: Path, offset: int, on_line: Callable[[str], None], *, final: bool) -> int:
    """Pass the whole lines after `offset` (and, when `final`, an unended last one) to
    `on_line`; the offset after them."""
    try:
        with open(log, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return offset
    end = len(data) if final else data.rfind(b"\n") + 1
    for line in data[:end].decode("utf-8", errors="replace").splitlines():
        on_line(line)
    return offset + end


# --- the supervisor ---


class _Proc(Protocol):
    pid: int

    def poll(self) -> int | None: ...


def _end_group(ctx: ToolContext, proc: _Proc | None, pgid: int) -> None:
    """SIGTERM the group, wait up to KILL_GRACE_S for it to go, then SIGKILL it.

    `proc.poll()` reaps the leader: a zombie leader keeps `killpg(pgid, 0)` succeeding."""
    ctx.signal_group(pgid, signal.SIGTERM)
    if _group_gone(ctx, proc, pgid, KILL_GRACE_S):
        return
    ctx.signal_group(pgid, signal.SIGKILL)
    _group_gone(ctx, proc, pgid, 5.0)


def _group_gone(ctx: ToolContext, proc: _Proc | None, pgid: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while True:
        if proc is not None:
            proc.poll()
        if not ctx.signal_group(pgid, 0):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_GROUP_POLL_S)


def _last_line(log: Path) -> str:
    """The last non-empty line of the log, or `(empty log)`."""
    try:
        with open(log, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(max(0, size - _TAIL_READ))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        text = ""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return "(empty log)"


def _quote(content: str) -> str:
    flat = " ".join(content.split())
    return flat if len(flat) <= _REASON_QUOTE_MAX else flat[: _REASON_QUOTE_MAX - 1] + "…"


class _Supervisor:
    """One hook run, from spawn to its final state. Every state change is written."""

    def __init__(self, ctx: ToolContext, spec: dict, stops: list[int]) -> None:
        self.ctx = ctx
        self.spec = spec
        self.stops = stops  # the stop signals received, appended by the handlers
        self.root = Path(spec["root"])
        self.stage = spec["stage"]
        self.log = Path(spec["log"])
        self.result = Path(spec["result"])
        self.t0 = self.touched = time.monotonic()
        self.proc = ctx.spawn_logged(
            spec["argv"],
            cwd=self.root,
            log_path=self.log,
            extra_env={
                "OMC_STAGE_NAME": self.stage,
                "OMC_PROJECT_ID": spec["project_id"],
                "OMC_WORKTREE_ID": spec["worktree_id"],
                "OMC_STAGE_LOG": spec["log"],
                "OMC_STAGE_DIR": spec["stage_dir"],
                "OMC_STAGE_RESULT": spec["result"],
            },
        )
        self.record: dict = {
            "run": spec["run"],
            "stage": self.stage,
            "state": "running",
            "pid": os.getpid(),
            "pgid": self.proc.pid,
            "started": time.time(),
            "ended": None,
            "boot": ctx.boot_time(),
            "leader_start": ctx.process_start(self.proc.pid),
            "head": spec["head"],
            "dirty": spec["dirty"],
            "exit_code": None,
            "verdict": None,
            "reason": "",
            "worktree_id": spec["worktree_id"],
            "log": spec["log"],
            "instructions": spec["instructions"],
            "result": spec["result"],
            "fix_cycles": spec["fix_cycles"],
            "warnings": spec["warnings"],
            "timeout_s": spec["timeout_s"],
            "quiet_s": spec["quiet_s"],
        }
        self._write()

    @property
    def state(self) -> str:
        return self.record["state"]

    def _write(self) -> None:
        text = json.dumps(self.record) + "\n"
        omc = _omc_dir(self.root)
        try:  # never make directories: the worktree may be gone, and must stay gone
            write_atomic(omc / "run.json", text, parents=False)
            write_atomic(omc / f"last-{self.stage}.json", text, parents=False)
        except FileNotFoundError:
            pass

    def _touch(self) -> None:
        """Mark the run's stage dir and instructions as in use, so prune keeps the run.
        Never the log: its mtime is the answer's `quiet`."""
        now = time.monotonic()
        if now - self.touched < self.spec["touch_s"]:
            return
        self.touched = now
        for path in (self.spec["stage_dir"], self.spec["instructions"]):
            try:
                os.utime(path)
            except OSError:
                pass

    def _set(self, state: State, **fields: object) -> None:
        self.record.update(state=state, **fields)
        self._write()

    def _read_result(self) -> str:
        try:
            with open(self.result, "rb") as fh:
                return fh.read(_RESULT_READ).decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _finish(self, state: State, reason: str = "") -> int:
        """The run's final state: the sentinel line, then the record (the lock goes last)."""
        # Also ends what a hook that exited left behind in its group.
        _end_group(self.ctx, self.proc, self.proc.pid)
        rc = self.proc.poll()
        try:
            with open(self.log, "rb+") as fh:
                size = fh.seek(0, os.SEEK_END)
                newline = b""
                if size:
                    fh.seek(size - 1)
                    newline = b"" if fh.read(1) == b"\n" else b"\n"  # a hook's unended line
                fh.write(newline + (sentinel_line(rc) + "\n").encode("utf-8"))
        except OSError:
            pass
        verdict = state if state in ("passed", "failed") else None
        self._set(state, ended=time.time(), exit_code=rc, verdict=verdict, reason=reason)
        return 0

    def _finish_by_result(self, content: str) -> int:
        if content in ("passed", "failed"):
            reason = "" if content == "passed" else 'result file says "failed"'
            return self._finish(content, reason)
        return self._finish("failed", f'result file says "{_quote(content)}"')

    def run(self) -> int:
        size, grew = -1, self.t0
        timeout_s, quiet_s = self.spec["timeout_s"], self.spec["quiet_s"]
        while True:
            # 1. A stop signal: kill, or a newer start in this worktree.
            if self.stops:
                return self._finish("killed", _STOP_REASONS[self.stops[0]])
            # 2. The worktree or the watch is gone; a hold included (A1).
            if not self.root.is_dir():
                return self._finish("killed", WORKTREE_GONE_MSG)
            if not lock_held(Path(self.spec["alive_lock"])):
                return self._finish("killed", WATCH_GONE_MSG)
            self._touch()
            # 3. The result file decides, read once it holds a word.
            # Empty while the hook lives is no verdict yet: `echo x > f` creates f first.
            if self.state == "running" and self.result.exists():
                content = self._read_result()
                alive = self.proc.poll() is None
                if content == "failed" and self.stage == "verify" and alive:
                    self._set("held", verdict="failed", reason=_HELD_MSG)
                elif content or not alive:
                    return self._finish_by_result(content)
            # 4. The hook exited.
            rc = self.proc.poll()
            if rc is not None:
                if self.state == "held":
                    return self._finish("failed", 'result file says "failed"')
                if self.result.exists():
                    return self._finish_by_result(self._read_result())
                return self._finish("passed") if rc == 0 else self._finish("failed", f"exit {rc}")
            if self.state == "running":
                now = time.monotonic()
                # 5. Timeout.
                if timeout_s is not None and now - self.t0 >= timeout_s:
                    return self._finish(
                        "timed-out", f"timed out after {stagedef.format_duration(timeout_s)}"
                    )
                # 6. Quiet timeout: no log growth.
                try:
                    current = self.log.stat().st_size
                except OSError:
                    current = 0
                if current != size:
                    size, grew = current, now
                elif quiet_s is not None and now - grew >= quiet_s:
                    return self._finish(
                        "quiet-timed-out",
                        f"no log progress for {stagedef.format_duration(quiet_s)} "
                        f"at: {_last_line(self.log)}",
                    )
            time.sleep(POLL_S)


def supervise(spec: dict) -> int:
    """The detached supervisor: take the stage lock, run the hook, hold the lock to the end.

    The stop handlers go in first, so a signal sent at any time ends the run on the next
    pass; one that comes once the state is final changes nothing."""
    ctx = ToolContext.from_env()
    stops: list[int] = []
    for sig in _STOP_REASONS:
        signal.signal(sig, lambda signum, _frame: stops.append(signum))
    lock = FileLock(str(_omc_dir(Path(spec["root"])) / "stage.lock"))
    try:
        lock.acquire(timeout=_LOCK_TAKE_S)
    except Timeout:
        return 1  # another start's supervisor holds it
    try:
        return _Supervisor(ctx, spec, stops).run()
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "supervise":
        print("usage: python -m omc.stagerun supervise <spec-json>", file=sys.stderr)
        return 2
    spec: object = None
    try:
        spec = json.loads(args[1])
        return supervise(spec)
    except Exception:
        # stdio is /dev/null: the run's log is the one place anyone will look.
        log = spec.get("log") if isinstance(spec, dict) else None
        if isinstance(log, str):
            try:
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(f"omc stage supervisor crashed:\n{traceback.format_exc()}")
            except OSError:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
