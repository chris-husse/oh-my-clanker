---
name: gitnexus-document
description: Internal — used by /omc:document; not meant for direct invocation. Regenerate the project's LLM documentation from its GitNexus graph and sync it to .omc/docs/gitnexus/docs in the primary worktree root.
---

# omc gitnexus-document (internal)

## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill.

## Step 2 — refresh index + documentation

```sh
omc internal gitnexus refresh --enable-documentation
```

Python owns the LLM choice (omc's configured default and that provider's docs
model, never the session model); the wiki runs supervised (no deadline, killed
only on a stall) and, when the verdict is fresh afterwards, is mirrored into
`.omc/docs/gitnexus/docs/` in the primary root (`.omc/docs/` is generated
output — keep it gitignored). This is LLM-driven and can take a while on a
large repo. It waits for a running `omc watch` tick to finish before starting
(busy lock) and never runs two regenerations at once.

## Step 3 — report

The last stdout line is `OMC_KNOWLEDGE {…}`. rc 0 → list what landed in
`.omc/docs/gitnexus/docs/` (page count, top-level titles). rc 3 → the docs are
still behind: relay the `reasons` and stop; never sync or report a partial wiki
as current.
