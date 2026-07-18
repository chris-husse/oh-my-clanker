# Model-Tier Policy in the Plan/Implement Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plans produced by omc's brainstorm/plan/implement flow assign every task a self-adapting model tier (top tier for spec/review/judging, Sonnet-class floor for coding, Opus-class for bigger coding, cheap/fast tier never), on Claude and OpenAI-family providers alike.

**Architecture:** One canonical policy block in `src/omc/distribution/AGENTS.md` (the behavior layer symlinked into every omc-managed repo, refreshed by `omc update`) plus thin apply-the-policy directives in the two skills that hand off to external superpowers skills (`skills/implement/SKILL.md`, `skills/plan/SKILL.md`). Tiers ride plans as names only; resolution to concrete models happens at dispatch time.

**Tech Stack:** Markdown skills, pytest contract tests in `tests/unit/test_plugin_manifests.py`.

**Spec:** `docs/superpowers/specs/2026-07-18-brainstorm-plan-model-tier-policy-design.md`.

## Global Constraints

- Tier vocabulary, verbatim everywhere: **top tier** (Fable-class), **heavy coding tier** (Opus-class), **standard coding tier** (Sonnet-class); the cheap/fast tier (Haiku-class or provider equivalent) is **never used**.
- Plans carry tier names only — never pinned model ids.
- Only three files change besides tests: `src/omc/distribution/AGENTS.md`, `skills/implement/SKILL.md`, `skills/plan/SKILL.md`. `skills/spec/SKILL.md` stays untouched.
- The old phrasing "efficient models for well-specified execution work" must be GONE from the distribution AGENTS.md (it invites Haiku-class dispatch).
- `test_implement_skill_contract`'s spec→plan→build→ship order assertion must keep passing — additions must not move the four anchor strings relative to each other.
- Gates before each commit: `uv run pytest tests/unit -q` green; `uvx ruff format --check .` and `uvx ruff check .` clean (lint via `uvx ruff`, NOT `uv run ruff`).
- TDD: failing tests first (RED), then implement (GREEN). No `pytest.skip` anywhere.

## File Structure

- Modify: `src/omc/distribution/AGENTS.md` (Model selection bullet), `skills/implement/SKILL.md` (Phase 2 + Phase 3), `skills/plan/SKILL.md` (Step 4), `tests/unit/test_plugin_manifests.py` (one new test, two extended tests)
- Modify: `.superpowers/sdd/progress.md` (deferred live-E2E ledger entry)

---

### Task 1: Canonical policy block in the behavior layer

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/distribution/AGENTS.md` (the `**Model selection**` bullet)
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Produces: the behavior-layer policy text whose exact tier vocabulary (`model-tier policy`, `top tier`, `heavy coding tier`, `standard coding tier`, `never used`) Task 2's skill directives reference by name.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_plugin_manifests.py` (top-level, after `test_integrate_skill_contract` / near the other distribution-related tests):

```python
def test_distribution_agents_model_tier_policy():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    for needle in (
        "model-tier policy",
        "top tier",
        "heavy coding tier",
        "standard coding tier",
        "never used",
        "OpenAI",
    ):
        assert needle in text, f"behavior layer missing {needle!r}"
    # the old guidance invited cheap-tier models for execution work
    assert "efficient models" not in text, "old Model selection phrasing must be gone"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_distribution_agents_model_tier_policy -q`
Expected: FAIL — `behavior layer missing 'model-tier policy'`.

- [ ] **Step 3: Replace the Model selection bullet**

In `src/omc/distribution/AGENTS.md`, replace this bullet:

```markdown
- **Model selection**: the main session runs the model chosen in
  `omc configure` — never second-guess it. When dispatching subagents,
  assess each task and pick the model that fits: the heavyweight model for
  planning/design, reviews, and judging subagent output; efficient models
  for well-specified execution work.
```

with:

```markdown
- **Model selection**: the main session runs the model chosen in
  `omc configure` — never second-guess it. When dispatching subagents or
  assigning models to plan tasks, apply the **model-tier policy**. Tiers
  are abstract, baselined on Claude's stack, and resolved at dispatch time
  against the provider's *current* lineup: pin the **top tier** to whatever
  is the latest & best model available (Fable-class today), then
  reinterpret the **heavy coding tier** (Opus-class) and the **standard
  coding tier** (Sonnet-class) down the current hierarchy. On other
  providers (OpenAI, …), map the tiers to that provider's current
  equivalents the same way.
  - Spec, review, and judging tasks → **top tier**.
  - Coding tasks → never below the **standard coding tier**; bigger coding
    tasks (multi-file, architecturally tricky, or ambiguous) → **heavy
    coding tier**.
  - The cheap/fast tier (Haiku-class or its equivalent on any provider) is
    **never used**, for anything.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS (whole file — no other contract broke).

- [ ] **Step 5: Gates, then commit**

Run: `uv run pytest tests/unit -q && uvx ruff format --check . && uvx ruff check .`
Expected: all green/clean.

```bash
git add src/omc/distribution/AGENTS.md tests/unit/test_plugin_manifests.py
git commit -m "feat: model-tier policy in the behavior layer (red->green)"
```

---

### Task 2: Apply-the-policy directives in the plan and implement skills

**Model:** standard coding tier

**Files:**
- Modify: `skills/implement/SKILL.md` (Phase 2 and Phase 3 sections), `skills/plan/SKILL.md` (Step 4)
- Modify: `.superpowers/sdd/progress.md` (deferred ledger entry)
- Test: `tests/unit/test_plugin_manifests.py` (`test_implement_skill_contract`, `test_plan_skill_contract`)

**Interfaces:**
- Consumes: the tier vocabulary from Task 1 (`model-tier policy`, `top tier`, `Model:` lines).
- Produces: handoff directives that make `superpowers:writing-plans` emit per-task `Model:` lines and `superpowers:subagent-driven-development` resolve them at dispatch.

- [ ] **Step 1: Extend the contract tests (failing first)**

In `tests/unit/test_plugin_manifests.py`, add to the needle tuple in `test_plan_skill_contract` (after `"non-fatal",`):

```python
        "model-tier",
```

and add to the needle tuple in `test_implement_skill_contract` (after `"/omc:explain",`):

```python
        "model-tier policy",
        "`Model:`",
        "top tier",
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_plan_skill_contract tests/unit/test_plugin_manifests.py::test_implement_skill_contract -q`
Expected: both FAIL — `plan skill missing 'model-tier'`, `implement skill missing 'model-tier policy'`.

- [ ] **Step 3: Add the Phase 2 directive to skills/implement/SKILL.md**

At the end of the `## Phase 2 — plan` section, append this paragraph:

```markdown
Pass this directive to writing-plans verbatim: "Per the behavior layer's
model-tier policy (AGENTS.md, Model selection), every task in the plan
carries a `Model:` line naming its tier — `top tier` for spec, review, and
judging tasks; `standard coding tier` as the floor for coding tasks;
`heavy coding tier` for bigger coding tasks (multi-file, architecturally
tricky, or ambiguous). Tier names only, never pinned model ids."
```

- [ ] **Step 4: Add the Phase 3 directive to skills/implement/SKILL.md**

In the `## Phase 3 — build` section, append this paragraph immediately after the sentence ending "its own checkpoints and reviews apply.":

```markdown
Dispatch each task's subagent with its `Model:` tier resolved against the
provider's current lineup (the Agent tool's model parameter); reviewer and
judge subagents always get the top tier. Where the harness cannot switch
per-subagent models, proceed on the session model — never substitute a
cheaper tier. Plans missing `Model:` lines fall back to the behavior
layer's model-tier policy directly.
```

- [ ] **Step 5: Add the Step 4 policy pointer to skills/plan/SKILL.md**

In `## Step 4 — hand off to brainstorming`, the handoff list currently reads "Invoke `superpowers:brainstorming` with: the user's seed, the primer, the presentation rule below, and — only when `OMC_SLUG` is set …". Extend the always-passed items so the skill hands over the policy pointer too. Replace:

```markdown
Invoke `superpowers:brainstorming` with: the user's seed, the primer, the
presentation rule below, and — only when `OMC_SLUG` is set
```

with:

```markdown
Invoke `superpowers:brainstorming` with: the user's seed, the primer, the
presentation rule below, this model-tier pointer: "Any implementation plan
born from this brainstorm follows the behavior layer's model-tier policy
(AGENTS.md, Model selection): every task carries a `Model:` line naming
its tier; the cheap/fast tier is never used." — and, only when `OMC_SLUG`
is set
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS, including the order assertion in `test_implement_skill_contract` (the additions do not reorder the spec→plan→build→ship anchors).

- [ ] **Step 7: Record the deferred live-E2E in the ledger**

Append to `.superpowers/sdd/progress.md`:

```markdown
- [deferred] live-E2E for model-tier policy: a Docker-per-test run where a
  generated plan is asserted to carry per-task `Model:` tier lines (and no
  cheap-tier assignment). Deferred per the plan/implement precedent —
  contract needles cover the policy text; the generative behavior needs a
  real-LLM run.
```

- [ ] **Step 8: Gates, then commit**

Run: `uv run pytest tests/unit -q && uvx ruff format --check . && uvx ruff check .`
Expected: all green/clean.

```bash
git add skills/implement/SKILL.md skills/plan/SKILL.md tests/unit/test_plugin_manifests.py .superpowers/sdd/progress.md
git commit -m "feat: plan/implement skills apply the model-tier policy (red->green)"
```
