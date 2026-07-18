# omc behavior layer (ships with the omc install — `omc update` updates it everywhere)

This repo is omc-managed. Root `AGENTS.md`/`CLAUDE.md` resolve here so every
harness (Claude Code, Codex, OpenCode) gets the same ground rules:

- **Worktrees are snapshots of main** — code AND knowledge (`.gitnexus/`,
  `.omc/docs/`). Refresh a worktree with `/omc:rebase-main` (it is also
  `/omc:finish`'s first step). Never hand-copy or hand-delete those dirs;
  the deterministic mirror lives in `omc internal rebase-main`.
- **Finish work through `/omc:finish`** — rebase, squash, project stage gates
  (`/omc:build` → `/omc:verify` → `/omc:review`), described push. Do not
  bypass a failing stage.
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
  `OMC_SQUASH` / `OMC_REBASE_MAIN` verdicts are parsed by tools — emit them
  exactly as their skills specify, never wrapped in markdown.
- Skills marked "not meant for direct invocation" are internal — compose
  them via their user-facing entry points.

## Project instructions

Read `.omc/config/AGENTS.md` next and follow it — that file is the
project's own guidance (omc never edits it) and takes precedence over this
layer wherever they overlap.
