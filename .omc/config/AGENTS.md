# oh-my-clanker — project agent instructions

(Reached via omc's AGENTS.md control chain — the root AGENTS.md/CLAUDE.md
symlinks send you here.) Deeper truth map: `.omc/skills/explain-context/SKILL.md`.

This repo is a uv-installed Python CLI (`src/omc/`) plus a skills plugin
(`skills/`, installable in Claude Code / Codex / OpenCode from this repo).
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
- Tier *selection* is allowed: `just build` (fast: ruff + unit, no LLM/network/
  Docker) vs `just e2e-tests` (Docker-per-test, real LLMs, token-gated).
  Within a selected tier, every test runs or fails loud.

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

- **`ToolContext` (src/omc/toolctx.py) is the only subprocess/env boundary.**
  Nothing else imports subprocess or reads `~/.omc`. Argv lists only — never
  `shell=True`; user-controlled strings go through `shlex.quote`.
- Exit codes: 0 ok, 1 error (`OmcError`), 2 refusal (`Refusal`),
  3 bail (`omc internal` only: "inconclusive, caller falls back to its own
  judgment").
- **Skill machine contracts** are single JSON lines: `OMC_SLUG`, `OMC_STAGE`,
  `OMC_SQUASH`, `OMC_REBASE_MAIN`. Parsers tolerate markdown wrapping; skills
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

## Build & verify

- `just build` — the default gate; run after every change.
- `just e2e-tests [selector]` — Docker E2E; tokens from `.env`
  (`cp env.example .env`). First image build is slow; layers cache.
- Project stages for this repo: `.omc/skills/{build,verify,review}` (used by
  `/omc:finish`).
