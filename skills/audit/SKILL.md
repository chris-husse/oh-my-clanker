---
name: audit
description: Use when a committed omc design record and implementation are ready for the fixed review (conformance first) and the fixed verify before publication, either directly in the implementation session or in a fresh session launched by `omc review`.
---

# omc audit (conductor)

Invoked directly (`/omc:audit` in Claude, `$omc:audit` in Codex), by the
bare `/omc:audit` seed from `omc review`, or by `/omc:implement` continuing in
the same turn with provider selection per step off. This command authorizes
the fixed review (conformance inside it) and the fixed verify, their fixes
and record amendments, and publication through `finish`.
Do not ask for permission between phases. A required CRITICAL answer pauses the
flow; after it arrives, resume without asking for another command.

## Phase -1 — externalize the flow (first action, no exceptions)

Write every remaining phase into the task list now: gates → dirty-file
classification → rebase-main --squash → build → check → review (conformance
first) → record review → build → check (when the review changed the tree)
→ verify → record verify → finish. Mark each
completed as you pass it.
The record verdict, stage verdicts and gate records are arguments to the next
phase, never the end of the turn.

## Phase 0 — gates and sources

Run `omc internal design-record` and read its single `OMC_DESIGN_RECORD {…}`
line. Only `"ok": true` passes: read the record at `path` in full. If false,
refuse with the verdict's `message` and stop. A missing record means
`/omc:design` must finish first; do not write a new record in this phase.

Resolve `<base>` as `finish` does: use `worktree.base_branch` from the repo's
`.omc/config.yaml` when readable, otherwise the default branch from
`git remote show origin`. Run `git fetch origin <base>`, then inspect the
changed paths in `git diff --name-only origin/<base>...HEAD`. If there is no
change outside `docs/superpowers/`, refuse with
"nothing to audit — run `/omc:implement` first". The design record by itself
is not product implementation. Uncommitted changes are classified in Phase 1,
before the squash folds them in; do not let unrelated dirty files become
audit work.

Read `docs/superpowers/plans/*-$OMC_SLUG-plan.md` when it exists. The plan is
context; report its absence and continue. The committed design record is the
truth when the plan and record differ.

## Phase 1 — dirty-file classification

Before the squash, read `git status --porcelain` and classify every path
against the implementation and known stage/configuration work. Do not treat
all dirt as unrelated. If a path is unrelated or undisposed user work,
preserve its bytes and do not stage, stash, restore, delete, or publish it
while authorization is unresolved. Stop and ask one batched CRITICAL
question listing every such path. Phase 2's squash, like `finish`'s, uses
`git add -A`, so it would publish unrelated work: leaving it unstaged is not
a way to keep it out of publication. After the answer, re-read
`git status --porcelain`. Proceed only when no unrelated residue remains or
the user explicitly authorizes including it in the published change. An
answer to leave it unchanged, left untouched and outside publication does
not resolve the conflict while it remains in the worktree: explain that the
squash would include it and stay paused. Do not promise to continue around
it.

## Phase 2 — squash and rebase

Invoke the **`rebase-main`** skill with `--squash` (`omc internal
rebase-main --squash`): it folds the classified uncommitted changes,
squashes the branch to one commit over its merge-base with `origin/<base>`,
and rebases that commit onto `origin/<base>`. Read its outcome by the rules
finish's Step 2 states for rc 1, rc 2 and rc 3, for the commit count after an
`"ok": true` verdict carrying `"squashed"`, and for a `note` verdict; audit
cites those rules and does not restate them. A failed audit leaves the
branch squashed; the stderr `pre-squash tip … (undo: …)` line restores the
original commits.

## Phase 3 — build → check

Invoke the **`build`** skill, then the **`check`** skill, on the squashed,
rebased commit: `build` → `check`. Every stage in Phases 3 to 5 runs by
finish's stage-running rules (finish's Step 3), which say how tool drift is
restored and how fixes, the files they created included, are amended into
the squashed commit. Only `"passed": true` passes.

Phases 2 to 5 are one conductor run with one fix-cycle budget under the
behavior layer's **stage-gate rule — bounded fix-forward, then a CRITICAL stop**;
the rule sets the bound, and a red `post_rebase` in Phase 2 spends that same
budget. A red build or check falls under its general clause: the fix is
amended and the sequence restarts at `build`. Do not invoke `finish` while
any stage is red. The `finish` that Phase 6 invokes is
a second conductor run with its own budget.

## Phase 4 — the fixed review

Before each review run, note the tree as finish's rule for a review that
changed the tree says; Phase 5 applies that rule. Invoke the **`review`** skill
(`/omc:review`): the fixed review, the only unconditional one. It runs
conformance first against the committed record (its step 2a: the conformance worker, disposition, fix workers, the
`## Implementation review` trace and its post-fix check), then the project
stage and omc's grug and human-review lenses. When the stage returns,
whatever its verdict, its changes (conformance and lens fixes with the
files they created, record amendments, the trace, waivers) are amended into
the squashed commit by those rules, once known tool drift is restored. That amend keeps the record committed, so a
run that stops on a CRITICAL question leaves a clean record for the
design-record gate when `/omc:audit` is invoked again.

On a passing verdict run `omc internal gate record review`. A red review
(`"passed": false`, or no verdict) falls under the stage-gate rule's
general clause: fixed, amended, and the sequence restarts at `build`, then
`check`, then the review again. Its own repair does not draw on the
budget; a red build or check after it does. The review's batched CRITICAL
questions pause the flow; their answers resume this phase.

## Phase 5 — the fixed verify

First apply finish's rule for a review that changed the tree (finish's
Step 3): when the review's amend changed the tree Phase 3 built and checked,
`build` → `check` run again on this run's budget before the verify, and a
red one there never returns to review.

Say on its own line that the project's `verify` stage is starting: it can
run for minutes, and a silent minute is a bug. Then invoke the **`verify`**
skill (`/omc:verify`): the fixed verify, the only unconditional one. A red
verify is repaired by the stage-gate rule's verify clause on this run's
budget, and it never returns to review: verify findings and verify fixes
never trigger a review. On green, restore known tool drift, amend the
verify fixes into the squashed commit, and run
`omc internal gate record verify`.

A `record` (here or in Phase 4) that does not exit 0 has decided nothing:
nothing is published; report the error and stop.

## Phase 6 — publish

Invoke `finish` (`/omc:finish`) and follow its full flow: squash, rebase,
build → check (skipped when the tree is the one Phase 5 recorded), the MR
gate, describe, push, ticket sync and follow-ups. `finish` repeats the
squash and rebase audit just did: on a squashed branch that costs one fetch,
keeps `finish` usable on its own, and picks up a base that moved in the
meantime for the MR gate. Its failing-stage behavior (the stage-gate rule's
fix-forward) and conflict resolution apply unchanged. Do not stop at the
audit's amends; `finish` is the publication route.

## Completion contract

`audit` is complete only when `finish` reaches its own completion contract:
the branch is pushed with a description, ticket sync has been attempted, and
follow-ups have been offered. Before ending the turn, read the task list and
continue any pending phase. A batched CRITICAL list awaiting answers is a
valid stop; once answered, continue the remaining phases under this command.
