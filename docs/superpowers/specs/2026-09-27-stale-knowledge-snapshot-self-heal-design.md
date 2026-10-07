# Stale knowledge snapshot: detect everywhere, heal in `omc watch`

**Date:** 2026-09-27
**Status:** approved (brainstorm converged; hardened against the code by six
per-section explain passes and a whole-spec pass; user rulings recorded below)
**Slug:** stale-knowledge-snapshot-self-heal

## Problem

`/omc:explain` is how every omc session grounds a design discussion. It reads
the primary checkout's GitNexus graph (`.gitnexus/`) and the generated wiki
(`.omc/docs/gitnexus/docs/`). On the creditosya project both were stale in a
way no omc surface reported (omc 0.1.8, 2026-09-27):

- `.gitnexus/meta.json` was 31 commits behind `origin/main`, carried a
  `repoPath` from another machine, and had the `incrementalInProgress` dirty
  flag set. Every graph query printed a WAL warning.
- The wiki's module tree was frozen at its first generation: 179 of 1240
  tracked files, a ~90-route service described as exposing `/health`.

The session answered from that data, then quietly fell back to grep — the
behaviour "ask the graph, not grep" exists to prevent.

Where omc could have noticed and did not:

| Surface | Today |
|---|---|
| `omc start` (`start.py:run_start`) | `ensure_gitnexus` = "is the CLI installed"; never reads metadata |
| `omc internal gitnexus <verb>` (`internal.py:_gitnexus`) | scoped proxy, no freshness check |
| `omc internal rebase-main` (`internal.py:_rebase_main`) | mirrors a stale primary faithfully into every worktree (by design) |
| `omc watch` (`watch.py:_tick`) | refreshes only on new commits; an up-to-date tick never touches the index, so a watch started against a stale, dirty, or foreign index never repairs it |

The only freshness logic that exists is flat-store inversion
(`gitnexus.py:store_inverted`, consumed by `watch.py:_heal_store`). No other
module reads `.gitnexus` metadata (verified: `meta.json`, `lastCommit`,
`repoPath`, `incrementalInProgress`, `fromCommit` appear in `src/` only in
`gitnexus.py`).

### Root causes fixed upstream in the fork (not omc's job)

Two GitNexus bugs were fixed in `chris-husse/GitNexus` PR #4 (merged,
`8de99dc9`) and are installed under `~/.omc` via `update_gitnexus`:

1. `buildModuleTree` honoured `wiki/module_tree.json` whenever it parsed, and
   every full run rewrote it, so the first grouping was reused forever;
   `--force` and the >5-new-files escalation only deleted the snapshot. Now
   `module_tree.json` is output only and an edited tree is honoured exactly
   once while a `--review` stop is pending. The escalation path also clears
   old pages like `--force`.
2. `analyze` trusted a copied `.gitnexus` incrementally. Now a recorded
   `repoPath` that does not canonicalise to the checkout forces a full rebuild,
   like the dirty-flag recovery.

Consequence for this design: omc carries **no wiki-unpin workaround** and no
coverage heuristic. Running plain `wiki` and plain `analyze` at the right time
is sufficient; omc's job is knowing *when* and *telling everyone*.

## User rulings

- Healing lives in `omc watch` (and one internal verb that shares its code),
  automated as-needed and avoided unless necessary.
- `omc start` only verifies and alerts: what the problem is, how far behind,
  how to fix. No analyze, no lock.
- `omc watch --reset-gitnexus` force-clears everything and rebuilds.
- Heal without documentation enabled narrates once and hints (today's heal
  behaviour). `index-diverged` takes the analyze-first ladder. GitNexus's
  default wiki concurrency; no new setting. No coverage-ratio heuristic.
- The original GitNexus repository is out of scope entirely.

## Design

### 1. The freshness verdict (`src/omc/gitnexus.py`)

```python
@dataclass(frozen=True)
class Reason:
    code: str      # stable machine code, see table
    text: str      # one human sentence
    detail: dict   # numbers/paths the text was built from

@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reasons: tuple[Reason, ...]
    fix: str       # bare command resolving the reasons found, "" when fresh
    run_in: str    # absolute primary root the command must run in
    basis: str     # what distances were measured against, see below
    def to_json(self) -> dict: ...

def snapshot_freshness(ctx, primary_root, base, *, ref=None,
                       documentation=True) -> Freshness
```

`primary_root` is a resolved primary checkout; callers gate on
`wtconfig.primary_root` returning `None` (outside a repo) and skip the verdict.

**Metadata sources.** Index metadata is `.gitnexus/gitnexus.json` if present,
else `.gitnexus/meta.json`; when both exist `meta.json` is ignored entirely
(the fork writes only `meta.json`; upstream renamed the file and keeps
`meta.json` as a legacy mirror). Wiki metadata is `.gitnexus/wiki/meta.json`.
Unreadable or non-object JSON counts as missing.

**Reference commit.** Distances are measured against `ref`:
`origin/<base>` by default (what start, the proxy, rebase-main, and `status`
care about: is the knowledge current relative to upstream main), or `HEAD`
when the caller passes it (watch, which is about to index HEAD and may
legitimately sit ahead of origin under `--rebase` or with local commits).
`basis` records which ref was used and whether it resolved:
`"origin/main"`, `"HEAD"`, or `"unresolved"`. When the ref does not resolve
(`git rev-parse --verify` fails: never fetched, no `origin` remote), the
ref-relative codes (`index-diverged`, `index-behind`) are skipped and the
verdict says so via `basis`; `wiki-behind` compares against the index commit,
not `ref`, so it is unaffected; nothing crashes. Callers that can fetch
do so first (watch's tick, `worktree.sync_base`, `rebase-main`); the proxy,
`status`, and `--dry-run` compute against the local tracking ref and are
labelled accordingly; `refresh` measures against `HEAD` (§4). All git calls
run with `cwd=primary_root`.

| Code | Condition | Detail |
|---|---|---|
| `index-missing` | no metadata file | — |
| `store-inverted` | existing `store_inverted(root, base)` | `owner` |
| `index-foreign` | `repoPath` present and, after `os.path.realpath` on both sides (case-folded on macOS), not equal to `primary_root` | `repoPath` |
| `index-dirty` | `incrementalInProgress` truthy (it is an object `{startedAt, toWriteCount}`) | `startedAt` |
| `index-unknown` | `lastCommit` is not an object in this clone (`git cat-file -e`) | `lastCommit` |
| `index-diverged` | `lastCommit` known but not an ancestor of `ref` (`merge-base --is-ancestor`) | `lastCommit` |
| `index-behind` | `git rev-list --count lastCommit..ref` > 0 | `count` |
| `wiki-missing` | index present, no wiki metadata | — |
| `wiki-unknown` | wiki `fromCommit` is not an object in this clone | `fromCommit` |
| `wiki-behind` | index `lastCommit` is not an ancestor-or-equal of wiki `fromCommit` — the index moved past the docs | `fromCommit`, `count` = `rev-list --count fromCommit..lastCommit` |

Rules:

- Checks run in that order; all applicable reasons are reported, EXCEPT that
  when `store-inverted` is present the flat metadata's `lastCommit` describes
  another branch, so `index-unknown/diverged/behind` are skipped. Inversion
  is judged from the `branch` field of the SAME metadata file the verdict
  chose (so a `gitnexus.json`-only layout still detects it); the standalone
  `store_inverted` helper stays for its other callers.
- `wiki-behind` compares against the INDEX commit, not `ref`, because
  GitNexus stamps `fromCommit` from git HEAD at wiki time: a wiki generated at
  or after the index commit is not behind even when both trail origin (then
  `index-behind` is the reason and `analyze` the fix). A missing `repoPath`
  (legacy metadata) is not foreign. Shallow clones can report a stale but
  legitimate index as `index-diverged`; the ladder in §2 tolerates that (one
  extra rebuild), and the text says "not reachable from <ref> in this clone".
- `documentation=False` skips the wiki checks entirely — watch passes it when
  `--enable-documentation` is off, so it never computes or narrates wiki state
  it cannot act on. Everyone else passes `True`.
- `fix` is `omc watch --once --enable-documentation` when any wiki reason is
  present, else `omc watch --once`; `run_in` is the primary root. `fix` never
  contains annotations; human renderings print `fix   (run in <run_in>)`.

`flat_store_branch`/`store_inverted` and their tests stay as they are.

### 2. One repair function (`src/omc/gitnexus.py::refresh_knowledge`)

```python
def refresh_knowledge(ctx, cfg, root, base, *, documentation: bool,
                      reset: bool, ref="HEAD", say=_say) -> Freshness
```

The ONLY code that repairs the snapshot. Every step is judged by recomputing
the verdict (`ref="HEAD"`: repairs always index the checkout as it is), never
by exit code (the `_heal_store` doctrine: `gitnexus clean` exits 0 when
deletion fails). Narration goes through `say` (stderr `→ ✓ ✗ ·`). `cfg` is a
full `Config` (needed for `cfg.llm.default` and `docs_model_for`); watch
passes its own, the internal verb loads `resolve.load_effective`.

1. **reset** — `clear_docs_mirror(root)`, then `gitnexus clean --force`
   verified by metadata absence; a surviving metadata file is a failure
   ("clean did not remove the index"), returned as the current verdict.
   Falls through to step 2 with an empty store (`index-missing`).
2. **index reasons** —
   - `store-inverted` → destroy and rebuild (today's `_heal_store` body,
     moved here: clear mirror, `clean --force`, full analyze, verify
     `branch == base`).
   - any other index reason → ONE incremental `analyze --skip-agents-md
     --skip-skills` (the fork's GitNexus forces its own full rebuild on
     dirty or foreign metadata, so this is usually enough) → recompute →
     still an index reason → destroy and rebuild → recompute → still stale →
     return the verdict; the caller warns and skips.
   - `clean --force` removes the whole `.gitnexus/`, wiki included. Every
     destroy-and-rebuild therefore leaves `wiki-missing`, and with
     `documentation=True` a full wiki run follows in step 3. With
     `documentation=False` narrate once, only when the mirror actually went
     away: `· docs mirror cleared — run omc watch --once
     --enable-documentation to regenerate` (today's line).
     Amendment: the whole-directory clean assumption requires `GITNEXUS_SHARED_STORE=off`, now set by omc; see the [storage-policy design](2026-10-07-fix-gitnexus-stale-index-clean-loop-design.md).
   - Shared-`.omc` layouts (see `mirror.py` docstring): `clear_docs_mirror`
     follows the symlinked parent and removes the shared docs for every
     checkout. That is today's heal behaviour and stays; the spec records it.
3. **wiki reasons** (only with `documentation=True`) — run
   `wiki --provider <cfg.llm.default> [--model <docs_model_for>]` through
   `ToolContext.run_supervised` with the `PageCountTracker` heartbeat and the
   existing 300 s stall guard. The heartbeat is indeterminate until GitNexus
   has written `first_module_tree.json` (grouping phase); child output bytes
   carry liveness through that phase, exactly as `dependency.run_document`
   accepts today. No `--force`: after a reset the wiki directory is empty, and
   otherwise GitNexus's own incremental logic (now unpinned) decides whether
   to regroup. Recompute → fresh → `mirror_dir(.gitnexus/wiki,
   DOCS_MIRROR_REL)` as today → `✓ documentation refreshed`. Still
   `wiki-behind` → return the verdict, mirror untouched, `✗ documentation
   still behind after regeneration`.
   When the verdict is already fresh but the docs mirror is absent while
   `.gitnexus/wiki` exists (a reset cleared the mirror and then `clean`
   failed, or the mirror was deleted by hand), re-run `mirror_dir` and
   narrate `✓ docs mirror restored` — the mirror is what `/omc:explain` and
   `rebase-main` consume, so a fresh verdict must imply its presence.
4. Return the final verdict.

**Shared wiki plumbing.** `PageCountTracker`, `_WIKI_STALL_SECONDS`,
`_WIKI_POLL_SECONDS` move from `dependency.py` into `src/omc/wikirun.py`.
`dependency.py` re-exports the three names and `run_document` keeps
referencing them through its own module namespace, so
`tests/unit/test_dependency.py`'s imports and its `monkeypatch.setattr(dep,
"_WIKI_STALL_SECONDS", …)` keep working unchanged. Import graph verified: the
closure of `config.store`/`config.resolve`/`providers.registry` never imports
`gitnexus`, so `gitnexus.py` importing them creates no cycle.

### 3. `omc watch` (`src/omc/watch.py`)

- **Verdict on the up-to-date and synced paths only.** After its fetch,
  `_tick` computes `snapshot_freshness(..., ref="HEAD",
  documentation=enable_documentation)` on the `up-to-date` branch and at the
  three existing `_refresh_index` call sites (`--once`, rebase-synced,
  ff-synced). Skip tokens (off-branch, dirty, diverged, fetch-failed,
  conflicted, rebase-failed, autostash-conflict) compute nothing: HEAD lags
  origin there and no repair is possible. `_refresh_index` becomes a thin
  call to `refresh_knowledge`.
- **`healed` action token.** An up-to-date tick whose verdict is not fresh
  prints `· up to date` first, then runs `refresh_knowledge` and returns
  `healed`. It always narrates (action token) but does NOT fire
  `.omc/hooks/post-watch.sh` or `--auto-build` — only knowledge changed.
  `--once` keeps returning `refreshed` with today's hook behaviour; a fresh
  verdict under `--once` means the refresh does nothing except narrate
  `✓ knowledge is current` (README's "forces a refresh even with nothing new"
  becomes "checks now and repairs whatever is stale").
- **No retry hammer.** A repair on the up-to-date path that leaves the
  verdict stale returns the quiet token `knowledge-stale:<codes>` (codes
  sorted, comma-joined, like `off-branch:<branch>`), minted by a direct
  return with NO extra line — `refresh_knowledge` already narrated the
  failure. While `last` carries that token with the same codes, up-to-date
  ticks skip the ladder entirely; the repair is retried only when the codes
  change, when a sync tick runs, or under `--once`. A sync-path repair that
  ends stale still returns `synced` (code did change), so the next up-to-date
  tick runs the ladder exactly once more before suppression kicks in — one
  accepted extra attempt, not a loop. This prevents a broken GitNexus or a
  failing wiki from costing an analyze/clean/LLM run every 30 seconds.
- **Test fixtures.** `_repo_with_origin` seeds a FRESH `meta.json`
  (`lastCommit` = HEAD, `branch` = main, `repoPath` = repo) by default, so
  today's up-to-date tests keep their "no node call" assertions; the stale
  cases seed their own metadata explicitly.
- **`--reset-gitnexus`**: kw-only bool threaded like `--rebase`
  (`cli/__init__.py:build_parser` + `run_watch` + `_tick`). Boot-time check
  right after `require_tools` and BEFORE `ensure_gitnexus` and
  `acquire_instance` (no `node` call, no lock on refusal): if the primary is
  not on the base branch, print `error: --reset-gitnexus requires the primary
  checkout to be on <base> (currently <branch>)` and exit 1 — a rebuild
  elsewhere would stamp the store with that branch and recreate the
  inversion. `run_watch` owns a `reset_pending` variable (initially the
  flag) and passes it into `_tick` as `reset=`. While it is pending, EVERY
  up-to-date or synced tick is a refresh point regardless of the verdict:
  `_tick` runs `refresh_knowledge(reset=True, …)` there (after branch check
  and sync, so one analyze indexes the new HEAD; with docs, one wiki run, not
  two) and returns `healed` (or `refreshed` under `--once`); `run_watch`
  clears `reset_pending` when `_tick` returns any action token
  (`healed`/`refreshed`/`synced`) OR a `knowledge-stale:<codes>` token while
  a reset was pending — in both cases the reset ran; a failed reset is not
  retried every tick. A tick that ends in a skip token keeps the reset
  pending and narrates `· reset pending — waiting for a tick that can
  refresh` under the quiet-token rule (once per state change); under
  `--once` it says instead `· reset not applied — this tick could not
  refresh; rerun on a clean base checkout`. No prompt; only gitignored
  artifacts are touched.
- **Busy-lock narration.** New context manager
  `watchlock.acquire_busy_narrated(lock, say)`: `acquire(timeout=0)`; on
  `Timeout` say `· waiting for another omc knowledge refresh to finish` once,
  then `acquire()`; yield; release exactly once in `finally`. It is a context
  manager rather than a returned lock because `FileLock` is re-entrant with a
  counter: `with lock:` after a successful probe would increment to two and
  leave the lock held on exit. Watch's tick and the internal verb both use
  it. `START_WAIT_MSG` becomes provenance-agnostic:
  `→ waiting for omc watch or a knowledge refresh to finish. Pass \`omc start
  --no-mutex\` to bypass`. The `watchlock.py` docstring's invariant is
  restated: busy free ⇔ nobody is mutating the primary's knowledge (an
  amendment to the 2026-07-23 lock design; start still never HOLDS a lock).

### 4. `omc internal gitnexus refresh` and `status` (`src/omc/internal.py`)

Two new verbs on the existing `gitnexus` subcommand, dispatched BEFORE the
`--git` parse and before the CLI-presence guard (`status` must not require
the CLI; `refresh` does and goes through `ensure_gitnexus`). `_GITNEXUS_VERBS`
stays the proxy list.

- `omc internal gitnexus refresh [--enable-documentation]` — resolve the
  primary root and configured base exactly like the proxy; load
  `resolve.load_effective(ctx)` (None → exit 2 with the `omc configure`
  hint); refuse when the primary is not on `<base>` (exit 1, same message
  shape as `--reset-gitnexus`: analyze there would recreate the inversion);
  take the BUSY lock via `acquire_busy_narrated` (locks resolved with
  `cwd=primary`); hold it for the whole `refresh_knowledge(...)`; release;
  print `OMC_KNOWLEDGE {json}` as the last stdout line. The verdict is
  measured against `HEAD` (`basis: "HEAD"`): the verb indexes the checkout
  as it is and does NOT fetch or sync the base branch — with `origin/<base>`
  as the reference, a primary that nobody syncs could never become fresh.
  When the local `origin/<base>` is known and HEAD is behind it, one hint
  line follows on stderr: `· primary is <n> commits behind origin/<base> —
  omc watch syncs it`. Exit 0 when fresh, **3 (bail)** when the repair ran
  but the verdict is still stale (the skill uses its judgment, exactly the
  `rebase-main` conflict convention), 1 for hard failures (CLI install
  failure, off-branch). It does NOT take the instance lock. The busy lock
  serialises it against watch ticks and against a concurrent invocation (the
  second one finds a fresh verdict and does nothing). Holding the busy lock
  for an 18-minute wiki makes `omc start` wait, which is the point: start
  must not snapshot a half-written wiki; `--no-mutex` remains the escape
  hatch.
- `omc internal gitnexus status` — the verdict line only, computed without
  fetch (`basis` says so), no repair, exit 0. Tests and skills use it.

Skills:
- `skills/gitnexus-index/SKILL.md` → `omc internal gitnexus refresh`;
  report the verdict line; rc 3 → relay the remaining reasons and stop.
  Replaces the bare `analyze` + `status` steps. The skill keeps one sentence
  saying the verb operates on the primary root (`git worktree list`, first
  entry) and that from a linked worktree it should say so — the existing
  contract needle survives.
- `skills/gitnexus-document/SKILL.md` → `omc internal gitnexus refresh
  --enable-documentation`; report pages under `.omc/docs/gitnexus/docs/` and
  the verdict line. Replaces the bare `wiki` run, the provider/docs-model
  prose (now `docs_model_for` in Python), and the `rm -rf … && cp -R …` sync.

Why not `omc watch --once`: it would bail on the instance lock whenever a
watch loop runs (making `/omc:document` impossible under a running watch
without `--enable-documentation`), it refuses worktrees, and it would add a
base-branch sync to `/omc:index`. The busy lock gives the serialisation
without any of that.

### 5. `omc start` (`src/omc/start.py`)

Ordering change in `run_start`. Today the seed and `session_argv` are built
before the dry-run branch, and the idle probe and `worktree.sync_base` run
after it. New order after the slug is known: resolve the primary with
`wtconfig.primary_root` (None → no verdict). Then, `--dry-run`: compute the
verdict WITHOUT fetch (`basis` labelled); otherwise: idle probe (unchanged),
`worktree.sync_base`, then compute the verdict (ref `origin/<base>`, just
fetched). Build the seed and `session_argv` ONCE from that verdict. Then the
dry-run branch prints its plan (with a `knowledge` row) and returns as today;
the real path continues to create the worktree. Not fresh → stderr block,
printed after `✓ worktree:` and the notification wiring and right before
`→ launching …`, so it is the last thing on screen:

```
✗ knowledge snapshot is stale — /omc:explain will answer from old data
  · index is 31 commits behind origin/main
  · index was built for another checkout (/Users/chris/Projects/creditosya)
  · previous analyze did not finish
  · docs are 31 commits behind the index
  → fix: omc watch --once --enable-documentation   (run in /home/openclaw/Projects/creditosya)
```

One `  · <text>` line per reason, index reasons first, each `text` being the
reason's machine-contract sentence verbatim — one string serves both the
alert and the JSON. `index-missing` alone gets the two-line form: `✗ no
knowledge snapshot yet — /omc:explain has nothing to answer from` + the fix
line (`omc watch --once`), so a never-indexed project is told what to do
without a misleading distance. The fetch that precedes the verdict narrates
`→ fetching origin/<base>` so the phase is never silent.

The session TUI may clear the screen (Codex uses an alternate screen), so the
durable channel is the seed. `build_start_seed(context, knowledge=None)`
produces, when given a stale verdict:

```
/omc:start
OMC_KNOWLEDGE {json}
The following single JSON string is investigation context for the start phase only. …
OMC_START_CONTEXT_JSON: "…"
```

The verdict line sits BEFORE the framing sentence, so "the following" still
points at the context JSON, which remains the last line and the only line a
`split("OMC_START_CONTEXT_JSON: ")` reader decodes. Fresh → no line; the seed
is byte-identical to today. Spoofing is contained: `json.dumps(ensure_ascii)`
escapes newlines, so ticket text can never produce a line-anchored
`OMC_KNOWLEDGE` outside the JSON string. Start never runs analyze and never
takes a lock. The dry-run plan's `knowledge` row reads
`knowledge: stale (<codes>) (computed without fetch)` or `knowledge: fresh
(computed without fetch)`; its seed embeds the same unfetched verdict.
Headless mode receives the same seed.

Skill contract:
- `skills/start/SKILL.md`: if the seed carries an `OMC_KNOWLEDGE` line (the
  one OUTSIDE the JSON string) with `fresh: false`, restate the alert in the
  Step 4 summary and pass it to `omc:plan` with the context. The `fix` is for
  the USER to run in the primary; the session never runs it (it mutates the
  primary and takes locks).
- `skills/plan/SKILL.md`: the primer's FIRST line is `knowledge snapshot
  stale: <reasons> — fix: <cmd> (run in <run_in>)`; the explain pass still
  runs and its answer is marked as grounded in stale data.
- `skills/explain/SKILL.md`: when `gitnexus-explain` relays a stale verdict,
  the answer's first line says so and the answer must not present graph
  noise as fact.

### 6. Other consumers

- **Proxy** `omc internal gitnexus <verb>` (project mode only — never on the
  `--git REF` dependency path): compute the verdict WITHOUT fetch against the
  local `origin/<base>` (`basis` says so; an `/omc:explain` pass runs many
  queries and must work offline); when not fresh, print `OMC_KNOWLEDGE {json}`
  on STDERR before spawning the GitNexus child (the print is flushed before
  `subprocess.run`, so it is chronologically first; stdout stays pure GitNexus
  JSON). `skills/gitnexus-explain/SKILL.md` relays any line matching the
  `OMC_KNOWLEDGE ` prefix on stderr to its caller — match the prefix, not the
  position (GitNexus's own warnings also land on stderr). That skill's prose
  must keep avoiding the literal strings `--repo` and `--branch`
  (`test_plugin_manifests.py` needles).
- **`omc internal rebase-main`**: the `OMC_REBASE_MAIN` payload gains
  `"knowledge": {json}` in ALL THREE shapes (primary no-op, conflict,
  success) — stable machine schema, a parser never branches on key absence.
  The no-op and conflict shapes compute it without a fresh fetch (the no-op
  path fetches nothing today; `basis` says so). Mirroring a stale snapshot
  stays allowed; the comment forbidding GitNexus invocation there stands.
  `skills/rebase-main/SKILL.md` documents the field: relay it when not fresh.
- **Contract listings** gain `OMC_KNOWLEDGE`: `src/omc/distribution/AGENTS.md`
  (machine contracts bullet), `.omc/config/AGENTS.md` (this repo's own
  project guidance), `.omc/skills/review/SKILL.md`,
  `.omc/skills/explain-context/SKILL.md` (currently lists only three). The
  `internal.py` module docstring's exit-code list becomes 0 ok, 1 error,
  2 usage, 3 bail — 1 was already practised (`_rebase_main` fetch failure)
  but undocumented.
- **README**: `--reset-gitnexus` in the watch row and prose; `--once`
  wording; a paragraph on the start alert and the `OMC_KNOWLEDGE` line.

## Machine contract

`OMC_KNOWLEDGE {json}` — single line, JSON object, keys always present:

```json
{"fresh": false,
 "basis": "origin/main",
 "reasons": [{"code": "index-behind", "text": "index is 31 commits behind origin/main", "detail": {"count": 31}}],
 "fix": "omc watch --once --enable-documentation",
 "run_in": "/home/openclaw/Projects/creditosya"}
```

Fresh form: `{"fresh": true, "basis": "origin/main", "reasons": [], "fix": "",
"run_in": "/path"}`. Emitted on stdout by `refresh`/`status`, on stderr by the
proxy, embedded in the start seed, and as the `knowledge` field of
`OMC_REBASE_MAIN`. Parsers use the `_parse_stage` regex idiom (line-anchored
prefix, tolerant of markdown wrapping, last match wins); emitters never wrap
it.

## Verification

Project policy: red → green for every change; no skips; stubs on a
restricted PATH use shell builtins or absolute paths; assert on artifacts.

Unit (`just check` tier):

- `tests/unit/test_gitnexus_freshness.py` — synthetic metadata in a real
  throwaway repo with an origin: one test per reason (behind, diverged,
  unknown, wiki-behind, wiki-unknown need real commits); all-reasons-reported
  and the inverted-skips-distances rule; `ref="HEAD"` vs `origin/<base>`;
  unresolved ref → `basis: "unresolved"` and no distance codes;
  `gitnexus.json` preferred and `meta.json` ignored; `documentation=False`
  skips wiki checks; `repoPath` realpath/symlink alias not foreign; missing
  `repoPath` not foreign; `fix`/`run_in`; `to_json` shape.
- `tests/unit/test_watch.py` — `_repo_with_origin` seeds fresh metadata.
  BOTH `node` stubs (`_ctx_with_node_stub` and the existing
  `_ctx_with_healing_node_stub`) gain `case "$*"` branches: `analyze` writes
  fresh metadata (`lastCommit` from `/usr/bin/git rev-parse HEAD` — a real
  SHA, never a placeholder like `"new"` which reads as `index-unknown`;
  `branch` — which stays the healing stub's `analyze_stamps` knob so
  `test_heal_wrong_stamp_never_claims_success` keeps stamping
  `feature/omc-v1`; `repoPath="$PWD"`), `clean` removes `.gitnexus`, `wiki`
  writes `.gitnexus/wiki/meta.json` with `fromCommit` = HEAD plus one page.
  Tests that monkeypatch `watch_mod.clear_docs_mirror`
  (`test_heal_survives_an_undeletable_docs_mirror`) retarget
  `gitnexus.clear_docs_mirror`. `test_once_refreshes_index_even_when_up_to_date`
  and `test_once_with_documentation_refreshes_docs_even_when_up_to_date` are
  rewritten: each seeds STALE metadata (index behind / wiki behind) and keeps
  its argv assertion; a sibling asserts that `--once` on the fresh fixture
  records no `node` call and narrates `✓ knowledge is current`. New tests: stale index on an up-to-date tick
  → `· up to date` then analyze recorded, token `healed`, post-watch hook NOT
  run; analyze that leaves metadata stale escalates to `clean --force` then
  analyze; a still stale verdict yields `knowledge-stale:<codes>` and the
  NEXT up-to-date tick records no node call; a pending reset on a FRESH
  up-to-date tick still runs clean then analyze; `--reset-gitnexus` records
  clean then analyze and removes the docs mirror, consumed after the sync
  (one analyze at new HEAD); `--reset-gitnexus` off the base branch exits 1
  with no `analyze`/`clean` call and before the instance lock exists; wiki
  reasons produce no narration without `--enable-documentation`; wiki still
  behind after the run leaves the mirror untouched; `--once` on a fresh
  verdict narrates `✓ knowledge is current` and still fires the hook; busy
  lock held by a subprocess (`_mutexproc.py` doctrine) → waiting line
  narrated once.
- `tests/unit/test_start_mutex.py` (`WAIT_LINE`) and
  `tests/unit/test_watchlock.py` update their exact-match of
  `START_WAIT_MSG`.
- `tests/unit/test_internal.py` — `refresh` blocks while the busy lock is
  held by a subprocess and proceeds after release; measures against HEAD and
  never fetches (no `fetch` in the argv recorded by a FORWARDING git stub
  that logs then `exec /usr/bin/git "$@"` — the verdict needs real git, and
  absolute-path exec is allowed under the stub rules); prints the
  behind-origin hint when the local `origin/<base>` is ahead; rc 3 when
  still stale; refuses off-branch; `status` prints the verdict without
  invoking `node`;
  rebase-main payload carries `knowledge` in all three shapes; proxy prints
  the verdict on stderr only when stale and never on the `--git` path.
- `tests/unit/test_start.py` — stale verdict → alert block on stderr and
  the `OMC_KNOWLEDGE` line before the framing sentence with the context JSON
  still last; `index-missing` two-line form; fresh → neither and the seed is
  byte-identical to today; `primary_root` None → no verdict; dry-run row.
- `tests/unit/test_dependency.py` unchanged (re-export contract);
  `tests/unit/test_wikirun.py` covers `PageCountTracker` in its new home.
- Skill contract needles (`test_plugin_manifests.py`): new commands in
  `gitnexus-index`/`gitnexus-document` (old `--skip-agents-md`, `--provider`,
  `.gitnexus/wiki`, `openai` needles retired), `OMC_KNOWLEDGE` in
  `rebase-main`/`gitnexus-explain`/`start`/`plan`/`explain`, the
  distribution AGENTS.md contract bullet.

E2E (Docker, real GitNexus cloned from the fork, `require_token("claude")`
gated like the existing wiki test), beside
`test_watch_once_heals_feature_branch_owned_index`: seed a wiki whose
metadata is several commits behind with a one-module tree, commit more than
five new files spread over clearly distinct concerns (so the LLM grouping
reliably yields several modules), run `omc watch --once
--enable-documentation`, assert the tree has more than one module, the docs
mirror is non-empty, and `omc internal gitnexus status` reports fresh. The
E2E image pins the fork's GitNexus at the PR #4 merge commit via a
`GITNEXUS_REF` build argument in `docker/Dockerfile.e2e`, so a cached layer
can never carry the pre-fix generator.
Existing `test_document_generates_wiki_docs` and
`test_index_then_explain_on_real_repo` assert only on artifacts and survive
the skill delegation.

Manual acceptance on creditosya: `omc start` prints the alert; `omc watch
--once --enable-documentation` heals without hand deletion; a second run
narrates `✓ knowledge is current` and does nothing.

## Out of scope

- Coverage heuristics and any wiki-unpin workaround (fixed in the fork).
- Cross-machine transport of `.gitnexus/`; `index-foreign` only detects.
- Syncing the fork with the original GitNexus repository, or any change to
  that repository.
- Deleting docs on a threshold: the verdict travels with every consumer
  instead.
