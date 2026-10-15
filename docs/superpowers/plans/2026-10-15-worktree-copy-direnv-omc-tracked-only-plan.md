# Worktree Copy, Cut Failure, and Direnv Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed worktree cut stops `omc design`, new worktrees copy every gitignored file except `.gitnexus/` and `target/`, and provider sessions start with their direnv environment.

**Architecture:** Keep the `wt` and `direnv` process boundary in `ToolContext`. Choose the worktrunk command before invoking it, inspect Git state before and after a failed cut, and remove only resources this invocation created. Let Worktrunk perform the ignored-file copy in a blocking hook; keep only `.omc/docs` in omc's later snapshot mirror. Prefix provider argv centrally in `session.py`, with direnv availability supplied by each launcher.

**Tech Stack:** Python 3.11+, pytest, uv, Worktrunk, direnv, Git, TOML, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-15-worktree-copy-direnv-omc-tracked-only-design.md`

**Planning directive:** "Every implementation task in the plan carries a `Complexity: simple | medium | high` line, choosing one value. Label multi-file, architecturally tricky or ambiguous coding work `high`; choose `simple` for narrow routine work and `medium` otherwise. The plan stores complexity, never model ids."

## Global Constraints

- This plan implements the committed design. Its detailed §1 cleanup rule controls: remove a worktree created by this invocation even when its branch pre-existed. The contrary sentence in §8's risk summary is stale.
- The copy set is every gitignored path except `.gitnexus/` and `target/`. Preserve `.env`, `.secrets/`, `kgrpc_modules/`, agent links, and `.omc`; Worktrunk preserves a symlinked `.omc` as a symlink and copies a directory `.omc` as a directory. Copying ignored files never stages or commits them.
- Final-review ruling: the starter copies ignored files before `direnv allow .`, so an ignored `.envrc` is present when approval runs. This supersedes the design record's original hook order; the session must receive that environment.
- `ensure_wt_config` creates only when absent. Existing project configs are analyzed, never rewritten. Existing worktrees' `.gitnexus/` copies remain untouched.
- Use `ToolContext` for every production subprocess. No `shell=True`; preserve the five positional parameters of `run_headless` and the `OMC_REBASE_MAIN` verdict schema.
- Write each regression test before its implementation, run it to observe the expected failure, then make it pass. Missing `wt` or `direnv` in a selected test tier is `pytest.fail` with an install command, never a skip. Restricted-PATH stubs use shell builtins or absolute executables.
- After each omc task, the conductor runs `/omc:check` (this project's `just check`); the implementation milestone runs `/omc:build` (`just build`) then `/omc:verify` (`just e2e-tests`). No `omc install` or `uv tool install` from this checkout.
- `README.md` is generated. Update `.claude/skills/regenerate-readme/SKILL.md` before running its recipe; never hand-edit README content.
- **Execution scope:** Tasks 1–4 are in this omc branch and its `/omc:check` loop. Task 5 is a separate commit in an isolated worktree of `/Users/chriphus/Projects/hummingbird-wt`, which is outside this worker's writable roots and outside the omc gate loop. Do not edit its dirty primary checkout or its existing untracked files. The omc branch can build and reach handoff without Task 5; complete Task 5 separately before claiming the two-repository rollout is finished.

## File Map

| Area | Files and responsibility |
|---|---|
| Cut and launch | `src/omc/worktree.py` selects one `wt` command, parses the result and cleans new Git resources; `src/omc/start.py` shares selection in dry-run and propagates errors. |
| Copy | `src/omc/wtconfig.py` owns the starter and copy-step detection; `.config/wt.toml` is this repo's instance; `skills/check-wt-config/SKILL.md` describes the expected shape. |
| Knowledge | `src/omc/mirror.py` owns mirror scope; `src/omc/distribution/AGENTS.md`, `skills/rebase-main/SKILL.md`, `skills/finish/SKILL.md`, the README recipe and generated README explain it. |
| Direnv | `src/omc/probe.py` tests availability; `src/omc/session.py` composes argv; `src/omc/{start,implement,review}.py` pass the fact for interactive, headless and dry-run paths. |
| Evidence | Existing `tests/unit/test_*.py` files cover exact behavior; `tests/unit/test_worktrunk_real.py` drives real `wt` and `direnv` in isolated Git repos; `.github/workflows/ci.yml` provisions both for `just check`. |
| Other repository | Hummingbird's `.config/wt.toml` drops `target` copying; its two `scripts/wt-*.py` target-fixup scripts become obsolete and are removed in that repository only. |

## Review Focus

1. A zero-exit `wt` response with malformed or pathless JSON must still fail and clean only newly created Git resources (Task 1 test).
2. A failed `git worktree remove` or `git branch -D` must be reported without replacing the original hook failure (Task 1 test).
3. A branch name that prefixes another branch must not make the porcelain parser remove the other worktree (Task 1 test).
4. A config that merely mentions `copy-ignored` in an unrelated value or comment must remain `suspicious`; both hook tables and their string/table forms count only when they actually run the step (Task 2 test).
5. A blocked `.envrc` must fail with direnv's diagnostic; with no local `.envrc`, an allowed parent exports its environment and no parent leaves the launch unchanged (Task 4 test).

---

## 1. Fail a Broken Cut

**Pressure test via `/omc:explain`:** `start.run_start` consumes `worktree.create_worktree`; dry-run currently duplicates its `--create` argv. Give both paths one command selector. `tests/unit/test_start.py:_make_git_stub` currently treats unknown Git verbs as success, so make `show-ref` mean absent by default. The graph indexes the primary's `main` snapshot; current source confirms these call sites.

### Task 1: One Worktrunk Invocation and Precise Cleanup

**Complexity: high**

**Files:** Modify `src/omc/worktree.py`, `src/omc/start.py`, `tests/unit/test_worktree.py`, `tests/unit/test_start.py`, `.github/workflows/ci.yml`; create `tests/unit/test_worktrunk_real.py`.

**Interfaces:** `worktree.switch_command(ctx: ToolContext, branch: str, base: str | None) -> tuple[list[str], bool]` returns full `wt` argv and whether `refs/heads/<branch>` existed before this invocation. `create_worktree(ctx, branch, base=None) -> str` returns a path or raises `OmcError`. An internal exact-branch parser of `git worktree list --porcelain` returns that branch's worktree path or `None`. `start.run_start` calls the selector for dry-run and lets creation errors propagate before notification wiring or session launch.

- [ ] **Step 1: Write failing tests.** Replace `test_create_worktree_retries_without_create` with a hook-failure test: `show-ref` absent, one exact `wt switch --create feature/x --base origin/main --no-cd --yes --format=json` call, stderr `✗ pre-start command failed: exit status: 7`, `OmcError` containing branch, `exit 1`, stderr and `wt config state logs`, then exact `git worktree remove --force <new-path>` and `git branch -D feature/x`. Add successful existing-branch selection, failed existing branch/worktree preservation, failed existing branch/new worktree removal, failure before any new resource, malformed/pathless success JSON, OSError, cleanup failure and branch-prefix collision cases. In `test_start.py`, assert stubbed `omc design` exits 1 without notification/session calls and dry-run prints the re-entry argv when the branch exists; teach `_make_git_stub` `show-ref) exit 1` and empty `worktree list` output.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest -q tests/unit/test_worktree.py tests/unit/test_start.py`; the new assertions fail because the current code retries `wt` and returns `None`.
- [ ] **Step 3: Implement the command and cleanup.** Probe `git show-ref --verify --quiet refs/heads/<branch>` and exact worktree presence before the one `wt` call; if the pre-state cannot be established, fail before calling `wt`. For an existing branch use `wt switch <branch> --no-cd --yes --format=json`; otherwise add `--create` and optional `--base`. On nonzero exit, OSError or bad/pathless JSON, capture the original error; re-list worktrees and remove a newly appeared path with `git worktree remove --force <path>`, then delete the branch only if it did not exist before and now exists. Guard each operation by observed state, narrate cleanup failures, and always raise the original `OmcError` with stderr tail and log hint. Delete the `path is None` branch in `start.run_start`; use `switch_command` for its dry-run output.
- [ ] **Step 4: Add real-tool evidence.** In `tests/unit/test_worktrunk_real.py`, use a throwaway committed Git repo with private `HOME` and `XDG_CONFIG_HOME` (and private XDG state/data/cache). Resolve real `wt` with `shutil.which`; `pytest.fail` with an install command if absent. First prove red on `pre-start = "exit 7"`: real `wt` leaves branch/worktree but old `create_worktree` returns a path on retry. After the fix, assert `OmcError`, no registered worktree or branch, and no copied ignored files staged. Keep tests short and independent of the developer's Worktrunk config.
- [ ] **Step 5: Provision CI prerequisites.** Before `just check`, install `direnv` with `apt-get` and Worktrunk from the pinned v0.79.0 Linux musl release archive with its sibling SHA256 check, adapting the architecture selection from `docker/Dockerfile.e2e`. Keep `wt` and `direnv` on `PATH`; do not add a conditional skip. The real-tool file's prerequisite failure should identify the same install route.
- [ ] **Step 6: Verify and commit.** Run the focused pytest command, then `just check`; expect all pass. Commit only Task 1's tracked files, including tests and CI setup. Do not `git add -A` in a worktree containing copied ignored files.

## 2. Copy Rules and Knowledge Snapshot

**Pressure test via `/omc:explain`:** `watch.run_watch` is the only `ensure_wt_config` caller, so the config skill must not promise seeding by `omc design`. `internal._rebase_main` consumes `mirror_snapshot`, while GitNexus queries use the primary graph. The README recipe currently instructs generation of a copied-graph diagram; update that recipe before regenerating. Its output is the generated artifact, not the source of policy.

### Task 2: Blocking Copy with Two Excludes

**Complexity: high**

**Files:** Modify `src/omc/wtconfig.py`, `.config/wt.toml`, `skills/check-wt-config/SKILL.md`, `tests/unit/test_wtconfig.py`, `tests/unit/test_worktrunk_real.py`; preserve skill-contract needles in `tests/unit/test_plugin_manifests.py`.

**Interfaces:** `WT_TEMPLATE` is the exact content of `.config/wt.toml`. `_has_copy_ignored(text: str) -> bool` recognizes a `wt step copy-ignored` command in `pre-start` or `post-start`, each as a string or named-step table; `ensure_wt_config` retains `created | ok | suspicious` and never edits an existing file.

- [ ] **Step 1: Write failing tests.** Assert TOML parses, `pre-start` orders guarded submodule update, blocking `wt step copy-ignored` and guarded `direnv allow .`; assert `exclude == [".gitnexus/", "target/"]`, no `post-start`, and repo config equals the template. Assert copy detection for a top-level `pre-start` string, `[pre-start]` table and legacy `[post-start]` string/table; absent or incidental mention is `suspicious` and remains unedited. Extend the real-tool fixture with no `.envrc`: cut succeeds and retains ignored `.env`, `.secrets/` and agent symlink while excluding `.gitnexus/` and `target/`; test primary `.omc` as both symlink and directory, and assert `git status --porcelain` stays clean.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest -q tests/unit/test_wtconfig.py tests/unit/test_worktrunk_real.py`; template/detection/copy assertions fail against the old post-start template.
- [ ] **Step 3: Implement.** Replace the template and this repo's config with design §2's excludes and the final-review copy-before-allow order; remove `[post-start]`. Update `_has_copy_ignored` to inspect both parsed hook locations and their supported shapes, without treating arbitrary comments/values as commands. Correct its module docstring and the check-wt-config skill: `.gitnexus/` and `target/` excludes are expected, excluding `.omc/docs/` is harmful, copy can be in either hook, and only `omc watch` seeds an absent starter. Preserve `omc internal wt-template`, `.config/wt.toml` and `never edit` wording pinned by skill tests.
- [ ] **Step 4: Verify and commit.** Run the focused tests plus `uv run --frozen pytest -q tests/unit/test_plugin_manifests.py -k check_wt_config`, then `just check`; expect pass. Commit this task's tracked config, skill, code and tests.

### Task 3: Mirror Docs Only and Regenerate the README

**Complexity: high**

**Files:** Modify `src/omc/mirror.py`, `tests/unit/test_mirror.py`, `tests/unit/test_internal.py`, `src/omc/distribution/AGENTS.md`, `skills/rebase-main/SKILL.md`, `skills/finish/SKILL.md`, `.claude/skills/regenerate-readme/SKILL.md`, generated `README.md`; retain `tests/unit/test_plugin_manifests.py` and `tests/unit/test_cli.py` guards.

**Interfaces:** `SNAPSHOT_DIRS == (".omc/docs",)`. `mirror_snapshot` and the `OMC_REBASE_MAIN` JSON schema stay unchanged; `synced` and `shared` never list `.gitnexus`. Existing `.gitnexus` content in a worktree is left exactly as found.

- [ ] **Step 1: Write failing tests.** Update `test_mirror_snapshot_syncs_known_dirs_only` and related cases to assert only `.omc/docs` is synchronized, an existing worktree `.gitnexus` file remains untouched, and a missing docs source yields no sync. Add a primary `.omc/docs/page.md` fixture to `test_rebase_main_rebases_and_mirrors_snapshot`, then assert `synced == [".omc/docs"]`, the verdict still has `shared`, and old `.gitnexus` content survives. Keep the shared `.omc` symlink test. Add a focused README guard that the generation recipe's knowledge-flow slot describes primary `.gitnexus/` and worktree `.omc/docs/` without a copied graph.
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest -q tests/unit/test_mirror.py tests/unit/test_internal.py tests/unit/test_cli.py -k 'mirror or rebase_main or readme'`; new assertions fail because `.gitnexus` is mirrored and the recipe is stale.
- [ ] **Step 3: Narrow the mirror and source prose.** Change only `SNAPSHOT_DIRS` and its docstring in `mirror.py`; retain `mirror_dir` and `_sync_tree`. Correct the distributed behavior layer and rebase/finish skills to say the graph stays primary and `.omc/docs` is the worktree knowledge snapshot. Keep their `OMC_REBASE_MAIN`, `rc 3`, `conflict`, `knowledge` and finish-order needles. Update the README recipe's watch copy claim, knowledge diagram instructions, and Development worktrunk row to exclude `.gitnexus/` and `target/`; explicitly route explain to the primary graph and rebase-main to docs only.
- [ ] **Step 4: Regenerate README from its updated recipe.** Read its required sources and frozen CLI help; render the complete `README.md` with four Mermaid fences and retained sections, including the requested copy rundown. Run the recipe's `uv run --frozen pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'`. Check `git diff -- README.md .claude/skills/regenerate-readme/SKILL.md` and `git diff --check`.
- [ ] **Step 5: Verify and commit.** Run the focused mirror/internal/CLI/skill tests, then `just check`; expect pass. Commit code, tests, source guidance and generated README together.

## 3. Start Sessions Under Direnv

**Pressure test via `/omc:explain`:** `session_plan` feeds all interactive shell builders and has no I/O; `run_headless` directly calls `ctx.run`. Each of design, implement and review passes through both functions. Probe once in each launcher via `ToolContext` and pass the boolean through a keyword, preserving the established positional call shape; no shell-specific patch is needed.

### Task 4: Probe Direnv and Prefix Provider Argv

**Complexity: high**

**Files:** Modify `src/omc/probe.py`, `src/omc/session.py`, `src/omc/start.py`, `src/omc/implement.py`, `src/omc/review.py`, `tests/unit/test_probe.py`, `tests/unit/test_session.py`, `tests/unit/test_start.py`, `tests/unit/test_implement.py`, `tests/unit/test_review.py`, `tests/unit/test_shells.py`, `tests/unit/test_worktrunk_real.py`.

**Interfaces:** `has_direnv(ctx: ToolContext) -> bool` uses `tool_version(ctx, ["direnv", "version"])`. Add keyword `direnv: bool = False` to `session_plan` and `run_headless`; the latter's five positional arguments stay unchanged. When true, `session_argv` and headless argv start `['direnv', 'exec', '.', *provider_argv]`; when false, argv remains byte-identical. The launcher resolves availability once for its dry-run or real path, then supplies it to both interactive and headless composition.

- [ ] **Step 1: Write failing tests.** In `test_probe.py`, stub `direnv version` on restricted PATH and assert true; missing command gives false. In `test_session.py`, assert exact prefix and unchanged provider tail for both session and headless, plus default-false identity. In shell tests, assert fish `-C` executes `cd <wt>` before `direnv exec . <provider>` and zsh/bash/sh do likewise. In launcher tests, assert design/implement/review dry-run displays the prefix when a stub direnv is present, and headless passes `direnv=True`; adjust monkeypatched headless recorders to accept the new keyword while preserving five positional arguments. In the real-tool test, use allowed `.envrc` exporting `OMC_TEST_MARK` and a stub provider that prints the marker; then change `.envrc` without allowing it and assert nonzero exit with direnv's blocked diagnostic. Also test no `.envrc` anywhere (unchanged launch) and no local `.envrc` with an allowed parent `.envrc` (marker exported).
- [ ] **Step 2: Observe red.** Run `uv run --frozen pytest -q tests/unit/test_probe.py tests/unit/test_session.py tests/unit/test_shells.py tests/unit/test_worktrunk_real.py`; the prefix, marker and blocked-file assertions fail with the current provider argv.
- [ ] **Step 3: Implement.** Add the probe, then prefix argv centrally in `session_plan` and `run_headless`. Wire `has_direnv(ctx)` into all three launchers after their existing gates, including design dry-run; pass `direnv` to `session_plan` and headless calls. Keep `session_plan` pure and leave `ToolContext` as the only process boundary. Do not alter provider adapters or shell implementations.
- [ ] **Step 4: Verify and commit.** Run the focused probe/session/shell/launcher/real-tool tests, then `just check`; expect pass. Commit code and tests together.

## 4. Separate Hummingbird Repository

**Pressure test via `/omc:explain`:** The omc graph covers this repository, so it cannot validate hummingbird's config. The committed design and read-only inspection of the separate checkout show its own blocking `pre-start` hook, a sub-path `target` exclusion and two scripts used only to repair copied target artifacts. This is a rollout dependency, not a prerequisite for omc's Python build.

### Task 5: Update Hummingbird's Tracked Worktrunk Config

**Complexity: high**

**Execution scope:** This task runs only from a separately writable, isolated worktree of `/Users/chriphus/Projects/hummingbird-wt`, on its own branch and commit. Its primary checkout is dirty and behind `origin/main`; preserve all of its untracked files and do not reset/rebase it. A worker confined to this omc workspace records the dependency and hands it to a worker with access to the hummingbird repository. Do not stage these paths in an omc commit.

**Files in hummingbird only:** Modify `.config/wt.toml`; delete `scripts/wt-sanitize-copied-cargo-target.py` and `scripts/wt-mirror-mtimes.py`. Read that repository's `AGENTS.md` and `.omc/config/AGENTS.md` in full before editing, plus any deeper scoped instructions.

**Interfaces:** The tracked config retains unconditional `git submodule update --init --recursive` and its existing order, then guarded `direnv allow .`, then blocking `wt step copy-ignored`. Its `[step.copy-ignored]` exclusion is exactly `[".gitnexus/", "target/"]`; there are no references to either target-fixup script in the active hook.

- [ ] **Step 1: Isolate work.** Fetch `origin/main` in the hummingbird repository when network access is available; create a new isolated worktree/branch from that updated ref. Do not move the dirty primary's HEAD, overwrite its untracked files, or work from its behind-main snapshot. If fetch is unavailable, stop this separate task with the exact failure rather than silently using stale `origin/main`.
- [ ] **Step 2: Demonstrate red.** In the isolated checkout, parse `.config/wt.toml` with `python3`/`tomllib` and assert whole-directory excludes are `['.gitnexus/', 'target/']`, no script names remain in `pre-start`, and the two scripts are absent; the current config fails these assertions. Use the real `wt` in a disposable Git repo seeded with this config, an allowed `.envrc`, and ignored `.omc`, `.env`, `.gitnexus/`, `target/`; check that its cut omits only the two excluded directories. Do not run a cut that could modify the dirty primary checkout.
- [ ] **Step 3: Implement.** Replace the old sub-path exclude with the two whole-directory patterns, drop both target-fixup invocations and their COPS-992/COPS-1043 explanatory comments, update the header's warm-`target` claim, then delete the two obsolete scripts. Keep submodule/direnv/copy order and the tracked `.envrc` assumption.
- [ ] **Step 4: Verify and commit there.** Repeat the TOML and disposable real-`wt` checks; assert `.omc` keeps its symlink or directory shape, `.env` survives, `target/` and `.gitnexus/` do not copy, and no ignored file enters `git diff --cached`. Follow hummingbird's own applicable gates for this config-only change and commit only its three tracked paths on its isolated branch. Record that separate commit for rollout; do not push or merge through the omc branch.

## Milestone and Handoff

- [ ] After Tasks 1–4 have green per-task `/omc:check` verdicts, invoke `/omc:build` (`just build`) then `/omc:verify` (`just e2e-tests`) in order; Docker and the configured provider token are required. The existing `tests/e2e/test_e2e_start.py` exercises a real `wt` cut and branch re-entry; the new unit-tier real-tool tests cover local copy and direnv effects. No Codex-specific gate is added because provider code is untouched.
- [ ] Commit the plan with the omc implementation handoff, keep the omc working tree clean, and report the separate hummingbird commit as pending or completed independently. Only after that separate task is committed should the two-repository user request be called complete.
