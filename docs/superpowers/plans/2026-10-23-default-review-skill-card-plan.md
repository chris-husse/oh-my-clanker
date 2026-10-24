# Default Human Review Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the shipped human MR review rules on every `/omc:review` diff beside grug, with one disposition path and one stage verdict.

**Architecture:** Add an internal leaf skill with two bundled rule documents. Extend the existing review skill to dispatch both lenses after the project stage, then disposition their Important findings together. Keep the implementation in skills and contract tests; no Python runtime or new E2E is needed.

**Tech Stack:** Markdown skills, Python 3.12+, pytest, uv, just, git.

**Spec:** `docs/superpowers/specs/2026-10-23-default-review-skill-card-design.md`

## Global Constraints

- The two rule files are copied byte for byte, including trailing newlines, from the handed-over source files.
- `mr-feedback-rules.md` SHA-256: `5d25bcd583528281f360fc3b60e7b8686d21abc30ac28a07b4a73a2407106f5f`; `mr-feedback-reference.md` SHA-256: `24e918a49ee65ad78a92854a1fd2daeca74cd83cb74f084fd545aacaa7a9060c`. Verify the copied files with `shasum -a 256` before committing; do not pin hashes in tests.
- Name them “human MR review rules” or “rules,” never “cards.” Keep the source wording, including language-specific examples; do a manual neutrality read, with no organization-name denylist test.
- The compact rules file is the reviewer's only rules input. Its own preamble defines the grammar. The reference file ships for maintainers and is never read by the reviewer.
- The leaf reads only its compact rules, the filtered diff and the selected design record's “Deliberate complexity” section. It runs no graph query, invokes no skill, edits nothing, dispatches nothing and asks no user.
- The project review stage runs first. A failed project stage skips both lenses. `configured` describes only that stage; `passed` requires it, both lenses and all Important dispositions.
- Use the existing `OMC_MODELS` Review choice, `tasks.review.model` and supported `tasks.review.effort`. The caller owns fixes, waivers, questions and the sole `OMC_STAGE` line.
- Red → green for every change: run each new or amended test and observe the expected failure before changing its target. No skipped tests. Use `/omc:check` after task changes, then `/omc:build` and `/omc:verify` only at the implementation milestone.
- Preserve the existing review test anchors `3. **Grug lens.**` and `- **Grug unavailable.`. Do not edit `src/omc/`, `skills/audit/SKILL.md`, plugin manifests, `.omc/docs/` or E2E tests.
- `README.md` is generated: change `.claude/skills/regenerate-readme/SKILL.md`, then follow its regeneration steps. Do not hand-edit the README.

## Review Focus

- A malformed, duplicate or truncated compact rule must fail validation before judgment, rather than silently consulting fewer rules; Task 1's grammar assertions and Task 2's invalid-rules contract pin this.
- A missing or unreadable bundled compact file is a broken install and yields `human-review: invalid rules — <reason>` with no findings; Task 2 pins the explicit failure path.
- An absent or unfetchable base prints `human-review: cannot resolve base <base>; skipping the lens` and counts as a run with zero findings; Task 2 pins this separate git-plumbing path.
- After the project stage passes or is unconfigured, a headless harness with no worker dispatch still invokes both skills inline; a skill that cannot be invoked fails review. Task 3 pins both branches of this distinction.
- Overlapping Important findings on one hunk cause one fix and one `/omc:check`, while any surviving Important finding still needs a waiver or a batched question; Task 3 pins the shared disposition path.

## File Map

| File | Responsibility |
|---|---|
| `skills/human-review/mr-feedback-rules.md` | Verbatim compact grammar and 31 review rules. |
| `skills/human-review/mr-feedback-reference.md` | Verbatim maintainer reference with matching slugs. |
| `skills/human-review/SKILL.md` | Validate, judge and report the lens without disposition. |
| `skills/review/SKILL.md` | Dispatch project stage and both lenses, disposition once, emit `OMC_STAGE`. |
| `skills/finish/SKILL.md` | Explain that either lens can make review fail or amend tracked files. |
| `.claude/skills/regenerate-readme/SKILL.md` | Source of the README review-stage wording. |
| `README.md` | Regenerated user-facing result. |
| `tests/unit/test_plugin_manifests.py` | Bundled-file grammar/parity and skill-contract guards. |
| `tests/unit/test_cli.py` | README review-stage wording guard. |

## Pressure-test evidence

The generated graph docs are 12 commits behind the current code index (`OMC_KNOWLEDGE fresh:false`; fix in the primary checkout: `omc watch --once --enable-documentation`). The four `/omc:explain` passes below therefore use current source and graph symbol locations, not stale docs. Source spectrum is unavailable for this repository's forge.

- **Bundling and tests:** `tests/unit/test_plugin_manifests.py:test_skills_have_frontmatter` and `:test_internal_skills_marked_internal` already iterate `INTERNAL_SKILLS`. `.codex-plugin/plugin.json` points to `./skills/`, and `pyproject.toml` force-includes `skills/` into the wheel. Only the internal list and new tests need changing.
- **Leaf inputs:** `skills/grug/SKILL.md` supplies the existing base fallback, fetch/diff filter, design-record selection and waiver pattern. The human-review leaf deliberately omits grug's project-conventions read; it can follow the same git mechanics without taking that fourth input.
- **Review orchestration:** `tests/unit/test_plugin_manifests.py:test_review_proxy_runs_grug` and `:test_review_proxy_dispatches_grug_judgment_on_review_choice` pin the current step-3 anchors and Review choice. `skills/review/SKILL.md` already owns the project-stage gate, fix/check/waive order and one `OMC_STAGE`; extend these in place.
- **Finish and README:** `tests/unit/test_plugin_manifests.py:test_finish_skill_contract` guards the stage order. `tests/unit/test_cli.py:test_readme_headings_match_regeneration_skill` guards generated structure. `skills/finish/SKILL.md` Step 4 and the recipe's lifecycle paragraph are the two source locations to change before regeneration.

### Task 1: Bundle and validate the two rule files

Complexity: high

**Files:** Create `skills/human-review/mr-feedback-rules.md`, `skills/human-review/mr-feedback-reference.md`; modify `tests/unit/test_plugin_manifests.py`.

**Interfaces:** Produces a compact file with 11 categories and 31 unique `<category>/<slug>` rules, plus a reference file with the same slug set. Task 2 reads only the compact file beside its `SKILL.md`.

- [ ] **Step 1: Write the failing tests.** Add `test_human_review_rules_grammar` to assert the source preamble's title/category/rule structure, enclosing-category match, one nonempty principle paragraph followed by `Flag:` and `Fine:` (including wrapped lines), unique slugs and per-category counts `{types:1, structure:6, data-flow:2, control-flow:1, errors:3, observability:3, naming:1, docs:2, tests:6, scope:4, config:2}`. Add `test_human_review_reference_slug_parity` to assert matching `### <category>/<slug>` sets. Use a small test-local parser; keep its rule interpretation aligned with the compact file's preamble.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k 'human_review_rules_grammar or human_review_reference_slug_parity'`; expect missing bundled files, never a skip.
- [ ] **Step 3: Copy exact assets.** `mkdir -p skills/human-review`; use `cp` from the two stable source paths named above. Do not normalize line endings, headings or newline. Read both for genericity, and run `shasum -a 256 skills/human-review/mr-feedback-{rules,reference}.md`; expect the two Global Constraints digests.
- [ ] **Step 4: Verify and commit.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k 'human_review_rules_grammar or human_review_reference_slug_parity'`, then `/omc:check`; expect green. Run `git diff --check` and commit both rule files with their tests.

### Task 2: Add the internal human-review leaf

Complexity: high

**Files:** Create `skills/human-review/SKILL.md`; modify `tests/unit/test_plugin_manifests.py`.

**Interfaces:** Consumes optional `$ARGUMENTS` `<base>`, `mr-feedback-rules.md`, filtered `git diff origin/<base>...HEAD` and file-level waivers in the selected record. Produces finding blocks with `file`, `line`, `severity`, `rule`, `current`, `suggested`, `notes`, `ambiguous`, followed by `human-review: N rules consulted, k Important, m Minor`; or a single `human-review: invalid rules — <reason>` failure line; or the git-plumbing skip line.

- [ ] **Step 1: Write the failing contract test.** Add `human-review` to `INTERNAL_SKILLS` and `test_human_review_skill_contract`: assert the internal frontmatter marker and mandatory tokens (`$ARGUMENTS`, compact filename, `Flag:`, `Fine:`, `human-review:<category>/<slug>`, `Deliberate complexity`, `Important`, `Minor`, `ambiguous`, `Never Critical`, `invalid rules`, `rules consulted`). Assert missing/unreadable rules fail with no findings, failed base/fetch skips with zero findings, and that the skill explicitly forbids reading the reference file, project conventions and source outside the diff, graph calls, edits, dispatch and a user-facing `/omc:human-review` entry point.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k 'human_review_skill_contract or skills_have_frontmatter or internal_skills_marked_internal'`; expect missing `SKILL.md`.
- [ ] **Step 3: Write `skills/human-review/SKILL.md`.** Reuse grug's optional-base resolution, `git fetch` and filtered three-dot diff, and design-record selection, but read exactly the three inputs in Global Constraints. Validate all rules before judgment, following the compact file's preamble and the design's extra structural checks. A missing/read-error/invalid compact file returns the one failure line and no findings; git plumbing returns the skip line and zero findings. Apply all 31 principles and `Flag:`/`Fine:` boundaries; suppress a matching `human-review:<category>/<slug>` in a named file when the record waives it. Cite each principle verbatim in `notes`. Important is a definite Flag with a local or behavior-preserving fix; ambiguous matches and nonlocal definite fixes are Minor with the specified `ambiguous` value. Never emit Critical. Count findings in the final line.
- [ ] **Step 4: Verify and commit.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k 'human_review or skills_have_frontmatter or internal_skills_marked_internal'`, then `/omc:check`; expect both green. Recheck both copied SHA-256 digests and `git diff --check`, then commit the skill and its test changes.

### Task 3: Run both lenses through one review disposition

Complexity: high

**Files:** Modify `skills/review/SKILL.md`, `tests/unit/test_plugin_manifests.py`.

**Interfaces:** Consumes the Task 2 finding blocks and final line beside grug's findings and `grug summary:`. Emits the existing single-line `OMC_STAGE {"stage":"review", "configured":..., "passed":..., "summary":...}` contract; no new machine prefix.

- [ ] **Step 1: Write the failing test.** Add `test_review_proxy_runs_human_review` in `tests/unit/test_plugin_manifests.py`. Assert project stage precedes both lenses; step 3 names both Review-choice workers, parallel dispatch when possible, grug-first serial fallback, and inline invocation when no worker is available. Assert `human-review skill unavailable — plugin stale? run omc update` and `human-review: invalid rules` both give `"passed": false`; a failed project stage skips both. Assert one fix/check per overlapping hunk, shared fix→waive→batched-question order, Minor advisory status, both lenses in step-4 summary examples, and absence of `human-review` from `check`, `build`, `verify`. Preserve the existing grug test anchors.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k 'review_proxy_runs_human_review'`; expect failure on the absent lens.
- [ ] **Step 3: Extend `skills/review/SKILL.md`.** Change its description/title and unconfigured message to name both lenses. In step 3 resolve one base, dispatch grug and human-review at `tasks.review.model`/supported `tasks.review.effort`, with fresh or partial-history Codex forks when overriding a model; use grug-first serial fallback, and inline skills on the session model only when no worker can be dispatched. Keep the literal grug anchors. Unavailable invocation of either skill and invalid human rules fail the stage; failed git fetch within a running lens does not. Disposition all Important findings once: Review worker fix, one `/omc:check` per overlapping hunk, revert red fixes, then record file/rule waiver under “Deliberate complexity,” then ask one numbered batch. List Minor findings without gating. Emit exactly one `OMC_STAGE` whose `configured` remains project-only and whose summary names project, grug and human-review.
- [ ] **Step 4: Verify and commit.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k 'review_proxy or stage_proxy_contract'`, then `/omc:check`; expect green. Run `git diff --check` and commit the skill and its tests.

### Task 4: Propagate the review contract to finish and the generated README

Complexity: high

**Files:** Modify `skills/finish/SKILL.md`, `.claude/skills/regenerate-readme/SKILL.md`, `README.md`, `tests/unit/test_plugin_manifests.py`, `tests/unit/test_cli.py`.

**Interfaces:** Consumes Task 3's existing `OMC_STAGE` verdict. Finish's gate order and amend rule are unchanged; the README describes both always-on lenses and Important disposition.

- [ ] **Step 1: Write failing guards.** Extend `test_finish_skill_contract` so Step 4 names `human-review` beside grug in tracked-file amendments and unconfigured review failure. Add `test_readme_documents_human_review` to assert the recipe's lifecycle slot and README review-stage paragraph name grug and human-review, and say an Important finding needs a fix or waiver. Keep README structure guards intact.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py::test_finish_skill_contract tests/unit/test_cli.py::test_readme_documents_human_review -q`; expect the new assertions to fail.
- [ ] **Step 3: Update the sources and regenerate.** Edit only the two Step-4 sentences in `skills/finish/SKILL.md` that name grug; do not alter stage order or `configured` semantics. Update the recipe's always-on-review line, then follow `.claude/skills/regenerate-readme/SKILL.md` to regenerate the whole README and check its carryover facts. Do not hand-edit the generated README.
- [ ] **Step 4: Verify and commit.** Run `uv run --frozen pytest tests/unit/test_plugin_manifests.py::test_finish_skill_contract tests/unit/test_cli.py -q -k 'readme or shell_integration_documents or finish_skill_contract'`, then `/omc:check`; expect green. Inspect `git diff -- README.md .claude/skills/regenerate-readme/SKILL.md`, run `git diff --check`, then commit the skill, recipe, README and tests.

## Completion gate

- [ ] Review the full spec against all four tasks and verify that `src/omc/`, `skills/audit/SKILL.md`, plugin manifests and E2E tests stayed untouched. Check the copied rule-file hashes once more and read both files for neutrality.
- [ ] Run the final `/omc:check`, then `/omc:build` and `/omc:verify` at the implementation milestone, following the repository's bounded stage-failure rule. Existing finish and lifecycle E2E provide the live review path; add no new E2E.
- [ ] Inspect `git status --short` and commit any remaining plan or task documentation so the handoff branch is clean and unpublished. Report the implementation continuation choices required by `/omc:implement`.

## Execution note

The original constraint to leave E2E tests untouched could not hold after the
milestone verify exposed a fixture permission mismatch. The finish E2E actor
allowed `Bash` and `Skill`, while the new review skill needs `Read` to open its
bundled rules. Both affected finish cases failed at that permission boundary.
Commit `1cd6549` adds `Read` to the four finish actor invocations and expands
the failure message in the intermittent verify-blocks-handoff variation to
retain actor evidence. Its two targeted finish E2E cases passed; the final
milestone gates passed with 1,699 unit tests, a clean build, and 5 golden plus
60 remaining E2E tests. The intermittent variation passed in that full run.
