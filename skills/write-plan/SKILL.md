---
name: write-plan
description: Write, pressure-test (via /omc:explain) and commit the implementation plan for this slug from the committed design record, then stop with the session open. Optional step between /omc:design and /omc:implement; /omc:implement also invokes it when no plan exists.
---

# omc write-plan (conductor)

Invoked directly once `/omc:design` has committed the design record
(`$omc:write-plan` in Codex, `/omc:write-plan` in Claude), or by the
`/omc:implement` conductor when no plan exists yet. /omc:write-plan authorizes
writing, pressure-testing and committing the implementation plan for this
slug — nothing else. It never builds, never touches product code, never
pushes. When it ends, the session stays open and the user chooses how to
continue. It owns the plan the way `design` owns the record.

## User Input

```text
$ARGUMENTS
```

The whole input selects the mode:

- Exactly the single word `implement` → caller mode: the `/omc:implement`
  conductor is the caller (it invokes `/omc:write-plan implement`) and its
  authorization covers this run.
- Empty, or anything other than the single word `implement` → direct use.
  Any text is a detail the user attached (a late constraint); pass it to the
  Plan worker as context.

## Step 0 — externalize the flow (first action, no exceptions)

**Write the remaining steps into the task list now**: record gate →
existing-plan check → Plan worker writes → per-section pressure test →
commit the plan → close (direct use: summary and continuations; caller mode:
return the plan path). Mark each completed as you pass it. A committed plan
is an argument — to the user's next command in direct use, to the caller in
caller mode — not a destination. Reading the list is how you answer "am I
done in this authorized phase?".

## Step 1 — record gate

Run `omc internal design-record` and read its single `OMC_DESIGN_RECORD {…}`
line (never wrapped in markdown). `"ok": true` names the record at `path`:
read it in full; it is the design truth for the plan. Anything but
`"ok": true` → refuse with the verdict's `message` and stop: a missing record
means `/omc:design` has not run (point the user at it); an unclean record
means the design session died before its commit step; an ambiguous one is a
human decision. Never write a design record from here.

`$OMC_SLUG` below is the `slug` field of this verdict.

## Step 2 — existing plan

Look for a plan for this slug at `docs/superpowers/plans/*-$OMC_SLUG-plan.md`.
Whether git tracks it (`git ls-files -- <path>` prints it) decides what
happens, since every finished write-plan commits its plan:

- **tracked** (committed by an earlier write-plan, with or without the ticked
  boxes of a build in progress) → say that a plan exists at `path`, state the
  Step 5 continuations (caller mode: return the plan path), and write nothing.
  Never write a second dated plan; to re-plan, the user deletes the file
  first and runs `/omc:write-plan` again.
- **untracked** (an interrupted write-plan died before its commit, or an
  implement run that never committed it) → inspect it. A plan that
  already has ticked tasks is committed as is (Step 4) and closed (Step 5):
  no re-pressure-test, and never discard the ticks. Otherwise have the Step 3
  Plan worker finish its per-section pressure test instead of writing a new
  plan, then commit it (Step 4) and close (Step 5).
- none → Step 3.

## Step 3 — plan

Run `omc internal models` once and read its single `OMC_MODELS` verdict.
An `"ok": false` verdict is a blocker; report its `message`. Dispatch a
Plan worker with `tasks.plan.model` and, where supported,
`tasks.plan.effort`. On Claude, start a fresh worker and give it the
committed record, current task context and project conventions; a Codex
model override requires a fresh or partial-history fork, not a full-history
fork, with that same materialized context in a fresh fork. If the harness
cannot pin a worker model, use the orchestrator model and say so in one line.
Apply effort only where the worker harness accepts it.

The worker invokes `superpowers:writing-plans` on the committed record and
saves the plan to `docs/superpowers/plans/YYYY-MM-DD-$OMC_SLUG-plan.md`. Pass
this directive to writing-plans verbatim: "Every implementation task in
the plan carries a `Complexity: simple | medium | high` line, choosing one
value. Label multi-file, architecturally tricky or ambiguous coding work
`high`; choose `simple` for narrow routine work and `medium` otherwise.
The plan stores complexity, never model ids." Every task therefore carries
its `Complexity:` line; the build reads it.

Then, for each MAJOR section of the plan, the worker invokes `/omc:explain`
once to pressure-test the implementation choices — emphasis here is
implementation-level design, not architecture: should this be an enum? add a
parameter here, or reuse an existing mechanism? does this fit what the
codebase already has? Refine the section with the answers.

The worker returns the plan path and any CRITICAL questions or real
alternatives to the main session. The main session owns the user
conversation: it asks the returned questions in one batch, and the answers go
back to the Plan worker to finish the plan. A CRITICAL question is a required
answer: wait for it, then resume. Workers never ask the user or commit.

writing-plans closes by asking which execution approach to use; that question
is superseded here, so neither the worker nor the main session asks it —
Step 5 replaces it.

## Step 4 — commit

Commit the plan file only (`docs/superpowers/plans/…-plan.md`): `git add`
that one path, then `git commit -m <message> -- <plan path>` so the commit
carries that path alone — in direct use and in caller mode alike. Files the
user already staged stay staged and uncommitted.
Everything else in the tree is left exactly as found. write-plan runs no
tests, so it produces no tool drift of its own; anything that was already
dirty is named in the summary (caller mode: alongside the returned path),
never committed or discarded.

## Step 5 — close

**Direct use.** Post a short summary of the plan (task count, the
pressure-test findings that changed it), then end with this statement — a
statement, not a question; no approval is requested:

> The plan is committed. To build on this provider, type `/omc:implement`
> here (`$omc:implement` on Codex). To build on another provider, exit this
> session and run `omc implement --claude` or `omc implement --codex` in
> this worktree.

**Caller mode** (`$ARGUMENTS` is `implement`). Skip the summary and the
statement; return the plan path to `/omc:implement`. That path is an
argument to the caller, not the end of your turn: the caller continues to
its build in the same turn.

## Completion contract

`write-plan` is complete when the plan is committed and, in direct use, the
two continuations have been stated. Waiting for the user's next command is a
valid stop. Agreement, `ok`, or praise is not `/omc:implement`. Do not build
or push from here.
