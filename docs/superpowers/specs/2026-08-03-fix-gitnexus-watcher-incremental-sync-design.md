# Fix: GitNexus watcher incremental sync — heal the flat-store inversion

**Date:** 2026-08-03
**Status:** approved (brainstorm converged; scope trimmed by user to the watch-side heal)
**Slug:** fix-gitnexus-watcher-incremental-sync

## Problem

`omc start` sessions report the GitNexus knowledge graph as out of sync even
though `omc watch` runs and refreshes on every new commit, and the generated
docs are stale (they cite files deleted weeks ago, e.g. `src/omc/cli.py`).

Root cause — **flat-store inversion**. GitNexus's *default store*
(`.gitnexus/meta.json` + `.gitnexus/lbug`) permanently belongs to whichever
branch a repo was **first** indexed on. This repo was first indexed on
2026-07-17 while the primary checkout was on `feature/omc-v1` (since merged
and deleted), so:

- The default store is frozen at that day's commit (`1fd58d6`, 87 files) with
  `branch: "feature/omc-v1"` stamped in its meta. It can never advance:
  GitNexus's `resolveBranchPlacement` routes every analyze on a non-owner
  branch into a side store, and only default-store analyzes update the flat
  meta and the registry's top-level `lastCommit`.
- Every analyze on `main` — including the watcher's incremental refreshes —
  lands in `.gitnexus/branches/main-0d6e4079/`. That store IS current
  (verified at main HEAD `520e77c`, 211 files). The watcher works.
- But the readers don't read it: the MCP server resolves the default store
  unless a query explicitly passes `branch` (none do), its staleness hint
  compares the frozen commit to HEAD ("⚠️ Index is N commits behind HEAD"),
  and the `wiki` command has **no `--branch` at all** — it always reads the
  default store's graph while stamping `fromCommit` from git HEAD. Doc
  regeneration therefore rewrites 2026-07-17 content and marks it current.
- omc's own query proxy (`src/omc/internal.py:_gitnexus`) pins
  `--repo`/`--branch` and is unaffected — which is why `/omc:explain` answers
  correctly while sessions' MCP tools and the wiki lie.

Writers key on branch name; default readers key on "first indexed". With the
first-indexed branch dead, they disagree forever.

GitNexus's own remediation hint ("run `gitnexus clean --branch <owner>`")
does not work: `clean --branch` refuses to touch anything outside
`.gitnexus/branches/` (verified in `clean.ts`), and the flat owner has no
branch-store entry. The only working primitive is a full `gitnexus clean
--force` (removes the whole `.gitnexus/`, unregisters the repo) followed by a
fresh full analyze on the base branch, which *adopts* the now-empty default
store and stamps it with the base branch.

## Design

`omc watch` ensures the GitNexus default store belongs to the configured base
branch; when it does not, it destroys the index — including the generated
docs — and regenerates.

### Detection (`src/omc/gitnexus.py`)

- `flat_store_branch(root: Path) -> str | None` — read
  `<root>/.gitnexus/meta.json` and return its `branch` value; `None` when the
  file is absent, unparseable, or the field is missing/empty.
- `store_inverted(root: Path, base: str) -> bool` — true iff
  `flat_store_branch` returns a non-empty value different from `base`.

A missing store or an empty/legacy stamp is **not** inversion: GitNexus's
adoption rule lets the next analyze on the base branch claim the default
store in both cases, so the incremental flow already works there. Detection
is one small JSON read per action tick — no subprocess.

### Heal (`src/omc/gitnexus.py`, driven from `src/omc/watch.py`)

`_refresh_index` checks `store_inverted` **first**, before the incremental
analyze. Inverted → heal instead of increment. This single placement covers
`--once` boot, every action tick, and any future recurrence (e.g. a fresh
clone first-indexed on a feature branch). Steps:

1. Narrate: `✗ GitNexus index is owned by '<owner>', not '<base>' —
   destroying and rebuilding`.
2. `gitnexus clean --force` in the primary root — removes the whole
   `.gitnexus/` (frozen default store, the shadow `branches/<base>-*` store,
   the corrupt wiki) and unregisters the repo. Removing the shadow branch
   store matters on its own: gitnexus 1.6.x resolves `--branch <base>` to a
   branch store when one exists, so leaving it would re-invert the bug for
   proxy callers after the default store is reclaimed.
3. Full `analyze --skip-agents-md --skip-skills` — running on `<base>`, this
   claims the default store and stamps `branch: <base>`. Zero LLM cost. From
   then on the watcher's incremental updates land where the MCP server,
   staleness checks, and `wiki` all read, and the registry's top-level
   `lastCommit` advances on every run.
4. Docs: **always** delete the stale mirror `.omc/docs/gitnexus/docs` (stale
   docs are worse than absent docs — they cite deleted files as current; the
   path is gitignored, so deletion cannot dirty the tree and block the
   watcher's own sync). With `--enable-documentation`, regenerate the wiki
   (full by construction after a clean — reading the fresh default-store
   graph) and re-mirror via `mirror_dir`, exactly as the incremental path
   does today. Without it, narrate: `· docs mirror cleared — run omc watch
   --once --enable-documentation to regenerate`.
5. **Verify post-conditions, don't trust exit codes**: `clean --force` exits
   0 even when deletion fails (it logs and returns). After clean, the flat
   `meta.json` must be gone; after analyze, the flat meta must exist and be
   stamped `<base>`. A failed post-condition warns with the captured output
   and skips — never claims success, never crashes the loop (watch doctrine:
   warn-and-skip applies to every tick action).

The heal runs only in the primary root (watch already refuses to run
anywhere else), and the only destructive operations are GitNexus's own
`clean --force` (guarded by its `assertSafeStoragePath`) and the removal of
the gitignored `.omc/docs/gitnexus/docs` mirror. The user's checkout is
never touched.

### Hardening notes (from the spec explain passes)

- `_refresh_index` has exactly three call sites, all inside `_tick`, and
  `_tick` returns `off-branch` before reaching any of them — so the heal's
  analyze can only ever run with the base branch checked out, which is
  precisely the condition for claiming the default store. No extra branch
  check is needed in the heal.
- The watch busy lock wraps the whole tick including the heal, and
  `omc start` verifies the lock is free before cutting a worktree — a long
  heal cannot be snapshotted mid-flight by `start`. Two readers do NOT take
  the lock and never have: the session-side `/omc:index` skill and
  `omc internal rebase-main`'s `mirror_snapshot`. Both are the same race
  class that exists today with incremental analyze; `mirror_snapshot` skips
  an absent `.gitnexus` (mid-clean) harmlessly. Accepted, unchanged.
- `ANALYZE_ARGS` is consumed only by watch (dependency indexing in
  `src/omc/dependency.py` builds its own argv against separate per-commit
  checkouts that are always first-indexed on the pin branch — inversion
  cannot arise there, no change needed).
- Nothing else in omc reads `.gitnexus/meta.json`; the new helpers become
  its only in-repo readers.
- Directory deletion follows `src/omc/mirror.py` conventions (it already
  owns rmtree semantics for snapshot dirs); the docs-mirror removal lives
  beside `mirror_dir` as a small guarded helper rather than an inline
  `shutil.rmtree` in watch code.

### What this deliberately does NOT do (scope trimmed in brainstorm)

- No start-side warning or heal — `omc start` stays fast; the watcher is the
  maintenance loop.
- No MCP server entry rewriting in `~/.claude.json`.
- No skill↔CLI version handshake text.
- No upstream GitNexus changes (its wrong remediation hint, `wiki`'s missing
  `--branch`, registry top-level staleness, `moduleFiles` cleanup of deleted
  files, silent not-stale on gc'd commits) — post-heal, none of them bite
  this repo.

One-time manual steps go in the MR description's runbook, not in code:
`omc update` (brings the host CLI current; fixes the `omc internal gitnexus
ensure` exit-2 the plugin skills currently hit), then `omc watch --once
--enable-documentation` in the primary (fires the heal), and optionally
re-point the user's `gitnexus` MCP server entry from the chicken-era copy
(`~/.chicken/...`, 1.6.7) to omc's managed CLI (`~/.omc/...`, 1.6.8) — after
the heal both read the same, correct store, so that step is version hygiene,
not correctness.

### Decided (not open)

Heal without `--enable-documentation` deletes the stale docs mirror and
defers regeneration to the next documentation-enabled run, rather than
keeping known-wrong docs.

## Testing

Unit (`tests/unit/`):

- `flat_store_branch` / `store_inverted` over meta fixtures: absent
  `.gitnexus/`, unparseable JSON, missing/empty `branch`, owner == base,
  owner ≠ base.
- Heal orchestration with a recording fake `ctx.run`: exact argv sequence
  (clean → analyze → optional wiki), docs-mirror deletion in both
  documentation modes, and each step's failure path (bad exit, failed
  post-condition) warning and skipping without raising.
- `_refresh_index` branching: inverted → heal path; healthy → today's
  incremental path unchanged.

E2E (`tests/e2e/`, Docker harness, extends `test_e2e_watch.py` /
`test_e2e_gitnexus.py` patterns):

- Arrange a repo whose first index ran on a feature branch (run the real
  `gitnexus analyze` inside the container while a feature branch is checked
  out — `harness.make_work_repo` + `run_in` already support this); delete
  the branch; on the base branch run `omc watch --once` → assert the flat
  `meta.json` is stamped with the base branch at HEAD, no
  `branches/<base>-*` shadow store exists, and the registry's top-level
  `lastCommit` advanced. Runs without `--enable-documentation` (the watch
  e2e suite is deliberately token-free), so it also asserts the stale docs
  mirror was deleted and the "docs mirror cleared" line narrated.
