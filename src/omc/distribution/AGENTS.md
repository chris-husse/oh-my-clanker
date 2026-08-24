# omc behavior layer (ships with the omc install — `omc update` updates it everywhere)

This repo is omc-managed. Root `AGENTS.md`/`CLAUDE.md` resolve here so every
harness (Claude Code, Codex, OpenCode) gets the same ground rules:

- **Worktrees are snapshots of main** — code AND knowledge (`.gitnexus/`,
  `.omc/docs/`). Refresh a worktree with `/omc:rebase-main` (it is also
  `/omc:finish`'s first step). Never hand-copy or hand-delete those dirs;
  the deterministic mirror lives in `omc internal rebase-main`.
- **Finish work through `/omc:finish`** — rebase, squash, project stage gates
  (`/omc:check` → `/omc:build` → `/omc:verify` → `/omc:review`), described
  push. Do not bypass a failing stage.
- **Validation cadence**: `/omc:check` is the quick "am I on the right
  track" gate (build what the unit tests need, run them) — use it
  constantly while working. `/omc:build` builds the world, no tests.
  `/omc:verify` is full E2E — run it only after major milestones, never as
  a routine dev-loop gate.
- **Ask the graph, not grep**: `/omc:explain <question>` answers from the
  project's GitNexus knowledge graph and docs.
- **Model selection**: the main session runs the model chosen in
  `omc configure` — never second-guess it. When dispatching subagents or
  assigning models to plan tasks, apply the **model-tier policy**. Tiers
  are abstract, baselined on Claude's stack, and resolved at dispatch time
  against the provider's *current* lineup: pin the **top tier** to whatever
  is the latest & best model available (Fable-class today), then
  reinterpret the **heavy coding tier** (Opus-class) and the **standard
  coding tier** (Sonnet-class) down the current hierarchy. On other
  providers (OpenAI, …), map the tiers to that provider's current
  equivalents the same way.
  - Spec, review, and judging tasks → **top tier**.
  - Coding tasks → never below the **standard coding tier**; bigger coding
    tasks (multi-file, architecturally tricky, or ambiguous) → **heavy
    coding tier**.
  - The cheap/fast tier (Haiku-class or its equivalent on any provider) is
    **never used**, for anything.
- **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
  `OMC_SQUASH` / `OMC_REBASE_MAIN` / `OMC_TICKET` verdicts are parsed by tools
  — emit them exactly as their skills specify, never wrapped in markdown.
- **A verdict is an argument, not a destination.** Those verdict lines — and
  every other sub-skill artifact (an MR description, a plan, a spec) — end the
  SUB-SKILL, never the turn. "Nothing may follow it", "no commentary", "and
  end", "last line" are scoped to that skill's own output; none of them ever
  licenses stopping. Mechanically: **after emitting a verdict or artifact, your
  very next action in the same turn is a tool call** — the caller's next step.
  A turn that ends on a verdict line with no tool call after it is a failed
  run, not a completed step.
- **Externalize a composed flow before entering it.** On `/omc:start`,
  `/omc:finish`, `/omc:implement`: write every remaining step into the task
  list FIRST, then execute, marking each done as you pass it. These flows nest
  3–4 deep and each sub-skill arrives looking like a fresh user request, so
  completing one *feels* like completing the job. The task list is the only
  thing that survives that — "am I done?" is answered by reading it, not by
  feeling finished. A stale-task-list reminder mid-flow is the warning it
  appears to be.
- Skills marked "not meant for direct invocation" are internal — compose
  them via their user-facing entry points.

## Project instructions

Read `.omc/config/AGENTS.md` next and follow it — that file is the
project's own guidance (omc never edits it) and takes precedence over this
layer wherever they overlap.
