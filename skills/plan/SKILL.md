---
name: plan
description: Setup stage around superpowers:brainstorming - one /omc:explain pass over the work context builds a project primer, then the primed brainstorm starts. Invoked by /omc:start after context gathering; also works standalone.
---

# omc plan (brainstorm setup)

This is a design discussion phase. Keep the full work context and every user
answer together. A complete design and agreement do not authorize a spec,
implementation, commit, or push. Wait for a later direct user
implementation skill invocation to cross that handoff (`$omc:implement` in
Codex, `/omc:implement` in Claude).

## User Input

```text
$ARGUMENTS
```

`$ARGUMENTS` is the work context: the ticket recap and complete input passed by `/omc:start`,
or a free-text description when invoked standalone. Empty → ask the user
what they want to plan, and use their answer as the context.

## Step 1 — explain pass (one question, black box)

Compose exactly ONE question from the context:

> Which parts of this codebase are relevant to: <goal>? Cover the
> components involved, where the relevant docs/design records live, and
> conventions that constrain changes there.

Invoke `/omc:explain` with that question and collect its synthesized
answer. Call it as a command — never reach into its internals
(`gitnexus-*`) or its project hooks (`.omc/skills/explain-context`).

Every outcome is non-fatal:

- Full answer → goes into the primer verbatim.
- explain relays "no index — run `/omc:index` first" → the primer records
  that exact line, so the brainstorm knows graph grounding is absent.
- Any other failure → the primer records "explain unavailable — <reason>".

## Step 2 — assemble the primer

A short structured block containing, in order:

1. The work context (from `$ARGUMENTS`).
2. explain's answer (or its absence note).
3. Standing pointers: `docs/superpowers/specs/` (prior design records),
   `docs/superpowers/plans/` (implementation plans), and
   `.omc/docs/gitnexus/docs/` (generated LLM docs) when present.

## Step 3 — seed

Ask the user for their initial thinking / seed for this work — AFTER the
primer exists, so they can react to what the codebase already says. Actually
wait for their answer. A pending question or timeout is not a seed. Ask and
resolve material initial scope questions, retaining the answers with the
primer and seed before brainstorming.

## Step 4 — hand off to brainstorming

Invoke `superpowers:brainstorming` with: the user's actual seed, all material
scope answers, the complete input context, the primer, the
presentation rule below, this model-tier pointer: "Any implementation plan
born from this brainstorm follows the behavior layer's model-tier policy
(AGENTS.md, Model selection): every task carries a `Model:` line naming
its tier; the cheap/fast tier is never used." — and, only when `OMC_SLUG`
is set
(`echo "$OMC_SLUG"`) — this doc-naming directive: "Use the topic slug
`$OMC_SLUG` so the design doc lands at
`docs/superpowers/specs/YYYY-MM-DD-$OMC_SLUG-design.md` and the plan at
`docs/superpowers/plans/YYYY-MM-DD-$OMC_SLUG-plan.md`."

**Presentation rule (pass it to the brainstorm verbatim)**: present the
design as ONE well-formatted text document — every section printed in full,
open questions numbered and inlined where they arise — then ask for
targeted clarifications or an overall go-ahead in plain text. Never
drip-feed sections through question dialogs: dialog prompts hide the
surrounding prose, so a "does this section look right?" chain shows the
user questions about text they never saw. Question dialogs are for genuine
standalone forks (pick A/B/C), not for section sign-off.

**OMC caller contract (pass it to the brainstorm verbatim)**: explore the
user's questions and present the complete solution for discussion. Once the
user agrees, stop at the implementation handoff and wait for their later
direct implementation skill invocation (`$omc:implement` in Codex,
`/omc:implement` in Claude). Generic brainstorming instructions to proceed into
specification, planning, or coding after approval are superseded here.
Replies such as `ok` continue discussion or acknowledge the design; they do
not count as the direct command. Keep the full context, seed, and answers
available for the later handoff.

This skill prepares and hands off — it never designs, never writes code,
and never writes to the tracker.
