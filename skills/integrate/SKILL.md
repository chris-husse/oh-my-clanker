---
name: integrate
description: Guide the user step by step through integrating their project with omc, or reviewing/adjusting an existing integration (after an omc update, or when something seems fishy). Inventories every omc surface, then brainstorms each project skill grounded in the actual codebase.
---

# omc integrate

## User Input

```text
$ARGUMENTS
```

Optional intent ("fresh setup", "review after update", "the build stage feels
wrong"). Empty → the inventory decides the mode.

## Mode

- **Fresh**: no `.omc/` surfaces exist → guided first-time setup.
- **Review**: surfaces exist → evaluate each against BOTH the project's
  current reality (things drift) and omc's current expectations (omc updates
  add conventions). `$ARGUMENTS` naming a specific worry narrows the focus,
  but still run the full inventory — fishy feelings usually have neighbors.

**Non-interactive/headless runs: propose only — zero writes**, and skip
heavy steps (indexing) with a note. The interactive session is where files
get written.

## Phase 1 — foundation

1. **Inventory**, presented as a status table (present / missing /
   suspicious), one row each:
   - `AGENTS.md` + `CLAUDE.md` → do both root symlinks resolve into the omc
     install's `distribution/AGENTS.md` (machine-local, gitignored), with
     project guidance committed at `.omc/config/AGENTS.md`?
   - `.omc/config/AGENTS.md` — the project's own agent instructions
   - `.config/wt.toml` — does a copy-ignored step exist?
   - `.gitnexus/` index and `.omc/docs/` generated docs
   - `.omc/skills/build` · `.omc/skills/verify` · `.omc/skills/review` ·
     `.omc/skills/explain-context`
   - `.omc/hooks/post-watch.sh` — optional CLI-side hook `omc watch` runs
     after action ticks (sync / forced refresh)
2. **Mechanical fixes** via the existing machinery (with the user's go-ahead):
   - Chain missing/stale → re-run `omc configure`: read the CURRENT default
     from `~/.omc/config.json` and re-set it
     (`omc configure --set llm.default=<current>`) — **never `--defaults`,
     which would reset the user's config**. Blocked chain (regular files in
     the way) → walk the user through the migration steps configure printed.
   - wt config flagged → run the `check-wt-config` skill and present its
     findings.
   - No index → **offer** `/omc:index` now: the knowledge graph is what
     grounds Phase 2. On a huge repo the user may decline — fall back to
     reading the build/test artifacts directly.

## Phase 2 — design each project skill (the heart)

Go slot by slot, in this order, ONE at a time — investigate, propose,
iterate, and **write only on explicit approval** (these are the project's
files; in review mode show the existing file beside your proposal and flag
drift and gaps, never silently replace):

### `.omc/skills/build`
Investigate how this project actually builds and gates: justfile, Makefile,
package.json scripts, pyproject, CI workflows — and the graph
(`query "build"`). Propose a draft naming the REAL commands and what passing
means (exit codes, format/lint steps). Drift example worth flagging in
review mode: the skill says `make test` while CI runs `just build`.

### `.omc/skills/verify`
What's the heavier "does it still work" tier here — integration tests, e2e
suites, docker harnesses, smoke scripts? How long does it take, what does it
need (services, tokens)? Propose the stage with honest cost notes and hard
pass criteria.

### `.omc/skills/review`
What do this project's reviews actually check — CONTRIBUTING.md, CI lint
gates, codeowner norms, the repo's own invariants (find them via
`/omc:explain "what are this codebase's load-bearing conventions?"`).
Propose a review stage with project-specific findings categories and an
explicit pass rule (e.g. "no Critical/Important findings").

### `.omc/skills/explain-context`
Where does this project's truth live — READMEs, docs dirs, ADRs, wikis,
specs? In what order of authority? Propose the map so `/omc:explain` can
ground itself the way a senior teammate would.

### `.omc/hooks/post-watch.sh` (optional)
The CLI-side sibling of the session skills: `omc watch` runs it after every
cycle that did real work (env: `OMC_WATCH_OUTCOME`=`synced`|`refreshed`;
failures warn and link a log, never stop the loop). Investigate whether the
project has post-refresh work — regenerating downstream artifacts, cache
warming, notifying a dashboard. Propose it only when a real use exists;
absence is the correct default.

### `.omc/config/AGENTS.md`
The project's own agent instructions (the omc chain sends every agent here).
Gather what the user wants agents to always know — build/test commands,
architecture ground rules, review expectations, tribal knowledge — and
propose the content. omc never edits this file after seeding; this is the
user's voice.

## Phase 3 — wrap up

1. The after-table: created / updated / left alone, per surface.
2. What's now active: `/omc:finish` runs the stage gates you just designed;
   `/omc:explain` uses the context map; `omc watch` keeps the graph fresh
   (suggest the cadence); worktrees snapshot it all.
3. Suggest committing the new/changed files (they're all meant to be
   committed) — offer to run the project's own build stage first as a sanity
   check.
