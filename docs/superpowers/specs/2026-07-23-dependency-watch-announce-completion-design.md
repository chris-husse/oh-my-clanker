# dependency watch/list — drain to completion, announce, human listing

Approved 2026-07-23 (user-driven, from live usage of `omc dependency-watch`).
Two asks: (1) the loop must SAY when everything is updated and documented —
not a notification ping, a definitive stderr line; (2) remake the CLI as a
`omc dependency` command group: `watch` and a human-readable `list`.

## CLI remake

`omc dependency <watch|list>` replaces the top-level `omc dependency-watch`
(shipped yesterday, no users beyond this repo — renamed outright, no shim).

- `omc dependency watch [--interval N] [--once]` — the reconciliation loop;
  config-gated as before (documenting needs the configured LLM provider).
- `omc dependency list` — read-only human table on STDOUT (no config
  needed): DEPENDENCY (manifest key), COMMIT (short 7), REF, INDEXED ✓/✗,
  DOCUMENTED ✓/✗, CREATED (date). Empty manifest → one friendly line
  pointing at `/omc:explain-dependency`. The machine surface
  (`omc internal dependency list` JSON) is untouched — skills keep using it;
  the human table lives in `depwatch.py` (`run_dependency_list(home)`)
  beside the watch loop.

## Drain to completion + announcement

Problems observed live: after a pass that did work, the only "done" signal
was `· all dependencies reconciled — waiting for work` on the NEXT tick (≤
interval later), and by design a tick that ran `ensure` deferred `document`
to the next tick — so `--once` could exit with docs still ungenerated and
nothing said.

Fix, in `run_dependency_watch`:

- **Attempted-set drain**: `_tick(ctx, attempted)` skips any action already
  attempted this pass (identity: `(verb, key-or-checkout, commit)`) and
  records what it spawns. A pass (`_pass`) re-ticks immediately (no sleep)
  until a tick takes zero new actions. Because a failed or stubbed action
  stays in the attempted set, a pass can never spin — and a successful
  `ensure` is followed by that dependency's `document` IN THE SAME PASS
  (the re-tick reads the fresh manifest).
- **Completion accounting**: after draining, reload the manifest;
  `remaining` = commit entries where NOT (indexed AND documented).
- **Announcement** (stderr, `_say`):
  - actions > 0 and remaining == 0 →
    `✓ Finished documenting all dependencies! (N dependencies, M commits)`
  - actions > 0 and remaining > 0 →
    `· pass complete — K item(s) still pending (see ✗ lines above)` plus
    `; retrying next tick` in loop mode / `; re-run to retry` with `--once`.
  - actions == 0 → the existing deduped idle line, unchanged.
- `--once` now means: reconcile EVERYTHING once (full drain), announce,
  exit 0.

## Renames rippling out

`omc dependency-watch` → `omc dependency watch` in: `skills/
explain-dependency/SKILL.md` (backfill pointer), README prose + Commands
table (which also gains the `omc dependency list` row), the contract-test
needle in `test_plugin_manifests.py`, and depwatch.py's module docstring.

## Testing

- Existing depwatch tests hold under attempted-set semantics (stubs never
  mutate the manifest, so each action is attempted once and the drain
  terminates — the ensure-defers-document assertion becomes "document is
  not spawned when ensure did not change the manifest").
- New: a STATEFUL `omc` stub that actually flips the manifest
  (ensure → indexed:true, document → documented:true) proves a single
  `--once` pass drains ensure→document and prints the Finished line.
- Pending line: no-op stub + undocumented entry → pending message, no false
  Finished; ensure spawned exactly once (no spin).
- `omc dependency list`: table content for a seeded manifest (✓/✗ cells),
  friendly empty-manifest line; parser accepts `dependency watch --once` /
  `dependency list`, rejects the old `dependency-watch`; bare
  `omc dependency` → usage, exit 2.

Out of scope: notifications (explicitly declined), any change to
`omc internal dependency …` or the skills' query flow.
