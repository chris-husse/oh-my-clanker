"""Unit tests run on hosts without FHS binary paths (NixOS has no bash or git under /bin).

Every real binary a stub or argv executes is resolved through ``_stubs.host_bin``
(``shutil.which`` on the developer's PATH, a loud ``pytest.fail`` when absent).
This guard scans the string literals of ``tests/unit/**/*.py`` and the shipped fish
assets, and fails on any hardcoded ``/bin/<x>`` or ``/usr/bin/<x>`` other than the two
POSIX-portable ones.
"""

import importlib
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

from . import _stubs
from ._stubs import host_bin

UNIT = Path(__file__).resolve().parent
ASSETS = UNIT.parents[1] / "src" / "omc" / "assets"

# /bin/sh and /usr/bin/env exist on every POSIX host this suite targets, NixOS included.
PORTABLE = {"/bin/sh", "/usr/bin/env"}
# The lookbehind keeps prefixed paths (/usr/local/bin/wt, /opt/py/bin/python3) out.
FHS = re.compile(r"(?<![\w./-])/(?:usr/)?bin/[\w.+-]*\w")
# `SHELL` values are data: omc reads only their basename (src/omc/shells/*.py).
SHELL_VALUE = re.compile(r"""SHELL["']?\s*[:=]\s*(f?["'])?$""")
# Literals that are data, never executed: expected generated text compared as
# strings, and argv/ps fixtures for code under test that is stubbed out.
DATA_ONLY = {
    # title_argv / FISH_TITLE_ARGV only land in generated shell text the test compares.
    "test_shells.py": {"/usr/bin/title-helper", "/usr/bin/python3"},
    # open_argv() output and a canned `ps` listing; iTerm2 is never launched here.
    "test_private_iterm.py": {"/usr/bin/open", "/bin/fish", "/bin/zsh"},
}


def fhs_literals(path: Path) -> list[str]:
    allowed = PORTABLE | DATA_ONLY.get(path.name, set())
    found = []
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    for tok in tokens:
        if tok.type not in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
            continue
        prefix = tok.line[: tok.start[1]] if tok.start[0] == tok.end[0] else ""
        if SHELL_VALUE.search(prefix):
            continue
        for match in FHS.finditer(tok.string):
            if match.group() not in allowed:
                found.append(f"{path.relative_to(UNIT)}:{tok.start[0]}: {match.group()}")
    return found


def test_no_hardcoded_fhs_binary_paths_in_unit_tests():
    # This file is not scanned: its allowlist spells out the data-only literals.
    paths = [p for p in sorted(UNIT.rglob("*.py")) if p != Path(__file__).resolve()]
    found = [hit for path in paths for hit in fhs_literals(path)]
    assert found == [], (
        "hardcoded FHS binary paths break hosts without them (NixOS); resolve the real "
        "binary with tests/unit/_stubs.host_bin(name) instead:\n" + "\n".join(found)
    )


def test_no_hardcoded_fhs_binary_paths_in_fish_assets():
    # The fish hook runs in the user's shell: `command <tool>` through PATH, never /bin/<tool>.
    found = [
        f"{path.name}:{number}: {match.group()}"
        for path in sorted(ASSETS.glob("*.fish"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#")  # comments may name the paths they avoid
        for match in FHS.finditer(line)
        if match.group() not in PORTABLE
    ]
    assert found == []


def test_importing_test_modules_resolves_no_host_bin(monkeypatch):
    # A host_bin miss at import is a collection error that interrupts the whole run, so
    # every module must defer resolution to test and helper bodies.
    def miss(name):
        pytest.fail(f"`{name}` resolved at import time", pytrace=False)

    monkeypatch.setattr(_stubs, "host_bin", miss)
    resolved = []
    for path in sorted(UNIT.glob("*.py")):
        if path.stem in ("__init__", "_stubs"):
            continue
        for loaded in [m for m in sys.modules if m.startswith("tests.unit.")]:
            if loaded != "tests.unit._stubs":
                monkeypatch.delitem(sys.modules, loaded)
        try:
            importlib.import_module(f"tests.unit.{path.stem}")
        except pytest.fail.Exception as exc:
            resolved.append(f"tests.unit.{path.stem}: {exc}")
    assert resolved == []


def test_host_bin_resolves_on_the_developer_path_not_a_monkeypatched_one(tmp_path, monkeypatch):
    # Tests narrow PATH to a stub dir before writing stubs; host_bin must still find the
    # real tool, and a stub dir must never be what it caches.
    (tmp_path / "cat").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "cat").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    host_bin.cache_clear()
    try:
        found = Path(host_bin("cat"))
    finally:
        host_bin.cache_clear()
    assert found.parent != tmp_path and found.name == "cat"
