# Design: dependency watch liveness — progress bar, resume visibility, stall guard

- **Date**: 2026-07-23
- **Slug**: fix-dependency-watch-never-completes
- **Status**: approved design (brainstorm converged; **v2 display architecture**
  after PR #19 — parallel documents — landed on main mid-build, see
  "Display architecture (v2)")

## Problem

`omc dependency watch` reads as "never completes". Verified live on the machine
that reported it: nothing hangs — documenting a dependency runs `gitnexus wiki`,
which writes one LLM-generated page per module (79 and 96 modules for the two
cached dependencies, ~14 s/page on the Sonnet docs model, parent modules
sequential), so a first full pass over two large repos is ~40 minutes of honest
work. The user cannot see any of it:

- `depwatch._spawn` runs `omc internal dependency document` through `ctx.run`
  (captured). One `→` line, then silence until the pass ends.
- GitNexus's own progress bar (cli-progress) is TTY-gated and emits **nothing**
  through a pipe — streaming the child's output would not surface progress.
- Interrupted runs resume at page level (existing `.md` pages are skipped;
  `meta.json` lands only at the very end), but nothing announces that, so every
  impatient Ctrl-C looks like starting over. The reporting user was at 89/96
  and 47/79 pages when they gave up.
- No timeout exists anywhere in the chain (`ctx.run` → `gitnexus wiki` →
  `claude -p`): one genuinely wedged LLM call would hang a pass forever.

The reference expectation ("/omc:document finishes in minutes") comes from the
project wiki being 22 pages and from incremental regeneration after `meta.json`
exists. Dependency wikis are 4–8× larger and always run full generation per
pinned commit.

## Scope

1. **Progress bar** for dependency documentation, reusing the build-stage bar
   concept as a shared CLI component.
2. **Resume announcement** — one plain line when pages already exist:
   `· resuming — 47/79 pages already on disk`.
3. **Stall guard** — no fixed overall timeout (generation legitimately runs
   tens of minutes); instead, kill the child only after **300 s with no
   progress**.

**Deferred** (separate design, not confirmed by the user): seeding a new
commit's wiki from the same dependency's previous commit so re-documenting a
giant repo costs incremental time like `/omc:document`. Also deferred: a bar
for the `ensure` phase (clone+index, no LLM), and the unrelated observation
that the `/omc:document` skill prose picked the session model (`claude-fable-5`)
for a project wiki run despite the docs-model rule (skill prose fix, separate
change).

## Component: `src/omc/cli/progress_bar.py`

The bar is a CLI presentation utility and lives in its own module. `cli.py`
moves to `cli/__init__.py` (the `omc.cli:main` entry point and every
`from omc.cli import …` site keep working, and `src/omc/` already uses
subpackages — `config/`, `providers/`, `shells/`). NOT verbatim: every
single-dot relative import inside `cli.py` — the module-top ones and the
~10 lazy in-function ones (`from .depwatch import …`, `from .internal import
…`, …) — must become double-dot (`from ..depwatch import …`), since the file
moves one package level down. `hatch_build.py` and `pyproject.toml` need no
change beyond that. The component exposes:

- `render_bar(percent: int | None, elapsed: float, *, width=18, spin=0) -> str`
  (`spin` is the caller's redraw counter, advancing the bounce position in
  indeterminate mode)
  — the pure rendering core, extracted from today's
  `buildprogress.ProgressTracker.render`. **One uniform format for every
  mode**: `[====>       ]  30% (00:12:34)`; indeterminate (percent None)
  keeps the bouncing `<=>` marker: `[   <=>      ]  --% (00:12:34)`.
  Counts (`11/43`) are **internal only** — they feed the percent, they are
  not rendered.
- `BarThread` — moved from `watch.py` unchanged in spirit: 1 s redraw thread,
  TTY-gated on construction (non-TTY → no-op), `\r` in-place on stderr,
  clears the line on stop. One addition: it calls `tracker.refresh()` (no-op
  default) before each redraw, so polling trackers share the redraw beat.
- Tracker protocol: anything with `refresh() -> None` and
  `render(now=None) -> str`. `buildprogress.ProgressTracker` (line-fed build
  parsers — its parsers stay in `buildprogress.py`, that is build domain) and
  the new page-count tracker both satisfy it; both delegate rendering to
  `render_bar`.

`watch.py` imports `BarThread` from the component; `buildprogress.py` keeps
`follow_log`, the parser registry, and the sentinel (build domain), losing only
the rendering core.

## Progress source: disk, not child output

`PageCountTracker` (lives in `src/omc/dependency.py` — it is wiki-domain, the
component stays generic): constructed with the checkout's `.gitnexus/wiki`
directory. `refresh()` reads `first_module_tree.json` (recursive module count,
+1 for the final overview page) as the total and counts `*.md` files as done;
exposes `percent` (None until the tree snapshot exists — the grouping phase
then renders as indeterminate) and a `state` token for the stall guard. All
I/O is OSError/JSONDecodeError-contained: malformed or missing state degrades
to indeterminate, never raises (watch doctrine: degrade, never crash the
loop). Provider-agnostic by construction — no dependence on gitnexus output
formats.

## Stall guard: `ToolContext.run_supervised`

New method on the single subprocess boundary (`toolctx.py`):

```
run_supervised(argv, *, heartbeat: Callable[[], object], stall_after: float = 300,
               cwd=None, extra_env=None) -> tuple[CompletedProcess, bool]
```

- `Popen` with `start_new_session=True`; stdout/stderr captured by two reader
  threads (the `stream()` two-pipe doctrine: never merge pipes).
  `os.killpg` is POSIX-only — consistent with the rest of `src/omc/`, which
  contains no win32 handling anywhere (darwin/linux is the codebase status
  quo; E2E is Docker).
- Supervisor loop (1 s): progress = the `heartbeat()` token changed **or**
  output bytes arrived. Any progress resets the stall clock; `stall_after`
  seconds without it → `os.killpg` the process group, reap, return
  `(completed, stalled=True)`.
- No overall deadline — a healthy 40-minute run never trips it. The 300 s
  window is generous against measured page cadence (~14 s/page; single calls
  like grouping/overview run well under it).

`run_document` passes `heartbeat=tracker.state` (tree existence + page count),
so the same disk polls drive both the bar and the guard. No `--timeout` is
passed to gitnexus (its per-request timeout stays disabled — the stall guard
supersedes it and also covers non-LLM wedges). On a stall kill,
`run_document` prints `error: gitnexus wiki stalled — no progress for 300s;
killed` and exits 1; the watch marks the action failed and retries next tick,
resuming from the pages already on disk.

## Wiring

**`run_document`** (`src/omc/dependency.py`), between the index guard and the
wiki spawn: build `PageCountTracker`; if it sees K > 0 of M pages, print the
resume line (plain stderr line — lands in logs even without a TTY); start
`BarThread` (TTY-gated on stderr); run gitnexus via `run_supervised`; stop the
bar before printing success/error. Everything after (mirror, manifest flip,
`OMC_DEPENDENCY` verdict on stdout) is unchanged.

**`depwatch._spawn`** (`src/omc/depwatch.py`): document actions run with
**stderr passed through** so the child's resume line, bar redraws, and error
text reach the terminal live; stdout stays captured (the `OMC_DEPENDENCY`
verdict contract). `ToolContext.run` learns `capture="stdout"`
(stdout=PIPE, stderr=None/inherit, stdin=DEVNULL). The failure line for
document actions becomes `✗ failed (exit N) — see output above` (stderr is no
longer in hand). `ensure` actions stay fully captured as today. Non-TTY watch
(logs/CI) is unchanged except the resume line: the bar self-disables.

Direct callers (`omc internal dependency document` in a terminal) get the bar
for free; headless/skill callers (captured, non-TTY) see today's behavior.

## Testing

Unit tests, same idioms as `tests/unit/test_buildprogress.py` /
`test_depwatch.py`:

- `render_bar` golden strings: 0%, 30%, 100%, indeterminate; uniform
  time-suffix format.
- `PageCountTracker` against tmp dirs: no tree → indeterminate; tree + K
  pages → correct percent and state token; corrupt tree JSON → indeterminate,
  no raise; overview counted in the total.
- `run_supervised` with a scripted child: heartbeat progress keeps it alive;
  frozen heartbeat + silent child → killed, `stalled=True`, process group
  gone; output bytes alone count as progress.
- `run_document` wiring (monkeypatched ctx): resume line printed only when
  pages pre-exist; bar constructed TTY-only; stall kill → exit 1 and no
  manifest flip.
- `ctx.run(capture="stdout")`: verdict parsed from stdout, stderr fd
  inherited.
- `depwatch._spawn`: document passthrough vs ensure captured.

No live-LLM tests; E2E stays Docker-per-test doctrine, out of scope here.

## Display architecture (v2 — user decision, 2026-07-23)

Mid-build, main gained PR #19: `depwatch._document_batch` documents up to 8
dependencies in parallel. The v1 display (each `document` child renders its
own TTY bar; the watch passes stderr through) cannot survive parallelism —
N children repainting one terminal line is garbage. The user's decision
replaces it with a strict reporter/renderer split; **rendering is not
`run_document`'s business**:

- **Document jobs REPORT, never render.** Each `omc internal dependency
  document` run reports its progress as a machine line on stdout —
  `OMC_PROGRESS {"percent": N}` (0–100, single-line JSON, same contract
  family as `OMC_DEPENDENCY`) — emitted when the integer percent changes
  (driven by the same disk polls the stall-guard heartbeat already does),
  once up front when a resumed percent is already known, and a final
  `{"percent": 100}` on success before the verdict. The percent computation
  (PageCountTracker) stays a dependency-internal detail; nothing about wiki
  layout leaks to callers. The child also keeps the resume line and the
  stall guard. `PageCountTracker` loses its `render()` — it is a pure data
  source now.
- **The watch renders.** `_document_batch` owns display: per job it tees the
  child's full output to a log file (announced, tail -f-able — the "full
  context" channel), parses `OMC_PROGRESS` lines into a per-job percent, and
  holds the exit code via the pool future. On a TTY it renders **one bar
  line per dependency** (uniform — no single-vs-multi special case) as an
  in-place block: `<ref> [====>       ]  42% (00:03:10)`, indeterminate
  bounce until the first report. Non-TTY: no bar bytes, per-job start line
  (`→ … — log: <path>`) and final `✓`/`✗ (exit N) — log: <path>` lines only.
- **Component**: `MultiBarThread` joins `BarThread` in
  `omc.cli.progress_bar` — same doctrine (1 s beat, TTY-gated no-op,
  clears its block on stop so narration owns permanent output), repainting
  a fixed-height block of caller-rendered lines via cursor-up ANSI.
- **Superseded and removed**: the v1 stderr passthrough (`_spawn(...,
  passthrough=True)` and `ToolContext.run(capture="stdout")`) — document
  children are fully piped again; the machine contract (verdict AND progress
  on stdout) survives capture by construction.

## Hardening notes (explain pass, 2026-07-23)

- Golden render strings exist at `tests/unit/test_buildprogress.py:65,71`
  (`[====>             ]  21% (13:33:22)`); the `render_bar` extraction must
  preserve that exact spacing — the uniform-format decision keeps those
  goldens valid unchanged.
- Widening `ToolContext.run`'s `capture` to `bool | Literal["stdout"]` is
  additive: the only existing non-default callers pass `capture=False`
  (`internal.py:141,149`, `installer.py:38`) or `capture=True`
  (`toolctx.py:tool_version`).
- No omc code reads `first_module_tree.json` today — `PageCountTracker` is
  its first reader; it is read-only and does not interact with `mirror_dir`
  (which runs after wiki success).
- No circular import: nothing reachable from `cli/__init__.py`'s module-top
  imports (`config`, `errors`, `start` → `notify`/`worktree`/`agentsmd`/
  `plugin`, `toolctx`) imports `watch`/`depwatch`/`dependency`. Importing
  `omc.cli.progress_bar` from `watch.py`/`dependency.py` executes
  `cli/__init__.py` first (Python package semantics) — keep `progress_bar.py`
  itself dependency-free (stdlib only).

## Follow-ups recorded, not designed

- Incremental wiki seeding across commits of the same dependency.
- `ensure`-phase progress (indeterminate bar over clone+index).
- `/omc:document` skill prose: enforce the docs-model rule (project wiki ran
  on the session model today).
