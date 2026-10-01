"""`omc title set|release|apply`: quiet helpers that pin or release the CALLER's iTerm2 tab.

The fish hook (assets/omc-title.fish) dispatches `apply <request file>` fire-and-forget;
inside a failure cooldown `apply` waits it out (lock released) and then applies the latest
request. `set` and `release` are the manual, foreground forms and bypass the cooldown.
Contract (spec §3): no stdout ever; nothing on success; one omc-authored stderr line on
failure; exit 0 ok, 1 failed or timed-out API write, 2 refusal for bad input.
Never calls detect_terminal, never emits OSC.
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

# Module attributes so tests drive a fake clock; production is the wall clock.
_clock = time.time
_sleep = time.sleep

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
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cooldown_left(marker: Path, now: float) -> float:
    """Seconds of cooldown remaining on the failure marker; 0 when absent or expired."""
    try:
        age = now - marker.stat().st_mtime
    except OSError:
        return 0.0
    if 0 <= age < COOLDOWN_SECONDS:  # a future mtime (clock skew) never throttles
        return COOLDOWN_SECONDS - age
    return 0.0


def _fail(marker: Path, reason: str) -> str:
    line = f"omc: iTerm2 tab title update failed: {reason} ({RETRY_HINT})"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    try:
        _write_atomic(marker, f"{line}\nat {stamp}\n")
    except OSError as exc:
        line += f"; marker not written ({type(exc).__name__})"
    print(line, file=sys.stderr)
    return FAILED


def _perform(
    ctx: ToolContext, session_id: str, action: str, title: str | None, *, throttle: bool
) -> str:
    marker = failure_marker(ctx.home, session_id)
    if throttle and _cooldown_left(marker, _clock()) > 0:
        # Only the hook-dispatched `apply` is rate-limited: it bounds background work AND the
        # AppleScript cookie prompt to 1/min. `set`/`release` are the user's manual retry.
        return THROTTLED
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
    return 1 if _perform(ctx, _session(ctx), "set", title, throttle=False) == FAILED else 0


def run_title_release(ctx: ToolContext) -> int:
    return 1 if _perform(ctx, _session(ctx), "release", None, throttle=False) == FAILED else 0


def run_title_apply(ctx: ToolContext, request_file: str) -> int:
    session_id = _session(ctx)
    request = Path(request_file)
    # Inside the cooldown the first pass returns the remaining wait instead of acting; the
    # lock is released for the sleep (newer helpers may queue or finish meanwhile), then ONE
    # more pass re-reads the latest request. Bounded: cooldown + one worker deadline.
    first = _apply_pass(ctx, session_id, request, may_wait=True)
    if isinstance(first, int):
        return first
    _sleep(min(first, COOLDOWN_SECONDS))
    second = _apply_pass(ctx, session_id, request, may_wait=False)
    return second if isinstance(second, int) else 0


def _apply_pass(ctx: ToolContext, session_id: str, request: Path, *, may_wait: bool) -> int | float:
    """One locked pass: an int exit code, or (may_wait only) the float seconds left to wait."""
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
            # re-read AFTER the lock: the newest request wins. Bytes + explicit decode: no
            # universal-newline translation (a `\r` must reach validate_title), and a
            # non-UTF-8 refname is a refusal, not a traceback.
            text = request.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise Refusal(
                f"omc title: cannot read request file {request} ({type(exc).__name__})"
            ) from exc
        try:
            if applied.read_bytes().decode("utf-8") == text:
                return 0  # an earlier helper already applied this exact request
        except (OSError, UnicodeDecodeError):
            pass
        action, title = parse_request(text)
        if may_wait:
            left = _cooldown_left(failure_marker(ctx.home, session_id), _clock())
            if left > 0:
                return float(left)  # the `with` releases the lock before the caller sleeps
        outcome = _perform(ctx, session_id, action, title, throttle=True)
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
