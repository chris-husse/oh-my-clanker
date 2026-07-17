---
name: spec
description: Internal — used by /omc:implement; not meant for direct invocation. Write the design doc from a converged brainstorm, then harden it section by section with /omc:explain until it is rock solid.
---

# omc spec (internal)

Precondition: a converged brainstorm in the current session — the design
was agreed with the user.

## Step 1 — write

Write the design doc per repo conventions:
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` (topic = `$OMC_SLUG`
when set, else a short feature slug).

## Step 2 — per-section hardening

For EACH section of the spec, invoke `/omc:explain` with:

> Does this proposed change make architectural sense in this codebase:
> <section summary>? What existing components does it touch, and what
> problems might occur?

Refine the section with the answer. Emphasis here is architecture, purpose,
and general function — implementation-level choices (enums, parameters,
reuse) belong to the plan phase, not here.

## Step 3 — whole-spec pass

Run `/omc:explain` once more over the complete spec: does it cohere at a
high level, and does anything conflict with how the codebase already works?

## Step 4 — iterate

Repeat steps 2–3 until explain stops surfacing real issues. Surface genuine
architectural decisions to the user as explicit follow-up questions — never
make silent choices on their behalf.

## Step 5 — gate

Commit the spec, then ask the user to review it before the plan phase
begins. Do not proceed without their approval.
