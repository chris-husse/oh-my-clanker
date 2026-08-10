# Alias-Safe In-Place Snapshot Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `omc internal rebase-main` (and every other `mirror_dir` caller) must never crash or destroy data when a project shares one `.omc` across checkouts via symlinks — the snapshot mirror becomes an in-place incremental sync that no-ops when src and dst are the same directory.

**Architecture:** Rewrite `mirror_dir` in `src/omc/mirror.py` as a stdlib-only in-place sync (guards first: alias → `False` untouched, nesting → `OmcError`; then per-entry add/replace/delete with no wipe window). `mirror_snapshot` classifies each entry as `synced` (real sync ran) or `shared` (alias no-op) and returns a `SnapshotResult` NamedTuple; `internal.py` adds the always-present `"shared"` key to the `OMC_REBASE_MAIN` verdict; the rebase-main skill documents it. Callers `watch.py`/`dependency.py` are untouched.

**Tech Stack:** Python stdlib only (`os`, `shutil`, `filecmp`, `pathlib`), pytest + `tmp_path`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-06-fix-rebase-main-docs-mirror-missing-design.md`

## Global Constraints

- Stdlib only — no new dependency (no `dirsync`, no `rsync` subprocess; `mirror.py`'s doctrine is "no shell").
- `.omc` symlinks are preserved — never unlink, never materialize, never delete *through* a symlink (a dst-entry symlink is removed with `unlink()` of the link itself only).
- The dst root directory is never deleted or recreated (no empty/missing window for concurrent readers); it IS created (with parents) when missing.
- `OMC_REBASE_MAIN` stays a single-line JSON verdict; `synced` and `shared` keys are always present on the success path.
- Tests: `uv run pytest tests/unit -q` from the worktree root. **Before the first test run of the session, run `uv sync --reinstall` once** — a worktree's copied `.venv` can execute the PRIMARY checkout's code otherwise (known trap).
- `tests/unit/test_plugin_manifests.py` pins needles in `skills/rebase-main/SKILL.md` (`omc internal rebase-main`, `OMC_REBASE_MAIN`, `rc 3`, `conflict`) — skill edits must preserve them.
- Comment/docstring style: state constraints the code can't show; no change-narration comments.

---

### Task 1: `mirror_dir` becomes a guarded in-place incremental sync

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/mirror.py` (replace `mirror_dir`, add private helpers; `clear_docs_mirror`, `SNAPSHOT_DIRS`, `DOCS_MIRROR_REL` unchanged; `mirror_snapshot` untouched in this task — it still compiles because `mirror_dir`'s call signature is unchanged)
- Test: `tests/unit/test_mirror.py` (add six tests; existing tests untouched and must keep passing)

**Interfaces:**
- Consumes: `OmcError` from `src/omc/errors.py` (exists).
- Produces: `mirror_dir(src: Path, dst: Path) -> bool` — `True` when a real sync ran, `False` (filesystem untouched) when `src.resolve() == dst.resolve()`; raises `OmcError` when one resolved path nests inside the other. Task 2 relies on exactly this bool.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mirror.py` (imports at top of file already include `pytest`, `OmcError`, `mirror_dir`, `mirror_snapshot`; add `import os`, `import shutil` if missing):

```python
def test_mirror_dir_noop_when_dst_aliases_src(tmp_path):
    # The incident: primary/.omc and wt/.omc are symlinks to one shared tree;
    # syncing docs onto itself must touch nothing and return False.
    shared = tmp_path / "shared-omc"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "page.md").write_text("KNOWLEDGE")
    (tmp_path / "primary").mkdir()
    (tmp_path / "wt").mkdir()
    os.symlink(shared, tmp_path / "primary" / ".omc")
    os.symlink(shared, tmp_path / "wt" / ".omc")

    src = tmp_path / "primary" / ".omc" / "docs"
    dst = tmp_path / "wt" / ".omc" / "docs"
    assert mirror_dir(src, dst) is False

    assert (shared / "docs" / "page.md").read_text() == "KNOWLEDGE"


def test_mirror_dir_refuses_nested_paths(tmp_path):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    with pytest.raises(OmcError, match="nest"):
        mirror_dir(parent, child)
    with pytest.raises(OmcError, match="nest"):
        mirror_dir(child, parent)


def test_mirror_dir_updates_in_place(tmp_path):
    # No wipe-and-recreate: the dst root and unchanged files survive by inode.
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "same.md").write_text("same")
    (src / "changed.md").write_text("new content")
    (src / "added.md").write_text("added")
    shutil.copy2(src / "same.md", dst / "same.md")  # identical size+mtime
    (dst / "changed.md").write_text("old")
    (dst / "excess.md").write_text("x")
    root_ino = dst.stat().st_ino
    same_ino = (dst / "same.md").stat().st_ino
    same_mtime = (dst / "same.md").stat().st_mtime_ns

    assert mirror_dir(src, dst) is True

    assert dst.stat().st_ino == root_ino
    assert (dst / "same.md").stat().st_ino == same_ino
    assert (dst / "same.md").stat().st_mtime_ns == same_mtime
    assert (dst / "changed.md").read_text() == "new content"
    assert (dst / "added.md").read_text() == "added"
    assert not (dst / "excess.md").exists()


def test_mirror_dir_replaces_type_conflicts(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "dir-name").mkdir(parents=True)
    (src / "dir-name" / "f.md").write_text("F")
    (src / "file-name").write_text("file")
    dst.mkdir()
    (dst / "dir-name").write_text("was a file")
    (dst / "file-name").mkdir()
    (dst / "file-name" / "junk").write_text("j")

    assert mirror_dir(src, dst) is True

    assert (dst / "dir-name" / "f.md").read_text() == "F"
    assert (dst / "file-name").read_text() == "file"


def test_mirror_dir_replaces_dst_symlink_without_following(tmp_path):
    # dst has a symlink where src has a real dir: the LINK is removed,
    # its target's contents are never touched.
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "f.md").write_text("F")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.md").write_text("KEEP")
    dst = tmp_path / "dst"
    dst.mkdir()
    os.symlink(outside, dst / "docs")

    assert mirror_dir(src, dst) is True

    assert not (dst / "docs").is_symlink()
    assert (dst / "docs" / "f.md").read_text() == "F"
    assert (outside / "keep.md").read_text() == "KEEP"


def test_mirror_dir_recreates_src_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.md").write_text("real")
    os.symlink("real.md", src / "link.md")  # relative target preserved verbatim
    dst = tmp_path / "dst"

    assert mirror_dir(src, dst) is True

    assert (dst / "link.md").is_symlink()
    assert os.readlink(dst / "link.md") == "real.md"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_mirror.py -q`
Expected: the six new tests FAIL (`mirror_dir` returns `None`, alias case raises `FileNotFoundError`, nesting raises nothing); the six pre-existing tests still PASS.

- [ ] **Step 3: Implement the in-place sync**

Replace `mirror_dir` in `src/omc/mirror.py` and add the two helpers below it. Add `import filecmp` and `import os` to the imports. Keep `clear_docs_mirror` and the module constants exactly as they are; extend the module docstring's last paragraph with the shared-`.omc` sentence shown here:

```python
"""Deterministic directory mirroring (rsync --delete semantics, no shell).

The snapshot model copies the primary root's `.gitnexus/` and `.omc/docs/`
into worktrees; refreshing that snapshot must delete extraneous files. An LLM
is never trusted with that operation — this is the unit-tested Python it
calls instead (via `omc internal rebase-main`).

Some projects share one `.omc` across all checkouts via symlinks (the
chicken-era shared knowledge tree), so src and dst can be the SAME directory
reached through different paths. Sync is therefore in place (no
wipe-and-recreate window for concurrent readers) and alias-guarded.
"""

from __future__ import annotations

import filecmp
import os
import shutil
from pathlib import Path

from .errors import OmcError
```

```python
def mirror_dir(src: Path, dst: Path) -> bool:
    """Make ``dst`` an exact copy of ``src`` by syncing in place.

    Returns False — filesystem untouched — when both resolve to the same
    directory: with a shared `.omc`, dst already IS src, and the old
    delete-then-copy destroyed the source through the alias. Raises OmcError
    when one resolved path nests inside the other (a sync would delete its
    own source or recurse into itself).
    """
    src = Path(src)
    dst = Path(dst)
    src_r = src.resolve()
    dst_r = dst.resolve()
    if src_r == dst_r:
        return False
    if src_r in dst_r.parents or dst_r in src_r.parents:
        raise OmcError(f"refuse: {src} and {dst} nest within each other")
    _sync_tree(src, dst)
    return True


def _remove_entry(path: Path) -> None:
    """Remove one dst entry of any type; a symlink is unlinked, never followed."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _sync_tree(src: Path, dst: Path) -> None:
    """Recursively converge dst onto src without ever removing dst itself."""
    dst.mkdir(parents=True, exist_ok=True)
    names = set()
    for entry in sorted(src.iterdir()):
        names.add(entry.name)
        target = dst / entry.name
        # is_symlink() is checked before is_dir()/is_file() everywhere: both
        # follow links, and a link must never be traversed for type checks.
        if entry.is_symlink():
            link = os.readlink(entry)
            if target.is_symlink() and os.readlink(target) == link:
                continue
            if target.is_symlink() or target.exists():
                _remove_entry(target)
            os.symlink(link, target)
        elif entry.is_dir():
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                _remove_entry(target)
            _sync_tree(entry, target)
        else:
            if target.is_symlink() or (target.exists() and not target.is_file()):
                _remove_entry(target)
            elif target.is_file() and filecmp.cmp(entry, target, shallow=True):
                continue  # size+mtime match (copy2 preserves both): untouched
            shutil.copy2(entry, target)
    for leftover in list(dst.iterdir()):
        if leftover.name not in names:
            _remove_entry(leftover)
```

Notes for the implementer:

- `filecmp.cmp(..., shallow=True)` compares type, size, and mtime — the
  rsync-style quick check. `copy2` preserves mtime, so a file copied once is
  skipped on every later run.
- `shutil.copy2` onto an existing regular file overwrites in place (the
  inode survives) — no unlink needed on the plain "content changed" path.
- Do NOT use `shutil.copytree` or `shutil.rmtree(dst)` at the top level;
  the whole point is that dst is never absent mid-sync.

- [ ] **Step 4: Run the full mirror test file**

Run: `uv run pytest tests/unit/test_mirror.py -q`
Expected: all 12 tests PASS (6 pre-existing + 6 new).

- [ ] **Step 5: Commit**

```bash
git add src/omc/mirror.py tests/unit/test_mirror.py
git commit -m "mirror_dir: guarded in-place incremental sync (alias-safe, no wipe window)"
```

---

### Task 2: `mirror_snapshot` classifies synced vs shared (`SnapshotResult`)

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/mirror.py` (`mirror_snapshot` + new `SnapshotResult`)
- Test: `tests/unit/test_mirror.py` (one new test; two existing tests updated)

**Interfaces:**
- Consumes: `mirror_dir(src, dst) -> bool` from Task 1.
- Produces: `SnapshotResult(NamedTuple)` with fields `synced: list[str]`, `shared: list[str]`; `mirror_snapshot(primary_root: Path, worktree_root: Path) -> SnapshotResult`. Task 3 relies on `.synced` / `.shared`.

- [ ] **Step 1: Write the failing test and update the two existing asserts**

Append to `tests/unit/test_mirror.py`:

```python
def test_mirror_snapshot_reports_shared_omc_docs(tmp_path):
    # Incident regression end-to-end: shared .omc via symlinks in BOTH roots;
    # .gitnexus is a real per-checkout dir and still syncs.
    shared = tmp_path / "shared-omc"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "page.md").write_text("docs")
    primary = tmp_path / "primary"
    wt = tmp_path / "wt"
    (primary / ".gitnexus").mkdir(parents=True)
    (primary / ".gitnexus" / "graph.db").write_text("db")
    wt.mkdir()
    os.symlink(shared, primary / ".omc")
    os.symlink(shared, wt / ".omc")

    result = mirror_snapshot(primary, wt)

    assert result.synced == [".gitnexus"]
    assert result.shared == [".omc/docs"]
    assert (shared / "docs" / "page.md").read_text() == "docs"
    assert (wt / ".omc").is_symlink()  # the symlink is preserved, never materialized
```

In `test_mirror_snapshot_syncs_known_dirs_only`, replace

```python
    synced = mirror_snapshot(primary, wt)

    assert synced == [".gitnexus", ".omc/docs"]
```

with

```python
    result = mirror_snapshot(primary, wt)

    assert result.synced == [".gitnexus", ".omc/docs"]
    assert result.shared == []
```

In `test_mirror_snapshot_skips_missing_sources`, replace

```python
    assert mirror_snapshot(primary, wt) == [".gitnexus"]  # no .omc/docs -> skipped
```

with

```python
    result = mirror_snapshot(primary, wt)
    assert result.synced == [".gitnexus"]  # no .omc/docs -> skipped entirely
    assert result.shared == []
```

- [ ] **Step 2: Run to verify the new/updated tests fail**

Run: `uv run pytest tests/unit/test_mirror.py -q`
Expected: `test_mirror_snapshot_reports_shared_omc_docs` and the two updated tests FAIL (`mirror_snapshot` still returns a plain list, which has no `.synced`).

- [ ] **Step 3: Implement `SnapshotResult`**

In `src/omc/mirror.py`: add `from typing import NamedTuple` to the imports, then place the class above `mirror_snapshot` and update the function:

```python
class SnapshotResult(NamedTuple):
    """What the snapshot mirror did, per SNAPSHOT_DIRS entry."""

    synced: list[str]  # a real sync ran
    shared: list[str]  # src and dst are the same dir (shared .omc) — untouched


def mirror_snapshot(primary_root: Path, worktree_root: Path) -> SnapshotResult:
    """Mirror the knowledge snapshot from the primary root into a worktree.

    Only ``SNAPSHOT_DIRS`` are touched; absent sources are skipped. Entries
    whose src and dst alias the same directory (shared `.omc`) are reported
    as shared, not synced. Refuses to operate when both roots resolve to the
    same directory.
    """
    primary_root = Path(primary_root).resolve()
    worktree_root = Path(worktree_root).resolve()
    if primary_root == worktree_root:
        raise OmcError("refuse: primary and worktree are the same directory")
    synced: list[str] = []
    shared: list[str] = []
    for rel in SNAPSHOT_DIRS:
        src = primary_root / rel
        if not src.is_dir():
            continue
        if mirror_dir(src, worktree_root / rel):
            synced.append(rel)
        else:
            shared.append(rel)
    return SnapshotResult(synced=synced, shared=shared)
```

- [ ] **Step 4: Run the mirror test file**

Run: `uv run pytest tests/unit/test_mirror.py -q`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omc/mirror.py tests/unit/test_mirror.py
git commit -m "mirror_snapshot: classify synced vs shared entries (SnapshotResult)"
```

---

### Task 3: `OMC_REBASE_MAIN` verdict gains the always-present `shared` key

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/internal.py:75-82` (`_rebase_main` result handling)
- Test: `tests/unit/test_internal.py` (extend two existing verdict assertions)

**Interfaces:**
- Consumes: `mirror_snapshot(...) -> SnapshotResult` from Task 2.
- Produces: success verdict `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...], "shared": [...]}` — machine schema consumed by `skills/rebase-main/SKILL.md` (Task 4).

- [ ] **Step 1: Extend the existing test's assertions (failing first)**

In `tests/unit/test_internal.py::test_rebase_main_rebases_and_mirrors_snapshot`, after the existing `assert ".gitnexus" in verdict["synced"]` line, add:

```python
    assert verdict["shared"] == []  # stable schema: key present even when empty
```

Then add this new test after it (helpers `_setup_primary_with_origin`, `_add_worktree`, `_run` already exist in the file):

```python
def test_rebase_main_reports_shared_omc_docs(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    shared = tmp_path / "shared-omc"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "page.md").write_text("docs")
    os.symlink(shared, primary / ".omc")
    os.symlink(shared, wt / ".omc")

    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 0 and verdict["ok"] is True
    assert verdict["shared"] == [".omc/docs"]
    assert (shared / "docs" / "page.md").read_text() == "docs"  # nothing destroyed
```

- [ ] **Step 2: Run to verify both fail**

Run: `uv run pytest tests/unit/test_internal.py -q`
Expected: the extended test FAILS with `KeyError: 'shared'`; the new test FAILS the same way (on current main it would have crashed with `FileNotFoundError` before Task 1).

- [ ] **Step 3: Implement the verdict change**

In `src/omc/internal.py::_rebase_main`, replace

```python
    synced = mirror_snapshot(Path(primary), Path(root))
```

with

```python
    result = mirror_snapshot(Path(primary), Path(root))
```

and replace the success verdict line

```python
    _verdict({"ok": True, "rebased": f"{old}..{new}", "synced": synced})
```

with

```python
    _verdict(
        {
            "ok": True,
            "rebased": f"{old}..{new}",
            "synced": result.synced,
            "shared": result.shared,
        }
    )
```

(The large comment block between those lines about never invoking gitnexus stays exactly where it is.)

- [ ] **Step 4: Run the internal test file**

Run: `uv run pytest tests/unit/test_internal.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "rebase-main verdict: report shared snapshot entries alongside synced"
```

---

### Task 4: Document `shared` in the rebase-main skill; full-suite verification

**Model:** standard coding tier

**Files:**
- Modify: `skills/rebase-main/SKILL.md:20-25` (the "Interpreting the outcome" list)
- Test: whole unit suite (`tests/unit/test_plugin_manifests.py` guards the skill text)

**Interfaces:**
- Consumes: the verdict schema from Task 3.
- Produces: user-facing skill documentation; no code.

- [ ] **Step 1: Edit the skill**

In `skills/rebase-main/SKILL.md`, replace the first bullet of "Interpreting the outcome"

```markdown
- `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...]}` —
  report what moved and which snapshot dirs were re-mirrored, then continue.
```

with

```markdown
- `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...],
  "shared": [...]}` — report what moved and which snapshot dirs were
  re-mirrored, then continue. Entries under `shared` were already present
  via a shared `.omc` (symlinked across checkouts) — nothing was copied,
  and that is success, not a warning.
```

The needles `omc internal rebase-main`, `OMC_REBASE_MAIN`, `rc 3`, and `conflict` must all remain present in the file (they still are — this edit only touches the first bullet).

- [ ] **Step 2: Run the manifest guard test**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS.

- [ ] **Step 3: Run the whole unit suite**

Run: `uv run pytest tests/unit -q`
Expected: all PASS, no skips introduced by this work.

- [ ] **Step 4: Commit**

```bash
git add skills/rebase-main/SKILL.md
git commit -m "rebase-main skill: document the shared verdict key"
```
