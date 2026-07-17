---
name: gitnexus-index
description: Internal — used by /omc:index; not meant for direct invocation. Incrementally (re)index the current project into its GitNexus knowledge graph, always operating on the primary worktree root.
---

# omc gitnexus-index (internal)

## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill; use the `<CLI>` path it establishes.

## Step 2 — resolve the primary worktree root

`git worktree list` — the FIRST entry is the primary checkout. If the current
directory is a linked worktree, say so ("indexing the primary checkout at
<path>, not this worktree") and operate on the primary root anyway: the index
lives there so every worktree's `/omc:explain` reads one shared, current graph.

## Step 3 — index (incremental)

From the primary root:

```sh
node <CLI> analyze --skip-agents-md --skip-skills
```

`analyze` is incremental — it updates a stale index rather than rebuilding.
The two skip flags keep it index-only: no AGENTS.md/CLAUDE.md blocks, no agent
skill installs (omc owns those surfaces). The index lands at `.gitnexus/` in
the primary root (GitNexus-native; keep it gitignored).

## Step 4 — report

`node <CLI> status` from the primary root; report indexed state (repo name,
freshness). A failed analyze → surface its output and stop; never report a
stale index as fresh.
