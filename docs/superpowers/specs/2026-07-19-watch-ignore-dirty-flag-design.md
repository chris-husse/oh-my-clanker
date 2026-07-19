# `omc watch --rebase` — sync dirty or diverged checkouts via autostash rebase

Approved 2026-07-19. Amends the dirty/diverged→warn-and-skip decision in
`2026-07-17-omc-watch-design.md`: that remains the DEFAULT; `--rebase` is an
explicit per-invocation opt-out. (The work started as `--ignore-dirty`; the
name changed because the behavior is "rebase instead of ff-merge-or-skip",
not "ignore the check".)

## Behavior

New boolean CLI flag `--rebase` on `omc watch` (`src/omc/cli.py:build_parser`,
threaded as a keyword-only bool through `run_watch` → `_tick`, like
`once`/`enable_documentation`/`auto_build`). No config-schema change —
CLI-only, matching the `--auto-build` precedent.

Per tick WITH `--rebase`, `_tick`'s ladder becomes:

1. off-branch → warn, skip (UNCHANGED — watch never yanks a checkout off its
   branch, flag or no flag).
2. `git fetch origin <base>`; failure → warn, skip (unchanged).
3. Not behind → up to date (unchanged; a dirty tree with nothing to pull
   needs no action).
4. NEW guard: unmerged paths in the tree (`git ls-files -u` non-empty, e.g.
   left by a previous autostash conflict) → quiet-skip token `conflicted`:
   `· unmerged paths in the tree — resolve them, skipping sync`.
5. Behind → `git rebase --autostash origin/<base>` — replaces BOTH the
   dirty/diverged skips and the `merge --ff-only`. Clean+not-diverged rebase
   fast-forwards to the identical result, so one code path covers all cases.

Without `--rebase`, behavior is bit-for-bit today's (dirty → skip, diverged →
skip, clean → ff-merge); `test_tick_refuses_dirty_tree` keeps passing
unmodified.

## Rebase outcomes (verified empirically against git)

- **Success** (exit 0, no unmerged paths): narrate
  `✓ rebased <base>: <old>..<new> (<behind> commits)`, return `"synced"` —
  index refresh, post-watch hook, and `--auto-build` fire exactly as after an
  ff-merge. Local commits ahead of origin are replayed on top (the user
  opted into history rewriting by passing the flag); dirty edits are
  autostashed and restored.
- **Mid-rebase conflict** (exit ≠ 0, rebase in progress): `git rebase
  --abort` — verified to fully restore prior state INCLUDING the autostash
  (dirty edits back in the tree, stash empty, HEAD unchanged). Quiet token
  `rebase-failed`:
  `✗ rebase onto origin/<base> failed — aborted, checkout restored; resolve manually`.
  If the abort itself fails, warn loudly with git's stderr; the loop
  continues either way (tick failures never crash the loop).
- **Autostash-pop conflict** (exit 0 BUT unmerged paths afterwards): git
  rebases HEAD successfully, then leaves the tree with conflict markers
  (`UU`) AND keeps the changes in `stash@{0}`. Exit code alone cannot
  distinguish this from success — detect via `git ls-files -u` after any
  zero-exit rebase. Narrate loudly once:
  `✗ rebased, but restoring your uncommitted changes conflicted — resolve the markers; your changes are also safe in git stash`.
  Token `autostash-conflict`. NOT an action tick — no index refresh, no
  hooks — the tree contains conflict-marker garbage that must not be
  indexed; the next tick after manual resolution syncs normally. Until origin
  advances again the next tick is `behind 0` → "up to date"; once origin does
  advance, that sync attempt lands in guard 4 (`conflicted`, quiet), so the
  loop never re-rebases a conflicted tree and never spams.

All new repeatable outcomes (`conflicted`, `rebase-failed`,
`autostash-conflict`) follow the quiet-token convention: narrate only when
the outcome changed since the last tick.

## Doctrine

The module docstring's "never destructive" sentence (`src/omc/watch.py:6-7`)
gains the caveat that `--rebase` is an explicit opt-in past the dirty/diverged
skips. Safety that remains under the flag: off-branch stays untouchable,
mid-rebase conflicts abort-and-restore, autostash preserves uncommitted work
(worst case parked in stash, loudly narrated).

## Docs

- `README.md` flag table (watch row) and the watch prose paragraph gain
  `--rebase`.
- argparse help:
  `Sync via 'git rebase --autostash' — syncs even dirty or diverged checkouts (opt-out of warn-and-skip)`.

## Testing (`tests/unit/test_watch.py`, real-git fixtures like existing ones)

1. `--rebase` + dirty + behind → synced, dirty edit survives, analyze
   recorded.
2. `--rebase` + diverged (non-conflicting local commit) + behind → local
   commit replayed on the new base, synced.
3. `--rebase` + conflicting local commit → `rebase-failed`, checkout restored
   (HEAD unchanged, dirt intact), no analyze, loop continues.
4. `--rebase` + dirty edit conflicting with remote → autostash-conflict
   narrated, tree has unmerged paths, stash holds the autostash, no analyze;
   the NEXT tick yields quiet `conflicted`.
5. No flag → existing dirty/diverged skips unchanged (already pinned by
   existing tests).
6. Clean + behind + `--rebase` → still syncs (rebase fast-forwards).

## Scope

One file of logic (`src/omc/watch.py`), a flag in `src/omc/cli.py` (parser +
dispatch), tests, README, module docstring. Nothing else.
