---
name: implement
description: Lifecycle conductor from a committed design record to pushed branch - plan (pressure-tested via explain), subagent build, finish. Type it after /omc:design has committed the record, in the design session or in a fresh one launched by `omc implement`.
---

# omc implement (conductor)

Invoked directly (`$omc:implement` in Codex, `/omc:implement` in Claude) once
`/omc:design` has committed the design record — in the same session, or in a
fresh session that `omc implement [--claude|--codex]` seeded in this worktree.
Three phases, strictly in order; each phase is a black-box command call.
/omc:implement IS the user's approval to carry the committed record all the
way to a pushed branch: do not ask permission between phases. The only
interactive stops are genuine blockers and CRITICAL questions a plan cannot
answer. The user's authorization persists through a required answer: once a
critical question is resolved, resume the remaining phases without a new
command. Subagents assigned implementation tasks inherit this authorization;
they do not ask the user to invoke `/omc:implement` again. Generic sub-skill
requests for routine plan, execution-mode, task, or stage approval are
satisfied by this direct command.

## Phase -1 — externalize the flow (first action, no exceptions)

**Write the three phases into the task list now**, before the record gate:
plan → subagent build → ship. Mark each completed as you pass it.

This is omc's deepest nesting (`implement → finish → create-mr →
get-mr-description`), and every phase ends in a large, polished artifact — a
1,200-line plan, an MR description. **The bigger the artifact, the more it
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

Invoke `superpowers:writing-plans` on the committed record. Then, for each
MAJOR section of the plan, invoke `/omc:explain` once to pressure-test the
implementation choices — emphasis here is implementation-level design, not
architecture: should this be an enum? add a parameter here, or reuse an
existing mechanism? does this fit what the codebase already has? Refine the
section with the answers; surface real alternatives to the user.

Pass this directive to writing-plans verbatim: "Per the behavior layer's
model-tier policy (AGENTS.md, Model selection), every task in the plan
carries a `Model:` line naming its tier — `top tier` for spec, review, and
judging tasks; `standard coding tier` as the floor for coding tasks;
`heavy coding tier` for bigger coding tasks (multi-file, architecturally
tricky, or ambiguous). Tier names only, never pinned model ids."

## Phase 2 — build

Execute the plan via `superpowers:subagent-driven-development` — a fresh
subagent per task; its own checkpoints and reviews apply.

Dispatch each task's subagent with its `Model:` tier resolved against the
provider's current lineup (the Agent tool's model parameter); reviewer and
judge subagents always get the top tier. Where the harness cannot switch
per-subagent models, proceed on the session model — never substitute a
cheaper tier. Plans missing `Model:` lines fall back to the behavior
layer's model-tier policy directly.

After EACH task's subagent completes (and its reviews pass), run
`/omc:check` — the project-defined quick gate (build what the unit tests
need, run them) — before dispatching the next task. A failing check blocks
progression: fix forward until check passes. Never substitute ad-hoc test
commands for the stage; an unconfigured check is a pass, so this costs
nothing on projects without one. Full E2E (`/omc:verify`) is NOT part of
the per-task loop — it belongs to major milestones (finish runs it).

Phase 1 → 2 is NOT a gate: once the plan is written and pressure-tested,
start the subagent build immediately. Do not ask which execution approach
to use (writing-plans offers a choice; this conductor has already made it)
and do not ask permission to begin — the user typed /omc:implement, that
IS the instruction to build. The only stops are CRITICAL questions a plan
cannot answer and genuine blockers.

## Phase 3 — ship

Invoke the `finish` skill (`/omc:finish`): rebase, squash with the MR
description as the commit message, check/build/verify/review stages, push.

## Completion contract

`implement` is complete only when `finish` has run to its own completion
contract — branch pushed, described, ticket moved, follow-ups offered. Before
ending the turn, read the task list: a pending phase means continue, not stop.
