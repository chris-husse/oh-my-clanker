# Fix omc dependency watch — corrupt store spins the watch forever

Date: 2026-08-04
Slug: `fix-omc-dependency-watch`
Repo: oh-my-clanker (this branch).

## Problem

`omc dependency watch` retries the document step of
`kakarot.chorse.space/risk/fuse@b021f87` every 30s pass, forever.

Reproduced root cause: the dependency's `.gitnexus/lbug` store is
unopenable — **every** GitNexus open of it (`wiki` and a plain
`query --repo`) segfaults Node (`EXC_BAD_ACCESS at 0x18` in `lbugjs.node`:
`Database::initMembers → WALReplayer::replay → Checkpointer::readCheckpoint
→ NodeTable::deserialize → PrimaryKeyIndex::load → DiskArrayInternal`).
The child dies before printing any error, so stdout holds only the
`GitNexus Wiki Generator` banner and stderr is empty. `run_document`'s
failure message — `(cp.stderr or cp.stdout)[:400]`, no exit code
(`src/omc/dependency.py:458`) — therefore reduces to the useless line:

```
error: gitnexus wiki failed: GitNexus Wiki Generator
```

Fallout observed: 242 `omc-dep-document-*.log` temp files and 25 macOS
node crash reports, one per watch tick.

Three omc defects, one upstream bug:

1. **Diagnosability** — the failure message swallows the exit/signal code
   and drops one output stream entirely.
2. **No heal path** — the manifest entry is stuck
   `indexed: true, documented: false`; nothing revisits `indexed` when the
   store is provably broken, and the watch has no notion of a permanent
   failure.
3. **Log hygiene** — `_DocumentJob` mkstemps a fresh log per attempt and
   never deletes any; an eternal retry leaks logs forever.
4. **Upstream (GitNexus/lbug)** — opening a corrupt store must error, not
   SIGSEGV. Fixed in GitNexus per the standing ruling (ships via
   `omc update`); this spec only records the follow-up (see below).

## Approaches considered

- **A. Auto-heal with a one-shot cap, then park (chosen).** On evidence of
  a broken store, wipe the derived `.gitnexus` cache and flip the entry
  back to un-indexed so the existing ensure path rebuilds it — once. If
  the store is broken again after a rebuild, park the entry as `broken`
  and stop retrying. `.gitnexus` is pure derived data (rebuildable by
  `analyze` in the existing checkout); the watch's purpose is unattended
  reconciliation; the cap prevents a heal↔crash loop.
- **B. Park-only, heal manually.** Safest, but leaves the likely-common
  case (a one-off bad write) needing manual surgery — exactly what a
  watcher exists to avoid.
- **C. Heal decided in the watch loop.** Violates doctrine — depwatch only
  scans and schedules; every mutation is a worker verb — and the decisive
  evidence (`cp.returncode`) lives in the worker anyway.

## Design

### 1. Diagnosability — failure messages name the death and show both streams

New shared helper (natural home: `src/omc/gitnexus.py`, which both
`dependency.py` and `watch.py` already import), used by `run_document`'s
wiki-failure path and `run_ensure`'s analyze-failure path (same
`or`-swallow pattern today). `watch.py`'s four sibling sites
(`watch.py:248,252,272,294` — project-store analyze/wiki failures) adopt
the helper too: message-only there, no behavior change — the project
store keeps its existing heal machinery.

- `cp.returncode < 0` → `killed by SIGSEGV (signal 11)` (name via
  `signal.Signals(-rc).name`, numeric fallback for unnamed signals).
- Otherwise → `exit <rc>`.
- Detail = stderr and stdout **both**, each redacted and capped at 400
  chars, labeled when both are non-empty.

What the user would have seen instead of the banner line:

```
error: gitnexus wiki failed (killed by SIGSEGV (signal 11)) — stdout: GitNexus Wiki Generator
```

### 2. Heal — worker-side detection in `run_document`

**Detection:** the wiki child died by signal (`cp.returncode < 0`) and not
via the stall-killer (the `stalled` branch returns earlier, unchanged). A
signal death on open is the corruption signature. A false positive (e.g.
an external OOM SIGKILL) costs one bounded re-index of a healthy store —
acceptable, and capped.

**Heal (first time — manifest `heals` counter absent/0):**

- `rm -rf <checkout>/.gitnexus` — derived cache only; the git checkout is
  never touched.
- Manifest entry updated under the existing `update_manifest` flock:
  `indexed: false`, `heals: 1`.
- Print what happened and why; exit 1 (this document attempt still
  failed).
- The next watch tick sees `indexed: false` → schedules `ensure` (checkout
  exists, so it goes straight to `gitnexus analyze`) → then `document`
  again. No new scheduling logic — the existing state machine already does
  this.

**Park (signal death again — `heals` ≥ 1):**

- Manifest entry gets
  `broken: "gitnexus <verb> killed by <signal> after reindex — likely a GitNexus/lbug bug"`;
  no second wipe.
- The message tells the user the entry is parked and how to retry after
  upgrading GitNexus: `omc update`, then
  `omc internal dependency ensure --git <url> --commit <commit>` (the
  dependency's clone URL, from the manifest — `run_ensure` accepts only
  URLs, not key@hash refs).

**Counter lifecycle** (corrected during plan pressure-testing — the
brainstormed version reset `heals` on every successful re-index, which
would have let a deterministically-corrupting store loop
wipe→reindex→crash forever, and pointed the manual retry at `run_ensure`'s
cached-hit path, which returns early and would never have cleared
`broken`):

- `run_document` success → `documented: true` and both `heals` and
  `broken` are removed — a healthy document is the natural reset point.
- The automatic re-index (`run_ensure`'s full path → `_record`) clears
  `broken` defensively but **preserves `heals`** — that is what makes the
  cap bind on the second signal death.
- `run_ensure`'s **cached-hit path** (entry indexed, checkout present — the
  path a manual `ensure` of a parked entry takes) clears `broken` *and*
  `heals` when `broken` was set: an explicit human retry re-arms one fresh
  heal cycle. The watch never takes this path for parked entries (it skips
  them entirely), so the re-arm cannot loop.

Side effects audited:

- Wiping `.gitnexus` leaves GitNexus's global registry entry for the
  checkout dangling until the re-analyze re-registers it. Queries against
  it in that window fail with a clean "No GitNexus index found" error, not
  a crash; a parked entry leaves the same clean-erroring state. Acceptable.
- `internal.py:134` (the dependency-scoped gitnexus proxy) treats a
  healed/parked entry (`indexed: false`) as not-indexed and refuses with
  the ensure hint — correct fail-closed behavior, unchanged.
- Concurrency: only one action per `key@commit` per pass (attempted-set),
  and each heal wipes only its own commit's checkout; manifest writes stay
  under the flock. Safe under `_DOCUMENT_JOBS` parallelism.

### 3. Park semantics in the watch

- `depwatch._tick` skips entries with `broken` set — in **both** the
  ensure branch and the document branch (a parked entry is never
  rescheduled).
- `_manifest_status` stops counting broken entries as "remaining" and
  reports them separately. Announcements:
  - remaining > 0: existing pending wording, plus
    `, N broken (see omc dependency list)` when N > 0.
  - remaining = 0, broken > 0:
    `· pass complete — N item(s) broken (see omc dependency list); not retrying`.
  - remaining = 0, broken = 0: existing
    `✓ Finished documenting all dependencies!` (never claimed while
    anything is broken).
  - idle ticks (no actions) with broken entries parked say so once:
    `· idle — N broken item(s) parked (see omc dependency list); waiting for work`.
- `omc dependency list` shows `broken` in the DOCUMENTED column for
  parked entries.

### 4. Temp-log hygiene

- `_DocumentJob`: on success (`rc == 0`) the log file is deleted and the ✓
  line drops the log path; failures keep their log — that is the
  diagnosis artifact.
- No startup sweep of old logs: parking removes the source of unbounded
  growth, and macOS reaps stale `$TMPDIR` files on its own.

### 5. Manifest schema

Two new optional per-commit fields, tolerated by all existing readers
(everything uses `.get`): `heals: int` and `broken: str`. Absent means
healthy/never-healed. No schema version bump — an older omc reading a
newer manifest ignores them benignly.

### 6. Doctrine compliance

- Watch doctrine holds: every new behavior is warn-and-skip; no new crash
  paths in the loop.
- Workers keep the report-don't-render contract: plain stderr lines + exit
  codes; `OMC_PROGRESS`/`OMC_DEPENDENCY` lines unchanged.
- The heal's manifest writes go through `update_manifest`'s flock — safe
  under `_DOCUMENT_JOBS = 8` parallelism.

### 7. Testing

Unit (`tests/unit/test_dependency.py`, fake `ToolContext` per existing
pattern):

- wiki child rc `-11` → `.gitnexus` wiped, `indexed` flipped false,
  `heals` = 1, exit 1, message names SIGSEGV.
- rc `-11` with `heals` = 1 → no wipe, `broken` set, message carries the
  retry hint.
- document success removes `heals`/`broken`.
- automatic re-index (`_record`) preserves `heals` (the cap binds on the
  second signal death); cached-hit ensure of a parked entry clears
  `broken` and `heals` (manual re-arm).
- failure message contains both streams and the exit code (plain nonzero
  exit too).

Unit (`tests/unit/test_depwatch.py`):

- tick skips broken entries in both branches; pass announcement counts
  broken separately; idle transition honest. Existing announcement tests
  (e.g. `test_once_pass_drains_to_completion_and_announces`) are updated
  for the new wording.
- success deletes the temp log; failure keeps it.

E2E: unchanged — a real lbug store cannot be corrupted deterministically;
the crash path stays unit-faked. `test_e2e_gitnexus.py` still covers the
happy path.

### 8. Rollout on this machine

After merge: the first document attempt on fuse segfaults → heal wipes +
re-indexes (analyze succeeded before, so likely fine) → wiki either
succeeds (done) or segfaults again → parked with a clear message. Either
way the 30s spam ends. The old logs and crash reports age out or can be
deleted by hand.

## Follow-ups (out of code scope, recorded here)

- **Upstream GitNexus issue** (drafted as a docs-only file alongside this
  spec, `docs/superpowers/specs/2026-08-04-fix-omc-dependency-watch-upstream-issue.md`):
  opening a corrupt lbug store segfaults instead of erroring (full native
  stack captured on macOS/arm64, GitNexus 1.6.8, Node 26). Open question
  upstream: how an `analyze` that exited 0 produced an unopenable store.
