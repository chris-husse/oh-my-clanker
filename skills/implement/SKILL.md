---
name: implement
description: Lifecycle conductor from converged design to pushed branch - spec (hardened via explain), plan, subagent build, finish. Type it during brainstorming once the design is ready to become a spec.
---

# omc implement (conductor)

Typed during/after brainstorming, when the design has converged and is
ready to become a spec. Four phases, strictly in order; each phase is a
black-box command call. All existing human gates stay interactive.

## Phase 0 — resume check

If a spec for the current work already exists
(`docs/superpowers/specs/*-$OMC_SLUG-design.md` when `OMC_SLUG` is set, or
the topic's equivalent), do NOT silently resume — the spec may be
incomplete. Tell the user what was found and ask for guidance: resume at
the plan phase, re-run spec hardening on the existing doc, or start the
spec over.

## Phase 1 — spec

Invoke the internal `spec` skill. It writes the design doc, hardens it
section by section with /omc:explain, and ends at the user spec-review
gate.

## Phase 2 — plan

Invoke `superpowers:writing-plans`. Then, for each MAJOR section of the
plan, invoke `/omc:explain` once to pressure-test the implementation
choices — emphasis here is implementation-level design, not architecture:
should this be an enum? add a parameter here, or reuse an existing
mechanism? does this fit what the codebase already has? Refine the section
with the answers; surface real alternatives to the user.

## Phase 3 — build

Execute the plan via `superpowers:subagent-driven-development` — a fresh
subagent per task; its own checkpoints and reviews apply.

## Phase 4 — ship

Invoke the `finish` skill (`/omc:finish`): rebase, squash with the MR
description as the commit message, build/verify/review stages, push.
