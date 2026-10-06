# Run verify before phase handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Require the session producing implementation or audit fixes to pass milestone validation before handing its work on.

**Architecture:** Keep orchestration in the skills and the shared retry rule in the distributed behavior layer. Extend existing marker-based lifecycle assertions and the recorded-snapshot variation; leave CLI seeds and stage proxies unchanged.

**Tech Stack:** Markdown skills, Python, pytest, Docker lifecycle fixtures, just/uv.

**Spec:** `docs/superpowers/specs/2026-10-06-run-verify-in-implement-phase-design.md`

## Global Constraints

- Per-task validation stays `/omc:check` only; implementation milestone order is check → build → verify.
- Audit always checks/builds; verify is conditional on tracked product fixes.
- At most two fix-and-rerun cycles (three gate runs); missing verdicts fail closed.
- The retry bound lives once in `src/omc/distribution/AGENTS.md`; conductors cite bounded fix-forward, then a CRITICAL stop.
- No CLI/config changes, no changes to finish or stage proxies, no generated knowledge edits.
- Red-first validation; no skips; real E2E artifacts establish lifecycle behavior.
- The branch remains unpublished. Do not commit the plan until the final green gate.

## Review Focus

- Missing/unconfigured verdicts: missing fails closed, unconfigured pass proceeds; contract tests in Task 1.
- Failed repair checks and flaky reruns: bounded cycles cannot reset; shared-rule contract tests in Task 1.
- Audit without code changes: check/build still run; verify stays conditional; Task 1 contracts.
- Earlier/later stage markers cannot hide missing implementation stages: Task 2 order cases, scoped to implemented artifacts.
- Unavailable E2E environment: Task 2 red-path checks task commits, uncommitted plan, unchanged remote, and a verify question.

## Pressure-test evidence

`omc internal gitnexus query` located the skill contracts and existing fixture; `context _assert_implemented_artifacts` confirms both golden/headless and in-session callers. `context _assert_audited_artifacts` confirms golden, drift, and cross-provider consumers. Generated skills-library docs describe stage proxies as the existing boundary; no new Python stage runner is needed. Current source shows the golden audit does not introduce drift: explicitly add greeting drift there so its three-verify assertion has a real cause. General audit helpers must distinguish drift-repair scenarios from audits without changes.

### Task 1: Add conductor milestone gates and the shared failure rule

**Model:** heavy coding tier

**Files:** Modify `skills/implement/SKILL.md`, `skills/audit/SKILL.md`, `src/omc/distribution/AGENTS.md`, `README.md`; test `tests/unit/test_plugin_manifests.py`.

**Interfaces:** Consumes existing single-line `OMC_STAGE` verdicts; produces skill instructions only. No CLI APIs change.

- [x] Extend `test_implement_skill_contract`, `test_audit_skill_contract`, and `test_distribution_agents_validation_cadence` before changing prose. Assert Phase 2b, ordered build/verify after subagent build and before Phase 3, shared stage-gate citation, conditional audit verification, missing verdict failure, unconfigured success, two-cycle bound and CRITICAL stop. Preserve current needles and negative assertion for `finish runs it`.
- [x] Run `uv run pytest tests/unit/test_plugin_manifests.py -q` and record expected red failures.
- [x] Implement design sections 4.1–4.4. The final task's green check supplies milestone check; invoke build then verify. Announce project verify on its own line. Retry rule: one heavy-tier fixer, check, restart from build; failed checks cannot allow progression or reset the bound. Stop after two cycles with stage, summary, attempts, needed answer; no handoff commit/continuations or finish on red. Update four-step externalization and user-facing README placement.
- [x] Run the focused suite green; self-review and commit only task files. Controller reviews then runs `/omc:check`.

### Task 2: Exercise milestone order and blocked handoff with lifecycle artifacts

**Model:** heavy coding tier

**Files:** Modify `tests/e2e/lifecycle_helpers.py`, `tests/unit/test_e2e_lifecycle_stages.py`, `tests/e2e/variations/test_from_recorded.py`, `tests/e2e/golden/test_golden_claude.py`; add a focused unit fixture test file only if needed.

**Interfaces:** Add `_assert_implement_stage_order(markers: str) -> None`; call it in `_assert_implemented_artifacts`. `_fixture(..., failing_stage="verify")` installs the verify sentinel next to build. Audit assertion must require three verify markers in explicit drift-fix scenarios while preserving valid no-fix audits.

- [x] Write parametrized marker tests first: accept `check\nbuild\nverify\n`, preceding checks/reviews, and later finish block; reject empty, no-check, reversed or separated build/verify, and missing verify. Run red before implementing the helper.
- [x] Extend `_fixture` with unconditional verify sentinel script and `failing_stage="verify"`; sentinel failure says the E2E environment is unavailable. Test its real generated shell script behavior locally with a fake container transport only for fixture setup, if necessary; integration is the real Docker variation below.
- [x] Implement marker helper and require marker evidence in `_assert_implemented_artifacts`. For explicit audit drift scenarios require at least three verify markers; introduce the existing greeting drift into the golden audited stage before review, using the established variation's pattern. Avoid imposing three on legitimate no-fix consumers.
- [x] Add `test_failing_verify_blocks_implementation_handoff`, forked from recorded, initially expensive. Touch sentinel before direct implement; assert verify ran, new task commits over recorded HEAD, plan exists but is uncommitted, remote refs unchanged, and response asks a question naming verify (same-provider judge if semantic assertions need one). Do not infer task commits just from the already-committed record.
- [x] Run focused unit tests green. Self-review and commit task files; controller reviews and runs `/omc:check`.
- [x] Controller runs `/omc:build` then `/omc:verify` (`just e2e-tests`), plus targeted expensive red-path and golden audited evidence needed by this change. Update golden timing comment only from a measured passing run; promote the red variation only if measured within 240 seconds. Any unavailable prerequisites are failures, never skips.

### Authorized repair: Claude notification result parsing

**Model:** heavy coding tier

The user approved repairing the live verify failure after the original retry bound. Scope: `parse_claude_stream` in `tests/e2e/conversation.py` and regression coverage in `tests/unit/test_e2e_conversation.py`; leave provider launch behavior unchanged.

- [x] Recover or reproduce the rejected real stream shape and identify the invalid parser assumption.
- [x] Write and run a failing regression for that shape, preserving rejection of malformed, unrelated, duplicate, cross-session and out-of-order results.
- [x] Apply the minimal parser repair, run focused tests and format/lint checks, commit, and independently review.
- [x] Run check, build, and full verify; apply the project's shared-driver Codex gate if required. Retain this source's Docker images with temporary test-owned holders during live runs.

### Gate repair: Codex global-instructions test diagnostic

**Model:** heavy coding tier

The shared-driver regression gate proved Codex loaded the expected global heading, but its test rejected a nonfatal unknown-feature requirement diagnostic. Repair only the assertion in `tests/e2e/test_e2e_global_instructions.py` and add focused unit coverage.

- [x] First reproduce successful heading plus the observed known diagnostic; also require arbitrary errors, tool items, and wrong/missing headings to fail.
- [x] Narrowly recognize that diagnostic, retain all substantive assertions, run focused checks, commit and independently review.
- [x] Run project check/build and the full serial Codex gate. Preserve the already-green full Claude suite as evidence for unchanged Claude behavior.

### Completion

- [x] Whole-branch review and fix any material findings.
- [x] Green check/build/verify milestone; retain actual output and timing evidence.
- [x] Commit plan and remaining task documentation, restore only proven tool drift, inspect final status, preserve unrelated pre-existing backup.
- [x] State the three implementation continuations without publication.


## Validation and implementation record — 2026-10-06

- Project check after all code/test changes: **1,283 passed**, seven pre-existing warnings (18.08 seconds).
- Build: Ruff formatting/lint, source distribution, and wheel passed.
- Full Claude verify after the parser repair: **5 golden + 57 parallel passed**, including the verify-failure scenario. The subsequent patch changes only the Codex heading oracle and its unit tests; Claude behavior remains unchanged.
- Full golden audited scenario: **6 passed**; audit repaired greeting drift and passed the three-verify assertion.
- Final serial Codex gate: **8 passed** in 637.22 seconds, including the repaired global-instructions oracle.
- Independent task, whole-branch, and scoped repair reviews found no blocking issues.

Live verification exposed two harness issues and one host issue. Claude can background an Agent through `task_started` without `run_in_background` in the tool input; the parser now correlates that runtime event with the tool, task, and session. Codex emits a nonfatal unknown-`ultrafast_mode` diagnostic; its heading test now tolerates only that observed diagnostic while retaining its other error/tool/heading checks. Both fixes have red-first regressions. Docker image tags disappeared outside the suite, so foreground verification wrappers retained test-owned image holders and cleaned those holders afterward; no repository retention mechanism was added.

The user approved the additional driver repair and relocation of the pre-existing settings backup. Its bytes are preserved at `/private/tmp/omc-run-verify-settings.local.json.bak`. Known version-only `uv.lock` tool drift was restored.

**Fixture decision:** explicitly introduce greeting drift in the golden audit and require three verify markers only for explicit repair scenarios. The existing golden audit did not make the product change assumed by the design. Any rework from this decision is confined to test fixtures.

**Nonblocking review limitations:** the red-path transcript assertion checks `verify` and a question mark independently; cumulative verify counts do not attribute duplicate retry markers to individual phases. Artifact assertions still enforce the primary handoff boundary.

The implementation remains on this feature branch for audit/publication through the separate lifecycle commands.
