"""Write fake executables onto an isolated PATH dir for probe/argv tests."""

from __future__ import annotations

import stat
from pathlib import Path


def make_stub(bindir: Path, name: str, *, stdout: str = "", stderr: str = "", rc: int = 0) -> Path:
    # Quoted heredoc so stdout survives verbatim — JSON verdicts contain double quotes.
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / name
    # The fd-2 heredoc is emitted ONLY when asked, so the default script stays
    # byte-identical for every existing caller: an unconditional block would put
    # a stray newline on stderr and could flip a "stderr is empty" assertion.
    err = f"/bin/cat >&2 <<'OMC_STUB_ERR_EOF'\n{stderr}\nOMC_STUB_ERR_EOF\n" if stderr else ""
    path.write_text(
        f"#!/bin/sh\n/bin/cat <<'OMC_STUB_EOF'\n{stdout}\nOMC_STUB_EOF\n{err}exit {rc}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def stub_env(bindir: Path, **extra: str) -> dict[str, str]:
    """A minimal env whose PATH contains ONLY the stub dir."""
    return {"HOME": str(bindir.parent), "PATH": str(bindir), **extra}
