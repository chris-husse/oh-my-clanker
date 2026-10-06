# Review Provider Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an audit handoff that checks the committed implementation against its design before publishing it.

**Architecture:** Mirror the existing implement launcher as `review.py`, seeding the new `audit` conductor. Then atomically change implement's ending and the lifecycle evidence so implementation commits without publishing and audit owns finish.

**Tech Stack:** Python, argparse, pytest, Markdown skills, Docker lifecycle harness.

**Spec:** `docs/superpowers/specs/2026-10-05-add-omc-review-provider-handoff-design.md`

## Global Constraints

- `ToolContext` is the only runtime subprocess/env/network boundary; preserve existing launcher exec conventions.
- Record gate first; seed `/omc:audit`; session `<slug>-audit`; `OMC_SLUG` stays the slug.
- Configured provider is the default; flags override only this run.
- No launcher refactor: import `IMPLEMENT_ALLOWED_TOOLS` from `implement.py`; do not modify implement.py, session.py, internal.py, wtconfig.py, or providers.
- `finish`, `design`, `review`, `grug`, `create-mr`, and `ticket-sync` skills stay unchanged.
- Conformance fixes only; record amendments cannot contradict Decisions taken during brainstorm.
- Trace goes immediately before `## Deliberate complexity`, with dated re-audit entries; commit before finish.
- Landing order: launcher and audit first; implement's ending flips with moved E2E assertions in one commit.
- Tests first, observe expected failure, then implement; no skips; real external integration evidence is required.
- No hand edits to generated knowledge/docs; no host omc installation.
- Per the behavior layer's model-tier policy (AGENTS.md, Model selection), every task in the plan carries a `Model:` line naming its tier — `top tier` for spec, review, and judging tasks; `standard coding tier` as the floor for coding tasks; `heavy coding tier` for bigger coding tasks (multi-file, architecturally tricky, or ambiguous). Tier names only, never pinned model ids.

## Review Focus

- Invalid/dirty record must refuse before plugin repair or provider probes (Task 1 refusal tests).
- Dry run with notifications enabled must create no notification files (Task 1 mirrored dry-run tests).
- A design-only branch must refuse audit even though its diff contains a record (Task 2 gate contract; Task 3 live refusal variation).
- Re-audit trace must preserve the terminal complexity section and commit before a failed finish can be retried (Task 2 contract).
- Audit evidence must reject added/deleted/modified unrelated specs, rather than just finding any review heading (Task 3 snapshot helper tests).

## Pressure-test evidence

`omc:explain` graph queries for launcher, manifest, and lifecycle seams identify `src/omc/implement.py`, `tests/unit/test_cli.py:test_implement_parser_and_dispatch`, `tests/unit/test_plugin_manifests.py:test_implement_skill_contract`, `tests/e2e/lifecycle_helpers.py:_assert_implemented_artifacts`, and `tests/e2e/golden/test_golden_claude.py:test_stage_implemented`. Source inspection confirms a copied launcher needs no provider enum or shared-tail refactor. Manifest contracts can land audit before flipping implement. Snapshot dictionaries already contain source/spec hashes and remote refs, so audit comparison needs a pure helper, not a driver schema change. `.omc/docs/gitnexus/docs/overview.md` confirms the CLI/skill boundary; code is the detailed truth.

---

### Task 1: Launch a review session from a committed record

Model: heavy coding tier

**Files:** Create `src/omc/review.py`, `tests/unit/test_review.py`; modify `src/omc/cli/__init__.py`, `tests/unit/test_cli.py`, `tests/e2e/variations/test_from_agreed.py`.

**Interfaces:** Consumes `resolve_design_record`, `session_plan`, `run_headless`, `IMPLEMENT_ALLOWED_TOOLS`; produces `run_review(ctx: ToolContext, cfg: Config, *, dry_run: bool = False, headless: bool = False) -> int` and `REVIEW_SEED = "/omc:audit"`.

- [x] Write mirrored launcher/CLI tests before implementation: exact seed and audit session name; record-first refusal for missing/dirty record; configured provider and override; mutual exclusion; plan rows; headless allow-list and return code; interactive shell wiring; notifications disabled/enabled with dry run free of writes. Mirror `test_implement.py` fixtures and existing CLI monkeypatch dispatch convention.
- [x] Run `uv run pytest tests/unit/test_review.py tests/unit/test_cli.py -q`; confirm expected missing-review/parser failure.
- [x] Implement the independent launcher modeled on `run_implement`, module-local `os`, `detect_shell`, `run_headless`, plan heading `omc review — plan`; add registry provider flags and lazy CLI dispatch with `_with_provider`.
- [x] Add model-free review dry-run variation beside the implement fixture-record case. Assert refusal before record, then exact `record`, `session`, `/omc:audit` seed rows and unchanged HEAD/tree.
- [x] Run the targeted unit tests, format/lint changed Python, and commit tests + launcher. Controller runs full `/omc:check` after review.

### Task 2: Add the audit conductor and installed skill contract

Model: heavy coding tier

**Files:** Create `skills/audit/SKILL.md`; modify `tests/unit/test_plugin_manifests.py`, `tests/e2e/codex_plugin_payload.py` and its affected unit fixtures if needed.

**Interfaces:** Consumes the bare `/omc:audit` seed and `omc internal design-record`; produces a user-facing conductor which gates, reviews conformance, disposes findings, commits trace, and invokes unchanged `finish`.

- [x] Add failing manifest tests: audit in user-facing and conductor tuples; frontmatter; externalize first; completion contract; phase order gate → conformance → finish; required record/design/check/finish names; product-diff gate excludes `docs/superpowers/`; trace ordering, re-audit entry and commit-before-finish instructions. Pin installed Codex payload inclusion.
- [x] Run `uv run pytest tests/unit/test_plugin_manifests.py tests/unit/test_e2e_codex_auth.py -q`; observe missing audit/payload failure.
- [x] Write audit according to spec §3.4: resolve base as finish does; fetch; refuse with `nothing to audit — run /omc:implement first` when no non-docs/superpowers changes; record truth and optional plan context. Findings cite record section and file:line, Important/Minor, code deviation or stale record. Fix or amend within decisions, else batch CRITICAL questions. Check after code fixes; trace provider/date/findings immediately before terminal complexity section, dated repeat entries; commit trace/fixes; finish. Preserve authorization through required answers.
- [x] Add `audit` to installed Codex payload; do not flip implement or distributed authority yet.
- [x] Run targeted tests, then commit skill + tests. Controller runs full `/omc:check` after review.

### Task 3: Move publication from implement to audit with lifecycle evidence

Model: heavy coding tier

**Files:** Modify `skills/implement/SKILL.md`, `src/omc/distribution/AGENTS.md`, `README.md`, `justfile`, `tests/unit/test_plugin_manifests.py`, `tests/unit/test_e2e_lifecycle_stages.py`, `tests/unit/test_lifecycle_command.py`, `tests/e2e/lifecycle_helpers.py`, `tests/e2e/golden/test_golden_claude.py`, `tests/e2e/variations/test_from_recorded.py`, `tests/e2e/variations/test_codex_handoff.py`, `tests/e2e/test_e2e_lifecycle_full.py`; create `tests/e2e/variations/test_from_implemented.py`.

**Interfaces:** Consumes Task 1 `omc review` and Task 2 `audit`. Produces `_direct_audit(provider: str, detail: str = "") -> str`, `_assert_reviewed_record(before: dict, after: dict, record: str) -> None`, `_assert_audited_artifacts(container, repo, worktree, branch, evidence, implemented)`, and new `audited` snapshot. Keep `_assert_implemented_artifacts` signature while changing its contract to unpublished. Update `_assert_successful_implementation` callers to explicitly assert implementation then audit publication.

- [x] First rewrite implement manifest pins: record → plan → build → handoff, `/omc:audit`, `omc review`, no finish invocation. Pin three authority words and audit conductor in distribution. Run manifest tests and observe current two-word/ship failure.
- [x] First add pure dictionary tests for `_assert_reviewed_record`: only named record hash changes; reject unchanged record, missing record, added/deleted/changed other specs; ignore plans. Run and observe missing helper failure. Add exact lifecycle collection expectation for `test_codex_review_handoff_from_claude_implementation` and observe collection failure.
- [x] Implement pure helper and split artifact contracts. Implemented means changed source/new plan/unchanged specs/clean tree/≥1 commits over origin/main/no remote branch. Audited means exactly named record changed among specs, review heading present in that record, clean tree/one commit, ordered finish markers, published correct fix. Add `_direct_audit` with Codex `$omc:audit` and Claude `/omc:audit`.
- [x] Change implement to commit plan and task residue, restore only known tool lockfile drift, verify clean tree, and state verbatim spec §3.5 continuations. No finish call. Update behavior layer and README three-word rule, commands, conductor endpoints and E2E stages; retain unchanged skill files required above.
- [x] Update golden implemented artifacts and measure elapsed seconds; add expensive audited stage from implemented running `omc review --claude --headless` with implement budget. Keep implemented expensive until measurement supports <240s; update recipe comments to describe both stages. No stage forks audited.
- [x] Move failing-build variation to implemented and type audit; add committed wrong-greeting drift then audit and assert correct published source + record review trace. Rename recorded variation to unpublished implementation and assert child dispatch plus new artifact contract. Add design-only audit refusal variation from recorded to prove gate ignores docs/superpowers changes.
- [x] Add explicit audit turns to all three monolithic lifecycle scenarios. Keep failing-build sabotage and assert build fails during audit with remote untouched. Adapt Codex implement evidence to unpublished; add Codex review evidence from Claude implemented with configured default restored to Claude, Codex account volume, override probe assertion, audited artifact assertions, expensive marks and lifecycle-full collection.
- [x] Run targeted unit tests and collection, format/lint changes, and commit ending flip with all moved assertions atomically. Controller reviews and runs `/omc:check`.
- [x] Run real golden-full and cross-provider handoff evidence through the existing recipes at the milestone, record implement timing, and move implemented to default golden only if measured below 240s. Missing prerequisites fail loudly. Follow required project finish gates without bypassing failures.

## Controller completion

- [x] Task-scoped review and `/omc:check` after each task; final whole-branch review.
- [ ] Follow this session's invoked installed implement conductor through `/omc:finish` (the changed repository skill defines future sessions): rebase, squash, check/build/verify/review, describe and push, ticket/follow-ups.


## Review amendments

The final review added a narrow audit-to-finish check for unrelated or undisposed dirty work. Such work remains preserved and blocks publication pending disposition; relevant feature/stage configuration continues through unchanged finish. The fix wave also strengthened exact argv and refusal assertions, added the personal-draft variation, and recalculated the monolithic timeout.

## Live validation and follow-up fixes

Rebased onto main at `5d82db1` (0.1.19), including its global-instruction repair in the mirrored review launcher. Implementation measured 38.4, 37.5 and 43.8 seconds, supporting promotion to the default golden tier. All six expanded golden stages and the four audit variations passed, including publication, drift repair, design-only refusal, external build failure, and unrelated-draft preservation after a keep-out answer. Both provider handoffs produced passing evidence.

Live runs corrected the dry-run test's missing model argument and fixture bytecode ignore. The fixture now owns the build sentinel check in its baseline; the failure variation changes only external state, so it reaches the build gate without introducing ambiguous tracked edits. Audit explicitly stays paused when excluded unrelated residue remains because finish's squash stages all dirt.

A resumed Claude implementation exposed extra metadata in task-notification result origins. A failing unit regression reproduced the exact-dictionary comparison; the parser now recognizes `origin.kind` while retaining task, session, ordering and UUID validation. This changes no driver API or output schema. The final unit check passed 1,243 tests, build passed, and the parser-fixed default suite passed five golden stages plus 58 parallel cases; the resumed implementation regression passed in 43.99 seconds.

One initial Codex implementation handoff changed the spec hash map. Two isolated reruns against the same image passed (304.21 and 312.31 seconds), but the original diff was unavailable, so its cause remains unresolved. Failure-only diagnostics now preserve the diff and actor output if it recurs. Passing reruns are not a claim that this intermittent observation was fixed. The expensive monolithic and native iTerm2 tiers were not run.
