"""Stamp MAJOR.MINOR.PATCH into every version-bearing file.

MAJOR.MINOR is owned by humans in pyproject.toml; PATCH is owned by GitHub
(github.run_number) and passed in on each merge to main. This script reads
MAJOR.MINOR from pyproject.toml and OVERWRITES the three plugin manifests
AND uv.lock's omc entry to match, so a forgotten edit self-heals.

uv.lock is included because it pins the root package's own version: without
it, every release left the lock one version behind pyproject, and the drift
resurfaced as a spurious diff the first time anything ran `uv run` (which
re-syncs the lock silently). Stamping it here is a value-only edit, identical
to what `uv lock` would write, and avoids putting a resolver step in CI.

Edits are surgical: only the version VALUE is replaced, via a regex that must
match exactly once per file. A json.dumps round-trip is deliberately avoided
because the manifests use inline objects (e.g. `"author": { "name": "..." }`)
that a reformat would reflow.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"
JSON_FILES = (
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".codex-plugin" / "plugin.json",
)

_PYPROJECT_RE = re.compile(r'(?m)^(version = ")[^"]*(")$')
# Value-only replacement of the single "version" field per file. This assumes
# marketplace.json carries exactly one plugin entry (the omc one) — if a second
# plugin with its own "version" is ever added, the exactly-one guard in
# _replace_once fails loud rather than stamping the wrong field.
_JSON_RE = re.compile(r'("version"\s*:\s*")[^"]*(")')
# uv.lock carries a `version` line for EVERY locked package (two dozen here),
# so the omc entry has to be anchored on its own `name = "omc"` line. uv emits
# name-then-version for each [[package]]; if that order ever changes, the
# exactly-one guard in _replace_once fails loud rather than stamping a
# dependency's version.
_LOCK_RE = re.compile(r'(?m)^(name = "omc"\nversion = ")[^"]*(")$')


def major_minor(pyproject_text: str) -> str:
    """The human-owned MAJOR.MINOR from pyproject's [project] version."""
    parts = tomllib.loads(pyproject_text)["project"]["version"].split(".")
    if len(parts) < 2:
        raise ValueError(f"pyproject version is not MAJOR.MINOR[.PATCH]: {parts}")
    return f"{parts[0]}.{parts[1]}"


def _replace_once(pattern: re.Pattern[str], text: str, version: str, where: str) -> str:
    new, n = pattern.subn(rf"\g<1>{version}\g<2>", text)
    if n != 1:
        raise ValueError(f"{where}: expected exactly one version field, found {n}")
    return new


def stamp_pyproject(text: str, version: str) -> str:
    return _replace_once(_PYPROJECT_RE, text, version, "pyproject.toml")


def stamp_json(text: str, version: str, where: str) -> str:
    return _replace_once(_JSON_RE, text, version, where)


def stamp_lock(text: str, version: str) -> str:
    return _replace_once(_LOCK_RE, text, version, "uv.lock")


def stamp(patch: int) -> str:
    """Write MAJOR.MINOR.<patch> into all five files; return the new version."""
    version = f"{major_minor(PYPROJECT.read_text())}.{patch}"
    PYPROJECT.write_text(stamp_pyproject(PYPROJECT.read_text(), version))
    for path in JSON_FILES:
        path.write_text(stamp_json(path.read_text(), version, path.name))
    LOCK.write_text(stamp_lock(LOCK.read_text(), version))
    return version


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: stamp_version.py <patch-number>", file=sys.stderr)
        return 2
    print(stamp(int(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
