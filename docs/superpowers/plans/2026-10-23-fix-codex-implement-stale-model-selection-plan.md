# Fix Codex Implement Stale Model Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every Codex model choice from `codex debug models` on the binary omc launches, so reconciliation and launch use one catalog.

**Architecture:** `taskmodels` owns one validated, context-scoped Codex catalog reader. Reconciliation forces a read at entry; subsequent resolution and configure pickers reuse it, while the next watch tick forces another read. Claude's catalog and refresh path remain as they are.

**Tech Stack:** Python 3, `ToolContext.run_bounded`, pytest, shell/Python CLI stubs, Docker E2E.

**Spec:** `docs/superpowers/specs/2026-10-23-fix-codex-implement-stale-model-selection-design.md`

## Global Constraints

- Red → green for every changed behavior: write and run a failing test before implementation. Tests in a selected tier must run or fail loudly, never skip.
- Use `ToolContext` for subprocesses; argv lists only. `codex debug models` is the sole Codex catalog source, with no cache-file fallback or model turn.
- The context memo is per object, including failures; `catalog(ctx, "codex")` always refreshes it. `resolve_choice` and `model_options` reuse it. No process-global or `id(ctx)` cache.
- Preserve Claude catalog and no-model refresh behavior, Codex full-ID live probe, remap/write-back/dry-run behavior, and existing launch argv aside from the resolved slug.
- Restricted-PATH stubs use shell builtins, absolute paths, or quoted heredocs. The check tier must never invoke a host Codex binary.
- `README.md` is generated: update `.claude/skills/regenerate-readme/SKILL.md` and follow `/regenerate-readme`; do not hand-edit README.
- After every task's tests and review, run `/omc:check` and require its `OMC_STAGE` verdict with `"passed": true` before the next task. The conductor runs `/omc:build` then `/omc:verify` at the implementation milestone.

## Review Focus

- A successful `debug models` JSON response accompanied by a stderr warning still resolves from stdout; Task 1 tests this.
- A missing `codex` executable reports the provider install hint for resolution while the picker degrades to families; Task 1 tests both paths.
- A hung `debug models` command fails with a named, bounded `ConfigError` and never reads a planted valid cache; Task 1 tests this.
- Two `ToolContext` objects in one Python process do not share success or failure memo entries; Task 1 tests this.
- A failed watch-tick read can recover on the next tick; Task 2 tests two forced reads on one context, the second succeeding.

## File Map

| File | Responsibility |
| --- | --- |
| `src/omc/taskmodels.py` | One Codex CLI reader, shape validation, memo, and its catalog/resolution/picker consumers. |
| `src/omc/toolctx.py` | Provider-neutral `runtime_cache` owned by each `ToolContext`. |
| `src/omc/reconcile.py` | Keep the stale/remap transaction and refresh only Claude's CLI list. |
| `src/omc/configure.py` | Probe distinct changed leaves without Codex refresh bookkeeping. |
| `src/omc/providers/codex.py` | Remove the obsolete cache-file path method. |
| `tests/unit/_stubs.py` and affected `tests/unit/test_*.py` | Make isolated Codex stubs answer `debug models`; assert CLI calls and state. |
| `tests/e2e/test_e2e_task_models.py`, `tests/e2e/test_e2e_reconcile.py` | Derive expected IDs from the real Codex command. |
| `.claude/skills/regenerate-readme/SKILL.md`, `README.md` | State the provider-specific refresh rule in the recipe and generated output. |

The plan sections below were pressure-tested against the GitNexus graph and current files. The graph identifies `_model_list`'s direct callers as `_codex_catalog`, `model_options`, and `resolve_choice` (`src/omc/taskmodels.py`); its impact reaches `reconcile`, `configure`, and `internal models`. The generated graph docs are 12 commits behind the index, so use current source for exact interfaces.

---

### Task 1: Replace the Codex reader and preserve one catalog per context

Complexity: high

**Files:**
- Modify: `src/omc/taskmodels.py`, `src/omc/toolctx.py`
- Modify: `tests/unit/_stubs.py`, `tests/unit/test_taskmodels.py`
- Modify: `tests/unit/test_start.py`, `tests/unit/test_implement.py`, `tests/unit/test_review.py`, `tests/unit/test_session.py`, `tests/unit/test_watch.py`, `tests/unit/test_configure.py`, `tests/unit/test_configure_menu.py`, `tests/unit/test_configure_pty.py`, `tests/unit/test_reconcile.py`

**Interfaces:**
- Produce `ToolContext.runtime_cache: dict[str, object]`, with `field(default_factory=dict, repr=False, compare=False)`; test context fakes that exercise the reader gain this field and `run_bounded`.
- Produce `taskmodels._codex_models(ctx: ToolContext, *, refresh: bool) -> list[_ListedModel]`. Store either the parsed list or its `ConfigError` in `ctx.runtime_cache` under a private taskmodels key. A refresh replaces either value. `catalog(ctx, "codex")` calls with `refresh=True`; `resolve_choice` and `model_options` call with `refresh=False`.
- Keep `catalog`, `resolve_choice`, `task_choice`, `model_options`, and `validate_selection` public signatures unchanged in this task. Task 2 removes `validate_selection(refresh=...)`.
- Produce `make_codex_stub(bindir: Path, *, catalog: dict[str, object] | str, catalog_rc: int = 0, stdout: str = "", rc: int = 0, argv_log: Path | None = None) -> Path` in `tests/unit/_stubs.py`: `debug models` writes catalog to stdout and exits `catalog_rc`; all other argv preserve the requested launch output/exit code. Append exact argv lines when logging. Use restricted-PATH-safe shell syntax.

- [ ] **Step 1: Write the failing regression using the existing stub.** In `tests/unit/test_taskmodels.py`, plant `.codex/models_cache.json` with `client_version: "0.159.2"` and priority-0 `gpt-6.1-sol`. Use the already available `make_stub(bindir, "codex", stdout=json.dumps({"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}))`; no new helper is needed for this first test. Assert `session_plan(...).session_argv` contains `-m gpt-6-sol`, never `gpt-6.1-sol`. The old code must select the planted cache slug. Also assert a later `reconcile(ctx, cfg, {"model"}, ...)` reports `✓ codex models current`, performs no remap, and does not write config.
- [ ] **Step 2: Run that test and record the expected red result.** Run `uv run --frozen pytest tests/unit/test_taskmodels.py -q -k 'other_client'`; expected failure is the stale `gpt-6.1-sol` launch slug.
- [ ] **Step 3: Test the branching stub helper.** Add `test_make_codex_stub_routes_debug_models_and_records_calls` in `test_taskmodels.py`, accessing the not-yet-defined helper through the `_stubs` module. Assert `debug models` returns the catalog JSON, a normal launch returns `stdout`, and the argv log has both exact lines. Run `uv run --frozen pytest tests/unit/test_taskmodels.py::test_make_codex_stub_routes_debug_models_and_records_calls -q`; expect `AttributeError` for the absent helper.
- [ ] **Step 4: Implement the helper and turn its test green.** Add `make_codex_stub` with the interface above, using a `case "$1 $2"` branch, quoted heredoc and append-only argv log. Run `uv run --frozen pytest tests/unit/test_taskmodels.py -q -k 'make_codex_stub'`; expected pass.
- [ ] **Step 5: Add reader tests before the reader.** Add `test_debug_models_prefers_visible_priority_and_efforts` (`sol:high` resolves to `ModelChoice("gpt-6-sol", "high")`, hidden priority-0 and `solar` ignored); parametrized `test_debug_models_invalid_output_never_uses_cache` (empty/invalid JSON or malformed fields raises `ConfigError` naming the command despite a valid cache); `test_debug_models_nonzero_and_timeout_never_use_cache` (named `ConfigError` with exit/timing detail); `test_debug_models_missing_binary_has_install_hint`; `test_debug_models_uses_bundled_stdout_and_ignores_stderr` (valid stdout resolves despite warning and no auth); `test_debug_models_passes_codex_home`; `test_debug_models_memo_is_per_context` (seven task choices issue one command, new context issues another); and a menu test using configure's `warn_once` callback (seven pickers, one failed command and one warning). The existing `_model` factory supplies entries; only these failure tests plant a valid competing cache.
- [ ] **Step 6: Run the focused reader tests and record red.** Run `uv run --frozen pytest tests/unit/test_taskmodels.py -q -k 'debug_models or other_client'`; expect failures from the file-backed reader or missing CLI calls, not from test setup.
- [ ] **Step 7: Implement the reader and switch all three consumers.** `ctx.run_bounded(["codex", "debug", "models"], timeout=docsllm.AUTH_STATUS_TIMEOUT)` parses stdout only, through today's `_ListedModel` field validation. Use no `cwd` (the command is not project-relative and isolated contexts need not create `ctx.home`). Convert `FileNotFoundError`, `TimeoutError`, nonzero status, and invalid/empty stdout to `ConfigError` naming `codex debug models`; missing binary includes `CodexProvider.install_hint()`. Keep the error's output tail bounded. Preserve priority, visibility, effort, family-word matching, and cheap-model exclusion. Update `_family_model`'s source label to `codex debug models`, and remove the cache-file read and Codex `CatalogMissing` branch. Leave the obsolete `model_list_path` method until Task 2 removes `validate_selection`'s last reference. At the CLI call site, comment on the verified 0.158 stdout shape, TTL/version refresh, logged-out bundled list, and stderr warning behavior.
- [ ] **Step 8: Migrate the unit fixture seam and old cache assertions.** Replace `seed_codex_model_cache` at its six test-file call sites and `_cache`/`_codex_catalog` fixture writers with branching stubs; keep a planted cache only in the regression and no-fallback tests. Give six duck-typed context fakes `run_bounded` and `runtime_cache`, or use real `ToolContext` over a stub PATH. Add a `debug models` branch to `test_reconcile.py::_cli_stub` while preserving its remap behavior. For the transitional old refresh test, its existing `refreshed` hook must change the stub's subsequent `debug models` response after the no-model turn; Task 2 deletes the hook and the turn. Put an isolated stub first on both the PTY subprocess PATH and its `ToolContext.env["PATH"]` in `test_configure_pty.py`. Leave the obsolete provider-path test for Task 2. Update existing expected errors from cache paths to the CLI command.
- [ ] **Step 9: Run focused tests to green.** Run `uv run --frozen pytest tests/unit/test_taskmodels.py tests/unit/test_providers.py tests/unit/test_session.py tests/unit/test_start.py tests/unit/test_implement.py tests/unit/test_review.py tests/unit/test_watch.py tests/unit/test_configure.py tests/unit/test_configure_menu.py tests/unit/test_configure_pty.py tests/unit/test_reconcile.py -q`; expected all pass. Keep the old refresh-turn expectations only as transitional coverage; Task 2 changes them.
- [ ] **Step 10: Commit the test and implementation together.** The controller then runs spec and code-quality reviews, followed by `/omc:check`. Require `OMC_STAGE {"stage":"check",..."passed":true,...}` before Task 2.

**Graph pressure test:** `src/omc/taskmodels.py:_model_list` has exactly three direct callers in the current graph, and `src/omc/taskmodels.py:resolve_choice` feeds `task_choice` and configure validation. The context-local cache is necessary because `src/omc/internal.py:_models` resolves all seven tasks in one process, while a watch process reuses its context across ticks. Keep the memo at the reader so no launch or picker can bypass it.

### Task 2: Remove Codex refresh turns and keep watch/check agreement

Complexity: high

**Files:**
- Modify: `src/omc/reconcile.py`, `src/omc/configure.py`, `src/omc/taskmodels.py`, `src/omc/providers/codex.py`
- Modify: `tests/unit/test_reconcile.py`, `tests/unit/test_configure.py`, `tests/unit/test_taskmodels.py`, `tests/unit/test_watch.py`, `tests/unit/test_configure_menu.py`, `tests/unit/test_providers.py`

**Interfaces:**
- Consume Task 1's `_codex_models(ctx, *, refresh)` behavior. `catalog(ctx, "codex")` already forces a fresh read; no new reader API is needed.
- Change `validate_selection(ctx, provider, value, *, task, say) -> ModelChoice` by removing `refresh`. Family choices resolve with `validate_effort=True` against the memo; full IDs still call `docsllm.cli_model_probe` once.
- Preserve `reconcile(ctx, cfg, leaves, *, say, dry_run, report_current=True) -> Config` and `_probe_task_models(ctx, gcfg, *, before) -> None` signatures.

- [ ] **Step 1: Write failing Codex refresh tests.** In `test_reconcile.py`, a stale Codex leaf must issue one `debug models` read, then make the remap `exec` its first model turn; assert absence of `→ refreshing codex model list...`. For a current `sol:high` leaf followed by `session_plan` on the same context, assert exactly one `debug models` call and the launched `-m` equals that catalog's priority winner. Two reconciliations on one context must read twice; if the first read fails, the second can succeed. Keep the Claude stale test asserting its no-model refresh. In `test_configure.py`, setting a Codex family must make one `debug models` call and zero `exec` turns, while a full ID still probes once.
- [ ] **Step 2: Run focused tests red.** Run `uv run --frozen pytest tests/unit/test_reconcile.py tests/unit/test_configure.py -q -k 'codex or claude'`; expect old Codex no-model refresh assertions to fail for the new tests.
- [ ] **Step 3: Remove only Codex refresh behavior.** In `reconcile`, include only stale Claude CLI leaves in the refresh set; update the comment/docstring to describe Claude's turn and Codex's live read. Keep dry-run before any refresh/remap, and leave remap, config write, current narration, and watch retry handling intact. In `validate_selection`, remove the refresh argument, no-model probe, and obsolete missing-file translation; then delete `CodexProvider.model_list_path` and its provider test. In `_probe_task_models`, remove `refreshed` and its family check while keeping distinct effective-value deduplication. Update the picker docstring to say CLI failure causes families-only degradation; repeated picker calls share the memoized error and `warn_once` prevents repeated user warnings.
- [ ] **Step 4: Update behavior tests and watch case.** Replace old Codex refresh/cache assertions in `test_taskmodels.py`, `test_configure.py`, and `test_reconcile.py`. Keep the Claude assertions. In `test_watch.py::test_auto_build_model_resolution_failure_does_not_stop_watch_loop`, make `debug models` fail and assert its command appears in the warning while the loop continues. In `test_configure_menu.py`, remove cache-file assumptions and assert a missing/failing binary yields families-only choices with one warning.
- [ ] **Step 5: Run focused tests green.** Run `uv run --frozen pytest tests/unit/test_reconcile.py tests/unit/test_configure.py tests/unit/test_taskmodels.py tests/unit/test_watch.py tests/unit/test_configure_menu.py -q`; expected all pass.
- [ ] **Step 6: Commit the test and implementation together.** The controller then runs spec and code-quality reviews, followed by `/omc:check`. Require a passed `OMC_STAGE` verdict before Task 3.

**Graph pressure test:** `src/omc/reconcile.py:reconcile` owns the refresh loop after its dry-run check, and `src/omc/configure.py:_probe_task_models` is the only caller passing `refresh=` to validation. The graph shows configure's menu enters through `model_options`; preserving its families-only degradation keeps an absent CLI from crashing menu rendering. No change is needed in `session.py` or `watch.py` product code: both resolve through `taskmodels`.

### Task 3: Verify the real binary and regenerate user documentation

Complexity: medium

**Files:**
- Modify: `tests/e2e/test_e2e_task_models.py`, `tests/e2e/test_e2e_reconcile.py`
- Modify: `.claude/skills/regenerate-readme/SKILL.md`, generated `README.md`

**Interfaces:**
- Consume the existing `run_in(container, ["codex", "debug", "models"]) -> tuple[int, str]` E2E harness call; parse its stdout JSON `{"models": [...]}`. Do not read `models_cache.json` or run a no-model turn to populate it.

- [ ] **Step 1: Change the real-tool tests first.** `test_codex_family_uses_real_model_list` derives its priority-winning visible Sol slug from `codex debug models`, then asserts `omc internal models` returns it for `simple`. `test_e2e_reconcile.py::_catalog_models` obtains visible IDs/efforts from the same real command for its retired-ID recovery assertion. Make absence or invalid output fail with an exact setup/action message; preserve Claude's catalog branch. Assert artifacts and verdict/config values, not transient mid-session text.
- [ ] **Step 2: Run a local syntax/import check.** Run `uv run --frozen pytest --collect-only tests/e2e/test_e2e_task_models.py tests/e2e/test_e2e_reconcile.py -q`; expected collection succeeds. The real E2E run belongs to the milestone `/omc:verify` gate, not the per-task loop.
- [ ] **Step 3: Correct the README recipe and regenerate.** In `.claude/skills/regenerate-readme/SKILL.md`, replace the universal “one refresh” claim with: Codex reads `codex debug models` on its launched binary and remaps a still-stale value immediately; Claude keeps one no-model refresh before remap. Follow that skill's full regeneration steps to produce `README.md`; preserve its generated banner, outline, links, and guards.
- [ ] **Step 4: Run documentation guards.** Run `uv run --frozen pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'` and `git diff --check`; require both pass.
- [ ] **Step 5: Commit the E2E and documentation changes together.** The controller then runs spec and code-quality reviews, followed by `/omc:check` with a passed `OMC_STAGE` verdict. The conductor runs `/omc:build` followed by `/omc:verify` at its milestone gate.

**Graph pressure test:** the existing E2E Codex helpers read the cache file, so they would continue testing the obsolete source unless changed. The README recipe explicitly mandates regeneration and contains the current universal refresh claim. The real Codex command may warn on stderr in Docker; parse stdout JSON only.

## Self-review

- The reader, reconcile, configure, watch, internal-models, menu, session, E2E, and README requirements each have an owning task and test.
- Each behavior change starts with a test observed red; each task ends with the project check gate.
- `runtime_cache` is provider-neutral and per context; `_codex_models` is the only Codex read; `refresh=True` is used only for reconciliation's entry read.
- No task edits the superseded design record, Claude catalog code, direnv launch behavior, or the remap parser.
