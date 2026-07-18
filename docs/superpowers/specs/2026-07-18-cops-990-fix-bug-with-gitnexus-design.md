# COPS-990 — fix gitnexus repo lookup + worktree-install guardrail

Ticket: https://seabound.atlassian.net/browse/COPS-990 (sub-task of COPS-855).

## Problem

1. `omc internal gitnexus <verb>` crashes with GitNexus's raw
   `Repository "<name>" not found. Available: <labels>` (a Node stack trace,
   exit 1) whenever the primary checkout's **directory basename** differs from
   the name GitNexus registered the repo under. GitNexus derives registry
   names from the remote URL — live example: the checkout at
   `~/Projects/hummingbird-chicken` is registered as
   `gcx-backend-hummingbird-bridge` — while omc's proxy
   (`src/omc/internal.py:_gitnexus`) passes `--repo Path(primary).name`.
   Every graph-backed skill (`/omc:explain`, plan priming) breaks in such
   repos.
2. Agents have repeatedly run `omc install <feature-worktree>` on their own
   initiative. `omc install <path>` re-roots every future `omc update` at
   that path (uv receipt), which is how the host install ended up pinned to
   `feature/version-provenance@a211cc7` in a worktree. Decision: **no
   installer code change** — this is a behavioral rule for agents.

## Approaches considered (problem 1)

- **A (chosen): pass the primary root's path as `--repo`.** GitNexus's
  `resolveRepo` (`dist/mcp/local/local-backend.js`, via
  `resolveRepoFromCache`) matches by name **or path** and canonicalizes both
  sides for path matches. One-token change, robust to any name-derivation
  scheme GitNexus uses now or later, consistent with the proxy's existing
  "resolve deterministically from the primary root" philosophy.
- B: read `~/.gitnexus/registry.json`, match the entry by `path`, pass its
  `name`. Re-implements GitNexus's resolution in omc, couples to the registry
  file format, still needs a missing-entry fallback. Rejected.
- C: change GitNexus to register under the directory basename. Wrong layer —
  registry naming is GitNexus's semantic (it disambiguates clones of the same
  repo), and omc must work with released GitNexus versions. Rejected.

## Design

### Change 1 — `_gitnexus` scopes by path, not basename

`src/omc/internal.py:_gitnexus` builds its argv as:

```python
argv = gitnexus_argv(ctx, *rest, "--repo", primary, "--branch", base)
```

(previously `Path(primary).name`). The docstring's scoping rationale gains
one sentence: `--repo` is pinned to the primary root's *path* because
GitNexus registers repos under remote-URL-derived names that need not match
the directory basename, while its resolver also matches canonicalized paths.

No other call site is affected: the only other proxy invocation,
`_rebase_main`'s best-effort `gitnexus index` after a mirror sync, passes no
`--repo` and stays as is.

**Testing (red → green):** update
`tests/unit/test_internal.py::test_gitnexus_proxy_injects_scoping_and_runs_from_primary`
— the assertion `"--repo primary" in logged` (basename) becomes an exact
check that `--repo <absolute primary path>` was passed. Run it against the
current code and watch it fail, then apply the fix and watch it go green.
The basename assertion *was* the bug, encoded in a test.

### Change 2 — worktree-install guardrail in `.omc/config/AGENTS.md`

`.omc/config/AGENTS.md` is the project's own guidance file in omc's
AGENTS.md control chain (root `AGENTS.md`/`CLAUDE.md` route agents there;
git-tracked; omc never edits it). Add one bullet under **Architectural
invariants** (a standing behavioral rule, same family as "never create
daemons"):

```markdown
- **Never run `omc install` / `uv tool install` on your own initiative.**
  `omc install <path>` re-roots every future `omc update` at that path — an
  install pointed at a feature worktree has silently pinned the host omc to
  a stale branch multiple times. Installing is a USER decision, made from
  the primary checkout on `main`; if a task seems to require reinstalling
  omc, stop and tell the user the exact command instead of running it.
```

Only this worktree's tracked file is edited; merge + `/omc:rebase-main`
mirroring propagate it.

**Testing:** none — prose guidance has no runtime surface; the red→green
policy applies to behavior changes, and the enforcement point is the agent
reading the file. An LLM-judge E2E for one bullet would be disproportionate.

## Error handling / edge cases

- Repo indexed under an old path (moved checkout): path match fails,
  GitNexus retries once after a registry refresh, then throws its "not
  found" error — same behavior as today, but now only in genuinely broken
  states.
- Multiple registered repos: `--repo <path>` disambiguates exactly (that is
  resolveRepo's documented purpose); basenames can collide, paths cannot.
- Worktree sessions: unchanged — the proxy already resolves and runs from
  the primary root; only the identifier it passes changes.

## Deliberately out of scope

- Fail-soft proxy behavior for a genuinely unresolvable `--repo` (bail
  exit 3 with a clean one-liner instead of GitNexus's Node stack trace) —
  agreed to spin off as a separate ticket.
- Any `omc update` / installer change (warn/re-root/force-main) — explicitly
  declined; the AGENTS.md guardrail is the whole remedy for problem 2.
- GitNexus-side changes.

## Scope

Two files (`src/omc/internal.py`, `.omc/config/AGENTS.md`) plus one test
edit. Verify with `just build`.
