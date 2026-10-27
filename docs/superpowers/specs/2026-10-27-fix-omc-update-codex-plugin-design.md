# Make Codex plugin updates follow omc's installation source

## Purpose and current behavior

`omc update` must reconcile Codex's `oh-my-clanker` marketplace with the installation source of the omc CLI, then refresh and verify `omc@oh-my-clanker`. A CLI installed from a local checkout must use that checkout for its plugin. An old marketplace registration must not keep the plugin attached to a different worktree.

`src/omc/installer.py:run_update` already resolves `marketplace_source(ctx.env)`. That function uses `src/omc/installsrc.py:install_source` to read uv's receipt. However, `src/omc/providers/codex.py:CodexProvider.plugin_update_argvs` discards the source and returns only `codex plugin marketplace upgrade`. The installer reports success when that command exits zero. Refreshing Git marketplace snapshots does not establish that the intended source is registered or the installed plugin has advanced.

The observed failure includes a marketplace attached to a stale worktree without a supported manifest. Codex's plugin and marketplace listings can both fail in that state. The existing empirical record in `docker/PLUGIN-NOTES.md` demonstrates a Git snapshot refresh on Codex 0.144.5, but does not assert that an already installed plugin acquires new skill files. Current Codex 0.158.0 exposes marketplace add/remove/upgrade, plugin add, and JSON plugin listing commands.

## Decisions taken during brainstorm

| Decision | Reason |
| --- | --- |
| Use `marketplace_source(ctx.env)`, backed by uv's installation receipt, as the source of truth. | Codex must follow the source from which omc itself was installed. Neither the invoking worktree nor the old Codex registration chooses the source. |
| Preserve the existing source mapping: a directory/editable installation stays local; a supported GitHub installation maps to its repository; unknown or unsupported sources use the canonical repository fallback. | The existing resolver already supplies the agreed policy. This fix applies it to Codex. |
| Repair stale registration even when listing currently fails. | The broken registration itself can prevent the initial probe. |
| Refresh an already installed plugin and verify its installed version or skill content against the reconciled source before reporting success. | A successful marketplace command alone does not prove that Codex serves the updated plugin. |
| Use Codex's supported CLI to mutate marketplace and plugin state. | Codex owns its registry and cache formats. |
| Prove the fix with the real Codex CLI in a disposable home. | The missing proof is the installed artifact changing, which an argv stub cannot establish. |

## Update behavior and ownership

The existing update flow resolves the desired source after the uv upgrade and invokes a focused Codex plugin update operation. Keep orchestration in the installer, plugin reconciliation and verification in the existing plugin area, and Codex command details with the provider's existing responsibilities. All external commands use `ToolContext`. This needs no new generic plugin manager, configuration option, or background process.

The operation establishes three postconditions in order:

1. **Registration:** `oh-my-clanker` resolves to the desired installation source. Reconcile a missing or different registration using Codex marketplace commands. If the old registration prevents listing, repair the known omc marketplace directly through those commands and probe again. A failed initial list is not proof that the plugin is absent. Replacement is confined to the omc marketplace and plugin; unrelated registrations and plugins are preserved. The desired source must be usable; failure must identify that source rather than silently choosing another checkout.
2. **Installed content:** refresh any Git snapshot needed for the chosen source, then use Codex's supported installation or replacement operation so that an already installed `omc@oh-my-clanker` acquires the source's current plugin. Local sources must also refresh installed content. A second update with the same source and content succeeds without accumulating registrations or plugin copies.
3. **Verification:** probe the canonical installed plugin, confirm it is enabled, and establish that its installed artifact matches what the reconciled source offers. An available marketplace entry or a refreshed snapshot is insufficient. Version comparison is valid when it identifies the installed artifact and distinguishes the expected revision; when versions cannot distinguish content, compare relevant installed skill files with the chosen source. Successful commands followed by absent, stale, malformed, or unverifiable installed state are a Codex plugin update failure.

Only after all three postconditions hold may the installer print the Codex plugin success line. Failures identify the failed operation and useful Codex error detail or recovery command. Preserve the existing best-effort provider loop and overall exit policy: a Codex plugin failure is reported and other providers continue, as asserted by `tests/unit/test_installer.py:test_update_isolates_provider_failures`. Registration and installation steps in this operation cannot inherit the generic loop's rule that any non-final command failure is benign.

The implementation must establish the exact supported CLI sequence and JSON fields using the real installed Codex CLI. CLI behavior that the implementation depends on belongs in comments at the provider code site and in the empirical plugin notes. This design does not assume that repeating `plugin add` replaces an existing installation, or that a marketplace listing reports installed content. If supported commands cannot establish the postconditions, report the blocker; do not substitute registry edits or a success claim.

The scope is the Codex path of `omc update`. Existing Claude behavior, uv source resolution, and the other update stages retain their contracts. Codex startup/configure self-healing, superpowers management, and general support for additional installation source formats are separate changes.

Implementation validation also exposed an ambiguity in `skills/implement/SKILL.md`: checked task commits must precede milestone verification, while the final handoff commit waits for green gates. The resulting skill clarification is a lifecycle gate repair and does not change the Codex update source policy.

## Validation and acceptance

Follow the project's red → green policy: first write and run a reproducer that fails because Codex remains attached to the old source or retains old installed content, then implement the repair. The selected tests must run or fail with actionable prerequisites; they must not skip.

Unit coverage verifies receipt-driven local/Git/fallback source selection, the Codex update orchestration, repeat updates, failed listing followed by repair, failed registration/install commands, and verification rejecting stale or unverifiable results. Assert exact command arguments where stubs are used. Preserve the existing provider failure isolation contract and ensure no Codex plugin success line appears after verification failure.

The decisive E2E uses the real Codex CLI and a disposable `HOME`/`CODEX_HOME`, omc configuration, and uv receipt. Its fixtures supply valid marketplace/plugin manifests and distinguishable old and new skill content. It drives the omc update path and asserts artifacts:

- Install an older omc plugin from source A, set omc's receipt to source B, and update. Assert the registered marketplace now resolves to B, the canonical installed plugin exists, and its installed skill files contain B's content. Repeat the update and assert the same valid result.
- Begin with omc registered to a stale worktree that makes Codex listing fail. Update using the valid receipt source. Assert listing recovers, registration resolves to the desired source, and installed skill files match it.
- Refresh an existing installation when its chosen source advances. Cover a local source and a Git marketplace snapshot so the assertions prove installed content advancement for both source types. Include changed skill content under an unchanged version to prevent version metadata from masking a stale cache.

Keep fixtures and side effects isolated from the user's installation and Codex home. Do not reinstall or re-root the host omc to exercise this path. Use the existing E2E harness: `tests/e2e/test_e2e_marketplace_repair.py` provides the analogous Claude transition tests, and `tests/e2e/codex_plugin_payload.py` already demonstrates checking installed Codex payload paths, enabled state, manifests, and skill bytes. These are evidence and test patterns, not a reason for production code to import the test harness or add a general validation framework. Record the real Codex version and observed command semantics in `docker/PLUGIN-NOTES.md`. Run the project check gate during implementation and the build/verify milestone gates before handoff.

## Implementation review

2026-10-27 — Codex auditor:

| Severity | Kind | Record section | Location | Disposition |
| --- | --- | --- | --- | --- |
| Important | Code deviates from the record | Update behavior and ownership; Validation and acceptance | `src/omc/plugin.py:171`; `tests/unit/test_codex_plugin.py:468`; `tests/unit/test_installer.py:269` | Fixed in `c8be4ce`: invalid marketplace source paths now raise contextual `OmcError`; red-first regressions prove provider continuation and no false Codex success. |
| Minor | Record is stale | Update behavior and ownership, scope paragraph | `skills/implement/SKILL.md:80`; `skills/implement/SKILL.md:114`; `tests/unit/test_plugin_manifests.py:1288` | Documented the lifecycle gate repair above; no brainstorm decision changed. |

## Deliberate complexity

None.
