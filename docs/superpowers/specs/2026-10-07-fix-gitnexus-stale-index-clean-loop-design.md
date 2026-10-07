# Run GitNexus with shared stores off: end the stale-index / clean loop in `omc watch`

**Date:** 2026-10-07
**Slug:** `fix-gitnexus-stale-index-clean-loop`
**Status:** design, hardened with `/omc:explain` and the grug lens
**Predecessors:** `2026-09-27-stale-knowledge-snapshot-self-heal-design.md` (the
heal this record keeps), `2026-07-17-omc-gitnexus-design.md` (the repo-local
snapshot model this record keeps), `2026-10-06-fix-watch-docs-grouping-huge-projects-design.md`
(the fork pin bump that introduced the behaviour)

## 1. Problem

`omc watch` on the primary checkout of this repository loops forever:

```
→ refreshing GitNexus index (incremental)
✓ index refreshed
✗ index still stale after analyze (index-behind) — destroying and rebuilding
✗ clean did not remove the index: GitNexus Clean (1.6.12) …
```

Every pass runs a full reindex of about 14 s whose result is thrown away. The
watch tick's quiet token `knowledge-stale:<codes>` already refuses to retry the
same codes every tick, so the log shows two rebuild attempts per upstream
commit (one after each `synced` tick), not one every 30 s; the cost is bounded
but the knowledge never becomes current.

Side damage on the primary, all caused by the loop:

- `clean --force` removed the GitNexus registry entry for the primary, so
  `omc internal gitnexus query|context|impact` answer "Repository not found"
  and `/omc:explain` is blind.
- The docs mirror `.omc/docs/gitnexus/docs` was deleted by the heal's
  `_clear_mirror` and never restored. The wiki pages still exist in
  `.gitnexus/wiki` (24 pages from commit 096c90d).
- `.gitnexus/meta.json` is stuck at 096c90d with a null `runnerIdentity`.

## 2. Root cause (reproduced)

omc commit 01d7fcf (2026-10-06) moved the GitNexus fork pin to 24af4f60
(`docker/Dockerfile.e2e` `GITNEXUS_REF`; the host clone under
`~/.omc/dependencies/gitnexus` follows through `omc update`). That pin carries
upstream GitNexus #3374, "share one index store across linked worktrees and
sibling clones".

**GitNexus side (1.6.12).** A main checkout whose git common dir has linked
worktrees is always routed to a shared store at
`$GITNEXUS_HOME/stores/<basename>-<hash>/checkouts/<slot>` (fork
`src/storage/shared-store.ts`, `resolveIdentity` → `hasLinkedWorktrees`). The
primary has 16 linked worktrees. On that route:

- `analyze` seeds the slot from the legacy local `.gitnexus`, indexes into the
  slot, writes a pointer `.gitnexus/store.json`, and leaves the legacy
  `.gitnexus/meta.json` and `lbug` untouched. `gitnexus wiki` writes to
  `<storagePath>/wiki`, i.e. into the slot.
- `--no-share` is refused for worktree checkouts ("linked worktrees always use
  the shared index store. Set GITNEXUS_SHARED_STORE=off"). The only switch is
  the environment: `isSharedStoreDisabled` is true when `GITNEXUS_SHARED_STORE`
  is one of `off|0|false|no`, or when `GITNEXUS_STORAGE_PATH` or
  `GITNEXUS_STORAGE_ROOT` is set. The registry's `shareOptOut` flag governs
  only opted-in clone sharing (`resolveOptedInStore`), not worktree identity.
- `clean --force` on a shared checkout deletes the slot and the pointer,
  unregisters the repo and reclaims the store, but keeps the legacy local index
  by upstream design (#3352 R13; `clean --local-index --force` is the arm that
  removes it).
- With sharing off, a checkout still registered at a store slot takes the
  `leavingStore` path in `core/run-analyze.ts` (~line 1211): it indexes into
  its own `.gitnexus` again, and `registerLeftStore` re-points the registry at
  the local index and removes the pointer file. The old slot is left behind for
  `clean --gc` (the `leaveSharedStore` variant that also deletes the slot runs
  only for an explicit `--no-share`). A pointer-only `.gitnexus` (store.json,
  .gitignore, run.cjs, lock files) counts as empty storage, so analyze proceeds
  normally there too.

**omc side (`src/omc/gitnexus.py`).** `INDEX_META_NAMES = ("gitnexus.json",
"meta.json")` are read from `<root>/.gitnexus/`, so `snapshot_freshness` sees
the stale legacy commit forever and reports `index-behind`. `_destroy` judges
`clean --force` by `read_index_meta(root) is not None`, and the legacy file
survives, hence "clean did not remove the index". `_destroy_and_rebuild`
judges by `flat_store_branch(root) == base`, again on the legacy directory.
`read_wiki_meta` reads `.gitnexus/wiki/meta.json`; `_run_wiki` mirrors
`.gitnexus/wiki` to `DOCS_MIRROR_REL`; `mirror.py`'s `SNAPSHOT_DIRS =
(".gitnexus", ".omc/docs")` are what `omc internal rebase-main` copies into
worktrees. Nothing in omc sets `GITNEXUS_HOME` or `GITNEXUS_SHARED_STORE`.
`ToolContext` (`src/omc/toolctx.py`) builds every child environment from
`child_env()` (`{**self.env, **self.uv_env}`) plus a per-call `extra_env`, for
`run`, `run_bounded`, `run_supervised` and `stream`. The E2E suite passed
because the Docker fixture repository has no linked worktrees.

**Reproduction.** An isolated clone with one `git worktree add`, a scratch
`GITNEXUS_HOME`, and the primary's stale `meta.json` and `lbug` copied in:
`analyze` exits 0 and prints "Shared store: seeded from the local index at
…/.gitnexus" and "Shared store: published commit graph d4f0d6de5704"; the
local `meta.json` is unchanged at 096c90d, the pointer `store.json` is
written, the registry points at the slot. `clean --force` then prints
"Deleted: …/checkouts/<slot>" and "Shared store: removed 1 commit graph(s)",
empties the registry, and leaves the local `meta.json` in place. The same
clone without a worktree: "Full rebuild required (3 reasons): index schema
changed (built by an unidentified GitNexus build), analysis capabilities
changed, analyzer runner identity changed", then local
`.gitnexus/gitnexus.json` and `meta.json` at d4f0d6d, registered, in 13.5 s.
That second run is exactly the primary's current state once sharing is off,
and it is why the reconcile in §4.2 needs no new repair code.

## 3. Decisions taken during brainstorm

| # | Decision | Why |
|---|----------|-----|
| D1 | Direction A: omc forces GitNexus sharing off; the repo-local `.gitnexus` snapshot model (2026-07-17 record: index at `.gitnexus/` in the primary root, wiki at `.gitnexus/wiki/`, mirrored into worktrees) stays. Direction B, adopting shared stores in omc, is a follow-up and is not designed here | Every omc surface reads the local directory; one environment variable restores the model GitNexus 1.6.11 gave for free. Direction B would touch the mirror, rebase-main, the proxy, dependency indexing and the docs model at once |
| D2 | `GITNEXUS_SHARED_STORE=off` is set in `ToolContext.child_env()` unconditionally, overriding an inherited value. `GITNEXUS_STORAGE_PATH` / `GITNEXUS_STORAGE_ROOT` are left alone and documented as unsupported with omc | User chose "override": omc owns the snapshot model, so a user's inherited setting must not reintroduce the loop. The storage overrides are a different feature nobody uses with omc |
| D3 | The heal reconciles an already damaged primary automatically, with no new repair code | GitNexus's own gates ("index schema changed", "runner identity changed") force the full local rebuild and re-register the repo; reproduced in §2 |
| D4 | When a `.gitnexus/store.json` pointer was present before the heal, omc runs `gitnexus clean --gc --force` after the successful local re-index and narrates the result. Without a pointer omc never touches the stores | User: "so run it". `--gc` only drops member slots whose checkout dir is gone or whose registry entry points elsewhere, skips locked slots, deletes unreferenced commit graphs and removes an empty store (fork `shared-store-lifecycle.ts`, `orphanMembers` / `reclaimSharedStoreLocked`); dependency checkouts keep their slots |
| D5 | After a fresh index verdict, if `.gitnexus/wiki` exists (hardening tightened this to "carries readable metadata", §4.3) and `.omc/docs/gitnexus/docs` is absent, `mirror_dir` restores it and narrates `✓ docs mirror restored`, regardless of the documentation flag. Wiki-behind remains a documentation-mode concern | Today the restore runs only under `documentation=True` in `refresh_knowledge.finish`, so the primary's 24 existing pages stay unmirrored. The 2026-09-27 ruling that documentation off never computes wiki reasons is untouched |
| D6 | Pointer detection is one narration line in the heal, no new `OMC_KNOWLEDGE` reason code; the machine contract is unchanged | The pointer is a transient state of one upgrade, not a freshness fact a consumer should branch on |
| D7 | `_destroy`'s post-condition is unchanged: with sharing off `clean --force` removes the whole local `.gitnexus/`. The 2026-09-27 record gets an amendment note saying its §2 assumption holds with sharing off | The local directory is the path-owned namespace, deletable in every ownership state; the assumption was true for 1.6.11 and is true again |
| D8 | The E2E fixture gains a linked worktree before the index step so GitNexus's shared-store routing is exercised; unit tests reproduce the loop with a stub | The suite passed through the regression because no fixture had a worktree |
| D9 | The GitNexus fork and the Dockerfile pin are untouched; upstream GitNexus is out of scope | The behaviour is upstream's design, not a bug; omc only has to say which model it wants |

## 4. Design

### 4.1 One environment rule

`ToolContext.child_env()` in `src/omc/toolctx.py` becomes

```python
GITNEXUS_ENV = {"GITNEXUS_SHARED_STORE": "off"}

def child_env(self) -> dict[str, str]:
    return {**self.env, **self.uv_env, **GITNEXUS_ENV}
```

The switch is unconditional and wins over an inherited value (D2).
`extra_env` still merges on top in every runner, so the wiki API-key path
(`run.extra_env` in `_run_wiki` and `run_document`) keeps working.

Why one place. Every GitNexus child omc spawns goes through `child_env()`:
`refresh_knowledge`'s `analyze`, `clean` and `wiki` calls (`run` and
`run_supervised`), the `omc internal gitnexus` proxy (`internal.py:_gitnexus`,
`run`), `dependency.py`'s `analyze --index-only` and `wiki`
(`run`/`run_supervised`), `update_gitnexus`'s `--version` probe, and the
headless harness sessions (`session.run_headless` → `ctx.run`). The
`plugin.py` probe builds its own `ToolContext.from_env({**ctx.child_env(), …})`,
so it inherits the rule as well. A GitNexus hook or MCP server started by a
harness session therefore sees sharing off too, which closes the ping-pong
where omc indexes locally and a session's GitNexus routes the same checkout
back into a shared store.

One path does not go through `child_env()`: the interactive session launch.
`start.py`, `implement.py` and `review.py` apply `SessionPlan.env` with
`os.environ.update(plan.env)` and then `exec_interactive` the shell, so the
session inherits the process environment, not `child_env()`. `session.py`
builds `SessionPlan.env` and `run_headless`'s `extra_env` from the same
dict literal (`{**provider.title_env(), "OMC_SLUG": slug, "OMC_PROVIDER": name}`);
only `SessionPlan.env` gains `**GITNEXUS_ENV`, so the interactive session
carries the switch as well. `run_headless` is left alone: it runs through
`ctx.run`, whose `child_env()` already carries the switch, and a second copy
there would be redundant. `session.py` imports the constant from `toolctx`;
there is no second source of truth.

The storage overrides `GITNEXUS_STORAGE_PATH` and `GITNEXUS_STORAGE_ROOT` are
not touched: they also disable sharing, but they move the index out of
`.gitnexus/`, which omc's snapshot model cannot follow. README documents
them as unsupported with omc (§4.5).

### 4.2 Reconcile: no new repair code

With sharing off, `refresh_knowledge` behaves as designed in the 2026-09-27
record and GitNexus itself resolves the two states a damaged primary can be
in. omc adds narration and one cleanup call; no step is judged by exit code.

**State 1, legacy directory only** (the primary today: `.gitnexus/meta.json`
at 096c90d, no pointer, no registry entry, slot already deleted). The verdict
says `index-behind`; the incremental `analyze` runs with sharing off;
GitNexus's `resolveStoragePath` finds no registry entry, no shared identity
and no live pointer target, so it uses the default local path, and its gates
force a full rebuild into `.gitnexus/` and register the repo. The recomputed
verdict is fresh. Narration is today's: `→ refreshing GitNexus index
(incremental)`, `✓ index refreshed`.

**State 2, pointer and live slot** (a primary caught before `clean --force`
ran, or a user who ran 1.6.12 by hand): the verdict says `index-behind`
because the legacy metadata is stale. Before the `analyze`, when
`.gitnexus/store.json` exists, the heal narrates once (D6):

```
· GitNexus shared-store pointer found — re-indexing locally; sharing is off under omc
```

The `analyze` takes GitNexus's `leavingStore` path: it indexes into the local
`.gitnexus/`, re-registers the repo there and removes the pointer. The
recomputed verdict is fresh.

**Store cleanup (D4).** Only when a pointer was present before the heal, and
only once the index verdict is fresh, the heal runs `gitnexus clean --gc
--force` with `cwd=root` through `ctx.run`. The command is a global sweep of
every store under `$GITNEXUS_HOME/stores` (fork `cli/clean.ts`,
`collectSharedStores`), not a per-repository call: it prints a
nothing-to-collect line and exits 0 when no stores exist, keeps every slot
whose checkout is present and registered (omc's dependency checkouts), and
fails loudly only on an unreadable stores root. Its exit code is narrated,
never trusted, and its output excerpt (last 400 characters, the tail where
GitNexus prints its summary; the existing `analyze` and `clean` failure lines
take the first 400) is shown:

```
→ collecting orphaned GitNexus shared stores (clean --gc)
· <GitNexus's own summary line(s)>
```

A non-zero exit narrates `✗ clean --gc failed (exit N): <excerpt>` and changes
nothing else: the verdict is already fresh, the slot is only disk space, and
the next pointer-case heal retries. Without a pointer omc never runs `--gc`,
so a user's deliberately shared dependency stores are never visited by a
plain heal (dependency checkouts are registered and keep their slots in any
case).

`store.json`'s presence is read once at the top of the index branch of
`refresh_knowledge` and carried as a local boolean; the gc call sits after
the final "still stale after rebuild" check, where the index codes are
empty. A pointer beside an already fresh verdict (the index branch never
runs) is left alone: nothing is broken, and the next heal that does run
the branch collects it. Detecting the pointer is a file existence check, not
a freshness reason: `snapshot_freshness`, `Reason`, `Freshness.to_json` and
the `OMC_KNOWLEDGE` line are unchanged. `ANALYZE_ARGS` is unchanged too; the
switch is environment, not argv.

### 4.3 Docs mirror restore (D5)

`refresh_knowledge.finish` restores the mirror today only in the
`documentation=True` branch. The fence existed for a reason worth keeping in
view: a documentation-off run never computes wiki reasons (2026-09-27
ruling), so it must never imply that the docs are current, and the hint
(`· docs mirror cleared — run omc watch --once --enable-documentation to
regenerate`) was the honest substitute for a mirror it could not vouch for.
`tests/unit/test_gitnexus_refresh.py::test_documentation_off_leaves_the_cleared_mirror_to_the_hint`
encodes exactly that ("documentation=False never mirrors").

Restoring pages that already exist does not cross that line: it re-exposes
what `/omc:explain` and `rebase-main` consumed before the mirror was deleted,
claims no freshness, and leaves the verdict untouched. So the restore moves
out of the branch: for any fresh verdict, when `.gitnexus/wiki` carries
readable metadata (`read_wiki_meta(root) is not None`, the same file the
verdict consults) and `DOCS_MIRROR_REL` is absent, `mirror_dir` recreates it
and narrates `✓ docs mirror restored`. Gating on the metadata rather than on
the directory keeps a half-written wiki left by a killed run (stall kill,
Ctrl-C) out of the mirror. The `documentation=False` hint stays for the case
where the mirror actually went away during this run and there is no wiki to
restore from (every destroy-and-rebuild, since `clean --force` removes the
wiki too); when the restore ran, the hint is not printed, because the mirror
is present again. The test above flips to assert the restore; the watch tests
that assert the hint after a destroy-and-rebuild keep passing, because their
stub's `clean` removes `.gitnexus/` whole.

A fresh verdict with `documentation=False` may therefore sit beside restored
pages older than the index. That is the pre-upgrade state of every primary
that runs watch without documentation, and `rebase-main` already mirrors
whatever is there; detecting stale pages is documentation mode's job
(`wiki-behind`), not this record's.

### 4.4 `_destroy` and the watch bound: unchanged

`_destroy` keeps judging `clean --force` by `read_index_meta(root) is None`
(D7). With sharing off the local `.gitnexus/` is GitNexus's path-owned
storage for the checkout and `clean --force` deletes it whole, so the
post-condition is the right one again. The 2026-09-27 record's §2 bullet
"`clean --force` removes the whole `.gitnexus/`" gets a one-line amendment
stating that it holds with `GITNEXUS_SHARED_STORE=off`, which omc sets since
this record.

`watch._tick`'s `knowledge-stale:<codes>` quiet token is unchanged. It bounded
the damage here and it remains the backstop if a future GitNexus introduces
another storage route omc does not know.

### 4.5 Documentation

- This record.
- `docs/superpowers/specs/2026-09-27-stale-knowledge-snapshot-self-heal-design.md`:
  amendment note under §2 (the `clean --force` bullet) pointing here.
- `internal.py:_gitnexus` docstring: its "verify on a gitnexus upgrade"
  sentence gains the shared-store finding (1.6.12 routes worktree checkouts
  to `$GITNEXUS_HOME/stores`; omc sets `GITNEXUS_SHARED_STORE=off` in
  `child_env()`).
- README is generated (`.claude/skills/regenerate-readme/SKILL.md`), never
  hand-edited. The recipe reads `src/omc/toolctx.py` and `src/omc/internal.py`
  for its "How omc is put together" section, so the statement that omc runs
  GitNexus with shared stores off and that `GITNEXUS_STORAGE_PATH` /
  `GITNEXUS_STORAGE_ROOT` are unsupported under omc lives as the comment
  beside `GITNEXUS_ENV` and in the `_gitnexus` docstring; the recipe then
  regenerates README (its existing GitNexus prose sits in the architecture
  section beside the `.gitnexus/` snapshot explanation). Because the whole
  file is regenerated, the run also absorbs any README drift that predates
  this record (the task-model menu and `OMC_MODELS` prose from the previous
  feature).

### 4.6 Files touched

`src/omc/toolctx.py` (constant, `child_env`, comment the README recipe
reads), `src/omc/session.py` (the shared env literal and the
`SessionPlan.env` field comment), `src/omc/gitnexus.py` (`refresh_knowledge`:
pointer boolean, narration, gc call, `finish` restore), `src/omc/internal.py`
(`_gitnexus` docstring only); `tests/unit/test_toolctx.py`,
`tests/unit/test_session.py`, `tests/unit/test_taskmodels.py`,
`tests/unit/test_gitnexus_refresh.py`, `tests/unit/test_watch.py`,
`tests/unit/test_cli.py` (README contract guard);
`tests/e2e/test_e2e_gitnexus.py`; this record and the 2026-09-27 record;
`README.md` through the recipe. No fork file, no Dockerfile.

## 5. Error handling and edge cases

- **`analyze` fails in state 2 before re-registering.** The verdict is still
  `index-behind`; the heal escalates to `_destroy_and_rebuild` as today.
  `clean --force` then resolves the checkout's storage through the registry,
  which still names the slot, so it deletes the slot and unregisters but
  leaves the legacy directory; `_destroy` reports `✗ clean did not remove the
  index` and returns the verdict. The tick's quiet token holds the failure to
  one attempt per upstream commit, and the next heal starts from state 1,
  which rebuilds. No new code; the record names the path so the plan's tests
  cover it.
- **`clean --gc --force` hits a locked slot.** GitNexus skips locked slots and
  reports them; the heal narrates the excerpt and the verdict is unaffected.
- **A user's own `GITNEXUS_SHARED_STORE=on`** is overridden (D2) and the
  README says so. A user who wants shared stores with omc is asking for
  Direction B.
- **`GITNEXUS_STORAGE_PATH` / `GITNEXUS_STORAGE_ROOT` inherited.** GitNexus
  indexes outside `.gitnexus/`; omc's verdict reports `index-missing` and the
  heal cannot converge. Documented as unsupported; not detected, by D2.
- **Mirror restore on a shared `.omc` layout.** `mirror_dir` already returns
  `False` when source and destination resolve to the same directory and
  refuses nested paths; the restore reuses it unchanged.
- **Wiki written into the slot before this fix.** Pages generated by 1.6.12
  live in `<slot>/wiki`, not `.gitnexus/wiki`; `clean --gc` removes them with
  the slot. The local `.gitnexus/wiki` is whatever predates the upgrade (24
  pages on the primary) and is what D5 restores. With documentation on, the
  normal `wiki-behind` ladder regenerates.

## 6. Testing

Unit (all under `just check`, node stub, no network):

- `tests/unit/test_toolctx.py`: `child_env()` carries
  `GITNEXUS_SHARED_STORE=off`; an inherited `GITNEXUS_SHARED_STORE=on` is
  overridden; the switch reaches `run`, `run_bounded`, `run_supervised` and
  `stream` children (recorded by an `env`-printing stub); `extra_env` still
  wins over `child_env()` for its own keys.
- `tests/unit/test_session.py` and `tests/unit/test_taskmodels.py`:
  `SessionPlan.env` carries the switch; `run_headless` relies on
  `child_env()`, so its `extra_env` assertions stay on title/slug/provider.
  The two exact-dict assertions `plan.env == {"OMC_SLUG": "s",
  "OMC_PROVIDER": "codex"}` (`test_session.py:43`, `test_taskmodels.py:299`)
  gain the key.
- `tests/unit/test_gitnexus_refresh.py`: a second node stub built on
  `_ctx_with_healing_node_stub` (`tests/unit/test_watch.py`; a `#!/bin/sh`
  script, so a `case "$GITNEXUS_SHARED_STORE"` selects the behaviour, with
  the builtins and absolute paths the existing stub already uses) that
  behaves like shared-store GitNexus when the switch is absent (analyze
  writes the stamped meta under a scratch store and a `store.json` pointer,
  leaves the legacy `meta.json` alone; `clean --force` removes only the
  pointer and the store) and like local GitNexus when the switch is present
  (analyze writes `.gitnexus/meta.json`, removes the pointer; `clean --force`
  removes `.gitnexus/`; `clean --gc --force` records the call and removes the
  scratch store). Tests, asserting on the `say` lines `_run` collects and on
  disk: the heal ends fresh; every recorded GitNexus call carries the switch;
  the pointer narration appears exactly in the pointer case; `clean --gc
  --force` is invoked only in the pointer case and only after the fresh
  verdict; a failing `--gc` narrates and leaves the verdict fresh; the mirror
  is restored with `documentation=False` when the wiki has metadata and the
  mirror is absent, and not restored when the wiki lacks `meta.json`;
  `test_documentation_off_leaves_the_cleared_mirror_to_the_hint` flips to the
  new contract (§4.3) and is renamed
  `test_documentation_off_restores_the_cleared_mirror`.
- `tests/unit/test_watch.py`: a tick against the shared-store stub without
  the switch still ends in the `knowledge-stale:index-behind` quiet token (the
  reproduction of the loop and the proof the bound holds).

E2E (`tests/e2e/test_e2e_gitnexus.py::test_index_then_explain_on_real_repo`):
one `git -C /repo worktree add /tmp/wt-e2e -b e2e-wt` before the `/omc:index`
step, so GitNexus's worktree routing is live in the container (`/repo` is a
real repository with `refs/remotes/origin/main` and a global git identity,
`docker/Dockerfile.e2e`). New assertions through `run_in`: the local
metadata (`/repo/.gitnexus/gitnexus.json`, else `meta.json`) records the
container repo's HEAD; `/repo/.gitnexus/store.json` does not exist;
`${GITNEXUS_HOME:-$HOME/.gitnexus}/stores` does not exist (GitNexus's home
is `GITNEXUS_HOME` or `~/.gitnexus`, fork `storage/global-dir.ts`; the image
sets neither, and the container runs as root). The existing registry
assertion (`Path: /repo`) stays. One git command is added; the suite keeps
its five-minute ceiling.

Red first: on the current code the new `test_gitnexus_refresh` tests show the
`index-behind` → "clean did not remove the index" sequence, and the E2E
`store.json` assertion fails against the fixture with a worktree.

Plan note: each task in the implementation plan carries a `Complexity:` line
(`simple | medium | high`); the environment rule and the docs are `simple`,
the stub and the heal change are `medium`.

## 7. Out of scope

- **Direction B**, adopting shared stores in omc. It would retire the 124 MB
  `.gitnexus` copy per worktree and touch `mirror.py`'s `SNAPSHOT_DIRS`,
  `rebase-main`, the proxy's `--repo` pinning, `document`, and dependency
  indexing (`dependency.py` registers each dependency checkout separately).
  A follow-up record.
- Upstream GitNexus and any change to the fork or the Dockerfile pin (D9).
- Detecting `GITNEXUS_STORAGE_PATH` / `GITNEXUS_STORAGE_ROOT` (D2).
- A new `OMC_KNOWLEDGE` reason for the pointer (D6).
- Removing legacy local indexes GitNexus left behind on checkouts omc does
  not manage (`clean --local-index`).

## 8. Hardening notes

The primary's GitNexus graph was unavailable during hardening (the bug this
record fixes unregistered the repository), so every explain pass was grounded
in the code under `src/omc/`, the records under `docs/superpowers/specs/`, and
the fork source at the pinned commit 24af4f60.

**§4.1 (explain + grug).** Explain confirmed `child_env()` is the single env
source for `run`, `run_bounded`, `run_supervised` and `stream`, each merging
`extra_env` after it, and that `plugin.py`'s probe and `gitnexus._build`'s
native-build env ride on it. It found the one bypass: the interactive session
is launched by `os.environ.update(plan.env)` plus `os.execvp`
(`shells/base.py`), so the design now adds the constant to `SessionPlan.env`
in `session.py`; `run_headless` already goes through `ctx.run` and
`child_env()`, so it does not get a copy. It named the two exact-dict test
assertions that must change. Grug found nothing: the session site has a
stated need and the constant has two consumers at birth (`child_env()` and
`SessionPlan.env`).

**§4.2 (explain + grug).** Explain confirmed the insertion points inside
`refresh_knowledge`, that `omc internal gitnexus refresh` inherits them, that
the `clean --force` substring assertions in existing tests cannot match
`clean --gc --force`, and that `clean --gc` is a global sweep of
`$GITNEXUS_HOME/stores` (now stated in the section). From the fork it
established that `clean --force` deletes an unregistered legacy `.gitnexus/`
by path ownership (`repo-manager.ts:loadRepo`), which is what makes D7 hold
on today's primary, and that the leavingStore path calls `registerLeftStore`
(pointer removed, slot left) when sharing is turned off by environment, so
the gc has a real orphan to collect. Grug raised one Important `say-no`
finding on the gc call; waived by decision D4 (see Deliberate complexity).

**§4.3 and §4.4 (explain + grug).** Explain confirmed `finish` is the single
exit for every return, that `mirror_dir` syncs in place, and that one
existing test encodes the contract D5 reverses. It pointed out that today's
restore keys on `is_dir()` and narrates regardless of `mirror_dir`'s return;
the section now gates on `read_wiki_meta`. Grug raised one Important `fence`
finding: the section removed the documentation-off fence without stating why
it stood. Fixed by writing the fence's purpose and why a restore of existing
pages respects it.

**§4.5 and §6 (explain + grug).** Explain confirmed the node stub is a
`#!/bin/sh` script that can switch on the variable, that the E2E `/repo` is
a real repository with an origin ref and a git identity, that GitNexus's home
is `GITNEXUS_HOME` or `~/.gitnexus`, and that the README recipe reads
`toolctx.py` and `internal.py`. Grug found nothing: the project's testing
policy sets the test level and the plan follows it.

**Whole record (explain + grug).** Explain found two coherence gaps: the D5
row said "exists" while §4.3 gates on metadata (parenthetical added), and
`session.py` was edited by §4.1 but named nowhere as touched (§4.6 added).
It found no conflict with the 2026-09-27 rulings, the 2026-07-17 snapshot
model, or the `OMC_KNOWLEDGE` contract. Grug: zero cross-section findings
beyond the recorded D4 waiver.

## Implementation review

**2026-10-07 — auditor: Claude (Fable 5.1), `/omc:audit` on
`feature/fix-gitnexus-stale-index-clean-loop` against `origin/main`.**
Review worker on the configured `review` model walked the record, the plan
and the branch diff. Every decision D1–D9, every §4 promise, every §5 edge
case and every §6 test is delivered; no excluded §7 work was added. Unit
gate at review time: 195 focused tests passed. No Important findings.

Minor findings and dispositions:

- Minor, code deviates from the record, §4.2, `src/omc/gitnexus.py:493`:
  the `clean --gc` success excerpt was narrated without the `· ` prefix the
  record shows. Fixed in code (prefix added) and in
  `tests/unit/test_gitnexus_refresh.py::test_pointer_converges_and_gc_is_best_effort`.
- Minor, record is stale, §4.2: the excerpt is the last 400 characters,
  while the existing `analyze`/`clean` failure lines take the first 400.
  Record amended.
- Minor, record is stale, §4.6: `tests/unit/test_cli.py` (the README
  contract guard) was missing from the files-touched list. Record amended.
- Minor, record is stale, §6, `tests/unit/test_gitnexus_refresh.py:169`:
  the flipped test was also renamed to
  `test_documentation_off_restores_the_cleared_mirror`. Record amended.
- Minor, record is stale, §4.5, `README.md`: regenerating the README also
  absorbed pre-existing task-model drift. Record amended.

Outside the record: commit `d99c7db` (`tests/e2e/test_e2e_explain_sources.py`,
`tests/unit/test_e2e_explain_sources.py`) is the `/omc:verify` stage-gate
repair made during `/omc:implement`; test infrastructure only, documented in
the plan's implementation record, no amendment needed.

`/omc:finish` review stage, same date: the project review passed (no
Critical or Important findings). The grug lens raised one Important
`grug:locality` finding at `src/omc/session.py` (`run_headless` copied
`GITNEXUS_ENV` into an `extra_env` that `child_env()` already supplies);
fixed behaviour-preservingly by dropping the copy, relaxing the four exact
`extra_env` assertions, and amending §4.1, §6 and §8 to match.

## Deliberate complexity

- §4.2 `gitnexus clean --gc --force` after a pointer-case heal (`grug:say-no`):
  a global sweep of `$GITNEXUS_HOME/stores`, one narration branch and one
  failure path, to reclaim the disk space of one orphan slot when the index
  is already fresh; a hint in the pointer line would let the user run it
  once. Waived by brainstorm decision D4, where the user chose to run it.
