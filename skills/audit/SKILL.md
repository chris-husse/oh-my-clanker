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

Write every remaining phase into the task list now: gates → dependencies → conformance →
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

## Phase 0.5 — dependencies

Run `omc internal workspace list` and read its single `OMC_WORKSPACE` verdict.
An error or missing verdict blocks this phase; surface its message. Only
`current_role == "master"` traverses dependencies. An empty workspace or a
dependency role means no child launch: continue to local Phase 1. Walk entries
with `role == "dependency"` in list order. Never launch the master as a child.
The master orchestrates only; all dependency edits belong to that repository's
own lifecycle session.

Use a fresh `omc internal workspace list` verdict for the completion test.
After a successful listing, apply these branches in order:

- No upstream: publication remains; launch the dependency audit even though
  `ahead` is `null` for this unpublished branch.
- Upstream exists and `ahead` is unknown: block and resolve the inspection
  failure before continuing. Unknown counts are never zero.
- Upstream exists and integer `ahead == 0`: skip this pushed dependency.
- Upstream exists and integer `ahead > 0`: launch the dependency audit.

An inspection failure blocks the phase; never map it to zero. Re-read the
listing after each child exits or manual recovery completes. The local
product-change refusal in Phase 0 still applies before this phase.

For each incomplete dependency, run `omc review --headless` from its exact
`worktree` directory, using the harness's background/continuation mechanism.
This is an ordinary foreground CLI process managed by the harness, not a daemon.
On its own line, announce which repository is pending; retain its output and
wait for process exit before inspecting artifacts. Poll through the harness's
continuation handle, keeping the user informed during long waits. Do not treat
transient dirt or a running child's output as a final outcome. After exit,
recheck the completion predicate. A restarted master uses the same predicate to
skip completed dependencies. Proceed to local Phase 1 only when all pass.
Never auto-close a dependency child worktree; the master owns workspace closure.

Inspect the final output for an unanswered CRITICAL question even when artifacts
appear complete: relay it verbatim and wait for the answer. For a nonzero exit
or early stop without completion, also relay the child's final output verbatim.
Never guess an answer or start another child while that question is pending.
A launch failure is an actionable failure, not evidence that a resumable session
exists: report its command, directory and error, resolve the launch blocker, then
retry. An early stop without a CRITICAL question needs an actionable diagnosis,
not a fabricated question. A nonzero exit still requires disposition even if the
artifacts look complete; inspect its error before progressing.

Once the required answer arrives, resume the same child session as follows:

- **Claude:** from that dependency directory, use print mode with
  `claude -p <answer> --resume <slug>-audit --output-format text`, preserving
  the child's configured model/effort, full provider environment (`OMC_SLUG`,
  `OMC_PROVIDER=claude`, provider title settings, `GITNEXUS_SHARED_STORE=off`)
  and implementation tool grants. Append `--allowed-tools` LAST with the complete
  `IMPLEMENT_ALLOWED_TOOLS` list from `omc.implement` (including its MCP grants).
  Pass the answer as one prompt argument immediately after `-p`; use an argv
  API, or shell-quote every argument with `shlex.quote`, never interpolate the
  answer into shell code. The initial resume handle is the name `<slug>-audit`.
  If that name is ambiguous, require the exact session ID or manual dependency
  resumption. Never substitute a fresh named session for a resume.
- **Codex:** headless resume is unverified. Give the exact dependency directory
  and `omc review` for manual recovery there. Wait for the user to finish that
  recovery, then recheck artifacts; do not invent a Codex resume command or
  claim live verification. The same manual fallback is available for an
  ambiguous Claude session when no exact ID can be established.

Keep resumed processes under the same background wait and artifact check loop.
Required answers continue the existing authorization; they do not require a new
master lifecycle invocation. No ledger completion flag is written.

## Phase 1 — conformance

Run `omc internal models` once and read its `OMC_MODELS` verdict before the
conformance pass. An `"ok": false` verdict blocks dispatch with its `message`.
Dispatch a Review worker with `tasks.review.model` and, where supported,
`tasks.review.effort` to walk the record and branch diff against
`origin/<base>` together. Supply the worker the record, plan when present, diff
and base branch. It checks promised behavior, decisions, exclusions and
observable tests; `/omc:explain` is available when a code path needs locating.
Dispatch this worker even when the implementation appears clean and no fix
worker will be needed. Do not perform the conformance judgment inline in the
main session. For Codex model overrides use a fresh or partial-history fork
with the needed context. If the harness cannot pin a worker model, use the
orchestrator model and say so in one line; apply effort only when supported.
If a worker cannot be dispatched at all, the conformance pass has not run and
the audit cannot proceed to disposition.

The Review worker returns findings to the main session. Have it name each
finding's kind: **code deviates from the record** (a promise is missing, a
decision was implemented differently, or excluded work was added), or
**record is stale** (the implementation is right but the record no longer
describes it). It cites the record section and relevant `file:line`, and
classifies each finding Important or Minor using the same severity scale as
`grug`. Minor findings are listed and do not gate publication.

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

Reuse the `OMC_MODELS` verdict from Phase 1 for audit fix workers.
The auditor labels each fix worker `Complexity: simple | medium | high` using
the same coding judgment as the implementation plan (multi-file, tricky or
ambiguous work is `high`; missing labels use `medium`). Dispatch that worker
with the corresponding `tasks.simple`, `tasks.medium`, or `tasks.high`
model and effort. The conformance pass already ran on Review in Phase 1.
For Codex model overrides use fresh or partial-history forks and supply the
needed record and finding context. If the harness cannot pin a worker model,
use the orchestrator model and say so in one line; apply effort only when
supported. Workers return CRITICAL questions to the main session, which
batches and asks them before resuming disposition.

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
