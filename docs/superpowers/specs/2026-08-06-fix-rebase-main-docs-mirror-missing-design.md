# Fix: rebase-main docs mirror — alias-safe, in-place incremental sync

**Date:** 2026-08-06
**Status:** approved (brainstorm converged; symlink-preserving in-place sync chosen over materialization)
**Slug:** fix-rebase-main-docs-mirror-missing

## Problem

`omc internal rebase-main`, run in a hummingbird worktree, crashed:

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/chriphus/Projects/hummingbird-wt/.omc/docs'
```

Root cause — **snapshot self-destruction under a shared `.omc`**. Some
projects share one `.omc` directory across all checkouts via symlinks
(hummingbird: every checkout's `.omc` →
`~/Projects/chicken-data/hummingbird-omc`; the chicken-era shared knowledge
tree). `mirror_snapshot` (`src/omc/mirror.py`) guards only whole-root
equality, so for the `.omc/docs` snapshot entry both `src`
(primary `.omc/docs`) and `dst` (worktree `.omc/docs`) resolve **through
their symlinks to the same physical directory**. `mirror_dir` then:

1. `dst.exists()` → True → `shutil.rmtree(dst)` — deletes the shared docs,
   which *is* `src` (real data loss; `omc watch` happened to regenerate the
   directory minutes later, which is how the same-second birth time via
   every checkout exposed the aliasing).
2. `shutil.copytree(src, dst)` → `os.scandir(src)` → `FileNotFoundError`,
   reported under the primary's unresolved path spelling.

Verified empirically: identical inode via both paths, a probe file created
through one path visible through the other. Sandbox-verified that a
genuinely *missing* `.omc/docs` is skipped cleanly by the existing
`is_dir()` guard — only aliasing crashes.

A second, latent defect with the same root: wipe-and-recreate leaves a
window where the target directory is missing entirely. Any concurrent
reader (a session mid-`/omc:explain`, watch's own flows) sees the snapshot
vanish — for `.gitnexus` that window spans a 363 MB copy.

## User rulings (converged over brainstorm iterations — final)

1. **Preserve the `.omc` symlink.** Never materialize a real copy, never
   unlink it. Earlier design iterations (alias no-op only; full
   materialization; first-level per-item copy) were explicitly superseded.
2. **Fix the update semantics instead**: documentation is generated
   out-of-place (GitNexus `wiki` already writes to the `.gitnexus/wiki`
   staging directory) and then **synced incrementally** into `.omc/docs` —
   replace changed files, add new ones, delete excess ones — never
   wipe-and-recreate. A smooth, transparent update with no empty/missing
   window for concurrent readers.
3. Under a shared `.omc` the sync is a natural no-op (a directory synced
   onto itself changes nothing) — guard it explicitly, skip untouched, and
   report it.
4. Symlinks *inside* `.omc` are preserved as symlinks; if they alias
   something, that is the user's problem — omc must merely never crash on
   or delete through them.
5. **Stdlib only.** No new dependency: `dirsync` is unmaintained; shelling
   to `rsync` violates `mirror.py`'s own no-shell doctrine and macOS ships
   divergent rsync implementations. The sync is small, deterministic
   Python over `os`/`shutil`, fully unit-tested — in keeping with "mirror
   logic is deliberately unit-tested Python an LLM is never trusted with".

## Design

### `mirror_dir` becomes an in-place incremental sync (`src/omc/mirror.py`)

Outward contract unchanged — "make `dst` an exact copy of `src`, rsync
`--delete` semantics" — new mechanics and a new `bool` return:

**Guards, checked before touching anything:**

- `src.resolve() == dst.resolve()` → return `False`, filesystem untouched.
  This is the shared-`.omc` case: `dst` already *is* `src`.
- One resolved path is an ancestor of the other → raise `OmcError` naming
  both paths (deleting `dst` would eat the source; copying would recurse
  into itself).

**Sync (returns `True`):**

- Never delete or recreate the `dst` root; create it (with parents) when
  missing — `dependency.py`'s first documentation run relies on that, as
  does the first snapshot into a fresh worktree. Walk `src` top-down and
  apply per entry:
  - missing directories in `dst` → create;
  - symlink entries → recreate verbatim in `dst` (the `symlinks=True`
    doctrine; replace only when the link target differs);
  - files → copy when missing or different by shallow compare
    (size + mtime; `copy2` preserves both, so unchanged files converge and
    are skipped on later runs — never rewritten);
  - type conflicts (file vs dir vs symlink at the same name) → remove just
    that entry and replace it; when the conflicting `dst` entry is a
    symlink, removal is `unlink` of the link itself — never a delete
    *through* it.
- Then remove `dst` entries absent from `src` (excess files unlinked,
  excess directories rmtree'd — an excess subtree is by definition not the
  source).
- `dst` paths that traverse a user symlink to somewhere *else* sync
  through it — that is the user's layout and, per ruling 4, the user's
  problem. Only the self-destructive alias case is guarded.

### `mirror_snapshot` classifies instead of crashing

Loop unchanged (`SNAPSHOT_DIRS` = `.gitnexus`, `.omc/docs`; absent sources
still skipped). Each `mirror_dir` result is classified: `True` → `synced`,
`False` → `shared`. Return type changes from `list[str]` to a
`SnapshotResult` NamedTuple `(synced: list[str], shared: list[str])`.
The whole-root equality refusal stays.

Bonus from the in-place semantics: the `.gitnexus` snapshot (363 MB graph)
no longer vanishes mid-refresh under a running session, and repeat
rebase-mains copy only what changed.

### Verdict (`src/omc/internal.py::_rebase_main`)

```
OMC_REBASE_MAIN {"ok": true, "rebased": "a..b", "synced": [".gitnexus"], "shared": [".omc/docs"]}
```

`shared` is always present (stable machine schema; skills parse this
line). Shared-not-copied is success — with a shared `.omc` the docs are
already in the worktree by construction — so exit codes are unchanged.

### Callers — no signature churn

- `watch.py` (wiki → `.omc/docs/gitnexus/docs`) and `dependency.py`: call
  sites unchanged; both inherit the smooth in-place sync and the guards.
- `clear_docs_mirror` stays a deliberate full delete (heal path: stale
  docs are worse than absent docs).
- `skills/rebase-main/SKILL.md` enumerates the verdict (line 22) — add the
  `shared` key ("already present via a shared `.omc` — nothing copied").
  `tests/unit/test_plugin_manifests.py:261` pins needles in that skill
  text (`omc internal rebase-main`, `OMC_REBASE_MAIN`, `rc 3`,
  `conflict`) — the edit must preserve them.

### Out of scope

- Worktrunk's `copy-ignored` behavior (it created the worktree `.omc`
  symlinks; that layout is now fully supported, not fought).
- `omc:check-wt-config` learning to recognize symlinked-`.omc` setups —
  follow-up candidate.
- `start.py` changes; `clear_docs_mirror` changes.

## Testing

Unit (`tests/unit/test_mirror.py`, plain pytest + `tmp_path`, one behavior
per test):

- **Incident regression**: primary and worktree `.omc` both symlinks to
  one shared tree → `mirror_dir` returns `False`, shared tree
  byte-identical afterwards; `mirror_snapshot` reports
  `shared == [".omc/docs"]` and nothing is deleted.
- **In-place proof**: `dst` root inode unchanged across a sync; an
  unchanged file keeps its inode and mtime (never rewritten).
- Add + replace + delete-excess in one sync; type-conflict replacement
  (file↔dir↔symlink); symlink entries inside `src` recreated in `dst`.
- Containment → `OmcError`, both directions.
- `mirror_snapshot` synced/shared classification; existing tests carried
  over (same outward contract, updated for `SnapshotResult`).
