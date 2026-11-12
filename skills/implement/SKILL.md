---
name: implement
description: Lifecycle conductor from a committed design record to an unpublished implementation - plan (pressure-tested via explain), subagent build, handoff; with provider selection per step on it stops there, off (the default) continues into the next stage (/omc:audit). Type it after /omc:design has committed the record, in the design session or in a fresh one launched by `omc implement`.
---

# omc implement (conductor)

Invoked directly (`$omc:implement` in Codex, `/omc:implement` in Claude) once
`/omc:design` has committed the design record — in the same session, or in a
fresh session that `omc implement [--claude|--codex]` seeded in this worktree.
With provider selection per step off (the default), `/omc:design` or a
directly typed write-plan invokes it in the same turn; the committed record
plus that setting carry the same authority as the typed word (behavior
layer). Four phases, strictly in order; each phase is a black-box command call.
/omc:implement IS the user's approval to carry the committed record through
committed implementation on this branch: do not ask permission between phases. The only
interactive stops are genuine blockers and CRITICAL questions a plan cannot
answer. The user's authorization persists through a required answer: once a
critical question is resolved, resume the remaining phases without a new
command. Subagents assigned implementation tasks inherit this authorization;
they do not ask the user to invoke `/omc:implement` again. Generic sub-skill
requests for routine plan, execution-mode, task, or stage approval are
satisfied by this direct command.

## Phase -1 — externalize the flow (first action, no exceptions)

**Write the four phases into the task list now**, before the record gate:
plan → subagent build → milestone gate → handoff:
continue into `/omc:audit` when provider selection per step is off,
otherwise state the three continuations and wait. Mark each completed as you
pass it.

Every phase can end in a large, polished artifact — a
1,200-line plan or a completed build. **The bigger the artifact, the more it
reads as a destination**, when it is only an argument to the next phase. The
task list is what keeps the outer frames alive; without it this flow reliably
stops after the plan, and a half-run conductor is indistinguishable from a
broken one from the user's side.

## Phase 0 — the record gate

Run `omc internal design-record` and read its single `OMC_DESIGN_RECORD {…}`
line. `"ok": true` names the record at `path`: read it in full; it is the
design truth for every phase below. `"ok": false` → refuse with the verdict's
`message` and stop: a missing record means `/omc:design` has not run (point
the user at it); an unclean record means the design session died before its
commit step; an ambiguous one is a human decision. Never write a design
record from here.

## Phase 1 — plan

Always invoke `/omc:write-plan implement` as a black box (the `implement`
argument tells it implement is the caller) and take the plan path it
returns. That path is an argument to Phase 2, not the end of your turn.
write-plan owns the existing-plan check: when a plan already exists for this
slug it returns that plan's path (finishing and committing it first if an
interrupted run left it uncommitted) and never re-plans it; otherwise it
runs the Plan worker, the writing-plans directive, the per-section
`/omc:explain` pressure test and the plan commit. CRITICAL
questions it returns are asked by this main session; the answers go back to
write-plan to finish the plan.

Continue the build from the next unfinished task of the returned plan (task
1 when none are ticked).

## Phase 2 — build

Run `omc internal models` once and read its single `OMC_MODELS` verdict
before the first task dispatch. An `"ok": false` verdict is a blocker; report
its `message`. Keep this verdict; its `provider_selection_per_step` field
decides Phase 3's close.

Execute the plan via `superpowers:subagent-driven-development` — a fresh
subagent per task; its own checkpoints and reviews apply.

Require each implementation worker to commit each task's tested changes before
returning. The task commits belong to Phase 2, before the milestone gate; do
not defer them to the Phase 3 handoff commit. After the task review, confirm
the worker's commit exists before running that task's `/omc:check`.

For each implementation task, read its `Complexity:` value and dispatch the
worker with the matching `OMC_MODELS` task choice: `simple`, `medium`, or
`high`. A plan missing `Complexity:` defaults to `medium`. Dispatch spec,
code-quality and other reviewer or judge workers with `tasks.review` (Review).
Pass each choice's `model` and, where supported, `effort` (Codex
`reasoning_effort`). Use a fresh or partial-history Codex fork for a model
override, materializing task context for a fresh fork. Where the harness
cannot pin a worker model, use the orchestrator model and say so in one line;
ignore effort if the worker harness has no effort control. The main session
retains task sequencing, checkpoint reviews and the user-question dialog.

After EACH task's subagent completes (and its reviews pass), run
`/omc:check` — the project-defined quick gate (build what the unit tests
need, run them) — before dispatching the next task. A failing check blocks
progression: fix forward until check passes. Never substitute ad-hoc test
commands for the stage; an unconfigured check is a pass, so this costs
nothing on projects without one. Full E2E (`/omc:verify`) is not part of
the per-task loop; it runs once at the milestone gate below, and `finish`
runs all four stages again on the squashed commit.

Phase 1 → 2 is NOT a gate: once the plan path is in hand, start the
subagent build immediately. Do not ask which execution approach to use
(this conductor has already made that choice)
and do not ask permission to begin — the user typed /omc:implement (or the
chain invoked it), that IS the instruction to build. The only stops are CRITICAL questions a plan
cannot answer and genuine blockers.

## Phase 2b — milestone gate

Before `/omc:build`, confirm at least one task commit exists after the
design-record commit. A task commit is one that touches a path outside
`docs/superpowers/`; the plan commit alone never counts. If none exists,
Phase 2 is unfinished: finish and commit the implementation task before
entering this gate.

After the last task's subagent and reviews pass, its green `/omc:check`
supplies this milestone's check. Invoke `/omc:build`, then `/omc:verify` as
black-box project-stage proxies. Before invoking verify, announce on its own
line that the project's `verify` stage is starting. Read each single-line
`OMC_STAGE` verdict: only `"passed": true` permits progression, including
an unconfigured stage with `"configured": false`. A missing verdict is a
failure, even if the invocation otherwise appears successful.

For any red stage, follow the behavior layer's stage-gate rule: bounded fix-forward, then a CRITICAL stop. Do not enter Phase 3 or make a handoff
commit while the gate is red. With provider selection per step off too: a
red gate never reaches `/omc:audit`. On a red gate, keep those task commits;
the plan was already committed by `/omc:write-plan`; the gate blocks only
the handoff commit. After a required answer, resume this gate and the
remaining phases under the same `/omc:implement` authorization.

## Phase 3 — hand off

Commit the plan's ticked checkboxes and any tracked product or documentation
changes left by the implementation workers. Restore only known tool drift,
such as a lockfile changed by `uv run` or the milestone verify run; never
discard unknown work.
Verify the working tree is
clean and at least one task commit exists over the base — a commit that
touches a path outside `docs/superpowers/`, so the record and plan commits
never count. If either condition fails, resolve it before handoff.

Post a short implementation summary, then close by the `OMC_MODELS` verdict
Phase 2 read. If that verdict is no longer in context, read it again the same way before closing. Only an `OMC_MODELS` verdict with `"ok": true` and `"provider_selection_per_step": false` continues; a missing field or any other value stops as below.

- **Provider selection per step off** — print this one notice line, then
  invoke `/omc:audit` as a black box in this same turn. State no
  continuations.

> Provider selection per step is off — continuing to `/omc:audit`
> (turn on: `omc configure` → LLM → Provider Selection Per Step).

On Codex the command is spelled `$omc:audit`.

- **Otherwise** — state this continuation verbatim as a statement, not a
  question:

> The implementation is committed on this branch and not yet published. To
> audit and publish on this provider, type `/omc:audit` here (`$omc:audit`
> on Codex). To have another provider audit it, exit this session and run
> `omc review --claude` or `omc review --codex` in this worktree. To publish
> without an audit, type `/omc:finish`.

## Completion contract

`implement` is complete when the branch is committed, its working tree is
clean, and either the three continuations have been stated (provider
selection per step on) or `/omc:audit` has been invoked (off). This holds in
a fresh session seeded by `omc implement` too. With the setting on, waiting
for the audit handoff is a valid stop. Before ending the turn, read the task
list: a pending phase means continue, not stop.
