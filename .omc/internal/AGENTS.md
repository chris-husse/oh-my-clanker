# omc behavior layer (generated — do not edit; `omc configure` regenerates it)

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
- **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
  `OMC_SQUASH` / `OMC_REBASE_MAIN` verdicts are parsed by tools — emit them
  exactly as their skills specify, never wrapped in markdown.
- Skills marked "not meant for direct invocation" are internal — compose
  them via their user-facing entry points.

## Project instructions

Read `.omc/config/AGENTS.md` next and follow it — that file is the
project's own guidance (omc never edits it) and takes precedence over this
layer wherever they overlap.
