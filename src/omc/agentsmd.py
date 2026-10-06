"""Deliver omc's installed behavior layer through global harness instructions."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

from .errors import OmcError
from .installsrc import package_root
from .providers.registry import get_provider
from .toolctx import ToolContext

_PROJECT_REL = Path(".omc/config/AGENTS.md")
_DISTRIBUTION_REL = Path("distribution/AGENTS.md")
SECTION_UUID = "75a24d62-844e-46f7-ab20-767f41397704"
BEGIN_MARKER = (
    f"<!-- omc:begin {SECTION_UUID} — managed by omc; edits inside are overwritten "
    "by `omc configure` / `omc update` -->"
).encode()
END_MARKER = f"<!-- omc:end {SECTION_UUID} -->".encode()

PROJECT_STARTER = """\
# Project agent instructions

This file is YOURS — omc seeds it once and never touches it again. Put the
project's real guidance here: build/test commands, architecture ground
rules, review expectations, tribal knowledge. Agents read it after omc's
global behavior layer when working in this repository.
"""


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def distribution_agents_md() -> Path:
    """The installed, authoritative behavior-layer file."""
    target = package_root() / _DISTRIBUTION_REL
    if not target.is_file():
        raise OmcError(f"broken install: {target} is missing")
    return target


def seed_project_agents_md(root: str | Path) -> None:
    """Seed project guidance once; existing project and root files are owned by the project."""
    project = Path(root) / _PROJECT_REL
    if not project.exists() and not project.is_symlink():
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(PROJECT_STARTER)


def _target(ctx: ToolContext, provider_name: str) -> Path:
    return get_provider(provider_name).instructions_file(ctx.env)


def _read(target: Path) -> bytes | None:
    if target.is_symlink():
        raise OmcError(f"{target}: global instructions file is a symlink; refusing to replace it")
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OmcError(f"{target}: cannot read global instructions: {exc}") from exc


def _span(target: Path, data: bytes) -> tuple[int, int] | None:
    begins, ends = data.count(BEGIN_MARKER), data.count(END_MARKER)
    if begins == ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise OmcError(f"{target}: malformed omc section markers (begin={begins}, end={ends})")
    start = data.index(BEGIN_MARKER)
    end = data.index(END_MARKER) + len(END_MARKER)
    if start >= end - len(END_MARKER):
        raise OmcError(f"{target}: malformed omc section markers (end before begin)")
    return start, end


def _render() -> bytes:
    body = distribution_agents_md().read_bytes()
    return (
        BEGIN_MARKER + b"\n" + body + (b"" if body.endswith(b"\n") else b"\n") + END_MARKER + b"\n"
    )


def _atomic_write(target: Path, data: bytes, *, mode: int | None = None) -> None:
    """Replace within the same directory, preserving an existing file's mode."""
    temp_name: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as temp:
            temp_name = temp.name
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    except OSError as exc:
        raise OmcError(f"{target}: cannot write global instructions: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def ensure_global_section(ctx: ToolContext, provider_name: str) -> str:
    """Ensure the exact installed section; return created, updated, or current."""
    target = _target(ctx, provider_name)
    previous = _read(target)
    rendered = _render()
    if previous is None:
        updated, result = rendered, "created"
    else:
        span = _span(target, previous)
        if span is None:
            separator = (
                b""
                if not previous or previous.endswith(b"\n\n")
                else (b"\n" if previous.endswith(b"\n") else b"\n\n")
            )
            updated, result = previous + separator + rendered, "created"
        else:
            start, end = span
            # The rendering includes one trailing newline; the old section's
            # trailing line break is part of its span when present.
            if previous[end : end + 1] == b"\n":
                end += 1
            updated, result = previous[:start] + rendered + previous[end:], "updated"
        if updated == previous:
            return "current"
    mode = stat.S_IMODE(target.stat().st_mode) if previous is not None else None
    _atomic_write(target, updated, mode=mode)
    _say(f"→ omc section written to {target}")
    return result


def remove_global_section(ctx: ToolContext, provider_name: str) -> str | None:
    """Remove only omc's marked section, leaving foreign instructions intact."""
    target = _target(ctx, provider_name)
    previous = _read(target)
    if previous is None:
        return None
    span = _span(target, previous)
    if span is None:
        return None
    start, end = span
    if previous[end : end + 1] == b"\n":
        end += 1
    prefix, suffix = previous[:start], previous[end:]
    if prefix.endswith(b"\n\n"):
        # Keep the preceding user line's terminator. When the section sits
        # between two separated blocks, the following separator already
        # supplies that line break.
        prefix = prefix[:-2] if suffix.startswith(b"\n") else prefix[:-1]
    elif suffix.startswith(b"\n\n"):
        suffix = suffix[2:]
    remaining = prefix + suffix
    try:
        if remaining.strip():
            _atomic_write(target, remaining, mode=stat.S_IMODE(target.stat().st_mode))
        else:
            target.unlink()
    except OSError as exc:
        raise OmcError(f"{target}: cannot remove omc section: {exc}") from exc
    return f"removed omc section from {target}"
