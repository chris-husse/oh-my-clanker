# Reconcile Stale Configured Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before a provider-using omc launch, detect configured models absent from the provider's current catalog, ask its default model for replacements only when needed, and persist valid full IDs.

**Architecture:** `taskmodels` reads normalized provider catalogs and checks compatibility; `reconcile` owns refresh, one default-model remap turn per provider, validation, and leaf-only write-back. CLI entry and each watch tick call it explicitly. Static config validation and the read-only `omc internal models` remain unchanged.

**Tech Stack:** Python dataclasses, YAML, pytest, Claude/Codex CLIs, Anthropic models API through `ToolContext`.

**Spec:** `docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md`

## Global Constraints

- Check session leaves (`model` and six `tasks.*`), documentation `docs_model`, and watch auto-build `model` only where the command uses them. Blank leaves resolve through provider defaults; blank Codex `docs_model` needs no check.
- The cheap/fast tier (`haiku`, `luna`) is never offered or chosen. A remap persists a catalog-listed full ID, optionally with supported effort, to `~/.omc/config.yaml` only.
- No model turn or config write on a compatible catalog. On an incompatible CLI catalog, first make one no-model refresh turn and reread; the API catalog is already live. A dry run reports staleness but makes no turn or write.
- Use `ToolContext` for process, environment, provider file, and network access. Preserve redacted probe errors and the provider's exact argv; do not add subprocess/network I/O elsewhere.
- Keep `validate_task_model`, `omc internal models`, provider family menus, configure probes, and knowledge snapshots unchanged. Do not edit generated `README.md` by hand or run `omc install` / `uv tool install`.
- Follow the required red → green cycle for every change. Selected tests must run or fail loudly, with restricted-PATH stubs using builtins or absolute commands. Use `just check` in the dev loop; `just build` and real-provider E2E at the implementation milestone.
- Commit each task's test and implementation together on this unpublished branch. Do not push or publish during implementation.

## Review Focus

- A hand-edited current full ID absent only from yesterday's cache must survive a refresh; pin this in Task 3's refresh-only test.
- A Claude account with files from two organizations must use the `orgId` from its current auth status; pin this in Task 1.
- A stale family in `omc design --dry-run` must report all stale leaves and exit successfully without reaching session model resolution; pin this in Task 4.
- Two stale leaves for the same provider must produce one remap turn and no partial save when one answer is invalid; pin this in Tasks 2–3.
- A long-running watch must preserve a newer configure edit in an unrelated leaf; pin this in Task 3's fresh-reload test.

## Pressure-test findings

I applied `/omc:explain` to the catalog, remap/persistence, command/watch, and E2E sections. The local GitNexus snapshot reports current against the main HEAD and connects `configure._probe_task_models → taskmodels.validate_selection → docsllm.cli_model_probe → Provider.headless_argv`, plus `watch._auto_build → taskmodels.orchestrator`; focused source reads establish the remaining entry seams. Graph search is keyword-only and incomplete, so source contracts below take precedence. Source spectrum: unavailable because this repository's origin is GitHub and Spectrum's repository gate permits only the Kraken GitLab.

- Catalog: `taskmodels._model_list` already strictly parses Codex fields; `docsllm.cli_connection_probe` owns Claude auth diagnostics; `ToolContext.read_text` is the file seam. Add a directory-list sibling and keep provider quirks beside the code that relies on them.
- Remap: `docsllm.cli_model_probe` already uses a bounded no-model headless call and redacts/truncates errors. `store.load_global` / `save_global` are the persisted config boundary; `resolve.load_effective` takes `llm` from the global file alone.
- Entry/watch: `cli._dispatch` applies `_with_provider` before worker calls; `watch.run_watch` holds the busy lock around each `_tick` and auto-build. Reconcile there before `_tick`. `dependency watch` delegates document runs to subprocesses, so its entry check alone suffices.
- E2E: `harness.configure_omc` pins all seven session leaves and `conftest.py` marks Codex provider tests for the opt-in serial gate. Use direct container config edits for retired IDs; `omc configure` rightly refuses them during its live probe.
- Dry-run refinement: `start`, `implement`, and `review` build a session plan after dispatch. A stale Codex family would fail there, so a stale dry run needs a successful short circuit after reporting every stale leaf; a current dry run keeps the existing complete preview.

### Task 1: Normalize current provider catalogs

Complexity: high

**Files:** Modify `src/omc/{taskmodels,docsllm,toolctx}.py`; test `tests/unit/{test_taskmodels,test_docsllm,test_toolctx}.py`.

**Interfaces:** Produce `CatalogModel(id: str, family: str, efforts: tuple[str, ...])`, `CatalogMissing(ConfigError)`, `taskmodels.catalog(ctx: ToolContext, provider: str, *, docs_api_key: str = "") -> list[CatalogModel]`, and `taskmodels.compatible(value: str, effort: str, models: list[CatalogModel]) -> bool`. Catalog order puts the provider's preferred/newest entry first within a family: Codex ascending `priority`, API descending `created_at`, Claude catalog order (confirm against the live CLI). `CatalogMissing` distinguishes an absent CLI cache eligible for one refresh from malformed data that must fail immediately. Produce `docsllm.auth_status(ctx: ToolContext, provider: str) -> dict[str, object]` with `cli_connection_probe`'s existing failure messages; preserve `cli_connection_probe(ctx, provider) -> tuple[bool, str]`. Produce `ToolContext.list_dir(path: Path) -> list[Path]`.

- [ ] Write failing tests for Codex visible exact-family matches, priority, supported efforts, hidden/cheap exclusions, malformed list errors naming the file, and `CODEX_HOME` path. Assert `compatible("gpt-5.1-sol", "", listed) is False`, a listed full ID is true, and unsupported applied effort is false.
- [ ] Write failing Claude tests using the spec's `catalog.config.models[].{id,short_name,thinking.effort_options[].id}` shape and `auth status` with `orgId`/`configDirectory`: select the current organization's `<orgId>-*-cc.json`, choose newest `fetchedAt` among matches, include `main` and `overflow`, strip `[1m]` only for lookup, reject malformed shape with its path, and report missing catalog with the “open claude once” hint. Cover logged-out/broken auth without changing the old probe contract.
- [ ] Write failing API and ToolContext tests: reuse a fake `ctx.http_get`, preserve the API probe's redaction on failure, filter Haiku, derive family through `api_model_family`, and verify `list_dir` through the context seam.
- [ ] Run `uv run pytest tests/unit/test_taskmodels.py tests/unit/test_docsllm.py tests/unit/test_toolctx.py -q`; observe failures caused by missing catalog/list_dir/auth interfaces.
- [ ] Implement the signatures above. Reuse `_model_list`, `_matches_family`, `api_connection_probe`, and `probe_key` semantics; do not change `resolve_choice` or static load behavior. Place Claude catalog path/filename/auth-status assumptions in code-site comments. Do not read or print the API key outside the existing redacted probe.
- [ ] Run the focused command again and `just check`; expect both to exit 0. Commit test and product files as `feat: read provider model catalogs for reconciliation`.

### Task 2: Define and validate one default-model remap response

Complexity: medium

**Files:** Create `src/omc/reconcile.py`, `tests/unit/test_reconcile.py`.

**Interfaces:** Produce immutable `StaleLeaf(path: str, provider: str, value: str, role: str, effort: str, source: str)` (`source` is `cli` or `api`), `build_remap_prompt(stale: list[StaleLeaf], offered: dict[str, list[CatalogModel]]) -> str`, and `parse_remap_output(output: str, stale: list[StaleLeaf], offered: dict[str, list[CatalogModel]]) -> dict[str, str]`. `offered` is keyed by leaf path. Output keys are exactly the stale paths (`model`, `tasks.review`, `docs_model`); each value is a listed full ID with optional supported effort. Later tasks use these without duplicating validation.

- [ ] Write failing tests that the prompt names each leaf, schema role, effective value, and only that leaf's allowed `(id, family, efforts)` list; it asks for one JSON object and includes no API key. Verify two stale leaves from one provider appear together.
- [ ] Write failing parser tests: surrounding CLI lines are allowed; missing/extra keys, malformed JSON, off-list ID, cheap ID, unsupported effort, malformed task choice, and malformed docs ID raise `ConfigError` naming leaf, answer, and offered IDs. Assert one bad answer leaves the entire result unavailable to the caller.
- [ ] Run `uv run pytest tests/unit/test_reconcile.py -q`; observe missing-interface failures.
- [ ] Implement the prompt and strict parser in `reconcile.py`, using `validate_task_model` for task/model leaves and `docsllm.check_model_id` for docs. Validate all values before returning any mapping. Document Codex stdout framing at the parser site and confirm its actual shape during Task 5.
- [ ] Run the focused command and `just check`; expect exit 0. Commit as `feat: validate catalog-bound model remap answers`.

### Task 3: Reconcile, refresh, and persist only stale leaves

Complexity: high

**Files:** Modify `src/omc/reconcile.py`; extend `tests/unit/test_reconcile.py`.

**Interfaces:** Produce `reconcile(ctx: ToolContext, cfg: Config, leaves: Collection[str], *, say: Callable[[str], object], dry_run: bool) -> Config`, where groups are `session`, `docs`, `orchestrator`. Produce `DryRunStale` as a successful-control sentinel raised only after all stale dry-run lines have been emitted; Task 4 catches it. `session` checks the effective provider's `model` and six task leaves, `docs` uses `cfg.llm.docs.provider or cfg.llm.default` and its backend/key, and `orchestrator` checks `cfg.llm.default`'s `model`.

- [ ] Write failing no-op and dry-run tests over a `ToolContext.from_env` temporary home: old config with blank task leaves reads defaults, current leaves make zero headless calls and zero writes with one `✓ <provider> models current (<count> listed)` line per provider, empty group makes no auth/file read, and a stale dry run reports every `· <leaf>: ... (would remap)` before `DryRunStale`, with zero turns and byte-identical YAML.
- [ ] Write failing refresh tests using exact-argv stubs: one stale CLI leaf causes one no-`--model`/`-m` refresh, then reread. When the refreshed catalog now lists the original ID, assert zero remap turns and zero writes. Missing Claude catalog after refresh raises `ConfigError` with the catalog path and “open claude once”; a failed refresh carries the redacted probe detail.
- [ ] Write failing remap/write tests: vanished family, retired pinned ID, effort-only mismatch, CLI and API `docs_model`, two leaves batched into one no-model remap turn, and combined docs/API plus orchestrator catalogs for one provider. Assert exact provider argv, full-ID results, unchanged unrelated YAML leaves, in-memory values updated, and one remap narration per leaf. For API docs, assert GET list then no CLI refresh but still a default-model CLI remap.
- [ ] Write failing atomicity tests: invalid answer or failed turn leaves YAML byte-identical; a fresh `store.load_global(ctx.home)` just before save retains an unrelated newer edit over the long-running `cfg` snapshot. Concurrent writes to the same leaf use the design's accepted last-writer-wins behavior.
- [ ] Run `uv run pytest tests/unit/test_reconcile.py -q`; observe the expected failures before implementation.
- [ ] Implement collection, catalog buckets by provider and source, refresh once per stale CLI provider (including `CatalogMissing`) via `docsllm.cli_model_probe(ctx, provider, "")`, regroup after reread, one remap turn per provider, full validation, fresh global reload and leaf-only `save_global`. Treat API docs and CLI session catalogs separately even if they share a provider; omit blank Codex docs default. Surface probe errors as rc 1 through the existing `OmcError` path. Add no hidden work to getters.
- [ ] Run the focused command and `just check`; expect exit 0. Commit as `feat: reconcile stale model leaves at launch`.

### Task 4: Wire command entry and every watch tick

Complexity: high

**Files:** Modify `src/omc/cli/__init__.py`, `src/omc/watch.py`; test `tests/unit/test_cli.py`, `tests/unit/test_watch.py`, `tests/unit/test_depwatch.py` as needed.

**Interfaces:** Consume Task 3 `reconcile` and `DryRunStale`. `_dispatch` calls `reconcile` immediately after `_with_provider` for `design`/`start`, `implement`, `review` with `{"session"}` and for `dependency watch` with `{"docs"}`. `run_watch` calls it inside the busy lock at the start of every tick with `{"docs"}` if documentation is enabled and `{"orchestrator"}` if auto-build is enabled; no groups means no call. Pass the returned `Config` to the existing worker/tick.

- [ ] Write failing dispatch tests by monkeypatching reconciliation beside existing worker stubs. Assert call order, provider override isolation (`--claude`/`--codex` lists only the effective session provider), group, dry-run flag, and returned config identity for each of the four entry commands. `dependency list`, `configure`, and `internal models` must not reconcile.
- [ ] Write failing stale dry-run dispatch tests for all three session commands: fake `reconcile` emits two reports then raises `DryRunStale`; assert rc 0, no worker, no write/model turn. With current models it returns normally and the existing full dry-run preview executes.
- [ ] Write failing watch tests for zero/one/both flags across two ticks: reconciliation runs once at each tick's beginning while the busy lock is held, before `_tick` and `_auto_build`; no flags yield no call. Assert a docs-only watch checks the docs provider, auto-build checks default orchestrator, and a remapped config reaches `_auto_build`.
- [ ] Run `uv run pytest tests/unit/test_cli.py tests/unit/test_watch.py tests/unit/test_depwatch.py -q`; observe the expected dispatch failures.
- [ ] Add the explicit entry calls and stale dry-run catch. Keep `require_tools` and `ensure_plugin` in their existing workers; the new probe precedes them as the design specifies. Do not add per-tick reconciliation to `dependency watch` or to `_auto_build` itself.
- [ ] Run the focused command and `just check`; expect exit 0. Commit as `feat: reconcile models before provider commands and watch ticks`.

### Task 5: Verify actual provider catalogs and launch recovery

Complexity: high

**Files:** Create `tests/e2e/test_e2e_reconcile.py`; modify `tests/e2e/test_e2e_task_models.py` only if sharing a fixture; add provider-site comments in `src/omc/{taskmodels,reconcile}.py` for observed CLI behavior.

**Interfaces:** Use existing `configure_omc`, `make_work_repo`, `run_in`, `require_token`, and `require_codex_ready`; no new production interface. Mark Claude and Codex cases individually with `e2e_provider`, so the Codex case remains in the opt-in serial gate.

- [ ] Write a real Claude variation: configure and pin seven leaves, edit `~/.omc/config.yaml` directly so the orchestrator or one task leaf is retired `claude-opus-4-1`, run `omc design PROJ-1 --headless` in a prepared repo, assert rc 0/session artifact and YAML now contains a full ID present in that container's current Claude catalog. Assert a second launch/check has no remap narration or config diff. Never assert on an invisible mid-session verdict.
- [ ] Write a real Codex variation with retired `gpt-5.1-sol`, `CODEX_HOME/models_cache.json`, `require_codex_ready`, and the same launch/config/catalog assertions. Keep this case in `just codex-gate`; a missing account or catalog fails with an exact repair command, never skips.
- [ ] Run the new selected E2E tests and observe failures before any production adjustment. If the live Claude catalog's ordering, creation on first headless turn, or Codex stdout framing differs from Task 1–2 assumptions, add a focused failing unit test, then make the smallest correction and record the observed CLI behavior at its code site.
- [ ] Run `uv run pytest tests/unit/test_taskmodels.py tests/unit/test_docsllm.py tests/unit/test_reconcile.py tests/unit/test_cli.py tests/unit/test_watch.py -q`, `just check`, and `just build`; expect exit 0. Run `just e2e-tests tests/e2e/test_e2e_reconcile.py` for Claude and `just codex-gate tests/e2e/test_e2e_reconcile.py` for Codex, recording exact results and external prerequisites. Commit as `test: prove stale model recovery with real providers`.

Controller continuation: review the whole branch and address findings, then run the implementation milestone gates `/omc:check` → `/omc:build` → `/omc:verify` under the bounded stage rule. Commit the plan with the final implementation, keep the branch unpublished, and hand off the three continuations required by `/omc:implement`.
