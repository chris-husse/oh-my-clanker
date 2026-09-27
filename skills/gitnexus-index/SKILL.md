---
name: gitnexus-index
description: Internal — used by /omc:index; not meant for direct invocation. Incrementally (re)index the current project into its GitNexus knowledge graph, always operating on the primary worktree root.
---

# omc gitnexus-index (internal)

## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill.

## Step 2 — refresh the index

```sh
omc internal gitnexus refresh
```

The verb operates on the primary worktree root (`git worktree list`, first
entry) no matter where it is invoked; from a linked worktree, say so
("indexing the primary checkout at <path>, not this worktree"). It computes the
freshness verdict, runs whatever repair is needed (incremental analyze,
escalating to a full rebuild only when that leaves the index stale), and
prints a single machine-readable last line:

`OMC_KNOWLEDGE {"fresh": true|false, "basis": …, "reasons": [...], "fix": …, "run_in": …}`

## Step 3 — report

- rc 0 → report "index current at <basis>".
- rc 3 → the repair ran but the verdict is still stale: relay the remaining
  `reasons` verbatim and stop; never report a stale index as fresh.
- rc 1/2 → surface stderr and stop.
