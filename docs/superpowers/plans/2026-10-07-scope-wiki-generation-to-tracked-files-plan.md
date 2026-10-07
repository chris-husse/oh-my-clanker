# Scope wiki generation to tracked files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fork's index and wiki exclude untracked files and submodule contents while preserving existing ignore behavior, then pin omc's E2E image to the released fork change.

**Architecture:** Keep both existing glob enumerations and intersect their results with one git-backed tracked-path set before stat and discovery callbacks. The walker propagates this scope to indexing, content drift, and Dart discovery; the include extractor applies the same filter. omc changes only its dependency pin and artifact assertions, with publication separated from unpublished implementation.

**Tech Stack:** TypeScript, Node, Git, glob 13, Vitest; Python/pytest, Docker, just.

**Spec:** `docs/superpowers/specs/2026-10-07-scope-wiki-generation-to-tracked-files-design.md`

## Global Constraints

- Tracked-only is unconditional: no `GITNEXUS_SCAN_SCOPE` environment variable, no `.gitnexusrc` key, no CLI flag, no omc `GITNEXUS_ENV` entry.
- `listTrackedFiles(repoPath: string): Set<string> | null`; successful empty listing means empty scope, failure means filesystem fallback.
- Submodule gitlinks (mode `160000`) are skipped; staged new files are included; tracked vendored/generated files remain eligible.
- Keep glob options, ignore precedence, `dot: false`, size filtering, sorting, `.d.ts` deduplication and existing callback sequencing except narrowing its input.
- No wiki reconciliation, freshness reasons, omc-side path filters, host update, or owner project reset.
- Red before green; tests run or fail loudly, never skip. Selected expensive documentation E2E requires the user's explicit decision.
- Work only in isolated checkouts. Never edit the installed dependency or copy/delete project knowledge directories.
- `/omc:implement` authorizes clean unpublished commits. Fork publication/merge is a later authorization boundary; never substitute a local-only SHA for a remotely fetchable release pin.
- Section 5.2's “Exactly two omc files” describes runtime changes; section 7 explicitly requires the two E2E edits below. Plan/ledger are lifecycle artifacts, not new runtime scope.

## Review Focus

- Git roots below the repository root: only cwd-relative tracked paths enter scope (Task 1 test).
- Empty index versus failed git: empty set must never trigger fallback (Tasks 1–2 tests).
- Tabs/newlines/non-ASCII path characters: split only NUL records and the first metadata tab (Task 1 test).
- Symlink directories and nested clones: descendants absent from the parent index stay excluded (Task 2 test).
- Discovery metadata and read-only callers: callback sees the intersection before size filtering; `quiet: true` emits no scope line (Task 2 test).

## Evidence and boundaries

The omc knowledge snapshot is stale: one commit behind `origin/main`, wiki missing. Its suggested refresh is `omc watch --once --enable-documentation` in `/Users/chriphus/OpenSource-Projects/oh-my-clanker`; this plan does not run it. Current source, not stale graph facts, establishes omc behavior below.

Each major section was pressure-tested through `/omc:explain`: context maps in both worktrees agree; `refresh_knowledge` context and integration query located current-source checks; pinned dependency contexts for `walkRepositoryPaths` and `IncludeExtractor.discoverIndexableFiles` confirm scan, drift and Dart callers and the independent include enumeration. Dependency ensure was a cached no-op. The fork graph is current at `24af4f6006f5ee05ebbeeb187bba4c6186d2d5ce`; generated dependency docs are absent (run `omc dependency watch` to backfill the LLM docs).

Source spectrum: unavailable — repository origin is not on the Kraken GitLab; Spectrum indexes only Kraken repositories.

The helper's required `Set | null` result cannot carry differentiated failures. Use the accurate generic fallback reason `git ls-files unavailable` in the scope line; do not claim every command failure proves the root is not a worktree. No additional public API or diagnostic subprocess is needed.

## File map

Fork paths below are relative to the isolated clone root `/private/tmp/omc-tracked-wiki-gitnexus-20261007`, with TypeScript package in `gitnexus/`:

- `gitnexus/src/storage/git.ts`: tracked-path helper beside existing git path utilities.
- `gitnexus/src/core/ingestion/filesystem-walker.ts`: intersection and scope narration.
- `gitnexus/src/core/group/extractors/include-extractor.ts`: matching intersection and maintenance note.
- `gitnexus/test/unit/git-tracked-files.test.ts`: new real-Git helper tests.
- `gitnexus/test/integration/filesystem-walker.test.ts`: scope fixtures and parity.
- `gitnexus/test/unit/index-content-drift.test.ts`: untracked file excluded from new coverage/additions.

omc paths:

- `tests/e2e/test_e2e_gitnexus.py`: existing real index test gains negative and positive graph controls.
- `tests/e2e/test_e2e_docs_artifact.py`: existing expensive wiki test gains tracked-membership assertion.
- `docker/Dockerfile.e2e`: final published merge SHA and explanatory comment.
- Design record and `.superpowers/sdd/progress.md`: outcome, validation and publication dependency.

### Task 1: Git-backed tracked-path helper

Complexity: medium

**Files:** create `gitnexus/test/unit/git-tracked-files.test.ts`; modify `gitnexus/src/storage/git.ts`.

**Interfaces:** consumes existing `gitPathListExec`; produces exported `listTrackedFiles(repoPath: string): Set<string> | null`.

- [ ] Verify the already-created isolated clone `/private/tmp/omc-tracked-wiki-gitnexus-20261007`: branch `feature/scope-wiki-generation-to-tracked-files`, base `24af4f6006f5ee05ebbeeb187bba4c6186d2d5ce`, origin `https://github.com/chris-husse/GitNexus.git`. It was cloned with `--no-hardlinks`; do not recreate it or touch the installed dependency. Read clone-local contributor instructions before edits.
- [ ] Follow fork AGENTS: impact before symbol edits, detect-changes before commits. For baseline impact use `omc internal gitnexus --git github.com/chris-husse/GitNexus@24af4f6006f5ee05ebbeeb187bba4c6186d2d5ce impact <symbol>` from the omc checkout, explicitly labeling it baseline graph evidence. Establish any local change-detection prerequisite inside the isolated clone only; do not edit or mirror installed knowledge.
- [ ] Prepare independent dependencies using the fork's documented build order: install/build `gitnexus-shared` before `gitnexus`. Do not symlink writable build outputs or dependency trees into the installed clone.
- [ ] Add real temporary Git fixtures covering committed and staged files, gitlink via `update-index --cacheinfo 160000,<commit>,module`, tab/newline/non-ASCII filenames, empty repository, root below repository, non-repository, and missing git using temporarily empty PATH restored in `finally`. Use a real commit for the gitlink object and no global git configuration.
- [ ] Assert `expect(listTrackedFiles(root)).toEqual(new Set(['committed.ts', 'staged.ts', 'tab\tname.ts', 'line\nname.ts', 'é.ts']))`; gitlink absent. Assert subdirectory returns `new Set(['inside.ts'])`; empty repository returns `new Set()`; non-repository and missing git return `null`.
- [ ] Run `npx vitest run test/unit/git-tracked-files.test.ts` from the package and record expected red (missing export).
- [ ] Implement the exact helper with `execFileSync('git', ['ls-files', '--stage', '-z', '--'], { cwd: repoPath, windowsHide: true, ...gitPathListExec })`. Parse NUL records, discard `160000`, retain the path after the first tab without trimming or separator replacement; Git emits POSIX paths. Catch failures and return `null`.
- [ ] Run the new file and existing `test/unit/git.test.ts` if present; expect all selected tests pass. Commit helper and tests together in the fork.

### Task 2: Intersect both walkers and preserve discovery behavior

Complexity: high

**Files:** modify both enumeration sources, `gitnexus/test/integration/filesystem-walker.test.ts`, and `gitnexus/test/unit/index-content-drift.test.ts`.

**Interfaces:** consumes Task 1 helper; preserves `walkRepositoryPaths` and private `discoverIndexableFiles` signatures and return shapes.

- [ ] Add a real temporary git repository fixture with committed `src/keep.ts`, staged `src/staged.ts`, untracked `scratch.ts`, ignored file, tracked dotfile, tracked vendored file, and local submodule populated from a second temporary repository using `git -c protocol.file.allow=always submodule add`. Stage intended files explicitly; never add the untracked negative control.
- [ ] Assert walker path set equals `['src/keep.ts', 'src/staged.ts', 'crates/vendored/kept.ts']` after sorting, and include discovery agrees for this fixture. Access the private method using the test suite's existing pattern or a narrow test-only structural cast; do not export a production API for testing.
- [ ] Add fallback/empty-index cases, a root-subdirectory case, nested untracked clone, tracked missing file, and tracked directory symlink whose target files must not appear through the symlink. Preserve existing non-git fixtures unchanged. For symlink creation failure fail with the prerequisite, never skip.
- [ ] Add assertions that `onPathsDiscovered` receives only tracked eligible paths before size filtering, normal walk emits one scope message with intersected and original glob counts, empty listing explicitly reports zero tracked files, fallback names `git ls-files unavailable`, and `quiet: true` stays silent. Reuse existing logging/progress spies.
- [ ] Add drift case using real git and a recorded committed file: an added untracked on-disk file produces no `added` drift and is absent from the new scan coverage. Preserve pre-change recorded-path recovery semantics.
- [ ] Run `npx vitest run test/integration/filesystem-walker.test.ts test/unit/index-content-drift.test.ts`; record red from untracked/submodule presence before production changes.
- [ ] Import the helper in both sources; keep glob unchanged, intersect immediately after enumeration and existing path normalization, before callback/stat. Do not normalize Git filenames by replacing literal backslashes. Preserve all other filters and the enumeration-error throw. Add the maintenance bullet documenting the tracked intersection.
- [ ] Narrate through `warnLargeFileSkip` guarded by `!options.quiet`; use tracked-count/original-count scope text and generic failure reason above. Keep fallback silent in include discovery to avoid duplicate walk announcements.
- [ ] Run `npx vitest run test/unit/git-tracked-files.test.ts test/integration/filesystem-walker.test.ts test/unit/index-content-drift.test.ts test/unit/filesystem-walker-order.test.ts test/integration/ignore-and-skip-e2e.test.ts`; expect green. Run package test/typecheck/build commands required by the fork and available formatting check, plus `git diff --check`. Investigate actual failures; never rewrite unrelated tests to conceal changed semantics.
- [ ] Commit tests and production changes in the fork; record full SHA, root, commands and results for review. No push or PR in this phase.

### Task 3: omc real-index and wiki artifact regressions

Complexity: high

**Files:** modify `tests/e2e/test_e2e_gitnexus.py` and `tests/e2e/test_e2e_docs_artifact.py`.

**Interfaces:** consumes `make_work_repo`, `run_in`, existing container fixture and real GitNexus CLI; produces artifact assertions without another container or LLM call.

- [ ] Before `/omc:index` in `test_index_then_explain_on_real_repo`, write `/repo/scope-untracked.py` and add a local submodule at `/repo/scope-submodule` from `make_work_repo`. Ensure its source has a committed indexable `.py` file. Use `protocol.file.allow=always` on that one add command. Do not commit `/repo` or move HEAD; assert each setup command succeeds.
- [ ] After indexing query `MATCH (f:File) RETURN f.filePath` through `omc internal gitnexus cypher` in `/repo`, parse its JSON and assert the positive control `src/omc/gitnexus.py` exists, `scope-untracked.py` is absent and no path starts `scope-submodule/`. Assert parsed result is nonempty and correct shape; do not silently accept malformed output. Preserve metadata/lastCommit and judge assertions.
- [ ] Run the existing targeted test against the old pin via the repository's E2E selector mechanism; record red from actual negative-control File nodes (not a token, Docker or setup error). Since pytest stops at the first failure, report both unexpected path classes in one assertion or inspect the path set.
- [ ] In the expensive artifact test after `/omc:document`, load `/repo/.gitnexus/wiki/meta.json`, flatten its module `moduleFiles` according to actual schema, collect `git -C /repo ls-files -z`, and assert `set(module_paths) <= set(tracked_paths)`. Keep this assertion before syncing artifacts back. No extra LLM call. Test the assertion itself against a captured/minimal metadata artifact containing an untracked path before accepting the edit; expensive live tier remains unselected unless expressly authorized.
- [ ] Run `/omc:check` and capture `OMC_STAGE`; expected passed true. Local fork green integration evidence can be obtained with an ephemeral test-only image/context overlay that builds the exact fork commit; label this as local evidence, not proof of the standard published pin build. Do not commit an overlay or weaken Docker's approved-source contract.
- [ ] Commit E2E changes with a ledger entry explicitly recording that standard green depends on Task 4. Do not claim implementation complete or invoke final handoff while that required validation remains blocked.

### Task 4: Release pin and required final gates

Complexity: simple

**Files:** modify `docker/Dockerfile.e2e`, design outcome notes and build ledger.

**Interfaces:** consumes a remotely available fork **merge commit** containing Tasks 1–2; produces immutable `ARG GITNEXUS_REF=<full-merge-SHA>`.

- [ ] Before any publication decision, prepare reviewable fork commits, full diff, successful fork validation, local regression evidence, and an exact proposed Dockerfile comment/pin diff using the concrete fork SHA as a clearly marked candidate artifact outside tracked product files. Do not commit a fabricated merge hash.
- [ ] Authorization boundary: obtain the later publication authority needed for fork PR/merge, or wait for the owner to merge the prepared fork branch. The design calls for a merged fork commit; `/omc:implement` alone does not authorize pushing/merging it. Ask only after all independent implementation above is concrete and reviewed. Alternative is explicit amendment to accept an unpublished local-evidence handoff with release pin deferred; never infer that amendment.
- [ ] Once the real merged SHA exists, verify it contains the reviewed fork diff, replace the Dockerfile ARG and add the tracked-only walker/design filename to its comment. Record fork PR/merge evidence and retained owner migration command in design outcome notes. Keep omc runtime code untouched.
- [ ] Run `/omc:check`, then `/omc:build`, then `/omc:verify` using the standard Dockerfile; consume each single `OMC_STAGE` verdict. This proves the image fetches/builds the new pin and the real negative/positive graph assertions pass. Follow the bounded stage-fix rule on failure; no progression past red.
- [ ] Run the expensive artifact test only if the user selects it; otherwise report it unselected, never as a passed test. Standard verify remains required.
- [ ] Review complete changes against the design, commit the validated omc changes cleanly, and hand off the unpublished branch with validation evidence and the prescribed implementation continuations. Host `omc update` and project reset remain owner rollout actions.

## Self-review

All design decisions D1–D8 map to Tasks 1–4. The two repositories are one dependent deliverable: the consumer cannot use the behavior before the fork commit is available. The helper/filter bodies are narrow, but Task 2 crosses two implementations and metadata callers, and Task 3 spans Docker/LLM artifact contracts, so those combined tasks are high complexity. No model identifiers are stored. Interface names match the source. The only unresolved critical dependency is authorized fork publication/merge before the final pin and standard green E2E; ordinary implementation choices require no user approval.
