# Task-type Model Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Configure seven task models per provider and use those choices for sessions and lifecycle agents.

**Architecture:** Blank schema leaves resolve through provider defaults. A focused `taskmodels.py` owns family resolution and configure probes; pure adapters build argv. The internal verdict supplies resolved choices to skills, while the main session remains the orchestrator.

**Tech Stack:** Python dataclasses, YAML, pytest, mininterface, provider CLIs, Markdown skills.

**Spec:** `docs/superpowers/specs/2026-10-06-task-type-model-selector-design.md`

## Global Constraints

- Config stores `family[:effort]`; blank at rest, resolved on read. Nothing resolved is persisted.
- Defaults: Claude orchestrator/medium `opus`, design/plan/review/high `fable`, simple `sonnet`; Codex orchestrator/medium `sol:high`, design/plan/review/high `astra`, simple `sol:medium`.
- Claude efforts: `low, medium, high, xhigh, max`; Codex efforts: `low, medium, high, xhigh, max, ultra`.
- `ToolContext` is the only subprocess/env/network boundary. Provider adapters remain pure argv builders. Config loading never reads provider files.
- The cheap/fast tier (Haiku-class or its equivalent) is never used. Documentation model behavior stays unchanged.
- Every changed behavior has observed failing validation before implementation, followed by passing validation. No test skips; restricted-PATH stubs use builtins or absolute paths.
- Implementation remains committed and unpublished. Do not install omc, merge, push, or hand-copy knowledge directories.

## Review Focus

- Invalid nested config must fail without creating a partial provider entry (Task 1).
- Model lists containing hidden, older, malformed, or similarly named models must not select the wrong family (Task 2).
- A failed or unchanged menu edit must not persist a candidate or repeat its probe (Task 3).
- Explicit provider overrides and hand-started sessions must choose the correct provider without requiring project config (Task 2).
- Skill workers must respect lifecycle authority and surface critical questions through the orchestrator (Task 4); E2E fixtures must avoid accidentally selecting costly defaults (Task 5).

## Pressure-test findings

`omc:explain` was applied to config/menu, probe/resolution, launch, skill dispatch, and E2E sections. Graph queries found `_compose_menu → _apply_settings → _probe_docs` and `cli_model_probe → Provider.headless_argv → ToolContext.run_bounded`; focused source reads confirmed the integration seams. Query results report partial FTS/schema coverage (missing Protocol/Category and route properties); absence of graph matches is not evidence of absence. No index mutation is needed for implementation.

- Reuse nested dataclass hydration, and validate provider-specific values from `validate_llm`; do not add a provider dependency to schema.
- Preserve `_apply_settings`'s probe-before-save boundary and menu candidate-copy rollback.
- Extend `cli_model_probe` with optional prompt/effort/result handling that preserves its current callers and default result; do not duplicate process management.
- Resolve once at each session/slug/watch call; keep `session_plan` subprocess-free.
- Add the internal models verb beside design-record; it needs global config only, so avoid unnecessary project resolution.
- Update lifecycle dispatch prose and its contract tests together; do not change historical design records.

### Task 1: Typed task choices and pure provider contracts

**Model:** heavy coding tier

**Files:** `src/omc/config/schema.py`, `src/omc/config/store.py`, `src/omc/providers/{base,claude,codex}.py`, `src/omc/configure.py`; tests `test_config_store.py`, `test_providers.py`, `test_configure_menu.py`.

**Interfaces:** Produce `TaskModelsConfig` with string fields `design, plan, review, simple, medium, high`, all blank, nested as `ProviderConfig.tasks`. Add `Provider.families() -> list[str]`, `effort_levels() -> list[str]`, `default_task_model(task: str) -> str`, and `CodexProvider.model_list_path(env) -> Path`. All argv builders accept optional keyword `effort: str = ""` (including streaming). Store exports `validate_task_model(provider: str, value: object) -> str` for static validation only.

- [x] Write config tests for old files without tasks, blank round trips, all six nested setters, invalid task paths/types, valid family/effort and full IDs, invalid families/efforts, and mutation-free rejection. Assert defaults remain blank in serialized YAML.
- [x] Write provider tests asserting the exact defaults in Global Constraints, family lists (`fable, opus, sonnet`; `astra, sol`), effort lists, `CODEX_HOME` path behavior, and exact argv suffixes `--effort high` / `-c model_reasoning_effort=high` with blank effort adding nothing.
- [x] Run focused tests and record expected failures before implementation.
- [x] Implement metadata (`Orchestrator model`, `Task models`, task labels), schema-derived leaf/type handling in the store, and static validation on load/set/save. Treat provider-shaped full IDs as escape hatches while rejecting malformed tokens and banned cheap families. Keep docs_model's existing rules.
- [x] Generalize the menu's schema traversal enough to represent nested tasks without breaking existing menu generation; Task 3 supplies the final task pickers and probes. Update old label/schema expectations.
- [x] Run `uv run pytest tests/unit/test_config_store.py tests/unit/test_providers.py tests/unit/test_configure_menu.py -q`, then `just check`; expected exit 0. Commit tests and code as `feat: add typed provider task model choices`.

### Task 2: Resolve models and route CLI launches

**Model:** heavy coding tier

**Files:** Create `src/omc/taskmodels.py`, `tests/unit/test_taskmodels.py`; modify `src/omc/{session,slug,watch,internal,toolctx}.py` as needed; tests `test_session.py`, `test_start.py`, `test_implement.py`, `test_review.py`, `test_slug.py`, `test_watch.py`, `test_internal.py`, and affected fixture helpers.

**Interfaces:** Consume Task 1 provider contracts. Produce immutable `ModelChoice(model_arg: str, effort: str)`, `resolve_choice(ctx, provider: str, value: str, *, validate_effort: bool = False) -> ModelChoice`, `task_choice(ctx, cfg, task: str, *, provider: str | None = None) -> ModelChoice`, `orchestrator(ctx, cfg) -> ModelChoice`, and `model_options(ctx, provider: str, task: str) -> dict[str, str]` for Task 3. `omc internal models` exposes all seven tasks with `model` and `effort` keys.

- [x] Write fixture-list resolution tests: lowest priority visible exact family word wins case-insensitively; hidden models ignored; full ID bypasses absent cache; malformed JSON/fields/types reports ConfigError naming the file; absent cache says `run omc configure`; unavailable family names available choices; unsupported effort is checked only when requested.
- [x] Write verdict tests for seven keys, Claude alias/Codex slug output, `OMC_PROVIDER` precedence, default fallback, unknown provider, missing global config/cache: one `OMC_MODELS` JSON line, exit 0 on success or 2 with `ok:false,message` on refusal.
- [x] Write launch tests covering both providers' model/effort in interactive/headless design, implement, review, slug, and watch auto-build; `OMC_PROVIDER` must accompany `OMC_SLUG` in session and headless environments. Add fixture caches or explicit full IDs to existing Codex launch tests rather than weakening production resolution.
- [x] Run the focused new tests; record failures before implementation.
- [x] Implement resolution using only `slug`, `display_name`, `visibility`, `priority`, `supported_reasoning_levels`; use a ToolContext file-read helper if needed. Static loading stays I/O-free; dispatch does not reject model-specific effort beyond static validation. Build picker options with provider default first and Other last; Codex uses supported live levels or family-only if missing; Claude task leaves use families only, orchestrator includes efforts.
- [x] Replace four direct model reads with `orchestrator`; extend session env and internal verb. Keep docs callers' argv unchanged. Run focused tests and `just check`, then commit as `feat: resolve task models and route orchestrator launches`.

### Task 3: Task model pickers and validate-before-save probes

**Model:** heavy coding tier

**Files:** `src/omc/configure.py`, `src/omc/taskmodels.py`, `src/omc/docsllm.py`; tests `test_taskmodels.py`, `test_configure.py`, `test_configure_menu.py`, `test_configure_pty.py`, `test_docsllm.py` as needed.

**Interfaces:** Consume Task 2 `model_options`, `resolve_choice`, `task_choice`. Produce `taskmodels.validate_selection(ctx, provider: str, value: str, *, task: str, say) -> ModelChoice`; add `_probe_task_models` next to `_probe_docs`. Extend `cli_model_probe` with backward-compatible keyword options for prompt, effort and returning trimmed output.

- [x] Write probe tests for changed vs unchanged leaves (exactly one vs zero probes), Claude exact prompt `Reply with only your exact model id`, display of returned ID, Codex blank-model exec without `-m`, cache validation, failed save retaining prior file, and default-clear resolving the effective default. A restricted-PATH Codex stub writes a fixture cache.
- [x] Write menu tests for six task leaves per provider, `Provider default (<effective value>)` storing blank, Other input, nested leaf persistence, Claude task pickers without effort, orchestrator efforts for both, Codex live-list choices/family-only fallback, and failed edit rollback including renderer replay.
- [x] Run those tests and observe expected failures.
- [x] Implement `_probe_task_models` comparing each provider's edited values before/after; run before any persistence, independently of documentation probes. Wire final pickers through existing one-pair `_apply_settings`; preserve configured toggles and docs behavior. Store only user's family/effort string.
- [x] Verify the real Codex headless refresh in a temporary CODEX_HOME using existing credentials without printing or persisting them in tracked files; record whether `codex exec` writes models_cache.json. If it does not, give the spec's `open codex once to fetch its model list` diagnostic. Avoid host config edits. Record live findings as provider-site comments and in the report.
- [x] Run focused tests and `just check`; commit as `feat: configure task model pickers with provider validation`.

### Task 4: Route lifecycle agents through configured choices

**Model:** heavy coding tier

**Files:** `skills/{design,implement,audit,review,plan}/SKILL.md`, `src/omc/distribution/AGENTS.md`, `.omc/config/AGENTS.md`, `.omc/skills/explain-context/SKILL.md`, `.omc/skills/review/SKILL.md` where it dispatches reviewers, root `AGENTS.md` only if tracked, `tests/unit/test_plugin_manifests.py`, `README.md`.

**Interfaces:** Consume `OMC_MODELS`; plan tasks emitted by the updated skill carry `Complexity: simple | medium | high` (missing -> medium), implementers use that key, reviewers use review.

- [x] Write contract tests for OMC_MODELS listings, configured choices replacing old Model-tier directives, Design/Plan worker delegation, critical-question return, complexity fallback, Review choices, effort handling and no-model-pin fallback. Run and record expected failures.
- [x] Update design to delegate write/harden to a Design worker from converged text; main thread asks critical questions and commits. Update implement to delegate planning/explain to Plan, dispatch task workers by complexity and reviewers by Review, retaining phase gates and committed-unpublished handoff. Audit fixes use complexity; review/grug/project reviewers use Review; plan points to new vocabulary.
- [x] Update behavior layer to Orchestrator + verdict choices, preserve E2E tier sentence and cheap-tier prohibition. Keep lifecycle authority, task lists, checkpoint reviews and user-question handling intact.
- [x] Match the actual harness: current Codex supports explicit model/effort on fresh or partial-history forks; do not instruct a full-history model override when this harness rejects it. Capture observed acceptance of `gpt-6-sol`/`gpt-6-astra` in provider-site comments; if a preset mapping is necessary, implement only what evidence requires. Claude fresh Design worker gets seed, answers and converged document; unavailable per-agent effort is ignored as specified.
- [x] Document configuration/defaults and `omc internal models` in README, without per-task documentation subsystems. Run focused manifest tests and `just check`; commit as `feat: dispatch lifecycle agents using configured task models`.

### Task 5: Real-provider integration coverage and final validation

**Model:** heavy coding tier

**Files:** `tests/e2e` fixture/seed helpers and fast variation tests, opt-in Codex suite; related unit fixture-contract tests; provider live-verification comments if needed.

**Interfaces:** All seven golden fixture model choices pin the suite's configured full model ID, so lifecycle subagents retain existing E2E cost and do not need a cache in the container.

- [x] Write failing fixture-contract assertions that all seven model choices match the E2E model. Add a fast Claude seeded-session variation invoking `omc internal models` and asserting its artifact/verdict. Add opt-in Codex real-model-list family resolution coverage; selected missing prerequisites must fail with repair instructions, never skip.
- [x] Observe failing focused validation before updating seed configuration and integration behavior.
- [x] Implement fixture pinning for golden and other shared session configuration paths. Keep actor and judge override semantics. Resolve aliases to full IDs when the suite provides a Claude alias rather than silently storing defaults.
- [x] Run the focused unit validation and `just check`, then `just build`. Run the new real-provider E2E cases using repository runner selection, supplying existing configured authentication through the established harness. Record exact results; genuine unavailable external prerequisites are blockers, not passes.
- [x] Commit as `test: verify configured task models through real providers`.

Controller continuation: perform whole-branch review, fix findings through a worker, commit the plan, and verify clean unpublished state.
