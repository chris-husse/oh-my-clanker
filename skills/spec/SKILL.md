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

Every design doc ends with an unconditional final section:

```markdown
## Deliberate complexity

None.
```

It lists every Important grug finding the user waived during hardening,
each with its one-line reason. It reads `None.` when nothing was waived. Its
presence is guaranteed so that `review` can rely on it later.

## Step 2 — per-section hardening

For EACH section of the spec, two calls:

1. Invoke `/omc:explain` with:

   > Does this proposed change make architectural sense in this codebase:
   > <section summary>? What existing components does it touch, and what
   > problems might occur?

2. Invoke the internal `grug` skill with `grug section <section text,
   followed by explain's answer as context>`. With explain's answer in hand,
   grug can say "reuse the existing X" instead of guessing.

   If the `grug` skill cannot be invoked (unknown skill, not listed, or it
   answers a well-formed payload with its usage line), stop hardening and
   report `grug skill unavailable — plugin stale? run omc update`. Never
   continue with explain-only hardening as if the lens had run.

Refine the section with both answers. Emphasis here is architecture,
purpose, general function, and whether each mechanism pays for itself —
implementation-level choices (enums, parameters, reuse) belong to the
plan phase, not here.

## Step 3 — whole-spec pass

Run `/omc:explain` once more over the complete spec: does it cohere at a
high level, and does anything conflict with how the codebase already works?
Then invoke `grug spec <path>` over the committed-to-be file for
cross-section findings: total new surface, layer count, mechanisms with a
single user.

## Step 4 — iterate

Repeat steps 2–3 until neither explain nor grug surfaces real issues. Every
Important grug finding is dispositioned in this order — fix first, ask last:

1. **Fix, by the top tier.** The top-tier model (the behavior layer's
   model-tier policy, `AGENTS.md` Model selection) rewrites the section with
   the simpler alternative when that keeps the converged design. Dispatch it
   as a top-tier subagent where the harness can pick a model per subagent;
   otherwise the session model does it — never a cheaper tier.
2. **Waive by record.** A finding the rewrite rejects because it contradicts
   an entry in the record's "Decisions taken during brainstorm" table is
   waived into "Deliberate complexity" citing that decision — the
   brainstorm already settled it, so it is not a CRITICAL question.
3. **Ask, batched.** Only what survives both goes to the user, as ONE
   numbered list of CRITICAL follow-up questions at the end of the pass —
   never one dialog per finding, never a silent choice on their behalf.
   What the user waives goes into "Deliberate complexity" with its reason.

## Step 5 — commit & report

Commit the spec and post a short summary of what hardening found and
changed. When run under /omc:implement, do NOT wait for user review:
continue to the next phase unless hardening surfaced a CRITICAL issue —
one that invalidates part of the converged design or forces an
architectural decision the brainstorm never settled (those go to the user
as explicit questions, per Step 4). A grug-unavailable stop (Step 2,
`grug skill unavailable — plugin stale? run omc update`) is a hard stop as
well, under /omc:implement included: do NOT commit the spec and do NOT begin
the next phase; hand the sentence to the user, whose remediation is
`omc update` and a new session. Invoked standalone, end here and hand the
committed spec back to the user.
