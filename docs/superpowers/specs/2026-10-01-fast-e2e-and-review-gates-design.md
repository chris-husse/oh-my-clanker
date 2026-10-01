# Fast E2E and review gates: minutes, in parallel, with a 5-minute ceiling

**Date:** 2026-10-01
**Slug:** `fast-e2e-and-review-gates`
**Status:** design, approved in chat

## 1. Problem

The live lifecycle matrix (`tests/e2e/test_e2e_lifecycle.py`, 10 cases) takes
over an hour serially and is a mandatory gate of the `verify` stage for most
changes. On 2026-10-01 it turned a one-line fix into a day. Measured: cases
run 5 to 15 minutes each; 7 of 10 completed in about 70 minutes before the run
was stopped.

### Where the time goes

- **A case is the whole product, run by a real agent.** `omc start` (slug
  model call, worktree, first turn with an explain pass over the graph),
  three discussion turns, then `/omc:implement`: spec with an explain pass
  per section, plan with an explain pass per section, a subagent per task
  plus a reviewer per task, a final review, then finish with four stages and
  an MR description. The test budgets that single implement turn at 1800 s.
- **Every Claude turn is a cold process.** `ClaudeConversation.send` runs a
  fresh `claude --resume -p`, reloading the full session each turn.
- **Top-tier models everywhere.** Actors and judges default to
  `claude-fable-5-1`; Codex runs at effort `high`. Two or three judge calls
  per case, each a separate `claude -p`.
- **Serial by construction.** The Codex account volume rotates an OAuth
  refresh token, so `codex_auth.py` takes an exclusive non-blocking lock and
  hard-fails under pytest-xdist. The session-scoped image fixture deletes
  its image on exit, so N workers would build N images and delete each
  other's. pytest-xdist is not installed.
- **Per-test setup pulls from the network.** `docker/setup-plugins.sh` adds
  marketplaces and installs plugins at test time (the image build only
  does it "best-effort"), 30 to 60 s per container.

The harness itself is not the sink: polling is 0.1 s and the image builds
once in about 3 minutes.

## 2. Goals and non-goals

Goals:

1. The default E2E run (what `verify` gates on) completes in minutes, all
   cases in parallel, no case above 5 minutes.
2. The review stage rejects tests that exceed 5 minutes or cannot run in
   parallel, and the ceiling is enforced mechanically, not by reading.
3. Codex cases remain available as an explicit opt-in gate for changes that
   could break the Codex integration (no Codex API key exists; the account
   volume stays the only Codex auth).
4. The unit suite gets faster too.

Non-goals:

- Changing omc's own lifecycle (spec hardening depth, subagent-per-task).
  Decided against on 2026-10-01.
- Testing Codex and Claude lifecycles in parallel with each other on one
  account volume. The OAuth rotation forbids it.
- Removing the full end-to-end lifecycle run. It survives as an opt-in.

## 3. Design

### 3.1 One golden path, snapshotted per stage; variations fork from a stage

The monolithic lifecycle cases are replaced by **one golden path per
provider** that runs the real lifecycle once, as ordered stage tests, and
**variation tests** that fork from a stage snapshot and run one or two turns.

**Golden path.** `tests/e2e/golden/test_golden_<provider>.py` holds the
stages in order, each one or two agent turns with its own assertions and a
300 s budget:

| stage | turns | asserts |
| --- | --- | --- |
| `start` | `omc start` | worktree and branch created, no product edits |
| `primer` | first turn | primer presented, seed asked, nothing committed |
| `design` | seed → design | complete design presented, boundary unchanged |
| `agreed` | detail → "ok" | design acknowledged, still waiting for the direct command |
| `implemented` | `/omc:implement` | spec and plan written, product changed, a child dispatched |
| `finished` | (part of implement) | stages ran in order, branch pushed with a real description |

After each stage passes, the container is snapshotted with `docker commit`
to `omc-e2e-stage:<provider>-<stage>-<source-sha>`. A manifest at
`/tmp/omc-stage.json` inside the image records slug, repo, worktree,
session name and model. Claude's named session is on the filesystem under
`~/.claude`, so `claude --resume <slug>` works in any container started
from the image; Codex sessions live under `CODEX_HOME` the same way, with
the account volume remounted because mounts are not part of a commit.

**Variations.** `tests/e2e/variations/` holds tests decorated
`@variation(stage="agreed")`. The fixture starts a fresh container from
that stage image, copies the conversation driver in, and returns a session
already bound to the resumable slug and worktree. The test mutates the
clone as it likes (sabotage the build script, plant a conflicting
requirement, delete a file, stale the plugin), then sends one or two turns
and asserts. Today's scenarios become variations from `agreed`: a clean
`/omc:implement`; the critical question; the failing build with the build
script sabotaged in the clone. A new failure-injection test is a short
function and never touches the golden path.

**Parallelism.** `just e2e-tests` runs two phases: the golden stages
sequentially (one worker, in order, snapshots as side effects), then
everything else with `-n auto`: variations across all stages, smoke,
marketplace. Stage images are keyed by source sha, so a variation can be
iterated against existing snapshots during development without re-running
the golden path, and a source change invalidates them. Snapshots older
than the current sha are pruned by the recipe.

The Codex conversation-capability and native-skill-mention cases stay as
they are (they are Codex integration checks), under the opt-in group.

### 3.2 Models for tests

`CLAUDE_E2E_MODEL` and `CLAUDE_E2E_JUDGE_MODEL` default to the standard
coding tier (`claude-sonnet-5-5`). Judges check three-line rubrics; the
fixture is a one-line greeting change. The top tier is available by setting
the variables. The behavior layer's "judging goes to the top tier" applies
to omc's own review and spec work, not to test rubrics; the exemption is
written into `AGENTS.md`.

### 3.3 Parallel execution

- `pytest-xdist` (3.8.0, MIT) and `pytest-timeout` (2.4.0, MIT) become dev
  dependencies.
- **Image built once.** `tests/e2e/conftest.py` builds the image in
  `pytest_configure` on the xdist controller (or in a plain run) and
  exports its tag through `OMC_E2E_SHARED_IMAGE`, which spawned workers
  inherit; the `e2e_image` fixture yields that tag without building or
  deleting. The controller removes the image in `pytest_unconfigure`. The
  prebuilt-image override keeps working unchanged.
- **Grouping.** A collection hook adds `xdist_group("codex-account")` to
  every test whose provider is Codex, and the recipes use
  `--dist loadgroup`, so Codex cases share one worker and run serially
  there while Claude cases spread across the rest. The hard
  `pytest.fail` under xdist in `codex_auth.py` becomes a **blocking** lock
  with a timeout, so a misrouted Codex test waits instead of failing.
- **Recipes.** `just e2e-tests` and `just check` run with `-n auto`;
  `just lifecycle-tests` runs the Claude phase tests with `-n auto`;
  `just codex-gate` runs the Codex group (`-m codex_gate`) serially with
  the account volume; `just lifecycle-full` runs the old end-to-end cases,
  kept under `tests/e2e/test_e2e_lifecycle_full.py` behind the `expensive`
  marker.

### 3.4 Ceiling, enforced

- `[tool.pytest.ini_options]` sets `timeout = 300` and
  `timeout_method = thread`. A test that exceeds 5 minutes fails. The
  `expensive` tier overrides the timeout per test with `@pytest.mark.timeout`.
- `just e2e-tests` always runs with `-n auto`, so a test that cannot run in
  parallel fails rather than passes.

### 3.5 Review and verify stage policy

`.omc/skills/review/SKILL.md` gains two load-bearing rules, each an
Important finding when violated:

- **No slow tests.** A test whose runtime can exceed 5 minutes (its budgets,
  loops, or `timeout` marker allow it) is rejected; the only exception is
  the `expensive` tier, which no stage gates on.
- **No serial-only tests.** A test that cannot run under `-n auto` (module
  or session state shared across tests, locks that fail instead of wait,
  guards on `PYTEST_XDIST_WORKER`, fixed host ports or paths) is rejected.
  Serialization is expressed only through `xdist_group`.

`.omc/skills/verify/SKILL.md` becomes: run `just e2e-tests` (smoke,
marketplace, phase tests, all parallel) and require exit 0; run
`just codex-gate` only when the change touches the Codex provider, the Codex
conversation driver, Codex plugin payload or setup; the full lifecycle run
is never a gate.

### 3.6 Per-test setup

The image build runs `docker/setup-plugins.sh` to completion (failure fails
the build instead of deferring), and the script skips every step whose
result is already present (marketplace registered, plugin listed healthy).
Test-time setup becomes a verification pass with no network.

## 4. Decisions taken during brainstorm

| # | Decision | Reason |
| --- | --- | --- |
| 1 | One golden path run once per provider, snapshotted per stage with `docker commit`; variations fork from a stage and run one or two turns | Each test stays under 5 minutes, the shared preamble runs once instead of six times, and any stage can be hooked to inject a failure. User design 2026-10-01. |
| 2 | Codex cases opt-in, serial on the account volume, gate only for Codex-touching changes | No Codex API key; the account volume's OAuth rotation forbids concurrency. User decision 2026-10-01. |
| 3 | Standard tier for test actors and judges | Three-line rubrics and a one-line fixture; Fable buys nothing. |
| 4 | pytest-timeout 300 s as the mechanical ceiling; `-n auto` as the mechanical parallel check | Review prose alone does not stop a slow test from merging. |
| 5 | No change to omc's own lifecycle | User decision 2026-10-01. |
| 6 | Full lifecycle run kept as `expensive` opt-in | It is the only place the real nested flow is exercised end to end; it just stops gating. |

## 5. Risks

- A variation only sees what the golden path produced, so a change in the
  skills' contracts changes every variation's starting point at once; that
  is the intent (no hand-made fixture can drift from the product).
- `docker commit` captures the filesystem, not mounts or running
  processes; the Codex account volume is remounted per clone and a stage
  snapshot is taken only between turns.
- `timeout_method = thread` cannot interrupt a blocked `subprocess`
  `communicate`; the conversation driver already enforces its own
  per-turn timeouts, which stay at or below 300 s.
- Building the image in `pytest_configure` runs even for `--collect-only`
  under `-n`; gated on at least one e2e test being selected.

## Deliberate complexity

None.
