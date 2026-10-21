# Watch: print “models current” only when it is news — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `omc watch` model reconciliation on every tick while printing `✓ <provider> models current (N listed)` only on the first tick and the first successful check after a failed check.

**Architecture:** Add a defaulted keyword to the shared `reconcile` function that controls only its all-clear line. Keep the prior-check failure state in `run_watch`, which passes the keyword each tick. Other entry commands continue using the default.

**Tech Stack:** Python, pytest, uv, just.

**Spec:** `docs/superpowers/specs/2026-10-21-watch-hide-models-current-line-design.md` (read in full before implementation).

## Global Constraints

- `omc watch` reconciles at the start of every tick; only the all-clear narration changes.
- First tick and first successful check after a failed check print the all-clear. Later successful checks stay quiet until another failure.
- A first-tick reconciliation error propagates; later errors print `✗ model reconciliation failed: …`, keep the previous config, and allow later ticks.
- Refresh, probe, remap, and dry-run narration remain unconditional. `omc design`/`start`, `implement`, `review`, and `omc dependency watch` retain their one-time all-clear line.
- Write and run failing tests before each source change. Tests in a selected tier must run or fail loud; do not skip. Assert on state and artifacts rather than CLI transcripts.
- `ToolContext` remains the only subprocess, environment, and network boundary. No dependency or public CLI change.
- Update the 2026-10-15 design record §4 with the pointer specified in the spec; leave its Decision 4 intact. Do not hand-edit generated `README.md` or `.omc/docs/`.

## Review Focus

- An all-current config on a quiet tick must still read catalogs but make no headless CLI call or YAML write: `test_current_models_can_suppress_all_clear_without_side_effects`.
- A stale model must still print refresh, probe `✓ default model works`, and remap lines when the all-clear is suppressed: `test_suppression_keeps_refresh_probe_and_remap_narration`.
- A remap for one provider must not cause an uninteresting all-clear for another provider: `test_suppression_omits_current_provider_during_other_provider_remap`.
- A failed remap for one provider must not print another provider’s all-clear before the whole reconciliation succeeds: `test_failed_mixed_provider_remap_does_not_report_current`.
- A later failure must print `✗`, retain the previous config, and make only the next successful check announce recovery: `test_watch_reports_current_on_entry_and_recovery_only`.
- `--once` must count as the first tick and request its all-clear: `test_watch_once_requests_current_report`.

## File Structure

- `src/omc/reconcile.py`: Owns model checks and their output; add the keyword and guard only the `current_providers` narration loop.
- `src/omc/watch.py`: Owns tick history; track the prior check failure beside `first_tick` and pass the keyword to `reconcile`.
- `tests/unit/test_reconcile.py`: Exercise the flag with current, stale, and mixed-provider catalogs using existing `_context`, `_config`, catalog, and CLI stub helpers.
- `tests/unit/test_watch.py`: Exercise the requested keyword over ticks and update the four existing patched `reconcile` signatures.
- `docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md`: Point its §4 narration sentence to the new watch-specific rule.

Graph pressure check: `src/omc/reconcile.py:reconcile` is called by `src/omc/watch.py:run_watch` and `src/omc/cli/__init__.py:_dispatch`; the defaulted keyword preserves the latter. `src/omc/watch.py:run_watch` owns the loop and calls `_tick` after reconciliation. GitNexus reported `fresh: false` because its generated wiki is nine commits behind its code index, so the exact choices below were checked against the current source and tests. The optional Spectrum source is unavailable for this GitHub-origin repository.

---

### Task 1: Suppress repeated watch all-clears while preserving reconciliation

**Complexity: high** — the task changes two production files and their shared call contract. The code itself is narrow.

**Files:**

- Modify: `tests/unit/test_reconcile.py`
- Modify: `tests/unit/test_watch.py`
- Modify: `src/omc/reconcile.py:198`
- Modify: `src/omc/watch.py:446`
- Modify: `docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md:165`

**Interfaces:**

- Consumes: existing `reconcile(ctx: ToolContext, cfg: Config, leaves: Collection[str], *, say: Callable[[str], object], dry_run: bool) -> Config` and `run_watch(...) -> int`.
- Produces: `reconcile(ctx: ToolContext, cfg: Config, leaves: Collection[str], *, say: Callable[[str], object], dry_run: bool, report_current: bool = True) -> Config`. The CLI dispatcher calls it unchanged; `run_watch` passes `report_current=first_tick or last_reconcile_failed`.

- [ ] **Step 1: Write the failing reconcile tests.** Add these tests beside the matching existing cases in `tests/unit/test_reconcile.py`:
  - `test_current_models_can_suppress_all_clear_without_side_effects`: reproduce `test_old_blank_session_config_is_current_without_turn_or_write` with `report_current=False`; wrap `under_test.taskmodels.catalog` to record provider calls and assert `['codex']`, `lines == []`, `_calls(log) == []`, and config bytes unchanged. Keep the existing default-`True` assertions at lines 396 and 559.
  - `test_suppression_keeps_refresh_probe_and_remap_narration`: use `_config(ctx, model="gpt-5-sol")`, `CODEX_CURRENT`, and `_cli_stub(ctx, "codex", remap_output='{"model": "gpt-6-sol"}')`; call with `report_current=False`; assert one `→ refreshing codex model list with the default model`, one `✓ default model works`, one `→ model: … config updated`, and the saved model is `gpt-6-sol`. Assert `_headless(_calls(log))` has the refresh and remap turns.
  - `test_suppression_omits_current_provider_during_other_provider_remap`: for each of two cases, make a separate home directory under `tmp_path`; start with the same stale Codex model; set `cfg.llm.docs.provider = "claude"` and `cfg.llm.providers["claude"] = ProviderConfig(docs_model="claude-sonnet-5-5")`, then persist that config with `store.save_global`. Install both catalogs and both CLI stubs in the same `ctx.env["PATH"]` bindir. Reconcile `{"orchestrator", "docs"}` with `report_current=False`; assert Codex remap narration and config update, no line containing `models current`, and no headless Claude call. Repeat in the other home with the default keyword and assert `✓ claude models current (3 listed)` is present. This paired assertion prevents a vacuous one-provider test: `current_providers` excludes stale providers.
  - `test_failed_mixed_provider_remap_does_not_report_current`: configure current Claude docs and stale Codex orchestrator models; make the Codex remap probe fail. With the default keyword, assert `OmcError`, refresh and probe narration, no `models current` line, no config write, and no Claude headless turn.

- [ ] **Step 2: Run the new reconcile tests red.** Run `uv run python -m pytest -q tests/unit/test_reconcile.py -k 'current_models_can_suppress or suppression_keeps or suppression_omits or failed_mixed_provider_remap'`. The three flag tests fail because `reconcile` does not yet accept `report_current`; the mixed failure test fails because the all-clear precedes the failed remap. Inspect any other failure before proceeding.

- [ ] **Step 3: Add the flag in `src/omc/reconcile.py`.** Add the keyword-only `report_current: bool = True` after `dry_run`. Guard only the `for provider in sorted(current_providers)` all-clear loop, and emit it on both successful return paths after any remap completes. Do not guard catalog reads, stale detection, refresh/probe, remap, or dry-run output.

- [ ] **Step 4: Run reconcile tests green.** Run `uv run python -m pytest -q tests/unit/test_reconcile.py`; expect all tests to pass, including the two unchanged default-line assertions.

- [ ] **Step 5: Write the failing watch tests.** In `tests/unit/test_watch.py`, add `test_watch_reports_current_on_entry_and_recovery_only` using `_fake_watch_boot`. A patched `reconcile` with a required `report_current` keyword records it and returns outcomes good → `ProbeFailed("GET /v1/models returned 503")` → good → good; patch `_tick` to record config identity and `time.sleep` to stop after four ticks. Assert the requested values are `[True, False, True, False]`, the failure line occurs once, the failed tick uses the previous config, and the loop continues. Add `test_watch_once_requests_current_report` with `once=True`, `enable_documentation=True`, and a patched reconcile that records `report_current`; assert `[True]`. Both tests fail before the watch code passes the keyword.

- [ ] **Step 6: Run the new watch tests red.** Run `uv run python -m pytest -q tests/unit/test_watch.py -k 'watch_reports_current_on_entry_and_recovery_only or watch_once_requests_current_report'`. Expect missing `report_current` at the patched call or an incorrect recorded sequence, not an unrelated boot failure.

- [ ] **Step 7: Update `src/omc/watch.py` and its existing stubs.** Initialize `last_reconcile_failed = False` next to `first_tick`. Call `reconcile(..., report_current=first_tick or last_reconcile_failed)` on every selected-model tick; set the flag to `False` on success and to `True` after a later-tick `OmcError` warning. Preserve the first-tick raise and `first_tick = False` placement. Update `_current_models`, `fake_reconcile`, `failing_reconcile`, and `flaky_reconcile` in `tests/unit/test_watch.py` to accept the new keyword; do not relax their existing behavior assertions.

- [ ] **Step 8: Run watch tests green.** Run `uv run python -m pytest -q tests/unit/test_watch.py`; expect all tests to pass, including first-tick failure and locked-tick coverage.

- [ ] **Step 9: Amend the earlier design record.** Add after its §4 narration sentence: “In `omc watch` this line prints only on the first tick and on the first successful check after a failed one — see `2026-10-21-watch-hide-models-current-line-design.md`.” Check that Decision 4 still says watch reconciles at the start of every tick.

- [ ] **Step 10: Run the project gate.** Run `just check`; expect exit 0 and no skipped tests. Do not turn a missing prerequisite into a skip. The E2E `omc design --dry-run` assertion retains its default behavior but is not a routine development-loop gate.

- [ ] **Step 11: Commit the task.** Review `git diff --check` and the five-file diff, then commit the test, code, and record amendment together with a focused message. Follow the enclosing `/omc:implement` lifecycle for its final check, build, and verify gates before handoff.
