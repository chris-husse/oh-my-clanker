# Fix the false stall in documentation runs: GitNexus reports progress, omc listens

**Date:** 2026-10-01
**Slug:** `fix-doc-false-stall-gitnexus-progress`
**Status:** design, hardened with `/omc:explain` and the grug lens

## 1. Problem

Documentation runs on large repos die as a "stall" while GitNexus is
healthy and working. On the hummingbird primary (9,230 files, 60k graph
nodes, API docs backend with `claude-opus-5-5`) the run was killed with
`✗ wiki stalled — no progress for 300s; killed`, leaving no module tree and
no pages.

`ToolContext.run_supervised` (`src/omc/toolctx.py`) kills the
`gitnexus wiki` child after `_WIKI_STALL_SECONDS` (300 s,
`src/omc/wikirun.py`) with **no heartbeat change and no output bytes**.
Both wiki call sites use it: `_run_wiki` (`src/omc/gitnexus.py`, the project
wiki — the hummingbird case) and `run_document` (`src/omc/dependency.py`,
dependency docs).

## 2. Root cause

Two facts combine into total silence during GitNexus's grouping phase:

1. **Nothing reaches disk.** Grouping (`generator.ts` `batchedGrouping`)
   splits a repo above `GROUPING_TOKEN_BUDGET` (100k tokens) into
   sequential LLM calls, one per directory chunk, and writes
   `first_module_tree.json` only when the whole phase ends. omc's
   `PageCountTracker` heartbeat reads that file, so it is indeterminate for
   the entire phase.
2. **Nothing reaches the pipe.** GitNexus's progress bar
   (`src/cli/wiki.ts`, cli-progress 3.12, default options, stream stderr)
   returns from `start()` when stderr is not a TTY. Every `onProgress`
   event — per batch, per streamed chunk, the one-second elapsed tick —
   is routed only into that bar and discarded when piped.

**The silence clock is per phase, not per call.** Twenty grouping batches of
60 s each are twenty silent minutes and a kill, with no single call near
300 s. The kill proves the phase was silent; it says nothing about how fast
any model answers. The 2026-07-23 design record that introduced the guard
(`fix-dependency-watch-never-completes`) sized 300 s against per-page
cadence on a 22-page wiki and assumed "single calls like grouping run well
under it" — true per call, irrelevant when a phase of many calls emits
nothing.

## 3. Goals and non-goals

Goals:

- omc never kills a GitNexus wiki child that is alive and working,
  regardless of model, repo size, or backend.
- A run wedged outside an LLM call still fails within a bounded time; a
  hung LLM call is the accepted exception (§4.1.3, §6).
- The user sees grouping progress instead of an indeterminate bar for the
  first 40 minutes of a large run.
- Both wiki call sites (project wiki, dependency docs) benefit.

Non-goals:

- Tuning the window per model. Liveness must not depend on model speed.
- Any change to indexing (`gitnexus analyze`); omc never runs
  `gitnexus index`.
- Opus cost of a 9k-file run (user's call; the docs floor stays sonnet).
- The stale `omc watch --enable-documentation` from 2026-09-30 still
  running in the oh-my-clanker primary (Ctrl-C there before any new
  documentation run).

## 4. Design

The fix lives in both repos. GitNexus speaks while it works; omc listens
without ever parsing prose for liveness.

### 4.1 GitNexus side (fork `chris-husse/GitNexus`, separate PR, lands first)

**4.1.1 Off-TTY progress lines.** In `src/cli/wiki.ts`, when
`process.stderr.isTTY !== true`, the command does not start the
cli-progress bar. The `onProgress` callback instead writes one
newline-terminated line to stderr:

```
GITNEXUS_PROGRESS {"phase":"grouping","percent":17,"detail":"Grouping batch 2/23 (LLM)..."}
```

A line is written only when the `(percent, detail)` pair changed, and
`stream` chunk events are skipped entirely: they only move a token-count
label, never the percent, and the heartbeat (4.1.2) already covers liveness
during a call. The TTY path is untouched. Always on when piped, no flag:
today a piped `gitnexus wiki` is silent, so lines are strictly an
improvement, and a flag omc would have to pass is one more thing to drift.
The prefixed single-line JSON shape mirrors omc's own machine contracts, and
coexists with the pino JSON records GitNexus already writes to stderr when
piped (`src/core/logger.ts`): consumers match on the prefix.

**4.1.2 In-flight heartbeat in the generator's LLM wrapper.** The
generator's `invokeLLM` method (`src/core/wiki/generator.ts`) is the single
point every grouping and page call passes through, and its `await` spans
the client's retries and backoff. It starts an unref'd 30 s interval before
the call and clears it in `finally`; each tick reports
`onProgress('heartbeat', this.lastPercent, `${label} (${seconds}s)`)`,
which 4.1.1 prints like any other change. No change to `CallLLMOptions` or
to either LLM client.

This is the same shape as the fork's existing unref'd 60 s `touchWikiDb`
keepalive in `generator.run()` (commit 0af36e28; record
`2026-07-17-fix-wiki-ladybugdb-not-initialized-design.md`: local agent CLIs
buffer stdout until exit, so no streaming callback can be relied on for
liveness — a timer does the job).

The heartbeat is **scoped to LLM calls on purpose**. Outside a call GitNexus
goes quiet, and omc's 300 s silence rule still catches a wedged graph query
or a stuck disk write. Inside a call the heartbeat keeps omc off GitNexus's
back; a call that hangs is not bounded by anyone (4.1.3), by user decision.
That split is what lets omc stop guessing.

**4.1.3 No request timeout.** No GitNexus change, and omc passes no
`--timeout`. GitNexus does not fail a timed-out or failed LLM call: a failed
grouping batch falls back to directory grouping for the whole repo
(`generator.ts` `batchedGrouping` catch), a failed page is pushed to
`failedModules` and reported as `Failed: <name>`, and the run exits 0. The
local CLI client has no retries. A `--timeout` would therefore turn a hung
call into a silently degraded wiki that omc marks fresh. A hung LLM call is
deliberately left unbounded by omc — user decision 2026-10-02, after the
whole-branch review found this behaviour; GitNexus heartbeats through it and
the operator ends it with Ctrl-C (§6). The 300 s window (4.2.3) still bounds
silence outside LLM calls.

**4.1.4 Tests (fork).** Reporter: one line per changed pair, no line for
`stream` events, nothing on a TTY, no bar off a TTY. Heartbeat: the
generator wrapper ticks during an in-flight call and not before or after,
fake-clock pattern from `test/unit/wiki-keepalive.test.ts`.

### 4.2 omc side (this branch)

**4.2.1 `run_supervised` gains `on_line`.** Optional
`on_line: Callable[[str], None] | None = None` on `ToolContext.run_supervised`.
The pump threads already read whole lines; each line is handed to `on_line`
under one lock — the serialization `stream()` already uses — and callback
exceptions are swallowed like heartbeat exceptions, so a reporter bug can
never kill a child or crash the supervisor. That is a deliberate difference
from `stream()`, which re-raises callback failures after reaping, and the
comment at the site says so. Default `None` keeps every existing caller
byte-identical. Liveness semantics do not change: bytes on either pipe
still reset the clock. `_WIKI_STALL_SECONDS` (300.0) and
`_WIKI_POLL_SECONDS` (1.0) keep their names and values.

**4.2.2 `GitNexusProgress` in `wikirun.py`.** One small class shaped like
`buildprogress.ProgressTracker`: `feed(line)` matches the
`GITNEXUS_PROGRESS ` prefix, decodes the JSON, and stores `percent` only when
it is an int in 0..100 (the same eight lines `depwatch._DocumentJob.feed`
uses for `OMC_PROGRESS`; anything else is ignored, nothing raises);
`percent` is `None` until the first accepted line; `render(now=None)` goes
through the shared `render_bar`, so `BarThread` can drive it unchanged. No
clamp: with `stream` events dropped in 4.1.1, GitNexus's whole-run percent
ascends by construction. Single attribute writes under the GIL make it
thread-tolerant in the same documented way `ProgressTracker` is: fed on a
pump thread, read on the supervising or bar thread.

Precedence is a one-liner at each site: GitNexus's percent when it is not
`None`, else `PageCountTracker.percent`. Before the first line that is
today's behavior for `OMC_PROGRESS`, and what an older GitNexus still gets.
The tracker keeps its other two jobs unchanged: the resume announcement and
the disk heartbeat.

**4.2.3 The stall window.** `_WIKI_STALL_SECONDS = 300.0` stays in
`wikirun.py` as a plain literal. GitNexus heartbeats on stderr every 30 s
while an LLM call is in flight (4.1.2), so 300 s of silence means a wedge
**outside** any LLM call — a graph query, a disk write — which is exactly
what the guard should kill. A hung LLM call itself is not bounded (4.1.3);
the comment at the constant says why and cites the 2026-10-02 decision.
`DocsRun.wiki_args` is unchanged from main, as the 2026-07-23 record left it.

Both sites share one progress precedence: GitNexus's percent once it has
spoken, the disk page count before that. The dependency path applies it when
emitting `OMC_PROGRESS` (4.2.4); the project-wiki bar gets it by handing
`GitNexusProgress` the `PageCountTracker` as a render-only fallback (4.2.5).
The holder's raw `percent` stays GitNexus-only, which is what the dependency
path's precedence relies on.

**4.2.4 `run_document` (dependency docs).** Passes `on_line` feeding a
`GitNexusProgress`; the existing one-second `_beat` on the supervising thread
keeps being the only emitter of `OMC_PROGRESS {"percent": N}` on stdout,
reading the authoritative percent (4.2.2) and printing when it moves. The
stdout contract is unchanged: same line shape, same consumer
(`depwatch.py`), same final verdict. The disk `_beat` stays as the heartbeat.

**4.2.5 `_run_wiki` (project wiki).** The stall fix applies here with no
code change: bytes are liveness. For visibility it also passes `on_line`
into a `GitNexusProgress` and hands that to the existing TTY-gated
`BarThread` (`src/omc/cli/progress_bar.py`), the same way `watch.py`'s
auto-build does with `ProgressTracker`, stopping the bar before the success
or failure line is narrated. On a non-TTY the bar is a no-op and, following
the project rule that a silent minute is a bug and `depwatch`'s precedent of
narrating headless runs, `_run_wiki` instead narrates each GitNexus **phase
change** as a `· <detail>` line through `say` — heartbeat lines excluded:
one line per GitNexus phase change, which is a handful of fixed phases, one
per grouping batch, and one per module page, so a 60-module wiki narrates
roughly 70 lines. On a TTY the bar shows the page count until GitNexus speaks
(4.2.3), so an older GitNexus that prints no lines still gets a moving bar
instead of an indeterminate one for the whole run. That makes
headless `omc watch` logs readable and gives the CLI's stderr an artifact
the real-tool E2E can assert (§7). `refresh_knowledge` is also reached from
`internal._knowledge_refresh` inside a Claude session, where stderr is not a
TTY and the same narration applies.

**4.2.6 Error handling.** Malformed or unexpected lines are ignored. Lines
are stderr-only, so no stdout machine contract can collide. GitNexus's
stderr is consumed inside `run_supervised` and never reaches the dependency
logs; those keep logging only the `omc internal dependency document`
child's own lines, exactly as today. Stall and non-zero-exit handling at
both sites is unchanged, including redact-before-truncate, with one
adjustment: the 400-char failure excerpt is the **tail** of the redacted
text rather than the head, so the error — which GitNexus prints last — is
what the user sees, not the pino records and progress lines that precede
it. `detail` carries module names and paths, never credentials.

### 4.3 Sequencing and acceptance

1. GitNexus fork PR merges to main.
2. omc PR merges. omc unit tests use fake children, never the real GitNexus.
3. `omc update` on the host pulls both (`update_gitnexus` fetches, fast-forwards
   main, rebuilds). The E2E image pins a fork commit
   (`docker/Dockerfile.e2e`, `ARG GITNEXUS_REF`); the omc PR bumps it to
   the fork PR's merge commit, which rebuilds the image layer. The bump
   waits for fork PR #5 to merge, and the omc merge waits for the bump.
4. Live acceptance: document hummingbird with api + opus and see batch lines
   within the first minute; then `omc dependency document` on a mid-sized
   dependency to confirm the `OMC_PROGRESS` path.

No version gate in omc: an older GitNexus emits no lines and omc behaves
exactly as today.

## 5. Decisions taken during brainstorm

| # | Decision | Why |
| --- | --- | --- |
| 1 | Fix in both GitNexus and omc | GitNexus is the only party that knows it is waiting on a model; omc is the only party that renders. |
| 2 | omc does not bound a hung LLM call; it keeps only the 300 s silence window, which the heartbeat scopes to wedges outside LLM calls. omc never parses GitNexus prose for liveness | User decision 2026-10-02 after learning GitNexus degrades rather than fails a timed-out call. |
| 3 | omc surfaces grouping progress, from structured lines only | User asked for it "if it works reliably"; regexing a human bar string is not reliable. |
| 4 | Any model must be supported; no per-model window | User requirement. Liveness comes from the heartbeat, not from model speed. |
| 5 | Rejected `--timeout` (first 1200 s, then 300 s) | GitNexus turns a timed-out call into a silently degraded wiki with exit 0. |
| 6 | Off-TTY lines always on, no flag | Piped runs are silent today; a flag is drift surface. |
| 7 | Heartbeat 30 s, window 300 s | 10× margin; nothing to tune per model. |
| 8 | Project-wiki bar included (4.2.5) | Same complaint class as the 2026-07-23 work; plumbing shared with 4.2.4. |
| 9 | Rejected: cli-progress `noTTYOutput` | Ticks regardless of generator state (masks all wedges), ~1200 log lines per run, prose to parse. |
| 10 | Rejected: omc-only wider or phase-aware window | Still a model-speed guess; no progress for the user. |

## 6. Risks and open points

- **Non-LLM silence over 300 s.** A graph query or HTML build on a very
  large repo that stays silent for five minutes is still killed. Accepted:
  the hummingbird run proved gather finished well inside the window, and
  `onProgress` events during gather produce lines.
- **A hung LLM call is unbounded.** GitNexus heartbeats through it and omc
  never kills it; the operator sees progress stop moving (the heartbeat's
  label only counts the seconds of one call up; on a TTY the bar's elapsed
  clock runs while the percent stays put; narration excludes heartbeats, so
  off a TTY the last phase line going stale is the clue) and ends it with
  Ctrl-C. Accepted by the user 2026-10-02 in preference to
  GitNexus's silent degradation.
- **Two repos, one behavior.** Until `omc update` runs, an omc with 4.2 and
  a GitNexus without 4.1 behaves as today apart from the excerpt tail and
  the page-count bar on a TTY (no lines, no gain, no
  regression). The E2E image follows the `ARG GITNEXUS_REF` bump in the omc
  PR, so it cannot silently keep testing the old GitNexus. The bump waits
  for fork PR #5 to merge; until then the omc merge is held.

## 7. Testing

omc (`tests/unit`):

- `test_toolctx.py`: `on_line` receives whole lines from both pipes,
  serialized; a raising callback neither kills an active child nor crashes
  the supervisor; existing seven supervised tests unchanged.
- `test_wikirun.py`: `GitNexusProgress.feed` accepts a valid line and
  ignores wrong prefix, bad JSON, missing or out-of-range percent; `percent`
  is `None` before the first line; `render()` matches `render_bar`; with a
  `PageCountTracker` fallback the bar shows the disk page count while the raw
  `percent` stays `None`, and GitNexus's percent wins once a line arrives; the
  existing constants assertion (300.0 / 1.0, dependency re-export) is
  unchanged.
- `test_dependency.py`: a fake child script prints `GITNEXUS_PROGRESS`
  lines to stderr and `run_document` emits matching `OMC_PROGRESS` lines;
  GitNexus percent takes precedence over the tracker once seen; the existing
  stall-kill test still passes.
- `tests/unit/test_gitnexus_refresh.py`: the documentation step feeds the
  bar on a TTY and narrates `·` phase lines, heartbeats excluded, otherwise
  (these pins live here, not in `test_watch.py`).

omc (`tests/e2e`): `test_document_api_backend_generates_wiki_docs`
(`test_e2e_gitnexus.py`) already drives the real GitNexus through `_run_wiki`
against the real Anthropic endpoint; it gains one assertion that the refresh
output carries a `·` phase line from GitNexus, proving reporter, `on_line`
and holder end to end with no extra LLM run. The plan checks whether that
test carries the `expensive` marker CI excludes.

GitNexus (fork `test/unit`): per 4.1.4.

## 8. Hardening notes

**§4.1 (explain + grug).** Explain corrected two claims: GitNexus's stderr
never reaches dependency logs (it is consumed inside `run_supervised`), and
the E2E image pins a fork commit via `ARG GITNEXUS_REF` that the omc PR must
bump. It also flagged that the 400-char failure excerpt could now start with
progress lines; 4.2.6 takes the tail instead (settled in the §4.2 pass). Grug found three
Important simplifications, all applied: the heartbeat lives in the
generator's single `invokeLLM` wrapper instead of a new `CallLLMOptions` field
plus an interval in each LLM client (locality); `stream` events are skipped
off-TTY instead of throttled (80/20); `elapsedMs` is dropped as a field with
no consumer (say no).

**§4.2 (explain + grug).** Explain corrected the timeout arithmetic: the
API client's `AbortSignal.timeout` is created once per call and spans all
retries, so a hung call fails after 300 s, not after three attempts. It
also pointed at `buildprogress.ProgressTracker` as the documented
thread-tolerant precedent for a holder fed on a pump thread and rendered by
`BarThread`. Grug found three Important simplifications, all applied: the
monotonic clamp is gone (nothing can walk the percent backwards once
`stream` lines are dropped); parser, holder and "precedence rule" collapsed
into one `ProgressTracker`-shaped class with precedence as a one-liner at
each site; the failure excerpt takes the tail instead of filtering one
prefix.

**§4.3 (explain + grug).** Explain confirmed delivery mechanics (host clone
at `8de99dc9` equals fork main, so `omc update` short-circuits until the fork
PR lands; `ARG GITNEXUS_REF` precedes `COPY . /repo`, so the bump rebuilds
one layer onward) and found the real-tool E2E for this exact path, which had
no artifact for progress; the fix is non-TTY `·` phase narration in
`_run_wiki`, which the project's own narration rule asks for anyway. Grug
raised one Important finding — two renderers for the project wiki — waived
by brainstorm decision 8 (see Deliberate complexity).

**Final review (2026-10-02).** The whole-branch review found that the
branch's `--timeout 300` rested on a false premise: verified in
`generator.ts` and `wiki.ts`, GitNexus does not fail a timed-out call — a
grouping batch falls back to directory grouping, a page lands in
`failedModules` as `Failed: <name>`, and the run exits 0 — so the flag would
have traded a loud kill for a silently degraded wiki marked fresh. The user
decided to remove it and accept that a hung LLM call is unbounded (4.1.3,
4.2.3, decisions 2 and 5, §6); the §4.2 timeout arithmetic above is
superseded. Minor 3 fixed: an older GitNexus on a TTY bounced the project-wiki
bar indeterminate for the whole run, so the bar now falls back to the page
count (4.2.5). Minor 6 fixed in text: the narration count (one line per page,
not "about a dozen") and the test file that pins the bar/narration (§7).
Merge ordering: the omc merge is held until fork PR #5 merges and
`ARG GITNEXUS_REF` is bumped to its merge commit.

## Deliberate complexity

- **Two renderers for the project wiki** (`grug:80-20` @ §4.2.5): a
  `BarThread` on a TTY plus `·` phase narration off a TTY, where phase lines
  alone would do on both. Waived by brainstorm decision 8: the user chose the
  bar, and it matches what dependency watch already renders on a TTY.
