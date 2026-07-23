# dependency watch — document up to 8 dependencies in parallel

Approved 2026-07-23. Supersedes the "one wiki at a time" line of the
2026-07-23 dependency-watch-announce spec: with the docs model floored at
the standard coding tier (#18), wiki runs are minutes not hours, and the
remaining wall-clock cost is the SEQUENTIAL loop over undocumented
dependencies. `omc dependency watch` now documents up to 8 missing
dependencies concurrently.

## Parallel document batch (depwatch.py)

- A tick still scans sequentially (adoption + ensure spawns unchanged —
  they are fast and mutate git state), but `document` actions are COLLECTED
  during the scan and executed as one batch through a
  `ThreadPoolExecutor(max_workers=min(_DOCUMENT_JOBS, len(batch)))`,
  `_DOCUMENT_JOBS = 8` (hard cap per the ask; no flag until wanted).
- Threads only wait on subprocesses (`ctx.run`) — no shared mutable state
  beyond `_say` narration, whose per-line prints interleave but stay whole
  lines. A multi-item batch announces itself:
  `→ documenting N dependencies (up to 8 in parallel)`.
- Drain/attempted-set/announcement semantics are unchanged: the batch's
  size counts as the tick's actions; completion is judged from the manifest
  after the drain as before.

## Manifest lock (dependency.py) — the correctness half

8 concurrent `omc internal dependency document` processes finishing close
together each do load → flip `documented` → save. The atomic tmp+rename
write prevents torn files but NOT lost updates: two writers loading the
same snapshot lose one flip — and a lost `documented: true` re-runs an
entire LLM wiki next tick. Fix:

- `update_manifest(home, mutate) -> dict`: flock-guarded read-modify-write
  (`fcntl.flock` exclusive on `<home>/dependencies.json.lock`, a sibling
  lockfile; fcntl is stdlib on macOS/Linux — omc's platforms). Loads inside
  the lock, applies `mutate(manifest)`, saves, returns the saved manifest.
- `run_ensure` and `run_document` route their existing reload-before-mutate
  blocks through `update_manifest`. Reads (`resolve_ref`, `list`,
  depwatch scans) stay lock-free — they tolerate staleness by design
  (attempted-set, next tick).

## Testing

- Parallelism proven without timing flakiness: three undocumented deps
  whose sorted-first document STUB blocks until a marker file that the
  sorted-last dep's stub creates. Sequential execution can never create
  the marker (the first call would time out and exit 1); parallel completes
  quickly. Assert all three document argvs spawned and no `✗ failed` line.
- Lock proven by hammering: two threads × 25 `update_manifest` mutations
  adding distinct keys → all 50 present (lost updates would drop some).
- Existing depwatch/document tests unchanged (spawn-membership assertions
  are order-independent).

Out of scope: parallel ensure (fast, and concurrent clones of the same
remote add failure modes for no gain); a --jobs flag; streaming wiki
progress (separate follow-up).
