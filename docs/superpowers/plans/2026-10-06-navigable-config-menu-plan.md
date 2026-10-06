# Navigable configure menu implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace the configure questionnaire with an immediately saved, schema-driven nested terminal menu.

**Architecture:** Schema fields own labels/help. A shared application function preserves the existing scripted validation/probe/write pipeline; menu callbacks apply one edit against the last persisted state. Bare mininterface renders nested dictionaries of Tags, imported only on the interactive configure path.

**Tech Stack:** Python 3.12+, dataclasses, PyYAML, mininterface 1.4.x, pytest, PTY.

**Spec:** `docs/superpowers/specs/2026-10-06-navigable-config-menu-design.md`

## Global Constraints

- Only `label` and `help` schema metadata; preserve schema defaults, types, file formats and import cost.
- Every edit saves immediately; blank keeps current; unchanged/repeated validation never probes or writes.
- `--set` and `--defaults` preserve existing behavior and tests. Secrets first, only in secrets.yaml, mode 0600, never echoed.
- Runtime subprocess/env/network stays behind ToolContext. No questionary, prompt-toolkit, or mininterface extras in the final dependency graph.
- Menu constructed explicitly with get_interface("text", settings=...), never run(); non-TTY remains Refusal rc 2.
- Esc returns one level/root exits; Ctrl-C abandons edit or exits menu; post-steps once; first-run exit seeds missing global/project files.
- Follow test-first red/green policy; no skips. Keep this branch committed and unpublished.

## Review Focus

- Rejected edit followed by valid edit: persisted state and probe baseline must not retain rejected values (Task 2).
- Provider toggled off then edited: entry recreated, configured tag refreshed without rebuilding menu (Task 2).
- Existing model outside known choices and empty provider sets: render without crashes or accidental changes (Task 2).
- Legacy project values during a secrets-only or first-run session: seed/migrate without losing ownership (Tasks 1, 2).
- Terminal cancellation/EOF and redraws: no traceback, busy loop, or lost already-saved edit (Task 3).

## Files and responsibilities

- `src/omc/config/schema.py`: human metadata alongside existing defaults.
- `src/omc/config/store.py`: provider removal alongside set_key.
- `src/omc/configure.py`: extracted save pipeline, composition, callbacks, session lifecycle.
- `tests/unit/test_config_store.py`, `tests/unit/test_configure.py`: storage/scripted regression coverage.
- `tests/unit/test_configure_menu.py`: renderer-independent composition/hook/lifecycle tests.
- `tests/unit/test_configure_pty.py`: real terminal behavior and disk artifacts.
- `pyproject.toml`, `uv.lock`: dependency replacement.
- `README.md`, `tests/e2e/test_e2e_dependency.py`: user guidance and live dependency fixture.

## Graph pressure test

1. `run_configure` context and `set_key` query confirm routing/probes live in configure while validation and persistence live in store. Extract the existing sequence; retain multi-pair semantics instead of implementing menu-only persistence.
2. `set_key` context and schema definitions confirm provider creation via setdefault and registry validation. Removal belongs beside set_key; the synthetic configured toggle is composition state, not a persisted schema field.
3. Interactive/instructions/dependency query confirms only configure owns walkthroughs; post-steps must remain outside individual edits. The dependency E2E refers to questionary and must move with the manifest. Generated graph documentation remains managed through primary-checkout omc:document, not hand-edited here.

### Task 1: Share configuration application and add schema metadata

Model: heavy coding tier

**Files:** Modify `src/omc/configure.py`, `src/omc/config/schema.py`, `src/omc/config/store.py`, `tests/unit/test_configure.py`, `tests/unit/test_config_store.py`.

**Interfaces:**
- Consumes existing store validators/savers and `_probe_docs`.
- Produces `_apply_settings(ctx: ToolContext, root: Path | None, gcfg: GlobalConfig, pcfg: ProjectConfig, scfg: SecretsConfig, sets: list[str], *, defaults: bool = False, legacy: bool = False, remove_provider: str | None = None) -> tuple[bool, bool, bool]` returning global/project/secrets write flags. Mutates supplied candidate configs as existing code does; callers needing rollback supply copies. Does not run post-steps.
- Produces `store.remove_provider(cfg: GlobalConfig, name: str) -> None`, registry-validates name and removes an existing entry idempotently.

- [x] Write new tests first: removal persists absence, removing default/docs provider is allowed under existing consistency validation, invalid provider refused; schema metadata preserves asdict/defaults and uses only label/help; direct application routes project/global/secrets flags and leaves post-steps to caller.
- [x] Run focused new tests and record expected failures; existing scripted tests are unchanged.
- [x] Extract the scripted pipeline as a pure move to `_apply_settings`; preserve messages, defaults precedence, probe ordering, migration carry, and secret-first writes. Removal uses the same validation/probe/write path. Add labels/help to all displayed schema fields/sections and API-key metadata on SecretsConfig.api_keys; configured is a synthetic bool, never a schema field.
- [x] Run `uv run pytest tests/unit/test_configure.py tests/unit/test_config_store.py -q`, then `just check`; all pass.
- [x] Self-review and commit implementation/tests together.

### Task 2: Compose and run the immediately saved menu

Model: heavy coding tier

**Files:** Modify `src/omc/configure.py`, `pyproject.toml`, `uv.lock`, `tests/unit/test_configure.py`; create `tests/unit/test_configure_menu.py`.

**Interfaces:**
- Consumes Task 1 `_apply_settings` and metadata/removal operation.
- Produces `_run_menu(ctx: ToolContext, root: Path | None, gcfg: GlobalConfig, pcfg: ProjectConfig, scfg: SecretsConfig, *, legacy: bool = False, plain_menu: bool = False) -> tuple[bool, bool, bool]` for interactive entry and PTY tests; updates supplied configs to accepted state and returns accumulated write flags.
- Produces `_compose_menu(ctx, root, gcfg, pcfg, scfg, *, legacy=False)` with a root dictionary and shared edit/session state (choose a small explicit return type within configure.py and document it in report).

- [x] Write failing composition/hook/session tests before implementation. Assert roots LLM/Documentation/Notifications and Worktree (project) only in git; schema field labels/help; registry provider submenus; configured toggles; api key only for API-capable providers; configured-provider choices and cli/api capability choices; known models plus an Other text prompt.
- [x] Test repeated/blank callbacks have zero validator/probe/write calls; edits route to exactly their owning file except existing resolved-model/legacy carry behavior; secrets 0600/masked; failures preserve bytes/state and return messages; resolved model stored/displayed; repeated callback after resolution no-ops. Test disable/edit/re-enable, empty provider sets, custom stored models, and rejected-then-valid sequences.
- [x] Add `mininterface>=1.4,<1.5` bare and remove questionary; lock/sync. Inspect actual library callback and display contracts rather than assuming Tag semantics. Implement composed dataclass traversal plus the small synthetic/provider routing adaptations; options computed at build time, stable root, configured tag synced after implicit configuration. Use transactional copies per edit and advance baseline only after success.
- [x] Replace walkthroughs with `_run_menu`; catch library Cancelled/InterfaceNotAvailable and expected omc errors at the right boundaries; blank/current no-op; masked key display through docsllm.mask_key; API-without-key menu error tells user to set key first. Model Other uses the same library for text input.
- [x] Test first-run defaults, legacy carry, no-global-write migration rules, post-steps once for normal/cancel exit, non-TTY unchanged, explicit interface despite environment/argv. Adapt existing interactive instructions test to patch `_run_menu`, retaining artifact assertions. Remove both walkthrough functions.
- [x] Run focused tests then `just check`; self-review and commit.

### Task 3: Exercise the real terminal and update user/dependency guidance

Model: heavy coding tier

**Files:** Create `tests/unit/test_configure_pty.py`; modify `README.md`, `tests/e2e/test_e2e_dependency.py`; amend `src/omc/configure.py` only for demonstrated terminal defects.

**Interfaces:** Consumes `_run_menu(..., plain_menu=True)` with real mininterface, temporary home/project, and stubbed unrelated post-steps where needed.

- [x] Write PTY tests and run them before any required fix: wait for each prompt before sending a line; navigate Notifications, change backend to file:///tmp/omc-menu.log, leave, assert persisted backend and preserved unrelated settings. Missing PTY fails with prerequisite guidance, never skips; child shutdown/timeout is bounded and cleaned up.
- [x] Cover Esc one-level/root exits and Ctrl-C saved-edits survival. Observe EOF and actual simple-term-menu redraw path in a targeted PTY test/probe; avoid treating terminal loss as a successful save or allowing a loop. Record observed behavior; fix demonstrated defects with reproducing tests first.
- [x] Replace questionary dependency fixture with mininterface, repository github.com/CZ-NIC/mininterface, and a question/rubric about real nested text forms and Tag validation. Keep artifact assertions for manifest/index; do not run costly LLM E2E merely for wording.
- [x] Update README configure description with Enter edit, Esc back/leave, immediate saves and existing scripted equivalents.
- [x] Run PTY tests and `just check`; then `just build` at the milestone. Self-review and commit. Controller performs task and whole-branch reviews; publish/audit is a later user invocation.


## Implementation completion — 2026-10-06

Implemented and reviewed through `7fda1fb`; branch remains unpublished.
All three task reviews and the whole-branch review findings are closed.

Final validation:

- `just check`: 1,250 passed; seven pre-existing macOS forkpty deprecation warnings.
- `just build`: Ruff formatting/lint and source distribution/wheel passed.
- `just e2e-tests` on `7fda1fb`, source image `f8b20975a7be`: four lifecycle tests passed (one expensive case deselected), then 57 other E2E tests passed. An earlier rerun had one setup error because a lifecycle snapshot was missing; subsequent inspection also found its base image absent. Exact removal cause was not established. The complete final-commit rerun passed without weakening tests or changing the E2E harness.
- Real terminal tests exercise plain/default renderers, immediate saves, navigation, cancellation, UTF-8 input, terminal loss, and masked secret entry.

Implementation rulings and tradeoffs:

1. Editing an API key for an unconfigured provider enables its entry, as required for provider leaves; this may write both global and secrets files. If that interpretation is wrong, it creates an unwanted empty provider entry. Existing-provider key edits remain secrets-only and scripted behavior is unchanged.
2. A local selection-tag bridge invokes validation that mininterface 1.4.0 bypasses, including its double-callback behavior. This preserves one prompt/probe/write per edit; dependency upgrades may require adapter maintenance.
3. A narrow getpass adapter masks secret input because mininterface's text SecretTag input echoes. It retains library menu rendering but adds dependency-integration maintenance.
4. A scoped unbuffered stdin proxy fixes a reproduced complete-line input stall. UTF-8, terminal loss, and stdin restoration are tested; a wrong adaptation could affect terminal compatibility. Default-renderer EIO cleanup failure is translated narrowly rather than swallowing arbitrary assertions.
5. Provider/backend choices remain fixed at menu construction, as specified. README documents reopening after enabling/changing a provider; the cost is an additional configure invocation for dependent choices.
6. An additional focused fix/re-review cycle closed a new replay regression found by final review. The cost was additional validation time, avoiding a known silently discarded model edit.

Next lifecycle phase is audit/publication, requiring its own direct user invocation. The worktree is retained for review or further requested changes.

### Rebase integration — native notifications

Main c8d263e replaced the global notifications section with a per-provider
native-notifications toggle. Preserve that schema and expose its field through
the menu metadata. Preserve main's unknown-key migration behavior. The real
terminal regressions now edit the project base branch instead of the retired
notification backend. Metadata and native-toggle regressions failed before
the merge adaptation, then all 172 configuration/terminal tests passed.
