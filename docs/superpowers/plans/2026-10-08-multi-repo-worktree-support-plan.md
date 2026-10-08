# Multi-repository Worktree Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a shared slug a persistent workspace of ordinary per-repository worktrees, design records, lifecycle runs, and merge-verified closure.

**Architecture:** A focused `workspace.py` owns deterministic discovery, the locked ledger, Git/Worktrunk inspection, and add/list/close verdicts. Existing helpers gain optional directory parameters, while skills retain design and child-session orchestration. M1 delivers plumbing and design integration; M2 adds implement/audit proxying on the same branch.

**Tech Stack:** Python, pathlib/json/fcntl/tempfile, ToolContext, Git, Worktrunk, Markdown skills, pytest, Docker E2E.

**Spec:** `docs/superpowers/specs/2026-10-08-multi-repo-worktree-support-design.md`

## Global Constraints

- Every repository carries the same slug and its **own** configured branch prefix and base branch.
- Only a named second modification target engages workspace machinery; ordinary single-repository work keeps its behavior.
- Ledger: `~/.omc/workspaces.json` under `ToolContext.home`, flock around read-modify-write, unique temporary file plus atomic rename, lock-free readers.
- Repositories are stored in registration order; ordered walks visit dependencies in that order and the master last.
- `OMC_WORKSPACE` is exactly one single-line JSON verdict on stdout; progress is stderr. Exit codes: 0 ok, 1 error, 2 refusal, 3 bail.
- ToolContext remains the only runtime subprocess/env/network boundary. Argv lists only; no forge API, no daemon, no automatic install, no hand-editing knowledge snapshots.
- No topological merge scheduler, cross-repository stage gate, static dependency config, or master plan document for generated workspaces.
- The generated master record has a one-time `## Repositories` snapshot and `## Cross-repo contract`; dependency records are committed first, master last.
- Repositories without `.omc/` are retried or dropped as “handled by hand”; never silently treated as plain repositories.
- Write tests first, observe expected failure, then implement. No skips. Restricted-PATH stubs use shell builtins or absolute tool paths.
- Every task is followed by conductor `/omc:check`. Final green check → `/omc:build` → `/omc:verify`; follow the bounded fix-forward rule. Workers do not publish.
- `README.md` is generated through `.claude/skills/regenerate-readme/SKILL.md`, never edited as independent source.

## Review Focus

- Two masters using the same slug, or two checkout paths for the same remote, must not accidentally attach to one another; Task 2 pins identity and Task 3 pins membership.
- A sibling name that is a worktree, a destination containing spaces, or an occupied non-repository clone destination must not cause wrong-directory edits; Tasks 1 and 3 pin these inputs.
- Missing upstream, malformed Worktrunk output, or a deleted worktree must never look pushed or completed; Tasks 3 and 4 pin these failures.
- A removal failure midway through close must retain the failing and later entries, with the master last; Task 4 pins resumability and fetch failure.
- Design-only commits and dependency child sessions must not count as completed implementation or recursively launch dependencies; Task 7 pins the resolved completion rule and role guard.

---

## Pressure-test findings and decisions

The Plan worker invoked `omc:explain` via the context-map resolver and GitNexus ensure/query/context/impact commands before source inspection. The graph reports current `main` at `da87c41593001b0570fdc8353b2f537191b11203`. Direct source checks below govern details; generated workflow docs contain stale claims that audit never commits and therefore are not authoritative on audit scope.

| Major section | Evidence and refinement |
|---|---|
| Directory-aware helpers and ledger (Tasks 1–2) | `worktree.py:create_worktree` has one caller, `start.py:run_start`; preserve defaults and add keyword-only directory arguments. `config/resolve.py:project_config` has a broad caller graph, so preserve no-argument behavior. `dependency.py:update_manifest`/`save_manifest` supply the flock and atomic-save pattern; use a separate workspace file and schema, not dependency-index metadata. |
| Add/list/close (Tasks 3–4) | `wtconfig.py:find_design_record(ctx, root, slug)` already accepts a root and returns `RecordVerdict`; do not create another record validator. Observed real Worktrunk schema 2 has `repo.forge`, `items[*].branch`, `worktree.path`, and optional `upstream.{remote,branch,ahead,behind}`. Never substitute `default_branch.ahead` for upstream ahead. Worktrunk is not indexed as an external dependency; this plan relies on its observed CLI contract, not inferred internals. |
| Design and finish skills (Task 5) | `skills/design/SKILL.md` already separates worker writing from main-session commits. Insert workspace preparation before worker dispatch, dependency explain before master hardening completes, and narrowed dependency records after master hardening. `skills/finish/SKILL.md` offers closure only after push; preserve the three follow-ups and role-key the close command. |
| E2E (Task 6) | `harness.py:make_work_repo(container, path)` supports distinct paths and local bare origins; `configure_omc` also performs provider model validation, so “no LLM” means no lifecycle/model task in this test, not no existing configuration probe. Use the ordinary container, not golden-state forks. |
| Child lifecycle (Task 7) | `implement.py:run_implement` and `review.py:run_review` reuse `session.py:run_headless`, which captures output until exit. Keep them unchanged. Their session names are `<slug>-implement` and `<slug>-audit`; repeated launches make name-based resume ambiguous, explicitly documented in `implement.py`. Master-only Phase 0.5 is mandatory because list exposes the full workspace inside dependencies too. |

Source spectrum: unavailable — repository origin is not on the Kraken GitLab; Spectrum indexes only Kraken repositories.

### Resolved implementation and closure decisions

The user accepted both recommendations after the implementation review exposed two conflicts. For M2, require a clean dependency tree, its committed slug-specific implementation plan, and a later commit changing tracked files outside design/plan documents before skipping implementation. A design-only or plan-only commit must fail that predicate. Store no new completion field in the ledger. For closure, dependency finish keeps its registered worktree and directs closure to the master; only single-repository finish retains local removal. Amend the relevant design prose to record these approved corrections.
Two implementation clarifications require no new product choice: Phase 0.5 runs only when `current_role == "master"`; and `add` accepts an optional `--path <checkout>` so a location retry retains its target name/URL. Direct existing checkout paths are accepted as targets too.

## File Responsibilities and Shared Contracts

- `src/omc/workspace.py` (new): ledger IO, workspace identity/membership, repository resolution, Worktrunk JSON interpretation, add/list/close, verdict rendering.
- `src/omc/worktree.py`, `src/omc/wtconfig.py`, `src/omc/config/resolve.py`: backward-compatible root/cwd seams only.
- `src/omc/internal.py`: `workspace add TARGET [--path CHECKOUT] | list | close` dispatch; no workspace algorithms.
- `skills/{workspace,design,start,plan,finish,implement,audit}/SKILL.md`: user interaction, design workers, child-session sequencing and rendering.
- `src/omc/distribution/AGENTS.md`, `.omc/config/AGENTS.md`, `.omc/skills/{explain-context,review}/SKILL.md`: behavior and machine-contract listings.
- `tests/unit/test_workspace.py` (new): ledger, resolution, listing, closure tests. Existing helper/internal/manifest suites cover their own seams.
- `tests/e2e/test_e2e_workspace.py` (new): real Git/Worktrunk workspace chain under Docker.
- `.claude/skills/regenerate-readme/SKILL.md` and generated `README.md`: complete user-facing workspace description.

Keep the persisted schema simple: `{"version":1,"workspaces":{<workspace-id>:{"master":<repo-key>,"slug":<slug>,"repositories":[<entry>, ...]}}}`. A workspace ID is an unambiguous JSON encoding of `[master_repo_key, slug]`; do not concatenate with a delimiter that remote URLs can contain. An entry has `key`, `primary`, `worktree`, `branch`, `base`, and `role`. Paths are canonical absolute paths. Persist no derived design/push status in M1.

The list success payload is `{"ok":true,"slug":str,"current_role":"master"|"dependency"|null,"master_worktree":str|null,"repositories":[...]}`. No workspace returns an empty list and null role/master path. Enriched entries add `record` (`RecordVerdict.to_json()`), `upstream` (object or null), `ahead`/`behind` (integers or null), and `compare_url` (string or null). Unknown is null, never fabricated zero. Add success additionally names `repository`; close success names `removed` and the remaining `repositories`. Failures carry `ok:false`, `reason`, `message`, and identifying fields when available.

### Task 1: Make repository helpers directory-aware

Complexity: high

**Files:** Modify `src/omc/worktree.py`, `src/omc/wtconfig.py`, `src/omc/config/resolve.py`; test `tests/unit/test_worktree.py`, `tests/unit/test_wtconfig.py`, `tests/unit/test_config_store.py`.

**Interfaces:** Produce `repo_root(ctx, root: str | Path | None = None) -> str | None`, `primary_root(ctx, root: str | Path | None = None) -> str | None`, `project_config(ctx, root: str | Path | None = None) -> ProjectConfig`; `sync_base(ctx, base: str, *, cwd: str | Path | None = None) -> bool`; `create_worktree(ctx, branch: str, base: str | None = None, *, cwd: str | Path | None = None) -> str | None`. Thread `cwd` through private `_switch`.

- [ ] **Write failing tests** `test_create_worktree_explicit_cwd_and_retry`, `test_sync_base_explicit_cwd`, `test_repo_roots_explicit_root`, `test_project_config_explicit_root`. Record argv and cwd independently, including a directory with spaces. Assert:

```python
assert calls[0].argv == [ctx.wt_bin, "-C", str(primary), "switch", "--create", "topic/shared", "--base", "origin/develop", "--no-cd", "--yes", "--format=json"]
assert calls[1].argv == [ctx.wt_bin, "-C", str(primary), "switch", "topic/shared", "--no-cd", "--yes", "--format=json"]
assert fetch.argv == [ctx.git_bin, "fetch", "origin", "develop"]
assert fetch.cwd == str(primary)
assert project_config(ctx, dependency).worktree.branch_prefix == "topic/"
```

  Use existing stub helpers or a ToolContext call recorder; fixtures must define these recorded fields. Existing no-cwd tests must remain unchanged.
- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_worktree.py tests/unit/test_wtconfig.py tests/unit/test_config_store.py -q`; new tests fail on unsupported root/cwd arguments.
- [ ] **Implement the interfaces** with `ctx.run(cwd=...)` for Git and `wt -C <primary>` for Worktrunk. Do not change callers using defaults, config precedence, fetch's best-effort result, or retry semantics.
- [ ] **Run green:** repeat the focused command, then conductor `/omc:check` after review.
- [ ] **Commit** these helper and test files as `feat: support explicit repository roots in worktree helpers`.

### Task 2: Add the workspace ledger and identity helpers

Complexity: medium

**Files:** Create `src/omc/workspace.py`, `tests/unit/test_workspace.py`.

**Interfaces:** Produce `ledger_path(home: Path) -> Path`, `load_ledger(home: Path) -> dict`, `save_ledger(home: Path, data: dict) -> None`, `update_ledger(home: Path, mutate: Callable[[dict], None]) -> dict`; `workspace_id(master_key: str, slug: str) -> str`; `repository_key(wt_data: dict, origin: str) -> str`; `ordered_repositories(workspace: dict) -> list[dict]`.

- [ ] **Write failing tests** for absent-ledger empty schema, round-trip, exclusive lock around fresh read/mutate/save, unique temporary file plus atomic replacement, failed replacement cleanup, malformed schema refusal, ordered repositories, and distinct masters sharing one slug. Core assertions:

```python
assert load_ledger(home) == {"version": 1, "workspaces": {}}
assert ledger_path(home) == home / "workspaces.json"
assert workspace_id("host/owner/a", "same") != workspace_id("host/owner/b", "same")
assert [r["role"] for r in ordered_repositories(workspace)] == ["dependency", "dependency", "master"]
assert repository_key({"repo": {"forge": {"host": "github.com", "owner": "org", "name": "lib"}}}, "ignored") == "github.com/org/lib"
assert repository_key({"repo": {}}, "/work/lib-origin") == "/work/lib-origin"
```

  Mirror existing dependency lock tests without importing its private implementation. Reject unsupported versions and malformed entries explicitly instead of resetting a corrupt ledger.
- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_workspace.py -q`; imports/new behavior fail.
- [ ] **Implement the interfaces** using the dependency manifest pattern. The sibling lock is `workspaces.json.lock`; readers do not lock. Keep credential-excluded origin URL fallback, including exact local bare paths, host/port/path and SSH usernames; credentials must not affect membership identity or appear in output. Explicit URLs may reuse a checkout only after its origin matches under conservative same-transport normalization (host case and optional remote .git/trailing slash; preserve port, SSH user and local path). Require enough forge identity fields before using forge identity.
- [ ] **Run green:** focused tests, then conductor `/omc:check` after review.
- [ ] **Commit** the module and tests as `feat: persist workspace membership atomically`.

### Task 3: Implement workspace add and enriched list

Complexity: high

**Files:** Modify `src/omc/workspace.py`, `src/omc/internal.py`, `tests/unit/test_workspace.py`, `tests/unit/test_internal.py`.

**Interfaces:** Consume Tasks 1–2. Produce `run_add(ctx: ToolContext, target: str, *, path: str | None = None) -> int`, `run_list(ctx: ToolContext) -> int`, `run_workspace(ctx: ToolContext, argv: list[str]) -> int`; internal helpers may stay private. The public runners emit exactly one verdict and return its exit code.

- [ ] **Write failing add tests** using a ToolContext argv/cwd recorder plus real temporary directory topology: sibling first (zero ls-remote/clone calls); derived HTTPS, SCP-style and local bare-origin URL; failed ls-remote → rc 3 `unresolved`; two `.git` directories permit clone; one directory plus any number of `.git` files → rc 3 `not-a-projects-folder`; explicit `--path` allows the user-selected destination; existing target path works; occupied non-repository target refuses without deleting it; absent `.omc/` → rc 2 `not-omc-aware` with checkout; custom prefix/base; repeated add no duplicate and no second worktree cut; master-self and cross-workspace membership collision refuse explicitly. Assert no ledger exists after any failed first add.
- [ ] **Write failing list/dispatch tests** for no workspace, member dependency, unrelated repo with same slug, no upstream, record status success/missing/unclean, custom base, local bare origin, malformed JSON, missing tracked worktree, and one verdict on every usage/error/refusal/bail/success. Pin the payload:

```python
assert result["current_role"] == "dependency"
assert [r["role"] for r in result["repositories"]] == ["dependency", "master"]
assert result["repositories"][0]["upstream"] is None
assert result["repositories"][0]["ahead"] is None
assert result["repositories"][0]["compare_url"] is None  # local origin
assert len([line for line in stdout.splitlines() if line.startswith("OMC_WORKSPACE ")]) == 1
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_workspace.py tests/unit/test_internal.py -q`; workspace behavior is absent.
- [ ] **Implement add resolution and registration.** Derive the master from the current real worktree and its primary, current branch and own config; reject primary-checkout registration and detached/no-prefix branches. Resolve existing sibling primaries before URL derivation; verify guessed URLs with `git ls-remote` before clone. Treat a direct existing path as explicit location; use `--path` for a nonexisting clone destination. On clone failure, report it and preserve user files. Verify `.omc/`, load dependency config, best-effort fetch, use `create_worktree`, then locked registration rechecks identity/idempotence. Never hold the ledger lock while fetching/cloning/cutting. A registered worktree in another master workspace refuses; do not let one branch be owned by two masters.
- [ ] **Implement list enrichment and dispatch.** Match membership by canonical current worktree, current branch/slug, and repository identity, not slug alone. Read each primary with `wt -C <primary> --config-set list.json-schema=2 list --format=json` (real pinned Worktrunk defaults to schema 1), select the registered branch/worktree item, derive record state with `find_design_record`, and order dependencies first. Preserve upstream counts separately from base counts. Build one forge compare URL using recognized provider URL conventions (GitHub `/compare/<base>...<branch>`, GitLab `/-/compare/<base>...<branch>`, Bitbucket `/branches/compare/<branch>..<base>`); URL-encode branch names as path/query data. Unknown forge → null. Missing/malformed inspection must be an explicit failure, not empty success. Dispatch every malformed workspace invocation through its one-verdict error path; update `_USAGE`.
- [ ] **Run green:** focused tests, then conductor `/omc:check` after review.
- [ ] **Commit** as `feat: add and inspect multi-repository workspaces`.

### Task 4: Close merged workspaces in resumable order

Complexity: high

**Files:** Modify `src/omc/workspace.py`, `tests/unit/test_workspace.py`, `tests/unit/test_internal.py`.

**Interfaces:** Produce `run_close(ctx: ToolContext) -> int`, reached from `run_workspace(ctx, ["close"])`. Consume ledger ordering/membership from Tasks 2–3.

- [ ] **Write failing tests** `test_close_dependency_refuses`, `test_close_stops_at_unmerged`, `test_close_resumes_after_partial_success`, `test_close_fetch_failure_retains_entries`, `test_close_remove_failure_retains_entries`, `test_close_master_last_deletes_workspace`, and `test_close_missing_branch_fails_closed`. Assert:

```python
assert (rc, payload["reason"]) == (2, "not-master")
assert (rc, payload["reason"]) == (2, "unmerged")
assert removed_keys == ["dependency-a", "dependency-b", "master"]
assert wt_call.argv == [ctx.wt_bin, "-C", str(primary), "remove", branch]
assert remaining_keys == ["dependency-b", "master"]  # after dependency-b failure
assert workspace_id(master_key, slug) not in load_ledger(home)["workspaces"]  # final success
```

  For each repository assert fetch precedes `git merge-base --is-ancestor <branch> origin/<base>`, which precedes remove. Distinguish rc 1 (not ancestor) from operational Git failure. No removal may run on either failure.
- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_workspace.py tests/unit/test_internal.py -q`.
- [ ] **Implement `run_close`.** Refuse non-master callers before mutation, fetch strictly for closure with `+refs/heads/<base>:refs/remotes/origin/<base>` to refresh the exact ancestry ref regardless of restricted fetch mappings, verify ancestry, then remove. Only after successful removal perform a locked update dropping that entry; retain all later entries. Re-read/recheck entry identity inside each mutation so unrelated workspaces are preserved. Delete the workspace when its master entry drops. Do not use force removal or manufacture merge evidence; a missing worktree/branch is a reported recovery problem, not implicit success.
- [ ] **Run green:** focused tests and conductor `/omc:check` after review.
- [ ] **Commit** as `feat: close workspace worktrees after verified merges`.

### Task 5: Integrate workspace design, reporting, and user entry point (M1)

**Review correction approved:** Dependency finish retains its registered worktree for master workspace closure; single-repository finish keeps local removal. Fix the reviewed `3660352` behavior and amend the design prose accordingly.

Complexity: high

**Files:** Create `skills/workspace/SKILL.md`; modify `skills/{start,plan,design,finish}/SKILL.md`, `src/omc/distribution/AGENTS.md`, `.omc/config/AGENTS.md`, `.omc/skills/{explain-context,review}/SKILL.md`, `tests/unit/test_plugin_manifests.py`.

**Interfaces:** Consume the exact CLI/verdict schema above. Produce a user-facing `workspace` skill supporting `list|close`; no new design-phase skill or Python orchestrator.

- [ ] **Write failing manifest tests** for `workspace` membership/frontmatter, list table columns, close refusal messages, modification-target naming in start/plan, design gate ordering before first write, dependency explain brief/context paths, narrowed dependency record path, whole-record explain/grug pass, dependency-first commits/master-last, role-keyed finish follow-up, and `OMC_WORKSPACE` in every machine-contract listing (including existing project review listing). Assertions use section boundaries and required instructions rather than whole-file snapshots.
- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q`.
- [ ] **Edit the design flow.** Externalize the new steps. After the record gate and before writing, `workspace add` every named modification target; unresolved asks URL, not-a-projects-folder asks destination and retries with `--path`, not-omc-aware asks retry/drop. Only registered targets pass; record dropped targets as handled by hand without storing them as active ledger entries. Skip all workspace calls when no second target exists. Explain workers explicitly `cd` to each dependency, read project instructions and explain-context, and disclose file fallback when index missing. Master Design worker folds findings into the two master sections, hardens normally, then dependency Design workers narrow it with one whole-record explain/grug pass. Workers never commit; main commits dependencies, validates their records in those roots, commits master last. On resume reuse existing records, never invent a second date or regenerate the committed repository snapshot.
- [ ] **Edit the thin workspace and finish skills.** Render role/branch/worktree/record/ahead/behind/compare URL; no API MR lookup. `workspace close` reports `unmerged`/`not-master` identity and stops. Finish lists nonempty workspaces; only master close delegates to `workspace close`, dependencies retain their worktrees for master closure; single-repo runs retain their own removal. Preserve stacked-branch guard, local stages, ticket sync, three follow-ups, and move session to primary after removal. Behavior layer says the master lifecycle authorizes dependency lifecycles, and dependency code is changed only inside its implement/audit run.
- [ ] **Run green:** manifest suite and conductor `/omc:check` after review.
- [ ] **Commit** as `feat: coordinate workspace design and finish skills`.

### Task 6: Prove the M1 chain with real Git and Worktrunk

Complexity: high

**Files:** Create `tests/e2e/test_e2e_workspace.py`; only extend `tests/e2e/harness.py` if existing helpers cannot express the fixture, with unit coverage for such helper changes.

**Interfaces:** Consume `container`, `configure_omc(container, "claude")`, `make_work_repo(container, path)`, `run_in(container, argv, cwd=..., timeout=...)`. One ordinary Docker test, no golden fork and no LLM lifecycle/judge.

- [ ] **Write the failing integration test** before any integration repair. Create `/work/projects/master` and `/work/projects/dependency`, each with its existing helper's `-origin` bare repository; commit different prefix/base configs and `.omc/` before cutting. Create the master's slug worktree using real `wt`. Before add, move the prepared dependency primary to `/work/prepared/dependency` while retaining its bare origin; then add `dependency-origin --path /work/projects/dependency`. List from both roles, and assert paths, correct custom branch, role, fallback origin key, committed/missing record status as applicable, and absent upstream is null. Push both feature branches with tracking, add one local commit to assert upstream ahead 1 (then push again), and assert ahead 0 after push. Close before merge must refuse, preserving all worktrees. Fast-forward only dependency origin base, close removes dependency then refuses unmerged master and retains master ledger entry. Fast-forward master origin base, rerun close removes master and workspace. Use artifact assertions only, timeout at most 300 s, no skips.
- [ ] **Pin real resolution and idempotence in that fixture.** The derived target resolves to `/work/projects/dependency-origin` by swapping the master origin basename; assert the clone and worktree are separate directories. Re-add the resulting existing sibling `dependency` and assert the ledger and worktree are unchanged. This proves real `ls-remote`, clone, sibling reuse and fallback identity using local origins. Avoid modifying primaries beyond fixture setup/base merges; no second workspace is needed.
- [ ] **Run the first real-tool check:** `just e2e-rest tests/e2e/test_e2e_workspace.py`. Any integration correction must start with its observed expected failure before changing code. If it already passes, record that no product repair was needed; do not manufacture a red test. Earlier tasks already supply the feature's required red-to-green unit evidence.
- [ ] **Make only necessary integration fixes**, each backed by its failing test; do not widen the feature scope. Real `wt` behavior wins over stub assumptions; report any mismatch with the committed record to the conductor.
- [ ] **Run green:** the focused E2E, then conductor `/omc:check`. The conductor's final milestone still runs `/omc:build` → `/omc:verify`; this focused test does not replace it.
- [ ] **Commit** as `test: cover real multi-repository workspace lifecycle`.

### Task 7: Proxy implement and audit through dependency sessions (M2)

Complexity: high

**Files:** Modify `skills/implement/SKILL.md`, `skills/audit/SKILL.md`, `src/omc/{workspace,internal}.py`, `tests/unit/test_plugin_manifests.py`, this plan and the design record; add `tests/unit/test_workspace_completion.py` for the approved deterministic artifact check. Do not change provider launchers or `session.py` merely to move orchestration into Python.

**Interfaces:** Consume workspace list and each repository's existing `omc implement --headless` / `omc review --headless`. Produce master-only Phase 0.5 and required-answer resume behavior. Add `omc internal workspace implementation-status <slug>`: one `OMC_WORKSPACE` verdict, exit 0 with `ok:true, complete:true|false` for valid inspection, exit 1 with `ok:false` on Git failure; no ledger state. **Precondition met:** user accepted the artifact-based predicate above; no ledger completion flag.

- [ ] **Use the approved completion predicate.** Commit the selected implementation plan at the end of local Phase 1 before build tasks. Require clean tree, a committed slug-specific implementation plan, and a later commit with tracked changes outside design/plan documents. Compare against the reachable first-add commit of the plan, not its last amendment, so a final handoff edit does not erase successful evidence. Design-only, plan-only and dirty states fail. Amend design §3.5 to this approved correction; keep completion derived from Git rather than ledger fields. Test the executable check against actual temporary Git repositories.
- [ ] **Write failing contract tests** for Phase 0.5 after record gate and before local planning/audit; empty-workspace or dependency role → no child launch; master → dependencies only in list order; exact two child commands; harness background wait and repository announcement; artifact completion and skip condition; nonzero/early child output relayed verbatim; answer required before same-session resume; no launch of master as a dependency. Add the selected completion regression cases: design-only false, plan-only false, dirty false, genuine task handoff true. Audit requires upstream present and ahead zero, with inspection failure never mapped to zero.
- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q` plus the selected predicate tests.
- [ ] **Implement Phase 0.5 text in both conductors.** Extend initial task lists. Call list after the record gate; only `current_role == "master"` traverses dependencies. For audit insert before local conformance, preserving its existing local product-change refusal. Launch each command from the dependency worktree through the harness's background/continuation mechanism; announce which repository is pending and wait for process exit. Do not inspect transient dirty state as final success while child still runs. Keep child output available, evaluate the approved artifact predicate, skip completed repositories on restarted master runs, and proceed to local phases only after all dependencies pass.
- [ ] **Implement the CRITICAL relay loop.** Claude initially resumes the named `<slug>-implement` or `<slug>-audit` session from that dependency cwd using print mode, preserving the implementation tool grants and provider environment. Treat the answer as one correctly quoted prompt argument, never shell interpolation. Ambiguous named sessions require an exact session ID or manual dependency resumption, never a fresh child masquerading as resume. Codex follows the record's manual fallback: report the exact dependency directory and `omc implement`/`omc review`, wait for user recovery, then recheck artifacts. A launch failure is an actionable failure, not automatically a CRITICAL question with an available resume session. Do not invent or claim live-verified Codex resume. Do not auto-close any dependency child worktree.
- [ ] **Run green:** manifest/predicate tests and conductor `/omc:check`. Keep LLM proxy E2E excluded as the design explicitly decides; existing lifecycle E2E remains the milestone validation.
- [ ] **Commit** as `feat: run dependency implement and audit lifecycles before master`.

### Task 8: Regenerate workspace documentation and complete both milestones

Complexity: medium

**Files:** Modify `.claude/skills/regenerate-readme/SKILL.md`, generated `README.md`, and `tests/unit/test_cli.py`; update `.superpowers/sdd/progress.md` only according to the conductor's existing build-ledger practice.

**Interfaces:** Consume implemented CLI and skill contracts, including the final M2 decision. Produce a generated README consistent with the recipe and source.

- [ ] **Write failing README guards** requiring workspace command/skill mention, workspace ledger, dependency lifecycle ordering, and `OMC_WORKSPACE`, without snapshotting prose. Run `uv run --frozen pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'` and observe the missing workspace requirements.
- [ ] **Update the recipe** to include workspace lifecycle/list/close sources, increment its enumerated skill listing, describe M1/M2 and Codex fallback accurately, and include the new machine contract. Then invoke/read-and-follow the complete regenerate-readme skill: inspect its source matrix, render frozen help, regenerate the full README, preserve carryover slots and shell documentation, validate four Mermaid fences and links.
- [ ] **Run green:** README guards, `git diff --check`, and conductor `/omc:check` after review.
- [ ] **Commit** documentation/tests as `docs: explain multi-repository workspace lifecycle`.
- [ ] **Return to the conductor** for `/omc:build` → `/omc:verify`, final branch review, plan/build-ledger commit and clean unpublished handoff. Read single `OMC_STAGE` verdicts, follow bounded repair, restore only known tool drift, and do not offer completion on a red gate. Scope is both M1 and M2; an M1-only handoff is permitted by the record only if gates run long, and must explicitly say M2 remains.

## Self-review

Coverage: record §§3.1–3.3 and 3.8 → Tasks 1–4; §3.4 → Task 5; §§3.5–3.6 → Tasks 5 and 7; §3.7 → Tasks 5, 7, 8; §3.9 → all unit tasks plus Task 6; §3.10 → task ordering and final milestone. Interfaces align across tasks, every coding task has a complexity label, and each Review Focus item has an owning test. The user resolved both product decisions; the corrected closure and M2 predicates are now binding.
