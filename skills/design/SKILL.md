---
name: design
description: Write the design record from a converged brainstorm, harden it section by section with /omc:explain and the grug lens, commit it, and stop with the session open. Type it when the brainstorm has converged; it is the first of the two authority words (/omc:implement is the second).
---

# omc design (conductor)

Invoked directly after a brainstorm has converged (`$omc:design` in Codex,
`/omc:design` in Claude). /omc:design IS the user's approval to write, harden
and commit the design record — and nothing else. It never plans, builds or
pushes. When it ends, the session stays open and the user chooses how to
continue.

## User Input

```text
$ARGUMENTS
```

An optional detail the user attached to the command (a late requirement, a
constraint). Fold it into the design before writing. If it contradicts the
converged design, it is a CRITICAL question: stop HERE, before Step 1 writes
anything, and ask it as one plain question — never a silent choice, never a
question deferred until after hardening. Resume at Step 0 once answered.

## Step 0 — externalize the flow (first action, no exceptions)

**Write the remaining steps into the task list now**: record check → Design
worker writes → per-section hardening → whole-record pass → iterate → commit and report →
wait for the implementation handoff. Mark each completed as you pass it. The
last task is literally to wait: a committed record is an argument to the
user's next command, not a destination. Reading the list is how you answer
"am I done in this authorized phase?".

## Step 1 — record check, then write

Run `omc internal design-record` and read its single `OMC_DESIGN_RECORD {…}`
line (never wrapped in markdown):

- `"reason": "missing"` → write the record (below).
- `"ok": true` or `"reason": "unclean"` → a record for this slug already
  exists at `path`: inspect it and continue hardening it (Step 2). Never
  write a second dated file.
- `"reason": "ambiguous"` → refuse with the verdict's `message`; two records
  for one slug is a human decision.
- `"reason": "no-prefix"` → this is not an omc worktree; refuse with the
  message.

Run `omc internal models` once and read its single `OMC_MODELS` verdict. An
`"ok": false` verdict is a blocker; report its `message`. Dispatch a Design
worker with `tasks.design.model` and, where the harness accepts it,
`tasks.design.effort`. On Claude, start a fresh worker and supply the seed,
all material answers, and the converged design text. On Codex, a model override
requires a fresh or partial-history fork; supply that same materialized context
when using a fresh fork. Do not request a model override on a full-history
fork. If the harness cannot pin a worker model, run it on the orchestrator
model and say so in one line; ignore effort where the harness has no worker
effort control.

The worker writes and hardens the record through Steps 1–4, returning its
path, findings and any CRITICAL questions to the main session. The main
session owns the user conversation and asks any returned questions in one
batch. After the answers arrive, send them back to the Design worker to
complete hardening; the main session then commits in Step 5 and posts the
Step 5 report and closing statement itself, in full — the worker's return is
input to that report, never a substitute for it. Workers never ask the user
or commit.

Write the design doc per repo conventions:
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` (topic = the `slug` field of the
OMC_DESIGN_RECORD verdict from Step 1 — it is derived from the branch, so
`omc implement` finds the same file; never a hand-made slug).

Every design doc ends with an unconditional final section:

```markdown
## Deliberate complexity

None.
```

It lists every Important grug finding the user waived during hardening,
each with its one-line reason. It reads `None.` when nothing was waived. Its
presence is guaranteed so that `review` can rely on it later.

## Step 2 — per-section hardening

For EACH section of the record, two calls:

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

## Step 3 — whole-record pass

Run `/omc:explain` once more over the complete record: does it cohere at a
high level, and does anything conflict with how the codebase already works?
Then invoke `grug spec <path>` over the committed-to-be file for
cross-section findings: total new surface, layer count, mechanisms with a
single user.

## Step 4 — iterate

Repeat steps 2–3 until neither explain nor grug surfaces real issues. Every
Important grug finding is dispositioned in this order — fix first, ask last:

1. **Fix, by Design.** The Design worker rewrites the section with the
   simpler alternative when that keeps the converged design. Use the
   `OMC_MODELS` Design choice for any replacement worker.
2. **Waive by record.** A finding the rewrite rejects because it contradicts
   an entry in the record's "Decisions taken during brainstorm" table is
   waived into "Deliberate complexity" citing that decision — the
   brainstorm already settled it, so it is not a CRITICAL question.
3. **Ask, batched.** The worker returns only what survives both to the main
   session as ONE numbered list of CRITICAL follow-up questions at the end
   of the pass. The main session asks the user — never one dialog per
   finding, never a silent choice on their behalf.
   What the user waives goes into "Deliberate complexity" with its reason.
   A CRITICAL question is a required answer: wait for it, then resume.

## Step 5 — commit, report, and wait

Commit the record if hardening changed it (a re-hardened record that is
already committed and unchanged has nothing to commit; say so instead of
failing) (`docs/superpowers/specs/…-design.md` only). A
grug-unavailable stop (Step 2, `grug skill unavailable — plugin stale? run
omc update`) is a hard stop: do NOT commit and do NOT report success; hand
the sentence to the user, whose remediation is `omc update` and a new
session.

Then post a short summary of what hardening found and changed, and end with
this statement — a statement, not a question; no approval is requested:

> The design record is committed. To continue on this provider, type
> `/omc:implement` here (`$omc:implement` on Codex). To continue on another
> provider, exit this session and run `omc implement --claude` or
> `omc implement --codex` in this worktree.

## Completion contract

`design` is complete when the record is committed and the two continuations
have been stated. Waiting for the user's later direct implementation handoff
is a valid stop (behavior layer: "Waiting for a required user answer or the
later implementation handoff is a valid stop"). Agreement, `ok`, or praise is
not that handoff. Do not plan, build, or push from here.
