"""herdr sidebar cwd reports (spec 2026-11-14).

Builds host-less OSC 7 sequences so herdr can show folder/branch in its sidebar.
"""

import json
import os
import re
from pathlib import Path
from typing import TextIO

from .toolctx import ToolContext

_SAFE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/")


def percent_encode_path(path: str) -> str:
    """Percent-encode a path byte by byte (matches fish `string escape --style=url`)."""
    return "".join(chr(b) if b in _SAFE else f"%{b:02X}" for b in os.fsencode(path))


def osc7_report(path: str) -> str:
    """Return the host-less OSC 7 cwd report for `path`."""
    return "\033]7;file://" + percent_encode_path(path) + "\a"


_DEV_DIR = Path("/dev")
_MAX_ANCESTORS = 32
_TTY_NAME = re.compile(r"pts/[0-9]+|tty[A-Za-z0-9]+")


def _ancestor_tty(ctx: ToolContext) -> str | None:
    """The first ancestor's terminal device name (relative to /dev), or None."""
    pid = os.getppid()
    for _ in range(_MAX_ANCESTORS):
        proc = ctx.run_bounded(["ps", "-o", "ppid=", "-o", "tty=", "-p", str(pid)], timeout=2)
        if proc.returncode != 0:
            return None
        fields = proc.stdout.split()
        if len(fields) != 2:
            return None
        ppid, tty = int(fields[0]), fields[1]
        if tty not in ("?", "??"):
            return tty if _TTY_NAME.fullmatch(tty) else None
        if ppid <= 1:
            return None
        pid = ppid
    return None


def run_herdr_cwd(ctx: ToolContext, stdin: TextIO) -> int:
    """SessionStart hook (spec §4.5): report the session cwd to herdr's sidebar.

    Best effort and silent: every outcome returns 0 and prints nothing.
    """
    if ctx.env.get("TERM_PROGRAM") != "herdr":
        return 0
    try:
        payload = json.loads(stdin.read())
        if not isinstance(payload, dict):
            return 0
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return 0
        report = osc7_report(cwd).encode("ascii")
        tty = _ancestor_tty(ctx)
        if tty is None:
            return 0
        fd = os.open(_DEV_DIR / tty, os.O_WRONLY | os.O_NOCTTY)
        try:
            os.write(fd, report)
        finally:
            os.close(fd)
    except (OSError, TimeoutError, ValueError):
        pass
    return 0
