# Fix Watch Index Ownership Flap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin primary-checkout analysis to the configured base and abort a knowledge repair when the checkout moves, preserving watch's synced hook/build behavior.

**Architecture:** Keep one repair function and put branch guards at its writers. Pass the transient `CheckoutMoved` signal to the two existing callers, with watch catching it once in `_refresh_index`; freshness verdicts retain their existing contract.

**Tech Stack:** Python, pytest, shell stubs through `ToolContext`, GitNexus's pinned Node CLI, existing Docker E2E harness.

**Spec:** `docs/superpowers/specs/2026-10-07-fix-watch-index-ownership-flap-design.md` (read fully before implementation).

## Global Constraints

- `OMC_KNOWLEDGE` codes, `store-inverted` semantics and `snapshot_freshness` are unchanged.
- `CheckoutMoved` is a plain `Exception`, not an `OmcError` or freshness reason.
- `ToolContext` remains the only subprocess/env/network boundary; reuse `wtconfig.current_branch`.
- Keep `GITNEXUS_SHARED_STORE=off`, flat-store placement, and existing on-base repair behavior.
- No changes to `dependency.py`, GitNexus, Dockerfiles, SKILL.md, AGENTS.md, the `watch.py` module docstring, or generated README.
- Do not gate successful pointer-heal `clean --gc`; no new guard after successful analysis or during wiki generation. The residual upstream TOCTOU window is out of scope.
- Product changes require observed red → green tests. No skips; unavailable prerequisites fail loudly. Use `/omc:check` in the development loop and the controller's build/verify stages at the completed implementation milestone.
- Keep all work unpublished; task commits are local. The controller owns lifecycle gates, reviews, ledger, and handoff.

## Review Focus

These implied edge conditions receive explicit tests in Task 1 in addition to the spec's nine cases:

- Detached HEAD at a writer must raise with `branch == "HEAD"`, even though upstream accepts an explicit label there.
- An unreadable branch (`current_branch` returns `None`) must raise with an empty branch name, without a write.
- Repeated off-base ticks after a mid-tick warning must stay quiet and resume ordinary repair when base returns.
- A failed analyze on base must retain verdict-based behavior: a fresh artifact may succeed, and a stale artifact still escalates.
- A configured base other than `main` must reach both analyze argv and guard comparisons without a hard-coded branch.

---

## File Responsibilities

| Files | Responsibility |
|---|---|
| `src/omc/gitnexus.py` | Pinned argv, movement exception/guard, shared analyze runner, writer entry guards |
| `src/omc/watch.py` | One catch and warning; two up-to-date token mappings |
| `src/omc/internal.py` | Mid-repair refusal, no verdict, normal busy-lock release |
| `tests/unit/test_watch.py` | Faithful analyze stub, movement injection, watch outcomes and hook/build artifacts |
| `tests/unit/test_gitnexus_refresh.py` | Exact argv, writer boundaries and unchanged on-base repair behavior |
| `tests/unit/test_internal.py` | Stub argv compatibility and internal-command movement/lock regression |
| `tests/e2e/test_e2e_gitnexus.py` | Existing real-tool scenario gains mismatch invariant coverage |
| The 2026-08-03 incremental-sync and 2026-09-27 self-heal specs | Narrow amendment notes required by design §4.6 |

## Pressure Test: Repair, Callers, and Regression Fixtures

The primary knowledge snapshot is stale (`index-behind`: one commit; `wiki-missing`). The prescribed fix is `omc watch --once --enable-documentation` in `/Users/chriphus/OpenSource-Projects/oh-my-clanker`; this planning pass did not mutate knowledge. Graph results are structural leads, independently checked against current source.

`/omc:explain` was applied to the repair/caller boundary and fixture choices using `context refresh_knowledge --file src/omc/gitnexus.py`, `context _tick --file src/omc/watch.py`, `context _run_wiki --file src/omc/gitnexus.py`, and a query for the refresh tests. Current source confirms exactly two repair callers (`watch.py:_refresh_index`, `internal.py:_knowledge_refresh`), two analyze sites, and one wiki caller. The existing `_run_wiki` lacks a base argument, so this plan explicitly threads `base` into it. Current `watch.py:run_watch` confirms hooks/build gate on `synced` or `refreshed`, while `off-branch:*` preserves reset pending. Keep the guards and both callers in one task: splitting them would temporarily expose an uncaught exception to the watch loop.

Source spectrum: unavailable — repository origin is not on the Kraken GitLab; Spectrum indexes only Kraken repositories.

### Task 1: Pin and guard repair writers, handling movement in both callers

Complexity: high

**Files:** Modify the three product modules, three unit-test modules, and two predecessor records listed above.

**Interfaces:**

- Consumes `current_branch(ctx: ToolContext, root: str) -> str | None` and existing `refresh_knowledge(...) -> Freshness`.
- Produces `analyze_argv(ctx: ToolContext, base: str) -> list[str]` with exact suffix `["analyze", "--skip-agents-md", "--skip-skills", "--branch", base]`.
- Produces `CheckoutMoved(branch: str, base: str)` with `.branch` and `.base` attributes, `_require_base(ctx: ToolContext, root: Path, base: str) -> None`, and `_run_analyze(ctx: ToolContext, root: Path, base: str) -> subprocess.CompletedProcess[str]`. Keep the subprocess annotation import under `TYPE_CHECKING`, as already established here.
- Changes `_run_wiki(ctx: ToolContext, cfg: Config, root: Path, base: str, say) -> bool` and its sole call; changes watch `_refresh_index(...) -> Freshness | CheckoutMoved`.
- `refresh_knowledge` retains its existing arguments and return type, but can raise `CheckoutMoved`; an optional `reset_progress: ResetProgress | None = None` argument records reset entry for watch. `_tick` and `_refresh_index` forward the same optional progress object.

- [x] **Step 1: Make fixtures express the race and add failing product regressions.**

In `test_watch.py`, replace `analyze_stamps` on `_ctx_with_healing_node_stub` with `move_to: str | None = None` and `rogue_stamp: str | None = None`, retaining `clean_removes`. On the first analyze, `move_to` switches the real fixture checkout. Parse optional `--branch`: with a label, compare against `/usr/bin/git rev-parse --abbrev-ref HEAD`, refuse mismatch with upstream's exact message and exit 1 before writing; without a label stamp the actual checkout (necessary to reproduce today's bug). Otherwise stamp the label, except `rogue_stamp` deliberately overrides it. Keep the existing metadata `repoPath` format compatible with `_foreign_stamping_stub`. Use absolute utility paths or shell builtins. Update the four end-anchored analyze shell cases to accept trailing arguments; migrate the wrong-stamp test to `rogue_stamp`.

Add a forwarding `ctx.git_bin` stub that moves to an existing `feature/x` on its first `fetch`, then execs `/usr/bin/git`; its marker makes the switch one-shot. Use the existing internal fixture's forwarding shape. Use the same faithful analyze behavior for the internal-command movement test rather than an inert exception mock.

Add/extend the following tests; listed assertions are the decisive assertions, with fixture setup following existing tests:

| Test | Setup and decisive assertions |
|---|---|
| `test_stale_index_heals_with_one_incremental_analyze` and `test_inverted_store_destroys_first_exactly_one_analyze` | Record split argv and assert each analyze is exactly `["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]`; update the existing three exact-list assertions too. |
| `test_move_after_guard_aborts_before_node` (parameterize force refresh true/false) | Stale main-owned index; fetch wrapper moves; `assert token == "off-branch:feature/x"`; no node call; `assert err.count("✗ primary moved") == 1`. |
| `test_move_during_incremental_preserves_snapshot` | Stale main-owned index plus docs sentinel, `move_to="feature/x"`; `assert rc == 0` from once; metadata bytes and docs sentinel unchanged; no `clean`, `wiki`, `destroying and rebuilding`, `✓ knowledge is current`, or mirror-restored narration. |
| `test_move_during_rebuild_warns_without_success` | Initially inverted store, move during its one rebuild analyze; warning present; no `✓ index rebuilt` or `not owned by` failure line. Cleaning before this later move is allowed. |
| `test_refresh_heals_inverted_store`, `test_heal_wrong_stamp_never_claims_success` | Keep ordinary third-party inversion healing on main (`clean --force` then one pinned analyze, rebuilt success); with persistent `rogue_stamp`, keep the existing wrong-owner refusal assertion. |
| `test_reset_move_before_write_remains_pending` | Loop with pending reset and move on first fetch; docs/index bytes unchanged, no clean, `· reset pending` once. Return to main between ticks and assert exactly one eventual reset/clean, proving pending state survived. |
| `test_refresh_move_during_analyze_releases_lock` in `test_internal.py` | Real stub-driven move; `assert rc == 1`; error includes `error: refresh requires the primary checkout to be on main (currently feature/x)`; no `OMC_KNOWLEDGE` stdout line; `assert flock_free(repo / ".git" / "omc-watch-busy.lock")`. |
| `test_move_before_wiki_aborts` | Fresh index, stale wiki, documentation on; switch branch at entry to the actual `_run_wiki` via a delegating wrapper, then let its real guard run; no wiki invocation, warning, no mirror restore or knowledge-current claim. |
| `test_synced_move_keeps_hook_and_auto_build` (parameterize rebase false/true) | Advance origin so tick syncs; move during analyze; warning; post-watch hook writes its usual artifact with `synced`; configured auto-build stub writes an artifact too; `assert rc == 0`. Preserve both artifact checks, not only a token mock. |

Add focused boundary cases in `test_gitnexus_refresh.py`: parameterize `_require_base` with observed `"HEAD"` and `None`, assert `CheckoutMoved.branch == observed or ""` and `.base == "main"`; also invoke reset and `_destroy_and_rebuild` while detached with existing docs/index sentinels and assert no calls or changes. For a custom base `"trunk"`, switch the fixture's branch and call `refresh_knowledge(..., "trunk")` on stale metadata stamped trunk; assert the exact pinned suffix and a fresh verdict. Extend the non-zero analyze regression to cover both fresh artifacts and still-stale artifacts on base (the latter must clean/rebuild).

Add `test_mid_tick_warning_deduplicates_until_base_returns` using the watch loop helper: first tick moves and warns, second stays away and produces no new off-branch message, then return to main and assert a normal repair. In the up-to-date movement tests seed a hook artifact and assert it is absent.

- [x] **Step 2: Observe red before any product edit.**

Run `uv run pytest -q tests/unit/test_watch.py tests/unit/test_gitnexus_refresh.py tests/unit/test_internal.py`. Expected failures: missing pin, missing movement guard/signal, destructive escalation, or wrong caller outcome. A missing new helper is an acceptable focused boundary-test red, but the artifact-based incremental regression must also run and fail for the actual race. Do not accept fixture shell failures as the reproducer. Record the failing tests and causes.

- [x] **Step 3: Implement the pinned writer boundary.**

In `gitnexus.py`, replace `ANALYZE_ARGS` with `analyze_argv`; comment the verified upstream mismatch rule, four placement cases, and why omc reaches only the flat store (inversion is cleaned first and metadata absence checked). `_require_base` uses `current_branch(ctx, str(root)) or ""`, raises without narration, and treats detached/error results as movement. `_run_analyze` checks before `ctx.run(analyze_argv(...), cwd=str(root))`, checks again only on non-zero exit, and returns the completed process; existing callers retain their normal failure narration/verdict logic.

Call `_require_base` before `_clear_mirror` in `_destroy_and_rebuild` and the reset path. Route both analyze sites through `_run_analyze`. Thread `base` to `_run_wiki` and guard on its entry before provider resolution or process startup. Let the exception bypass `finish()`; do not add catches inside the repair ladder or change freshness calculation.

- [x] **Step 4: Implement the two caller contracts.**

In watch `_refresh_index`, catch once and return the exception object after printing exactly:

```text
✗ primary moved to '<branch>' mid-tick — knowledge snapshot NOT updated; retrying when it is back on <base>
```

At the force-refresh and ordinary heal sites in `_tick`, check `isinstance(result, CheckoutMoved)` before freshness access and return `f"off-branch:{result.branch}"` directly (the warning has already printed). Leave both synced sites returning `synced` after the wrapper; preserve the existing hook/build gates. **Implementation-review amendment (2026-10-07):** the original assertion that reset consumption could also remain unchanged was incorrect. Create `ResetProgress(attempted=False)` per tick and pass it to repair; set `attempted` only after the reset base guard and before destructive work. Consume `reset_pending` from this fact independently of the tick token. Movement before reset preserves the pending request (and once-mode notice), while movement after reset must not repeat destructive reset on return to base. Cover ff-merge/rebase with pre-reset movement, hook/build artifacts and eventual single clean, plus movement after clean on both synced and up-to-date ticks.

In `_knowledge_refresh`, catch around the busy-lock-protected repair, print the existing off-base error with the exception's branch/base, and return 1. Ensure the lock context exits and the later knowledge-verdict output is unreachable.

- [x] **Step 5: Re-run focused tests, then the quick stage.**

Run the Step 2 command: expect all pass. Run `/omc:check` (project command `just check`); consume its single successful `OMC_STAGE` verdict through the controller. Do not proceed on a red gate. Existing plugin-manifest tests must remain green with no skill flag changes.

- [x] **Step 6: Amend predecessor records and commit the cohesive change.**

Under Heal step 3 in `docs/superpowers/specs/2026-08-03-fix-gitnexus-watcher-incremental-sync-design.md`, add a dated pointer to this design: analyze now carries `--branch <base>`, `ANALYZE_ARGS` became `analyze_argv`, movement aborts the heal. Under §2 in `docs/superpowers/specs/2026-09-27-stale-knowledge-snapshot-self-heal-design.md`, record the new exception and caller behavior and clarify `_heal_store` is now `_destroy_and_rebuild`'s body. Review `git diff --check`; commit only this task's listed files and plan as coordinated with the controller, with subject `fix: abort knowledge repair when the primary checkout moves`.

## Pressure Test: Real GitNexus Contract

`/omc:explain` applied to the E2E extension used `context test_index_then_explain_on_real_repo --file tests/e2e/test_e2e_gitnexus.py`; current source confirms an already-configured `/repo` container, real index metadata, and a later explain operation that needs main restored. The indexed GitNexus dependency at `24af4f6006f5ee05ebbeeb187bba4c6186d2d5ce` was queried for `resolveBranchPlacement`; direct source checks at `gitnexus/src/storage/branch-index.ts:68` and `gitnexus/src/core/run-analyze.ts:1235` confirm mismatch refusal precedes placement/write, and absent/unstamped/same-owner stores remain flat. Its graph is current and docs are absent; `omc dependency watch` backfills dependency docs when desired.

**Clarification of design §6:** The direct real-CLI step passes `--branch main` explicitly and tests behavior already supplied by the pinned fork. It may already pass before this omc change. Product red evidence comes from Task 1; do not claim an upstream red or manufacture one by deriving E2E argv from a new helper.

Source spectrum: unavailable — repository origin is not on the Kraken GitLab; Spectrum indexes only Kraken repositories.

### Task 2: Add the real CLI mismatch invariant to the existing E2E

Complexity: simple

**Files:** Modify `tests/e2e/test_e2e_gitnexus.py:test_index_then_explain_on_real_repo` only.

**Interfaces:** Consumes existing `run_in`, `_CLI`, `artifacts`, and `head` from that test; adds no public helper/interface and no product behavior.

- [x] **Step 1: Add the contract assertions after index assertions and before explain.**

Capture metadata bytes using the existing `gitnexus.json`/`meta.json` fallback. Switch `/repo` to a throwaway branch `e2e-analyze-mismatch`; in a `try/finally` run exactly `["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]` with cwd `/repo`. Assert `rc != 0` and `'--branch "main" does not match the checked-out branch "e2e-analyze-mismatch"' in out`. Re-read metadata and assert byte equality and `metadata["lastCommit"] == head.strip()`; assert `/repo/.gitnexus/branches` does not exist. Run `["omc", "internal", "gitnexus", "refresh"]` there, assert `rc == 1` and the existing requires-main error. In `finally`, switch `/repo` back to main, asserting success, so the remainder still tests normal explanation. Do not add a container, LLM call, skip, or mock CLI.

- [x] **Step 2: Validate at the completed implementation milestone.**

Run `git diff --check`, then the controller runs final `/omc:check` → `/omc:build` → `/omc:verify` under the lifecycle's bounded stage-gate rule. The verify stage must execute the extended real-tool case; for targeted diagnosis use `just e2e-tests tests/e2e/test_e2e_gitnexus.py::test_index_then_explain_on_real_repo`. Expected: real CLI refuses, metadata remains byte-identical, no branch slot is created, internal refresh refuses, and later explain succeeds. Missing Docker/token/CLI prerequisites are failures, never skips.

- [x] **Step 3: Commit the validated test.**

Commit `tests/e2e/test_e2e_gitnexus.py` with subject `test: verify GitNexus rejects a mismatched analyze label`. Controller finishes review, ledger, and unpublished handoff under the existing implementation authorization.

## Self-review

All design §4 writers/callers, §4.6 amendments, §5 guard edge conditions, and §6 nine cases map to Task 1; the real integration maps to Task 2. The five Review Focus entries each have an owning test. No new verdict code, process boundary, or publication step is introduced. No CRITICAL blocker or design alternative was found; the only clarification is the existing-upstream E2E's red expectation described above.


## Implementation outcome

Implemented and reviewed on this branch. Task commits: `d9a3d89` (repair), `1ade88b` (real-CLI invariant), `c1d8f07` (independent reset progress), and `3623ad7` (E2E formatting).

Final verification: `PYTHONPATH="$PWD/src" just check` passed 1504 tests with seven preexisting forkpty warnings; `just build` passed formatting, lint and package builds; `PYTHONPATH="$PWD/src" just e2e-tests` passed five lifecycle cases and 59 parallel cases. The explicit source path avoids the shared pytest entrypoint importing the primary checkout. An initial formatting failure was corrected; a Docker plugin-clone TLS interruption passed on the unchanged retry. Both bounded repair cycles were consumed; the final gate is green.

Whole-branch review found that synced outcomes could consume an unapplied reset. The reviewed fix tracks reset attempts separately, with seven new regression cases and the design clarification above. Scoped re-review found all findings addressed and no new blocking issues.

Implementation decisions:
- The real-CLI mismatch step verifies an existing upstream invariant; product red evidence comes from the artifact regression. Risk if wrong: integration coverage alone would not prove the omc regression, so observed product red/green remains required.
- The E2E task was committed locally after static checks and reviewed before the conductor's full milestone. Risk if wrong: a local task commit can temporarily contain an unproven E2E; final handoff remained blocked until verify passed.
- Reset attempt state is separate from sync outcome, clarifying the original assumption that unchanged token logic sufficed. Risk if wrong: extra state might repeat or lose resets; before/after-reset movement tests cover both outcomes.

The seven baseline forkpty warnings remain deferred outside this change. Upstream analyze/wiki race windows and preexisting sync races retain the committed design's scope. Nothing was published.
