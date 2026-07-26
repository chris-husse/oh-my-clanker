# Install GitNexus from the Python CLI (+ marketplace self-heal)

Date: 2026-07-26
Slug: install-gitnexus-in-omc-update
Status: approved design, ready for planning

## Problem

`omc update` skips GitNexus with `GitNexus not installed — /omc:index installs
it on first use; skipping.` and defers first install to the `gitnexus-ensure`
Claude skill. The Python CLI already knows how to *build and update* the
managed clone (`gitnexus.update_gitnexus`, `src/omc/gitnexus.py:72`) — it just
never *creates* it. As a result:

- `omc update` never installs GitNexus; a fresh machine stays without it until
  a session happens to run `/omc:index`.
- `omc watch` hard-errors when the CLI is missing (`src/omc/watch.py:399`),
  refusing to start and telling the user to run `/omc:index` first.
- `omc start` never checks GitNexus at all (`src/omc/probe.py` probes only
  git/wt/provider).

The same `omc update` run also prints:

```
✗ claude: claude plugin marketplace update oh-my-clanker failed: Marketplace
'oh-my-clanker' not found. Available marketplaces: claude-plugins-official,
superpowers-marketplace — continuing
```

`oh-my-clanker` *is* the correct marketplace name
(`.claude-plugin/marketplace.json:2`, `docker/PLUGIN-NOTES.md:16`). It fails
because `omc update`'s claude plugin-update sequence
(`src/omc/providers/claude.py:108`) runs `marketplace update` + `plugin update`
with **no preceding `marketplace add`**, so when the `oh-my-clanker`
marketplace isn't registered on the machine the update errors.
`ensure_plugin` on the `omc start` path already self-heals this by running
`marketplace add <source>` first (`src/omc/plugin.py:62`); update does not.

## Goals

- GitNexus installation is a **Python-owned critical prerequisite** of
  `omc update`, `omc watch`, and `omc start` — no longer delegated to a Claude
  skill on first use.
- The three commands share one uniform prerequisite gate: probe git / wt /
  provider CLI, then ensure GitNexus is installed.
- `omc update`'s claude plugin-update path self-heals a missing marketplace
  registration instead of erroring.
- The `gitnexus-*` skills keep working for a standalone in-session `/omc:index`
  (a session not launched by `omc start`).

## Non-goals

- No change to *how* GitNexus is built (the two-step npm build is unchanged) or
  where it lives (`~/.omc/dependencies/gitnexus`).
- No change to the approved-source guarantee (clone only from
  `https://github.com/chris-husse/GitNexus.git`; refuse any other origin).
- No change to the incremental-analyze / wiki flows in `omc watch`.
- No new daemon / background install — install runs inline, foreground.

## Decisions (resolved with the user)

1. **Marketplace error is in scope** — fixed in this branch.
2. **GitNexus install failure is fatal** for `omc start` and `omc watch` — it
   aborts the command exactly like a missing `git`/`wt`, honoring "critical
   prerequisite".
3. **Silent on the healthy path** — when GitNexus is already installed, the
   ensure step prints nothing (start/watch call it every run); it narrates only
   on an actual install/build.
4. **`omc update` also runs `require_tools`** — the full git/wt/provider probe,
   for uniformity across all three commands.

## Design overview

Two independent changes ship together on this branch:

**A. Python owns GitNexus install.** Factor an idempotent
`ensure_gitnexus(ctx)` that installs-if-missing / heals-if-broken and is a
fast, silent no-op when the CLI is already healthy. Reuse the existing build
sequence. Wire the uniform prerequisite gate (`require_tools` +
`ensure_gitnexus`) into `start`, `watch`, and `update`. Expose
`omc internal gitnexus ensure` so the `gitnexus-ensure` skill becomes a thin
wrapper over the Python logic (single source of truth; standalone `/omc:index`
still works).

**B. Marketplace self-heal.** `omc update`'s claude plugin-update path runs
`marketplace add <source>` (best-effort) before `marketplace update` /
`plugin update`, mirroring `ensure_plugin`.

## Component changes

### 1. `src/omc/gitnexus.py` — `ensure_gitnexus()` + shared helpers

Factor the existing `update_gitnexus` body into two private helpers:

- `_clone_if_missing(ctx, approved_origin)` — if `<root>/.git` is absent,
  `git clone <approved_origin> <root>`; if present, verify
  `remote get-url origin == approved_origin`, reusing today's redacted-origin
  refusal (`src/omc/gitnexus.py:86-95`) for a wrong origin. Returns 0 or an
  error code.
- `_build(ctx)` — the existing three-command two-step build (`npm install` in
  `gitnexus-shared/`, then `npm ci` + `npm run build` in `gitnexus/`),
  unchanged, including the `_run_tool` missing-binary handling.

Add:

- `ensure_gitnexus(ctx) -> int` — **heal/install, no forced update.** If
  `_cli_version(ctx)` reports healthy → return 0 **silently** (the common path
  start/watch hit every run). Else: `_clone_if_missing` → `_build` → verify
  `--version`; on success print one line (`✓ GitNexus installed (<ver>)`), on
  failure surface the build output and return 1. It does **not** fetch/ff main
  — that is `update`'s job.

Change:

- `update_gitnexus(ctx)` — replace the "not installed → print skip → return 0"
  early return (`src/omc/gitnexus.py:80-85`) with `_clone_if_missing`; then keep
  today's fetch → ff-to-main → `_build` → verify. A missing clone becomes a
  first install rather than a skip.

### 2. `src/omc/internal.py` — `ensure` verb

Add `ensure` to the gitnexus proxy: `omc internal gitnexus ensure` calls
`ensure_gitnexus(ctx)` and returns its code. This verb is the seam the skill
wrapper (and any other caller) uses. Because ensure *installs*, it must be
intercepted at the **top** of `_gitnexus` — ahead of the `--git` parse, the
`_GITNEXUS_VERBS` membership check (`src/omc/internal.py:107`), and the
"CLI present" guard (`src/omc/internal.py:110`) — and takes no `--git`/`--repo`/
`--branch` scoping (it is repo-independent). It stays distinct from the query
verbs (`query|context|impact|cypher`), which keep the CLI-present guard —
those *read* the graph and must not trigger an install. Add `ensure` to
`_USAGE` so the proxy's help lists it.

### 3. `src/omc/watch.py` — prerequisite gate replaces the hard error

In `run_watch`, replace the "GitNexus is not installed → error, return 1" block
(`src/omc/watch.py:399-405`) with the uniform gate, placed **before**
`ensure_wt_config` and the watch-mutex acquisition (`src/omc/watch.py:406-407`)
so a failed prerequisite never leaves a lock behind:

1. `require_tools(ctx, cfg)` — probes git, wt, provider CLI in parallel; raises
   `OmcError` on any miss. `cfg` is already resolved in `run_watch`. The
   `OmcError` propagates through `_dispatch` to `main()`'s handler
   (`src/omc/cli/__init__.py:133`).
2. `rc = ensure_gitnexus(ctx); if rc: return rc`.

Note: `require_tools` probes `wt`, which `omc watch` itself never uses (it runs
in the primary checkout and never creates worktrees). This is a deliberate
choice per decision 4 (uniform gate), not an oversight.

### 4. `src/omc/start.py` — ensure as a boot prerequisite

In `run_start`, after `require_tools(ctx, cfg)` (`src/omc/start.py:72`) and
before slug generation, call `rc = ensure_gitnexus(ctx)`; a non-zero result
aborts start the same way a missing tool does (fatal, decision 2). Skipped on
the `--dry-run` path — dry-run makes no changes.

### 5. `src/omc/installer.py` — `require_tools` in `run_update`

In `run_update`, after the `uv tool upgrade omc` step succeeds and before the
managed-dependency step, run `require_tools(ctx, cfg)`. `run_update` currently
loads the config only after the gitnexus step (`src/omc/installer.py:68`);
reorder so the config load happens up front. Then:

- **Config present** → `require_tools(ctx, cfg)` (git/wt/provider probe; raises
  `OmcError` on a miss, aborting update) → `update_gitnexus` → plugin loop.
- **No config** (fresh install, never `omc configure`) → **skip
  `require_tools` entirely** (there is no configured provider to probe), keep
  today's `· no config — skipping plugin updates` line, but still run
  `update_gitnexus`. An unconfigured machine still gets its GitNexus + uv
  update.

`update_gitnexus` (now install-or-update) runs regardless of config.

Config source: keep `store.load_global(ctx.home)` (not `resolve.load_effective`)
— `omc update` may run outside any repo, where project/effective config is
absent, and the provider choice lives in the global config anyway.
`store.load_global` returns a `GlobalConfig`, while `require_tools`'s annotation
is `Config`; both expose `llm.default`, which is all `require_tools` reads, so
the plan should widen the annotation (or accept the shared `llm`-bearing shape)
rather than construct a throwaway `Config`.

### 6. Marketplace self-heal — `src/omc/providers/claude.py` + `installer.py`

Make `omc update`'s claude plugin path self-heal the marketplace registration,
mirroring `ensure_plugin` (`src/omc/plugin.py:58-63`):

- Change the provider contract `plugin_update_argvs(self)` →
  `plugin_update_argvs(self, marketplace_source: str | None = None)`
  (`src/omc/providers/base.py:95`). Claude prepends
  `["claude", "plugin", "marketplace", "add", marketplace_source]` to today's
  two argvs; codex/opencode ignore the parameter (unchanged behavior).
- In `run_update`, compute `source = marketplace_source(ctx.env)`
  (already in `src/omc/plugin.py:25`) and pass it to `plugin_update_argvs`.
- The `marketplace add` step is **best-effort**: a re-add of an
  already-registered marketplace returns non-zero but is benign, and must not
  abort the sequence or print `✗`. Adjust `run_update`'s per-provider loop
  (`src/omc/installer.py:81-95`) so the marketplace add/update steps are
  best-effort and the final `plugin update` is the pass/fail signal — the same
  best-effort-add / real-signal split `ensure_plugin` already uses. The loop
  stays overall best-effort (a plugin failure logs `✗ … — continuing` and never
  fails the command; the command's exit code remains `dep_rc` from GitNexus).

### 7. `skills/gitnexus-ensure/SKILL.md` — thin wrapper

Replace the clone + two-step-build prose (Steps 2–4) with: run
`omc internal gitnexus ensure` and report what it prints. Keep the header
naming the CLI path and the approved-source contract as documentation, but the
imperative logic now lives in Python. The `gitnexus-index` / `gitnexus-explain`
/ `gitnexus-document` skills keep their unchanged "Step 1 — run gitnexus-ensure"
line; that path now bottoms out in `ensure_gitnexus`.

### 8. Stale hints

Update the messages that tell users to run `/omc:index` to install GitNexus,
now that `update`/`start`/`watch` install it:

- `src/omc/internal.py:112` (gitnexus query proxy, CLI-missing branch).
- `src/omc/dependency.py:301` (`run_ensure`, CLI-missing branch).
- The removed `src/omc/watch.py:401` hard-error text.

New wording points at `omc update` (or start/watch) as the installer.

## Data flow

```
omc start  → run_start  → require_tools → ensure_gitnexus → slug → worktree
omc watch  → run_watch  → require_tools → ensure_gitnexus → (mutex) → loop
omc update → run_update → uv upgrade → require_tools → update_gitnexus
                          (_clone_if_missing → force main → _build → verify)
                        → plugin self-heal (marketplace add → update → plugin update)
/omc:index → gitnexus-index skill → gitnexus-ensure skill
           → `omc internal gitnexus ensure` → ensure_gitnexus
```

`ensure_gitnexus` short-circuits on a healthy CLI (`node --version` succeeds),
so start/watch pay only a ~100 ms version probe once healthy; the clone+build
cost is paid exactly once, on first run, and produces no output when already
installed.

## Error handling

- **Approved-source guarantee preserved.** `_clone_if_missing` refuses any
  existing clone whose origin isn't the approved GitNexus URL, reusing the
  current redacted-origin refusal — never re-points, never builds an
  unapproved tree.
- **Missing `node`/`npm`.** Already handled by `_run_tool` returning `None` →
  "npm not found on PATH", code 1. For start/watch this aborts as a failed
  prerequisite (fatal).
- **Broken build.** Post-build `--version` must succeed; otherwise surface the
  build output and return 1 — never claim success (today's invariant, kept).
- **Offline + not installed.** Clone fails → prerequisite fails (fatal),
  consistent with treating GitNexus as critical.
- **Marketplace add benign failure.** Re-adding a registered marketplace is
  tolerated; only a failed `plugin update` produces the `✗ … — continuing`
  line, and even that never fails the `omc update` exit code.

## Testing

- **`tests/unit/test_gitnexus_update.py`** (extended, network-free via
  monkeypatched `ctx.run` / `_run_tool`, mirroring the file's existing style):
  - `ensure_gitnexus` no-ops **silently** when `_cli_version` is healthy.
  - `ensure_gitnexus` clones + builds + verifies when the root is absent.
  - `ensure_gitnexus` refuses a wrong-origin existing clone.
  - `ensure_gitnexus` returns 1 when the post-build `--version` is broken.
  - `update_gitnexus` clones-then-builds when the root is absent (was: skip).
- **`tests/unit/test_installer.py`**:
  - `run_update` runs `require_tools` and aborts on a missing tool.
  - `run_update` still delegates to `gitnexus.update_gitnexus`
    (module-attribute monkeypatch), now expecting install-on-missing.
  - `run_update` prepends `marketplace add <source>` for claude and does not
    print `✗` on a benign re-add; a `plugin update` failure logs `✗ …
    continuing` without failing the command.
- **New start/watch coverage**:
  - `run_start` invokes `ensure_gitnexus` and aborts on its failure; `--dry-run`
    does not.
  - `run_watch` invokes `require_tools` then `ensure_gitnexus` instead of the
    old hard error, and does both **before** acquiring the watch mutex — a test
    asserts no lock is left when a prerequisite fails.
- **Provider unit tests**: `plugin_update_argvs(source)` for claude includes the
  `marketplace add`; codex/opencode ignore the argument (unchanged output).
- **Docker E2E unchanged**: `docker/Dockerfile.e2e:58-70` already prebakes the
  clone+build, so `ensure_gitnexus` finds it healthy and no-ops — no image
  change.

## Out of scope

- Reworking the cross-marketplace superpowers dependency documented in
  `docker/PLUGIN-NOTES.md` — untouched.
- Any change to codex/opencode plugin install/update behavior beyond accepting
  (and ignoring) the new `marketplace_source` argument.
