# Force the managed GitNexus clone onto origin/main in `omc update`

**Date:** 2026-10-06
**Slug:** `fix-gitnexus-ff-only-merge-diverged`
**Status:** design, hardened with `/omc:explain` and the grug lens

## 1. Problem

`omc update` aborts while refreshing omc's managed GitNexus dependency:

```
→ updating GitNexus…
error: GitNexus merge --ff-only origin/main failed: hint: Diverging branches cant be fast-forwarded …
fatal: Not possible to fast-forward, aborting.
```

`update_gitnexus` (`src/omc/gitnexus.py`) refreshes the clone under
`~/.omc/dependencies/gitnexus` with `git checkout main` followed by
`git merge --ff-only origin/main`. The fork that serves as the approved
origin (`GITNEXUS_ORIGIN`) rewrote its `main` by rebasing onto upstream
GitNexus. On the reporting machine the clone is `main...origin/main
[ahead 28, behind 943]` with a clean tree: the local commits are the
pre-rebase history, and no fast-forward exists. `installer.run_update` treats a
non-zero return from `update_gitnexus` as fatal, so the whole update
fails, and it fails again on every retry until someone repairs the clone
by hand.

The function's own docstring ("forces main and rebuilds"), the design
record `2026-07-26-install-gitnexus-in-omc-update-design.md` (data flow
`_clone_if_missing → force main → _build → verify`) and the generated wiki
all describe this step as forcing `main`. The code never forced anything.

## 2. Goals and non-goals

Goals:

- `omc update` converges the managed clone on `origin/main` whether the
  remote moved forward or was rewritten, with no manual repair.
- The step is idempotent: a second rewrite of the fork's `main` needs no
  intervention either.
- The approved-origin refusal still runs before any git write.

Non-goals:

- The `--ff-only` merge in `src/omc/watch.py` operates on the user's own
  project checkout, guards for divergence and dirty trees, and skips
  quietly. It is correct for a repository omc does not own and is not
  touched.
- No dirty-tree guard on the managed clone. omc owns it; nobody edits it.
- No change to `ensure_gitnexus`, which deliberately never fetches or moves
  `main`.
- No Docker E2E change: `docker/Dockerfile.e2e` prebakes the clone and
  build, so `ensure_gitnexus` finds it healthy there and the update path
  is exercised by the unit test only.

## 3. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Force the clone's `main` to `origin/main` rather than refusing with a repair hint or falling back to a reset only after `--ff-only` fails | User's seed. The clone is omc-owned build material; the docstring and the 2026-07-26 record already promise "force main". One code path, idempotent across future rewrites |
| 2 | Minimal fix plus one reproducing unit test; no dirty-tree guard, no spec amendment, no change to `watch.py` | User's seed. Nobody has hit a dirty managed clone; the 2026-07-26 record already states the intended behavior |
| 3 | Tracked local modifications in the managed clone are discarded by the force | Follows from Decision 1; the clone is not a place for hand edits |

## 4. Design

### 4.1 The branch update

In `update_gitnexus`, after `git fetch origin --prune` and the SHA
short-circuit, the two-command loop

```
git -C <root> checkout main
git -C <root> merge --ff-only origin/main
```

becomes one command:

```
git -C <root> checkout -f -B main origin/main
```

`-B` creates or resets `main` to `origin/main` and checks it out, so it
converges from `main`, from another branch, or from a detached `HEAD`. `-f`
discards tracked local modifications instead of refusing to switch, which
is the force semantics of Decision 1. Untracked build output (`node_modules`,
`dist`) is left alone; `npm ci` in `_build` resets `node_modules` and the
build regenerates `dist`. Empirically the command converges from a
detached `HEAD` with a dirty tracked file and an untracked `node_modules`,
keeps the untracked directory, exits 0, and exits 0 again when repeated.
`git fetch` updates `origin/main` forcibly for remote-tracking refs, so a
rewritten remote never blocks the fetch that precedes it.

Everything around the command is unchanged: `_clone_if_missing` still
refuses a clone whose origin is not `GITNEXUS_ORIGIN` before any write, the
fetch still precedes the move, the "up to date" short-circuit still compares
`HEAD` to `origin/main` by SHA and skips the build, and `_build` plus the
`--version` verification still follow. The single call keeps the existing
error-message format, so a failure narrates as
`error: GitNexus checkout -f -B main origin/main failed: <git stderr>`.

A two-step variant (`checkout -B` then `reset --hard`) reaches the same
state with one more subprocess and one more failure branch; the single
command wins.

### 4.2 Testing

`tests/unit/test_gitnexus_update.py` already drives a real bare origin, a
seed clone and the managed clone under a temporary `OMC_HOME`, with
recording `npm` and `node` stubs. The seed clone already carries a git
identity and the bare origin accepts a force push, so no fixture plumbing
changes.

A new helper beside `_advance_origin` reproduces the field shape: the
managed clone and the origin have both moved past a common ancestor. The
origin's history is rewritten with `git push --force`. Amending the
fixture's single root commit is not that shape: git then reports
"refusing to merge unrelated histories", a different error from the one in
the field, so the fixture must have a shared ancestor commit.

One new test, `test_rewritten_origin_forces_main_and_rebuilds`: seed,
rewrite the origin, call `update_gitnexus`, assert return code 0, clone
`HEAD == origin/main`, the three npm build lines in order
(`npm install` in `gitnexus-shared`, `npm ci`, `npm run build`), and
"updated" on stderr.

Red first: on the current code this test returns 1 and stderr carries the
field error, "Not possible to fast-forward". The existing
`test_moved_pulls_builds_and_verifies` keeps passing because a fast-forward
is a subset of a forced update. `just check` is the gate.

## 5. Risks

- **Hand edits in the managed clone are lost.** Accepted by Decision 3; the
  clone is omc's build material under `~/.omc`, not a working copy.
- **A future rewrite of the fork's `main` with a different build layout**
  is handled by the same command; whether the build still succeeds is the
  build step's concern and is reported by `_build` as today.

## Implementation review

**Auditor:** OpenAI / Codex — 2026-10-06

conforms, no findings

Reviewed the branch against `origin/main`: §4.1's forced checkout and
unchanged error handling are implemented in `src/omc/gitnexus.py:652`;
approved-origin validation still precedes fetch and branch movement
(`src/omc/gitnexus.py:630`). §4.2's shared-ancestor rewrite helper and
regression assertions are present in `tests/unit/test_gitnexus_update.py:68`
and `tests/unit/test_gitnexus_update.py:253`. The §2 exclusions are preserved.
Implementation validation recorded the expected fast-forward failure before
the fix, 15 passing GitNexus tests afterward, and 1,258 passing project checks.

## Deliberate complexity

None.
