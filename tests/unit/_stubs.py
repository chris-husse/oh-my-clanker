"""Write fake executables onto an isolated PATH dir for probe/argv tests."""

from __future__ import annotations

import stat
from pathlib import Path


def make_stub(bindir: Path, name: str, *, stdout: str = "", rc: int = 0) -> Path:
    # Use printf to output stdout verbatim — JSON verdicts contain double quotes.
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / name
    # Escape backslashes and double quotes for the shell string
    escaped = stdout.replace("\\", "\\\\").replace('"', '\\"')
    path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{escaped}"\nexit {rc}\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def stub_env(bindir: Path, **extra: str) -> dict[str, str]:
    """A minimal env whose PATH contains ONLY the stub dir."""
    return {"HOME": str(bindir.parent), "PATH": str(bindir), **extra}
