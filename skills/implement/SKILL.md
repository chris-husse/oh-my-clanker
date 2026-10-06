---
name: implement
description: Lifecycle conductor from a committed design record to an unpublished implementation - plan (pressure-tested via explain), subagent build, handoff. Type it after /omc:design has committed the record, in the design session or in a fresh one launched by `omc implement`.
---

# omc implement (conductor)

Invoked directly (`$omc:implement` in Codex, `/omc:implement` in Claude) once
`/omc:design` has committed the design record — in the same session, or in a
fresh session that `omc implement [--claude|--codex]` seeded in this worktree.
Four phases, strictly in order; each phase is a black-box command call.
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
plan → subagent build → milestone gate → handoff. Mark each completed as you
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

If a plan already exists for this slug
(`docs/superpowers/plans/*-$OMC_SLUG-plan.md`), an earlier implement run was
interrupted: inspect it and continue from the next unfinished task instead of
writing a second plan.

## Phase 1 — plan

Run `omc internal models` once and read its single `OMC_MODELS` verdict.
An `"ok": false` verdict is a blocker; report its `message`. Dispatch a
Plan worker with `tasks.plan.model` and, where supported,
`tasks.plan.effort`. Give a fresh worker the committed record, current task
context and project conventions; a Codex model override requires a fresh or
partial-history fork, not a full-history fork. The worker invokes
`superpowers:writing-plans` on the committed record. Then, for each MAJOR
section of the plan, the worker invokes `/omc:explain` once to pressure-test the
implementation choices — emphasis here is implementation-level design, not
architecture: should this be an enum? add a parameter here, or reuse an
existing mechanism? does this fit what the codebase already has? Refine the
section with the answers. It returns the plan path and any CRITICAL questions
or real alternatives to the main session. The main session asks the user;
answers go back to the Plan worker. If the harness cannot pin a worker model,
use the orchestrator model and say so in one line. Apply effort only where
the worker harness accepts it.

Pass this directive to writing-plans verbatim: "Every implementation task in
the plan carries a `Complexity: simple | medium | high` line, choosing one
value. Label multi-file, architecturally tricky or ambiguous coding work
`high`; choose `simple` for narrow routine work and `medium` otherwise.
The plan stores complexity, never model ids."

## Phase 2 — build

Execute the plan via `superpowers:subagent-driven-development` — a fresh
subagent per task; its own checkpoints and reviews apply.

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

Phase 1 → 2 is NOT a gate: once the plan is written and pressure-tested,
start the subagent build immediately. Do not ask which execution approach
to use (writing-plans offers a choice; this conductor has already made it)
and do not ask permission to begin — the user typed /omc:implement, that
IS the instruction to build. The only stops are CRITICAL questions a plan
cannot answer and genuine blockers.

## Phase 2b — milestone gate

After the last task's subagent and reviews pass, its green `/omc:check`
supplies this milestone's check. Invoke `/omc:build`, then `/omc:verify` as
black-box project-stage proxies. Before invoking verify, announce on its own
line that the project's `verify` stage is starting. Read each single-line
`OMC_STAGE` verdict: only `"passed": true` permits progression, including
an unconfigured stage with `"configured": false`. A missing verdict is a
failure, even if the invocation otherwise appears successful.

For any red stage, follow the behavior layer's stage-gate rule: bounded fix-forward, then a CRITICAL stop. Do not enter Phase 3 or make a handoff
commit while the gate is red. After a required answer, resume this gate and
the remaining phases under the same `/omc:implement` authorization.

## Phase 3 — hand off

Commit the plan file and any tracked product or documentation changes left by
the implementation workers. Restore only known tool drift, such as a lockfile
changed by `uv run` or the milestone verify run; never discard unknown work.
Verify the working tree is
clean and at least one task commit exists over the base. If either condition
fails, resolve it before handoff.

Post a short implementation summary and state this continuation verbatim as a
statement, not a question:

> The implementation is committed on this branch and not yet published. To
> audit and publish on this provider, type `/omc:audit` here (`$omc:audit`
> on Codex). To have another provider audit it, exit this session and run
> `omc review --claude` or `omc review --codex` in this worktree. To publish
> without an audit, type `/omc:finish`.

## Completion contract

`implement` is complete when the branch is committed, its working tree is
clean, and the three continuations have been stated. Waiting for the audit
handoff is a valid stop. Before ending the turn, read the task list: a
pending phase means continue, not stop.
