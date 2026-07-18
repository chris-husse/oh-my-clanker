# COPS-987 "The omc update story" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `uv tool upgrade omc` propagate the agent behavior layer to every managed repo instantly (symlinks into the installed package), make `omc update` the whole update loop (CLI + per-provider plugins), and fix graph staleness by proxying GitNexus invocations through `omc internal gitnexus`.

**Architecture:** The `INTERNAL_AGENTS_MD` constant becomes a shipped package-data file (`src/omc/distribution/AGENTS.md`); managed repos' root `AGENTS.md`/`CLAUDE.md` become gitignored machine-local absolute symlinks to it, ensured by configure/start and repaired by watch. `omc update` fans out plugin updates over configured providers with failure isolation. A new `omc internal gitnexus` subcommand injects deterministic `--repo`/`--branch` scoping so skills can never read GitNexus's stale default store.

**Tech Stack:** Python 3.12+, hatchling, importlib.resources, pytest, testcontainers (E2E). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-17-cops-987-improve-omc-watch-design.md`

## Global Constraints

- Red→green TDD: write the failing test, RUN it and see it fail, implement, see it pass. Every task.
- Run tests with `uv run pytest <path> -v` from the repo root. Full unit suite: `uv run pytest tests/unit -q`.
- Lint before every commit: `uv run ruff check src tests && uv run ruff format --check src tests` (line-length 100).
- Watch narration doctrine: repeatable quiet outcomes narrate only on state CHANGE (quiet-token pattern in `watch._tick`); action outcomes always narrate; failures warn and skip, never crash the loop; never destructive.
- `omc internal` contract: machine stdout, no banner, exit codes 0 ok / 2 usage.
- Never log/echo credentials; `installsrc._redact` is the pattern.
- Provider quirks are documented as comments at the exact code site that depends on them.
- Commit messages: repo style is `feat:`/`fix:`/`docs:` + `(red->green)` suffix for TDD tasks, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `distribution/AGENTS.md` package data + resolvers

**Files:**
- Create: `src/omc/distribution/AGENTS.md`
- Modify: `src/omc/installsrc.py` (add `package_root()`)
- Modify: `src/omc/agentsmd.py` (add `distribution_agents_md()`; constant stays until Task 3)
- Test: `tests/unit/test_installsrc.py`, `tests/unit/test_agentsmd.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `installsrc.package_root() -> Path` (absolute dir of the installed omc package); `agentsmd.distribution_agents_md() -> Path` (the shipped AGENTS.md; raises `OmcError` when missing). Tasks 2 and 3 rely on both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_installsrc.py`:

```python
def test_package_root_is_the_omc_package_dir():
    from omc.installsrc import package_root

    root = package_root()
    assert root.is_dir()
    assert (root / "__init__.py").is_file()
    assert root.name == "omc"
```

Append to `tests/unit/test_agentsmd.py`:

```python
def test_distribution_agents_md_resolves_and_carries_the_layer():
    from omc.agentsmd import distribution_agents_md

    target = distribution_agents_md()
    assert target.is_file()
    text = target.read_text()
    assert ".omc/config/AGENTS.md" in text  # fans out to the project layer
    assert "rebase-main" in text and "OMC_" in text
    assert "subagent" in text.lower() and "efficient" in text  # model doctrine
    assert "omc update" in text  # header explains how the file updates
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_installsrc.py::test_package_root_is_the_omc_package_dir tests/unit/test_agentsmd.py::test_distribution_agents_md_resolves_and_carries_the_layer -v`
Expected: FAIL with `ImportError: cannot import name 'package_root'` (and same for `distribution_agents_md`).

- [ ] **Step 3: Create `src/omc/distribution/AGENTS.md`**

Copy the current `INTERNAL_AGENTS_MD` string body from `src/omc/agentsmd.py:21` verbatim, EXCEPT the first line. Replace:

```
# omc behavior layer (generated — do not edit; `omc configure` regenerates it)
```

with:

```
# omc behavior layer (ships with the omc install — `omc update` updates it everywhere)
```

Everything else (the bullet list through the "## Project instructions" section) is copied unchanged.

- [ ] **Step 4: Add the resolvers**

In `src/omc/installsrc.py` (module already imports `Path`):

```python
def package_root() -> Path:
    """Absolute directory of the installed omc package (contains distribution/).

    importlib.resources over __file__ math: works identically for wheel
    installs (uv tool venv) and the dev checkout (src/omc). uv tool venvs are
    real directories, so the result is a valid symlink target.
    """
    from importlib import resources

    return Path(str(resources.files("omc")))
```

In `src/omc/agentsmd.py` (add imports `from .errors import OmcError` and `from .installsrc import package_root`):

```python
_DISTRIBUTION_REL = Path("distribution/AGENTS.md")


def distribution_agents_md() -> Path:
    """The installed behavior-layer file — the chain's symlink target."""
    target = package_root() / _DISTRIBUTION_REL
    if not target.is_file():
        raise OmcError(f"broken install: {target} is missing")
    return target
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_installsrc.py tests/unit/test_agentsmd.py -v`
Expected: the two new tests PASS; all pre-existing tests still pass (the v1 chain is untouched so far).

- [ ] **Step 6: Verify packaging includes the file**

Run: `uv build 2>/dev/null && unzip -l dist/omc-0.1.0-py3-none-any.whl | grep distribution`
Expected: `omc/distribution/AGENTS.md` listed. Then `rm -rf dist`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/omc/distribution/AGENTS.md src/omc/installsrc.py src/omc/agentsmd.py tests/unit/test_installsrc.py tests/unit/test_agentsmd.py
git commit -m "feat: ship the behavior layer as package data (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `omc print-install-path`

**Files:**
- Modify: `src/omc/cli.py` (subparser at the `build_parser` block ~line 20, dispatch in `_dispatch` ~line 87, banner exemption in `main` ~line 77)
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `installsrc.package_root()` (Task 1).
- Produces: the CLI contract `omc print-install-path` → exactly one line on stdout, nothing on stderr, exit 0. Task 3's symlink target is `<that path>/distribution/AGENTS.md`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cli.py` (file already imports `main` from `omc.cli`; add `from pathlib import Path` if absent):

```python
def test_print_install_path_is_machine_pure(capsys):
    rc = main(["print-install-path"])
    assert rc == 0
    out = capsys.readouterr()
    assert out.err == ""  # banner-exempt, like version
    lines = out.out.splitlines()
    assert len(lines) == 1  # exactly one line: OMC_PATH=$(omc print-install-path)
    assert (Path(lines[0]) / "distribution" / "AGENTS.md").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli.py::test_print_install_path_is_machine_pure -v`
Expected: FAIL — argparse errors on the unknown command (SystemExit).

- [ ] **Step 3: Implement**

In `src/omc/cli.py`:

1. In `build_parser()`, after the `version` subparser line:

```python
    sub.add_parser(
        "print-install-path", help="Print the installed omc package directory (one line, no banner)"
    )
```

2. In `main()`, extend the banner exemption:

```python
    if args.command not in ("version", "print-install-path"):
        print(f"Oh My Clanker! v{__version__}", file=sys.stderr)
```

3. In `_dispatch()`, next to the `version` branch:

```python
    if args.command == "print-install-path":
        from .installsrc import package_root

        print(package_root())
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/omc/cli.py tests/unit/test_cli.py
git commit -m "feat: omc print-install-path - shell-composable install location (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Chain v2 — `ensure_agents_chain` rework with v1 migration

**Files:**
- Modify: `src/omc/agentsmd.py` (rewrite `ensure_agents_chain`, delete `INTERNAL_AGENTS_MD`, reword `PROJECT_STARTER`, add `_ensure_gitignore` + `chain_healthy`)
- Test: `tests/unit/test_agentsmd.py` (rewrite v1 tests to v2 semantics)

**Interfaces:**
- Consumes: `distribution_agents_md()` (Task 1).
- Produces: `ensure_agents_chain(ctx, root) -> str` returning `"created" | "ok" | "blocked"` (unchanged signature — `configure._ensure_repo_chain` keeps working untouched); `chain_healthy(root: str | Path) -> bool` (cheap check, no writes — Task 5's watch tick uses it).

- [ ] **Step 1: Rewrite the test file**

Replace the body of `tests/unit/test_agentsmd.py` with (keep the existing `_ctx` helper and imports; `INTERNAL` constant goes away):

```python
from pathlib import Path

from omc.agentsmd import chain_healthy, distribution_agents_md, ensure_agents_chain
from omc.toolctx import ToolContext

V1_INTERNAL = Path(".omc/internal/AGENTS.md")


def _ctx(tmp_path):
    return ToolContext.from_env({"HOME": str(tmp_path)})


def test_distribution_agents_md_resolves_and_carries_the_layer():
    target = distribution_agents_md()
    assert target.is_file()
    text = target.read_text()
    assert ".omc/config/AGENTS.md" in text  # fans out to the project layer
    assert "rebase-main" in text and "OMC_" in text
    assert "subagent" in text.lower() and "efficient" in text  # model doctrine
    assert "omc update" in text  # header explains how the file updates


def test_chain_created_from_nothing(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    target = distribution_agents_md().resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        link = root / name
        assert link.is_symlink(), f"{name} must be a symlink"
        assert link.resolve() == target  # absolute link into the install
    assert not (root / V1_INTERNAL).exists()  # v1 layer is never created
    assert (root / ".omc" / "config" / "AGENTS.md").is_file()  # starter seeded
    gitignore = (root / ".gitignore").read_text()
    assert "/AGENTS.md" in gitignore and "/CLAUDE.md" in gitignore
    assert "AGENTS.md" in capsys.readouterr().err  # narrated
    assert chain_healthy(root)


def test_correct_chain_is_silent_and_idempotent(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    ensure_agents_chain(_ctx(tmp_path), root)
    project = root / ".omc" / "config" / "AGENTS.md"
    project.write_text("# my project rules\n")
    before = (root / ".gitignore").read_text()
    capsys.readouterr()
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "ok"
    assert project.read_text() == "# my project rules\n"  # NEVER overwritten
    assert (root / ".gitignore").read_text() == before  # no duplicate entries
    assert capsys.readouterr().err == ""  # healthy chain is quiet


def test_v1_chain_migrates_to_v2(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    internal = root / V1_INTERNAL
    internal.parent.mkdir(parents=True)
    internal.write_text("# omc behavior layer (generated)\n")
    (root / ".omc" / "config").mkdir(parents=True)
    (root / ".omc" / "config" / "AGENTS.md").write_text("# mine\n")
    for name in ("AGENTS.md", "CLAUDE.md"):
        (root / name).symlink_to(V1_INTERNAL)  # relative v1 links
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    target = distribution_agents_md().resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).resolve() == target
    assert not internal.exists()  # v1 file retired
    assert not internal.parent.exists()  # empty .omc/internal removed
    assert (root / ".omc" / "config" / "AGENTS.md").read_text() == "# mine\n"


def test_dangling_v2_link_is_repaired_not_blocked(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    gone = tmp_path / "old-venv" / "omc" / "distribution" / "AGENTS.md"
    for name in ("AGENTS.md", "CLAUDE.md"):
        (root / name).symlink_to(gone)  # previous install location, now deleted
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    target = distribution_agents_md().resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).resolve() == target


def test_regular_root_file_is_never_replaced(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "AGENTS.md").write_text("# handwritten\n")
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "blocked"
    assert not (root / "AGENTS.md").is_symlink()
    assert (root / "AGENTS.md").read_text() == "# handwritten\n"
    err = capsys.readouterr().err
    assert ".omc/config/AGENTS.md" in err  # migration steps named
    assert not (root / "CLAUDE.md").exists()  # nothing half-created
    assert not (root / ".gitignore").exists()  # blocked mutates NOTHING


def test_foreign_symlink_is_warned_not_touched(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "other.md").write_text("x")
    (root / "AGENTS.md").symlink_to("other.md")
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "blocked"
    assert (root / "AGENTS.md").resolve() == (root / "other.md").resolve()


def test_chain_healthy_is_a_cheap_read_only_probe(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert not chain_healthy(root)
    ensure_agents_chain(_ctx(tmp_path), root)
    assert chain_healthy(root)
    (root / "AGENTS.md").unlink()
    assert not chain_healthy(root)
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/unit/test_agentsmd.py -v`
Expected: FAIL — `ImportError: cannot import name 'chain_healthy'`; after stubs, the v2 assertions fail against the v1 implementation.

- [ ] **Step 3: Rewrite `src/omc/agentsmd.py`**

Full new module body (replaces everything after the imports; keep `_say`):

```python
"""The AGENTS.md control chain, v2: root AGENTS.md + CLAUDE.md are
machine-local, gitignored symlinks into the INSTALLED omc package's
distribution/AGENTS.md, which defers to the project-owned
.omc/config/AGENTS.md.

`uv tool upgrade omc` replacing the venv is the whole propagation story —
every managed repo serves the new behavior layer instantly. The v1 chain
(root symlinks -> committed .omc/internal/AGENTS.md stamped from a constant)
is migrated automatically; omc never touches the project layer and never
replaces files it does not own.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .errors import OmcError
from .installsrc import package_root
from .toolctx import ToolContext

_V1_INTERNAL_REL = Path(".omc/internal/AGENTS.md")
_PROJECT_REL = Path(".omc/config/AGENTS.md")
_DISTRIBUTION_REL = Path("distribution/AGENTS.md")
_ROOT_NAMES = ("AGENTS.md", "CLAUDE.md")
_GITIGNORE_ENTRIES = ("/AGENTS.md", "/CLAUDE.md")

PROJECT_STARTER = """\
# Project agent instructions

This file is YOURS — omc seeds it once and never touches it again. Put the
project's real guidance here: build/test commands, architecture ground
rules, review expectations, tribal knowledge. Every agent reads it right
after omc's behavior layer (the root AGENTS.md/CLAUDE.md symlinks).
"""


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def distribution_agents_md() -> Path:
    """The installed behavior-layer file — the chain's symlink target."""
    target = package_root() / _DISTRIBUTION_REL
    if not target.is_file():
        raise OmcError(f"broken install: {target} is missing")
    return target


def _is_omc_link(link: Path) -> bool:
    """True when `link` is a symlink omc owns (v1, v2, or a stale v2 from a
    previous install location) and may therefore repair or migrate."""
    if not link.is_symlink():
        return False
    raw = os.readlink(link)
    if raw.endswith(str(_V1_INTERNAL_REL)):
        return True  # v1 relative link
    return raw.endswith(str(_DISTRIBUTION_REL))  # v2, current or stale


def chain_healthy(root: str | Path) -> bool:
    """Cheap read-only probe: both root links exist and hit the live target."""
    root = Path(root)
    target = distribution_agents_md().resolve()
    return all(
        (root / name).is_symlink() and (root / name).resolve() == target
        for name in _ROOT_NAMES
    )


def _ensure_gitignore(root: Path) -> bool:
    """Append-only: add missing root-anchored entries, never rewrite content."""
    gi = root / ".gitignore"
    text = gi.read_text() if gi.is_file() else ""
    missing = [e for e in _GITIGNORE_ENTRIES if e not in text.splitlines()]
    if not missing:
        return False
    chunk = "" if not text or text.endswith("\n") else "\n"
    chunk += "# machine-local omc chain symlinks (targets differ per machine)\n"
    chunk += "".join(f"{e}\n" for e in missing)
    gi.write_text(text + chunk)
    return True


def ensure_agents_chain(ctx: ToolContext, root: str | Path) -> str:
    """Verify/create the v2 chain. Returns "created" | "ok" | "blocked".

    - Root AGENTS.md/CLAUDE.md: absolute symlinks to the installed
      distribution/AGENTS.md; gitignored (entries ensured, append-only).
    - v1 chain artifacts (omc's own relative symlinks + the stamped
      .omc/internal/AGENTS.md) migrate automatically.
    - Foreign regular files or unknown symlinks: NEVER replaced — chain is
      "blocked" with migration steps and NOTHING is mutated.
    - .omc/config/AGENTS.md: seeded only if absent (the project owns it).
    """
    root = Path(root)
    target = distribution_agents_md()
    resolved_target = target.resolve()

    # Check the root files FIRST: a blocked chain must not half-mutate the repo.
    blocked = []
    for name in _ROOT_NAMES:
        link = root / name
        if not link.exists() and not link.is_symlink():
            continue  # missing -> creatable
        if not _is_omc_link(link):
            blocked.append(name)
    if blocked:
        _say(
            f"→ {', '.join(blocked)} already exist and are not omc's symlinks — "
            "omc will not replace them. To adopt the omc chain: move your content "
            f"into {_PROJECT_REL}, delete the root file(s), and re-run `omc configure`."
        )
        return "blocked"

    created = False
    for name in _ROOT_NAMES:
        link = root / name
        if link.is_symlink():
            if link.resolve() == resolved_target:
                continue  # already correct
            link.unlink()  # v1 or stale v2 — replace
        link.symlink_to(target)
        created = True

    internal = root / _V1_INTERNAL_REL
    if internal.is_file():
        internal.unlink()  # v1 stamped layer retired; content now ships installed
        if internal.parent.is_dir() and not any(internal.parent.iterdir()):
            internal.parent.rmdir()
        created = True

    project = root / _PROJECT_REL
    if not project.exists():
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(PROJECT_STARTER)
        created = True

    if _ensure_gitignore(root):
        created = True

    if created:
        _say(
            "→ AGENTS.md/CLAUDE.md now symlink into the omc install "
            f"({target}); they are machine-local (gitignored) — project guidance "
            f"lives in {_PROJECT_REL}, commit that one"
        )
        return "created"
    return "ok"
```

Note: `INTERNAL_AGENTS_MD` and the old `_INTERNAL_REL` are deleted; `ensure_agents_chain`'s signature is unchanged so `configure.py` needs no edit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_agentsmd.py tests/unit/test_configure.py -v`
Expected: all PASS (configure's tests exercise the chain through `_ensure_repo_chain`; if they assert v1 internals, update those assertions to v2: symlinks point at `distribution_agents_md()`, no `.omc/internal/AGENTS.md`).

- [ ] **Step 5: Migrate THIS repo's own chain (dogfood)**

The omc repo itself carries the v1 chain. Run: `uv run omc configure --set worktree.base_branch=main` (any `--set` triggers `_ensure_repo_chain`), then `git status --short`.
Expected: root `AGENTS.md`/`CLAUDE.md` deleted from the index (now gitignored symlinks), `.omc/internal/AGENTS.md` deleted, `.gitignore` gains the two entries. Stage exactly those deletions + `.gitignore`.

- [ ] **Step 6: Full unit suite, lint, commit**

```bash
uv run pytest tests/unit -q
uv run ruff check src tests && uv run ruff format --check src tests
git add -A src/omc/agentsmd.py tests/unit/test_agentsmd.py tests/unit/test_configure.py .gitignore AGENTS.md CLAUDE.md .omc/internal
git commit -m "feat: chain v2 - behavior layer symlinks into the install, v1 migrates (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `omc start` ensures the chain

**Files:**
- Modify: `src/omc/start.py` (in `run_start`, after the `ensure_plugin` call ~line 67)
- Test: `tests/unit/test_start.py`

**Interfaces:**
- Consumes: `ensure_agents_chain` (Task 3), `wtconfig.repo_root`.
- Produces: behavioral guarantee — any `omc start` in a repo leaves a working chain; blocked chains warn-but-proceed (start never aborts over it).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_start.py` (mirror the file's existing dry-run test setup — it builds a ToolContext with stub binaries and a temp git repo; reuse its fixtures/helpers verbatim):

```python
def test_start_dry_run_ensures_the_chain(tmp_path, capsys, monkeypatch):
    # Arrange exactly like the existing dry-run test in this file (temp git
    # repo as cwd, stubbed claude/wt/git ctx), then:
    from omc.agentsmd import chain_healthy

    rc = run_start(ctx, cfg, "PROJ-1 do the thing", dry_run=True)
    assert rc == 0
    assert chain_healthy(repo)  # chain exists even on dry runs


def test_start_proceeds_when_chain_is_blocked(tmp_path, capsys, monkeypatch):
    # Same arrangement, but pre-create a handwritten root file:
    (repo / "AGENTS.md").write_text("# handwritten\n")
    rc = run_start(ctx, cfg, "PROJ-1 do the thing", dry_run=True)
    assert rc == 0  # blocked chain never stops start
    assert (repo / "AGENTS.md").read_text() == "# handwritten\n"
```

(The implementer adapts variable names to the file's existing fixtures — the two assertions are the contract.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_start.py -v -k chain`
Expected: FAIL — `chain_healthy(repo)` is False (start never creates the chain today).

- [ ] **Step 3: Implement**

In `src/omc/start.py`, after the `plugin_status` lines in `run_start`:

```python
    root = repo_root(ctx)
    if root is not None:
        # Warn-but-proceed: a blocked chain is configure's fight, not start's.
        ensure_agents_chain(ctx, root)
```

Add imports: `from .agentsmd import ensure_agents_chain` and extend the existing `wtconfig` import with `repo_root` (check the file's current imports first).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_start.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/omc/start.py tests/unit/test_start.py
git commit -m "feat: omc start ensures the agents chain (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `omc watch` repairs the chain per tick

**Files:**
- Modify: `src/omc/watch.py` (new `_chain_tick` helper; wire into `run_watch`'s loop)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `chain_healthy`, `ensure_agents_chain` (Task 3).
- Produces: `watch._chain_tick(ctx, root, last) -> str` (chain outcome token; quiet-token narration like `_tick`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_watch.py` (reuse `_repo_with_origin`, `_ctx_with_node_stub`, `_run_once` helpers already in the file):

```python
def test_watch_repairs_a_dangling_chain(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    from omc.agentsmd import chain_healthy, ensure_agents_chain

    ensure_agents_chain(ctx, repo)
    (repo / "AGENTS.md").unlink()  # simulate a broken link
    assert not chain_healthy(repo)
    assert _run_once(repo, ctx) == 0
    assert chain_healthy(repo)  # tick repaired it
    assert "AGENTS.md" in capsys.readouterr().err  # repair narrates


def test_watch_blocked_chain_warns_once_and_never_stops_the_loop(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    (repo / "AGENTS.md").write_text("# handwritten\n")
    from omc.watch import _chain_tick

    first = _chain_tick(ctx, str(repo), None)
    capsys.readouterr()
    second = _chain_tick(ctx, str(repo), first)
    assert first == second == "chain-blocked"
    assert capsys.readouterr().err == ""  # quiet-token: repeat state is silent
    assert (repo / "AGENTS.md").read_text() == "# handwritten\n"


def test_watch_leaves_never_managed_repos_alone(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    from omc.watch import _chain_tick

    assert _chain_tick(ctx, str(repo), None) == "chain-absent"
    assert not (repo / "AGENTS.md").exists()  # watch never creates from nothing
    assert capsys.readouterr().err == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -v -k chain`
Expected: FAIL — `_chain_tick` doesn't exist; the repair test finds `chain_healthy` still False after `_run_once`.

- [ ] **Step 3: Implement**

In `src/omc/watch.py`, add (imports: `from .agentsmd import chain_healthy, ensure_agents_chain`). Design note: `ensure_agents_chain` narrates "blocked" on every call, so a blocked chain must NOT reach it on every tick — probe first, and skip the ensure while the blocking condition persists:

```python
def _chain_tick(ctx: ToolContext, root: str, last: str | None) -> str:
    """REPAIR the AGENTS.md chain; never create it from nothing (that is
    configure/start's job — watch must not mutate repos it merely observes).
    Healthy: silent. Repair: narrates (action outcome). Blocked: warn once
    per state change (quiet-token doctrine, like _tick), never block the loop."""
    if chain_healthy(root):
        return "chain-ok"
    root_p = Path(root)
    names = ("AGENTS.md", "CLAUDE.md")
    if not any((root_p / n).exists() or (root_p / n).is_symlink() for n in names):
        return "chain-absent"  # never chain-managed — silently leave it alone
    if last == "chain-blocked":
        # Still blocked (foreign root files don't vanish between ticks) —
        # re-running ensure would re-narrate the same warning every tick.
        if any((root_p / n).exists() and not (root_p / n).is_symlink() for n in names):
            return "chain-blocked"
    status = ensure_agents_chain(ctx, root)
    return "chain-blocked" if status == "blocked" else "chain-ok"
```

Wire into `run_watch` — inside the `while True:` loop, before the `last = _tick(...)` call:

```python
            chain_last = _chain_tick(ctx, root, chain_last)
```

and initialize `chain_last: str | None = None` next to `last: str | None = None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch.py -v`
Expected: all PASS (including the pre-existing quiet-state tests — the chain tick must not add noise to them; if any now fail on unexpected stderr, the chain tick is narrating when healthy — fix that, not the tests).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "feat: watch repairs the agents chain per tick, quiet-token narrated (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `omc update` — one-stop: uv upgrade + per-provider plugin updates

**Files:**
- Modify: `src/omc/providers/base.py` (new abstract method), `src/omc/providers/claude.py`, `src/omc/providers/codex.py`, `src/omc/providers/opencode.py`
- Modify: `src/omc/installer.py` (`run_update`)
- Test: `tests/unit/test_providers.py`, `tests/unit/test_installer.py`

**Interfaces:**
- Consumes: `config.store.load`, `providers.registry.get_provider` (existing).
- Produces: `Provider.plugin_update_argvs(self) -> list[list[str]]` (pure argv builder; `[]` = no scriptable path yet); `run_update(ctx) -> int` now fans out over `cfg.llm.providers`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_providers.py`:

```python
def test_plugin_update_argvs_are_pure_and_per_provider():
    from omc.providers.registry import get_provider

    claude = get_provider("claude").plugin_update_argvs()
    assert ["claude", "plugin", "marketplace", "update", "oh-my-clanker"] in claude
    assert ["claude", "plugin", "update", "omc@oh-my-clanker"] in claude
    codex = get_provider("codex").plugin_update_argvs()
    assert codex == [["codex", "plugin", "marketplace", "upgrade"]]
    assert get_provider("opencode").plugin_update_argvs() == []  # not scriptable yet
```

Append to `tests/unit/test_installer.py` (the file already builds ToolContexts with stub `uv`; follow its `_ctx`-style helper for PATH stubs, or add one):

```python
import stat

from omc.config import store
from omc.config.schema import Config, ProviderConfig


def _stub(bindir, name, rc=0):
    calls = bindir / f"{name}.calls"
    exe = bindir / name
    exe.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit {rc}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return calls


def _update_ctx(tmp_path, *, claude_rc=0):
    import os

    from omc.toolctx import ToolContext

    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv_calls = _stub(bindir, "uv")
    claude_calls = _stub(bindir, "claude", rc=claude_rc)
    codex_calls = _stub(bindir, "codex")
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    cfg = Config()
    cfg.llm.providers = {"claude": ProviderConfig(), "codex": ProviderConfig()}
    store.save(ctx.home, cfg)
    return ctx, uv_calls, claude_calls, codex_calls


def test_update_upgrades_then_updates_each_providers_plugin(tmp_path, capsys):
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(tmp_path)
    assert run_update(ctx) == 0
    assert "tool upgrade omc" in uv_calls.read_text()
    assert "plugin marketplace update oh-my-clanker" in claude_calls.read_text()
    assert "plugin update omc@oh-my-clanker" in claude_calls.read_text()
    assert "plugin marketplace upgrade" in codex_calls.read_text()


def test_update_isolates_provider_failures(tmp_path, capsys):
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(tmp_path, claude_rc=1)
    assert run_update(ctx) == 0  # a broken provider never fails the update
    assert "plugin marketplace upgrade" in codex_calls.read_text()  # codex still ran
    err = capsys.readouterr().err
    assert "claude" in err and "✗" in err  # failure narrated


def test_update_without_config_skips_plugins(tmp_path, capsys):
    import os

    from omc.toolctx import ToolContext

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc-home"),
         "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    assert run_update(ctx) == 0
    assert "skipping plugin updates" in capsys.readouterr().err
```

Note: `run_update` calls uv with `capture=False`, so the stub's `echo "$@"` writes to the calls file, not the test's capture — assertions read the calls files.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_providers.py::test_plugin_update_argvs_are_pure_and_per_provider tests/unit/test_installer.py -v`
Expected: FAIL — `plugin_update_argvs` undefined; `run_update` never touches claude/codex.

- [ ] **Step 3: Implement providers**

`src/omc/providers/base.py`, add to the ABC:

```python
    @abstractmethod
    def plugin_update_argvs(self) -> list[list[str]]:
        """Commands that update this provider's installed omc plugin, in order.

        [] means no scriptable update path is known yet — `omc update` says so
        and moves on. Builders stay pure (no I/O)."""
```

`src/omc/providers/claude.py`:

```python
    def plugin_update_argvs(self):
        # Marketplace snapshot first, then the plugin; claude docs: "restart
        # required to apply" — running sessions keep the old plugin.
        return [
            ["claude", "plugin", "marketplace", "update", "oh-my-clanker"],
            ["claude", "plugin", "update", "omc@oh-my-clanker"],
        ]
```

`src/omc/providers/codex.py`:

```python
    def plugin_update_argvs(self):
        # Refreshes ALL configured git marketplace snapshots (no per-marketplace
        # filter exists); plugins resolve from the refreshed snapshot. Verified
        # empirically in docker/PLUGIN-NOTES.md (Task 9 records the run).
        return [["codex", "plugin", "marketplace", "upgrade"]]
```

`src/omc/providers/opencode.py`:

```python
    def plugin_update_argvs(self):
        # opencode manages its plugin cache itself (git-ref entry in
        # opencode.json); no scriptable update verified yet — see
        # docker/PLUGIN-NOTES.md (Task 9 investigation).
        return []
```

- [ ] **Step 4: Implement `run_update`**

Replace `run_update` in `src/omc/installer.py` (add imports `from .config import store` and `from .providers.registry import get_provider`):

```python
def run_update(ctx: ToolContext) -> int:
    print("Updating omc via uv…", file=sys.stderr)
    rc = _uv(ctx, "tool", "upgrade", "omc")
    if rc != 0:
        return rc
    cfg = store.load(ctx.home)
    if cfg is None:
        print("· no config — skipping plugin updates (run `omc configure`)", file=sys.stderr)
        return 0
    for name in cfg.llm.providers:
        argvs = get_provider(name).plugin_update_argvs()
        if not argvs:
            print(f"· {name}: no scriptable plugin update yet — update it in-app", file=sys.stderr)
            continue
        ok = True
        for argv in argvs:
            try:
                cp = ctx.run(argv)
            except OSError as exc:
                print(f"✗ {name}: {argv[0]} not runnable ({exc}) — continuing", file=sys.stderr)
                ok = False
                break
            if cp.returncode != 0:
                detail = (cp.stderr or cp.stdout or "").strip()[:200]
                print(f"✗ {name}: {' '.join(argv)} failed: {detail} — continuing", file=sys.stderr)
                ok = False
                break
        if ok:
            print(f"✓ {name}: plugin updated", file=sys.stderr)
    return 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers.py tests/unit/test_installer.py -v`
Expected: all PASS (the pre-existing `run_update` test asserted only exit 0 with a uv stub and no config — it now exercises the skip path; adjust its assertions if it asserted exact stderr).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/omc/providers src/omc/installer.py tests/unit/test_providers.py tests/unit/test_installer.py
git commit -m "feat: omc update - uv upgrade + per-provider plugin updates, failure-isolated (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `omc internal gitnexus` proxy

**Files:**
- Modify: `src/omc/internal.py` (`_USAGE`, `run_internal` dispatch, new `_gitnexus`)
- Test: `tests/unit/test_internal.py`

**Interfaces:**
- Consumes: `gitnexus.gitnexus_cli/gitnexus_argv`, `wtconfig.primary_root`, `config.store.load` (all existing).
- Produces: CLI contract `omc internal gitnexus <query|context|impact|cypher> [args…]` — injects `--repo <primary basename> --branch <base>`, runs from the primary root, streams GitNexus output, returns its exit code. Task 8's SKILL.md rewrite depends on this exact invocation shape.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_internal.py` (reuse the file's existing patterns for ToolContext stubs; the node-stub helper mirrors `test_watch.py::_ctx_with_node_stub`):

```python
import os
import stat
import subprocess

from omc.internal import run_internal


def _chdir(path):
    old = os.getcwd()
    os.chdir(path)
    return old


def _gitnexus_env(tmp_path):
    """Real git repo + linked worktree + recording node stub + fake CLI + config."""
    from omc.config import store
    from omc.config.schema import Config

    repo = tmp_path / "primary"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "feat"], check=True
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\npwd >> "{calls}"\nexit 0\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "omc-home"
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake")
    store.save(home, Config())  # base_branch defaults to "main"
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return repo, wt, calls, env


def test_gitnexus_proxy_injects_scoping_and_runs_from_primary(tmp_path, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(wt)  # invoked from a WORKTREE
    try:
        rc = run_internal(["gitnexus", "query", "how does start work"])
    finally:
        os.chdir(old)
    assert rc == 0
    logged = calls.read_text()
    assert "query how does start work" in logged
    assert "--repo primary" in logged  # basename of the primary root
    assert "--branch main" in logged  # configured base branch
    assert str(repo) in logged  # pwd line: ran FROM the primary root


def test_gitnexus_proxy_rejects_unknown_subcommands(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        assert run_internal(["gitnexus", "analyze"]) == 2  # not a query verb
        assert run_internal(["gitnexus"]) == 2
    finally:
        os.chdir(old)


def test_gitnexus_proxy_errors_helpfully_without_the_cli(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    (tmp_path / "omc-home" / "dependencies").rename(tmp_path / "gone")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "query", "x"])
    finally:
        os.chdir(old)
    assert rc == 1
    assert "/omc:index" in capsys.readouterr().err  # install hint
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_internal.py -v -k gitnexus`
Expected: FAIL — `run_internal` prints usage and returns 2 for the unknown `gitnexus` command.

- [ ] **Step 3: Implement**

In `src/omc/internal.py`:

1. Update the usage line:

```python
_USAGE = "usage: omc internal {rebase-main [--base BRANCH] | wt-template | gitnexus <query|context|impact|cypher> [args…]}"
```

2. Add the handler:

```python
_GITNEXUS_VERBS = ("query", "context", "impact", "cypher")


def _gitnexus(ctx: ToolContext, rest: list[str]) -> int:
    """Scoped GitNexus proxy. GitNexus keys its DEFAULT store to the branch the
    repo was FIRST indexed on (which may since be deleted), while incremental
    analyze writes to .gitnexus/branches/<branch>/ — an unscoped query silently
    reads the frozen default store. So: always run from the PRIMARY root and
    always pin --repo (registry may hold several repos) and --branch (the
    configured base). gitnexus 1.6.x resolves --branch <base> against the
    branch store when one exists and falls back to the default store when the
    base branch IS the originally-indexed one — verify on a gitnexus upgrade.
    """
    if not rest or rest[0] not in _GITNEXUS_VERBS:
        print(_USAGE, file=sys.stderr)
        return 2
    if not gitnexus_cli(ctx).is_file():
        print(
            "error: GitNexus is not installed — run /omc:index once in a session first",
            file=sys.stderr,
        )
        return 1
    primary = primary_root(ctx)
    if primary is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    cfg = store.load(ctx.home)
    base = cfg.worktree.base_branch if cfg else "main"
    argv = gitnexus_argv(ctx, *rest, "--repo", Path(primary).name, "--branch", base)
    cp = ctx.run(argv, cwd=primary, capture=False)  # stream JSON straight through
    return cp.returncode
```

3. Wire the dispatch in `run_internal`, before the final usage fallback:

```python
    if cmd == "gitnexus":
        return _gitnexus(ToolContext.from_env(), rest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_internal.py -v`
Expected: all PASS.

- [ ] **Step 5: Live verification of the --branch edge case**

From THIS repo (which has a real index): `uv run omc internal gitnexus context ensure_agents_chain | head -5`
Expected: `"status": "found"` JSON (the fresh main branch store answers). If GitNexus errors on `--branch main`, STOP and re-read the docstring's assumption — adjust the injection (e.g. probe available branches first) and extend the unit tests accordingly; document what you found in the docstring.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "feat: omc internal gitnexus - deterministically scoped graph proxy (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: SKILL.md rewrites + contract tests

**Files:**
- Modify: `skills/gitnexus-explain/SKILL.md` (Steps 1–2 rewritten around the proxy; discards the uncommitted interim `--repo`/`--branch` prose edit already sitting in the working tree)
- Modify: `skills/integrate/SKILL.md` (line ~34 references `.omc/internal/AGENTS.md`)
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Consumes: the `omc internal gitnexus` contract (Task 7); chain v2 vocabulary (Task 3).
- Produces: skill prose that cannot drift from the proxy — locked by contract tests.

- [ ] **Step 1: Write the failing contract tests**

Append to `tests/unit/test_plugin_manifests.py` (follow the file's existing `test_gitnexus_ensure_contract` pattern for reading skill files):

```python
def test_gitnexus_explain_contract():
    text = (ROOT / "skills" / "gitnexus-explain" / "SKILL.md").read_text()
    assert "omc internal gitnexus" in text  # queries go through the proxy
    assert "--repo" not in text  # scoping is the proxy's job, not prose
    assert "--branch" not in text
    assert "node <CLI> query" not in text  # no raw CLI recipes remain


def test_integrate_skill_describes_chain_v2():
    text = (ROOT / "skills" / "integrate" / "SKILL.md").read_text()
    assert ".omc/internal/AGENTS.md" not in text  # v1 layer is retired
    assert "distribution" in text or "install" in text  # points at the v2 chain
```

(`ROOT` — reuse however the file locates the repo root; if it doesn't, add `ROOT = Path(__file__).resolve().parents[2]`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -v -k "explain_contract or chain_v2"`
Expected: FAIL on all four assertions groups.

- [ ] **Step 3: Rewrite `skills/gitnexus-explain/SKILL.md` Steps 1–2**

Replace the "Step 1 — ensure CLI + locate the graph" and "Step 2 — compose graph queries" sections with:

```markdown
## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill (installs/heals GitNexus under
`~/.omc/dependencies/gitnexus`).

## Step 2 — compose graph queries

There is no single `explain` CLI command — composing the query tools IS this
skill. Iterate until the question is answerable (prefer the graph over grep).
All queries go through omc's proxy, which resolves the graph location and
scoping deterministically (primary root, configured base branch) — pass ONLY
the verb and its arguments:

- `omc internal gitnexus query "<concept>"` — find the execution flows and
  symbols related to the question's concepts.
- `omc internal gitnexus context <symbol>` — 360° view of each load-bearing
  symbol (callers, callees, processes). Disambiguate with `--file <path>` if
  the name is shared.
- `omc internal gitnexus impact <symbol>` — blast radius, when the question
  is about change consequences ("what breaks if…").
- `omc internal gitnexus cypher "<stmt>"` — raw graph query for anything
  structural the higher-level commands can't express.

The proxy exits 1 with an install hint when GitNexus is missing — relay that
hint ("run `/omc:index` first") and stop; never index implicitly.
```

Also update Step 3's intro if it references "the chosen root" (there is no root choice anymore).

- [ ] **Step 4: Update `skills/integrate/SKILL.md`**

Find the inventory line mentioning `.omc/internal/AGENTS.md` (~line 34) and reword that checklist item to v2, e.g.:

```markdown
     root `AGENTS.md`/`CLAUDE.md` symlinks into the omc install's
     `distribution/AGENTS.md` (machine-local, gitignored), with project
     guidance committed at `.omc/config/AGENTS.md`?
```

Read the surrounding checklist to keep the item's grammar consistent with its siblings.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add skills/gitnexus-explain/SKILL.md skills/integrate/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: skills query the graph via the scoped proxy; integrate knows chain v2 (red->green)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Docker verification — chain E2E, codex/opencode plugin update investigation

**Files:**
- Create: `tests/e2e/test_e2e_chain.py`
- Modify: `docker/PLUGIN-NOTES.md` (append findings), `src/omc/providers/codex.py` / `src/omc/providers/opencode.py` (only if findings contradict Task 6's argvs)
- Test: itself (E2E) + `tests/unit/test_providers.py` (only on contradiction)

**Interfaces:**
- Consumes: the built E2E image (`docker build -f docker/Dockerfile.e2e -t omc-e2e:dev .`), Task 3's chain, Task 6's argvs.
- Produces: an executable record that the chain works in a clean container and that the codex/opencode update paths do what Task 6 claims.

- [ ] **Step 1: Write the chain E2E test**

Create `tests/e2e/test_e2e_chain.py` (mirror the container fixture usage in `tests/e2e/test_e2e_gitnexus.py` — same `container` fixture from `tests/e2e/conftest.py`, same `pytest.mark.e2e`):

```python
"""Chain v2 in a clean container: create from nothing, migrate from v1."""

import pytest

pytestmark = pytest.mark.e2e

SETUP = """
set -e
git init -q /tmp/proj && cd /tmp/proj
git config user.email t@t && git config user.name t
"""

CREATE_CHECK = """
cd /tmp/proj
omc configure --defaults >/dev/null 2>&1 || true
test -L AGENTS.md && test -L CLAUDE.md
readlink AGENTS.md | grep -q 'distribution/AGENTS.md'
grep -q '^/AGENTS.md$' .gitignore && grep -q '^/CLAUDE.md$' .gitignore
test -f .omc/config/AGENTS.md
test ! -e .omc/internal/AGENTS.md
head -1 "$(omc print-install-path)/distribution/AGENTS.md" | grep -q 'omc behavior layer'
"""

MIGRATE_SETUP = """
set -e
git init -q /tmp/v1 && cd /tmp/v1
git config user.email t@t && git config user.name t
mkdir -p .omc/internal .omc/config
echo '# omc behavior layer (generated)' > .omc/internal/AGENTS.md
echo '# mine' > .omc/config/AGENTS.md
ln -s .omc/internal/AGENTS.md AGENTS.md
ln -s .omc/internal/AGENTS.md CLAUDE.md
"""

MIGRATE_CHECK = """
cd /tmp/v1
omc configure --defaults >/dev/null 2>&1 || true
readlink AGENTS.md | grep -q 'distribution/AGENTS.md'
test ! -e .omc/internal
grep -q '# mine' .omc/config/AGENTS.md
"""


def test_chain_creates_and_migrates_in_container(container):
    for script in (SETUP, CREATE_CHECK, MIGRATE_SETUP, MIGRATE_CHECK):
        code, out = container.exec_run(["bash", "-lc", script])
        assert code == 0, out
```

Adapt the exec call to the actual `container` fixture API in `tests/e2e/conftest.py` (read it first — it may expose a `run(script)` helper rather than raw `exec_run`).

- [ ] **Step 2: Run the E2E test**

Run: `docker build -f docker/Dockerfile.e2e -t omc-e2e:dev . && uv run pytest tests/e2e/test_e2e_chain.py -v -m e2e`
Expected: PASS. If `omc configure --defaults` exits non-zero in-container for an unrelated reason, capture the output — do not paper over it with the `|| true` (that guard exists only for the plugin-hint tail).

- [ ] **Step 3: Verify codex plugin update empirically**

In a fresh container (`docker run --rm -it omc-e2e:dev bash`):

```bash
codex plugin marketplace add /repo
codex plugin marketplace list          # note the snapshot state
cd /repo && git log --oneline -1       # current marketplace content
codex plugin marketplace upgrade      # THE command Task 6 wired
codex plugin marketplace list          # did the snapshot refresh?
```

Record the exact commands + output in a new `## omc update: per-provider plugin update verification (COPS-987)` section appended to `docker/PLUGIN-NOTES.md`. If `upgrade` does NOT refresh (or needs different arguments), fix `CodexProvider.plugin_update_argvs` AND the assertion in `tests/unit/test_providers.py::test_plugin_update_argvs_are_pure_and_per_provider`, and re-run the unit suite.

- [ ] **Step 4: Investigate opencode plugin updating**

In the same container: `opencode --help 2>&1 | head -40`, look for plugin/cache subcommands; check `~/.config/opencode/` cache layout; consult the plugin entry docs (`opencode.json` `"plugin"` array). Answer: is there a scriptable "re-fetch the git-ref plugin" command? Record findings in the same PLUGIN-NOTES.md section. If a verified command exists, implement it in `OpencodeProvider.plugin_update_argvs` (+ update the unit test's `== []` assertion); if not, the `[]` + in-app hint stands, with the note explaining why.

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest tests/unit -q
uv run ruff check src tests && uv run ruff format --check src tests
git add tests/e2e/test_e2e_chain.py docker/PLUGIN-NOTES.md src/omc/providers tests/unit/test_providers.py
git commit -m "test: chain v2 E2E + codex/opencode plugin-update verification record

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task order and independence

1 → 2 → 3 → 4 → 5 (chain track, strictly ordered). 6 is independent of 1–5. 7 → 8 (proxy track, independent of the chain track). 9 last (needs 3 + 6). A reviewer can gate each task on its own test cycle.
