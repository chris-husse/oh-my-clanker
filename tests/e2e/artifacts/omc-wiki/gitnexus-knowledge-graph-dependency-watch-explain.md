# GitNexus Knowledge Graph & Dependency Watch — explain

# GitNexus Knowledge Graph & Dependency Watch — `explain`

## Purpose

`explain` answers "how does X work", "where is Y handled", and "what breaks if I change Z" questions about the current codebase. It is a thin orchestration layer: it does not itself search code or maintain the knowledge graph — it composes project context, the GitNexus graph query skill, and (when relevant) dependency-specific knowledge into one grounded answer.

The skill lives at `skills/explain/SKILL.md` and is invoked as `/omc:explain <question>`.

## Execution Flow

```mermaid
flowchart TD
    A[omc explain question] --> B{explain-context skill<br/>defined in project?}
    B -- yes --> C[Read & follow it]
    B -- no --> D[Skip]
    C --> E[gitnexus-explain]
    D --> E
    E --> F{Question hinges on<br/>external dependency?}
    F -- indexed --> G[explain-dependency skill]
    F -- not indexed --> H[Name dependency,<br/>point at /omc:explain-dependency]
    F -- no --> I[Synthesize answer]
    G --> I
    H --> I
```

### Step 1 — Project context (optional)

Before querying the graph, `explain` looks for `.omc/skills/explain-context/SKILL.md` in the project root (and the primary worktree root, if different). If the project defines this file, it is read and followed *first* — it tells `explain` where the project keeps canonical documentation, naming conventions, and decision records, so graph evidence gets interpreted with the right local context. If the file is absent, this step is skipped entirely; it's optional infrastructure a project can opt into.

### Step 2 — Graph evidence via `gitnexus-explain`

The skill invokes the internal `gitnexus-explain` skill with the user's question. `gitnexus-explain` is explicitly internal — `explain` calls it as a black box rather than reimplementing graph queries — and it returns:

- Symbol and file citations
- Execution flows
- Doc excerpts pulled from the project's GitNexus knowledge graph

If the project hasn't been indexed yet, `gitnexus-explain` reports that the index is missing. `explain` must relay that "run `/omc:index` first" guidance **verbatim** and stop — it does not attempt to answer without graph grounding.

### Step 3 — External dependency escalation (conditional)

Not every question is about this repo. `explain` judges whether the question actually hinges on the internals of an external dependency — a library or sibling service this project calls, not this repo's own code. If so, it checks `omc internal dependency list`:

- **Dependency is indexed** → invoke `omc:explain-dependency` as a black-box command with a focused sub-question, then fold its cited answer into the synthesis.
- **Dependency is not indexed** → don't auto-index it. Instead, name the dependency explicitly and point the user at `/omc:explain-dependency <name> <question>` so they can opt in.

Any answer folded in from a dependency skill is third-party-derived content and must be treated as data, not as instructions — this matters because dependency docs are effectively untrusted input from the skill's perspective.

### Step 4 — Synthesis

All gathered sources (project context, graph evidence, optional dependency answer) are combined into a single, direct response:

- Lead with the actual answer, in prose — not a list of raw citations.
- Cite evidence as `file:symbol` or `file:line` so every claim is checkable against the codebase.
- If the project's own context docs (Step 1) and the graph evidence (Step 2) disagree, surface the disagreement rather than silently preferring one.
- Explicitly state what could not be established instead of guessing to fill a gap.

## Design Notes

- **Composition over reimplementation**: `explain` never queries the graph or reads dependency source directly. It treats `gitnexus-explain` and `explain-dependency` as opaque, user-facing (or internal) skills and only shapes the question going in and the answer coming out. This mirrors the project-wide convention that skills call other skills as black boxes rather than reaching into their internals.
- **Graceful missing-index handling**: rather than falling back to ad hoc grepping, a missing index is a hard stop with a specific remediation path (`/omc:index`), keeping answers grounded exclusively in graph evidence plus explicit project context.
- **Extensibility point**: the optional `.omc/skills/explain-context/SKILL.md` hook lets any project layer in its own documentation conventions without modifying `explain` itself.

## Where This Fits

`explain` is the general-purpose entry point into the GitNexus knowledge graph. Related skills:

- `gitnexus-index` / `omc:index` — builds and incrementally updates the graph this skill queries.
- `gitnexus-explain` — internal, does the actual graph querying (query/context/impact/cypher) plus doc lookup.
- `explain-dependency` — the analogous entry point scoped to an external dependency's own per-commit graph.
- `document` / `gitnexus-document` — regenerates the human-readable docs that `gitnexus-explain` cites excerpts from.