---
name: implement
description: Lifecycle conductor from converged design to pushed branch - spec (hardened via explain), plan, subagent build, finish. Type it during brainstorming once the design is ready to become a spec.
---

# omc implement (conductor)

Typed during/after brainstorming, when the design has converged and is
ready to become a spec. Four phases, strictly in order; each phase is a
black-box command call. /omc:implement IS the user's approval to carry the
converged design all the way to a pushed branch: do not ask permission
between phases. The only interactive stops are CRITICAL spec findings
(see Phase 1) and genuine blockers.

## Phase 0 — resume check

If a spec for the current work already exists
(`docs/superpowers/specs/*-$OMC_SLUG-design.md` when `OMC_SLUG` is set, or
the topic's equivalent), do NOT silently resume — the spec may be
incomplete. Tell the user what was found and ask for guidance: resume at
the plan phase, re-run spec hardening on the existing doc, or start the
spec over.

## Phase 1 — spec

Invoke the internal `spec` skill. It writes the design doc, hardens it
section by section with /omc:explain, and commits it.

Phase 1 → 2 is NOT a gate. After the spec is committed, post a short
summary of what hardening found and continue straight into the plan
phase. Stop for the user ONLY if hardening surfaced a CRITICAL issue —
something that invalidates part of the converged design or forces an
architectural decision the brainstorm never settled. Cosmetic findings,
small fixes folded into the spec, and verification notes are not
critical; mention them in the summary and keep going. The user can always
interrupt and review the committed spec file at any time.

## Phase 2 — plan

Invoke `superpowers:writing-plans`. Then, for each MAJOR section of the
plan, invoke `/omc:explain` once to pressure-test the implementation
choices — emphasis here is implementation-level design, not architecture:
should this be an enum? add a parameter here, or reuse an existing
mechanism? does this fit what the codebase already has? Refine the section
with the answers; surface real alternatives to the user.

Pass this directive to writing-plans verbatim: "Per the behavior layer's
model-tier policy (AGENTS.md, Model selection), every task in the plan
carries a `Model:` line naming its tier — `top tier` for spec, review, and
judging tasks; `standard coding tier` as the floor for coding tasks;
`heavy coding tier` for bigger coding tasks (multi-file, architecturally
tricky, or ambiguous). Tier names only, never pinned model ids."

## Phase 3 — build

Execute the plan via `superpowers:subagent-driven-development` — a fresh
subagent per task; its own checkpoints and reviews apply.

Dispatch each task's subagent with its `Model:` tier resolved against the
provider's current lineup (the Agent tool's model parameter); reviewer and
judge subagents always get the top tier. Where the harness cannot switch
per-subagent models, proceed on the session model — never substitute a
cheaper tier. Plans missing `Model:` lines fall back to the behavior
layer's model-tier policy directly.

Phase 2 → 3 is NOT a gate: once the plan is written and pressure-tested,
start the subagent build immediately. Do not ask which execution approach
to use (writing-plans offers a choice; this conductor has already made it)
and do not ask permission to begin — the user typed /omc:implement, that
IS the instruction to build. The only stops are critical spec findings
(Phase 1) and genuine blockers.

## Phase 4 — ship

Invoke the `finish` skill (`/omc:finish`): rebase, squash with the MR
description as the commit message, build/verify/review stages, push.
