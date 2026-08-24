# `/check` stage + stage-semantics re-scope — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `check` stage (minimal build + unit tests, the quick dev-loop gate) to omc's stage system and re-scope the semantics of `build` (build the world, no tests) and `verify` (milestone-only full E2E) everywhere the stage set appears.

**Architecture:** Pure skill-text/docs/tests change — zero runtime Python changes except the behavior-layer markdown (`src/omc/distribution/AGENTS.md`). A new `skills/check/SKILL.md` proxy clones the established stage-proxy pattern (project defines meaning via `.omc/skills/check/SKILL.md`, proxy emits a one-line `OMC_STAGE` verdict, unconfigured = pass). Finish runs check → build → verify → review; implement mandates per-task `/omc:check`; integrate gains a check design section and a migration trigger; this repo's dogfood recipes split accordingly.

**Tech Stack:** Markdown skills, justfile, pytest (unit contract tests + hermetic Docker E2E), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-12-add-check-integration-quick-validation-design.md`

## Global Constraints

- The `OMC_STAGE` machine contract is unchanged: `OMC_STAGE {"stage": "<stage>", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`, one plain-text line, never fenced. Unconfigured → `"configured": false, "passed": true`.
- Stage semantics (copy verbatim where semantics are stated): `check` = build only what unit tests need, then run the unit tests — quick validation while working; `build` = build the world — everything compiles/packages, **no tests**; `verify` = full E2E — only after major milestones; `review` unchanged.
- `src/omc/watch.py` (`_auto_build`) is explicitly untouched — it keeps running the **build** stage.
- No new CLI flags, no config surface changes, no new dependencies.
- Unit test command throughout: `uv run pytest -m "not e2e" -q` (the justfile changes mid-plan; use the pytest command directly).
- Tests run or fail — never skip (repo rule: no `pytest.skip`/`skipif`).
- Every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `skills/check/SKILL.md` | Create | New user-facing stage proxy |
| `skills/build/SKILL.md`, `skills/verify/SKILL.md` | Modify | Semantic re-scope (description + one body line) |
| `skills/finish/SKILL.md` | Modify | Step 4 order: check → build → verify → review |
| `skills/implement/SKILL.md` | Modify | Phase 3 per-task check mandate; Phase 4 stage list |
| `src/omc/distribution/AGENTS.md` | Modify | Finish bullet + validation-cadence rule |
| `skills/integrate/SKILL.md` | Modify | Inventory row, check section, build/verify rewording, migration trigger |
| `justfile`, `.omc/skills/check/SKILL.md`, `.omc/skills/build/SKILL.md`, `.github/workflows/ci.yml` | Create/Modify | Dogfood split + CI keeps unit tests |
| `README.md` | Modify | Stage set/order documentation |
| `tests/unit/test_plugin_manifests.py` | Modify | Contract tests for all of the above |
| `tests/e2e/test_e2e_finish.py` | Modify | Check+build ordering scenario; failing-check scenario |

---

### Task 1: `check` proxy skill + proxy contract tests

**Model:** standard coding tier

**Files:**
- Create: `skills/check/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (`USER_FACING_SKILLS` tuple ~line 34; `test_stage_proxy_contract` ~line 100)

**Interfaces:**
- Produces: `skills/check/SKILL.md` — the stage proxy later tasks reference as `/omc:check`; emits `OMC_STAGE {"stage": "check", ...}`.

- [ ] **Step 1: Update the contract tests to expect `check` (failing first)**

In `tests/unit/test_plugin_manifests.py`, add `"check"` to `USER_FACING_SKILLS` (after `"review"`):

```python
USER_FACING_SKILLS = (
    "slug",
    "start",
    "plan",
    "implement",
    "finish",
    "check",
    "build",
    "verify",
    "review",
    "index",
    "document",
    "explain",
    "explain-dependency",
    "investigate",
    "rebase-main",
    "check-wt-config",
    "integrate",
)
```

And in `test_stage_proxy_contract`, change the tuple to include check:

```python
def test_stage_proxy_contract():
    for stage in ("check", "build", "verify", "review"):
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_skills_have_frontmatter tests/unit/test_plugin_manifests.py::test_stage_proxy_contract -q`
Expected: FAIL — `skills/check/SKILL.md` does not exist.

- [ ] **Step 3: Create `skills/check/SKILL.md`**

Exact content (clone of the build proxy, stage renamed, semantics line added):

```markdown
---
name: check
description: Run the project's check stage if one is configured (.omc/skills/check in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "check" means. Intended semantics: quick validation while working - build only what the unit tests need, then run them.
---

# omc check (project-stage proxy)

There is nothing omc-specific to do here: this skill runs the PROJECT's
check stage, if the project defines one.

Intended semantics: check is the fast "am I on the right track" gate —
build only what the unit tests need, then run them. Call it constantly
during development; the world-build belongs to `build` and full E2E to
`verify` (major milestones only).

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory.
2. Look for `<project-root>/.omc/skills/check/SKILL.md`.
   - **Missing** → report "no project `check` stage configured — nothing to
     do" and end (that is a PASS, not a failure).
   - **Present** → read it and follow its instructions, running commands from
     the project root. The project skill decides what passing means; take its
     instructions at face value and judge the outcome honestly.
3. **Always** end with exactly one machine-readable line (plain text, no
   backticks or code fences around it):

   `OMC_STAGE {"stage": "check", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

   Unconfigured → `"configured": false, "passed": true`. A configured stage
   that failed → `"passed": false` with the failure in `summary`.
```

- [ ] **Step 4: Run the unit suite to verify it passes**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add skills/check/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Add /omc:check stage proxy skill

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Semantic re-scope of `build` and `verify` proxies

**Model:** standard coding tier

**Files:**
- Modify: `skills/build/SKILL.md` (frontmatter description + body line after the intro paragraph)
- Modify: `skills/verify/SKILL.md` (same shape)

**Interfaces:**
- Consumes: nothing from other tasks (independent of Task 1 at runtime, but keep task order for coherent commits).
- Produces: re-scoped proxy descriptions other surfaces (AGENTS.md, README) describe consistently.

- [ ] **Step 1: Edit `skills/build/SKILL.md`**

Replace the frontmatter `description:` line with:

```yaml
description: Run the project's build stage if one is configured (.omc/skills/build in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "build" means. Intended semantics: build the world - everything compiles and packages, NO tests.
```

After the intro paragraph ("There is nothing omc-specific to do here: this skill runs the PROJECT's build stage, if the project defines one."), insert a blank line and:

```markdown
Intended semantics: build is the world-build — everything compiles and
packages, with NO tests. Unit-test validation belongs to `check`; full
E2E belongs to `verify`.
```

Steps section and the `OMC_STAGE` line are untouched.

- [ ] **Step 2: Edit `skills/verify/SKILL.md`**

Replace the frontmatter `description:` line with:

```yaml
description: Run the project's verify stage if one is configured (.omc/skills/verify in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "verify" means. Intended semantics: full E2E verification - reserved for after major milestones, never the routine dev loop.
```

After the intro paragraph, insert a blank line and:

```markdown
Intended semantics: verify is the heavyweight full-E2E tier. Run it after
major milestones — never as a routine dev-loop gate (that is `check`'s
job).
```

- [ ] **Step 3: Run the unit suite**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS — `test_stage_proxy_contract` needles (`.omc/skills/<stage>`, `OMC_STAGE`, `"configured"`, "nothing to do") all still present.

- [ ] **Step 4: Commit**

```bash
git add skills/build/SKILL.md skills/verify/SKILL.md
git commit -m "Re-scope build/verify proxy semantics: world-build, milestone E2E

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Finish pipeline runs check first

**Model:** standard coding tier

**Files:**
- Modify: `skills/finish/SKILL.md` (Step 4 heading + first paragraph, ~line 44; frontmatter description)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_finish_skill_contract` ordering assertion, ~line 95)

**Interfaces:**
- Consumes: `skills/check/SKILL.md` from Task 1 (invoked by name `check`).
- Produces: finish order squash → check → build → verify → review → create-mr, contract-tested.

- [ ] **Step 1: Update the ordering test (failing first)**

In `test_finish_skill_contract`, replace the ordering block with:

```python
    # squash is delegated, then stages run check -> build -> verify -> review, then push
    order = [text.index("`squash`"), text.index("`check`"), text.index("`build`")]
    order += [text.index("`verify`"), text.index("`review`"), text.index("`create-mr`")]
    assert order == sorted(order), "finish must order squash -> check -> build -> verify -> review -> push"
```

(`text.index("`check`")` cannot false-match `` `check-wt-config` `` — the closing backtick differs — and finish's skill text does not mention check-wt-config anyway.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_finish_skill_contract -q`
Expected: FAIL — `` `check` `` not found in finish skill text.

- [ ] **Step 3: Edit `skills/finish/SKILL.md`**

Replace the Step 4 heading and first paragraph:

```markdown
## Step 4 — project stages: check → build → verify → review

Invoke the **`check`**, **`build`**, **`verify`**, and **`review`** skills,
in that order — check first, as the cheap fail-fast gate: a broken unit
test dies in seconds, not after a world-build. Each is a proxy for the
project's own `.omc/skills/<stage>/SKILL.md`:
```

The three outcome bullets (unconfigured/skipped, amend tracked changes, failed stage stops) are unchanged. In the frontmatter `description:`, change "run the project's build/verify/review stages" to "run the project's check/build/verify/review stages".

- [ ] **Step 4: Run the unit suite**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/finish/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Finish runs check as fail-fast gate before build/verify/review

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Implement mandates per-task /omc:check

**Model:** standard coding tier

**Files:**
- Modify: `skills/implement/SKILL.md` (Phase 3, after the model-dispatch paragraph ~line 64; Phase 4 stage list ~line 76)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_implement_skill_contract` needles, ~line 221)

**Interfaces:**
- Consumes: `/omc:check` proxy from Task 1.
- Produces: per-task validation mandate in the conductor workflow.

- [ ] **Step 1: Add the needle to the contract test (failing first)**

In `test_implement_skill_contract`, add two needles to the tuple (after `"top tier"`):

```python
        "/omc:check",
        "before dispatching the next task",
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_implement_skill_contract -q`
Expected: FAIL — `/omc:check` not in implement skill text.

- [ ] **Step 3: Edit `skills/implement/SKILL.md`**

In Phase 3, after the paragraph ending "Plans missing `Model:` lines fall back to the behavior layer's model-tier policy directly.", insert:

```markdown
After EACH task's subagent completes (and its reviews pass), run
`/omc:check` — the project-defined quick gate (build what the unit tests
need, run them) — before dispatching the next task. A failing check blocks
progression: fix forward until check passes. Never substitute ad-hoc test
commands for the stage; an unconfigured check is a pass, so this costs
nothing on projects without one. Full E2E (`/omc:verify`) is NOT part of
the per-task loop — it belongs to major milestones (finish runs it).
```

In Phase 4, change "build/verify/review stages, push." to "check/build/verify/review stages, push."

- [ ] **Step 4: Run the unit suite**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS (the implement ordering assertion `spec -> writing-plans -> subagent-driven-development -> finish` is unaffected — the inserted text sits inside Phase 3).

- [ ] **Step 5: Commit**

```bash
git add skills/implement/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Implement Phase 3 mandates per-task /omc:check gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Behavior-layer validation-cadence rule

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/distribution/AGENTS.md` (finish bullet, lines 10–12; new bullet after it)
- Modify: `tests/unit/test_plugin_manifests.py` (new test after `test_distribution_agents_model_tier_policy`, ~line 320)

**Interfaces:**
- Produces: cross-harness cadence rule; contract test `test_distribution_agents_validation_cadence`.

- [ ] **Step 1: Write the failing test**

Add after `test_distribution_agents_model_tier_policy`:

```python
def test_distribution_agents_validation_cadence():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    for needle in (
        "Validation cadence",
        "/omc:check",
        "builds the world",
        "major milestones",
    ):
        assert needle in text, f"behavior layer missing {needle!r}"
    # finish bullet lists all four stage gates in order
    assert "`/omc:check` → `/omc:build` → `/omc:verify` → `/omc:review`" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_distribution_agents_validation_cadence -q`
Expected: FAIL.

- [ ] **Step 3: Edit `src/omc/distribution/AGENTS.md`**

Replace the finish bullet (lines 10–12) with:

```markdown
- **Finish work through `/omc:finish`** — rebase, squash, project stage gates
  (`/omc:check` → `/omc:build` → `/omc:verify` → `/omc:review`), described
  push. Do not bypass a failing stage.
- **Validation cadence**: `/omc:check` is the quick "am I on the right
  track" gate (build what the unit tests need, run them) — use it
  constantly while working. `/omc:build` builds the world, no tests.
  `/omc:verify` is full E2E — run it only after major milestones, never as
  a routine dev-loop gate.
```

- [ ] **Step 4: Run the unit suite**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS (including the existing `test_distribution_agents_model_tier_policy` and `tests/unit/test_agentsmd.py`).

- [ ] **Step 5: Commit**

```bash
git add src/omc/distribution/AGENTS.md tests/unit/test_plugin_manifests.py
git commit -m "Behavior layer: validation-cadence rule and four-stage finish gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Integrate — check slot, reworded build/verify slots, migration trigger

**Model:** heavy coding tier

**Files:**
- Modify: `skills/integrate/SKILL.md` (inventory row ~line 39; Phase 2 stage sections ~lines 62–80)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_integrate_skill_contract` needles, ~line 287)

**Interfaces:**
- Consumes: stage semantics wording from Global Constraints.
- Produces: integrate designs `.omc/skills/check` per project and migrates pre-split projects.

- [ ] **Step 1: Add needles to the contract test (failing first)**

In `test_integrate_skill_contract`, add to the needle tuple (after `".omc/skills/build"`):

```python
        ".omc/skills/check",
        "Migration trigger",
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_integrate_skill_contract -q`
Expected: FAIL.

- [ ] **Step 3: Edit `skills/integrate/SKILL.md`**

Inventory row (Phase 1, item 1) — change the stage list line to:

```markdown
   - `.omc/skills/check` · `.omc/skills/build` · `.omc/skills/verify` ·
     `.omc/skills/review` · `.omc/skills/explain-context` ·
     `.omc/skills/investigation-context`
```

In Phase 2, insert a new section BEFORE `### .omc/skills/build`:

```markdown
### `.omc/skills/check`
The fast "am I on the right track" gate: build only what the unit tests
need, then run them. Investigate the minimal test loop — justfile/Makefile
test recipes, package.json scripts, CI unit-test jobs, and the graph
(`query "unit tests"`). Propose a draft naming the REAL commands and what
passing means. Keep it fast: no E2E, no world-build.

**Migration trigger**: `.omc/skills/check` absent while `.omc/skills/build`
exists → the project predates the check/build split. Audit the build stage
against the current semantics (build = the world, NO tests): a build stage
that runs unit tests is pre-split — propose splitting it into `check`
(minimal build + unit tests) and `build` (world, no tests), grounded in the
project's actual commands. Applies in BOTH fresh-setup and review modes.
```

Replace the `### .omc/skills/build` section body with:

```markdown
### `.omc/skills/build`
Build the world — everything compiles and packages, NO tests (unit tests
live in `check`). Investigate how this project actually builds: justfile,
Makefile, package.json scripts, pyproject, CI workflows — and the graph
(`query "build"`). Propose a draft naming the REAL commands and what
passing means (exit codes, format/lint steps). Drift example worth flagging
in review mode: the skill says `make test` while CI runs `just build`.
```

In the `### .omc/skills/verify` section, append to the paragraph:

```markdown
Reserved for after major milestones — never the routine dev loop.
```

Also update the "What's now active" line (~line 119) from "the stage gates you just designed" context if it names stages: change any `build/verify/review` enumeration in this file to `check/build/verify/review`.

- [ ] **Step 4: Run the unit suite**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS. Note: `test_e2e_integrate.py::test_review_integrate_flags_drifted_build_stage` (token-gated E2E) depends on the drift example staying in the build section — it does.

- [ ] **Step 5: Commit**

```bash
git add skills/integrate/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Integrate designs .omc/skills/check and migrates pre-split build stages

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Dogfood split (justfile, .omc stages, CI)

**Model:** heavy coding tier

**Files:**
- Modify: `justfile` (recipes `build`, new `check`)
- Create: `.omc/skills/check/SKILL.md`
- Modify: `.omc/skills/build/SKILL.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/unit/test_plugin_manifests.py` (`test_dogfood_stage_and_context_skills`, ~line 248)

**Interfaces:**
- Produces: `just check` (unit tests) and `just build` (ruff + `uv build`, no tests); CI runs both.

- [ ] **Step 1: Update the dogfood contract test (failing first)**

In `test_dogfood_stage_and_context_skills`, change the tuple to:

```python
    for name, needle in (
        ("check", "just check"),
        ("build", "just build"),
        ("verify", "test_e2e_smoke"),
        ("review", "ToolContext"),
        ("explain-context", "docs/superpowers/specs"),
    ):
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_dogfood_stage_and_context_skills -q`
Expected: FAIL — `.omc/skills/check/SKILL.md` missing.

- [ ] **Step 3: Edit `justfile`**

Replace the `build` recipe block with:

```just
# Quick gate: build what the unit tests need, then run them. No LLM, no Docker.
check:
    uv run pytest -m "not e2e" -q

# Build the world: format check + lint + package build. NO tests (see `check`).
build:
    uvx ruff format --check .
    uvx ruff check .
    uv build
```

(The comment above the old `build` recipe — "Fast gate: lint + format check + unit tests..." — is replaced by these two.)

- [ ] **Step 4: Create `.omc/skills/check/SKILL.md`**

```markdown
---
name: check
description: omc's own check stage - the quick gate (unit tests via just check).
---

# check (this repo)

Run:

```sh
just check
```

Passing means: exit code 0 (the unit test suite all green). Any non-zero
exit or test failure means the stage FAILED; include the failing output in
your summary.
```

- [ ] **Step 5: Rewrite `.omc/skills/build/SKILL.md`**

```markdown
---
name: build
description: omc's own build stage - build the world (format check, lint, package build). No tests.
---

# build (this repo)

Run:

```sh
just build
```

Passing means: exit code 0 (ruff format --check, ruff check, and `uv build`
all clean). Any non-zero exit means the stage FAILED; include the failing
output in your summary. Unit tests are NOT run here — that is the `check`
stage's job.
```

- [ ] **Step 6: Update `.github/workflows/ci.yml`**

Replace the single `- run: just build` step with (check first, fail fast):

```yaml
      - run: just check
      - run: just build
```

- [ ] **Step 7: Verify recipes and suite**

Run: `just check`
Expected: exit 0, unit suite green (including the updated dogfood test).

Run: `just build`
Expected: exit 0 — ruff format --check clean, ruff check clean, `uv build` produces `dist/` artifacts (gitignored).

- [ ] **Step 8: Commit**

```bash
git add justfile .omc/skills/check/SKILL.md .omc/skills/build/SKILL.md .github/workflows/ci.yml tests/unit/test_plugin_manifests.py
git commit -m "Dogfood the check/build split: just check runs tests, just build builds the world

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: README stage documentation

**Model:** standard coding tier

**Files:**
- Modify: `README.md` (install step 3, ~line 23; finish/stages paragraph, ~line 62)

**Interfaces:**
- Consumes: final stage set `{check,build,verify,review}` and finish order from Tasks 1–3.

- [ ] **Step 1: Edit install step 3 (~line 23)**

Change "brainstorms your project's build/verify/review/explain-context/investigation-context skills" to "brainstorms your project's check/build/verify/review/explain-context/investigation-context skills".

- [ ] **Step 2: Edit the stages paragraph (~line 62)**

Replace the paragraph beginning "If the repo defines project stages" with:

```markdown
If the repo defines project stages (`.omc/skills/{check,build,verify,review}/SKILL.md` — each a skill saying what that stage means for *this* project), finish runs them in that order between squash and push, stopping before the push if one fails. The intended split: `check` is the quick "am I on the right track" gate (build what the unit tests need, run them — use it constantly while working), `build` builds the world without tests, and `verify` is the full-E2E tier reserved for major milestones. `/omc:check`, `/omc:build`, `/omc:verify`, and `/omc:review` run them standalone and are no-ops when unconfigured. It ends by offering to close the worktree (`wt remove` — the branch survives until merged), iterate on review comments (amend + re-push), or just talk through the change.
```

- [ ] **Step 3: Run the unit suite**

Run: `uv run pytest -m "not e2e" -q`
Expected: PASS (README has no contract test, but run the suite as the task gate anyway).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document the check/build/verify stage split in README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: E2E — check+build ordering scenario, failing-check scenario

**Model:** heavy coding tier

**Files:**
- Modify: `tests/e2e/test_e2e_finish.py` (helper `_add_build_stage` ~line 83; `test_finish_runs_passing_build_stage_then_pushes` ~line 98; new test after `test_finish_stops_before_push_on_failing_stage` ~line 182)

**Interfaces:**
- Consumes: finish order check → build (Task 3); the `check` proxy (Task 1).
- Produces: hermetic evidence that both stages run in order before push, and that a failing check blocks the push.

Note: these tests are token-gated (`require_token("claude")`) and Docker-based — they cannot run in this task's loop. The task's gates are collection + lint; the live run happens in `/omc:finish`'s verify stage or the E2E workflow.

- [ ] **Step 1: Generalize the stage helper**

Replace `_add_build_stage` with a stage-parameterized helper (keeping the exact heredoc mechanics):

```python
def _add_stage(container, repo, stage, skill_body):
    script = (
        f"mkdir -p {repo}/.omc/skills/{stage} && "
        f"cat > {repo}/.omc/skills/{stage}/SKILL.md << 'SKILLEOF'\n"
        "---\n"
        f"name: {stage}\n"
        f"description: test project {stage} stage\n"
        "---\n\n"
        f"{skill_body}\n"
        "SKILLEOF"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out
```

Update the two existing call sites from `_add_build_stage(container, repo, body)` to `_add_stage(container, repo, "build", body)`.

- [ ] **Step 2: Extend the passing scenario to prove check→build ordering**

Rename `test_finish_runs_passing_build_stage_then_pushes` to `test_finish_runs_passing_check_and_build_stages_then_pushes` and replace its stage setup with BOTH stages — build's command only succeeds if check's marker already exists, so the pushed result proves ordering:

```python
    _add_stage(
        container,
        repo,
        "check",
        "Run `sh -c 'echo checked > /tmp/omc-check-ran && echo CHECK-OK'`.\n"
        "CHECK-OK printed means the check passed; anything else is a failure.",
    )
    _add_stage(
        container,
        repo,
        "build",
        "Run `sh -c 'test -f /tmp/omc-check-ran && echo built > /tmp/omc-build-ran && echo BUILD-OK'`.\n"
        "BUILD-OK printed means the build passed; anything else is a failure.",
    )
```

The commit step's message becomes `'add check and build stages'`. After the finish run, replace the single marker assertion with both (before the push-count assertion, which is unchanged):

```python
    rc, _ = run_in(container, ["test", "-f", "/tmp/omc-check-ran"])
    assert rc == 0, "project check stage never executed"
    rc, _ = run_in(container, ["test", "-f", "/tmp/omc-build-ran"])
    assert rc == 0, "project build stage never executed (or ran before check)"
```

- [ ] **Step 3: Add the failing-check scenario**

After `test_finish_stops_before_push_on_failing_stage`, add (mirroring it exactly, with a check stage instead of build):

```python
def test_finish_stops_before_push_on_failing_check_stage(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)
    _add_stage(
        container,
        repo,
        "check",
        "Run `sh -c 'echo the unit tests are broken >&2; exit 1'`.\n"
        "A non-zero exit code means the check FAILED.",
    )
    rc, out = run_in(
        container, ["bash", "-c", f"cd {repo} && git add -A && git commit -qm 'add check stage'"]
    )
    assert rc == 0, out

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )
    # the branch must NOT reach origin when the check stage fails
    rc2, _ = run_in(
        container,
        ["git", "-C", f"{repo}-origin", "rev-parse", "--verify", "feature/manual-fix"],
    )
    assert rc2 != 0, f"failing check stage must stop the push!\ntranscript:\n{out[:2000]}"
```

- [ ] **Step 4: Verify collection and lint**

Run: `uv run pytest tests/e2e/test_e2e_finish.py --collect-only -q`
Expected: all tests collect, including the renamed and new ones; no import errors.

Run: `uvx ruff format --check . && uvx ruff check .`
Expected: clean.

Run: `just check`
Expected: PASS (unit suite unaffected).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_e2e_finish.py
git commit -m "E2E: prove check runs before build in finish; failing check blocks push

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review (done at plan time)

- **Spec coverage:** §1 check proxy → Task 1; §2 proxy re-scope → Task 2; §3 finish → Task 3; §4 implement → Task 4; §5 behavior layer → Task 5; §6 integrate + migration trigger → Task 6; §7 dogfood + CI finding → Task 7; §8 README → Task 8; Testing (unit) → folded into each task TDD-first; Testing (E2E) → Task 9. Out-of-scope items (watch.py) touched by no task. Delivery caveat (plugin snapshot refresh) is operational, not a code task.
- **Placeholder scan:** none — every step carries exact content.
- **Type consistency:** `_add_stage(container, repo, stage, skill_body)` is defined in Task 9 Step 1 and used consistently in Steps 2–3; test names referenced in run commands match the definitions.
