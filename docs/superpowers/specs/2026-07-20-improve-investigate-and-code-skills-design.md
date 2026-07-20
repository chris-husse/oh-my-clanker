# Improve /omc:investigate + per-task Fable quality gate

**Date:** 2026-07-20 · **Slug:** improve-investigate-and-code-skills · **Status:** approved in session

## 1. Goal and scope

Two independent improvements to omc's prose skill layer, one branch:

- **Part A** — land the already-written improvement to `skills/investigate/SKILL.md`
  (currently an uncommitted diff in the primary worktree): findings must be
  validated and refined through `/omc:explain` before being reported or built on.
- **Part B** — add a per-task **code-quality gate** to the build lifecycle
  (`omc:implement` Phase 3): after each task passes its normal review, a separate
  top-tier (Fable-class) quality reviewer with omc's own clean-code rubric must
  approve the code before the task counts as done.

No Python changes; no machine contracts touched (`OMC_SLUG` / `OMC_STAGE` /
`OMC_SQUASH` unaffected). Superpowers skills remain black boxes — Part B composes
via dispatch directives, never by editing superpowers files.

## 2. Part A — investigate: validate findings via /omc:explain

The diff already exists (34 insertions, 8 deletions in
`skills/investigate/SKILL.md`) and is adopted as-is:

- The investigation-loop diagram gains a mandatory **"Validate + refine finding
  via /omc:explain"** node between "receive finding" and the confidence check;
  the decision diamond becomes "Finding clear?" with a re-query/refine edge back
  to "Decide next mission" and a conflict edge to "Pause, ask user".
- New prose block after the diagram: a worker returns *what the data shows*;
  `/omc:explain` establishes *whether the code can produce that, and what it
  means*. Every load-bearing finding loops finding → explain → refine until the
  code-mechanism and the evidence tell one coherent story. An unvalidated
  finding is a hypothesis and may not be reported or used as the basis for the
  next mission. Explain's answer confirms, reframes (rewrite the finding), or
  exposes a gap (next mission). Workers never do this — reconciling a finding
  against the whole codebase is the orchestrator's job.
- The confidence-rules table, red flags, and common mistakes each gain matching
  entries (ungrounded finding → validate first; explain reframed the finding →
  rewrite it and re-check anything built on the old read; shipping an
  unvalidated finding is a listed mistake).

Mechanically: apply the primary worktree's diff onto this feature branch
(`git diff` in the primary checkout → `git apply` here) and commit it on this
branch. **The primary worktree is left untouched** — the user cleans up its
dirty state themselves once the branch lands (explicit decision; omc never
discards the user's uncommitted changes).

## 3. Part B — per-task Fable quality gate

### Why the current pipeline lets garbage through

`superpowers:subagent-driven-development` already reviews each task (spec +
quality in one dispatch), but two things dilute it:

1. Its Model Selection section tells the orchestrator to scale reviewer models
   **down** ("use the least powerful model that can handle each role"; small
   diff → cheap reviewer).
2. The single reviewer's attention is dominated by spec compliance, with
   calibration that only blocks on "Important".

Result: mechanical tasks get mechanical reviews, and low-grade quality defects
(noise comments, dead code, copy-paste, pointless abstractions) survive to
human review.

### New component: `skills/implement/quality-reviewer.md`

A prompt template, same pattern as `skills/investigate/worker-mission.md` —
omc ships the HOW, the dispatching orchestrator fills the placeholders.

- **Role:** "You are a code-quality reviewer. Spec compliance has already been
  checked; your ONLY job is whether this code is well-built. Judge the diff as
  if it were a PR from a developer you don't trust yet."
- **Inputs (placeholders):** `[DIFF_FILE]` (the review package the controller
  already produces per task), `[BASE_SHA]` / `[HEAD_SHA]`, `[BRIEF_FILE]` (task
  brief, for context only).
- **The rubric** (explicit, itemized):
  - narration comments (restating the next line, "why my change is correct"
    comments), commented-out code, leftover debug scaffolding
  - dead code, unused parameters/imports, speculative generality (abstractions
    with one caller, config nothing reads)
  - verbatim or near-verbatim duplication of logic blocks
  - naming and idiom inconsistent with the surrounding codebase
  - swallowed errors; catch-log-continue where the caller needs the failure
  - tests that assert nothing, over-mocked tests that verify mocks, missing
    edge cases the task obviously implies
  - wrong altitude: helpers that hide one line, functions doing three jobs,
    files growing without one clear responsibility
- **Discipline:** read-only; diff-scoped with the same "one focused check per
  named risk" rule the task reviewer uses; every finding carries `file:line`,
  why it matters, and the fix; severity Critical / Important / Minor calibrated
  by "would a senior reviewer block the merge over this?".
- **Output:** Strengths, findings by severity, final verdict line
  `Quality: Approved | Needs fixes`.

### Wiring in `skills/implement/SKILL.md` (Phase 3)

Phase 3 gains one paragraph — a directive passed to
`superpowers:subagent-driven-development` verbatim, keeping it a black box:

> After each task's review passes, dispatch one additional reviewer: the
> code-quality reviewer, built from `skills/implement/quality-reviewer.md`, on
> the **top tier** (model-tier policy; never scaled down by diff size). Reuse
> the task's existing review package as `[DIFF_FILE]`. Critical/Important
> findings go through the same fix-subagent → re-review loop as task-review
> findings; the task is not complete until the quality reviewer reports
> Approved. Minor findings are recorded in the progress ledger, not fixed
> inline. After **2** fix rounds still "Needs fixes" → stop and surface the
> findings to the user rather than looping.

Semantics:

- The quality reviewer runs **after** the spec review passes — one gate at a
  time; fix loops never interleave two reviewers' findings.
- The existing task reviewer is untouched: it keeps catching spec drift on
  whatever tier the orchestrator picks. The top-tier pinning applies to the new
  quality pass, which is the one that needs taste.
- Where the harness cannot set per-subagent models, implement's existing
  fallback applies: proceed on the session model — never a cheaper tier.
- Scope is per-task inside `omc:implement` only. The final whole-branch review
  (superpowers) and the `/omc:review` stage proxy stay as they are: the
  per-task gate is where the garbage originates, and the branch-level surfaces
  already run top-tier under omc's model-tier policy.

## 4. What deliberately does not change

- `skills/build|verify|review/SKILL.md` (stage proxies) — untouched;
  `OMC_STAGE` stays frozen API.
- superpowers plugin files — never edited; all behavior arrives via dispatch
  directives from omc's skills.
- No project hook for the rubric (unlike `investigation-context`): the rubric
  is omc's opinionated HOW and ships fixed. If a project someday needs
  additions, that is a later `.omc/skills/quality-context` hook — noted, not
  built (YAGNI).
- `skills/investigate/worker-mission.md` — untouched; Part A's validation loop
  is orchestrator-side only.

## 5. Error handling

- Quality reviewer contradicts the spec reviewer (e.g. wants an abstraction
  removed that the plan mandated): plan-mandated items are reported labeled as
  such and surfaced to the user — the human decides, matching the task
  reviewer's existing calibration rule.
- Review package missing (script failure): the quality reviewer falls back to
  fetching the diff itself (`git diff BASE..HEAD`), the same fallback the task
  reviewer template uses.
- Part A: `git apply` conflict on this branch (base moved) → stop and surface,
  never force.

## 6. Testing / verification

Prose-skill changes, so verification is structural:

- the investigate diff applies cleanly and the updated dot diagram parses;
- `quality-reviewer.md` documents every placeholder it uses;
- the `implement/SKILL.md` directive names a file that actually exists at the
  stated path;
- a dry read-through of Phase 3 end-to-end stays coherent (dispatch → spec
  review → quality review → fix loop → cap → ledger);
- the repo's own stage gates (`/omc:build` → `/omc:verify` → `/omc:review`)
  run at finish time as usual.

## 7. Decisions taken in session

- Separate quality reviewer over hardening the existing task review or a
  code-simplifier fix pass (chosen via explicit fork question).
- Primary worktree's dirty state is the user's to clean; omc never resets it.
- Fix-loop cap: 2 rounds, then surface.
- Per-task scope only; no rubric injection into the whole-branch review.
