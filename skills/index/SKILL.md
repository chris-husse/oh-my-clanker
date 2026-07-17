---
name: index
description: (Re)index this project's code knowledge graph (GitNexus) so /omc:explain has current answers. Run it in the main checkout as the base branch moves; it is incremental.
---

# omc index

Delegate to the internal **`gitnexus-index`** skill and relay its report
(what got indexed, where, freshness).

The intended cadence: run `/omc:index` (and `/omc:document`) in the MAIN
checkout whenever the base branch has moved — worktrees all read the primary
root's graph, so `/omc:explain` inside any worktree stays current.
