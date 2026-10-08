# oh-my-clanker — project agent instructions

(Read after omc's global behavior layer when working in this repository.)
Deeper truth map: `.omc/skills/explain-context/SKILL.md`.
`README.md` is generated: change `.claude/skills/regenerate-readme/SKILL.md` or its sources and run `/regenerate-readme`; never hand-edit it.

This repo is a uv-installed Python CLI (`src/omc/`) plus a skills plugin
(`skills/`, installable in Claude Code / Codex from this repo).
Design records live in `docs/superpowers/specs/`; the build ledger in
`.superpowers/sdd/progress.md`.

## Testing policy — REQUIRED, no exceptions

### Red → green for EVERY change

No feature, fix, or behavior change lands without validation written FIRST:

1. Write the test that captures the requirement (or reproduces the bug).
2. RUN it and watch it FAIL for the expected reason — a test that never
   failed proves nothing.
3. Implement until green. Commit test + implementation together.

Bug reports get a reproducing test before any fix (the first-run
`Unknown command: /omc:start` bug is the canonical example: the E2E gap WAS
the bug).

### Tests must RUN. A test that cannot run is a FAILURE, never a skip.

- **Never** `pytest.skip` / `mark.skip` / `skipif` / conditional skip-guards.
  A missing prerequisite is a `pytest.fail` naming the exact command that
  satisfies it (missing token → "put an ANTHROPIC_API_KEY in .env …").
- Tier *selection* is allowed: `just check` (fast gate: unit tests, no
  LLM/Docker; network only for the installed-wheel test on a cold uv cache)
  vs `just build` (ruff + package build, no tests) vs
  `just e2e-tests` (Docker-per-test, real LLMs, token-gated). Within a
  selected tier, every test runs or fails loud.

### No brittle tests

- **Assert on artifacts, not transcripts.** `claude -p --output-format text`
  prints only the FINAL message — mid-session output (OMC_* verdict lines,
  progress) is invisible. Assert on files, git state, exit codes, registry
  contents. Judge transcripts only for qualities artifacts can't carry.
- **Stub ≠ tested.** Argv-recording stubs prove omc CALLED a tool, not that
  the tool works. Every external integration keeps ≥1 E2E driving the REAL
  tool and asserting its on-disk effect.
- **Stub scripts run on a restricted PATH** (only the stub dir). Use shell
  builtins (`:` `echo` `case`), absolute paths (`/bin/cat`, `/usr/bin/wc`),
  or quoted heredocs — bare `touch`/`cat` silently break (this has bitten
  three times).
- **LLM judges**: judge on the same provider under test; a rubric per
  scenario; unparseable judge output raises — it never silently passes.
- Exact-argv assertions over "was called"; loose substring matching only
  where model output is inherently variable.

## Architectural invariants

- **`ToolContext` (src/omc/toolctx.py) is the only subprocess/env/network
  boundary.** Nothing else spawns a process, imports `urllib`, or reads
  `~/.omc` (`http_get` is omc's single network call). The only *runtime*
  `subprocess` imports outside it are for `subprocess.TimeoutExpired` in
  `except` clauses (notify.py, watch.py); gitnexus.py's is annotation-only
  under `TYPE_CHECKING` — add no more. Argv lists only — never
  `shell=True`; user-controlled strings go through `shlex.quote`.
- Exit codes: 0 ok, 1 error (`OmcError`), 2 refusal (`Refusal`),
  3 bail (`omc internal` only: "inconclusive, caller falls back to its own
  judgment").
- **Skill machine contracts** are single JSON lines: `OMC_SLUG`, `OMC_STAGE`,
  `OMC_SQUASH`, `OMC_REBASE_MAIN`, `OMC_KNOWLEDGE`, `OMC_DESIGN_RECORD`,
  `OMC_MODELS`, `OMC_WORKSPACE`. Parsers tolerate markdown wrapping; skills
  forbid it. Internal skills carry "not meant for direct invocation" in
  their frontmatter description.
- CLI phases narrate progress on stderr (`→` / `✓` / `·` lines) — a silent
  minute is a bug.
- Provider CLI quirks are comments at the exact code site that depends on
  them (`src/omc/providers/*.py`); they were live-verified — do not "clean
  up" flags without re-verifying against the real CLI.
- Long-running behavior is foreground-only: omc never creates daemons,
  LaunchAgents, or cron entries.
- **Never run `omc install` / `uv tool install` on your own initiative.**
  `omc install <path>` re-roots every future `omc update` at that path — an
  install pointed at a feature worktree has silently pinned the host omc to
  a stale branch multiple times. Installing is a USER decision, made from
  the primary checkout on `main`; if a task seems to require reinstalling
  omc, stop and tell the user the exact command instead of running it.
  The one exception is `tests/unit/test_installed_wheel.py`: it installs a wheel
  it just built (never a checkout path, so no `omc update` is re-rooted) into a
  private `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, with `OMC_HOME`, `HOME` and every
  `XDG_*` asserted to lie under pytest's tmp before uv runs; only the host uv
  cache is shared, so on a cold cache this one `just check` test needs network.

## Build & verify

- `just check` — the default gate; run after every change. `just build` runs
  the world-build (ruff + package build, no tests).
- `just e2e-tests [selector]` — Docker E2E; tokens from `.env`
  (`cp env.example .env`). First image build is slow; layers cache.
- Project stages for this repo: `.omc/skills/{check,build,verify,review}`
  (used by `/omc:finish`).
