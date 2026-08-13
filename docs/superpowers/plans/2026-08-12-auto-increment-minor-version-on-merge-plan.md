# GitHub Auto-Stamp Patch Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every merge to `main`, GitHub stamps the patch (third) version number = `github.run_number` into all four version files and commits it back, so `omc update` / `claude plugin update` reliably refreshes the plugin.

**Architecture:** A stdlib-only Python script (`scripts/stamp_version.py`) reads the human-owned `MAJOR.MINOR` from `pyproject.toml` and surgically rewrites the `version` value in all four version files to `MAJOR.MINOR.<patch>`. A new `version-stamp.yml` workflow runs it on push to `main` with `github.run_number` as the patch, then commits the result back with `[skip ci]` (the loop guard). Edits are value-only regex replacements — never a JSON round-trip — because the manifests use inline objects that a reformat would reflow.

**Tech Stack:** Python 3.12 (`tomllib`, `re`), pytest, GitHub Actions YAML.

---

## File Structure

- **Create** `scripts/stamp_version.py` — pure version-stamping logic + thin CLI. One responsibility: given a patch number, make all four files read `MAJOR.MINOR.<patch>`.
- **Create** `tests/unit/test_stamp_version.py` — unit tests for the script's pure functions (no disk writes).
- **Create** `.github/workflows/version-stamp.yml` — the merge-to-main trigger that runs the script and commits back.
- **Modify** `tests/unit/test_plugin_manifests.py` — add a regression guard that the four version strings agree.

The four version files the script targets: `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`.

---

## Task 1: Version-stamping script (TDD)

**Model:** standard coding tier

**Files:**
- Create: `scripts/stamp_version.py`
- Test: `tests/unit/test_stamp_version.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_stamp_version.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import stamp_version as sv  # noqa: E402


def test_major_minor_from_full_version():
    assert sv.major_minor('[project]\nversion = "0.1.47"\n') == "0.1"


def test_major_minor_from_two_part_version():
    assert sv.major_minor('[project]\nversion = "2.5"\n') == "2.5"


def test_major_minor_rejects_single_part():
    with pytest.raises(ValueError):
        sv.major_minor('[project]\nversion = "7"\n')


def test_stamp_pyproject_replaces_only_the_version_line():
    text = '[project]\nname = "omc"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    out = sv.stamp_pyproject(text, "0.1.9")
    assert 'version = "0.1.9"' in out
    assert 'name = "omc"' in out
    assert 'requires-python = ">=3.12"' in out


def test_stamp_json_preserves_inline_objects():
    text = '{\n  "name": "omc",\n  "version": "0.1.0",\n  "author": { "name": "X" }\n}\n'
    out = sv.stamp_json(text, "0.1.9", "plugin.json")
    assert '"version": "0.1.9"' in out
    # A json.dumps round-trip would reflow this inline object; a value-only
    # replacement must leave it byte-for-byte intact.
    assert '"author": { "name": "X" }' in out
    assert out == text.replace('"0.1.0"', '"0.1.9"')


def test_stamp_json_raises_when_no_version():
    with pytest.raises(ValueError):
        sv.stamp_json('{\n  "name": "omc"\n}\n', "0.1.9", "x.json")


def test_stamp_json_raises_when_multiple_versions():
    text = '{\n  "version": "0.1.0",\n  "nested": { "version": "9.9.9" }\n}\n'
    with pytest.raises(ValueError):
        sv.stamp_json(text, "0.1.9", "x.json")


def test_stamp_json_is_idempotent():
    text = '{\n  "version": "0.1.9"\n}\n'
    assert sv.stamp_json(text, "0.1.9", "x.json") == text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_stamp_version.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stamp_version'`.

- [ ] **Step 3: Write the script**

Create `scripts/stamp_version.py`:

```python
"""Stamp MAJOR.MINOR.PATCH into every version-bearing file.

MAJOR.MINOR is owned by humans in pyproject.toml; PATCH is owned by GitHub
(github.run_number) and passed in on each merge to main. This script reads
MAJOR.MINOR from pyproject.toml and OVERWRITES the three plugin manifests to
match, so a forgotten manifest edit self-heals.

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
JSON_FILES = (
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".codex-plugin" / "plugin.json",
)

_PYPROJECT_RE = re.compile(r'(?m)^(version = ")[^"]*(")$')
_JSON_RE = re.compile(r'("version"\s*:\s*")[^"]*(")')


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


def stamp(patch: int) -> str:
    """Write MAJOR.MINOR.<patch> into all four files; return the new version."""
    version = f"{major_minor(PYPROJECT.read_text())}.{patch}"
    PYPROJECT.write_text(stamp_pyproject(PYPROJECT.read_text(), version))
    for path in JSON_FILES:
        path.write_text(stamp_json(path.read_text(), version, path.name))
    return version


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: stamp_version.py <patch-number>", file=sys.stderr)
        return 2
    print(stamp(int(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_stamp_version.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Format and lint**

Run: `uvx ruff format scripts/stamp_version.py tests/unit/test_stamp_version.py && uvx ruff check scripts/stamp_version.py tests/unit/test_stamp_version.py`
Expected: no changes needed / all checks passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/stamp_version.py tests/unit/test_stamp_version.py
git commit -m "Add version-stamping script (patch = github.run_number)"
```

---

## Task 2: Version-sync regression guard

**Model:** standard coding tier

**Files:**
- Modify: `tests/unit/test_plugin_manifests.py`

- [ ] **Step 1: Write the guard test**

At the top of `tests/unit/test_plugin_manifests.py`, add `import tomllib` alongside the existing `import json` / `import re`. Then add this function (the existing `ROOT` constant is reused):

```python
def test_all_version_strings_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    marketplace = next(
        p
        for p in json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())["plugins"]
        if p["name"] == "omc"
    )["version"]
    codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())["version"]
    assert pyproject == claude == marketplace == codex, {
        "pyproject": pyproject,
        "claude": claude,
        "marketplace": marketplace,
        "codex": codex,
    }
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_all_version_strings_agree -v`
Expected: PASS — all four files currently read `0.1.0`, so the invariant already holds. (This is a regression guard, not red→green; it locks the invariant the stamp script maintains.)

- [ ] **Step 3: Lint**

Run: `uvx ruff check tests/unit/test_plugin_manifests.py`
Expected: all checks passed (the `import tomllib` is used).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_plugin_manifests.py
git commit -m "Guard that all four version strings stay in sync"
```

---

## Task 3: version-stamp workflow

**Model:** standard coding tier

**Files:**
- Create: `.github/workflows/version-stamp.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/version-stamp.yml`:

```yaml
# Stamp the patch (third) version number on every merge to main.
#
# The human owns MAJOR.MINOR in pyproject.toml; GitHub owns the patch and sets
# it to this workflow's run_number. scripts/stamp_version.py reads MAJOR.MINOR
# from pyproject.toml and writes MAJOR.MINOR.<run_number> into all four version
# files; this job then commits the result back to main with `[skip ci]` so the
# stamp commit does not re-trigger CI or this workflow (the loop guard).
#
# To bump MAJOR or MINOR: edit `version` in pyproject.toml in a normal PR. The
# patch you type there is irrelevant — it is replaced on merge.
#
# NOTE: pushes to main with the default GITHUB_TOKEN. main is currently
# unprotected. If branch protection is ever added, this job needs a bypass
# (a GitHub App token or an actor allowance) or the push will fail.
name: version-stamp
on:
  push:
    branches: [main]
permissions:
  contents: write
concurrency:
  group: version-stamp-main
  cancel-in-progress: false
jobs:
  stamp:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0
      - name: Stamp version = MAJOR.MINOR.run_number
        id: stamp
        run: echo "version=$(python3 scripts/stamp_version.py "$GITHUB_RUN_NUMBER")" >> "$GITHUB_OUTPUT"
      - name: Commit and push if the version changed
        run: |
          if git diff --quiet; then
            echo "version already current — nothing to stamp"
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git commit -am "chore: stamp version ${{ steps.stamp.outputs.version }} [skip ci]"
          git pull --rebase origin main
          git push origin HEAD:main
```

- [ ] **Step 2: Verify the YAML parses**

Run: `python3 -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/version-stamp.yml').read_text()); print('ok')"`
Expected: `ok`. (If PyYAML is unavailable outside the project venv, run `uv run python -c "..."` instead.)

- [ ] **Step 3: Sanity-check the script end-to-end locally (no commit)**

Run: `python3 scripts/stamp_version.py 999 && git --no-pager diff --stat`
Expected: prints `0.1.999` and shows all four files changed (only the version line in each).

- [ ] **Step 4: Revert the local sanity edit**

Run: `git checkout -- pyproject.toml .claude-plugin/plugin.json .claude-plugin/marketplace.json .codex-plugin/plugin.json`
Expected: `git status` clean for those four files.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/version-stamp.yml
git commit -m "Add version-stamp workflow: patch = run_number on merge to main"
```

---

## Self-Review Notes

- **Spec coverage:** §3 mechanism → Task 3 workflow + Task 1 script; §4 components → Tasks 1 & 3; §4 self-heal (overwrite manifests from pyproject) → `stamp()` reads only pyproject for MAJOR.MINOR; §8 unit tests → Task 1; §8 version-sync test → Task 2; §3/§6 loop guard + permissions → Task 3 workflow (`[skip ci]`, `contents: write`, branch-protection note in the header comment).
- **JSON formatting:** value-only regex (not `json.dumps`) — asserted by `test_stamp_json_preserves_inline_objects`. This is the one deliberate divergence from the spec's "JSON round-trip" wording; the round-trip would reflow the manifests' inline objects and fail the structural-invariance requirement.
- **Type/name consistency:** `major_minor`, `stamp_pyproject`, `stamp_json`, `stamp` used identically across script and tests.
- **Out of scope (spec §9):** no PyPI, no provenance-hook changes, no marketplace-ref changes — none touched.
