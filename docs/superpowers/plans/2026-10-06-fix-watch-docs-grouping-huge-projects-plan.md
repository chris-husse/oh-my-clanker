# Huge-project wiki grouping and failure reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make huge-repository grouping fit the answer budget and expose real wiki failures through omc.

**Architecture:** GitNexus reuses its existing grouping partitioner with input and output limits, falls back visibly, and diagnoses empty capped streams. omc shares a pure failure-excerpt helper between its two wiki callers. The Docker pin follows the fork merge.

**Tech Stack:** TypeScript/Vitest (GitNexus), Python/pytest (omc).

**Spec:** `docs/superpowers/specs/2026-10-06-fix-watch-docs-grouping-huge-projects-design.md`

## Global Constraints

- Output estimate is the sum of `estimateTokens` for each `"<path>",`; budget is one quarter of configured `maxTokens` (16,384 today), with one named constant beside `GROUPING_TOKEN_BUDGET`.
- No reasoning controls, retries, new flags, persisted logs, wiki-meta degradation flag, or omc wiki argv changes.
- Redact before filtering/truncation; excerpt is the final 400 characters of nonblank, non-progress stderr lines followed by stdout lines.
- Preserve ToolContext as the subprocess/environment/network boundary and standard-library-only top-level imports in wikirun.py.
- Tests first, observed red then green; restricted-PATH stubs use builtins or absolute commands. No skipped tests.
- Implement produces unpublished commits. Fork publication/merge, host update, and the full funds-rs acceptance run are later integration steps; a real fork merge commit is required before pinning.

## Review Focus

- An indivisible over-budget file must terminate batching without dropping its path (Task 1).
- Custom output caps and Windows/escaped paths must affect estimates consistently (Task 1).
- Empty non-length streams and nonempty length streams retain their behavior (Task 1).
- Blank/progress-only or absent streams yield a safe empty excerpt, with exit status still visible (Task 2).
- Secrets straddling the 400-character boundary must be redacted in full before truncation; exit zero without wiki must name the missing directory (Task 2).

## Pressure-test evidence and sequencing

omc:explain graph contexts `_run_wiki` and `run_document` confirm both callers already use wikirun and DocsRun.redact. Current source confirms the duplicated stderr-or-stdout selection. The helper therefore needs no new runtime dependency; use TYPE_CHECKING for CompletedProcess annotations.

GitNexus graph contexts `batchFilesForGrouping` and `readSSEStream` at e890baff confirm batching's existing input-budget checks and callLLM as the SSE caller. Source confirms the directory/sub-batch loop and symbol-trimming behavior; change its fit predicate, retaining an indivisible-file escape. Graph receiver typing is incomplete, so source and tests remain authoritative.

omc:explain `update_gitnexus` confirms the host update is a separate fetch/build operation. At planning time GitHub had no PR for this fix; PR #6 has since merged with explicit user approval. Implement both independent repositories locally before the publication boundary; do not invent a merge SHA or publish under implement authorization. The original spec's publication order remains fork first, then omc.

### Task 1: GitNexus grouping budget, visible fallback, and capped-stream diagnosis

**Model:** heavy coding tier

**Files:** In `/tmp/omc-gitnexus-output-grouping`, modify `gitnexus/src/core/wiki/generator.ts`, `gitnexus/src/core/wiki/llm-client.ts`, `gitnexus/src/cli/wiki.ts`; test `gitnexus/test/unit/wiki-grouping-batch.test.ts` and existing generator/LLM-client/CLI test siblings.

**Interfaces:** Consume existing `WikiGenerator.llmConfig.maxTokens`, `estimateTokens`, `fallbackGrouping`, and `invokeLLM`. Produce private `estimateGroupingOutputTokens(files: FileWithExports[]): number`, private `groupingFits(files: FileWithExports[]): boolean`, and optional `WikiRunResult.groupingFallback?: string`. SSE reader may accept the configured cap from callLLM for its error.

- [x] Write failing tests: input-under-budget/output-over-budget files trigger multiple grouping LLM calls; estimator equals sum of estimated quoted paths; configured caps affect batches; preserve all files including Windows/escaped paths and an indivisible over-budget path.
- [x] Write failing tests: rejected single and batched grouping resolve to directory grouping, emit a reason-bearing fallback progress line, and return the fallback reason. Verify both CLI summary sites display it; malformed grouping response fallback must also remain visible if it invokes fallbackGrouping.
- [x] Write failing SSE tests: no content plus `length` names `max_completion_tokens` and no visible text; empty `stop` retains old error; content plus `length` returns content.
- [x] Run the focused Vitest files and record expected failures before implementation.
- [x] Implement the shared input/output predicate throughout the partition loop and initial branch selection. Retain directory affinity, symbol trimming, and terminating singleton behavior. Catch single-path failures; propagate fallback reason to run results and both summaries without meta persistence. Track SSE finish reason without changing content handling.
- [x] Run focused tests and relevant existing wiki unit tests; record exact commands/results, self-review, and commit all test/product changes together. Do not push, merge, or update the installed clone.

### Task 2: Truthful omc failure excerpts

**Model:** heavy coding tier

**Files:** Modify `src/omc/wikirun.py`, `src/omc/gitnexus.py`, `src/omc/dependency.py`, `tests/unit/test_wikirun.py`, `tests/unit/test_gitnexus_refresh.py`, `tests/unit/test_dependency.py`.

**Interfaces:** Produce `wiki_failure_excerpt(cp: CompletedProcess[str], redact: Callable[[str], str]) -> str`. Consume it in `_run_wiki` with `run.redact` and `run_document` with exact-key redaction then `_redact`.

- [x] Add helper tests for stderr-before-stdout order, removal of GITNEXUS_PROGRESS/blank lines, None streams, 400-character tail, and a secret crossing that boundary. Example: `wiki_failure_excerpt(cp(stderr='GITNEXUS_PROGRESS {}\n', stdout='Error: capped'), identity) == 'Error: capped'`.
- [x] Change existing failure/redaction regressions to put progress on stderr and the actual error/key on stdout. Assert `wiki failed (exit 1):` / `gitnexus wiki failed (exit 1):`, visible error, no leaked key, no progress payload in excerpt.
- [x] Add exit-zero/no-wiki regression asserting the missing directory is named and `exit 0` is not presented as the failure.
- [x] Run focused pytest files and record expected failure before implementing.
- [x] Implement the pure helper and wire both callers. Keep stall handling, surrounding narration, and `_run_wiki` returning True unchanged. Use a separate missing-directory message for successful exit without output.
- [x] Run focused tests, self-review, and commit test/product changes together. Controller runs omc:check after review.

### Task 3: Pin the merged fork and complete handoff

**Model:** standard coding tier

**Files:** Modify `docker/Dockerfile.e2e`; update this plan's status.

**Interfaces:** Requires the actual merged commit containing Task 1 in `chris-husse/GitNexus` main. A branch head or local commit is not sufficient.

- [x] After fork publication is authorized and merged, verify the merge contains Task 1. Update ARG GITNEXUS_REF to its full SHA and cite this design record in the preceding comment.
- [x] Run omc:check; commit pin. Full E2E belongs to audit/finish. Host `omc update` and full funds-rs `omc watch --enable-documentation --once` follow the approved integration sequence, not the per-task loop.
- [x] Run whole-branch review, resolve findings, commit plan and authorized remaining changes, verify task commits and clean working tree. Preserve pre-existing `.claude/settings.local.json.bak`; it cannot be silently discarded for a clean-tree claim.
- [x] State all three implement continuations once complete. If fork merge or pre-existing work blocks completion, report the concrete remaining blocker rather than claim complete.

### Task 4: Regroup truncated grouping batches (fork follow-up after the failed live run)

**Why:** the first live funds-rs run after PR 6 fell back on batch 2 of 17 with `invalid grouping JSON`; the captured stream was well-formed JSON cut at `finish_reason: length` (design record section 2, second failure; section 4.1.5).

- [x] Capture one bounded failing grouping response: tee-proxy run stopped at the first fallback line (141 s, three calls); replay batch 2 with `reasoning_effort` low/none (ignored) and a 32k cap (completes). Throwaway scripts only.
- [x] Write failing tests: truncated batch regroups as halves with no fallback line and no `Other`; truncated single-prompt answer recovers the same way; single-file truncation falls back naming the cap; malformed-JSON reason carries finish reason and size; a 160-token estimate no longer fits a 1,000-token cap; `finishReason` populated on SSE and JSON paths. Observed red: seven failures for the expected reasons.
- [x] Implement: `LLMResponse.finishReason`; `buildModuleTree` → `groupInBatches` over `fits ? [files] : partition`; `groupBatch` splits on `length`; divisor 4 → 8; reason string. Green: 231 wiki tests, source typecheck, Prettier on changed files. Fork commit `baccf011` on `fix/wiki-grouping-split-on-truncation`.
- [x] Grouping-only live proof against the fork build (design 4.3 step 3): passed, 28/28 batches `stop` + valid JSON, no fallback, `Created 277 modules` in 953 s.
- [x] Fork PR published: https://github.com/chris-husse/GitNexus/pull/7 (commit `baccf011`).
- [x] PR 7 merged by the user at `24af4f6006f5ee05ebbeeb187bba4c6186d2d5ce`; `GITNEXUS_REF` pinned to it; managed clone updated and rebuilt to the same commit.

## Execution status — 2026-10-06

- Task 1 implemented in `/tmp/omc-gitnexus-output-grouping`, branch `fix/wiki-output-aware-grouping`: commits `79b25a7c` and `230c7945` atop `e890baff`. Task review found and fixed combined-budget singleton trimming. Final relevant validation: 225 wiki tests and source TypeScript check passed. Broad fork tests were stopped after missing worker/shared-graph failures; test TypeScript checking reports eight errors outside changed files. Those broader checks are not passes.
- Task 2 implemented here: `0dd55e0`, `6d43966`, `8c913e0`. Both task-review lint/format findings fixed.
- Whole-change review found a progress-secret leak introduced by reason-bearing fallback progress. Fixed in `4d6bb64` with two observed-red then green successful-fallback regressions; both wiki callbacks now redact before parsing/storing progress. The obsolete risk-description allowance was also corrected. Scoped final re-review: both findings addressed, no new Important/Critical findings.
- Final omc gates at `4d6bb64`: `just check` 1,249 passed (seven existing forkpty warnings); `just build` passed formatting, lint, source distribution, and wheel. Known uv.lock version drift from validation restored.
- Task 3: user approved fork publication and backup relocation. Fork PR https://github.com/chris-husse/GitNexus/pull/6 merged at `d194fc1b80a36e272f04a1051f5e538782b7df45`, containing reviewed formatting follow-up `f9c7771b`. Commit `9d72172` pins that exact merge SHA in Docker. Host update, Docker E2E, and full funds-rs acceptance have not run; they remain integration/acceptance work for audit/finish and the post-merge live gate.
- With user approval, pre-existing `.claude/settings.local.json.bak` was preserved at `/tmp/omc-watch-docs-settings.local.json.bak`; it no longer dirties the worktree.
- Ruling: prepare independent omc and fork code before publication, while preserving fork-first publication order. Cost if wrong: integration waits at publication boundary.
- Ruling: redact progress before parsing/rendering, extending excerpt-only redaction to close the new error-reason leak. Cost if wrong: matching literal key text is masked in progress details.
- No review findings remain open in implemented code. The implementation is committed locally; omc publication and the full integration/acceptance gates belong to the following lifecycle phase. Fork CI is not claimed green: eight test-type errors were reproduced at untouched base `e890baff`, two unchanged web files fail formatting, dependency review is unsupported on the fork, and broader CI was unfinished at merge. Changed-file Prettier, 225 wiki tests, and source TypeScript checking passed after the formatting follow-up.

- Final Task 3 review passed. `just check` at `9d72172`: 1,249 passed, seven existing warnings. Build was previously verified at the final product-code commit; the subsequent change is the Docker pin/comment. All implementation tasks are complete.

### Live run and Task 4 — 2026-10-06 (later)

- Full acceptance run (`omc watch --enable-documentation --once` on funds-rs at `47009e3f`, claude-sonnet-5): 17 batches, `Grouping failed (invalid grouping JSON)` after batch 2, stopped by the user during batch 4; zero pages. Log `/tmp/omc-funds-rs-acceptance.log`.
- Probe: tee-proxy rerun reproduced the same batch failing the same way (252 files, estimated answer 4,090 tokens, 14,989 chars of JSON then `finish_reason: length`, 109 s). Replays: `reasoning_effort` low and none accepted and ignored (16,384 completion tokens, truncated); `max_completion_tokens: 32768` finished with `stop` and valid JSON (14,317 completion tokens).
- Decisions (user, 2026-10-06): split-on-truncation plus divisor 8 over a grouping-only 32k cap; grouping-only live proof before any full run.
- Task 4 implemented as recorded above. First proof attempt was cut by a supervisor bug (stopped on the percent of the `batch 27/28` line); 26 of 28 batches had completed with `finish_reason: stop`, valid JSON, no fallback and no regrouping line. Supervisor fixed to stop on `Created N modules`; second attempt below.
- Second proof attempt passed: 28/28 batches `finish_reason: stop`, valid JSON, zero fallback/regrouping lines, `Created 277 modules` at 953 s; `first_module_tree.json` (277 modules) written in funds-rs. Observation, out of scope: batched grouping yields about ten modules per batch and merges only identical slugs, so a full generation will write about 277 pages.
- Fork PR 7 opened; merge, pin bump, clone update and the full acceptance run remain.

### Finish gates — 2026-10-06

- `just check` 1,340 passed; `just build` clean.
- Verify run 1: 56 passed, `test_failing_verify_blocks_implementation_handoff` failed on transcript text only. Cause: `parse_claude_stream` returned the requested-turn result even when Claude backgrounded the implementer Agent and answered through a task-notification result. Fixed in `tests/e2e/conversation.py` (turn text = last result) with red-first parser tests.
- Verify run 2: 56 passed, `test_index_then_explain_on_real_repo` failed; reproduced deterministically in a container without any model. Cause: `COPY . /repo` baked this checkout's `.gitnexus` (repoPath = the host primary) into the image; under the upstream storage-state guard carried by the `d194fc1b` lineage (main still pins `f858d50b`, so this lineage had never been E2E-tested), `analyze` refuses the foreign store and omc's rebuild path finds `clean --force` a no-op because the store is not in the container's registry. The test's `"repo" in listed` assertion matched "No indexed **repo**sitories found", so the failure surfaced only at the judge; run 1 passed because that model session worked around the failed index by hand. Fixed by excluding `.gitnexus/` and `.omc/docs/` from the Docker context and asserting on the registry's `Path: /repo` line.
- Follow-up (product, not this record): `omc internal gitnexus refresh` cannot heal a copied `.gitnexus` that is not in the registry; GitNexus adopts a repo-local foreign store only with `analyze --force`. Reproduce with any checkout whose `.gitnexus/meta.json` names another path.
- Verify run 3 (after the Docker-context fix): 56 passed, explain test green; `test_failing_verify_blocks_implementation_handoff` failed on `"?" in answer` although the final answer named the red verify gate and offered the user numbered options. Replaced the literal check with the same-provider judge the project policy prescribes for transcript qualities.
