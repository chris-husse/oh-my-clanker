# omc plan + implement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/omc:plan` (primer setup stage in front of superpowers:brainstorming) and `/omc:implement` (lifecycle conductor: spec → plan → subagents → finish, with an internal `omc:spec` skill), and rewire `/omc:start` to hand off to `omc:plan`.

**Architecture:** Entirely in the skills half — three new `skills/<name>/SKILL.md` files plus a step-4 rewrite in `skills/start/SKILL.md`. Skills compose by calling user-facing commands as black boxes (`/omc:explain`, `superpowers:*`, `/omc:finish`); internal skills are invoked only by their owner (`implement` owns `spec`). No Python changes.

**Tech Stack:** Markdown skills, pytest contract tests in `tests/unit/test_plugin_manifests.py`.

**Spec:** `docs/superpowers/specs/2026-07-17-omc-plan-implement-design.md`

## Global Constraints

- Skills call other skills ONLY as user-facing commands — never invoke another skill's internals (`gitnexus-*`) or project hooks (`.omc/skills/explain-context`) from a caller.
- Internal skills carry the exact frontmatter phrase `Internal — used by /omc:<owner>; not meant for direct invocation.`
- No `pytest.skip`/`skipif` anywhere — tests run or fail.
- Machine contracts unchanged: no edits to OMC_SLUG/OMC_STAGE/OMC_SQUASH producers.
- No new dependencies; no changes under `src/omc/` (single exception: Task 6 edits the `INTERNAL_AGENTS_MD` template in `src/omc/agentsmd.py` — user-directed scope addition).
- Run all commands from the repo root (the feature worktree).
- Deferred (record in ledger, do not build now): live-E2E scenarios for implement/spec-hardening; the existing `test_e2e_start.py` judge rubric already tolerates the rewired flow ("moves toward brainstorming ... or explains which prerequisite blocked it") and needs no change.

---

### Task 1: `/omc:plan` skill

**Files:**
- Create: `skills/plan/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (USER_FACING_SKILLS tuple + new test)

**Interfaces:**
- Consumes: existing `/omc:explain` command (black box), `superpowers:brainstorming`.
- Produces: user-facing skill name `plan` — referenced by Task 2 (`skills/start/SKILL.md` step 4) and documented in Task 5.

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_plugin_manifests.py`, add `"plan"` to the `USER_FACING_SKILLS` tuple (after `"start"`), and append this test after `test_explain_user_facing_contract`:

```python
def test_plan_skill_contract():
    text = (ROOT / "skills" / "plan" / "SKILL.md").read_text()
    for needle in (
        "/omc:explain",
        "superpowers:brainstorming",
        "primer",
        "$ARGUMENTS",
        "OMC_SLUG",
        "non-fatal",
    ):
        assert needle in text, f"plan skill missing {needle!r}"
    # composition rule: explain is called as a command, never unpacked
    assert "never reach into" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q -k "plan or frontmatter"`
Expected: FAIL — `FileNotFoundError` for `skills/plan/SKILL.md` in both `test_skills_have_frontmatter` and `test_plan_skill_contract`.

- [ ] **Step 3: Create `skills/plan/SKILL.md`**

```markdown
---
name: plan
description: Setup stage around superpowers:brainstorming - one /omc:explain pass over the work context builds a project primer, then the primed brainstorm starts. Invoked by /omc:start after context gathering; also works standalone.
---

# omc plan (brainstorm setup)

## User Input

​```text
$ARGUMENTS
​```

`$ARGUMENTS` is the work context: the ticket recap passed by `/omc:start`,
or a free-text description when invoked standalone. Empty → ask the user
what they want to plan, and use their answer as the context.

## Step 1 — explain pass (one question, black box)

Compose exactly ONE question from the context:

> Which parts of this codebase are relevant to: <goal>? Cover the
> components involved, where the relevant docs/design records live, and
> conventions that constrain changes there.

Invoke `/omc:explain` with that question and collect its synthesized
answer. Call it as a command — never reach into its internals
(`gitnexus-*`) or its project hooks (`.omc/skills/explain-context`).

Every outcome is non-fatal:

- Full answer → goes into the primer verbatim.
- explain relays "no index — run `/omc:index` first" → the primer records
  that exact line, so the brainstorm knows graph grounding is absent.
- Any other failure → the primer records "explain unavailable — <reason>".

## Step 2 — assemble the primer

A short structured block containing, in order:

1. The work context (from `$ARGUMENTS`).
2. explain's answer (or its absence note).
3. Standing pointers: `docs/superpowers/specs/` (prior design records),
   `docs/superpowers/plans/` (implementation plans), and
   `.omc/docs/gitnexus/docs/` (generated LLM docs) when present.

## Step 3 — seed

Ask the user for their initial thinking / seed for this work — AFTER the
primer exists, so they can react to what the codebase already says.

## Step 4 — hand off to brainstorming

Invoke `superpowers:brainstorming` with: the user's seed, the primer, and —
only when `OMC_SLUG` is set (`echo "$OMC_SLUG"`) — this doc-naming
directive: "Use the topic slug `$OMC_SLUG` so the design doc lands at
`docs/superpowers/specs/YYYY-MM-DD-$OMC_SLUG-design.md` and the plan at
`docs/superpowers/plans/YYYY-MM-DD-$OMC_SLUG-plan.md`."

This skill prepares and hands off — it never designs, never writes code,
and never writes to the tracker.
```

(Remove the zero-width markers `​` around the inner code fence when writing the file — the inner fence is a plain triple-backtick block.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add skills/plan/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: /omc:plan primes brainstorming via one explain pass (red->green)"
```

---

### Task 2: rewire `/omc:start` step 4 to hand off to `omc:plan`

**Files:**
- Modify: `skills/start/SKILL.md` (frontmatter description + Step 4)
- Modify: `tests/unit/test_plugin_manifests.py:121-130` (`test_start_skill_contract`)

**Interfaces:**
- Consumes: skill name `plan` from Task 1.
- Produces: `start` no longer names `superpowers:brainstorming`; it names `omc:plan`.

- [ ] **Step 1: Update the contract test (failing first)**

In `test_start_skill_contract`, replace the needle `"superpowers:brainstorming"` with `"omc:plan"`, and add a guard that the old direct handoff is gone:

```python
def test_start_skill_contract():
    text = (ROOT / "skills" / "start" / "SKILL.md").read_text()
    for needle in (
        "OMC_SLUG",
        "omc:plan",
        "omc start",
        "$ARGUMENTS",
        "merge-base",
    ):
        assert needle in text, f"start skill missing {needle!r}"
    # start hands off to plan; plan owns the brainstorming handoff now
    assert "Invoke `superpowers:brainstorming`" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py::test_start_skill_contract -q`
Expected: FAIL — `start skill missing 'omc:plan'`.

- [ ] **Step 3: Edit `skills/start/SKILL.md`**

Frontmatter description becomes:

```yaml
description: Session-side half of `omc start` - gather ticket context, verify base freshness, and hand off to omc:plan. Seeded automatically by the omc CLI; invoked cold it redirects to the shell command.
```

Replace the whole `## Step 4 — hand off to brainstorming` section (heading through the closing line "This skill prepares and hands off — it never designs or writes code itself.") with:

```markdown
## Step 4 — hand off to plan

1. Print a compact summary: ticket (key, title, 2–3 sentences),
   surroundings, doc list, and the workspace (branch + worktree path).
2. Invoke the `omc:plan` skill with the gathered context recap. `plan`
   runs the explain pass, asks the user for their seed, and starts the
   primed brainstorm.

This skill prepares and hands off — it never designs or writes code itself.
```

Steps 0–3 are untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/start/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: /omc:start hands off to omc:plan instead of brainstorming directly (red->green)"
```

---

### Task 3: internal `omc:spec` skill

**Files:**
- Create: `skills/spec/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (INTERNAL_SKILLS tuple + new test)

**Interfaces:**
- Consumes: `/omc:explain` (black box).
- Produces: internal skill name `spec`, invoked only by `implement` (Task 4).

- [ ] **Step 1: Write the failing test**

Add `"spec"` to the `INTERNAL_SKILLS` tuple (after `"squash"`), and append:

```python
def test_spec_skill_contract():
    text = (ROOT / "skills" / "spec" / "SKILL.md").read_text()
    for needle in (
        "/omc:explain",
        "EACH section",
        "whole-spec",
        "architectural",
        "follow-up",
        "review",
    ):
        assert needle in text, f"spec skill missing {needle!r}"
    # spec-phase emphasis is architecture; implementation choices are plan-phase
    assert "plan phase" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q -k "spec or internal"`
Expected: FAIL — `FileNotFoundError` for `skills/spec/SKILL.md`.

- [ ] **Step 3: Create `skills/spec/SKILL.md`**

```markdown
---
name: spec
description: Internal — used by /omc:implement; not meant for direct invocation. Write the design doc from a converged brainstorm, then harden it section by section with /omc:explain until it is rock solid.
---

# omc spec (internal)

Precondition: a converged brainstorm in the current session — the design
was agreed with the user.

## Step 1 — write

Write the design doc per repo conventions:
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` (topic = `$OMC_SLUG`
when set, else a short feature slug).

## Step 2 — per-section hardening

For EACH section of the spec, invoke `/omc:explain` with:

> Does this proposed change make architectural sense in this codebase:
> <section summary>? What existing components does it touch, and what
> problems might occur?

Refine the section with the answer. Emphasis here is architecture, purpose,
and general function — implementation-level choices (enums, parameters,
reuse) belong to the plan phase, not here.

## Step 3 — whole-spec pass

Run `/omc:explain` once more over the complete spec: does it cohere at a
high level, and does anything conflict with how the codebase already works?

## Step 4 — iterate

Repeat steps 2–3 until explain stops surfacing real issues. Surface genuine
architectural decisions to the user as explicit follow-up questions — never
make silent choices on their behalf.

## Step 5 — gate

Commit the spec, then ask the user to review it before the plan phase
begins. Do not proceed without their approval.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS (including `test_internal_skills_marked_internal` picking up `spec`).

- [ ] **Step 5: Commit**

```bash
git add skills/spec/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: internal omc:spec hardens the design doc via explain loops (red->green)"
```

---

### Task 4: `/omc:implement` skill

**Files:**
- Create: `skills/implement/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (USER_FACING_SKILLS tuple + new test)

**Interfaces:**
- Consumes: internal skill `spec` (Task 3), `superpowers:writing-plans`, `superpowers:subagent-driven-development`, `/omc:finish`.
- Produces: user-facing skill name `implement`, documented in Task 5.

- [ ] **Step 1: Write the failing test**

Add `"implement"` to the `USER_FACING_SKILLS` tuple (after `"plan"`), and append:

```python
def test_implement_skill_contract():
    text = (ROOT / "skills" / "implement" / "SKILL.md").read_text()
    for needle in (
        "`spec`",
        "writing-plans",
        "subagent-driven-development",
        "finish",
        "silently resume",
        "/omc:explain",
    ):
        assert needle in text, f"implement skill missing {needle!r}"
    # phases run strictly spec -> plan -> build -> ship
    order = [
        text.index("`spec`"),
        text.index("writing-plans"),
        text.index("subagent-driven-development"),
        text.index("`finish`"),
    ]
    assert order == sorted(order), "implement must order spec -> plan -> build -> ship"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q -k implement`
Expected: FAIL — `FileNotFoundError` for `skills/implement/SKILL.md`.

- [ ] **Step 3: Create `skills/implement/SKILL.md`**

```markdown
---
name: implement
description: Lifecycle conductor from converged design to pushed branch - spec (hardened via explain), plan, subagent build, finish. Type it during brainstorming once the design is ready to become a spec.
---

# omc implement (conductor)

Typed during/after brainstorming, when the design has converged and is
ready to become a spec. Four phases, strictly in order; each phase is a
black-box command call. All existing human gates stay interactive.

## Phase 0 — resume check

If a spec for the current work already exists
(`docs/superpowers/specs/*-$OMC_SLUG-design.md` when `OMC_SLUG` is set, or
the topic's equivalent), do NOT silently resume — the spec may be
incomplete. Tell the user what was found and ask for guidance: resume at
the plan phase, re-run spec hardening on the existing doc, or start the
spec over.

## Phase 1 — spec

Invoke the internal `spec` skill. It writes the design doc, hardens it
section by section with /omc:explain, and ends at the user spec-review
gate.

## Phase 2 — plan

Invoke `superpowers:writing-plans`. Then, for each MAJOR section of the
plan, invoke `/omc:explain` once to pressure-test the implementation
choices — emphasis here is implementation-level design, not architecture:
should this be an enum? add a parameter here, or reuse an existing
mechanism? does this fit what the codebase already has? Refine the section
with the answers; surface real alternatives to the user.

## Phase 3 — build

Execute the plan via `superpowers:subagent-driven-development` — a fresh
subagent per task; its own checkpoints and reviews apply.

## Phase 4 — ship

Invoke the `finish` skill (`/omc:finish`): rebase, squash with the MR
description as the commit message, build/verify/review stages, push.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/implement/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: /omc:implement conducts spec -> plan -> subagents -> finish (red->green)"
```

---

### Task 5: README + ledger + full gate

**Files:**
- Modify: `README.md` (Usage section, lines ~58 and ~76)
- Modify: `.superpowers/sdd/progress.md` (append ledger entries)

**Interfaces:**
- Consumes: everything above.
- Produces: user-facing docs current; deferred items recorded.

- [ ] **Step 1: Update README Usage flow**

Replace the sentence in the paragraph at line ~58 — `and then hands off to `superpowers:brainstorming` with that context plus your own seed thinking.` — with:

```markdown
and then hands off to `/omc:plan`, which runs one `/omc:explain` pass over the ticket ("which parts of this codebase are relevant to this?"), bundles the answer with pointers to prior design records into a project primer, asks for your own seed thinking, and starts a `superpowers:brainstorming` session that already knows the codebase. When the brainstorm converges, type `/omc:implement`: it writes the spec and hardens it section-by-section through `/omc:explain`, walks the implementation plan through the same scrutiny, builds via subagents, and ends by invoking `/omc:finish`.
```

In the prerequisites bullet at line ~76, replace `— `/omc:start` hands off to it directly` with `— `/omc:start` reaches it through `/omc:plan``.

- [ ] **Step 2: Append to the build ledger**

Append to `.superpowers/sdd/progress.md`: one entry per task (1–4) with its review verdict, plus a deferred item: "live-E2E scenarios for /omc:implement and spec-hardening (token-funded tier); test_e2e_start rubric already covers the rewired plan handoff."

- [ ] **Step 3: Run the full fast gate**

Run: `just build`
Expected: exit 0 — format clean, lint clean, all unit tests pass.

- [ ] **Step 4: Commit**

```bash
git add README.md .superpowers/sdd/progress.md
git commit -m "docs: README usage flow for /omc:plan + /omc:implement; ledger entries"
```

---

### Task 6: model-selection doctrine in the behavior layer

**Files:**
- Modify: `src/omc/agentsmd.py` (the `INTERNAL_AGENTS_MD` template string only)
- Modify: `tests/unit/test_agentsmd.py` (extend the created-chain content assertions)

**Interfaces:**
- Consumes: nothing from other tasks (independent).
- Produces: behavior-layer ground rule read by every harness in omc-managed repos.

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_agentsmd.py`, in the test that asserts the internal layer's content (the one containing `assert "rebase-main" in text and "OMC_" in text`), add directly after that line:

```python
    assert "omc configure" in text and "subagent" in text.lower()  # model doctrine
    assert "efficient" in text  # execution tier named
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_agentsmd.py -q`
Expected: FAIL — `assert "omc configure" in text ...` (the template has no model doctrine yet).

- [ ] **Step 3: Add the doctrine bullet to `INTERNAL_AGENTS_MD`**

In `src/omc/agentsmd.py`, inside the `INTERNAL_AGENTS_MD` string, insert this bullet between the "Ask the graph, not grep" bullet and the "Machine contracts are sacred" bullet:

```markdown
- **Model selection**: the main session runs the model chosen in
  `omc configure` — never second-guess it. When dispatching subagents,
  assess each task and pick the model that fits: the heavyweight model for
  planning/design, reviews, and judging subagent output; efficient models
  for well-specified execution work.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_agentsmd.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/omc/agentsmd.py tests/unit/test_agentsmd.py
git commit -m "feat: behavior layer teaches per-task subagent model selection (red->green)"
```
