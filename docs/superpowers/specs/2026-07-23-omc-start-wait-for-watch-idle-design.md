# `omc start` waits for `omc watch` idle — the watch locks

Approved 2026-07-23. `omc start` must not snapshot a primary checkout that
`omc watch` is mid-way through updating, and two `omc watch` instances must
never run against the same primary.

## Problem

Cutting a worktree SNAPSHOTS the primary checkout: worktrunk's `copy-ignored`
post-start step copies every gitignored file — `.gitnexus/`, `.omc/docs/`,
`.env`, caches — with no excludes (see the 2026-07-17 watch design). `omc
watch` is the process that mutates exactly those files: ff-merge → index
refresh → wiki mirror → post-watch hook → auto-build. A worktree cut while
watch is mid-tick snapshots a half-updated graph, half-mirrored docs, or a
half-built tree. Separately, two watch loops on one primary would race each
other over the same files. The processes shared nothing at runtime; nothing
prevented either race.

## Decision

OS advisory locks via the `filelock` library (new runtime dependency,
`filelock>=3,<4` — de-facto standard, Unlicense, actively maintained). The
kernel auto-releases an advisory lock when its holder dies, so a crashed
watch can never wedge anything — no hand-rolled state files, heartbeats, or
staleness heuristics (explicitly rejected).

TWO locks, because one cannot serve both jobs: an IDLE watch holds nothing
tick-wise, so a lock that means "watch is busy" cannot also mean "a watch
exists". Both live in the primary's `.git` dir — `.omc/` is committed
content, `.git` is per-clone and always exists in the primary. Resolution:
`git rev-parse --git-common-dir` (the shared `.git` from anywhere in the
repo, worktree or primary, one call — no path-joining assumptions):

1. **Instance lock** `<primary>/.git/omc-watch.lock` — held by `omc watch`
   for its ENTIRE lifetime. Forbids parallel watches.
2. **Busy lock** `<primary>/.git/omc-watch-busy.lock` — held only during the
   busy portion of each tick. Free ⇔ watch idle. This is what start probes.

`flock` is unreliable on NFS mounts — acceptable for a local dev tool
(documented in the module docstring).

## Mechanism

New module `src/omc/watchlock.py` — single owner of both locks' identities
and the acquire idioms. Lock paths resolve via `git rev-parse --git-common-dir` (see Decision); no
repo → no locks (nothing to guard — `wt` could not cut a worktree anyway).

**Watch side (`run_watch`).**

- Startup: try an instant acquire of the INSTANCE lock. Already held →
  print exactly
  ``Another `omc watch` instance may be running. Pass `--clear-mutex` to bypass``
  and exit 1. `--clear-mutex` → unlink the INSTANCE lock file only, then
  acquire fresh — this deliberately breaks mutual exclusion with a live
  holder (that is what a bypass means; "may be running" reflects that
  uncertainty). A bypassed second watch still serializes its ticks against
  the live one via the busy lock. Hold the instance lock until exit.
  A scheduled `omc watch --once` colliding with a running loop watch bails
  the same way — that is the feature working, not a regression: two
  mutators on one primary is exactly the forbidden race.
- A RESTART always wipes the lock by construction: a dead watch's flock is
  kernel-released, so a restarted watch acquires cleanly without
  `--clear-mutex`, even after SIGKILL mid-tick.
- Each loop iteration: the busy portion — `_chain_tick` + `_tick` +
  `_post_watch_hook` + `_auto_build` — runs inside `with busy_lock:`;
  released before `time.sleep(interval)`. Therefore lock free ⇔ watch idle,
  by construction. `--once` behaves identically (both locks, one tick).

**Start side (`run_start`).** `omc start` NEVER holds a lock — it only
verifies the BUSY lock is free immediately before the workspace-creating
moment (`worktree.sync_base` + `create_worktree`):

- `--no-mutex` passed → skip entirely.
- Try an instant acquire of the busy lock; on success release immediately
  and proceed silently (the common case: no watch running, or watch idle).
  A momentary acquire-and-release IS the probe — an advisory lock cannot be
  observed without touching it; it is held for microseconds and never
  carried into any work.
- Busy → print exactly
  ``→ waiting for omc watch to finish. Pass `omc start --no-mutex` to bypass``
  and block indefinitely (Ctrl-C aborts, as everywhere in start). On
  acquire: release immediately, proceed unlocked.
- Start never touches the instance lock, so ANY number of parallel
  `omc start` runs work while watch is running — each independently waits
  for idle, then all proceed concurrently (probes do not serialize the
  clones beyond microseconds).
- `--dry-run` never locks (it creates nothing). No lock is ever carried
  anywhere near the session launch/execvp.

**CLI.**

- `start --no-mutex`: "Do not wait for an in-flight `omc watch` update
  before creating the worktree."
- `watch --clear-mutex`: "Remove a leftover watch mutex and run anyway."

Accepted window: because start does not hold the busy lock, a watch tick can
begin while the clone's `copy-ignored` step is still copying. That requires
new commits landing on origin in exactly that window — judged acceptable,
and strictly better than today.

**Out of scope (recorded follow-up).** `omc internal rebase-main`
(`internal.py:mirror_snapshot`) also READS the primary's knowledge dirs when
re-mirroring a worktree's snapshot and can race watch's writes the same way.
It should probably adopt the same busy-lock probe; deliberately not part of
this change.

## Failure modes

| Situation | Behavior |
| --- | --- |
| No watch running | start: instant probe, zero overhead, no output |
| Watch mid-tick / hook / auto-build | start prints the wait line, proceeds the moment watch re-enters idle |
| Watch crashed / SIGKILLed | Kernel released both locks — start proceeds immediately; a restarted watch acquires cleanly (no `--clear-mutex` needed) |
| Second `omc watch` while one runs | Bails, exit 1, the exact "Another `omc watch` instance" message |
| `watch --clear-mutex` vs a live watch | Runs anyway (lock file unlinked + fresh acquire) — user-owned risk |
| Many concurrent `omc start` | All work, during and between watch ticks; none holds a lock |
| Not in a repo / no primary root | No locks, unchanged behavior |

## Testing

Real `filelock` and real subprocesses — no mocking the kernel. Two REQUIRED
tests named by the design:

1. **Restart wipes the lock**: spawn a real watch subprocess, SIGKILL it
   mid-tick (slow fake post-watch hook), start a new watch → it acquires
   cleanly and runs, no `--clear-mutex`.
2. **Parallel starts during watch**: with a real watch subprocess running,
   launch multiple `omc start` runs concurrently → all complete and each
   produced its worktree.

Plus:

- Second concurrent watch bails with exit 1 and the exact message;
  `--clear-mutex` makes it run.
- `tests/unit/test_watch.py`: busy lock held while a fake slow hook runs;
  released during the sleep window; instance lock held while idle.
- `tests/unit/test_start.py`: probe is free again BEFORE `create_worktree`
  is invoked; pre-held busy lock → the exact wait line on stderr, proceeds
  on release; `--no-mutex` never touches the lock; `--dry-run` unaffected.
- Existing Docker E2E suites stay green; `test_e2e_start` gains a cheap
  assertion that the lock files appear under the primary's `.git`.
- Existing start unit tests are untouched by construction: their git stub
  answers `rev-parse` with exit 128 ("not a repo"), so the probe self-skips.
  New lock tests use real tmp repos (the `test_watch.py` fixture pattern).
