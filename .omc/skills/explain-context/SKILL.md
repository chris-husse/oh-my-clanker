---
name: explain-context
description: How to find contextual information about oh-my-clanker - canonical docs, decision records, and their pecking order.
---

# explain-context (this repo)

Where this project keeps its truth, most-authoritative first:

1. **The code** (`src/omc/`, `skills/`, `tests/`) — always wins over any doc.
2. **`docs/superpowers/specs/`** — dated design records (one per feature:
   v1 core, finish, stages, gitnexus layer). Where a spec conflicts with the
   code, the code wins; the spec explains intent and what was deliberately
   dropped.
3. **`docs/superpowers/plans/`** — the v1 implementation plan (historical;
   task-by-task record of how the codebase was built).
4. **`README.md`** — user-facing install/usage truth, kept current.
5. **`docker/PLUGIN-NOTES.md`** — the empirical record of how plugin loading
   actually behaves per harness (cross-marketplace dependency pitfall, what
   was tested in containers).
6. **`.superpowers/sdd/progress.md`** — the build ledger: every task, review
   verdict, live-E2E result, and deferred minor, in order. Best place to
   answer "why is X this way" and "what is still open".

Conventions worth knowing: machine contracts are single-line JSON verdicts
prefixed `OMC_SLUG` / `OMC_STAGE` / `OMC_SQUASH`; "the chicken" in docs means
the internal predecessor tool this project descends from; provider quirks are
documented as comments at the exact code site that depends on them
(`src/omc/providers/*.py`).
