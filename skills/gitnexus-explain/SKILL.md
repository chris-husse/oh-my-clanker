---
name: gitnexus-explain
description: Internal — used by /omc:explain; not meant for direct invocation. Answer a code question by composing GitNexus graph queries (query/context/impact/cypher) plus the generated docs.
---

# omc gitnexus-explain (internal)

## Input

A question about the codebase (from `/omc:explain`).

## Step 1 — ensure the CLI

Run the `gitnexus-ensure` skill (installs/heals GitNexus under
`~/.omc/dependencies/gitnexus`).

## Step 2 — compose graph queries

There is no single `explain` CLI command — composing the query tools IS this
skill. Iterate until the question is answerable (prefer the graph over grep).
All queries go through omc's proxy, which resolves the graph location and
scoping deterministically (primary root, configured base branch) — pass ONLY
the verb and its arguments:

- `omc internal gitnexus query "<concept>"` — find the execution flows and
  symbols related to the question's concepts.
- `omc internal gitnexus context <symbol>` — 360° view of each load-bearing
  symbol (callers, callees, processes). Disambiguate with `--file <path>` if
  the name is shared.
- `omc internal gitnexus impact <symbol>` — blast radius, when the question
  is about change consequences ("what breaks if…").
- `omc internal gitnexus cypher "<stmt>"` — raw graph query for anything
  structural the higher-level commands can't express.

The proxy exits 1 with an install hint when GitNexus is missing — relay that
hint ("run `/omc:index` first") and stop; never index implicitly.

Also read the generated docs at `.omc/docs/gitnexus/docs/` (primary root)
when present — module pages often carry the architectural "why" the graph
alone can't.

## Step 3 — return findings

Return the evidence, organized for the caller to synthesize: the symbols and
files involved (cite `path:symbol`), how they connect (flows), and any
relevant doc-page excerpts. State what the graph could NOT answer rather than
guessing — absence of a finding is not proof of absence.
