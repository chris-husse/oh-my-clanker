"""Small filesystem helpers shared across omc's modules."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, text: str, *, parents: bool = True) -> None:
    """Write `text` to `path` through a temp sibling and os.replace, mode 0644, so no
    reader ever sees a half-written file. `parents=False` makes no directory: a missing
    one raises FileNotFoundError."""
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; these files are not secrets
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_exclusive(path: Path, text: str) -> None:
    """Publish `text` at `path` only if nothing is there: a full temp sibling, then link.

    os.link fails on an existing target, so of racing creators exactly one wins and
    no reader ever sees a half-written file. A loser's FileExistsError is swallowed:
    the caller re-reads whatever the winner wrote."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_json_object(path: Path) -> dict | None:
    """The JSON object in `path`; None when it is unreadable, not JSON or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
