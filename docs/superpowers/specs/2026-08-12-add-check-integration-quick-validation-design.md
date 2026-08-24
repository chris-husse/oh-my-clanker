# `/check` stage + stage-semantics re-scope — design

Approved 2026-08-12. Extends the stage system from
`2026-07-17-omc-stages-design.md` (which remains the historical record of
the original build/verify/review port).

## Problem

omc's stage system has no fast "am I on the right track" gate. The
convention so far overloads `build` as the fast gate (this repo's own
`just build` is format+lint+unit tests), leaving no name for "build the
world", and nothing teaches agents when to validate cheaply versus
expensively.

## New stage semantics (the contract)

| Stage | Meaning | When agents run it |
|---|---|---|
| `check` | **NEW** — build only what unit tests need, then run the unit tests | Constantly, during development — quick validation |
| `build` | Build the world — everything compiles/packages; **no tests** | At finish (gate), or on demand |
| `verify` | Full E2E verification | Only after major milestones; finish gate |
| `review` | Unchanged | Finish gate |

`check` and `build` are siblings, not subset/superset: check runs tests,
build never does. Projects still define what each stage *means* for them
(`.omc/skills/<stage>/SKILL.md`); omc now also ships the *intended
semantics* so agents and `/omc:integrate` steer projects toward this split.

The `OMC_STAGE` machine contract is unchanged; `check` emits
`OMC_STAGE {"stage": "check", "configured": bool, "passed": bool, "summary": "..."}`
exactly like its siblings, and an unconfigured check stage is a pass.

## Approaches considered

- **A. Minimal additive** — add the `check` proxy and finish wiring only.
  Rejected: without the re-scope, `check` vs `build` stays ambiguous and
  the "build = fast gate" convention keeps leaking.
- **B. Full re-scope (chosen)** — add `check` everywhere the stage set
  appears, restate `build`/`verify` semantics, re-scope this repo's
  dogfood, teach the cadence in the behavior layer and implement, and make
  `/omc:integrate` migrate existing projects. All skill-text/docs/tests;
  zero runtime code changes.
- **C. B + CLI awareness** — additionally point `omc watch --auto-build`
  at the check stage. Declined by the user: auto-build keeps running the
  **build** stage, untouched.

## Changes by component

### 1. New proxy skill — `skills/check/SKILL.md`

Exact clone of the build/verify proxy pattern: resolve project root
(`git rev-parse --show-toplevel`, else cwd) → look for
`<root>/.omc/skills/check/SKILL.md` → missing = "nothing to do", a PASS →
present = follow it from the project root → always end with the one-line
`OMC_STAGE` verdict. Frontmatter description carries the semantic: quick
validation — build what the unit tests need, run them; call it while
working for fast feedback.

### 2. Semantic re-scope of existing proxies

`skills/build/SKILL.md` and `skills/verify/SKILL.md` get updated
frontmatter descriptions plus one body line stating intended semantics
(build = the world, no tests; verify = full E2E, reserved for major
milestones). Proxy mechanics and the `OMC_STAGE` contract are untouched.
`skills/review` is unchanged.

### 3. Finish pipeline — `skills/finish/SKILL.md`

Step 4 becomes **check → build → verify → review**, with check as the
cheap fail-fast gate: a broken unit test dies in seconds, not after a
world-build. Same rules as today: unconfigured stages are noted and pass,
the first failure stops before push.

### 4. Implement workflow — `skills/implement/SKILL.md`

Phase 3 gains a per-task validation mandate: after each task's subagent
completes, run `/omc:check` (the project-defined gate, not ad-hoc test
guessing) before moving to the next task; a failing check blocks
progression until fixed. Phase 4 (finish) text updates to the four-stage
order.

### 5. Behavior layer — `src/omc/distribution/AGENTS.md`

(Root `AGENTS.md`/`CLAUDE.md` are symlinks to the installed copy — edits
go to the source file, delivered by `omc update`/reinstall.) Two edits:

- The finish bullet becomes `/omc:check` → `/omc:build` → `/omc:verify` →
  `/omc:review`.
- New **validation cadence** rule: `/omc:check` for quick validation while
  working; `/omc:verify` only after major milestones (full E2E);
  `/omc:build` builds the world without tests. Verify is never a routine
  dev-loop gate.

### 6. Integrate — `skills/integrate/SKILL.md`

- Inventory adds `.omc/skills/check`.
- New design section `### .omc/skills/check`: investigate the minimal
  build-for-unit-tests + run command (justfile/Makefile/CI config) and
  propose a draft naming the REAL commands.
- The `### .omc/skills/build` section is reworded to the world-build
  meaning (no tests).
- **Migration trigger (both fresh-setup and review modes)**: whenever
  `.omc/skills/check` is absent, integrate treats that as a signal the
  project predates the re-scope — it audits the existing `build` stage
  against the new semantics (a build stage that runs unit tests is
  pre-re-scope) and proposes splitting it into `check` (minimal build +
  unit tests) and `build` (world, no tests), grounded in the project's
  actual commands.

### 7. Dogfood (this repo)

- `justfile`: new `check:` recipe = `uv run pytest -m "not e2e" -q`;
  `build:` re-scoped to `uvx ruff format --check .` + `uvx ruff check .` +
  `uv build` (static gates + packaging, no tests).
- `.omc/skills/check/SKILL.md` → `just check` (pass = exit 0, all unit
  tests green).
- `.omc/skills/build/SKILL.md` → re-scoped description, still
  `just build`.

Side effect, accepted: `omc watch --auto-build` (which runs the build
stage per action tick) now gates this repo on ruff + `uv build` instead of
unit tests.

`uv build` artifacts land in `dist/`, already gitignored; `pyproject.toml`
has a `[build-system]` table, so the command works as-is.

**CI**: `.github/workflows/ci.yml` currently runs `just build` — after the
re-scope that would silently drop unit tests from CI. The workflow must
run `just check` AND `just build` (check first, fail fast). Hardening
finding; without this the re-scope removes CI test coverage.

### 8. Docs

- `README.md`: stage set becomes `{check,build,verify,review}`; finish
  order and the standalone no-op behavior updated in both the install
  walkthrough and the finish section.
- This spec records the re-scope; the 2026-07-17 stages spec stays as the
  historical record.

## Testing

**Unit** (`tests/unit/test_plugin_manifests.py` — exact touch points
confirmed during hardening):

- `USER_FACING_SKILLS` tuple gains `"check"` (drives the frontmatter
  test).
- `test_stage_proxy_contract`: tuple becomes
  `("check", "build", "verify", "review")`.
- `test_finish_skill_contract`: ordering assertion becomes squash →
  `check` → `build` → `verify` → `review` → create-mr.
- `test_dogfood_build_stage`: still asserts `just build`;
  `test_dogfood_stage_and_context_skills` gains
  `("check", "just check")` (and build's needle stays honest against the
  re-scoped recipe).
- `test_integrate_skill_contract`: needles gain `.omc/skills/check` and
  a migration-trigger needle.
- Behavior layer: extend the existing
  `test_distribution_agents_model_tier_policy` pattern with a new
  contract test asserting the validation-cadence rule in
  `src/omc/distribution/AGENTS.md`.
- Implement skill: needle for the per-task check mandate.

**E2E** (hermetic Docker, `tests/e2e/test_e2e_finish.py`):

- Extend the passing-stage scenario so the repo configures **both** check
  and build stages with distinct `/tmp` markers, proving both ran, in
  order, before the push.
- Add a failing-check scenario: origin must NOT receive the branch
  (mirrors the existing failing-build test).

## Explicitly out of scope

- `src/omc/watch.py` `_auto_build` — keeps running the **build** stage.
- No new CLI flags, no config surface, no `OMC_STAGE` contract changes.

## Delivery caveat

`plugin.json` is version-pinned at 0.1.0, so cached plugin snapshots don't
refresh — the new `check` skill reaches installed users via plugin
uninstall+reinstall (known trap; matters when testing the rollout).
