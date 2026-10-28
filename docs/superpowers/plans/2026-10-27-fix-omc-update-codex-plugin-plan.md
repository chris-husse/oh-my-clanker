# Codex Plugin Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `omc update` bind Codex's omc marketplace to the CLI installation source and prove the installed plugin contains that source's current skills.

**Architecture:** `installer.run_update` passes its receipt-derived source to a focused Codex update operation in `plugin.py`. That operation reconciles registration, refreshes installation through supported Codex commands, and verifies installed artifacts; Codex argv construction and comments about CLI behavior stay in `providers/codex.py`. Keep the existing Claude path and best-effort provider failure policy.

**Tech Stack:** Python, pytest, ToolContext, the real Codex CLI, existing Docker E2E harness, uv receipts, Git.

**Spec:** `docs/superpowers/specs/2026-10-27-fix-omc-update-codex-plugin-design.md` (committed `bb1c107`). Read it fully before executing.

## Global Constraints

- Use `marketplace_source(ctx.env)`, backed by uv's installation receipt, as the source of truth.
- Preserve the existing source mapping: a directory/editable installation stays local; a supported GitHub installation maps to its repository; unknown or unsupported sources use the canonical repository fallback.
- Use Codex's supported CLI to mutate marketplace and plugin state. Do not edit its registry or cache to make a test or update succeed.
- All external commands use `ToolContext`; no new runtime subprocess/network boundary.
- Success requires registration, installed content, and verification, in that order. Every required mutation must succeed; the generic loop's benign-intermediate-error rule does not apply.
- A failed initial list is not proof that the plugin is absent. Repair the known omc marketplace when that registration prevents listing.
- Confine replacement to the omc marketplace and plugin; preserve unrelated registrations/plugins.
- Successful commands followed by absent, stale, malformed, or unverifiable installed state are a Codex plugin update failure.
- Preserve provider isolation and the overall exit policy; no Codex success line after verification fails.
- Codex startup/configure self-healing, superpowers management, source-policy extensions, and host installation changes are out of scope.
- Write and run each reproducing test before its fix. Selected tests run or fail loudly with actionable prerequisites; never skip.
- Never run `omc install` or `uv tool install` against the host. Test homes, receipts, configuration, Git fixtures, and caches are disposable.
- The controller runs `/omc:check` after each reviewed task; final milestone is `/omc:build` then `/omc:verify`, consuming their `OMC_STAGE` verdicts. Workers use `just check` during implementation and targeted E2E for the changed integration.

## Review Focus

- Invalid or unavailable desired sources: report the desired source and operation, never silently retain another source while claiming success (Task 1).
- Duplicate names and unrelated plugin entries: verify the canonical `omc@oh-my-clanker`, and preserve unrelated installations even during repair (Task 1).
- Unchanged versions with changed, added, or removed skill files: compare installed content sufficiently to catch all three, including skill support files (Task 2).
- JSON success with malformed metadata, missing payloads, disabled plugins, or payload paths naming marketplace source files: fail verification (Task 2).
- A broken initial list and a later command failure: distinguish repairable registration failure from failed repair, retain useful Codex detail, and continue other providers (Tasks 1 and 2).

---

## File Map and Sequential Checkpoints

| File | Responsibility |
| --- | --- |
| `src/omc/installer.py` | Route configured Codex updates to the focused operation; success/failure narration and isolation. |
| `src/omc/plugin.py` | New `update_codex_plugin(ctx: ToolContext, source: str) -> str` orchestration and focused private verification helpers. Existing source resolver and Claude entry point keep their contracts. |
| `src/omc/providers/codex.py` | Pure Codex command builders; empirical comments beside commands that rely on observed semantics. |
| `tests/unit/test_codex_plugin.py` (new) | Stateful command-result fixtures and artifact fixtures for Codex reconciliation/verification failures. |
| `tests/unit/test_installer.py` | Receipt-driven dispatch, source mapping, provider isolation, and honest success reporting. |
| `tests/unit/test_providers.py` | Exact command arguments; replace the test that codifies ignoring the source. |
| `tests/e2e/test_e2e_codex_plugin_update.py` (new) | Disposable-home real CLI transition tests through `installer.run_update`. |
| `docker/PLUGIN-NOTES.md` | Exact tested Codex version, command/JSON semantics, observed red/green results and limitations. |

Two fresh implementation workers run sequentially. Task 1 delivers a working local-source update and the shared fixture; Task 2 strengthens Git and failure coverage without changing the source policy. Each task commits its tests and implementation together after its own tests pass. The initial E2E reproducer belongs to Task 1, so no intermediate commit intentionally leaves that test red.

### Task 1: Reconcile and Verify Codex Updates from the Installation Receipt

Complexity: high

**Files:** All files in the map above; Task 1 establishes the new E2E/unit modules. Read `tests/e2e/test_e2e_marketplace_repair.py`, `tests/e2e/codex_plugin_payload.py`, `tests/e2e/conftest.py`, `tests/e2e/parallel.py`, and `src/omc/toolctx.py` as references.

**Interfaces:**

- Consumes `marketplace_source(env) -> str`, `installer.run_update(ctx) -> int`, `ToolContext.from_env`, `ToolContext.run`, and E2E `harness.run_in`.
- Produces `plugin.update_codex_plugin(ctx: ToolContext, source: str) -> str`; returns a short verified status, raises `OmcError` on every unresolved postcondition.
- Produces pure methods on `CodexProvider` for the commands actually needed: `plugin_marketplace_list_argv() -> list[str]`, `plugin_marketplace_add_argv(source: str) -> list[str]`, `plugin_marketplace_remove_argv() -> list[str]`, `plugin_marketplace_upgrade_argv() -> list[str]`, `plugin_list_argv() -> list[str]`, `plugin_add_argv() -> list[str]`, and, if the experiment proves replacement needs it, `plugin_remove_argv() -> list[str]`. These target `oh-my-clanker` and `omc@oh-my-clanker`; do not add a cross-provider management API.
- Produces E2E helper `_update(container, source: str) -> tuple[int, str]`, which invokes the checkout implementation's `run_update` with a private uv receipt and configuration. Task 2 reuses it.

- [ ] **Step 1: Write the real CLI local-source reproducer.** Create valid omc marketplace/plugin fixtures A and B, with equal versions and distinguishable `skills/start/SKILL.md` bytes. Install A through real Codex commands in a disposable home, set the private uv receipt to B, and invoke `run_update`. Name the test `test_update_rebinds_receipt_source_and_installed_content`. Assert registration resolves to B, the canonical entry is installed/enabled, and the installed cache artifact contains B's bytes. Assert the same after a second update and assert one canonical registration/installation rather than accumulating copies.

  Use the existing generic container fixture and `pytest.mark.e2e`; this test invokes plugin management only and needs no account/model session. Do not select the provider marker that mounts the shared OAuth volume. Set `HOME`, `CODEX_HOME`, `OMC_HOME`, and `UV_TOOL_DIR` inside the fixture's disposable directory. Write the omc config directly through the existing config API. Stub only unrelated `run_update` boundaries such as uv upgrade, GitNexus update, host integration, and tool readiness; leave source receipt reading, Codex commands, production reconciliation, and verification real. Do not monkeypatch `marketplace_source` or the new update operation. Test command setup failures must fail assertions with their output.

- [ ] **Step 2: Run the reproducer and capture the expected red.** Run `just e2e-tests tests/e2e/test_e2e_codex_plugin_update.py -k rebinds`. The expected failure is the old source/installed bytes surviving the update, not a missing CLI, broken fixture, or absent Docker. Record that evidence in the task handoff; fix fixture prerequisites before treating any failure as the reproducer.

- [ ] **Step 3: Establish supported CLI semantics in the same isolated fixture before choosing replacement commands.** Record `codex --version` and inspect help for marketplace add/list/remove/upgrade and plugin add/list/remove. Host planning inspection found Codex 0.158.0 supports add/remove with `--json`; inspect the actual E2E binary too. Probe repeating `plugin add` after same-version source bytes change; if it retains the cache, probe supported remove/add replacement. Probe a marketplace whose manifest is deleted after registration, confirming both listing failure and a supported repair sequence. Capture JSON keys identifying registration source/root, canonical installed entry, enabled state, and the add result's installed path. No registry editing. If supported commands cannot refresh same-version installed bytes or repair the stale registration, report a genuine blocker to the controller before product implementation.

- [ ] **Step 4: Write focused red unit tests before adding the operation.** In `test_codex_plugin.py`, use the observed JSON schemas and real temporary manifest/skill files; do not fabricate a convenient schema. Add tests for matching source, absent registration, A-to-B source change, initial list failure followed by repair, repeat update, desired-source failure, and failed remove/add/install commands. Assert exact argv ordering, no later installation after required command failure, preserved unrelated entries, and canonical plugin selection. In `test_installer.py`, add a receipt-driven test that the Codex operation receives the local desired source and that a verification failure emits no Codex success line while later providers still run. Run `uv run pytest tests/unit/test_codex_plugin.py tests/unit/test_installer.py tests/unit/test_providers.py -q` and confirm the new behavior assertions fail.

- [ ] **Step 5: Implement the focused operation and provider command builders.** Implement `update_codex_plugin(ctx, source)` in `src/omc/plugin.py`. Keep parsing/verification helpers Codex-specific, using plain dictionaries/Paths unless a type actually improves the code. Validate the desired source offers the expected marketplace/plugin before replacing an old registration; a disposable Codex home is available if supported commands are needed to establish that. Reconcile a missing/different registration, or repair the known omc registration after a failed initial listing; then re-probe and require the intended source. Use only the command sequence established in Step 3. For Git sources refresh the chosen marketplace snapshot using the supported targeted command when available. Refresh/reinstall the canonical plugin even when its version is unchanged. Check every required command's return code and preserve useful stderr/stdout and source context in `OmcError`.

  Verify the actual installed artifact identified by Codex's install result and canonical installed listing: installed/enabled state, manifest identity/version consistency, readable payload root in the selected Codex home's installed cache, and installed skill bytes matching the desired source's offered payload. For remote sources the refreshed, verified marketplace root supplies the offered payload. Compare relevant skill content, not only version strings or one available marketplace entry; do not import E2E validators into production. No direct registry/cache mutation. Put observed CLI quirks and version evidence beside the provider argv methods.

- [ ] **Step 6: Wire `installer.run_update` to the verified result.** Add a Codex branch beside the existing Claude branch using the already resolved source. Catch `OmcError`, report failure, and continue; print Codex success only after the operation returns. Keep the generic fallback for other providers and keep the current overall return policy. Do not extend `ensure_plugin` to Codex because it is called by start/configure and those flows are out of scope. Replace obsolete tests/comments that claim Codex cannot add marketplaces or that bare marketplace upgrade proves plugin update. Ensure the old pure-update method no longer suggests an unverified sequence is sufficient.

- [ ] **Step 7: Add and run the stale-registration and source-advancement E2Es.** `test_update_repairs_unlistable_marketplace` removes the old fixture's supported manifest after installation, first asserts real listing fails, then verifies update restores listing, desired registration, and installed skill bytes. `test_update_refreshes_same_local_source` changes installed source content under its unchanged version, invokes update, and checks installed bytes advance. Keep an unrelated marketplace/plugin sentinel in at least one transition and assert it survives. Run the whole new E2E module; all three scenarios must pass against the real CLI.

- [ ] **Step 8: Verify, document, and commit Task 1.** Run targeted unit tests and `just check`; record the real CLI version, observed add/remove/list semantics, JSON fields, and initial red/final green in `docker/PLUGIN-NOTES.md`. Correct the historical claim that snapshot refresh alone proves installed refresh while preserving it as dated history. Commit the task's product/tests/notes together. Return the exact supported sequence, fixture interface, test results, and remaining Task 2 obligations. The controller reviews, then runs `/omc:check` before Task 2.

### Task 2: Prove Git Snapshot Advancement and Fail Closed on Unverifiable Content

Complexity: high

**Files:** Modify `tests/e2e/test_e2e_codex_plugin_update.py`, `tests/unit/test_codex_plugin.py`, `tests/unit/test_installer.py`, `tests/unit/test_providers.py`, and `docker/PLUGIN-NOTES.md`; modify the Task 1 production files only for failures these tests expose.

**Interfaces:** Consumes `update_codex_plugin(ctx, source)` and `_update(container, source)` from Task 1. Produces no new public API. Retains the source resolver policy.

- [ ] **Step 1: Write and run the Git installed-content advancement test before any Git fixes.** Add `test_update_refreshes_git_snapshot_and_installed_content`: serve a disposable bare Git marketplace through the existing notes' local HTTP pattern (foreground test-owned server, cleanup in teardown). Use a supported GitHub receipt and test-only Git URL rewriting in the disposable home to route its clone/fetch to that controlled origin; do not add production support for a new receipt/source form. Install version/content A through Codex, then commit/push B with unchanged version, including a changed skill and an added/removed skill file. Invoke `_update`; assert registration still names the receipt-selected repository, snapshot bytes advance, and canonical installed bytes and file set advance too. Repeat update and check there is one canonical installation. Assert source A existed in the installed cache before advancing it so setup cannot accidentally prove only a fresh install. Run `just e2e-tests tests/e2e/test_e2e_codex_plugin_update.py -k git_snapshot`. A failure must identify a real stale snapshot/payload; if Task 1 already satisfies it, record the pass and make no speculative production change.

- [ ] **Step 2: Add adversarial verification and source-policy unit tests.** Parameterize tests over missing canonical entry, `enabled=false`, wrong plugin id, malformed JSON/shape, missing/unreadable installed path, installed path naming marketplace source, absent/malformed installed manifest, matching version with differing skill/support bytes, and stale added/removed files. Assert each raises `OmcError`; test the installer suppresses success and preserves provider isolation. Add receipt fixtures for editable/directory, supported GitHub, unknown/PyPI, and unsupported source with the exact existing fallback `chris-husse/oh-my-clanker`. Assert the real resolver feeds the focused operation; do not replace the resolver with a stub. Test source paths containing spaces and shell metacharacters remain one argv item. Run the focused unit files, recording any red before fixing it.

- [ ] **Step 3: Fix only the failing cases and rerun the focused tests.** Update source/root parsing, the supported refresh sequence, or artifact checks as needed. Fail closed on unreadable or unverifiable offered/installed payloads, and include the failed operation/source in diagnostics. Prefer a deterministic relative-path-to-bytes comparison of the offered and installed skills trees, including support files, to version-only checks or a hand-maintained list of named skills. Resolve filesystem errors to `OmcError` so provider isolation holds. Preserve unrelated registrations/plugins and do not treat broken initial listings as absence.

- [ ] **Step 4: Run complete changed-integration verification and update empirical notes.** Run `uv run pytest tests/unit/test_codex_plugin.py tests/unit/test_installer.py tests/unit/test_providers.py -q`, `just check`, and `just e2e-tests tests/e2e/test_e2e_codex_plugin_update.py`. Record exact test counts, actual CLI version, both local and Git unchanged-version advancement, and recovery/failure observations in `docker/PLUGIN-NOTES.md`. No skip/xfail is an acceptance result. Where no test needed a product fix, document that existing Task 1 behavior passed it.

- [ ] **Step 5: Commit and hand off for milestone gates.** Commit the task's tests, required fixes, and notes together. Return red/green evidence, artifact assertions, and any genuine blocker. After reviews, the controller runs `/omc:check` then `/omc:build` and announces/runs `/omc:verify`; the Codex integration gate is required for this provider change under the project's verify instructions. The controller owns final plan/ledger commits and clean-branch handoff. Do not publish.

## Implementation Pressure Test and Self-Review

The `/omc:explain` procedure was used for each major task with project/primary context maps, GitNexus symbol queries, current source checks, and optional-source discovery. The knowledge snapshot reports `wiki-behind` by 24 commits; its repair instruction is `omc watch --once --enable-documentation` in the primary checkout. Graph results are navigation hints here; the claims below were checked against current files. No indexing or docs mirroring was performed.

- **Task 1 question:** Should source reconciliation extend `ensure_plugin`, return a longer pure argv list, or use a focused update operation? Current `src/omc/installer.py:run_update` confirms that the generic loop ignores nonfinal command errors. Current `src/omc/plugin.py:ensure_plugin` serves startup/configure and deliberately leaves non-Claude providers unverified. The plan therefore uses a focused update-only function and checked commands; it does not add an enum/state-machine framework or alter startup behavior. `plugin.py:_prepare_marketplace` demonstrates isolated replacement validation and source checking, but its Claude JSON shapes and command semantics must not be reused for Codex.
- **Task 2 question:** Can existing version metadata or E2E validators establish installed freshness, and how can Git be tested without broadening source policy? Current `tests/e2e/codex_plugin_payload.py:validate_payloads` shows canonical installed/enabled checks, `installedPath` from add output, cache containment, manifest checks, and skill byte comparisons. It explicitly rejects source-only proof; production needs a small focused equivalent without the validator's superpowers and fixed `/repo` assumptions. `docker/PLUGIN-NOTES.md` supplies the local HTTP Git fixture pattern but proves snapshot refresh only. A disposable Git URL rewrite keeps the production receipt mapping unchanged while allowing controlled Git commits.

Codex itself is not an indexed external dependency; `/omc:explain-dependency codex <question>` is the available route for future source-level dependency investigation. This plan instead requires supported real-CLI behavior experiments, which are the binding spec's evidence gate. Host help inspection established 0.158.0 command availability only; it did not establish replacement semantics.

Source spectrum: unavailable — repository origin is not on the Kraken GitLab; Spectrum indexes only Kraken repositories.

Self-review: both tasks have explicit interfaces and complexity labels; all design acceptance scenarios map to their named tests. The five review-focus conditions are covered above. No unresolved design decision requires a user answer before starting Task 1; unsupported CLI behavior discovered there is a genuine blocker, not permission to edit registry files.
