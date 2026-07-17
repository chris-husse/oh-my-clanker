# omc plan + implement — design

Date: 2026-07-17
Ticket: COPS-855 (umbrella: migrate the chicken to a repository and better design)
Status: approved design, pre-implementation

## Problem

`/omc:explain` is heavy — GitNexus CLI health check, index resolution, several
graph queries per question. That cost is wrong for rapid-fire Q&A but exactly
right when amortized into a one-time planning setup. Today `/omc:start` hands
off to `superpowers:brainstorming` directly, so the brainstorm starts cold:
it knows the ticket but not the codebase, where the docs live, or what prior
design records say. And after the brainstorm converges there is no rail — the
user must remember to drive spec → plan → execution → finish by hand.

Two new user-facing skills fix both ends:

- **`/omc:plan`** — the front half: a setup stage around brainstorming that
  primes it with project knowledge.
- **`/omc:implement`** — the back half: the lifecycle conductor from
  "design has converged" to "branch pushed", typed by the user when the
  brainstorm is ready to become a spec.

## Composition rule (load-bearing)

Skills compose by CALLING other user-facing commands as black boxes — input
in, answer out, move on. A caller never reaches into another skill's
internals (`gitnexus-explain`, `gitnexus-ensure`) or its project hooks
(`.omc/skills/explain-context`). Internal skills are invoked only by their
designated owner: `finish` owns `squash`/`create-mr`; `implement` owns the
new `spec`; `plan` owns nothing.

## `/omc:plan` (user-facing)

Input: the work context — the ticket recap passed by `/omc:start`, or free
text when invoked standalone. Empty input → ask what to plan.

1. **Explain pass** — compose exactly ONE question from the input:
   "Which parts of this codebase are relevant to: <goal>? Cover the
   components involved, where the relevant docs/design records live, and
   conventions that constrain changes there." Invoke `/omc:explain` with it.
   All outcomes are non-fatal:
   - full answer → primer, verbatim;
   - explain relays "no index — run `/omc:index` first" → primer records
     that line (brainstorm knows graph grounding is absent);
   - any other failure → primer records "explain unavailable — <reason>".
2. **Primer assembly** — a short structured block: the work context,
   explain's answer (or absence note), and standing pointers:
   `docs/superpowers/specs/` (prior design records),
   `docs/superpowers/plans/` (implementation plans), and
   `.omc/docs/gitnexus/docs/` (generated LLM docs) when present.
3. **Seed** — ask the user for their initial thinking AFTER the primer
   exists, so they react to what the codebase already says.
4. **Handoff** — invoke `superpowers:brainstorming` with seed + primer +
   the `$OMC_SLUG` doc-naming directive (directive included only when
   `OMC_SLUG` is set; standalone invocations get default naming).

`/omc:plan` never designs, never writes code, never writes to the tracker.

## `/omc:start` rewire

Step 4 shrinks to: print the compact ticket summary, then invoke `omc:plan`
with the gathered context recap. The seed question and the brainstorming
handoff move out of `start` into `plan`. Steps 0–3 (cold-path gate,
superpowers check, context gate, base-freshness gate) are unchanged.

## `/omc:implement` (user-facing)

Typed by the user during/after brainstorming, when the design has converged
and is ready to become a spec. Four phases, strictly in order; each phase is
a black-box command call:

1. **Spec** — invoke internal `omc:spec` (below). Ends at the user
   spec-review gate.
2. **Plan** — invoke `superpowers:writing-plans`. Then for each MAJOR plan
   section, call `/omc:explain` once to pressure-test the implementation
   choices — emphasis here is implementation-level design, not architecture:
   "should this be an enum? should we add a parameter here, or reuse an
   existing mechanism? does this fit what the codebase already has?" Refine
   the section with the answer; surface real alternatives to the user.
3. **Build** — execute the plan via
   `superpowers:subagent-driven-development` (subagent per task, its own
   checkpoints apply).
4. **Ship** — invoke `/omc:finish` (rebase, squash-with-MR-description,
   build/verify/review stages, push with `--force-with-lease`).

**Resume semantics**: if a spec for the current slug already exists when
`/omc:implement` is typed, do NOT silently resume — the spec may be
incomplete. Tell the user what was found and ask for guidance: resume at the
plan phase, re-run spec hardening on the existing doc, or start the spec
over.

## `omc:spec` (internal — owned by `/omc:implement`)

Marked "not meant for direct invocation". Owns spec writing + hardening:

1. **Write** the design doc from the converged brainstorm, per repo
   conventions (`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`).
2. **Per-section hardening** — for EACH section, call `/omc:explain` with:
   "Does this section's change make architectural sense in this codebase?
   What existing components does it touch, and what problems might occur?"
   Refine the section with the answer. Emphasis here is architecture,
   purpose, and general function (the plan phase covers implementation-level
   choices).
3. **Whole-spec pass** — one more `/omc:explain` over the complete spec for
   high-level coherence.
4. **Iterate** — repeat 2–3 until explain stops surfacing real issues.
   Genuine architectural decisions are surfaced to the user as they arise
   (explicit follow-up questions, never silent choices).
5. **Gate** — commit the spec and ask the user to review it before the plan
   phase begins.

Heavy `/omc:explain` use is deliberate here: spec hardening is where that
cost pays off. Rapid-fire Q&A remains `/omc:explain`'s anti-use-case.

## Division of labor after this change

- `/omc:start` → gates + ticket context → `plan`
- `/omc:plan` → primer → `superpowers:brainstorming` (front half)
- `/omc:implement` → `spec` → writing-plans → subagents → `finish`
  (back half)

## Model-selection doctrine (behavior layer)

Added mid-execution at Chris's direction (2026-07-17): model choice is
omc's concern, not assistant memory. The generated behavior layer
(`INTERNAL_AGENTS_MD` in `src/omc/agentsmd.py`) gains a ground rule:

- The MAIN session runs the model chosen in `omc configure` — it never
  second-guesses that choice.
- When dispatching subagents, the session ASSESSES each task and picks the
  right model for the job: the heavyweight model for planning/design,
  reviews, and judging subagent output; efficient models for well-specified
  execution work.

This is the one deliberate exception to "no changes under `src/omc/`" —
it edits only the behavior-layer template string and its test.

## Testing

- **Smoke tier**: plugin exposes `plan`, `implement`, and `spec` skills;
  `spec` carries the internal marker; `start` no longer names
  `superpowers:brainstorming` directly (it names `omc:plan`).
- **Contract checks**: skills keep machine contracts intact — no changes to
  OMC_SLUG/OMC_STAGE/OMC_SQUASH producers.
- **Live E2E tier** (token-funded, expensive): a plan→brainstorm handoff
  scenario and a spec-hardening scenario, alongside the existing
  watch/explain live tests.

## Deliberately dropped

- A shared "context-primer" internal skill (`gitnexus-explain` already is
  the shared internal; plan calls `/omc:explain` as a command instead).
- A new `.omc/skills/plan-context` project hook (explain owns project
  context discovery; plan consumes it through explain's answers).
- Autonomy modes for `implement` (all existing human gates stay: design
  approval, spec review, plan review, finish's integration choice).
- Python/CLI changes — this feature is entirely in the skills half.
