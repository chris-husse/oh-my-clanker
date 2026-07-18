# Build Provenance in `omc version` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `omc version` shows what the binary IS (branch@commit + origin remote at build time), not just where it was installed from.

**Architecture:** A hatchling build hook (`hatch_build.py`, ported from the chicken's omk) stamps `omc/_buildinfo.py` (BRANCH/COMMIT/SOURCE) into built artifacts via `force_include`; a checked-in all-"unknown" fallback keeps source installs clean. `installsrc.py` gains a `provenance()` accessor and `version_string` composes: `omc <v> (<branch>@<commit>) from <receipt source> (origin <remote>)`, each part appearing only when known/meaningful.

**Tech Stack:** Python 3.12 stdlib, hatchling (build-time only, guarded import), pytest.

**Spec:** `docs/superpowers/specs/2026-07-18-version-provenance-design.md`.

## Global Constraints

- The hook NEVER writes into the source tree (temp file + `force_include` only); the checked-in `src/omc/_buildinfo.py` stays all-`"unknown"`.
- Per-field resolution order: `OMC_BUILD_{BRANCH,COMMIT,SOURCE}` env var → `.git` probe → `"unknown"`.
- Build-time redaction strips `user:password@` from URLs (colon required; bare `user@host` ssh forms preserved verbatim). Display-time URLs additionally pass the existing `installsrc._redact` (`[REDACTED]` style).
- `(branch@commit)` omitted when branch AND commit are both `"unknown"`; `(origin <url>)` shown only when the displayed source is NOT remote-git and the provenance source IS remote-git.
- git probes: argv lists, `capture_output`, no shell.
- Gates before each commit: `uv run pytest tests/unit -q` green; `uvx ruff format --check .` and `uvx ruff check .` clean (lint via `uvx ruff`, NOT `uv run ruff`).
- New test imports go in the test file's TOP import block.
- TDD: failing tests first (RED), then implement (GREEN).

## File Structure

- Create: `hatch_build.py` (repo root), `src/omc/_buildinfo.py`, `tests/unit/test_buildinfo_hook.py`
- Modify: `pyproject.toml` (hook registration), `src/omc/installsrc.py` (provenance + version format), `tests/unit/test_installsrc.py`, `README.md` (Commands row), `tests/e2e/test_e2e_smoke.py` (version shape regex)

---

### Task 1: Stamping machinery — build hook, fallback module, provenance accessor

**Files:**
- Create: `hatch_build.py`, `src/omc/_buildinfo.py`, `tests/unit/test_buildinfo_hook.py`
- Modify: `pyproject.toml`, `src/omc/installsrc.py`
- Test: `tests/unit/test_buildinfo_hook.py`, `tests/unit/test_installsrc.py`

**Interfaces:**
- Produces: `installsrc.provenance() -> dict[str, str]` (fresh `{"branch","commit","source"}` dict, values `"unknown"` on source installs); `hatch_build._resolve(root) -> tuple[str,str,str]`, `hatch_build._redact(url) -> str`, `hatch_build._render(branch,commit,source) -> str` (pure helpers, unit-tested); wheel/sdist builds carry a stamped `omc/_buildinfo.py`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_buildinfo_hook.py`:

```python
"""Unit tests for hatch_build.py's pure helpers (the hook itself runs only at build
time). Imported from its file path explicitly — the repo root is not a package."""

import importlib.util
import subprocess
from pathlib import Path

_HOOK_PATH = Path(__file__).parents[2] / "hatch_build.py"
_spec = importlib.util.spec_from_file_location("hatch_build", _HOOK_PATH)
hatch_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hatch_build)


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "c"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "branch", "-M", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@example.com:x/y.git"],
        check=True,
    )
    return tmp_path


def test_resolve_from_git(tmp_path, monkeypatch):
    for var in ("OMC_BUILD_BRANCH", "OMC_BUILD_COMMIT", "OMC_BUILD_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    branch, commit, source = hatch_build._resolve(_git_repo(tmp_path))
    assert branch == "main"
    assert commit and commit != "unknown" and len(commit) >= 7
    assert source == "git@example.com:x/y.git"


def test_env_overrides_git(tmp_path, monkeypatch):
    monkeypatch.setenv("OMC_BUILD_BRANCH", "release/x")
    monkeypatch.setenv("OMC_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("OMC_BUILD_SOURCE", "/some/checkout")
    assert hatch_build._resolve(_git_repo(tmp_path)) == ("release/x", "abc1234", "/some/checkout")


def test_resolve_without_git_is_unknown(tmp_path, monkeypatch):
    for var in ("OMC_BUILD_BRANCH", "OMC_BUILD_COMMIT", "OMC_BUILD_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    assert hatch_build._resolve(tmp_path) == ("unknown", "unknown", "unknown")


def test_redact_strips_credentials_keeps_ssh_user():
    assert (
        hatch_build._redact("https://oauth2:glpat-abc@host/x.git") == "https://host/x.git"
    )
    assert hatch_build._redact("git@example.com:x/y.git") == "git@example.com:x/y.git"
    assert (
        hatch_build._redact("git+ssh://git@example.com/x.git") == "git+ssh://git@example.com/x.git"
    )


def test_render_shape():
    out = hatch_build._render("main", "abc1234", "git@example.com:x/y.git")
    assert 'BRANCH = "main"' in out and 'COMMIT = "abc1234"' in out
    assert out.startswith("# Auto-generated")
```

and append to `tests/unit/test_installsrc.py` (imports to the top block: `from omc import installsrc`):

```python
def test_provenance_fallback_is_unknown():
    prov = installsrc.provenance()
    assert set(prov) == {"branch", "commit", "source"}
    # the checked-in fallback ships all-unknown; a stamped build overwrites it
    assert prov["branch"] == "unknown"
    assert prov["commit"] == "unknown"
    assert prov["source"] == "unknown"
    prov["branch"] = "mutated"
    assert installsrc.provenance()["branch"] == "unknown"  # fresh dict per call
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_buildinfo_hook.py tests/unit/test_installsrc.py -q`
Expected: FAIL — `FileNotFoundError`/`AttributeError` (no hatch_build.py), `AttributeError: module 'omc.installsrc' has no attribute 'provenance'`.

- [ ] **Step 3: Create `src/omc/_buildinfo.py`**

```python
# Auto-generated build provenance. This checked-in copy is the source-install /
# no-hook fallback (all "unknown"); the hatchling build hook (hatch_build.py)
# OVERWRITES it INSIDE THE BUILT ARTIFACT (wheel/sdist) with the real
# branch/commit/source via force_include. The source-tree copy stays "unknown" so
# editable `uv run`/`uv sync` never dirties it. Do not edit by hand.
BRANCH = "unknown"
COMMIT = "unknown"
SOURCE = "unknown"
```

- [ ] **Step 4: Create `hatch_build.py`** (port of the chicken's, minus its skills staging — omc ships skills via a static force-include already):

```python
"""Hatchling build hook: stamp build-time provenance into the built ``omc`` package.

Runs at build time (``uv tool install`` / ``uv build`` / ``hatch build``) for both a
local-checkout install (``uv tool install <repo>``) and a git-URL install
(``uv tool install git+…``) — in both cases uv materializes a real working tree, so
the git probes below resolve the actual branch/commit/source. A worktree checkout
whose ``.git`` pointer file targets a path that exists (the normal host case) probes
fine; inside containers that copied only the worktree, probes fail soft to "unknown".

We do NOT write into the source tree (that would dirty the working copy on every
editable ``uv run``/``uv sync`` and the committed ``_buildinfo.py`` would never stay
the ``"unknown"`` fallback). Instead we write the stamped module to a temp file and
``force_include`` it into the artifact as ``omc/_buildinfo.py``, overriding the
checked-in fallback only inside the built wheel/sdist.

Provenance resolution order (first that yields a value wins, else ``"unknown"``):
  1. ``OMC_BUILD_BRANCH`` / ``OMC_BUILD_COMMIT`` / ``OMC_BUILD_SOURCE`` env vars;
  2. ``git`` in the source tree (when ``<root>/.git`` is present);
  3. ``"unknown"``.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:  # hatchling is a BUILD dep, absent from the runtime venv. Guard so the pure
    # provenance helpers below stay importable for unit tests (`uv run pytest`), while
    # the build env (uv build / uv tool install) gets the real base class.
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ModuleNotFoundError:  # pragma: no cover - exercised only outside the build env
    BuildHookInterface = object  # type: ignore[assignment, misc]


class BuildInfoHook(BuildHookInterface):
    """Generate ``omc/_buildinfo.py`` and force-include it into the build."""

    PLUGIN_NAME = "custom"  # ignored for the in-tree custom hook, but explicit/clear.

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        branch, commit, source = _resolve(Path(self.root))
        # Write to a temp file (NOT the source tree) and inject it into the artifact.
        fd, tmp = tempfile.mkstemp(prefix="omc-buildinfo-", suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_render(branch, commit, source))
        # force_include maps a real path -> the path it takes inside the artifact, so
        # this replaces the checked-in src/omc/_buildinfo.py within the wheel/sdist.
        build_data.setdefault("force_include", {})[tmp] = "omc/_buildinfo.py"


def _resolve(root: Path) -> tuple[str, str, str]:
    """(branch, commit, source) — for each field: env var → git probe → 'unknown'."""
    has_git = (root / ".git").exists()

    def field(env_key: str, *git_args: str) -> str:
        env_val = os.environ.get(env_key)
        if env_val:
            return _redact(env_val)
        if has_git:
            git_val = _redact(_git(root, *git_args))
            if git_val:
                return git_val
        return "unknown"

    return (
        field("OMC_BUILD_BRANCH", "rev-parse", "--abbrev-ref", "HEAD"),
        field("OMC_BUILD_COMMIT", "rev-parse", "--short", "HEAD"),
        field("OMC_BUILD_SOURCE", "remote", "get-url", "origin"),
    )


def _git(root: Path, *args: str) -> str:
    """Run a read-only ``git`` probe (array form, no shell); '' on any failure."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return cp.stdout.strip()


def _redact(url: str) -> str:
    """Strip ``user:password@`` credentials from a URL before it lands in the artifact.

    Only a form WITH a colon is a credential — a bare ``user@host`` is an ssh login
    (e.g. ``git@github.com:…``) and MUST be preserved verbatim, so the regex requires
    the ``:`` and leaves bare-user URLs untouched.
    """
    return re.sub(r"://[^/@]+:[^/@]+@", "://", url)


def _render(branch: str, commit: str, source: str) -> str:
    return (
        "# Auto-generated by hatch_build.py at build time; do not edit.\n"
        f"BRANCH = {branch!r}\n"
        f"COMMIT = {commit!r}\n"
        f"SOURCE = {source!r}\n"
    )
```

- [ ] **Step 5: Register the hook in `pyproject.toml`** — after the existing `[tool.hatch.build.targets.wheel.force-include]` table:

```toml
# Stamp build-time provenance into omc/_buildinfo.py inside built artifacts (see
# hatch_build.py). Runs for both `uv tool install <repo>` and `uv tool install git+…`.
[tool.hatch.build.targets.wheel.hooks.custom]
path = "hatch_build.py"

[tool.hatch.build.targets.sdist.hooks.custom]
path = "hatch_build.py"
```

- [ ] **Step 6: Add `provenance()` to `src/omc/installsrc.py`** — import `from . import _buildinfo` in the top block; add after `_redact`:

```python
def provenance() -> dict[str, str]:
    """Build provenance as a fresh dict: ``{branch, commit, source}``.

    All ``"unknown"`` for a source install where the build hook never fired
    (the checked-in ``_buildinfo`` fallback). A new dict each call so callers
    can mutate without affecting later reads.
    """
    return {
        "branch": _buildinfo.BRANCH,
        "commit": _buildinfo.COMMIT,
        "source": _buildinfo.SOURCE,
    }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_buildinfo_hook.py tests/unit/test_installsrc.py -q`
Expected: PASS.

- [ ] **Step 8: Sanity-check a real build fires the hook**

Run: `uv build --wheel -q 2>&1 | tail -2 && python3 -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('dist/omc-*.whl'))[-1]); print(z.read('omc/_buildinfo.py').decode())"`
Expected: printed module shows the REAL current branch/commit (not "unknown"). Then `rm -rf dist`.

- [ ] **Step 9: Full gate + commit**

Run: `uv run pytest tests/unit -q && uvx ruff format --check . && uvx ruff check .`

```bash
git add hatch_build.py src/omc/_buildinfo.py pyproject.toml src/omc/installsrc.py tests/unit/test_buildinfo_hook.py tests/unit/test_installsrc.py
git commit -m "feat: build hook stamps provenance into omc/_buildinfo.py (red->green)"
```

---

### Task 2: Display — version format, README, E2E shape

**Files:**
- Modify: `src/omc/installsrc.py` (`version_string`), `tests/unit/test_installsrc.py`, `README.md:107`, `tests/e2e/test_e2e_smoke.py`

**Interfaces:**
- Consumes: `installsrc.provenance()`, `installsrc.install_source()`, `installsrc._is_remote_git`, `installsrc._redact` (Task 1 / existing).
- Produces: `version_string(env)` → `omc <v>[ (<branch>@<commit>)] from <source>[ (origin <remote>)]`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_installsrc.py`:

(imports for the top block: `from omc import _buildinfo`)

```python
def _prov(monkeypatch, branch="unknown", commit="unknown", source="unknown"):
    monkeypatch.setattr(_buildinfo, "BRANCH", branch)
    monkeypatch.setattr(_buildinfo, "COMMIT", commit)
    monkeypatch.setattr(_buildinfo, "SOURCE", source)


def test_version_plain_when_provenance_unknown(tmp_path, monkeypatch):
    _prov(monkeypatch)
    out = version_string({"HOME": str(tmp_path)})
    assert "(" not in out and out.endswith("from unknown")


def test_version_with_provenance_directory_install(tmp_path, monkeypatch):
    _prov(monkeypatch, "main", "abc1234", "git@github.com:x/omc.git")
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", directory = "/checkout/omc" }]\n',
    )
    out = version_string(env)
    assert "(main@abc1234)" in out
    assert "from /checkout/omc" in out
    assert out.endswith("(origin git@github.com:x/omc.git)")


def test_version_remote_git_install_omits_origin(tmp_path, monkeypatch):
    _prov(monkeypatch, "main", "abc1234", "https://github.com/x/omc")
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", git = "https://github.com/x/omc" }]\n',
    )
    out = version_string(env)
    assert "(main@abc1234)" in out
    assert "origin" not in out  # from-URL already IS the remote


def test_version_origin_is_redacted(tmp_path, monkeypatch):
    # belt+braces: display-side redaction even if a credentialed URL reached _buildinfo
    _prov(monkeypatch, "main", "abc1234", "https://oauth2:tok@gitlab.example.com/x/omc")
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", directory = "/checkout/omc" }]\n',
    )
    out = version_string(env)
    assert "tok" not in out
    assert "(origin https://[REDACTED]@gitlab.example.com/x/omc)" in out
```

Note `test_version_string` (existing) keeps passing: with the real fallback module all-unknown, output stays `omc <v> from unknown`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_installsrc.py -q`
Expected: FAIL — new assertions on the `(branch@commit)` / `(origin …)` parts.

- [ ] **Step 3: Implement `version_string`** — replace in `src/omc/installsrc.py`:

```python
def version_string(env: Mapping[str, str]) -> str:
    """``omc <v> [(branch@commit)] from <source> [(origin <remote>)]``.

    ``(branch@commit)`` is build provenance — what the binary IS; omitted for
    source installs where the hook never fired. ``from <source>`` is uv's
    receipt — where it was installed from. ``(origin <remote>)`` names the
    checkout's remote for directory installs; a remote-git install's from-URL
    already IS the remote, so the suffix would be noise there.
    """
    source, is_remote = install_source(env)
    prov = provenance()
    parts = [f"omc {__version__}"]
    if not (prov["branch"] == "unknown" and prov["commit"] == "unknown"):
        parts.append(f"({prov['branch']}@{prov['commit']})")
    parts.append(f"from {source}")
    if not is_remote and _is_remote_git(prov["source"]):
        parts.append(f"(origin {_redact(prov['source'])})")
    return " ".join(parts)
```

- [ ] **Step 4: README** — change line 107:

```markdown
| `omc version` | Print version + build provenance + install source |
```

- [ ] **Step 5: E2E shape assertion** — in `tests/e2e/test_e2e_smoke.py`, extend `test_configure_and_gate` after the existing `assert rc == 0 and "/repo" in out`:

```python
    # version line shape: provenance optional (a worktree build context has a .git
    # POINTER file whose target doesn't exist in-container -> probes fail soft)
    assert re.search(r"omc \S+ (\(\S+@\S+\) )?from /repo", out)
```

Add `import re` to the file's top import block.

- [ ] **Step 6: Run the smoke E2E**

Run: `uv run pytest tests/e2e/test_e2e_smoke.py -q`
Expected: PASS (Docker running; image rebuild possible on first run).

- [ ] **Step 7: Full gate + commit**

Run: `uv run pytest tests/unit -q && uvx ruff format --check . && uvx ruff check .`

```bash
git add src/omc/installsrc.py tests/unit/test_installsrc.py README.md tests/e2e/test_e2e_smoke.py
git commit -m "feat: omc version shows build provenance + origin remote (red->green)"
```

---

## Post-plan verification (controller, not a task)

Host-side live check: `just install` from this worktree, then `omc version` must print `omc 0.1.0 (feature/version-provenance@<sha>) from <worktree path> (origin git@github.com:chris-husse/oh-my-clanker.git)`. Safe on this host: the branch is stacked on cops-988, so the installed snapshot keeps the notifications config schema.
