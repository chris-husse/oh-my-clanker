# Improve /investigate + Per-Task Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the investigate finding-validation improvement (existing diff in the primary worktree) and add a per-task top-tier code-quality reviewer to the `omc:implement` build lifecycle.

**Architecture:** Prose-only changes to omc's skill layer. Part A applies an already-written patch to `skills/investigate/SKILL.md`. Part B adds a new prompt template `skills/implement/quality-reviewer.md` (same "template alongside the skill file" pattern as `skills/investigate/worker-mission.md`) and one directive paragraph in `skills/implement/SKILL.md` Phase 3 that dispatches it after each task's spec review, keeping `superpowers:subagent-driven-development` a black box.

**Tech Stack:** Markdown skill files, git. No Python, no tests-as-code — verification is structural (grep/apply checks).

**Spec:** `docs/superpowers/specs/2026-07-20-improve-investigate-and-code-skills-design.md`

## Global Constraints

- Never edit superpowers plugin files; all behavior arrives via directives passed at dispatch time.
- The primary worktree (`/Users/chriphus/OpenSource-Projects/oh-my-clanker`) is read-only for this plan — its dirty state stays exactly as the user left it.
- Machine contracts (`OMC_SLUG` / `OMC_STAGE` / `OMC_SQUASH`) are untouched.
- Quality reviewer runs on the **top tier**, never scaled down by diff size; fix loop caps at **2** rounds then surfaces to the user; Minor findings go to the progress ledger, not inline fixes.
- Sibling template files are referenced as "alongside this file" (repo convention, see `skills/investigate/SKILL.md` step 6).
- Per the behavior layer's model-tier policy (AGENTS.md, Model selection), every task below carries a `Model:` line naming its tier; the cheap/fast tier is never used.

---

### Task 1: Apply the investigate finding-validation patch

**Model:** standard coding tier

**Files:**
- Modify: `skills/investigate/SKILL.md` (via patch from the primary worktree)

**Interfaces:**
- Consumes: uncommitted diff in `/Users/chriphus/OpenSource-Projects/oh-my-clanker` touching only `skills/investigate/SKILL.md`
- Produces: nothing later tasks depend on (Part A is independent of Part B)

- [ ] **Step 1: Export the patch from the primary worktree (read-only)**

```bash
git -C /Users/chriphus/OpenSource-Projects/oh-my-clanker diff -- skills/investigate/SKILL.md > /tmp/investigate-validate.patch
wc -l /tmp/investigate-validate.patch
```

Expected: ~100 lines (the diff is 34 insertions / 8 deletions plus context). If the file is empty, STOP — the primary worktree changes are gone; surface to the user.

- [ ] **Step 2: Check the patch applies cleanly**

Run from the feature worktree root (`/Users/chriphus/OpenSource-Projects/oh-my-clanker.feature-improve-investigate-and-code-skills`):

```bash
git apply --check /tmp/investigate-validate.patch && echo APPLIES
```

Expected: `APPLIES`. Any conflict output → STOP and surface; never force.

- [ ] **Step 3: Apply**

```bash
git apply /tmp/investigate-validate.patch
git diff --stat
```

Expected: `skills/investigate/SKILL.md | 42 ++++++...` — 34 insertions, 8 deletions.

- [ ] **Step 4: Structural verification**

```bash
grep -c 'Validate + refine finding' skills/investigate/SKILL.md
grep -c 'Finding clear?' skills/investigate/SKILL.md
python3 -c "s=open('skills/investigate/SKILL.md').read(); assert s.count('{')==s.count('}'), 'dot braces unbalanced'; print('OK')"
grep -n 'Confident next step?' skills/investigate/SKILL.md || echo REMOVED
```

Expected: `3` (node declaration + two edges), `3` (declaration + two edge lines), `OK`, `REMOVED` (the old diamond is fully replaced).

- [ ] **Step 5: Verify the primary worktree is untouched**

```bash
git -C /Users/chriphus/OpenSource-Projects/oh-my-clanker status --short
```

Expected: still exactly ` M skills/investigate/SKILL.md` — we only read from it.

- [ ] **Step 6: Commit**

```bash
git add skills/investigate/SKILL.md
git commit -m "investigate: validate and refine findings via /omc:explain before reporting"
```

---

### Task 2: Create the quality-reviewer prompt template

**Model:** standard coding tier

**Files:**
- Create: `skills/implement/quality-reviewer.md`

**Interfaces:**
- Consumes: nothing from other tasks
- Produces: `skills/implement/quality-reviewer.md` with placeholders `[BRIEF_FILE]`, `[DIFF_FILE]`, `[BASE_SHA]`, `[HEAD_SHA]` and the verdict line grammar `Quality: Approved | Needs fixes` — Task 3's directive names this file and that verdict string verbatim.

- [ ] **Step 1: Write the file with exactly this content**

````markdown
# Code-Quality Reviewer Prompt Template

Used by `/omc:implement` Phase 3. After a task's spec review passes, the
orchestrator fills `[BRIEF_FILE]`, `[DIFF_FILE]`, `[BASE_SHA]`, and
`[HEAD_SHA]`, and dispatches this prompt as a subagent on the **top tier**
(model-tier policy; never scaled down by diff size). Critical/Important
findings go to the fix-subagent → re-review loop (2 rounds max, then
surface to the user); Minor findings are recorded in the progress ledger.

```
Subagent (general-purpose):
  description: "Quality review Task N"
  model: [top tier, resolved against the provider's current lineup]
  prompt: |
    You are a code-quality reviewer. Spec compliance has already been
    checked by a separate reviewer; your ONLY job is whether this code is
    well-built. Judge the diff as if it were a PR from a developer you
    don't trust yet.

    ## Context

    Task brief (context only — do not re-litigate spec compliance):
    [BRIEF_FILE]

    ## Diff Under Review

    **Base:** [BASE_SHA]
    **Head:** [HEAD_SHA]
    **Diff file:** [DIFF_FILE]

    Read the diff file once — it contains the commit list, a stat summary,
    and the full diff with surrounding context, and it is your view of the
    change. If the diff file is missing, fetch the diff yourself:
    `git diff --stat [BASE_SHA]..[HEAD_SHA]` and
    `git diff [BASE_SHA]..[HEAD_SHA]`.

    Your review is read-only: never mutate the working tree, the index,
    HEAD, or branch state. Inspect code outside the diff only to evaluate
    a concrete risk you can name — one focused check per named risk, and
    name both the risk and what you checked in your report. Checking that
    surrounding code already provides a helper the diff reimplements, or
    that the diff's naming clashes with the file's existing idiom, are
    exactly such checks.

    ## The Rubric — hunt for these specifically

    - **Narration comments**: comments restating the next line, "why my
      change is correct" commentary, section banners over trivial code.
      Comments must state constraints the code cannot show.
    - **Leftovers**: commented-out code, debug prints/scaffolding,
      TODO/FIXME introduced by this diff.
    - **Dead weight**: unused parameters, imports, variables, branches
      that cannot be reached; code only tests exercise.
    - **Speculative generality**: abstractions with one caller, options
      and config nothing reads, layers that only forward.
    - **Duplication**: verbatim or near-verbatim logic blocks — in the
      diff, or reimplementing something the surrounding code already has.
    - **Naming and idiom**: names or patterns inconsistent with the
      surrounding codebase; the diff should read like the file's author
      wrote it.
    - **Error handling**: swallowed errors; catch-log-continue where the
      caller needs the failure; error messages that leak internals.
    - **Tests**: assertions that cannot fail, tests that verify mocks
      instead of behavior, missing edge cases the task obviously implies.
    - **Altitude**: helpers that hide one line, functions doing three
      jobs, files growing without one clear responsibility.

    ## Severity Calibration

    Would a senior reviewer block the merge over this?

    - **Critical**: incorrect or fragile behavior a quality defect causes.
    - **Important**: maintainability damage you would block a merge over —
      duplicated logic blocks, swallowed errors, tests that assert
      nothing, dead code, narration-comment noise across the diff.
    - **Minor**: polish; one awkward name, one marginal comment.

    A stated rationale in code comments or the implementer's report never
    downgrades a finding. If the plan explicitly mandated the defect,
    report it as Important labeled plan-mandated — the human decides.

    Acknowledge what is genuinely well-built before the findings; accurate
    praise makes the rest of the report trusted.

    ## Output Format

    Your final message is the report itself — no preamble, no process
    narration.

    ### Strengths
    [Specific, with file:line]

    ### Findings
    #### Critical
    #### Important
    #### Minor
    For each: file:line, what is wrong, why it matters, the fix.

    ### Verdict
    Quality: Approved | Needs fixes
    [One-sentence reasoning]
```

**Placeholders:**

- `[BRIEF_FILE]` — the task brief the implementer worked from (context only)
- `[DIFF_FILE]` — the review package the controller already wrote for the
  task's spec review (`scripts/review-package BASE HEAD` output path); reuse
  it, never regenerate
- `[BASE_SHA]` — commit before the task (recorded before dispatching the
  implementer)
- `[HEAD_SHA]` — the task's final commit

**Reviewer returns:** Strengths, findings by severity, and the verdict line
`Quality: Approved` or `Quality: Needs fixes`.
````

- [ ] **Step 2: Verify placeholders and verdict grammar**

```bash
for p in BRIEF_FILE DIFF_FILE BASE_SHA HEAD_SHA; do grep -q "\[$p\]" skills/implement/quality-reviewer.md && echo "$p ok"; done
grep -c 'Quality: Approved | Needs fixes' skills/implement/quality-reviewer.md
```

Expected: four `ok` lines, then `2` (once in the prompt's output format, once implied by the header/footer — accept `1` or `2`, but at least the prompt's Output Format occurrence must exist).

- [ ] **Step 3: Commit**

```bash
git add skills/implement/quality-reviewer.md
git commit -m "implement: add top-tier code-quality reviewer prompt template"
```

---

### Task 3: Wire the quality gate into implement Phase 3

**Model:** standard coding tier

**Files:**
- Modify: `skills/implement/SKILL.md` (Phase 3 section, after the model-dispatch paragraph)

**Interfaces:**
- Consumes: `skills/implement/quality-reviewer.md` from Task 2 (named in the directive; verdict string `Quality: Approved` must match Task 2's grammar)
- Produces: final skill behavior; nothing downstream

- [ ] **Step 1: Insert the directive paragraph**

In `skills/implement/SKILL.md`, Phase 3 currently ends its second paragraph with:

```
cheaper tier. Plans missing `Model:` lines fall back to the behavior
layer's model-tier policy directly.
```

Immediately after that paragraph (before the "Phase 2 → 3 is NOT a gate" paragraph), insert:

```markdown
Pass this directive to subagent-driven-development verbatim: "After each
task's review passes, dispatch one additional reviewer: the code-quality
reviewer, built from `quality-reviewer.md` (alongside this skill file), on
the **top tier** — never scaled down by diff size. Reuse the task's
existing review package as `[DIFF_FILE]`. Critical/Important findings go
through the same fix-subagent → re-review loop as task-review findings;
the task is not complete until the quality reviewer reports
`Quality: Approved`. Minor findings are recorded in the progress ledger,
not fixed inline. After 2 fix rounds still `Needs fixes` → stop and
surface the findings to the user rather than looping."
```

- [ ] **Step 2: Verify wiring consistency**

```bash
grep -n 'quality-reviewer.md' skills/implement/SKILL.md
grep -n 'Quality: Approved' skills/implement/SKILL.md skills/implement/quality-reviewer.md | head -5
test -f skills/implement/quality-reviewer.md && echo TEMPLATE-EXISTS
```

Expected: the directive line in SKILL.md; matching verdict strings in both files; `TEMPLATE-EXISTS`.

- [ ] **Step 3: Dry read-through**

Read `skills/implement/SKILL.md` Phase 3 top to bottom and confirm the sequence reads coherently: dispatch implementer → spec review → quality review (top tier) → fix loop (cap 2) → ledger for Minors → task complete; and that it does not contradict the "Phase 2 → 3 is NOT a gate" paragraph that follows.

- [ ] **Step 4: Commit**

```bash
git add skills/implement/SKILL.md
git commit -m "implement: dispatch per-task top-tier quality reviewer after spec review"
```
