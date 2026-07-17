---
name: gitnexus-explain
description: Internal — used by /omc:explain; not meant for direct invocation. Answer a code question by composing GitNexus graph queries (query/context/impact/cypher) plus the generated docs.
---

# omc gitnexus-explain (internal)

## Input

A question about the codebase (from `/omc:explain`).

## Step 1 — ensure CLI + locate the graph (local snapshot first)

Run the `gitnexus-ensure` skill. Then pick the graph to query:

- The CURRENT worktree has a local `.gitnexus/` → use it (worktrees carry a
  snapshot of main's graph from creation; `/omc:rebase-main` refreshes it —
  mention that if the snapshot looks stale relative to the question).
- Otherwise fall back to the primary worktree root (`git worktree list`,
  first entry) and run the queries from there.
- Neither has a `.gitnexus/` index → tell the user to run `/omc:index` first
  and stop (do not index implicitly; indexing a large repo is not something
  to trigger as a side effect of a question).

## Step 2 — compose graph queries

There is no single `explain` CLI command — composing the query tools IS this
skill. From the chosen root, iterate until the question is answerable
(prefer the graph over grep):

- `node <CLI> query "<concept>"` — find the execution flows and symbols
  related to the question's concepts.
- `node <CLI> context <symbol>` — 360° view of each load-bearing symbol
  (callers, callees, processes). Disambiguate with `--file <path>` if the
  name is shared.
- `node <CLI> impact <symbol>` — blast radius, when the question is about
  change consequences ("what breaks if…").
- `node <CLI> cypher "<stmt>"` — raw graph query for anything structural the
  higher-level commands can't express.

Also read the generated docs at `.omc/docs/gitnexus/docs/` (primary root)
when present — module pages often carry the architectural "why" the graph
alone can't.

## Step 3 — return findings

Return the evidence, organized for the caller to synthesize: the symbols and
files involved (cite `path:symbol`), how they connect (flows), and any
relevant doc-page excerpts. State what the graph could NOT answer rather than
guessing — absence of a finding is not proof of absence.
