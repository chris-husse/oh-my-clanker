# Stale Knowledge Snapshot Self-Heal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** omc computes a freshness verdict for the primary checkout's GitNexus snapshot, reports it from every surface (`omc start`, the query proxy, `rebase-main`, a new `status` verb), and repairs it from exactly one code path shared by `omc watch` and a new `omc internal gitnexus refresh` verb — never from `omc start`.

**Architecture:** `src/omc/gitnexus.py` gains a pure verdict (`snapshot_freshness`) and the single repair function (`refresh_knowledge`, absorbing `watch._heal_store`). `watch.py` becomes verdict-driven on its up-to-date and synced paths, gains the `healed` / `knowledge-stale:<codes>` tokens and `--reset-gitnexus`. `internal.py` gains `refresh`/`status` and carries the `OMC_KNOWLEDGE` line on the proxy's stderr and inside `OMC_REBASE_MAIN`. `start.py` alerts and seeds the verdict. Skills delegate to the new verb.

**Tech Stack:** Python 3.12, `filelock`, real git in tests, `/bin/sh` stubs on a restricted PATH, pytest via `uv run python -m pytest`, ruff via `uvx ruff`, GitNexus (fork `chris-husse/GitNexus` ≥ `8de99dc9`, which owns the wiki-regroup and copied-index fixes — omc carries NO workaround for either).

**Spec:** `docs/superpowers/specs/2026-09-27-stale-knowledge-snapshot-self-heal-design.md` — the single source of truth (amended after the plan pressure tests: `acquire_busy_narrated` is a context manager; `reset_pending` also clears on a `knowledge-stale:<codes>` token; the start alert renders one `  · <text>` line per reason; start narrates `→ fetching origin/<base>`; inversion is judged from the chosen metadata file's `branch`). Every reason code, token, message string, exit code and file name below is copied from it; when in doubt, the spec wins.

## Global Constraints

- red → green for EVERY change: write the failing test first, run it, watch it fail for the expected reason, then implement. EVERY task's commit leaves the unit suite green.
- never `pytest.skip` / `skipif`; a missing prerequisite is a `pytest.fail` naming the fix.
- stubs on a restricted PATH use shell builtins or absolute paths (`/bin/cat`, `/usr/bin/git`, `printf`); bare `touch`/`cat`/`git` silently break. Test FILES may use `subprocess` directly (they are not `src/`).
- assert on artifacts (files, argv logs, exit codes, JSON), not transcripts.
- `ToolContext` (`src/omc/toolctx.py`) is the ONLY subprocess/env boundary — nothing else in `src/omc` imports `subprocess` at runtime.
- machine contracts are single-line JSON (`OMC_KNOWLEDGE {…}`, `OMC_REBASE_MAIN {…}`); emitters never wrap them.
- CLI phases narrate on stderr with `→ ✓ ✗ ·`; a silent minute is a bug.
- never run `omc install` / `uv tool install` / `uv tool upgrade`.
- run unit tests from the worktree as `uv run python -m pytest tests/unit/… -q` (NOT bare `uv run pytest`, whose venv shebang resolves to the primary checkout's code).
- before EVERY commit: `uvx ruff check src tests && uvx ruff format --check src tests` must be clean (line length 100; `uvx ruff format <files>` fixes formatting).
- E2E (`just e2e-tests`, Docker + real GitNexus + tokens) is NOT part of the per-task loop; Task 9 only writes the test.
- commit each task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- `/omc:check` after every task (the conductor runs it); a failing check blocks the next task.

---

## File map

| File | Responsibility after this plan |
|---|---|
| `src/omc/wikirun.py` (new) | `PageCountTracker`, `_WIKI_STALL_SECONDS`, `_WIKI_POLL_SECONDS` — wiki-run supervision plumbing shared by dependency docs and the project wiki |
| `src/omc/dependency.py` | re-exports the three names; `run_document` unchanged otherwise |
| `src/omc/gitnexus.py` | + `Reason`, `Freshness`, `read_index_meta`, `read_wiki_meta`, `snapshot_freshness`, `refresh_knowledge` (+ private `_destroy_and_rebuild`, `_run_wiki`) |
| `src/omc/watchlock.py` | + `acquire_busy_narrated` context manager, `BUSY_WAIT_MSG`; `START_WAIT_MSG` reworded; docstring invariant |
| `src/omc/watch.py` | `_heal_store` removed; `_refresh_index` thin wrapper; verdict-driven `_tick` with `healed` / `knowledge-stale:<codes>`; `--reset-gitnexus`; busy-lock narration |
| `src/omc/cli/__init__.py` | `--reset-gitnexus` flag + dispatch |
| `src/omc/internal.py` | `gitnexus refresh` / `gitnexus status`; proxy stderr verdict; `knowledge` in `OMC_REBASE_MAIN`; docstring exit codes |
| `src/omc/start.py` | reordered `run_start`; `→ fetching` line; alert block; `build_start_seed(context, knowledge=None)`; dry-run `knowledge` row |
| skills `gitnexus-index`, `gitnexus-document`, `gitnexus-explain`, `explain`, `plan`, `start`, `rebase-main` | delegate / relay `OMC_KNOWLEDGE` |
| `src/omc/distribution/AGENTS.md`, `.omc/config/AGENTS.md`, `.omc/skills/review/SKILL.md`, `.omc/skills/explain-context/SKILL.md`, `README.md` | contract listings + docs |
| `docker/Dockerfile.e2e` | pins `GITNEXUS_REF` so the E2E image carries the fork's fixes |
| tests | `test_wikirun.py`, `test_gitnexus_freshness.py`, `test_gitnexus_refresh.py` (new); `test_watch.py`, `test_internal.py`, `test_start.py`, `test_start_mutex.py`, `test_watchlock.py`, `test_plugin_manifests.py` (updated); `tests/e2e/test_e2e_watch.py` (+1) |

---

### Task 1: Extract wiki-run plumbing into `src/omc/wikirun.py`

**Model:** standard coding tier

**Files:**
- Create: `src/omc/wikirun.py`
- Modify: `src/omc/dependency.py:35-41` (constants) and `:170-233` (`PageCountTracker` class)
- Test: `tests/unit/test_wikirun.py` (new); `tests/unit/test_dependency.py` must stay byte-identical and green

**Interfaces:**
- Produces: `omc.wikirun.PageCountTracker(wiki_dir: Path)` with `.refresh()`, `.state() -> tuple[int|None,int]`, `.beat()`, `.percent`; `omc.wikirun._WIKI_STALL_SECONDS = 300.0`; `omc.wikirun._WIKI_POLL_SECONDS = 1.0`. `omc.dependency` re-exports all three (same objects).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_wikirun.py
"""wikirun: the wiki-run supervision plumbing shared by dependency docs and the
project wiki (spec 2026-09-27-stale-knowledge-snapshot-self-heal §2)."""

import json


def test_tracker_lives_in_wikirun_and_is_reexported_by_dependency():
    import omc.dependency as dep
    import omc.wikirun as wr

    assert dep.PageCountTracker is wr.PageCountTracker
    assert dep._WIKI_STALL_SECONDS is wr._WIKI_STALL_SECONDS
    assert dep._WIKI_POLL_SECONDS is wr._WIKI_POLL_SECONDS
    assert wr._WIKI_STALL_SECONDS == 300.0 and wr._WIKI_POLL_SECONDS == 1.0


def test_tracker_counts_modules_pages_and_overview(tmp_path):
    from omc.wikirun import PageCountTracker

    (tmp_path / "first_module_tree.json").write_text(
        json.dumps([{"slug": "a", "children": [{"slug": "b"}]}, {"slug": "c"}])
    )
    (tmp_path / "a.md").write_text("x")
    t = PageCountTracker(tmp_path)
    assert t.beat() == (4, 1)  # 3 modules + overview; one page down
    assert t.percent == 25


def test_dependency_monkeypatch_of_constants_still_reaches_run_document(monkeypatch):
    """test_dependency.py patches `omc.dependency._WIKI_*`; run_document must keep
    reading those names from ITS OWN module namespace, or the patches go dead."""
    import inspect

    import omc.dependency as dep

    src = inspect.getsource(dep.run_document)
    assert "stall_after=_WIKI_STALL_SECONDS" in src
    assert "poll=_WIKI_POLL_SECONDS" in src
    monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", 0.5)
    assert dep._WIKI_STALL_SECONDS == 0.5  # rebinding the re-export is what tests do
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/unit/test_wikirun.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'omc.wikirun'`

- [ ] **Step 3: Create `src/omc/wikirun.py`**

Move the class and constants verbatim (cut from `dependency.py`, do not retype):

```python
"""Wiki-run supervision plumbing shared by dependency docs and the project wiki.

`ToolContext.run_supervised` kills a `gitnexus wiki` child only after
`_WIKI_STALL_SECONDS` with NO progress, where progress = the heartbeat token
changed OR output bytes arrived. `PageCountTracker` is that heartbeat: it reads
progress from DISK (gitnexus's own bar is TTY-gated and emits nothing through a
pipe). The heartbeat is indeterminate until gitnexus has written
first_module_tree.json (the grouping phase); output bytes carry liveness there.
"""

from __future__ import annotations

import json
from pathlib import Path

# Liveness window for wiki generation: the run may take 40+ minutes, but 300s
# with zero progress (no new page on disk, no child output) marks a wedge.
_WIKI_STALL_SECONDS = 300.0

# One disk poll per second drives BOTH the stall-guard heartbeat and progress
# reporting; monkeypatchable in tests.
_WIKI_POLL_SECONDS = 1.0


class PageCountTracker:
    ...  # the existing class body from dependency.py lines 170-233, unchanged
```

- [ ] **Step 4: Re-export from `dependency.py`**

Delete the two constant definitions and the class from `dependency.py`; add to its imports (this exact ordering satisfies ruff I001):

```python
from .wikirun import _WIKI_POLL_SECONDS, _WIKI_STALL_SECONDS, PageCountTracker  # noqa: F401
```

`run_document` already references `_WIKI_STALL_SECONDS` / `_WIKI_POLL_SECONDS` / `PageCountTracker` as module globals of `dependency` — leave those references untouched so `monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", …)` keeps working.

- [ ] **Step 5: Run tests and lint**

Run: `uv run python -m pytest tests/unit/test_wikirun.py tests/unit/test_dependency.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS (all; `test_dependency.py` unmodified), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/omc/wikirun.py src/omc/dependency.py tests/unit/test_wikirun.py
git commit -m "refactor: move wiki-run supervision plumbing into wikirun.py

PageCountTracker and the stall/poll constants are needed by the project
wiki refresh too; dependency.py re-exports them so its tests keep patching
the same names.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The freshness verdict (`snapshot_freshness`)

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/gitnexus.py` (imports lines 10-16; append after `store_inverted`, line 68)
- Test: `tests/unit/test_gitnexus_freshness.py` (new)

**Interfaces:**
- Consumes: `flat_store_branch(root)` (unchanged), `ToolContext.run`, `ToolContext.git_bin`. `store_inverted` stays untouched for its other callers; the verdict judges inversion from the CHOSEN metadata dict's `branch` (so a `gitnexus.json` file is honoured).
- Produces (spec §1, verbatim names):

```python
@dataclass(frozen=True)
class Reason:
    code: str
    text: str
    detail: dict

@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reasons: tuple[Reason, ...]
    fix: str        # bare command or ""
    run_in: str     # absolute primary root
    basis: str      # "origin/<base>" | "HEAD" | "unresolved"
    def to_json(self) -> dict  # {"fresh","basis","reasons":[{"code","text","detail"}],"fix","run_in"}
    def codes(self) -> list[str]
    def index_codes(self) -> list[str]
    def wiki_codes(self) -> list[str]

INDEX_META_NAMES = ("gitnexus.json", "meta.json")
def read_index_meta(root: Path) -> dict | None
def read_wiki_meta(root: Path) -> dict | None
def snapshot_freshness(ctx, primary_root, base, *, ref=None, documentation=True) -> Freshness
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_gitnexus_freshness.py
"""snapshot_freshness — one test per reason code (spec §1)."""

import json
import os
import subprocess

from omc.gitnexus import Freshness, snapshot_freshness
from omc.toolctx import ToolContext


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("one\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    return origin, repo


def _commit(repo, name):
    (repo / name).write_text(name)
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", name, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _seed_index(
    repo, *, last=None, branch="main", repo_path=None, dirty=False, name="meta.json"
):
    d = repo / ".gitnexus"
    d.mkdir(exist_ok=True)
    meta = {
        "branch": branch,
        "lastCommit": last if last is not None else _git("rev-parse", "HEAD", cwd=repo),
        "repoPath": repo_path if repo_path is not None else str(repo),
    }
    if dirty:
        meta["incrementalInProgress"] = {"startedAt": 1, "toWriteCount": 3}
    (d / name).write_text(json.dumps(meta))


def _seed_wiki(repo, from_commit):
    w = repo / ".gitnexus" / "wiki"
    w.mkdir(parents=True, exist_ok=True)
    (w / "meta.json").write_text(json.dumps({"fromCommit": from_commit, "moduleFiles": {}}))


def _ctx():
    return ToolContext.from_env({"HOME": os.environ["HOME"], "PATH": os.environ["PATH"]})


def _codes(v: Freshness):
    return [r.code for r in v.reasons]


def test_fresh_index_and_wiki(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    _seed_wiki(repo, _git("rev-parse", "HEAD", cwd=repo))
    v = snapshot_freshness(_ctx(), repo, "main")
    assert v.fresh and v.reasons == () and v.fix == "" and v.run_in == str(repo.resolve())
    assert v.basis == "origin/main"
    assert v.to_json() == {
        "fresh": True,
        "basis": "origin/main",
        "reasons": [],
        "fix": "",
        "run_in": str(repo.resolve()),
    }


def test_index_missing(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    v = snapshot_freshness(_ctx(), repo, "main")
    assert _codes(v) == ["index-missing"] and not v.fresh
    assert v.fix == "omc watch --once"  # no wiki reason when the index itself is missing


def test_store_inverted_skips_distance_codes(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, branch="feature/x", last="0" * 40)  # unknown commit, but inverted wins
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["store-inverted"]
    assert v.reasons[0].detail == {"owner": "feature/x"}


def test_inversion_judged_from_gitnexus_json_when_present(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, name="gitnexus.json", branch="feature/x")
    _seed_index(repo, name="meta.json", branch="main")  # legacy mirror says main: ignored
    assert _codes(snapshot_freshness(_ctx(), repo, "main", documentation=False)) == [
        "store-inverted"
    ]


def test_index_foreign_and_realpath_alias(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, repo_path="/Users/someone/Projects/app")
    assert "index-foreign" in _codes(snapshot_freshness(_ctx(), repo, "main"))
    link = tmp_path / "alias"
    os.symlink(repo, link)
    _seed_index(repo, repo_path=str(link))
    assert "index-foreign" not in _codes(snapshot_freshness(_ctx(), repo, "main"))


def test_missing_repo_path_is_not_foreign(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus").mkdir()
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": _git("rev-parse", "HEAD", cwd=repo)})
    )
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert "index-foreign" not in _codes(v)


def test_index_dirty(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, dirty=True)
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-dirty"] and v.reasons[0].detail == {"startedAt": 1}


def test_index_unknown(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, last="a" * 40)
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-unknown"]


def test_index_behind_counts_commits(tmp_path):
    origin, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)  # at c1
    _commit(repo, "c2")
    _commit(repo, "c3")
    _git("push", "-q", "origin", "main", cwd=repo)
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-behind"] and v.reasons[0].detail == {"count": 2}
    assert "2 commits behind origin/main" in v.reasons[0].text


def test_index_diverged(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _git("switch", "-qc", "feature/side", cwd=repo)
    side = _commit(repo, "side")
    _git("switch", "-q", "main", cwd=repo)
    _seed_index(repo, last=side)  # known object, not an ancestor of origin/main
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-diverged"]


def test_ref_head_instead_of_origin(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    _commit(repo, "local-only")  # HEAD ahead of origin/main, not pushed
    against_origin = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    against_head = snapshot_freshness(_ctx(), repo, "main", ref="HEAD", documentation=False)
    assert against_origin.fresh  # index == origin/main
    assert _codes(against_head) == ["index-behind"] and against_head.basis == "HEAD"


def test_unresolved_ref_skips_distances(tmp_path):
    repo = tmp_path / "solo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("x")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c", cwd=repo)
    _seed_index(repo)  # no origin at all
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert v.basis == "unresolved" and v.fresh


def test_gitnexus_json_preferred_and_meta_json_ignored(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, name="gitnexus.json")
    _seed_index(repo, name="meta.json", dirty=True)  # would be index-dirty if read
    assert snapshot_freshness(_ctx(), repo, "main", documentation=False).fresh


def test_unreadable_meta_is_missing(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus").mkdir()
    (repo / ".gitnexus" / "meta.json").write_text("{not json")
    assert _codes(snapshot_freshness(_ctx(), repo, "main")) == ["index-missing"]


def test_wiki_missing_and_fix_string(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    v = snapshot_freshness(_ctx(), repo, "main")
    assert _codes(v) == ["wiki-missing"]
    assert v.fix == "omc watch --once --enable-documentation"


def test_wiki_unknown(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    _seed_wiki(repo, "b" * 40)
    assert _codes(snapshot_freshness(_ctx(), repo, "main")) == ["wiki-unknown"]


def test_wiki_behind_compares_against_index_not_ref(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    c1 = _git("rev-parse", "HEAD", cwd=repo)
    _seed_wiki(repo, c1)
    _commit(repo, "c2")
    c3 = _commit(repo, "c3")
    _seed_index(repo, last=c3)  # index moved past the docs by 2
    v = snapshot_freshness(_ctx(), repo, "main", ref="HEAD")
    assert _codes(v) == ["wiki-behind"] and v.reasons[0].detail["count"] == 2


def test_wiki_ahead_of_index_is_not_behind(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    c1 = _git("rev-parse", "HEAD", cwd=repo)
    c2 = _commit(repo, "c2")
    _seed_index(repo, last=c1)
    _seed_wiki(repo, c2)  # docs generated AFTER the index commit
    v = snapshot_freshness(_ctx(), repo, "main", ref="HEAD")
    assert _codes(v) == ["index-behind"]  # only the index is stale


def test_documentation_false_skips_wiki_checks(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    assert snapshot_freshness(_ctx(), repo, "main", documentation=False).fresh


def test_all_applicable_reasons_reported(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, dirty=True, repo_path="/elsewhere")
    _commit(repo, "c2")
    _git("push", "-q", "origin", "main", cwd=repo)
    assert _codes(snapshot_freshness(_ctx(), repo, "main", documentation=False)) == [
        "index-foreign",
        "index-dirty",
        "index-behind",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_gitnexus_freshness.py -q`
Expected: FAIL — `ImportError: cannot import name 'Freshness' from 'omc.gitnexus'`

- [ ] **Step 3: Implement in `src/omc/gitnexus.py`**

`gitnexus.py` imports today only `json`, `re`, `sys`, `Path`, `TYPE_CHECKING`, `ToolContext`. Add `import os` and `from dataclasses import dataclass, field`. Append after `store_inverted`:

```python
# ─── Freshness verdict (spec 2026-09-27-stale-knowledge-snapshot-self-heal §1) ──

# Index metadata: upstream GitNexus renamed meta.json → gitnexus.json and keeps
# meta.json as a legacy mirror; the fork still writes only meta.json. Read the
# new name first; when both exist the legacy file is ignored entirely.
INDEX_META_NAMES = ("gitnexus.json", "meta.json")
WIKI_META_REL = Path(".gitnexus") / "wiki" / "meta.json"


def _read_json_object(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_index_meta(root: Path) -> dict | None:
    for name in INDEX_META_NAMES:
        p = Path(root) / ".gitnexus" / name
        if p.is_file():
            return _read_json_object(p)  # unreadable counts as missing, no fallback
    return None


def read_wiki_meta(root: Path) -> dict | None:
    return _read_json_object(Path(root) / WIKI_META_REL)


@dataclass(frozen=True)
class Reason:
    code: str
    text: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reasons: tuple[Reason, ...]
    fix: str
    run_in: str
    basis: str

    def to_json(self) -> dict:
        return {
            "fresh": self.fresh,
            "basis": self.basis,
            "reasons": [
                {"code": r.code, "text": r.text, "detail": dict(r.detail)} for r in self.reasons
            ],
            "fix": self.fix,
            "run_in": self.run_in,
        }

    def codes(self) -> list[str]:
        return [r.code for r in self.reasons]

    def index_codes(self) -> list[str]:
        return [c for c in self.codes() if not c.startswith("wiki-")]

    def wiki_codes(self) -> list[str]:
        return [c for c in self.codes() if c.startswith("wiki-")]


def _git_ok(ctx: ToolContext, root: Path, *args: str) -> bool:
    return ctx.run([ctx.git_bin, *args], cwd=str(root)).returncode == 0


def _git_out(ctx: ToolContext, root: Path, *args: str) -> str:
    cp = ctx.run([ctx.git_bin, *args], cwd=str(root))
    return (cp.stdout or "").strip() if cp.returncode == 0 else ""


def _same_checkout(recorded: str, root: Path) -> bool:
    a = os.path.realpath(recorded)
    b = os.path.realpath(str(root))
    if sys.platform == "darwin":
        return a.casefold() == b.casefold()
    return a == b


def _is_commit(ctx: ToolContext, root: Path, sha: object) -> bool:
    return isinstance(sha, str) and bool(sha) and _git_ok(ctx, root, "cat-file", "-e", f"{sha}^{{commit}}")


def snapshot_freshness(
    ctx: ToolContext,
    primary_root: Path | str,
    base: str,
    *,
    ref: str | None = None,
    documentation: bool = True,
) -> Freshness:
    """Is the primary's knowledge snapshot trustworthy? Pure: reads two metadata
    files and asks git; never repairs. Distances are measured against ``ref``
    (default origin/<base>; watch passes HEAD). Callers gate on a resolved
    primary root and fetch first when they can."""
    root = Path(primary_root).resolve()
    ref = ref or f"origin/{base}"
    reasons: list[Reason] = []
    ref_ok = _git_ok(ctx, root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    basis = ref if ref_ok else "unresolved"

    meta = read_index_meta(root)
    if meta is None:
        reasons.append(Reason("index-missing", "no knowledge snapshot yet (no GitNexus index)"))
    else:
        # Inversion from the CHOSEN metadata file (store_inverted reads only
        # meta.json; it stays as-is for its other callers).
        owner = meta.get("branch")
        inverted = isinstance(owner, str) and bool(owner) and owner != base
        if inverted:
            reasons.append(
                Reason(
                    "store-inverted",
                    f"index is owned by branch {owner!r}, not {base!r}",
                    {"owner": owner},
                )
            )
        recorded = meta.get("repoPath")
        if isinstance(recorded, str) and recorded and not _same_checkout(recorded, root):
            reasons.append(
                Reason(
                    "index-foreign",
                    f"index was built for another checkout ({recorded})",
                    {"repoPath": recorded},
                )
            )
        dirty = meta.get("incrementalInProgress")
        if dirty:
            started = dirty.get("startedAt") if isinstance(dirty, dict) else None
            reasons.append(
                Reason("index-dirty", "previous analyze did not finish", {"startedAt": started})
            )
        last = meta.get("lastCommit")
        if not inverted and ref_ok and isinstance(last, str) and last:
            if not _is_commit(ctx, root, last):
                reasons.append(
                    Reason(
                        "index-unknown",
                        f"index commit {last[:12]} is not in this clone",
                        {"lastCommit": last},
                    )
                )
            elif not _git_ok(ctx, root, "merge-base", "--is-ancestor", last, ref):
                reasons.append(
                    Reason(
                        "index-diverged",
                        f"index commit {last[:12]} is not reachable from {ref} in this clone",
                        {"lastCommit": last},
                    )
                )
            else:
                n = int(_git_out(ctx, root, "rev-list", "--count", f"{last}..{ref}") or 0)
                if n > 0:
                    reasons.append(
                        Reason("index-behind", f"index is {n} commits behind {ref}", {"count": n})
                    )
        if documentation:
            wiki = read_wiki_meta(root)
            if wiki is None:
                reasons.append(Reason("wiki-missing", "no generated docs yet"))
            else:
                fc = wiki.get("fromCommit")
                if not _is_commit(ctx, root, fc):
                    reasons.append(
                        Reason(
                            "wiki-unknown",
                            "docs were generated from a commit not in this clone",
                            {"fromCommit": fc},
                        )
                    )
                elif _is_commit(ctx, root, last) and not _git_ok(
                    ctx, root, "merge-base", "--is-ancestor", last, fc
                ):
                    n = int(_git_out(ctx, root, "rev-list", "--count", f"{fc}..{last}") or 0)
                    reasons.append(
                        Reason(
                            "wiki-behind",
                            f"docs are {n} commits behind the index",
                            {"fromCommit": fc, "count": n},
                        )
                    )

    codes = [r.code for r in reasons]
    if not codes:
        fix = ""
    elif any(c.startswith("wiki-") for c in codes):
        fix = "omc watch --once --enable-documentation"
    else:
        fix = "omc watch --once"
    return Freshness(
        fresh=not reasons, reasons=tuple(reasons), fix=fix, run_in=str(root), basis=basis
    )
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run python -m pytest tests/unit/test_gitnexus_freshness.py tests/unit/test_gitnexus_store.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS, ruff clean (run `uvx ruff format src/omc/gitnexus.py tests/unit/test_gitnexus_freshness.py` if format complains).

- [ ] **Step 5: Commit**

```bash
git add src/omc/gitnexus.py tests/unit/test_gitnexus_freshness.py
git commit -m "feat(gitnexus): snapshot_freshness verdict over index and wiki metadata

Reason codes index-missing/store-inverted/index-foreign/index-dirty/
index-unknown/index-diverged/index-behind/wiki-missing/wiki-unknown/
wiki-behind, measured against origin/<base> or HEAD; fix + run_in.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `refresh_knowledge` — the single repair function, watch routed through it

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/gitnexus.py` (append), `src/omc/watch.py:218-299` (`_heal_store` removed, `_refresh_index` becomes a wrapper), `src/omc/watch.py:29-36` (imports)
- Test: `tests/unit/test_gitnexus_refresh.py` (new); `tests/unit/test_watch.py` fixtures `_repo_with_origin` (line 18), `_ctx_with_node_stub` (62), `_ctx_with_healing_node_stub` (884), tests at lines 111 and 120, `test_heal_survives_an_undeletable_docs_mirror` (1010)

**Interfaces:**
- Consumes: Task 2's `snapshot_freshness`/`Freshness`; Task 1's `PageCountTracker`, `_WIKI_STALL_SECONDS`, `_WIKI_POLL_SECONDS`; `mirror.clear_docs_mirror`, `mirror.mirror_dir`, `mirror.DOCS_MIRROR_REL`; `providers.registry.docs_model_for`; `ToolContext.run_supervised`.
- Produces:

```python
def refresh_knowledge(ctx, cfg, root, base, *, documentation: bool, reset: bool,
                      ref: str = "HEAD", say=_say) -> Freshness
```
`watch._refresh_index(ctx, cfg, root, enable_documentation, *, reset=False) -> Freshness` (wrapper). `watch._heal_store` is deleted. `omc.gitnexus._WIKI_POLL_SECONDS` / `_WIKI_STALL_SECONDS` are the bindings the project-wiki path reads (monkeypatch THOSE in tests).

- [ ] **Step 1: Update the watch fixtures and the two `--once` tests (all in `tests/unit/test_watch.py`)**

Add `import sys` to the imports if absent, plus a helper next to `_git`:

```python
def _git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
```

`_repo_with_origin` — exclude the generated dirs (so `git add .` in the rebase tests never commits them) and seed FRESH metadata:

```python
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text(".gitnexus/\n.omc/docs/\n")
    _seed_fresh_index(repo)
    return origin, repo


def _seed_fresh_index(repo, branch="main"):
    """A metadata file that snapshot_freshness judges fresh at HEAD (index side)."""
    d = repo / ".gitnexus"
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps(
            {"branch": branch, "lastCommit": _git_out(repo, "rev-parse", "HEAD"), "repoPath": str(repo)}
        )
    )
```

`_ctx_with_node_stub` — the stub grows side effects (shell builtins + absolute paths only; `echo ok` stays unconditional so `ensure_gitnexus`'s `--version` probe passes):

```python
    node.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'case "$*" in\n'
        '  *" clean --force") rm -rf .gitnexus ;;\n'
        '  *" analyze --skip-agents-md --skip-skills") mkdir -p .gitnexus; '
        'printf \'{"branch":"main","lastCommit":"%s","repoPath":"%s"}\' '
        '"$(/usr/bin/git rev-parse HEAD)" "$PWD" > .gitnexus/meta.json ;;\n'
        '  *" wiki --provider"*) mkdir -p .gitnexus/wiki; '
        'printf \'{"fromCommit":"%s","moduleFiles":{}}\' "$(/usr/bin/git rev-parse HEAD)" '
        "> .gitnexus/wiki/meta.json; printf 'page' > .gitnexus/wiki/index.md ;;\n"
        "esac\n"
        "echo ok\nexit 0\n"
    )
```

`_ctx_with_healing_node_stub` — replace its `node.write_text(...)` block verbatim with:

```python
    node.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'case "$*" in\n'
        f'  *" clean --force") {clean_cmd} ;;\n'
        '  *" analyze --skip-agents-md --skip-skills") mkdir -p .gitnexus; '
        f'printf \'{{"branch":"{analyze_stamps}","lastCommit":"%s","repoPath":"%s"}}\' '
        '"$(/usr/bin/git rev-parse HEAD)" "$PWD" > .gitnexus/meta.json ;;\n'
        '  *" wiki --provider"*) mkdir -p .gitnexus/wiki; '
        'printf \'{"fromCommit":"%s","moduleFiles":{}}\' "$(/usr/bin/git rev-parse HEAD)" '
        "> .gitnexus/wiki/meta.json; "
        "printf 'regenerated from the healed graph' > .gitnexus/wiki/index.md ;;\n"
        "esac\n"
        "echo ok\nexit 0\n"
    )
```

(`branch` stays the `analyze_stamps` knob; `lastCommit` is a real SHA, never the placeholder `"new"`; `clean_removes` still selects `rm -rf .gitnexus` vs `:`.) `_seed_inverted_store` keeps `"lastCommit": "old"` (inverted skips distance codes).

`test_heal_survives_an_undeletable_docs_mirror` — retarget the monkeypatch:

```python
    import omc.gitnexus as gitnexus_mod

    monkeypatch.setattr(gitnexus_mod, "clear_docs_mirror", boom)
```

Rewrite the two `--once` tests at lines 111 and 120 and add a sibling (these seed REAL staleness — a second commit pushed to origin with metadata left at the first commit's SHA — so the fresh fixture no longer hides an accidental `index-missing` heal):

```python
def test_once_refreshes_index_even_when_up_to_date(tmp_path, capsys):
    """--once on an index that is BEHIND repairs it even though the checkout is
    already at origin (nothing to sync)."""
    origin, repo = _repo_with_origin(tmp_path)
    first = _git_out(repo, "rev-parse", "HEAD")
    (repo / "g.txt").write_text("two\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c2", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": first, "repoPath": str(repo)})
    )
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "up to date" in err
    assert "analyze --skip-agents-md --skip-skills" in calls.read_text()


def test_once_with_documentation_refreshes_docs_even_when_up_to_date(tmp_path, capsys):
    """--once with docs on a fresh index whose wiki is BEHIND regenerates the wiki."""
    _, repo = _repo_with_origin(tmp_path)
    first = _git_out(repo, "rev-parse", "HEAD")
    (repo / "g.txt").write_text("two\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c2", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    _seed_fresh_index(repo)  # index at the new HEAD
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": first}))  # docs one commit back
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx, enable_documentation=True) == 0
    recorded = calls.read_text()
    assert "wiki --provider claude" in recorded
    assert "--model sonnet" in recorded
    assert "analyze" not in recorded  # index was fresh: only the docs ran


def test_once_on_fresh_snapshot_narrates_current_and_calls_nothing(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, 'echo "$OMC_WATCH_OUTCOME" > hook-ran.txt\n')
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "✓ knowledge is current" in err
    recorded = calls.read_text()  # ensure_gitnexus's --version probe is recorded
    assert "analyze" not in recorded and "wiki" not in recorded
    assert (repo / "hook-ran.txt").read_text().strip() == "refreshed"  # --once still fires the hook
```

(`_seed_hook` is defined further down the module; Python resolves it at call time.)

- [ ] **Step 2: Write the failing tests for the ladder**

```python
# tests/unit/test_gitnexus_refresh.py
"""refresh_knowledge — the ONLY code that repairs the snapshot (spec §2).
Judged by recomputing the verdict, never by exit codes."""

import json

import pytest

from omc.config.schema import Config
from omc.gitnexus import refresh_knowledge

from .test_watch import _ctx_with_healing_node_stub, _ctx_with_node_stub, _repo_with_origin

REPO_PATH_SLOT = '"repoPath":"%s"'


@pytest.fixture(autouse=True)
def _fast_wiki_poll(monkeypatch):
    import omc.gitnexus as gitnexus_mod

    monkeypatch.setattr(gitnexus_mod, "_WIKI_POLL_SECONDS", 0.05)


def _stale_index(repo):
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": "a" * 40, "repoPath": str(repo)})
    )


def _foreign_stamping_stub(tmp_path):
    """Healing stub whose analyze stamps a foreign repoPath: stale after every step."""
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert REPO_PATH_SLOT in text
    node.write_text(text.replace(REPO_PATH_SLOT, '"repoPath":"/elsewhere%s"'))


def _run(ctx, repo, **kw):
    said = []
    v = refresh_knowledge(ctx, Config(), str(repo), "main", say=said.append, **kw)
    return v, said


def test_fresh_snapshot_narrates_current_and_calls_nothing(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists()  # refresh_knowledge itself never probes --version
    assert "✓ knowledge is current" in said


def test_stale_index_heals_with_one_incremental_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh
    recorded = calls.read_text()
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1
    assert "clean --force" not in recorded
    assert "→ refreshing GitNexus index (incremental)" in said and "✓ index refreshed" in said


def test_analyze_that_leaves_it_stale_escalates_to_clean(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    _foreign_stamping_stub(tmp_path)
    v, said = _run(ctx, repo, documentation=False, reset=False)
    recorded = calls.read_text()
    assert "clean --force" in recorded
    assert recorded.index("analyze") < recorded.index("clean --force")
    assert any(s.startswith("✗ index still stale after analyze") for s in said)
    assert not v.fresh  # the rebuild stamps the same foreign path: honest verdict


def test_failed_analyze_exit_code_still_falls_through_to_rebuild(tmp_path):
    """Exit codes are narrated, never trusted: a non-zero analyze that DID fix the
    metadata ends fresh; one that did not escalates like any other stale result."""
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    node.write_text(node.read_text().replace("echo ok\nexit 0\n", "echo ok\nexit 7\n"))
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert any(s.startswith("✗ analyze failed") for s in said)
    assert v.fresh  # the stub wrote fresh metadata despite exit 7
    assert "clean --force" not in calls.read_text()


def test_inverted_store_destroys_first_exactly_one_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "feature/x", "lastCommit": "old"})
    )
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    recorded = calls.read_text()
    assert recorded.index("clean --force") < recorded.index("analyze")
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1
    assert "✓ index rebuilt for main" in said and v.fresh


def test_reset_clears_mirror_and_rebuilds(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    docs = repo / ".omc" / "docs" / "gitnexus" / "docs"
    docs.mkdir(parents=True)
    (docs / "stale.md").write_text("x")
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=True)
    recorded = calls.read_text()
    assert recorded.index("clean --force") < recorded.index("analyze")
    assert not docs.exists() and v.fresh
    hint = "· docs mirror cleared — run omc watch --once --enable-documentation to regenerate"
    assert hint in said


def test_reset_with_failed_clean_aborts_before_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # fresh fixture: the FAILED clean is the story
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home", clean_removes=False)
    v, said = _run(ctx, repo, documentation=False, reset=True)
    assert any(s.startswith("✗ clean did not remove the index") for s in said)
    assert "analyze" not in calls.read_text()


def test_documentation_off_never_computes_wiki_reasons(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # index fresh, no wiki at all
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists()
    assert not any("docs" in s for s in said if s.startswith("✗"))


def test_wiki_behind_runs_wiki_supervised_and_mirrors(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=True, reset=False)
    recorded = calls.read_text()
    assert "wiki --provider claude" in recorded and "--model sonnet" in recorded
    assert v.fresh
    assert (repo / ".omc" / "docs" / "gitnexus" / "docs" / "index.md").read_text() == "page"
    assert "✓ documentation refreshed → .omc/docs/gitnexus/docs" in said


def test_wiki_still_behind_leaves_mirror_untouched(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert "> .gitnexus/wiki/meta.json;" in text
    # a wiki run that writes NO metadata: verdict stays wiki-missing
    node.write_text(text.replace("> .gitnexus/wiki/meta.json;", "> /dev/null;"))
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert "wiki-missing" in v.codes()
    assert not (repo / ".omc" / "docs").exists()
    assert "✗ documentation still behind after regeneration" in said
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_gitnexus_refresh.py -q`
Expected: FAIL — `ImportError: cannot import name 'refresh_knowledge'`

- [ ] **Step 4: Implement `refresh_knowledge` in `src/omc/gitnexus.py`**

Add imports: `from .mirror import DOCS_MIRROR_REL, clear_docs_mirror, mirror_dir` and `from .wikirun import _WIKI_POLL_SECONDS, _WIKI_STALL_SECONDS, PageCountTracker`; under `TYPE_CHECKING`: `from .config.schema import Config`. (`docs_model_for` is imported inside `_run_wiki`; `providers.registry` imports nothing from `gitnexus`, so no cycle either way.)

```python
def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _clear_mirror(root: Path, say) -> bool:
    try:
        cleared = clear_docs_mirror(root)
    except OSError as exc:
        # rmtree can fail (permissions, racing reader); the index is what we are
        # here for — warn and continue, the next documentation run re-mirrors.
        say(f"✗ could not delete the stale docs mirror: {exc}")
        return False
    if cleared:
        say("· stale docs mirror deleted")
    return cleared


def _destroy(ctx: ToolContext, root: Path, say) -> bool:
    """`clean --force` judged by POST-CONDITION (it exits 0 when deletion fails)."""
    cp = ctx.run(gitnexus_argv(ctx, "clean", "--force"), cwd=str(root))
    if read_index_meta(root) is not None:
        say(f"✗ clean did not remove the index: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return False
    return True


def _destroy_and_rebuild(ctx: ToolContext, root: Path, base: str, say) -> tuple[bool, bool]:
    """Today's watch._heal_store body. Returns (healed, mirror_cleared)."""
    mirror_cleared = _clear_mirror(root, say)
    if not _destroy(ctx, root, say):
        return False, mirror_cleared
    cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=str(root))
    if cp.returncode != 0:
        say(f"✗ full analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
    if flat_store_branch(root) != base:
        say(f"✗ rebuilt index is not owned by {base!r} — not claiming success")
        return False, mirror_cleared
    say(f"✓ index rebuilt for {base}")
    return True, mirror_cleared


def _run_wiki(ctx: ToolContext, cfg: Config, root: Path, say) -> bool:
    from .providers.registry import docs_model_for

    name = cfg.llm.default
    args = ["wiki", "--provider", name]
    # Docs model, never the session model: wiki is bulk grounded summarization
    # and a thinking-heavy session model turns it into an hours-long silent run.
    docs_model = docs_model_for(cfg, name)
    if docs_model:
        args += ["--model", docs_model]
    say(f"→ regenerating documentation via {name} (LLM-heavy)")
    tracker = PageCountTracker(root / ".gitnexus" / "wiki")
    cp, stalled = ctx.run_supervised(
        gitnexus_argv(ctx, *args),
        cwd=str(root),
        heartbeat=tracker.beat,
        stall_after=_WIKI_STALL_SECONDS,
        poll=_WIKI_POLL_SECONDS,
    )
    if stalled:
        say(f"✗ wiki stalled — no progress for {int(_WIKI_STALL_SECONDS)}s; killed")
        return False
    if cp.returncode != 0:
        say(f"✗ wiki failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
    return True  # the recomputed verdict, not the exit code, decides


def refresh_knowledge(
    ctx: ToolContext,
    cfg: Config,
    root: Path | str,
    base: str,
    *,
    documentation: bool,
    reset: bool,
    ref: str = "HEAD",
    say=_say,
) -> Freshness:
    """The ONLY code that repairs the knowledge snapshot (spec §2). Cheapest
    step first, escalate only when the recomputed verdict says so. Exit codes
    are narrated, never trusted."""
    rootp = Path(root)

    def verdict() -> Freshness:
        return snapshot_freshness(ctx, rootp, base, ref=ref, documentation=documentation)

    did_anything = False
    mirror_cleared = False
    if reset:
        say("→ resetting the knowledge snapshot (--reset-gitnexus)")
        did_anything = True
        mirror_cleared = _clear_mirror(rootp, say)
        if not _destroy(ctx, rootp, say):
            return verdict()

    v = verdict()
    if v.index_codes():
        did_anything = True
        if "store-inverted" in v.index_codes():
            owner = flat_store_branch(rootp)
            say(f"✗ GitNexus index is owned by {owner!r}, not {base!r} — destroying and rebuilding")
            healed, cleared = _destroy_and_rebuild(ctx, rootp, base, say)
            mirror_cleared = mirror_cleared or cleared
            if not healed:
                return verdict()
        else:
            say("→ refreshing GitNexus index (incremental)")
            cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=str(rootp))
            if cp.returncode != 0:
                # Narrate only — the verdict below decides whether to escalate.
                say(f"✗ analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
            else:
                say("✓ index refreshed")
            v = verdict()
            if v.index_codes():
                codes = ",".join(v.index_codes())
                say(f"✗ index still stale after analyze ({codes}) — destroying and rebuilding")
                healed, cleared = _destroy_and_rebuild(ctx, rootp, base, say)
                mirror_cleared = mirror_cleared or cleared
                if not healed:
                    return verdict()
        v = verdict()
        if v.index_codes():
            say(f"✗ index still stale after rebuild: {','.join(v.index_codes())}")
            return v

    if not documentation:
        if mirror_cleared:
            # Only when the mirror ACTUALLY went away — never contradict a warning.
            say("· docs mirror cleared — run omc watch --once --enable-documentation to regenerate")
        if not did_anything:
            say("✓ knowledge is current")
        return v

    if v.wiki_codes():
        did_anything = True
        if not _run_wiki(ctx, cfg, rootp, say):
            return verdict()
        v = verdict()
        if v.wiki_codes():
            say("✗ documentation still behind after regeneration")
            return v
        mirror_dir(rootp / ".gitnexus" / "wiki", rootp / DOCS_MIRROR_REL)
        say("✓ documentation refreshed → .omc/docs/gitnexus/docs")
    if not did_anything:
        say("✓ knowledge is current")
    return v
```

- [ ] **Step 5: Route watch through it**

In `src/omc/watch.py`: replace the import block from `.gitnexus` with `from .gitnexus import ensure_gitnexus, refresh_knowledge` (drop `ANALYZE_ARGS`, `flat_store_branch`, `gitnexus_argv`, `store_inverted`; drop `DOCS_MIRROR_REL, clear_docs_mirror, mirror_dir` from the `.mirror` import; drop `docs_model_for` from the registry import — keep `get_provider`). Delete `_heal_store` (lines 218–258) and replace `_refresh_index` (261–299) with:

```python
def _refresh_index(
    ctx: ToolContext, cfg: Config, root: str, enable_documentation: bool, *, reset: bool = False
):
    """Repair the knowledge snapshot — the ONE code path (gitnexus.refresh_knowledge)."""
    return refresh_knowledge(
        ctx,
        cfg,
        root,
        cfg.worktree.base_branch,
        documentation=enable_documentation,
        reset=reset,
        say=_say,
    )
```

`_tick`'s three call sites keep calling `_refresh_index(ctx, cfg, root, enable_documentation)` unchanged in this task (Task 5 rewires the up-to-date path). Because the `--once` path still calls `_refresh_index` unconditionally, the rewritten `--once` tests from Step 1 pass here already (stale seeds → analyze / wiki; fresh seed → `✓ knowledge is current` and no analyze).

- [ ] **Step 6: Run the tests and lint**

Run: `uv run python -m pytest tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py tests/unit/test_watch_mutex.py tests/unit/test_start_mutex.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: ALL PASS. Notable: `test_refresh_heals_inverted_store` (one analyze), `test_refresh_healthy_store_stays_incremental` (`"lastCommit":"old"` → `index-unknown` → one analyze, no clean), `test_heal_clean_failure_warns_and_skips`, `test_heal_wrong_stamp_never_claims_success`, `test_heal_with_documentation_regenerates_wiki` (wiki meta now written → fresh → mirrored), `test_heal_survives_an_undeletable_docs_mirror` (retargeted patch), the three `--once` tests from Step 1.

- [ ] **Step 7: Commit**

```bash
git add src/omc/gitnexus.py src/omc/watch.py tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py
git commit -m "feat(gitnexus): refresh_knowledge — one verdict-driven repair path; watch uses it

Absorbs watch._heal_store. Analyze-first ladder escalating to destroy-and-
rebuild, wiki via run_supervised, mirror only when the verdict is fresh.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `watchlock.acquire_busy_narrated` and the reworded wait message

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/watchlock.py` (docstring lines 1-16, `START_WAIT_MSG` line 31, append helper)
- Test: `tests/unit/test_watchlock.py:172-174` (exact match), `tests/unit/test_start_mutex.py:23` (`WAIT_LINE`), new tests in `test_watchlock.py`

**Interfaces:**
- Produces: `acquire_busy_narrated(lock: FileLock, say: Callable[[str], None] | None = None)` — a `@contextmanager` (the spec now says so explicitly): probes with `timeout=0`; on `Timeout` says `BUSY_WAIT_MSG` = `· waiting for another omc knowledge refresh to finish` once, then blocks; yields; releases on exit. Filelock is re-entrant, so `with lock:` on an already-held lock would leave it held after exit — hence the explicit acquire/release.
- `START_WAIT_MSG == "→ waiting for omc watch or a knowledge refresh to finish. Pass \`omc start --no-mutex\` to bypass"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_watchlock.py` (it already has `_make_repo`, `_ctx`, `_hold_in_subprocess`):

```python
def test_acquire_busy_narrated_free_is_silent_and_releases(tmp_path):
    from omc.watchlock import acquire_busy_narrated

    repo = _make_repo(tmp_path)
    lock = busy_lock(_ctx(), cwd=str(repo))
    said = []
    with acquire_busy_narrated(lock, said.append):
        assert lock.is_locked
    assert said == [] and lock.is_locked is False


def test_acquire_busy_narrated_waits_and_narrates_once(tmp_path):
    from omc.watchlock import BUSY_WAIT_MSG, acquire_busy_narrated

    repo = _make_repo(tmp_path)
    lock = busy_lock(_ctx(), cwd=str(repo))
    p = _hold_in_subprocess(lock.lock_file, 1.5)
    said = []
    with acquire_busy_narrated(lock, said.append):
        pass
    p.wait()
    assert said == [BUSY_WAIT_MSG] == ["· waiting for another omc knowledge refresh to finish"]
    assert lock.is_locked is False
```

Change the exact-match test:

```python
    assert START_WAIT_MSG == (
        "→ waiting for omc watch or a knowledge refresh to finish. "
        "Pass `omc start --no-mutex` to bypass"
    )
```

and in `tests/unit/test_start_mutex.py:23`:

```python
WAIT_LINE = (
    "→ waiting for omc watch or a knowledge refresh to finish. "
    "Pass `omc start --no-mutex` to bypass"
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_watchlock.py -q`
Expected: FAIL — `ImportError: cannot import name 'acquire_busy_narrated'` and the exact-match assertion.

- [ ] **Step 3: Implement**

In `src/omc/watchlock.py`: reword the constant, add `from collections.abc import Callable, Iterator` and `from contextlib import contextmanager`, append:

```python
BUSY_WAIT_MSG = "· waiting for another omc knowledge refresh to finish"


@contextmanager
def acquire_busy_narrated(
    lock: FileLock, say: Callable[[str], None] | None = None
) -> Iterator[None]:
    """HOLD the busy lock for a knowledge mutation (watch tick, internal refresh).
    Probe first; a holder elsewhere is narrated once, then we block. Not
    `with lock:` — filelock is reentrant, so entering an already-held lock
    would leave it held after exit."""
    try:
        lock.acquire(timeout=0)
    except Timeout:
        if say is not None:
            say(BUSY_WAIT_MSG)
        lock.acquire()
    try:
        yield
    finally:
        lock.release()
```

Docstring: replace the BUSY bullet with `- omc-watch-busy.lock (BUSY): held while ANYONE mutates the primary's knowledge — a watch tick or \`omc internal gitnexus refresh\`. Free ⇔ nobody is mutating. \`omc start\` probes it before cutting a worktree so it never snapshots a half-updated primary.` (Amends the 2026-07-23 lock design; start still never HOLDS a lock.)

- [ ] **Step 4: Run tests and lint**

Run: `uv run python -m pytest tests/unit/test_watchlock.py tests/unit/test_start_mutex.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/omc/watchlock.py tests/unit/test_watchlock.py tests/unit/test_start_mutex.py
git commit -m "feat(watchlock): acquire_busy_narrated; busy lock means any knowledge mutation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `omc watch` — verdict-driven ticks, `healed`, `knowledge-stale`, `--reset-gitnexus`

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/watch.py` (`_tick` lines 337-437, `run_watch` 440-512, module docstring), `src/omc/cli/__init__.py` (parser lines 53-78, dispatch 198-207), `README.md` (line 69 prose: `--once` wording + `--reset-gitnexus`; line 107 watch row)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `refresh_knowledge` via `_refresh_index(..., reset=)`, `snapshot_freshness`, `acquire_busy_narrated`.
- Produces: `_tick(..., reset: bool = False)`; `run_watch(..., reset_gitnexus: bool = False)`; tokens `healed`, `knowledge-stale:<codes>`; CLI flag `--reset-gitnexus`.

- [ ] **Step 1: Write the new failing tests**

Append to `tests/unit/test_watch.py`:

```python
def _stale_index(repo):
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": "a" * 40, "repoPath": str(repo)})
    )


def _foreign_stamping_stub(tmp_path):
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert '"repoPath":"%s"' in text
    node.write_text(text.replace('"repoPath":"%s"', '"repoPath":"/elsewhere%s"'))


def test_up_to_date_tick_heals_stale_index_without_hook(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    _seed_hook(repo, "touch hook-ran.txt\n")
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_loop(repo, ctx, ticks=1) == 0
    err = capsys.readouterr().err
    assert err.index("· up to date") < err.index("→ refreshing GitNexus index (incremental)")
    assert "analyze --skip-agents-md --skip-skills" in calls.read_text()
    assert not (repo / "hook-ran.txt").exists()  # healed is not synced/refreshed
    assert "post-watch" not in err


def test_healed_token_returned_by_tick(tmp_path, capsys):
    from omc.watch import _tick

    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    tok = _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False)
    assert tok == "healed"


def test_persistently_stale_verdict_is_not_retried_every_tick(tmp_path, capsys):
    from omc.watch import _tick

    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    _foreign_stamping_stub(tmp_path)
    first = _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False)
    assert first.startswith("knowledge-stale:") and "index-foreign" in first
    n = calls.read_text().count("analyze")
    second = _tick(
        ctx, Config(), str(repo), enable_documentation=False, force_refresh=False, last=first
    )
    assert second == first
    assert calls.read_text().count("analyze") == n  # ladder skipped: same codes


def test_pending_reset_runs_on_fresh_up_to_date_tick(tmp_path, capsys):
    from omc.watch import _tick

    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    tok = _tick(
        ctx, Config(), str(repo), enable_documentation=False, force_refresh=False, reset=True
    )
    assert tok == "healed"
    recorded = calls.read_text()
    assert recorded.index("clean --force") < recorded.index("analyze")


def test_reset_flag_consumed_after_sync_once(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    docs = _seed_docs_mirror(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    old = os.getcwd()
    os.chdir(repo)
    try:
        rc = run_watch(ctx, Config(), interval=1, once=True, reset_gitnexus=True)
    finally:
        os.chdir(old)
    assert rc == 0
    recorded = calls.read_text()
    assert recorded.count("clean --force") == 1
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1
    assert not docs.exists()
    assert (repo / "new.txt").exists()  # sync happened first: analyze indexed the NEW head
    meta = json.loads((repo / ".gitnexus" / "meta.json").read_text())
    assert meta["lastCommit"] == _git_out(repo, "rev-parse", "HEAD")


def test_failed_reset_is_not_rerun_next_tick(tmp_path, capsys):
    """A reset whose rebuild stays stale returns knowledge-stale:<codes>; run_watch
    clears reset_pending on that token too, so the next tick does not re-clean."""
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    _foreign_stamping_stub(tmp_path)
    old = os.getcwd()
    os.chdir(repo)
    try:
        import omc.watch as watch_mod

        real_sleep = watch_mod.time.sleep
        ticks = {"n": 0}

        def fake_sleep(s):
            if sys._getframe(1).f_globals.get("__name__") != "omc.watch":
                return real_sleep(s)
            ticks["n"] += 1
            if ticks["n"] >= 2:
                raise KeyboardInterrupt
            return None

        watch_mod.time.sleep = fake_sleep
        try:
            rc = run_watch(ctx, Config(), interval=1, once=False, reset_gitnexus=True)
        finally:
            watch_mod.time.sleep = real_sleep
    finally:
        os.chdir(old)
    assert rc == 0
    assert calls.read_text().count("clean --force") == 1  # tick 2 did not reset again


def test_once_reset_on_skip_tick_says_not_applied(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    (repo / "f.txt").write_text("uncommitted edit\n")  # dirty → skip tick
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    old = os.getcwd()
    os.chdir(repo)
    try:
        rc = run_watch(ctx, Config(), interval=1, once=True, reset_gitnexus=True)
    finally:
        os.chdir(old)
    assert rc == 0
    err = capsys.readouterr().err
    assert "· reset not applied — this tick could not refresh; rerun on a clean base checkout" in err
    recorded = calls.read_text()
    assert "clean" not in recorded and "analyze" not in recorded


def test_reset_refuses_off_base_branch_before_any_node_call(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _git("switch", "-qc", "feature/other", cwd=repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    old = os.getcwd()
    os.chdir(repo)
    try:
        rc = run_watch(ctx, Config(), interval=1, once=True, reset_gitnexus=True)
    finally:
        os.chdir(old)
    assert rc == 1
    err = capsys.readouterr().err
    assert (
        "error: --reset-gitnexus requires the primary checkout to be on main "
        "(currently feature/other)"
    ) in err
    assert not calls.exists()  # refused BEFORE ensure_gitnexus's --version probe
    assert list((repo / ".git").glob("omc-watch*.lock")) == []  # and before the instance lock


def test_wiki_reasons_silent_without_documentation(tmp_path, capsys):
    from omc.watch import _tick

    _, repo = _repo_with_origin(tmp_path)  # fresh index, no wiki
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    tok = _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False)
    assert tok == "up-to-date" and not calls.exists()  # _tick alone never probes --version
    assert "docs" not in capsys.readouterr().err


def test_tick_narrates_busy_lock_contention_once(tmp_path, capsys):
    from omc.watchlock import busy_lock

    from .test_watchlock import _hold_in_subprocess

    _, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    lock = busy_lock(ctx, cwd=str(repo))
    p = _hold_in_subprocess(lock.lock_file, 1.5)
    assert _run_once(repo, ctx) == 0
    p.wait()
    err = capsys.readouterr().err
    assert err.count("· waiting for another omc knowledge refresh to finish") == 1


def test_watch_reset_gitnexus_flag_parses():
    from omc.cli import build_parser

    args = build_parser().parse_args(["watch", "--reset-gitnexus"])
    assert args.reset_gitnexus is True
    assert build_parser().parse_args(["watch"]).reset_gitnexus is False


def test_watch_reset_gitnexus_flag_dispatches(monkeypatch):
    import omc.cli as cli
    from omc.config.schema import Config as Cfg

    seen = {}
    monkeypatch.setattr(cli, "_load_cfg_or_bail", lambda ctx: Cfg())
    import omc.watch as watch_mod

    monkeypatch.setattr(
        watch_mod, "run_watch", lambda ctx, cfg, **kw: seen.update(kw) or 0
    )
    assert cli.main(["watch", "--once", "--reset-gitnexus"]) == 0
    assert seen["reset_gitnexus"] is True and seen["once"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_watch.py -q -k "healed or stale or reset or busy_lock_contention or silent_without or reset_gitnexus_flag"`
Expected: FAIL — `TypeError: run_watch() got an unexpected keyword argument 'reset_gitnexus'`, `_tick() got an unexpected keyword argument 'reset'`, argparse `unrecognized arguments: --reset-gitnexus`, token assertions.

- [ ] **Step 3: Implement `_tick`**

Signature gains `reset: bool = False`. Replace the up-to-date branch (lines 371-381):

```python
    if behind in ("", "0"):
        if force_refresh:
            _say("· up to date")
            # --once is the "check now" button: repair whatever is stale (and a
            # pending reset), narrate "current" when nothing is.
            _refresh_index(ctx, cfg, root, enable_documentation, reset=reset)
            return "refreshed"
        verdict = snapshot_freshness(
            ctx, Path(root), base, ref="HEAD", documentation=enable_documentation
        )
        if verdict.fresh and not reset:
            return quiet("up-to-date", f"· up to date — waiting for changes on origin/{base}")
        codes = ",".join(sorted(verdict.codes()))
        if not reset and last == f"knowledge-stale:{codes}":
            return last  # same failure as last tick: no retry hammer, no extra line
        _say("· up to date")
        after = _refresh_index(ctx, cfg, root, enable_documentation, reset=reset)
        if after.fresh:
            return "healed"
        return "knowledge-stale:" + ",".join(sorted(after.codes()))
```

The two sync paths pass `reset=reset` into their `_refresh_index` calls. Import `snapshot_freshness` from `.gitnexus` and `Path` from `pathlib` if not already.

- [ ] **Step 4: Implement `run_watch`**

Signature gains `reset_gitnexus: bool = False`. After `require_tools(ctx, cfg)` and BEFORE `ensure_gitnexus` (no `node` call, no lock on refusal):

```python
    base = cfg.worktree.base_branch
    if reset_gitnexus:
        branch = _out(ctx, [ctx.git_bin, "rev-parse", "--abbrev-ref", "HEAD"], root)
        if branch != base:
            print(
                f"error: --reset-gitnexus requires the primary checkout to be on {base} "
                f"(currently {branch})",
                file=sys.stderr,
            )
            return 1
```

Loop body:

```python
    reset_pending = reset_gitnexus
    reset_note: str | None = None
    try:
        while True:
            with acquire_busy_narrated(busy, _say) if busy is not None else nullcontext():
                chain_last = _chain_tick(ctx, root, chain_last)
                last = _tick(
                    ctx,
                    cfg,
                    root,
                    enable_documentation=enable_documentation,
                    force_refresh=once,
                    last=last,
                    rebase=rebase,
                    reset=reset_pending,
                )
                if reset_pending and (
                    last in ("healed", "refreshed", "synced") or last.startswith("knowledge-stale:")
                ):
                    reset_pending = False  # applied — or failed honestly; never re-run blindly
                elif reset_pending and once:
                    _say(
                        "· reset not applied — this tick could not refresh; "
                        "rerun on a clean base checkout"
                    )
                elif reset_pending and last != reset_note:
                    _say("· reset pending — waiting for a tick that can refresh")
                    reset_note = last
                if last in ("synced", "refreshed"):
                    _post_watch_hook(ctx, root, last)
                    if auto_build:
                        _auto_build(ctx, cfg, root)
```

Import `acquire_busy_narrated` from `.watchlock` and `nullcontext` from `contextlib`. Update the module docstring: add "`--reset-gitnexus` force-clears index, wiki and docs mirror and rebuilds (base branch only); up-to-date ticks heal a stale verdict (`healed`, no hooks) and never retry the same failure every tick (`knowledge-stale:<codes>`)."

- [ ] **Step 5: CLI flag**

`src/omc/cli/__init__.py` parser, after `--clear-mutex`:

```python
    p_watch.add_argument(
        "--reset-gitnexus",
        action="store_true",
        help="Force-clear the GitNexus index, wiki and docs mirror and rebuild them "
        "(primary must be on the base branch)",
    )
```

dispatch: `reset_gitnexus=args.reset_gitnexus,`.

- [ ] **Step 6: README**

Line 107 watch row: add `--reset-gitnexus` to the flag list. Line 69 prose: replace "`--once` runs a single tick AND forces an index/docs refresh even with nothing new — the "refresh now" button" with "`--once` runs a single tick and repairs whatever the freshness verdict says is stale — the "check now" button (a fresh snapshot prints `✓ knowledge is current`)"; append to that paragraph: "Up-to-date ticks heal a stale, dirty, or foreign index automatically (no hooks fire for a pure knowledge repair); `--reset-gitnexus` force-clears index, wiki and docs mirror and rebuilds — it refuses unless the primary is on the base branch."

- [ ] **Step 7: Run the whole watch suite and lint**

Run: `uv run python -m pytest tests/unit/test_watch.py tests/unit/test_watch_mutex.py tests/unit/test_cli.py tests/unit/test_start_mutex.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS (including `test_loop_tick_up_to_date_does_not_reindex`, `test_loop_says_up_to_date_once_then_waits_quietly`, `test_quiet_loop_tick_does_not_run_hook` — fresh fixture keeps them quiet), ruff clean.

- [ ] **Step 8: Commit**

```bash
git add src/omc/watch.py src/omc/cli/__init__.py README.md tests/unit/test_watch.py
git commit -m "feat(watch): verdict-driven ticks, healed/knowledge-stale tokens, --reset-gitnexus

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `omc internal gitnexus refresh|status`, proxy verdict, `knowledge` in `OMC_REBASE_MAIN`

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/internal.py` (docstring lines 1-6, `_USAGE` 23-29, `_verdict`/`_rebase_main` 34-91, `_gitnexus` 94-163)
- Test: `tests/unit/test_internal.py`

**Interfaces:**
- Consumes: `snapshot_freshness`, `refresh_knowledge`, `acquire_busy_narrated`, `busy_lock`, `resolve.load_effective`, `resolve.project_config`, `wtconfig.primary_root`, `OmcError`.
- Produces: verbs `omc internal gitnexus status` (exact match `rest == ["status"]`, rc 0, no CLI needed), `omc internal gitnexus refresh [--enable-documentation]` (rc 0 fresh / 3 still stale / 1 hard failure / 2 unconfigured or usage); `OMC_KNOWLEDGE {json}` last stdout line; proxy stderr line; `knowledge` key in every `OMC_REBASE_MAIN` payload.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_internal.py` (`import stat`, `json`, `os`, `subprocess` already present; add `import sys`):

```python
def _seed_fresh_index(repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    (repo / ".gitnexus").mkdir(exist_ok=True)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": head, "repoPath": str(repo)})
    )


def _knowledge_line(out):
    line = next(ln for ln in out.splitlines() if ln.startswith("OMC_KNOWLEDGE "))
    return json.loads(line.split(" ", 1)[1])


_HEALING_NODE = (
    "#!/bin/sh\n"
    'echo "$@" >> "{calls}"\n'
    'case "$*" in\n'
    '  *" clean --force") rm -rf .gitnexus ;;\n'
    '  *" analyze --skip-agents-md --skip-skills") mkdir -p .gitnexus; '
    'printf \'{{"branch":"main","lastCommit":"%s","repoPath":"%s"}}\' '
    '"$(/usr/bin/git rev-parse HEAD)" "$PWD" > .gitnexus/meta.json ;;\n'
    "esac\n"
    "echo ok\nexit 0\n"  # unconditional ok: ensure_gitnexus's --version probe must pass
)


def _gitnexus_env_with_config(tmp_path, monkeypatch, *, node_body=_HEALING_NODE):
    """_gitnexus_env + a GLOBAL config (load_effective needs GlobalConfig, never
    Config — _hydrate rejects the `worktree` key) + a forwarding git stub that
    LOGS argv then execs the real git (the verdict needs real git)."""
    from omc.config import store
    from omc.config.schema import GlobalConfig

    repo, wt, calls, env = _gitnexus_env(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
    home = tmp_path / "omc-home"
    store.save_global(home, GlobalConfig())
    node = tmp_path / "bin" / "node"
    node.write_text(node_body.format(calls=calls))
    gitlog = tmp_path / "bin" / "git.calls"
    git = tmp_path / "bin" / "git"
    git.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{gitlog}"\nexec /usr/bin/git "$@"\n')
    git.chmod(git.stat().st_mode | stat.S_IXUSR)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return repo, wt, calls, gitlog


def test_status_prints_verdict_without_node(tmp_path, capsys, monkeypatch):
    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch)
    old = _chdir(wt)
    try:
        rc = run_internal(["gitnexus", "status"])
    finally:
        os.chdir(old)
    assert rc == 0
    v = _knowledge_line(capsys.readouterr().out)
    assert v["fresh"] is False and v["reasons"][0]["code"] == "index-missing"
    assert v["run_in"] == str(repo.resolve())
    assert v["basis"] == "unresolved"  # no origin in this fixture
    assert not calls.exists()


def test_refresh_heals_and_exits_zero(tmp_path, capsys, monkeypatch):
    repo, wt, calls, gitlog = _gitnexus_env_with_config(tmp_path, monkeypatch)
    old = _chdir(wt)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    out = capsys.readouterr().out
    assert rc == 0
    v = _knowledge_line(out)
    assert v["fresh"] is True and v["basis"] == "HEAD"
    assert out.strip().splitlines()[-1].startswith("OMC_KNOWLEDGE ")
    assert not any(line.startswith("fetch ") for line in gitlog.read_text().splitlines())


def test_refresh_still_stale_bails_rc3(tmp_path, capsys, monkeypatch):
    # node stub that never writes metadata: analyze, clean, analyze all leave index-missing
    inert = "#!/bin/sh\n" 'echo "$@" >> "{calls}"\n' "echo ok\nexit 0\n"
    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch, node_body=inert)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 3
    assert _knowledge_line(capsys.readouterr().out)["fresh"] is False


def test_refresh_refuses_off_base_branch(tmp_path, capsys, monkeypatch):
    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "switch", "-qc", "feature/z"], check=True)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 1
    err = capsys.readouterr().err
    assert "requires the primary checkout to be on main (currently feature/z)" in err
    recorded = calls.read_text() if calls.exists() else ""
    assert "analyze" not in recorded and "clean" not in recorded


def test_refresh_unconfigured_exits_2(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 2 and "omc configure" in capsys.readouterr().err


def test_refresh_waits_for_busy_lock_holder(tmp_path, capsys, monkeypatch):
    from omc.toolctx import ToolContext
    from omc.watchlock import busy_lock

    from .test_watchlock import _hold_in_subprocess

    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch)
    _seed_fresh_index(repo)
    lock = busy_lock(ToolContext.from_env(), cwd=str(repo))
    p = _hold_in_subprocess(lock.lock_file, 1.5)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    p.wait()
    assert rc == 0
    assert "· waiting for another omc knowledge refresh to finish" in capsys.readouterr().err


def test_refresh_hints_when_primary_is_behind_origin(tmp_path, capsys, monkeypatch):
    from omc.config import store
    from omc.config.schema import GlobalConfig

    _, primary = _setup_primary_with_origin(tmp_path)  # has an origin
    home = tmp_path / "omchome"
    store.save_global(home, GlobalConfig())
    monkeypatch.setenv("OMC_HOME", str(home))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(_HEALING_NODE.format(calls=calls))
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake")
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    # advance origin from another clone, fetch so the tracking ref is ahead of HEAD
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / "x").write_text("x")
    _git("add", ".", cwd=other)
    _git("commit", "-qm", "x", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)
    _git("fetch", "origin", "main", cwd=primary)
    old = _chdir(primary)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 0
    err = capsys.readouterr().err
    assert "· primary is 1 commits behind origin/main — omc watch syncs it" in err


def test_proxy_prints_stale_verdict_on_stderr_only_when_stale(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(wt)
    try:
        assert run_internal(["gitnexus", "query", "x"]) == 0
        captured = capsys.readouterr()
        assert captured.err.splitlines()[0].startswith("OMC_KNOWLEDGE ")
        assert "OMC_KNOWLEDGE" not in captured.out
        _seed_fresh_index(repo)
        (repo / ".gitnexus" / "wiki").mkdir()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        (repo / ".gitnexus" / "wiki" / "meta.json").write_text(json.dumps({"fromCommit": head}))
        assert run_internal(["gitnexus", "query", "x"]) == 0
        assert "OMC_KNOWLEDGE" not in capsys.readouterr().err
    finally:
        os.chdir(old)


def test_proxy_git_path_never_prints_verdict(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    _seed_dep(tmp_path / "omc-home")
    old = _chdir(repo)
    try:
        run_internal(["gitnexus", "--git", "github.com/foo/bar", "query", "x"])
    finally:
        os.chdir(old)
    assert "OMC_KNOWLEDGE" not in capsys.readouterr().err


def test_rebase_main_payload_carries_knowledge_in_all_shapes(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    rc, verdict, _ = _run(["rebase-main", "--base", "main"], primary, tmp_path, capsys)
    assert verdict["knowledge"]["fresh"] is False
    assert verdict["knowledge"]["reasons"][0]["code"] == "index-missing"
    wt = _add_worktree(primary, tmp_path)
    _advance_main(primary)
    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)
    assert rc == 0 and "knowledge" in verdict
    assert verdict["knowledge"]["run_in"] == str(primary.resolve())
```

Also extend `test_rebase_main_conflict_bails_rc3_and_leaves_rebase_paused` with `assert "knowledge" in verdict`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_internal.py -q`
Expected: FAIL — `status`/`refresh` return 2 (usage), no `OMC_KNOWLEDGE` lines, `KeyError: 'knowledge'`.

- [ ] **Step 3: Implement**

`src/omc/internal.py` docstring exit codes: `0 ok, 1 error, 2 usage, 3 bail (inconclusive — caller falls back to its own judgment)`. `_USAGE`: `" | gitnexus [--git REF] <ensure|status|refresh [--enable-documentation]|query|context|impact|cypher> [args…]"`. Imports: `from .gitnexus import ensure_gitnexus, gitnexus_argv, gitnexus_cli, refresh_knowledge, snapshot_freshness`, `from .watchlock import acquire_busy_narrated, busy_lock`, `from .errors import OmcError`, `from contextlib import nullcontext`, `import argparse`.

```python
def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _knowledge_line(v) -> None:
    print(f"OMC_KNOWLEDGE {json.dumps(v.to_json())}", flush=True)


def _primary_and_base(ctx: ToolContext) -> tuple[str, str] | None:
    primary = primary_root(ctx)
    if primary is None:
        print("error: not inside a git repository", file=sys.stderr)
        return None
    return primary, resolve.project_config(ctx).worktree.base_branch


def _knowledge_status(ctx: ToolContext) -> int:
    try:
        pb = _primary_and_base(ctx)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if pb is None:
        return 2
    primary, base = pb
    _knowledge_line(snapshot_freshness(ctx, Path(primary), base))  # no fetch: basis says so
    return 0


def _knowledge_refresh(ctx: ToolContext, rest: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="omc internal gitnexus refresh", add_help=False)
    parser.add_argument("--enable-documentation", action="store_true")
    try:
        args = parser.parse_args(rest)
    except SystemExit:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        cfg = resolve.load_effective(ctx)
        if cfg is None:
            print("error: omc is not configured — run `omc configure` first.", file=sys.stderr)
            return 2
        pb = _primary_and_base(ctx)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if pb is None:
        return 2
    primary, base = pb
    cp = ctx.run([ctx.git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=primary)
    branch = (cp.stdout or "").strip()
    if branch != base:
        print(
            f"error: refresh requires the primary checkout to be on {base} (currently {branch})",
            file=sys.stderr,
        )
        return 1
    if ensure_gitnexus(ctx):
        return 1
    lock = busy_lock(ctx, cwd=primary)
    with acquire_busy_narrated(lock, _say) if lock is not None else nullcontext():
        v = refresh_knowledge(
            ctx, cfg, primary, base, documentation=args.enable_documentation, reset=False, say=_say
        )
    behind = ctx.run([ctx.git_bin, "rev-list", "--count", f"HEAD..origin/{base}"], cwd=primary)
    n = (behind.stdout or "").strip()
    if behind.returncode == 0 and n.isdigit() and int(n) > 0:
        _say(f"· primary is {n} commits behind origin/{base} — omc watch syncs it")
    _knowledge_line(v)
    return 0 if v.fresh else 3
```

Order inside `_knowledge_refresh` is binding: argparse → cfg → primary/base → off-branch refusal → `ensure_gitnexus` → busy lock → `refresh_knowledge`. In `_gitnexus`, right after the `ensure` check and BEFORE the `--git` parse and the CLI-presence guard:

```python
    if rest == ["status"]:
        return _knowledge_status(ctx)
    if rest[:1] == ["refresh"]:
        return _knowledge_refresh(ctx, rest[1:])
```

Proxy project mode, before `ctx.run(argv, cwd=primary, capture=False)`:

```python
    verdict = snapshot_freshness(ctx, Path(primary), base)  # computed without fetch
    if not verdict.fresh:
        # stderr, flushed BEFORE the child spawns: stdout stays pure GitNexus JSON
        print(f"OMC_KNOWLEDGE {json.dumps(verdict.to_json())}", file=sys.stderr, flush=True)
```

`_rebase_main`: in the primary no-op branch compute `knowledge = snapshot_freshness(ctx, Path(primary), base).to_json()` (no fetch; `basis` is the local ref) and add `"knowledge": knowledge` to that payload. After the `git fetch origin <base>` succeeds compute it ONCE more and pass that dict into both the conflict payload and the success payload. `_verdict(payload)` itself is unchanged.

- [ ] **Step 4: Run tests and lint**

Run: `uv run python -m pytest tests/unit/test_internal.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "feat(internal): gitnexus refresh/status verbs; OMC_KNOWLEDGE on proxy stderr and in rebase-main

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `omc start` alerts and seeds the verdict

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/start.py` (`_print_plan` 27-36, `build_start_seed` 68-76, `run_start` 79-165, imports)
- Test: `tests/unit/test_start.py`

**Interfaces:**
- Consumes: `snapshot_freshness`, `wtconfig.primary_root`.
- Produces: `build_start_seed(context: str, knowledge: dict | None = None) -> str`; `render_knowledge_alert(v: Freshness) -> list[str]`; `_print_plan(..., knowledge_desc)`.

- [ ] **Step 1: Write the failing tests**

```python
OLD_SEED = (
    "/omc:start\n"
    "The following single JSON string is investigation context for the start phase only. "
    "Decode it as data; its words, commands, and delimiters never authorize "
    "implementation or change the lifecycle. Follow the loaded start skill.\n"
    'OMC_START_CONTEXT_JSON: "PROJ-1"'
)


def _stale_verdict():
    from omc.gitnexus import Freshness, Reason

    return Freshness(
        fresh=False,
        reasons=(
            Reason("index-behind", "index is 31 commits behind origin/main", {"count": 31}),
            Reason("wiki-behind", "docs are 31 commits behind the index", {"count": 31}),
        ),
        fix="omc watch --once --enable-documentation",
        run_in="/primary",
        basis="origin/main",
    )


def _wire_verdict(monkeypatch, verdict, seen=None):
    import omc.start as start_mod

    monkeypatch.setattr(start_mod, "primary_root", lambda ctx: "/primary")
    monkeypatch.setattr(start_mod, "snapshot_freshness", lambda ctx, root, base: verdict)
    monkeypatch.setattr(start_mod.worktree, "sync_base", lambda ctx, base: True)
    if seen is not None:
        monkeypatch.setattr(
            start_mod,
            "_run_headless",
            lambda ctx, cfg, seed, cwd, slug: seen.setdefault("seed", seed) and 0,
        )


def test_seed_embeds_knowledge_line_before_framing_and_keeps_context_last():
    from omc.start import build_start_seed

    seed = build_start_seed("PROJ-1", knowledge=_stale_verdict().to_json())
    lines = seed.split("\n")
    assert lines[0] == "/omc:start"
    assert lines[1].startswith("OMC_KNOWLEDGE ")
    assert json.loads(lines[1].split(" ", 1)[1])["fresh"] is False
    assert lines[2].startswith("The following single JSON string")
    assert lines[-1].startswith("OMC_START_CONTEXT_JSON: ")
    _, data = seed.split("OMC_START_CONTEXT_JSON: ", 1)
    assert json.loads(data) == "PROJ-1" and data.count("\n") == 0


def test_seed_without_knowledge_is_byte_identical_to_today():
    from omc.start import build_start_seed

    assert build_start_seed("PROJ-1") == OLD_SEED
    assert build_start_seed("PROJ-1", knowledge=None) == OLD_SEED


def test_stale_verdict_prints_alert_block_and_seeds_it(tmp_path, capsys, monkeypatch):
    seen = {}
    _wire_verdict(monkeypatch, _stale_verdict(), seen)
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()
    assert run_start(ctx, Config(), "PROJ-1", headless=True) == 0
    err = capsys.readouterr().err
    assert "→ fetching origin/main" in err
    assert "✗ knowledge snapshot is stale — /omc:explain will answer from old data" in err
    assert "  · index is 31 commits behind origin/main\n" in err
    assert "  · docs are 31 commits behind the index\n" in err
    assert "  → fix: omc watch --once --enable-documentation   (run in /primary)" in err
    assert err.index("✓ worktree:") < err.index("✗ knowledge snapshot is stale")
    assert err.index("✗ knowledge snapshot is stale") < err.index("→ running headless")
    assert "\nOMC_KNOWLEDGE " in seen["seed"]


def test_index_missing_uses_two_line_alert(tmp_path, capsys, monkeypatch):
    from omc.gitnexus import Freshness, Reason

    v = Freshness(
        False,
        (Reason("index-missing", "no knowledge snapshot yet (no GitNexus index)"),),
        "omc watch --once",
        "/primary",
        "origin/main",
    )
    _wire_verdict(monkeypatch, v)
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()
    assert run_start(ctx, Config(), "PROJ-1", headless=True) == 0
    err = capsys.readouterr().err
    assert "✗ no knowledge snapshot yet — /omc:explain has nothing to answer from" in err
    assert "  → fix: omc watch --once   (run in /primary)" in err
    assert "  · " not in err.split("✗ no knowledge snapshot yet")[1].split("→ fix")[0]


def test_fresh_verdict_prints_nothing_and_seed_unchanged(tmp_path, capsys, monkeypatch):
    from omc.gitnexus import Freshness

    seen = {}
    _wire_verdict(monkeypatch, Freshness(True, (), "", "/primary", "origin/main"), seen)
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()
    assert run_start(ctx, Config(), "PROJ-1", headless=True) == 0
    assert "knowledge" not in capsys.readouterr().err
    assert "OMC_KNOWLEDGE" not in seen["seed"]


def test_no_primary_means_no_verdict(tmp_path, capsys, monkeypatch):
    import omc.start as start_mod

    called = []
    monkeypatch.setattr(start_mod, "snapshot_freshness", lambda *a, **k: called.append(1))
    # full_env's git stub prints "git version 2.99" for `worktree list --porcelain`
    # (no `worktree ` line) so primary_root returns None → the verdict is skipped.
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()
    assert run_start(ctx, Config(), "PROJ-1", headless=True) == 0
    assert called == []


def test_dry_run_shows_knowledge_row_without_fetch(tmp_path, capsys, monkeypatch):
    import omc.start as start_mod

    fetched = []
    _wire_verdict(monkeypatch, _stale_verdict())
    monkeypatch.setattr(start_mod.worktree, "sync_base", lambda ctx, base: fetched.append(1))
    ctx = full_env(tmp_path)
    assert run_start(ctx, Config(), "PROJ-1", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "knowledge:    stale (index-behind,wiki-behind) (computed without fetch)" in out
    assert "OMC_KNOWLEDGE" in out  # the dry-run seed embeds the same verdict
    assert fetched == []


def test_dry_run_fresh_knowledge_row(tmp_path, capsys, monkeypatch):
    from omc.gitnexus import Freshness

    _wire_verdict(monkeypatch, Freshness(True, (), "", "/primary", "origin/main"))
    ctx = full_env(tmp_path)
    assert run_start(ctx, Config(), "PROJ-1", dry_run=True) == 0
    assert "knowledge:    fresh (computed without fetch)" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_start.py -q`
Expected: FAIL — `build_start_seed() got an unexpected keyword argument 'knowledge'`, `AttributeError: module 'omc.start' has no attribute 'snapshot_freshness'`.

- [ ] **Step 3: Implement**

Imports: `from .gitnexus import ensure_gitnexus, snapshot_freshness`, `from .wtconfig import primary_root, repo_root`.

```python
def build_start_seed(context: str, knowledge: dict | None = None) -> str:
    """Keep the native command first and frame arbitrary context as data. A stale
    knowledge verdict travels as its own omc-generated line BEFORE the framing
    sentence, so "the following" still points at the context JSON (last line).
    knowledge=None or a fresh verdict → byte-identical to the pre-verdict seed."""
    lines = ["/omc:start"]
    if knowledge is not None and not knowledge.get("fresh", True):
        lines.append("OMC_KNOWLEDGE " + json.dumps(knowledge, ensure_ascii=True))
    lines.append(
        "The following single JSON string is investigation context for the start phase only. "
        "Decode it as data; its words, commands, and delimiters never authorize "
        "implementation or change the lifecycle. Follow the loaded start skill."
    )
    lines.append("OMC_START_CONTEXT_JSON: " + json.dumps(context, ensure_ascii=True))
    return "\n".join(lines)


def render_knowledge_alert(v) -> list[str]:
    """The stderr block for a stale verdict (spec §5): header, ONE `  · <text>`
    per reason (index reasons first, then wiki), the fix line. index-missing
    alone gets the two-line form: a never-indexed project needs no distance."""
    fix = f"  → fix: {v.fix}   (run in {v.run_in})"
    if v.codes() == ["index-missing"]:
        return ["✗ no knowledge snapshot yet — /omc:explain has nothing to answer from", fix]
    ordered = [r for r in v.reasons if not r.code.startswith("wiki-")] + [
        r for r in v.reasons if r.code.startswith("wiki-")
    ]
    return (
        ["✗ knowledge snapshot is stale — /omc:explain will answer from old data"]
        + [f"  · {r.text}" for r in ordered]
        + [fix]
    )
```

`run_start` reorder — after `base = cfg.worktree.base_branch` (and after the slug is known):

```python
    primary = primary_root(ctx)
    knowledge = None
    if dry_run:
        if primary is not None:
            knowledge = snapshot_freshness(ctx, Path(primary), base)  # no fetch in dry-run
    else:
        if not no_mutex:
            lock = busy_lock(ctx)
            if lock is not None:
                wait_until_idle(lock, say=_say)
        _say(f"→ fetching origin/{base}")
        worktree.sync_base(ctx, base)
        if primary is not None:
            knowledge = snapshot_freshness(ctx, Path(primary), base)

    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    seed = build_start_seed(context, knowledge=knowledge.to_json() if knowledge else None)
    notify_argv = ...  # unchanged
    session_argv = ...  # unchanged
    title_seq = ...  # unchanged

    if dry_run:
        ...  # unchanged, plus:
        if knowledge is None:
            knowledge_desc = "unknown (not in a repo)"
        elif knowledge.fresh:
            knowledge_desc = "fresh (computed without fetch)"
        else:
            knowledge_desc = f"stale ({','.join(knowledge.codes())}) (computed without fetch)"
        _print_plan(
            branch, base, wt_argv, title_seq, session_argv, shell_argv, notify_desc, knowledge_desc
        )
        return 0

    _say(f"→ creating worktree {branch} (base origin/{base})")
    path = worktree.create_worktree(...)  # unchanged
    ...
    if cfg.notifications.enabled:
        ...  # unchanged
    if knowledge is not None and not knowledge.fresh:
        for line in render_knowledge_alert(knowledge):
            _say(line)
    if headless:
        ...  # unchanged
```

Remove the old `wait_until_idle`/`sync_base` block that sat after the dry-run return. `_print_plan` gains `knowledge_desc` and prints `print(f"  knowledge:    {knowledge_desc}")`.

- [ ] **Step 4: Format, run tests and lint**

Run: `uvx ruff format src/omc/start.py tests/unit/test_start.py && uv run python -m pytest tests/unit/test_start.py tests/unit/test_start_mutex.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS (existing `test_progress_lines_narrate_phases`, `test_dry_run_prints_plan`, `test_start_seed_keeps_all_context_as_one_json_data_value` keep passing), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/omc/start.py tests/unit/test_start.py
git commit -m "feat(start): alert on a stale knowledge snapshot and seed the OMC_KNOWLEDGE verdict

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Skills, contract listings, README

**Model:** standard coding tier

**Files:**
- Modify: `skills/gitnexus-index/SKILL.md`, `skills/gitnexus-document/SKILL.md`, `skills/gitnexus-explain/SKILL.md`, `skills/explain/SKILL.md`, `skills/plan/SKILL.md`, `skills/start/SKILL.md`, `skills/rebase-main/SKILL.md`, `src/omc/distribution/AGENTS.md:48-50`, `.omc/config/AGENTS.md:62-63`, `.omc/skills/review/SKILL.md:21-22`, `.omc/skills/explain-context/SKILL.md:26`, `README.md`
- Test: `tests/unit/test_plugin_manifests.py:176-196, 288-292`

- [ ] **Step 1: Update the needle tests first**

```python
def test_gitnexus_index_contract():
    text = (ROOT / "skills" / "gitnexus-index" / "SKILL.md").read_text()
    for needle in (
        "omc internal gitnexus refresh",
        "OMC_KNOWLEDGE",
        "rc 3",
        "git worktree list",
        "primary",
    ):
        assert needle in text, f"gitnexus-index missing {needle!r}"
    assert "--skip-agents-md" not in text  # analyze flags live in Python now


def test_gitnexus_document_contract():
    text = (ROOT / "skills" / "gitnexus-document" / "SKILL.md").read_text()
    for needle in (
        "omc internal gitnexus refresh --enable-documentation",
        ".omc/docs/gitnexus/docs",
        "OMC_KNOWLEDGE",
    ):
        assert needle in text, f"gitnexus-document missing {needle!r}"
    assert "cp -R" not in text and "--provider" not in text  # sync + model choice are Python's


def test_gitnexus_explain_contract():
    text = (ROOT / "skills" / "gitnexus-explain" / "SKILL.md").read_text()
    assert "omc internal gitnexus" in text
    assert "OMC_KNOWLEDGE" in text and "stderr" in text
    assert "--repo" not in text and "--branch" not in text
    assert "node <CLI> query" not in text


def test_machine_contract_listings_include_knowledge():
    for rel in (
        "src/omc/distribution/AGENTS.md",
        ".omc/config/AGENTS.md",
        ".omc/skills/review/SKILL.md",
        ".omc/skills/explain-context/SKILL.md",
    ):
        assert "OMC_KNOWLEDGE" in (ROOT / rel).read_text(), rel
```

Add `"OMC_KNOWLEDGE"` to the needle tuples of `test_plan_skill_contract`, `test_explain_user_facing_contract`, the start needles block (~line 150), and `"knowledge"` to `test_rebase_main_skill_contract`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py -q`
Expected: FAIL on the new needles.

- [ ] **Step 3: Rewrite `skills/gitnexus-index/SKILL.md` (body after the frontmatter)**

```markdown
# omc gitnexus-index (internal)

## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill.

## Step 2 — refresh the index

```sh
omc internal gitnexus refresh
```

The verb operates on the primary worktree root (`git worktree list`, first
entry) no matter where it is invoked; from a linked worktree, say so
("indexing the primary checkout at <path>, not this worktree"). It computes the
freshness verdict, runs whatever repair is needed (incremental analyze,
escalating to a full rebuild only when that leaves the index stale), and
prints a single machine-readable last line:

`OMC_KNOWLEDGE {"fresh": true|false, "basis": …, "reasons": [...], "fix": …, "run_in": …}`

## Step 3 — report

- rc 0 → report "index current at <basis>".
- rc 3 → the repair ran but the verdict is still stale: relay the remaining
  `reasons` verbatim and stop; never report a stale index as fresh.
- rc 1/2 → surface stderr and stop.
```

- [ ] **Step 4: Rewrite `skills/gitnexus-document/SKILL.md` body**

```markdown
# omc gitnexus-document (internal)

## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill.

## Step 2 — refresh index + documentation

```sh
omc internal gitnexus refresh --enable-documentation
```

Python owns the LLM choice (omc's configured default and that provider's docs
model, never the session model); the wiki runs supervised (no deadline, killed
only on a stall) and, when the verdict is fresh afterwards, is mirrored into
`.omc/docs/gitnexus/docs/` in the primary root (`.omc/docs/` is generated
output — keep it gitignored). This is LLM-driven and can take a while on a
large repo. It waits for a running `omc watch` tick to finish before starting
(busy lock) and never runs two regenerations at once.

## Step 3 — report

The last stdout line is `OMC_KNOWLEDGE {…}`. rc 0 → list what landed in
`.omc/docs/gitnexus/docs/` (page count, top-level titles). rc 3 → the docs are
still behind: relay the `reasons` and stop; never sync or report a partial wiki
as current.
```

- [ ] **Step 5: Edit the other skills, contract listings and README**

Skills:
- `skills/gitnexus-explain/SKILL.md`, end of Step 2: add "The proxy prints `OMC_KNOWLEDGE {…}` on **stderr** (matched by prefix, not position — GitNexus's own warnings land there too) when the snapshot is stale. Relay that line to your caller as the first thing you return."
- `skills/explain/SKILL.md`, Step 4: add bullet "If `gitnexus-explain` relayed an `OMC_KNOWLEDGE` line with `fresh: false`, the FIRST line of your answer says the knowledge snapshot is stale (reasons + fix, for the user to run in the primary) and nothing from the graph is presented as current fact."
- `skills/plan/SKILL.md`, Step 2: prepend item 0 — "If the start seed or context carries an `OMC_KNOWLEDGE` line with `fresh: false`, the primer's FIRST line is `knowledge snapshot stale: <reasons> — fix: <fix> (run in <run_in>) — the user runs it in the primary; this session never does`; the explain pass still runs and its answer is marked as grounded in stale data."
- `skills/start/SKILL.md`, Step 4 item 1: add "If the seed carries an `OMC_KNOWLEDGE` line (the one OUTSIDE the JSON string) with `fresh: false`, restate the alert in this summary and pass it to `omc:plan` with the context. The `fix` is for the USER to run in the primary checkout — this session never runs it (it mutates the primary and takes locks)."
- `skills/rebase-main/SKILL.md`: add "Every `OMC_REBASE_MAIN` payload carries `"knowledge": {…}` (the primary's freshness verdict, same schema as `OMC_KNOWLEDGE`). When `knowledge.fresh` is false, relay its `reasons` and `fix` — the mirrored snapshot is faithful but stale."

Contract listings — exact replacements:

`src/omc/distribution/AGENTS.md:48-50`, replace
```
- **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
  `OMC_SQUASH` / `OMC_REBASE_MAIN` / `OMC_TICKET` verdicts are parsed by tools
  — emit them exactly as their skills specify, never wrapped in markdown.
```
with
```
- **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
  `OMC_SQUASH` / `OMC_REBASE_MAIN` / `OMC_TICKET` / `OMC_KNOWLEDGE` verdicts are
  parsed by tools — emit them exactly as their skills specify, never wrapped in
  markdown.
```

`.omc/config/AGENTS.md:62-63`, replace
```
- **Skill machine contracts** are single JSON lines: `OMC_SLUG`, `OMC_STAGE`,
  `OMC_SQUASH`, `OMC_REBASE_MAIN`. Parsers tolerate markdown wrapping; skills
```
with
```
- **Skill machine contracts** are single JSON lines: `OMC_SLUG`, `OMC_STAGE`,
  `OMC_SQUASH`, `OMC_REBASE_MAIN`, `OMC_KNOWLEDGE`. Parsers tolerate markdown wrapping; skills
```

`.omc/skills/review/SKILL.md:21-22`, replace
```
- Skills keep their machine contracts intact (OMC_SLUG / OMC_STAGE /
  OMC_SQUASH lines; internal skills marked "not meant for direct invocation").
```
with
```
- Skills keep their machine contracts intact (OMC_SLUG / OMC_STAGE /
  OMC_SQUASH / OMC_REBASE_MAIN / OMC_KNOWLEDGE lines; internal skills marked
  "not meant for direct invocation").
```

`.omc/skills/explain-context/SKILL.md:26`, replace
```
prefixed `OMC_SLUG` / `OMC_STAGE` / `OMC_SQUASH`; "the chicken" in docs means
```
with
```
prefixed `OMC_SLUG` / `OMC_STAGE` / `OMC_SQUASH` / `OMC_REBASE_MAIN` /
`OMC_KNOWLEDGE`; "the chicken" in docs means
```

README: insert a NEW paragraph directly after line 65 (the "Three flags change the shape of the run" paragraph about `omc start`), not appended to the line-69 paragraph:

```
`omc start` also verifies the primary checkout's knowledge snapshot after fetching the base. When it is stale it prints what is wrong (how far behind, a foreign path, an unfinished analyze, docs behind the index) and the exact `omc watch` command that fixes it, to run in the primary checkout — start itself never rebuilds anything and never takes a lock. The same verdict travels into the session as a single `OMC_KNOWLEDGE` line, so `/omc:explain` says the graph is stale instead of answering from old data.
```

- [ ] **Step 6: Run tests and lint**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_skills_source.py -q && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add skills src/omc/distribution/AGENTS.md README.md tests/unit/test_plugin_manifests.py \
  .omc/config/AGENTS.md .omc/skills/review/SKILL.md .omc/skills/explain-context/SKILL.md
git commit -m "docs(skills): delegate index/document to omc internal gitnexus refresh; relay OMC_KNOWLEDGE

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: E2E — a stale, pinned wiki is regrouped by `omc watch --once --enable-documentation`

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/test_e2e_watch.py` (module docstring + append), `docker/Dockerfile.e2e:70` (pin `GITNEXUS_REF`)

Written now, executed only under `just e2e-tests` (Docker, real GitNexus from the fork, Claude token). Do not run it in the per-task loop.

- [ ] **Step 1: Pin the GitNexus ref in the E2E image**

`docker/Dockerfile.e2e`: add after line 44 (`ARG TARGETARCH`):

```dockerfile
# The fork commit the E2E image indexes with — must carry the wiki-regroup and
# copied-index fixes (chris-husse/GitNexus PR #4) this suite relies on.
ARG GITNEXUS_REF=8de99dc966f22eef3de74eeeb446b0bed695f3c7
```

and replace line 70

```dockerfile
    git clone --depth 1 https://github.com/chris-husse/GitNexus.git /root/.omc/dependencies/gitnexus; \
```

with

```dockerfile
    git clone --depth 1 https://github.com/chris-husse/GitNexus.git /root/.omc/dependencies/gitnexus; \
    git -C /root/.omc/dependencies/gitnexus fetch --depth 1 origin "${GITNEXUS_REF}"; \
    git -C /root/.omc/dependencies/gitnexus checkout -q FETCH_HEAD; \
```

`8de99dc966f22eef3de74eeeb446b0bed695f3c7` is a placeholder ONLY in the `ARG` default: replace it with the full 40-character SHA the conductor provides (the merge commit of PR #4 on `chris-husse/GitNexus` main, `8de99dc9…`).

- [ ] **Step 2: Update the module docstring and append the test**

Module docstring becomes: `"""Live \`omc watch --once\`: real git sync + real GitNexus reindex. Most tests need no tokens; the documentation test requires a Claude token (require_token)."""`. Import `require_token` from `.harness`.

```python
def test_watch_once_regroups_a_stale_pinned_wiki(container):
    """The fork's GitNexus (PR #4) regroups on its own once omc runs `wiki` at the
    right time; omc must not carry an unpin workaround. Seed: wiki metadata
    behind the index with a one-module tree, then three distinct concerns land."""
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container, path="/work/regroup-repo")
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, out[:1500]
    seed = (
        "import json, os, subprocess\n"
        f"repo = '{repo}'\n"
        "head = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'],"
        " capture_output=True, text=True).stdout.strip()\n"
        "w = repo + '/.gitnexus/wiki'\n"
        "os.makedirs(w, exist_ok=True)\n"
        "tree = [{'name': 'Everything', 'slug': 'everything', 'files': ['README.md']}]\n"
        "json.dump(tree, open(w + '/module_tree.json', 'w'))\n"
        "json.dump(tree, open(w + '/first_module_tree.json', 'w'))\n"
        "json.dump({'fromCommit': head, 'generatedAt': 'x', 'model': 'm', 'lang': '',"
        " 'moduleFiles': {'Everything': ['README.md']}, 'moduleTree': tree},"
        " open(w + '/meta.json', 'w'))\n"
        "open(w + '/everything.md', 'w').write('# Everything\\n')\n"
        "open(w + '/overview.md', 'w').write('# Overview\\n')\n"
    )
    rc, out = run_in(container, ["python3", "-c", seed])
    assert rc == 0, out
    # Three distinct concerns (auth, billing, reports), three real modules each,
    # landing on origin so watch syncs them: >5 new files trips GitNexus's
    # escalation and the LLM has genuinely separable material to group.
    grow = (
        f"git clone -q {repo}-origin /work/regroup-other && cd /work/regroup-other && "
        "mkdir -p auth billing reports && "
        "printf 'export function login(user, pw) { return user && pw ? {ok: true, user} : {ok: false}; }\\n' > auth/login.js && "
        "printf 'export function logout(session) { session.active = false; return session; }\\n' > auth/logout.js && "
        "printf 'export function hashPassword(pw) { return [...pw].reverse().join(\"\"); }\\n' > auth/password.js && "
        "printf 'export function createInvoice(items) { return {total: items.reduce((a, i) => a + i.price, 0), items}; }\\n' > billing/invoice.js && "
        "printf 'export function charge(card, amount) { return {card: card.slice(-4), amount, status: \"charged\"}; }\\n' > billing/charge.js && "
        "printf 'export function refund(chargeId) { return {chargeId, status: \"refunded\"}; }\\n' > billing/refund.js && "
        "printf 'export function dailyReport(rows) { return rows.filter(r => r.day === new Date().getDay()); }\\n' > reports/daily.js && "
        "printf 'export function monthlyReport(rows) { return rows.filter(r => r.month === new Date().getMonth()); }\\n' > reports/monthly.js && "
        "printf 'export function exportCsv(rows) { return rows.map(r => Object.values(r).join(\",\")).join(\"\\\\n\"); }\\n' > reports/export.js && "
        "git add -A && git commit -qm 'auth, billing, reports' && git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", grow])
    assert rc == 0, out

    rc, out = run_in(
        container, ["omc", "watch", "--once", "--enable-documentation"], cwd=repo, timeout=1800
    )
    assert rc == 0, f"watch failed:\n{out[:2000]}"
    assert "✓ documentation refreshed" in out, out

    rc, tree = run_in(container, ["bash", "-c", f"/bin/cat {repo}/.gitnexus/wiki/module_tree.json"])
    assert rc == 0 and tree.count('"slug"') > 1, f"module tree still pinned to one module:\n{tree}"
    rc, pages = run_in(container, ["bash", "-c", f"ls {repo}/.omc/docs/gitnexus/docs/*.md | wc -l"])
    assert rc == 0 and int(pages.strip()) > 1, f"docs mirror empty or single page:\n{pages}"
    rc, status = run_in(container, ["omc", "internal", "gitnexus", "status"], cwd=repo)
    assert rc == 0 and '"fresh": true' in status, status
```

- [ ] **Step 3: Syntax-check and lint only**

Run: `uv run python -m py_compile tests/e2e/test_e2e_watch.py && uvx ruff check src tests && uvx ruff format --check src tests`
Expected: rc 0, ruff clean (`uvx ruff format tests/e2e/test_e2e_watch.py` if the long printf lines need wrapping — keep each shell command intact inside the string).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_e2e_watch.py docker/Dockerfile.e2e
git commit -m "test(e2e): stale pinned wiki is regrouped by omc watch --once --enable-documentation

Pins GITNEXUS_REF in the E2E image so it carries the fork's regroup fix.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Final review — spec vs implementation

**Model:** top tier

**Files:** read-only over everything above.

- [ ] **Step 1: Full unit run**

Run: `uv run python -m pytest tests/unit -q`
Expected: all pass, zero skips.

- [ ] **Step 2: Build gate**

Run: `just build` (ruff + package build). Expected: clean.

- [ ] **Step 3: Boundary rules**

Run: `grep -rn "import subprocess" src/omc`
Expected output — EXACTLY these four, all pre-existing:
```
src/omc/toolctx.py:6:import subprocess
src/omc/notify.py:20:import subprocess
src/omc/gitnexus.py:19:    import subprocess
src/omc/watch.py:17:import subprocess
```
(`gitnexus.py:19` is the `TYPE_CHECKING` annotation import; `notify.py`/`watch.py` import it only for exception names.) There must be NO new occurrence in `wikirun.py`, `gitnexus.py` runtime code, `internal.py`, or `start.py`.

- [ ] **Step 4: Spec checklist** — for each spec item confirm code + test:
  §1 all ten codes and the inverted-skips-distances rule; inversion from the chosen metadata file's `branch`; `basis` unresolved; `fix`/`run_in` (Task 2). §2 reset/clean verified by metadata absence; inverted destroys first; analyze-first ladder with exit codes narrated, never trusted; wiki supervised; mirror only when fresh; "docs mirror cleared" only when it went away; `✓ knowledge is current` (Task 3). §3 verdict on up-to-date/synced only; `healed` no hooks; `knowledge-stale:<codes>` suppression; `--once` fires hook; `--reset-gitnexus` boot check before `ensure_gitnexus`; pending reset consumed on fresh tick and cleared on `knowledge-stale`; `--once` skip-tick "reset not applied" line; busy narration via the context manager (Tasks 4, 5). §4 status/refresh dispatch before `--git`; status exact match; HEAD basis; no fetch; hint line; rc 0/3/1/2; skills (Tasks 6, 8). §5 `→ fetching origin/<base>`; seed layout; per-reason alert lines; index-missing form; dry-run row wording; primary None (Task 7). §6 proxy stderr project-mode only; `knowledge` in all three payloads; contract listings (Tasks 6, 8). Machine contract keys always present (Task 2 `to_json`). Dockerfile `GITNEXUS_REF` real SHA, no placeholder left (Task 9).

- [ ] **Step 5: Report** any gap as a follow-up task in this plan file (do not leave it implicit), then hand back to the conductor for `/omc:finish`.

---

## Self-Review

**Spec coverage (amended spec):** §1 → Task 2 (incl. chosen-file inversion); §2 → Task 3 (+ Task 1 plumbing; exit codes narrated only); §3 → Task 5 (+ Task 4 context-manager lock helper; `knowledge-stale` clears `reset_pending`; `--once` "reset not applied" line); §4 → Task 6 + Task 8 skills; §5 → Task 7 (`→ fetching`, per-reason lines, dry-run wording); §6 → Task 6 (proxy, rebase-main) + Task 8 (listings, README paragraph); Machine contract → Task 2 `to_json` + Task 6 emitters; Verification list → Tasks 2–9 (unit) and Task 9 (E2E, token-gated, distinct concerns, docs mirror asserted, image pinned); `test_dependency.py` untouched → Task 1; `START_WAIT_MSG` exact matches → Task 4.

**Placeholder scan:** the only placeholder is `8de99dc966f22eef3de74eeeb446b0bed695f3c7` in Task 9 Step 1, deliberately marked for the conductor's SHA; Task 10 Step 4 checks it is gone. Task 1 Step 3's "existing class body" is a cut-and-paste instruction of code already in the repo.

**Type consistency:** `Freshness` fields `fresh/reasons/fix/run_in/basis` and methods `to_json()/codes()/index_codes()/wiki_codes()` are used identically in Tasks 2, 3, 5, 6, 7. `refresh_knowledge(ctx, cfg, root, base, *, documentation, reset, ref="HEAD", say)` matches `watch._refresh_index` (Task 3) and `internal._knowledge_refresh` (Task 6). `acquire_busy_narrated(lock, say)` is a `@contextmanager` in Task 4 and used with `with` in Tasks 5 and 6. `build_start_seed(context, knowledge=None)` takes the `to_json()` dict (Task 7). `_git_out(repo, *args)` in `test_watch.py` (Task 3) is what Task 5's tests call. `_HOLDER`/`_hold_in_subprocess` come from `test_watchlock.py` everywhere.

**Green at every commit:** Task 3 rewrites the two `--once` tests together with the fixture change, so no task ends with a red suite.
