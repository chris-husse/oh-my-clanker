# Force managed GitNexus updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `omc update` recover automatically when the approved GitNexus origin rewrites `main`.

**Architecture:** Replace the managed clone's checkout/fast-forward loop with one forced branch checkout through `ToolContext.run`. Keep origin validation, fetch, SHA short-circuit, build, and CLI verification unchanged. Exercise divergence using the existing local real-Git fixtures.

**Tech Stack:** Python, pytest, Git, uv, just.

**Spec:** `docs/superpowers/specs/2026-10-06-fix-gitnexus-ff-only-merge-diverged-design.md`

## Global Constraints

- Minimal fix plus one reproducing unit test; no dirty-tree guard, no spec amendment, no change to `watch.py`.
- No change to `ensure_gitnexus`, which deliberately never fetches or moves `main`.
- No Docker E2E change.
- The approved-origin refusal still runs before any git write.
- Tracked local modifications in the managed clone are discarded by the force.
- Use exactly `git -C <root> checkout -f -B main origin/main`.
- Keep the existing error-message format: `error: GitNexus checkout -f -B main origin/main failed: <git stderr>`.
- All runtime process execution stays behind `ToolContext`.
- Observe the reproducing test fail before changing production code; `just check` is the project gate.

## Review Focus

The binding spec explicitly limits this fix to one new reproducing test. Existing tests cover fast-forward updates, unchanged SHA, wrong origin, fresh install, and build failures. Additional detached-HEAD, dirty tracked-file, repeat-rewrite, untracked-output, and checkout-failure tests are deliberately not added; review the specified Git command and unchanged control flow for these conditions. This preserves the approved minimal scope.

## Pressure test via omc:explain

`omc internal gitnexus context update_gitnexus` confirms that the function uses `gitnexus_root`, `_clone_if_missing`, `_cli_version`, `_build`, and `ToolContext.run`. Source inspection confirms the two-command loop is the only required production edit. The current generated GitNexus dependency documentation describes the existing fast-forward behavior; the function docstring and committed design require forcing `main`. The implementation deliberately changes the former to meet the latter. No enum, new API, fallback path, or fixture framework is needed.

### Task 1: Force rewritten origin history and prove the regression

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/gitnexus.py` (`update_gitnexus` branch-movement block).
- Modify/Test: `tests/unit/test_gitnexus_update.py` (helper beside `_advance_origin`, one new test).

**Interfaces:**
- Consumes: `update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int`, existing `_seed_clone`, `_advance_origin`, `_make_ctx`, and `_git` fixtures/helpers.
- Produces: same public API; helper `_rewrite_origin(seed, dest)` and test `test_rewritten_origin_forces_main_and_rebuilds`.

- [x] **Step 1: Write the regression test and helper.**

  In `_rewrite_origin(seed, dest)`, advance the seed with `_advance_origin`, fetch and fast-forward the managed clone to that second commit, amend the seed's second commit with a different message, and force-push it. Both tips must retain the original shared ancestor; do not amend the root commit. The new test uses `_make_ctx` and `_seed_clone`, calls the helper, and asserts the following after `update_gitnexus`:

  ```python
  assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
  # Read both SHAs using real git subprocesses, as in the existing moved test.
  assert head == remote
  # Filter recording-stub lines to npm; assert exactly three entries in order.
  assert len(npm) == 3
  assert npm[0].startswith("npm install ")
  assert f"[cwd={dest / 'gitnexus-shared'}]" in npm[0]
  assert npm[1].startswith("npm ci ")
  assert npm[2].startswith("npm run build ")
  assert "updated" in capsys.readouterr().err
  ```

- [x] **Step 2: Observe RED.** Run `uv run pytest tests/unit/test_gitnexus_update.py::test_rewritten_origin_forces_main_and_rebuilds -q`. Expected: return-code assertion fails (`1 != 0`), stderr contains `Not possible to fast-forward`. Save evidence in the task report.

- [x] **Step 3: Implement the single-command update.** In `update_gitnexus`, replace the loop with one `argv` list containing the exact command from Global Constraints, one `ctx.run(argv)`, and the existing nonzero-return diagnostic. Do not alter surrounding logic or other functions.

- [x] **Step 4: Observe GREEN.** Run `uv run pytest tests/unit/test_gitnexus_update.py -q`; every test must pass, including the new regression and existing moved-update test. Run `just check` before committing and record results. Controller runs the omc:check stage after task review.

- [x] **Step 5: Self-review and commit.** Inspect the two-file diff against the spec, then commit only the test and implementation with subject `fix: force managed GitNexus clone onto origin main`. Report RED/GREEN evidence, commit, files, and concerns to the controller; do not publish.

## Implementation evidence

- Product/test commit: `8b10bd3`.
- RED: new regression failed with return code 1 and `Not possible to fast-forward`.
- GREEN: all 15 GitNexus update tests passed.
- Independent task review: spec compliant, quality approved, no blocking findings.
- Controller omc:check: `just check` exited 0; 1,258 tests passed with seven unrelated `forkpty()` deprecation warnings.
