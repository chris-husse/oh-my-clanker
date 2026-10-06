---
name: audit
description: Use when a committed omc design record and implementation are ready for a conformance review before publication, either directly in the implementation session or in a fresh session launched by `omc review`.
---

# omc audit (conductor)

Invoked directly (`/omc:audit` in Claude, `$omc:audit` in Codex) or by the
bare `/omc:audit` seed from `omc review`. This command authorizes conformance
review, conformance fixes, record amendments and publication through `finish`.
Do not ask for permission between phases. A required CRITICAL answer pauses the
flow; after it arrives, resume without asking for another command.

## Phase -1 — externalize the flow (first action, no exceptions)

Write every remaining phase into the task list now: gates → conformance →
disposition → post-fix gate → trace and commit → finish. Mark each completed
as you pass it.
The record verdict, findings and trace are arguments to the next phase, never
the end of the turn.

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
is not product implementation. Keep uncommitted changes visible during the
review, and include them in the eventual commit when they are conformance
fixes; do not let unrelated dirty files become audit work.

Read `docs/superpowers/plans/*-$OMC_SLUG-plan.md` when it exists. The plan is
context; report its absence and continue. The committed design record is the
truth when the plan and record differ.

## Phase 1 — conformance

Walk the record and the branch diff against `origin/<base>` together. Check
promised behavior, decisions, exclusions and observable tests. `/omc:explain`
is available when a code path needs locating; it is optional per finding.

For each finding, name its kind: **code deviates from the record** (a promise
is missing, a decision was implemented differently, or excluded work was
added), or **record is stale** (the implementation is right but the record no
longer describes it). Cite the record section and the relevant `file:line`.
Classify it Important or Minor, using the same severity scale as `grug`.
Minor findings are listed and do not gate publication.

## Phase 2 — disposition

For each Important finding, act in this order:

1. Fix code to match the record when that is the correct conformance repair.
   Changes are **conformance only**: do not rewrite a working implementation
   for taste or a new provider's preferences.
2. For a **record is stale** finding, amend the record to describe the correct
   implementation only when the amendment contradicts no row of its
   `Decisions taken during brainstorm` table. Do not silently replace a
   brainstorm decision.
3. If neither disposition is justified, batch every unresolved issue into
   one numbered list of CRITICAL questions at the end of the pass. Wait for
   the required answers, then resume this phase. Assigned fix workers inherit
   the `/omc:audit` authorization.

Record a clear disposition for every Important finding. List Minor findings
without making them a gate.

## Phase 2a — post-fix gate

After the disposition pass, always invoke `/omc:check`, then `/omc:build`,
whether or not a conformance fix changed files. If disposition step 1 fixed
code in tracked product files, also invoke `/omc:verify`; otherwise the gate
ends after build. Before invoking verify, announce on its own line that the
project's `verify` stage is starting. Read each single-line `OMC_STAGE`
verdict: only `"passed": true` permits progression, including an unconfigured
stage with `"configured": false`. A missing verdict is a failure.

For any red stage, follow the behavior layer's stage-gate rule: bounded fix-forward, then a CRITICAL stop. Fold an unresolved gate into the existing
batched CRITICAL question. Do not invoke `finish` while the gate is red. If a
gate repair itself changes tracked product files, run verify too. A required
answer resumes this gate and the remaining phases under the same `/omc:audit`
authorization.

Before invoking `finish`, read `git status --porcelain` and classify every
remaining path against the implementation, known stage/configuration work, and
conformance fixes. Do not treat all dirt as unrelated. If a path is unrelated
or undisposed user work, preserve its bytes and do not stage, stash, restore,
delete, or publish it while authorization is unresolved. Stop and ask one batched
CRITICAL question listing every
such path. `finish`'s squash uses `git add -A`, so leaving it unstaged is not a
way to keep it out of publication. After the answer, re-read `git status --porcelain`.
Proceed only when no unrelated residue remains or the user explicitly authorizes
including it in the published change. An answer to leave it unchanged, left
untouched and outside publication does not resolve the conflict while it remains
in the worktree: explain that `finish` would include it and stay paused. Do not
promise to continue `finish` around it. Relevant dirty work still has to pass
`finish`'s rebase gate; do not bypass a failing gate.

## Phase 2b — trace and commit

In the design record, insert `## Implementation review` immediately before
the terminal `## Deliberate complexity` section. Keep that complexity section
last. Record the active auditor provider and date, followed by each finding's
severity, kind, record section, `file:line`, and disposition; if there were no
findings, write `conforms, no findings`. For a re-audit, append a new dated
entry below the previous review entry rather than replacing its history.

Commit the trace, record amendments and conformance fixes on this branch
before publishing. Stage only files belonging to this audit. The committed,
clean record is required by the design-record gate if `finish` fails at a
stage and someone later invokes `/omc:audit` again.

## Phase 3 — publish

Invoke `finish` (`/omc:finish`) and follow its full flow: rebase, squash,
check, build, verify, review, describe, push, ticket sync and follow-ups. Its
failing-stage behavior and conflict handoff apply unchanged. Do not stop at
the audit commit; `finish` is the publication route.

## Completion contract

`audit` is complete only when `finish` reaches its own completion contract:
the branch is pushed with a description, ticket sync has been attempted, and
follow-ups have been offered. Before ending the turn, read the task list and
continue any pending phase. A batched CRITICAL list awaiting answers is a
valid stop; once answered, continue the remaining phases under this command.
