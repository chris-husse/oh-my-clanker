---
name: document
description: Regenerate this project's LLM documentation (GitNexus wiki) into .omc/docs/gitnexus/docs. Run it in the main checkout after /omc:index when the base branch has moved meaningfully.
---

# omc document

Delegate to the internal **`gitnexus-document`** skill and relay its report
(pages generated, where they landed).

For now this is exactly the GitNexus wiki refresh — future documentation
sources can join under this same command. Run it in the MAIN checkout (it
operates on the primary worktree root regardless); pair it with `/omc:index`
so the docs are generated from a current graph.
