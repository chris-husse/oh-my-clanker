# Fix wiki "LadybugDB not initialized for repo \_\_wiki\_\_" — design

Date: 2026-07-17
Slug: `fix-wiki-ladybugdb-not-initialized`
Repos: GitNexus (`github.com/chris-husse/GitNexus`, local clone
`/Users/chriphus/Projects/GitNexus`) + oh-my-clanker (this branch).

## Problem

`omc watch --once --enable-documentation` fails at the documentation step:

```
✗ wiki failed: GitNexus Wiki Generator
  Config saved to ~/.gitnexus/config.json
  Error: LadybugDB not initialized for repo __wiki__. Call initLbug first.
```

Root cause (GitNexus v1.6.7). The wiki generator opens the knowledge graph as
pool repo `__wiki__` (`gitnexus/src/core/wiki/graph-queries.ts`, `initWikiDb`
called once in `generator.ts` `run()`). The lbug connection pool idle-evicts
entries after 5 minutes, swept every 60s
(`gitnexus/src/core/lbug/pool-adapter.ts` — `IDLE_TIMEOUT_MS`,
`ensureIdleTimer`). The existing guard, `touchWikiDb()`, is only invoked from
the `onChunk` streaming callback (`generator.ts`, `streamOpts`). The `claude`
provider spawns `claude -p --output-format text --no-session-persistence`
(`local-cli-client.ts`), which emits stdout only at process exit — `onChunk`
never fires mid-call. Any single LLM call longer than 5 minutes (routine for
wiki generation on a real repo) lets the sweeper close `__wiki__`; the next
graph query throws the reported error. The codex/opencode/cursor providers are
buffered the same way.

Secondary quirk: omc passes `--provider`/`--model` on every watch tick, and the
wiki command persists CLI flags unconditionally (`cli/wiki.ts`), so a
background loop rewrites the user-global `~/.gitnexus/config.json` every 30s.

Deployment gap: omc's `gitnexus-ensure` skill only clones/builds when the CLI
is missing or broken — a healthy install never updates, so a GitNexus-side fix
would not reach `~/.omc/dependencies/gitnexus` on its own.

## Decisions (brainstorm outcomes)

- Fix the root cause in GitNexus, not around it in omc.
- GitNexus work branches off `origin/main` (which already contains the
  security round-3 merge).
- Keepalive approach: **run-scoped timer** (option A) — chosen over lazy
  re-init in `executeQuery` (weakens the pool's deliberate fail-fast
  semantics shared with the MCP server) and pinned pool entries (new
  pool-full-of-pinned edge case for no added benefit).
- Also fix the config rewrite, in GitNexus.
- Close the deployment gap by extending the existing `omc update` command;
  the managed clone is not a dev workspace, so the refresh always forces
  `main`.
- One spec (this file) in the omc repo; the GitNexus PR description
  summarizes its two workstreams and links back.

## Workstream 1 — GitNexus: run-scoped keepalive

In `WikiGenerator.run()` (`gitnexus/src/core/wiki/generator.ts`), immediately
after `await initWikiDb(this.lbugPath)`:

- Start `setInterval(() => touchWikiDb(), 60_000)`, unref'd so it can never
  hold the process open.
- Clear the interval in the existing `finally` block, next to
  `closeWikiDb()` — covering success, throw, and both generation modes
  (full and incremental), since the timer spans the whole `run()` body.

60s touch vs 5-minute idle timeout gives a 5× safety margin. The
now-redundant `onChunk` touch plumbing in `streamOpts` (`lastTouch` and the
`touchWikiDb()` call) is removed. A comment at the timer documents the
provider quirk per repo convention (comments at the dependent site): local
agent CLIs (claude/codex/opencode) buffer stdout until exit, so no streaming
callback can be relied on for liveness.

No changes to `pool-adapter.ts` — its eviction semantics are shared with the
MCP server and stay untouched.

## Workstream 2 — GitNexus: save CLI config only on effective change

In the flag-persistence block of `gitnexus/src/cli/wiki.ts`: after assembling
`updates` from CLI flags, compare each key against the loaded existing config
(including the provider-specific local-model key routing). If nothing
effectively changes, skip both `saveCLIConfig` and the
`Config saved to ~/.gitnexus/config.json` message. Genuinely new or changed
flags persist exactly as today.

## Workstream 3 — omc: `omc update` refreshes the GitNexus dependency

`run_update` (`src/omc/installer.py`) currently only runs
`uv tool upgrade omc`. It gains a dependency-refresh step implemented as a
deterministic function in `src/omc/gitnexus.py` (e.g.
`update_gitnexus(ctx) -> int`). Module charter extends from "locate only" to
"locate + update an existing clone"; **first install stays in the
`gitnexus-ensure` skill prose**.

Behavior:

1. `~/.omc/dependencies/gitnexus` absent → print
   "GitNexus not installed — /omc:index installs it on first use"; skip;
   success.
2. `git remote get-url origin` ≠
   `https://github.com/chris-husse/GitNexus.git` → refuse with an error,
   non-zero exit (same approved-source rule as the ensure skill; never
   re-point, never build an unapproved tree).
3. `git fetch origin --prune`; if HEAD already equals `origin/main` →
   "GitNexus up to date (vX.Y.Z)"; done.
4. Otherwise force `main`: `git checkout main` +
   `git merge --ff-only origin/main`, then the documented two-step build —
   `npm install --no-audit --no-fund` in `gitnexus-shared/`, then
   `npm ci && npm run build` in `gitnexus/` — then verify
   `node <CLI> --version` and report old → new version. Any failure →
   surface the output, non-zero exit; never claim success on a broken build.

`omc update` exits non-zero if either the uv upgrade or the dependency
refresh fails. The `gitnexus-ensure` skill gets one added line pointing
healthy-install updates at `omc update` so skill and CLI don't drift.

Also on this branch (already applied during brainstorming, at Chris's
request): `skills/plan/SKILL.md` gains a presentation rule for the primed
brainstorm — present designs as one formatted document with questions
inlined; never drip-feed sections through question dialogs.

## Testing

GitNexus (`gitnexus/`, vitest; gates per `TESTING.md`:
`npx tsc --noEmit && npm test`):

- New `test/unit/wiki-keepalive.test.ts` with fake timers and the existing
  mocked pool-adapter seam: an LLM call stubbed to outlive the idle timeout
  must see periodic `touchRepo`/`touchWikiDb` activity; after `run()` settles
  (success and throw), no further touches occur (interval cleared).
- Extend `test/unit/wiki-flags.test.ts`: a second invocation with identical
  flags must not call `saveCLIConfig` and must not print "Config saved".

omc (`tests/unit/`, following `_stubs.py` ToolContext patterns):

- `update_gitnexus`: skip-when-missing (success), refuse-on-wrong-origin
  (non-zero), up-to-date short-circuit (no build), moved → exact argv
  sequence (fetch, checkout main, ff-merge, both builds in order, version
  verify), build failure → non-zero with surfaced output.
- Branch finishes through the standard `/omc:finish` gates
  (build → verify → review).

Live proof (the only test exercising a genuinely >5-minute Claude call):
after the GitNexus PR merges — run `omc update` (pulls + rebuilds the managed
dependency), then `omc watch --once --enable-documentation` in
`/Users/chriphus/Projects/hummingbird-wt`; expect the wiki step to complete
and docs to land in `.omc/docs/gitnexus/docs`. This is LLM-heavy (full wiki
generation over hummingbird-bridge) and accepted as part of the plan.

## Out of scope

- Streaming output for local CLI providers in GitNexus (would also fix the
  symptom but is a larger provider-layer change; the keepalive is correct
  regardless).
- Any change to pool eviction policy or MCP-server behavior.
- omc-side retry/workaround logic in `watch.py` — root cause is fixed
  upstream; watch's existing error surfacing stays as is.
- Automatic/background dependency updates (the update is explicit via
  `omc update` by design — omc never creates daemons or persistence).

## Delivery

- GitNexus: branch `fix/wiki-lbug-keepalive` off `origin/main`; workstreams
  1+2 as one PR to `main` (both are wiki-command hygiene). PR description
  links back to this spec.
- omc: workstream 3 + the plan-skill presentation rule ride
  `feature/fix-wiki-ladybugdb-not-initialized`, finished via `/omc:finish`.
- Sequencing: GitNexus PR first (it is the actual fix), then the live proof
  via the new `omc update`, then finish the omc branch.
