# Native Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove omc notification delivery and enable each harness's native notifications for omc sessions through a default-on provider flag.

**Architecture:** Keep provider argv/settings builders pure and keep worktree writes in notify.py. Make config-file hydration tolerate unknown keys while preserving validation, then replace the old notification contract and all callers together.

**Tech Stack:** Python dataclasses, PyYAML, pytest, questionary, Claude/Codex CLIs, Docker E2E.

**Spec:** docs/superpowers/specs/2026-10-06-remove-custom-notifications-use-native-design.md

## Global Constraints

- `llm.providers.<name>.notifications`, boolean, default true.
- `set_key` coerces exactly `true` and `false` and rejects any other value.
- An unknown key in a config file never fails loading. A missing key takes its default.
- Value validation stays strict: a wrong type, an invalid provider name or an unsupported docs backend is still a `ConfigError`.
- Unknown keys in secrets.yaml produce a count-only warning; never echo their names or values.
- Only the omc session is affected. omc never edits `~/.codex/config.toml` or `~/.claude/settings.json`.
- Nothing in the new wiring can fail `omc start` or `omc implement`.
- No compatibility shim for `omc internal notify`: the command is removed.
- No omc-side delivery of any kind, no log file, no fallback for terminals without native support.
- Do not hand-edit `.gitnexus/`, `.omc/docs/`, or the E2E wiki copy-back artifact.
- Tests run or fail; no skips. Every behavior change has observed red-before-green evidence.
- `ToolContext` remains the only runtime subprocess/env/network boundary.

## Review Focus

1. Nested unknown keys across multiple config sections: one warning per load, defaults and known values preserved (Task 1).
2. Mixed-type unknown YAML keys and secrets transposed into key position: loading succeeds without type-sorting crashes or secret disclosure (Task 1).
3. Dismissed native-notification prompt: current false or true survives `None` (Task 2).
4. Malformed/mixed Claude hook structures and foreign hooks sharing a group: warn/skip safely or remove only exact omc entries without losing user data (Task 2).
5. Default provider has no provider-config entry; repeated launch or flag toggle: default-on behavior and idempotent settings update (Task 2).

## Implementation pressure test

The omc explain calls queried config hydration and native session wiring, then inspected `_hydrate` and `wire_worktree` context. GitNexus reports docs three commits behind the index; current source was read to verify all implementation decisions below. Refresh command for the user in the primary checkout: `omc watch --once --enable-documentation`.

- Config: `_load_yaml` and `load_legacy` recurse through `store._hydrate`; `load_secrets` is separate. Aggregate unknown keys at the outer hydration boundary, not one stderr warning per recursion. Keep provider-name dictionaries validated as names, not ignored as unknown field names. The legacy `secrets` key is a known protected runtime field, so its existing refusal remains; this is not an unknown key.
- Native session contract: `session.session_plan` alone builds interactive argv; headless builders remain unchanged. `start.run_start` and `implement.run_implement` both wire settings. Change the provider contract and callers atomically, avoiding a transitional sink adapter. Keep configuration removal with this task so each committed task passes the whole unit gate.
- Merge: use existing JSON merge seam; remove exact command matches inside Notification/Stop groups, preserve foreign hooks and top-level settings, then apply the provider fragment. No generic standalone-file ownership mechanism remains.

## Task 1: Tolerant loading and provider flag

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/config/schema.py`, `src/omc/config/store.py`
- Test: `tests/unit/test_config_store.py` (and existing resolve/configure tests only if their unknown-key expectations change)

**Interfaces:**
- Consumes: existing `load_global(home)`, `load_project(root)`, `load_legacy(home)`, `load_secrets(home)`, `set_key(cfg, dotted, value)`.
- Produces: `ProviderConfig.notifications: bool = True`; typed provider-leaf load/set validation; existing loader return types unchanged, unknown field keys ignored with one warning per load.
- Transitional boundary: retain `NotificationsConfig`, old backend validation and runtime users until Task 2 removes them together. No user-facing docs advertise the transitional state.

- [x] **Step 1: Add tests before implementation.**
  - `test_provider_notifications_default_and_round_trip`: new `ProviderConfig().notifications is True`, missing provider field defaults true, explicit false survives save/load.
  - `test_set_provider_notifications_bool`: set true/false on a previously absent codex provider; value is a bool; `yes`, `1`, trailing segments, and unknown provider names raise `ConfigError`.
  - `test_provider_notifications_wrong_type`: YAML `"false"`, `1`, `null`, lists and mappings raise `ConfigError`; bool values load.
  - Replace unknown-file-key rejection tests with successful loads plus stderr warning assertions; cover global, project, legacy and nested provider fields. Multiple unknown locations yield exactly one warning line per load. Assert the known model/worktree values survive and unknown fields disappear after save.
  - Mixed integer/string/null unknown keys must not raise during warning construction.
  - Secrets unknown top-level keys warn once with count and path, preserving known API keys; a secret-shaped unknown name and value appear nowhere in captured stdout/stderr. Unsupported names within `api_keys` and invalid key values remain errors.
  - Retain existing wrong docs/worktree/provider value tests and missing-key default tests.
- [x] **Step 2: Run affected tests and record expected failures.** Run `.venv/bin/pytest tests/unit/test_config_store.py -q`; identify failures caused by missing flag/strict unknown handling, not setup errors.
- [x] **Step 3: Implement.** Add the provider bool, extend the provider branch of `set_key` with exact boolean coercion, validate the new leaf on hydration. Collect unknown field names through recursive hydration and print once at its outer boundary in house style (`· config: ignoring unknown key(s) [...] in <path>`). Secrets use `· config: ignoring 1 unknown key in <path>` (plural for multiple), then validate known values as before. Keep any private helper signature minimal; public loader signatures remain unchanged.
- [x] **Step 4: Run affected tests green**, then self-review the diff against the constraints. Record commands, failing assertions, passing counts in task report.
- [x] **Step 5: Commit test and implementation together.** Suggested message: `feat: tolerate unknown config fields and add native notification flag`.
- [x] **Controller gate:** task spec/quality review, fix loop if needed, then `/omc:check` (`just check`).

## Task 2: Replace custom delivery with native session settings

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/providers/{base,claude,codex}.py`, `src/omc/notify.py`, `src/omc/session.py`, `src/omc/start.py`, `src/omc/implement.py`, `src/omc/internal.py`
- Modify: `src/omc/config/{schema,store,resolve}.py`, `src/omc/configure.py`
- Modify: `README.md`, `src/omc/distribution/AGENTS.md`
- Test: `tests/unit/test_{providers,notify,session,start,implement,internal,config_store,configure,resolve}.py` and any existing argv/fixture tests affected by the contract
- Modify: `tests/e2e/test_e2e_start.py`
- Delete: `tests/e2e/test_e2e_notify.py`

**Interfaces:**
- Consumes: Task 1's provider bool and tolerant loading.
- Produces: `Provider.session_argv(*, session_name, model, seed, notifications: bool = True) -> list[str]`; `Provider.notification_setup(notifications: bool) -> dict[str, str]`; `wire_worktree(provider, worktree: Path, notifications: bool) -> list[str]`.
- Keeps: `merge_claude_settings(existing_text: str, ours_text: str) -> str | None` and existing launch entry points.
- Deletes: `notifies_natively`, `notify_sink_argv`, sink/delivery/payload APIs, `NotificationsConfig`, backend validation, old internal command.

- [x] **Step 1: Replace/add requirement tests.** Completed with a sequencing deviation: some launch/configure/removal tests were added after production changes; see execution evidence below. The intended red-before-code sequence was not fully met.
  - Codex exact argv for true/false and omitted flag: title override retained, `-c tui.notifications=true|false` before seed, model ordering valid, no `notify=` override. Headless argv unchanged.
  - Claude exact fragments: `.claude/settings.local.json` contains only `{"preferredNotifChannel": "auto"}` for true or `{"preferredNotifChannel": "notifications_disabled"}` for false; session argv identical in both states.
  - Merge sets desired channel, preserves unrelated keys, removes only command exactly `omc internal notify --provider claude` within Notification/Stop hooks; similar command strings and hooks for other events survive. Mixed owned/foreign hooks in one group retain foreign members and group metadata. Repeated merge is idempotent; toggling true/false updates the same key. Wrong JSON shapes, malformed nested hooks, non-UTF-8 and write failures cannot fail launch and do not overwrite malformed settings.
  - Launch tests parameterize provider/flag; both launch paths wire exactly once even for false, missing pcfg defaults true, dry run writes nothing and prints `notifications: native, on` / `notifications: native, off` with native provider detail.
  - Configure confirm is immediately after each provider model question: `Enable native notifications for claude?` (or codex); default is current flag. Test yes/no and None preserving each prior boolean. Old backend question is absent; old `notifications.*` set keys are rejected. Old file section loads with warning and disappears on save/migration.
  - Internal `notify` command is absent/refused with ordinary unknown-command behavior, without compatibility stub.
  - Delete obsolete delivery-only tests; retain and reshape wiring tests. Update old constructor/argv fixtures as requirements change.
  - Extend existing real start E2E: Claude worktree settings has auto and no old hook command; Codex real dry-run contains `-c tui.notifications=true`. Keep artifact-based checks, no notification visibility claim from headless containers. Add assertions before implementation and attempt the focused E2E red run; record any environment blocker explicitly.
- [x] **Step 2: Implement providers and wiring.** Codex adds only `tui.notifications` beside title config; code comment records verified 0.158.0 and `notification_method=auto`, `notification_condition=unfocused`. Claude writes explicit channel state and a code-site version note grounded in local installed CLI evidence. Simplify notify.py to JSON merge/wiring; delete marker-file branch and delivery helpers. Catch malformed-settings and filesystem errors with warn-and-skip behavior, preserve originals.
- [x] **Step 3: Integrate callers and remove old config/CLI.** Pass flag from provider config (default true when missing) in session_plan and unconditionally wire in start/implement. Share a small pure dry-run description helper if necessary to avoid duplicating mapping logic; do not add another provider policy mechanism. Remove old schema/resolver/store branches and internal notify parser. Move confirm into per-provider loop, assigning only when answer is not None.
- [x] **Step 4: Update docs with native behavior and upgrade note.** Explain default on, per-provider false example, session-only overrides and preserved user Codex notify command. Tell old worktrees to remove exact omc entries from Notification/Stop manually; new/re-entered wiring strips them. Replace distribution wording with “set the harness's native notifications”. Leave historical specs/plans and generated E2E wiki alone.
- [x] **Step 5: Run affected units green** with `.venv/bin/pytest tests/unit/test_providers.py tests/unit/test_notify.py tests/unit/test_session.py tests/unit/test_start.py tests/unit/test_implement.py tests/unit/test_internal.py tests/unit/test_config_store.py tests/unit/test_configure.py tests/unit/test_resolve.py -q`. Adapt to actual existing test-file names. Inspect production/test references to deleted APIs; update only active references. Self-review and record evidence.
- [x] **Step 6: Commit.** Suggested message: `feat: replace custom notification delivery with native session controls`.
- [x] **Controller gate:** task review/fixes and `/omc:check`.

## Implementation completion and later audit

- [x] Whole-branch independent review, fix wave, and scoped re-review; all findings resolved.
- [ ] Live macOS check required by spec §3.7: Claude and Codex permission prompt and turn end produce native alerts; user's Codex Computer Use notifier still fires. Use the checkout directly without installing it. Record observed evidence; ask the user for observation only if UI access cannot establish this.
- [ ] Later, after direct `/omc:audit` authorization: `/omc:finish` gate, rebase via `/omc:rebase-main`, squash, `/omc:check`, `/omc:build`, `/omc:verify` (golden stages included in `just e2e-tests`, plus `just codex-gate`), `/omc:review`, described push, ticket sync and follow-ups. Do not bypass failing stages or claim unseen live notifications.

## Plan self-review

Spec §§3.1–3.2 map to Task 1 and Task 2's final removals; §§3.3–3.6 map to Task 2; §3.7 maps to both test steps and completion gates. The two tasks each have a passing unit boundary; no temporary compatibility API is introduced. All five review-focus cases have explicit tests. The updated user instructions received during implementation limit `/omc:implement` to a clean committed unpublished branch. Task reviews and validation continue; publication through finish requires a later direct `/omc:audit`. No additional plan approval is required.

Implementation handoff: commit all feature work, verify a clean tree, and offer audit in this session, `omc review --claude` / `omc review --codex` from the shell, or further discussion. Live macOS acceptance and publication gates remain required before the later finish.


## Execution evidence and unpublished handoff (2026-10-06)

Implementation code is complete and independently reviewed. The user's mid-run lifecycle update superseded this plan's original publication scope: the branch is committed and unpublished; a later direct audit authorizes finish.

- Task 1: `8a89240` plus formatting `ff2a9c3`; observed 16 expected config failures before implementation, then 86 focused passes. Controller `just check`: 1,220 passed.
- Task 2: `414cddb`; native provider/merge tests first produced 15 expected failures. Independent review reproduced a partial-write corruption risk; `70e5ef0` added same-directory temporary-file replacement, with a failing partial-write regression before its fix and 78 focused passes afterward.
- Final test correction `33e142e` makes both providers' missing-config cases literal; all 12 affected launch-matrix cases passed. Whole-branch review and scoped re-review found no remaining blocking or minor code findings.
- Final production-code `just check`: **1,218 passed**, seven pre-existing Python forkpty deprecation warnings. The later test-only correction was verified by its 12 targeted cases.
- `just build`: **passed** — 169 files format-clean, Ruff clean, wheel and source distribution built.
- Focused real Claude start E2E after the atomic-write fix and tightened worktree assertion: **1 passed, 2 deselected**, 229.48 seconds including setup. It reads the created worktree's settings JSON and confirms `preferredNotifChannel=auto` with no legacy omc hook.
- Focused real Codex start E2E: **1 passed, 2 deselected**, 335.31 seconds including setup. Its real dry-run assertion confirms `-c tui.notifications=true`.
- No host install, rebase, squash, push, ticket transition, or publication was performed in this implementation phase. The uv-generated `uv.lock` package-version-only correction was restored to HEAD content because it is unrelated to this feature.

**Testing-policy deviation:** some launch/configure/removal tests were written after implementation. Retrospective focused configure true-to-false and Codex false-argv checks failed against pre-Task2 source, demonstrating regression sensitivity; these do **not** establish historical test-first compliance. Other retrospective harness/setup failures are not counted as behavior evidence. The atomic-write repair did have observed red-before-green evidence. This deviation remains disclosed for audit rather than being described as satisfied retroactively.

**Still required before publication:** the design's live macOS native-alert checks for both providers (permission prompt and turn end), confirmation that the user's Codex Computer Use notifier still fires, the full golden/E2E and Codex gates, and the remaining audit/finish stages. Headless artifact and argv tests do not establish visible notification delivery. Generated knowledge docs remain untouched; their earlier stale snapshot is not asserted current.

Continue with `$omc:audit` in this session, `omc review --claude` or `omc review --codex` from the shell for a fresh audit session, or discussion of the implementation.
