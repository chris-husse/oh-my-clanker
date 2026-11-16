# Verify once after the audit review, an MR gate, project hooks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/omc:audit` runs the only unconditional review and verify (`build → check → review → verify`, after its own squash and rebase); every push passes an MR gate in `create-mr` decided by a tested CLI helper (`omc internal gate`) and two opt-in project hooks; five hook slots run through one proxy skill; red gates get up to ten progress-gated fix cycles; plans have about five to seven coherent tasks.

**Architecture:** One new Python module (`src/omc/gate.py`, wired into `omc internal`) owns the deterministic half: tree comparison against per-worktree state files and the absent-hook defaults. Everything a session does (run a review, a verify, a hook) stays skill prose: a new internal `hook` proxy skill, rewritten `audit`, `review`, `finish`, `create-mr`, `rebase-main`, `start`, `design`, `write-plan`, `implement`, `integrate`, the verify/check proxies and the behavior layer. Text assertions pin the prose, real-git tests pin the helper, E2E markers and git state pin the flow.

**Tech Stack:** Python 3 (stdlib only), git, Markdown skills, pytest, just, uv, ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-verify-after-audit-review-needs-verify-hook-design.md` (D1–D18; it partially supersedes `2026-11-15-run-verify-only-before-push-design.md`, see its section 8). Read §3 and §5 before any task.

## Global Constraints

- Red first (`.omc/config/AGENTS.md`): every task writes or rewrites its assertions, runs them and sees them FAIL for the expected reason, then changes code or prose. Never `pytest.skip`/`mark.skip`/`skipif`; a missing prerequisite is a `pytest.fail` naming the command that fixes it.
- Task order inside every task (spec §5.11, this repo's policy): **test fan-out → red run → code fan-out → build-and-fix pass → one commit** of tests plus implementation. Fan-out agents are edit-only (no build, no test) and each owns different files. The build-and-fix pass is `just build` then `just check` (D2: build before check), fixing until both are green. Each task leaves `just check` green on its own: a needle and the text it pins move in the same task.
- Targeted runs: `uv run --frozen python -m pytest <paths> -q -p no:cacheprovider`. Before Task 1's red run, run `just check` once and note any host-baseline failures (the 2026-11-15 plan saw `tests/unit/test_cli.py::test_aws_credential_process_runs_bannerless_and_needs_no_config` fail on a host without AWS tooling); a baseline failure is not this branch's to fix and is named in each task report.
- After any `uv run`/`uv build`, restore `uv.lock` when it is the only drift (`git checkout uv.lock`); never commit it.
- `ToolContext` (`src/omc/toolctx.py`) is the only subprocess boundary: `gate.py` reaches git only through `ctx.run([ctx.git_bin, …])`, argv lists only.
- Exit codes: 0 ok, 1 git error, 2 refusal or usage. `omc internal gate` never returns 3.
- Machine contracts are single lines, never wrapped in markdown: `OMC_GATE {"gate", "run", "ask", "reason", "tree", "previous", "message"}` (CLI-emitted) and `OMC_HOOK {"hook", …}` (skill-emitted). `OMC_STAGE` and `OMC_REBASE_MAIN` formats do not change (spec §2 non-goals).
- State files: `git rev-parse --git-path omc/reviewed-tree` and `omc/verified-tree`, holding one tree sha and a newline. Never committed.
- Hook files: `.omc/hooks/<name>/SKILL.md` at the project root (`git rev-parse --show-toplevel`), names exactly `needs_review`, `needs_verify`, `post_design`, `post_rebase`, `pre_push`. `.omc/hooks/post-watch.sh` and `src/omc/watch.py` are not touched.
- The bound and both repair clauses are stated once, in `src/omc/distribution/AGENTS.md` (Task 2). Skills cite "stage-gate rule" / "bounded fix-forward, then a CRITICAL stop" and never restate a number.
- `README.md` is regenerated with `/regenerate-readme` (Task 6), never hand-edited. Never run `omc install` / `uv tool install`.
- No E2E run during implement (`just e2e-tests` belongs to audit's verify). E2E edits are validated by the unit tests over their helpers and by `uv run --frozen python -m pytest --collect-only -q <module>`.
- Complexity labels choose the worker tier; this plan stores no model ids.

## Review Focus

1. **A tree pushed that no verify saw.** A `pre_push` fix, a verify fix or a describe amend after `omc internal gate record verify`. Expected: after a `pre_push` fix `create-mr` asks the verify question again (`omc internal gate verify`), and the describe amend changes no tree. Pinned by Task 3's create-mr ordering needles ("resumes at the verify question", "never the review question") and Task 6's red-then-green `pre_push` E2E, which asserts the verified-tree file equals the pushed tree.
2. **Tool drift blocks the record.** `uv run` dirties `uv.lock` after verify, and `record` refuses on a dirty tracked tree (exit 2). Expected: the order is restore known drift → amend → record, and an exit 2 stops the push and is reported. Pinned by Task 1's dirty-tree refusal test and Task 3's needles on finish's stage-running rules (restore before amend, record after amend).
3. **A hook echoes its own template line.** A hook skill's text contains `OMC_HOOK {"hook": "needs_verify", "run": true|false, …}`, and a model may echo it. Expected: the proxy reads the last `OMC_HOOK` line, requires the answer field to be a JSON boolean, and turns anything else into `"error"`, which counts as `"run": true`. Pinned by Task 2's proxy needles ("last `OMC_HOOK` line", "JSON boolean").
4. **Stale state after a branch switch in one worktree** (a plain clone's primary switching features). Expected: a different tree is never reported `unchanged`, and the question is asked again. Pinned by Task 1's `test_stale_state_from_another_branch_asks_again`.
5. **`post_rebase` skipped after a conflict.** The re-run after a resolved conflict reports `rebased` as `<tip>..<tip>`, so a detector that reads only the final verdict skips the hook in the one case where the base certainly moved. Expected: the pre-run `HEAD` is noted before the first `omc internal rebase-main`, and "moved" is `git merge-base <pre-run HEAD> origin/<base>` ≠ `git rev-parse origin/<base>`. Pinned by Task 4's rebase-main needles and Task 6's conflicted-rebase hook E2E, whose hook writes a marker file.

## Pressure-test evidence

There was one `/omc:explain` pass per task. The GitNexus graph has no index for this repo (`OMC_KNOWLEDGE` `index-missing`; run `/omc:index` first), so every answer below comes from reading the code directly.

- Task 1: `internal.py:_primary_and_base` returns the **primary** root, which is the wrong place for per-worktree state. So `gate.py` uses `wtconfig.repo_root` plus `resolve.project_config(ctx).worktree.base_branch`, the expression `_rebase_main` uses (`internal.py:226`). The codebase has no `Enum` (it uses tuples, e.g. `taskmodels.TASKS`), so the gates are a `GATES` tuple plus a small per-gate table. The verdict is a frozen dataclass with `to_json()`, like `wtconfig.RecordVerdict`. The state path is `rev-parse --git-path` joined to the root (`Path(root) / p`), as `wtconfig.rebase_in_progress` does; this is absolute in linked worktrees and relative in the primary. The hook lookup is the repo root only, like the stage proxies (`skills/verify/SKILL.md` step 2). It does not use `omc internal skills list`'s project/primary/global resolution, which serves knowledge maps, not branch content. Argv is parsed in `run_internal`, as for `rebase-main`.
- Task 2: nothing in `src/omc` needs to parse `OMC_HOOK` or `OMC_GATE`. `watch.py:_parse_stage` exists only because the CLI runs a build stage itself. The proxy adopts that parser's rule: the last verdict line wins. The contract lists are exactly the five files `test_machine_contract_listings_include_knowledge_and_models` reads, so they change together in one task. Skills are discovered by directory (`.codex-plugin/plugin.json` `"skills": "./skills/"`), so a new `skills/hook/` needs only `INTERNAL_SKILLS` in the test.
- Task 3: the only dependents of audit's phase headings are `tests/unit/test_plugin_manifests.py` lines around `test_audit_skill_contract` and `test_audit_and_project_review_dispatch_review_judgment_even_without_findings`; `src/omc/review.py:REVIEW_SEED` is the bare `/omc:audit`. Finish's stage-running rules stay stated once in finish and are cited by audit and `create-mr`, the way audit cites finish's Step 2 rc rules (spec §5.2 step 3). Restating them would put the same bullets in three skills. Review keeps its single `omc internal models` read and moves it ahead of conformance.
- Task 4: `rebase-main` cannot learn "moved" from `OMC_REBASE_MAIN` without a format change, which is a non-goal, and the rc 3 re-run hides the move. The skill therefore notes `git rev-parse HEAD` before its first run and compares merge-bases after success (Review Focus 5). Start keeps its own base resolution and passes `--base <base>`, which also keeps the needle `test_rebase_conflicts_are_resolved_not_handed_back` pins. The write-plan directive is a quoted string in `skills/write-plan/SKILL.md` Step 3, pinned by `test_write_plan_skill_contract`.
- Task 5: `git diff --name-only <tree> <tree>` works on two tree shas (checked against git 2.56). The proxy wording is pinned by `test_verify_proxy_runs_only_in_finish_and_reruns_subsets` and `test_integrate_skill_contract`, and the dogfood verify text by `test_dogfood_verify_stage_gates_on_parallel_e2e_only`.
- Task 6: keep the collapsed-marker regex model (`lifecycle_helpers._stage_steps`, `_REPAIRED_VERIFY`); only its patterns change. Hook E2Es seed state with the real `omc internal gate record verify` instead of a second finish run, which keeps them to one model run each. `tests/e2e/test_e2e_finish.py::test_finish_runs_passing_check_and_build_stages_then_pushes` encodes check-before-build (its build stage requires `/tmp/omc-check-ran`), so D2 breaks it. It is inverted here.

---

### Task 1: `omc internal gate` — the deterministic half of the MR gate

Complexity: medium

**Files:**
- Create: `src/omc/gate.py`
- Modify: `src/omc/internal.py` (`_USAGE`, `run_internal` dispatch)
- Test: `tests/unit/test_gate.py` (new)

**Interfaces:**
- Produces (CLI, consumed by Tasks 3 and 6):
  - `omc internal gate review` / `omc internal gate verify` → exactly one stdout line `OMC_GATE {"gate": …, "run": bool, "ask": bool, "reason": "unchanged"|"no-hook"|"hook"|"no-record", "tree": "<sha>", "previous": "<sha>"|null, "message": "…"}` (keys in that order), exit 0.
  - `omc internal gate record review|verify` → no stdout, stderr narration, exit 0. Exit 2 and nothing written on uncommitted changes to tracked files.
  - Outside a repo → `error: not inside a git repository`, exit 2. Git failure (e.g. no `origin/<base>` for the review merge-base) → `error: …`, exit 1. Bad argv → `_USAGE`, exit 2.
- Produces (Python): `GATES = ("review", "verify")`; `@dataclass(frozen=True) class GateVerdict` with fields `gate, run, ask, reason, tree, previous, message` and `to_json() -> dict`; `question(ctx: ToolContext, gate: str) -> int`; `record(ctx: ToolContext, gate: str) -> int`.

- [x] **Step 1 (test fan-out, one agent): write `tests/unit/test_gate.py`.** Helpers are local to the file, in the shape of `tests/unit/test_internal.py`'s `_setup_primary_with_origin`/`_add_worktree`/`_git_out`: a bare origin with `main`, a primary clone, a feature worktree with one commit; run via `monkeypatch.chdir(...)`, `monkeypatch.setenv("OMC_HOME", tmp_path/"omchome")`, `run_internal([...])`, `capsys`; `_gate_line(out)` asserts exactly one `OMC_GATE ` line and returns its JSON. Tests:
  - `test_unchanged_tree_runs_nothing[review|verify]`: `record <gate>` then `<gate>` → `run` False, `ask` False, `reason == "unchanged"`, `previous == tree == git rev-parse HEAD^{tree}`.
  - `test_review_never_reviewed_without_hook_does_not_run`: → `run` False, `ask` False, `"no-hook"`, `previous == git rev-parse $(git merge-base origin/main HEAD)^{tree}`, `"no review has run"` and `"needs_review"` in `message`.
  - `test_review_changed_since_record_without_hook`: record, new commit → `"no-hook"`, `run` False, `"no review has run"` not in `message`.
  - `test_verify_without_record_runs_without_asking`: with `.omc/hooks/needs_verify/SKILL.md` present → `run` True, `ask` False, `"no-record"`, `previous is None`.
  - `test_verify_changed_without_hook_runs`: record, new commit → `run` True, `ask` False, `"no-hook"`.
  - `test_changed_tree_with_hook_asks[review|verify]`: hook file `.omc/hooks/needs_<gate>/SKILL.md` committed, record, new commit → `run` True, `ask` True, `"hook"`.
  - `test_hook_only_in_primary_is_not_seen`: the hook file exists in the primary checkout, not in the worktree → verify `"no-hook"`.
  - `test_record_writes_the_git_path_file`: `record verify` → rc 0, stdout empty, stderr non-empty; `Path(wt) / git_out("rev-parse", "--git-path", "omc/verified-tree")` reads `tree + "\n"`.
  - `test_record_refuses_a_dirty_tracked_tree`: modify a tracked file → rc 2, file absent; an untracked file only → rc 0.
  - `test_worktrees_keep_separate_state`: record verify in worktree A; worktree B asks → `"no-record"`.
  - `test_message_only_amend_keeps_the_record`: record verify; `git commit --amend -m other` → `"unchanged"`.
  - `test_stale_state_from_another_branch_asks_again`: record verify on branch A; `git switch -c B origin/main` plus a different commit → reason is not `"unchanged"`.
  - `test_question_line_has_the_seven_fields`: `list(verdict) == ["gate", "run", "ask", "reason", "tree", "previous", "message"]`.
  - `test_gate_usage_errors`: `["gate"]`, `["gate", "bogus"]`, `["gate", "review", "x"]`, `["gate", "record"]`, `["gate", "record", "bogus"]` → rc 2, `usage:` on stderr, no stdout.
  - `test_gate_outside_a_repository_refuses`: rc 2, `error: not inside a git repository`.
  - `test_review_without_origin_is_a_git_error`: a repo with no remote → rc 1, `error:` on stderr, no `OMC_GATE` line.

- [x] **Step 2: Red run.** `uv run --frozen python -m pytest tests/unit/test_gate.py -q -p no:cacheprovider`. Expected: every test FAILs; the usage tests fail because `gate` is an unknown subcommand.

- [x] **Step 3 (code fan-out, one agent; both files are one interface): implement `src/omc/gate.py` and wire it.**
  - `gate.py`, module docstring citing spec §5.1. It holds a per-gate table `{"review": ("reviewed-tree", "needs_review", False), "verify": ("verified-tree", "needs_verify", True)}` (state file, hook name, no-hook `run`), a local `_say(msg)` to stderr, and `_git(ctx, root, *args) -> str` that raises `OmcError` with git's stderr on a non-zero exit.
  - `question`: `root = repo_root(ctx)` (None → error line, rc 2); `base = resolve.project_config(ctx).worktree.base_branch`; `tree = rev-parse HEAD^{tree}`; `previous` is the stripped state-file content or None. For review with no record, use `rev-parse <merge-base origin/<base> HEAD>^{tree}`. Hook present means `(Path(root)/".omc"/"hooks"/hook/"SKILL.md").is_file()`. Apply the spec §5.1 table, and print `OMC_GATE {json.dumps(verdict.to_json())}` with `flush=True`. Messages are one relayable sentence each; the never-reviewed review no-hook message reads "no review has run on this branch and no `needs_review` hook is configured — no review runs".
  - `record`: same root resolution. `git status --porcelain --untracked-files=no` non-empty → `error: uncommitted changes to tracked files — amend them before recording the <gate>`, rc 2, nothing written. Otherwise `mkdir(parents=True, exist_ok=True)`, write `tree + "\n"`, then `_say(f"✓ recorded the {gate} tree {tree[:12]}")`.
  - `OmcError` from git → `error: <msg>`, rc 1, caught inside `question`/`record` so `main()`'s boundary is not needed.
  - `internal.py`: add `| gate [record] review|verify` to `_USAGE` beside `design-record`. In `run_internal`, add `if cmd == "gate":` that accepts exactly `[g]` or `["record", g]` with `g in GATES`, and otherwise prints `_USAGE` and returns 2. Import lazily (`from .gate import GATES, question, record`), as `herdr-cwd` does.

- [x] **Step 4: Build-and-fix pass.** `just build` then `just check`, both green; `uv run --frozen python -m pytest tests/unit/test_gate.py tests/unit/test_internal.py -q -p no:cacheprovider` green.

- [x] **Step 5: Commit.** `git add src/omc/gate.py src/omc/internal.py tests/unit/test_gate.py && git commit -m "omc internal gate: tree-keyed review and verify questions"`

---

### Task 2: Hook proxy, contract lists and the behavior layer

Complexity: high

**Files:**
- Create: `skills/hook/SKILL.md`
- Modify: `src/omc/distribution/AGENTS.md`; `.omc/config/AGENTS.md` (contract list; "used by `/omc:finish`" line becomes "used by `/omc:audit`, `/omc:finish` and its MR gate"); `.omc/skills/explain-context/SKILL.md` (contract list); `.omc/skills/review/SKILL.md` (contract list); `.claude/skills/regenerate-readme/SKILL.md` (the "List exactly …" contract line only: add `OMC_GATE` as a CLI verdict and `OMC_HOOK` as skill-emitted)
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Produces: the internal skill `hook` (invoked as the `hook` skill, `omc:hook`). Its input is `<name> [key=value …]`: the first word is the hook name and the rest is the caller's context, passed to the hook unchanged. Contexts are `needs_review|needs_verify previous=<sha> tree=<sha>`, `post_design record=<path>`, `post_rebase base=<branch> old_merge_base=<sha> new_base=<sha> squash=true|false`, and `pre_push base=<branch> branch=<branch>`. Its output is one `OMC_HOOK` line: absent `{"hook": n, "configured": false}`; failed `{"hook": n, "configured": true, "error": "<why>"}`; otherwise the hook's own line, with answer field `run` (needs_*), `go` (post_design), or `ok` plus `before_implementation` (post_rebase), `ok` (pre_push), and `reason` always. Tasks 3 and 4 invoke it.
- Produces: the layer's rule name "stage-gate rule — bounded fix-forward, then a CRITICAL stop" (unchanged) with the D2 order, the hook clause, the ten-cycle bound and the red-gate triple ending "`create-mr` pushes nothing". Tasks 3–5 cite it.
- Must keep passing unchanged: `test_publish_route_squashes_before_rebasing` ("squash, rebase" in the layer), `test_behavior_layer_*`, `test_distribution_agents_task_model_policy`, `test_design_skill_contract`'s "valid stop" quote, `tests/unit/test_agentsmd.py`, `tests/unit/test_configure.py`.

- [x] **Step 1 (test fan-out, one agent: `tests/unit/test_plugin_manifests.py`).**
  - Add `"hook"` to `INTERNAL_SKILLS` (gives frontmatter and the internal marker).
  - New `test_hook_proxy_contract`: in `skills/hook/SKILL.md` body, `## User Input` before `## Steps`, `$ARGUMENTS`, `git rev-parse --show-toplevel`, `.omc/hooks/<name>/SKILL.md`, `'OMC_HOOK {"hook": "<name>", "configured": false}'`, `'"configured": true, "error": "<why>"'`, `"last `OMC_HOOK` line"`, `"JSON boolean"`, `"a `hook` field that is not `<name>`"`. The five hook names each appear in one table along with their answer field: `` `run` `` beside `needs_review` and `needs_verify`, `` `go` `` beside `post_design`, `` `ok` `` beside `post_rebase` and `pre_push`, and `before_implementation`. Also check `"the caller's rule"` and `"never edits"` (the proxy never edits the hook file).
  - Extend `test_machine_contract_listings_include_knowledge_and_models`: assert `OMC_GATE` and `OMC_HOOK` in each of its five files.
  - Rewrite `test_distribution_agents_validation_cadence` over the same `prose` / `cadence` / `gate` slices:
    - publish route: `"squash, rebase, `/omc:build` → `/omc:check`, the MR gate (`needs_review`, `needs_verify`), `pre_push`, described push"`;
    - cadence: `"`/omc:audit`'s fixed verify after its review"`, `"the MR gate when the gate says run"`, `"the user or the project may run it at any time"`, `"end of `/omc:implement`"`, `"post-fix pass"`;
    - general clause, in `gate`: `"run `/omc:build`, then `/omc:check`, then rerun the remaining gate stages in order"`; keep `"`OMC_STAGE`"`, `'"passed": false'`, `"no verdict"`, `'"configured": false'`, `"heavy coding tier"`, `"failed `/omc:check`"`, `"never resets the bound"`, `"CRITICAL"`, `"headless"`;
    - verify clause, ordered: `gate.index("A red `/omc:verify`") < gate.index("then `/omc:build`, then `/omc:check`") < gate.index("rerun only the failed pieces") < gate.index("the full `/omc:verify`")`; plus `"`/omc:verify rerun only: <pieces>`"`, `"briefed with verify's failure output"`, `"pieces that are red now"`, `"nothing before it is rerun"`, `"never returns to review"`; the no-subset branch after the skip sentence: `"the targeted rerun is skipped"`, `"`/omc:build`, then `/omc:check`, then the full `/omc:verify`"`, `"one full run per cycle"`;
    - hook clause: `"A red hook verdict"`, `'"ok": false'`, `"briefed with the hook's `reason`"`, `"the hook again"`;
    - guard: `"a fix repairs the code under test, never the stage's own definition"`, `"`.omc/skills/<stage>/`"`, `"`.omc/hooks/<name>/`"`, `"the script or command it runs"`, `"a stage that only passes by editing itself is the CRITICAL question"`, `"asked at once, without spending the remaining cycles"`;
    - bound (D17): `"up to ten fix cycles per conductor run"`, `"one budget shared by `/omc:check`, `/omc:build`, `/omc:verify` and the `post_rebase` and `pre_push` hooks"`, `"a cycle is one fix dispatch"`, `"never counted on their own"`, `"a fix whose rerun passes by flake still spent its cycle"`, `"fewer or different failing pieces"`, `"an idea no earlier cycle tried"`, `'"fix cycle 3/10: progress — 4 → 2 failing pieces"'`, `"A cycle that brings neither is the CRITICAL question at once"`, `"the tenth cycle ending red"`, `"A red review's own repair is not counted"`, `"what the fixes tried"`;
    - red-gate triple: `"implement makes no handoff on a red check"`, `"audit invokes no `finish`"`, `"`create-mr` pushes nothing"`;
    - gone from `prose`: `"at most two"`, `"the two cycles"`, `"what both fixes tried"`, `"final `/omc:verify`"`, `"only within `/omc:finish`"`, `"before review, and last before the push"`, `"finish pushes nothing"`, `"`/omc:build` once"`, plus the existing gone-list.
  - New `test_behavior_layer_repairs_every_red_verdict_forward` (D16): `"Every red verdict, from a stage or a hook, is repaired forward"` and `"only when no high-confidence decision is possible"` in the Lifecycle-scope bullet, after `"Stop for required answers or genuine blockers"`. The audit authority reads `"authorizes the fixed review (conformance inside it) and the fixed verify, their fixes and record amendments, and publication through finish"`. The Worktrees bullet contains `"`/omc:audit`'s and `/omc:finish`'s first working step"`.

- [x] **Step 2: Red run.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "hook_proxy or contract_listings or validation_cadence or red_verdict or frontmatter or internal_skills"`. Expected: FAIL (no `skills/hook`, missing contract names, old layer text).

- [x] **Step 3 (code fan-out, three agents on different files).**
  - Agent A — `skills/hook/SKILL.md`, in the stage proxies' shape (`skills/verify/SKILL.md`). The frontmatter description begins "Internal — used by …; not meant for direct invocation." Then `## User Input` with `$ARGUMENTS` and the grammar above, an unknown name → the `error` shape, and `## Steps` per spec §5.7 (resolve root; absent → `configured: false`; present → read and follow it from the project root as a black box, passing the context; relay its last `OMC_HOOK` line unchanged). The failure list: no line, unparseable JSON, a `hook` field that is not `<name>`, a missing answer field, or an answer field that is not a JSON boolean (an echoed `true|false` template fails here) → the `error` shape. Also include the per-hook table and "What a caller does with an absent or failed hook is the caller's rule". The proxy never edits the hook file. One machine line at the end, never wrapped.
  - Agent B — `src/omc/distribution/AGENTS.md`, per spec §5.6, worded to contain Step 1's needles. Keep "Check-only gates stay check-only" implicit: implement's per-task check, rebase-main's post-conflict check and review's per-fix check run no build.
  - Agent C — the four contract-list files (`.omc/config/AGENTS.md`, `.omc/skills/explain-context/SKILL.md`, `.omc/skills/review/SKILL.md`, the recipe's contract line), plus `.omc/config/AGENTS.md`'s stages line.

- [x] **Step 4: Build-and-fix pass.** `just build` then `just check` green; `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_agentsmd.py tests/unit/test_configure.py tests/unit/test_cli.py -q -p no:cacheprovider` green (test_cli only guards that the recipe's contract edit broke no README needle).

- [x] **Step 5: Commit.** `git commit -m "Hook proxy, OMC_GATE/OMC_HOOK contracts, layer: build→check, ten progress-gated fix cycles"` with the seven files.

---

### Task 3: audit conducts the fixed sequence; review gains conformance; finish publishes; create-mr gates every push

Complexity: high

**Files:**
- Modify: `skills/audit/SKILL.md`, `skills/review/SKILL.md`, `skills/finish/SKILL.md`, `skills/create-mr/SKILL.md`
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Consumes: Task 1's `omc internal gate …` (exact argv and verdict); Task 2's `hook` skill input grammar and the layer rule name.
- Produces: finish's **stage-running rules** (a named bullet block in finish's build/check step: restore known tool drift after each stage run before anything is amended; amend stage-made tracked changes and fixes with `git add -u && git commit --amend --no-edit` before a rerun; leave untracked artifacts alone; the Unconfigured / grug-and-human-review bullet). Audit and `create-mr` cite them as "finish's stage-running rules". The terms "fixed review", "fixed verify", "MR gate", "review question", "verify question" are reused by Tasks 4–6.

- [x] **Step 1 (test fan-out, one agent: `tests/unit/test_plugin_manifests.py`).**
  - `test_audit_skill_contract`, rewritten. Keep: `omc internal design-record`, `OMC_DESIGN_RECORD`, `/omc:design`, `git fetch origin <base>`, `origin/<base>...HEAD`, `docs/superpowers/`, the "nothing to audit" refusal, `CRITICAL`, `stage-gate rule`, `bounded fix-forward, then a CRITICAL stop`, `` `finish` ``, `unrelated or undisposed`, `preserve`, `explicitly authorizes including`, `left untouched and outside publication`, `re-read `git status --porcelain``, the intro needle; `"Before the squash"` replaces `"Before invoking `finish`"`, and Phase 1 comes before Phase 2's `--squash`. New, in heading order: `## Phase 0 — gates and sources` < `## Phase 1 — dirty-file classification` < `## Phase 2 — squash and rebase` < `## Phase 3 — build → check` < `## Phase 4 — the fixed review` < `## Phase 5 — the fixed verify` < `## Phase 6 — publish`. Phase 2 contains `` `rebase-main` ``, `--squash` and `"by the rules finish's Step 2 states"`. Phase 3's body orders `` `build` `` before `` `check` ``. Phase 4 contains `` `review` ``, `"whatever its verdict"`, `"amended into the squashed commit"` and `"omc internal gate record review"`. Phase 5 contains `"that the project's `verify` stage is starting"`, `"never returns to review"`, `"known tool drift"` and `"omc internal gate record verify"`, with `record verify` after `known tool drift`. Also `"finish's stage-running rules"` and `"one fix-cycle budget"`. Gone: `"## Phase 1 — conformance"`, `"Phase 2a"`, `"Phase 2b"`, `"two-cycle"`, `"final verify"`, `"Dispatch a Review worker"` (moved to review), `"stage is starting"` no longer banned. Keep `"conflict resolution apply unchanged"` (pinned by `test_rebase_conflicts_are_resolved_not_handed_back`) and `"squash, rebase"` (pinned by `test_publish_route_squashes_before_rebasing`). The frontmatter description contains `"fixed review"`.
  - `test_audit_and_project_review_dispatch_review_judgment_even_without_findings`: the conformance slice becomes `review.split("2a. **Conformance.**", 1)[1].split("2b. Look for", 1)[0]` (needs `omc internal design-record`, `"Dispatch a Review worker"`, `"even when"`); the project slice becomes `review.split("2b. Look for", 1)[1].split("3. **Grug lens.**", 1)[0]`.
  - New `test_review_runs_conformance_first`. `steps.index("omc internal models") < steps.index("2a. **Conformance.**") < steps.index("2b. Look for") < steps.index("3. **Grug lens.**")`. In the conformance slice: `"no design record — conformance skipped"`, `"this is not a failure"`, `"code deviates from the record"`, `"record is stale"`, `"file:line"`, `"Important"`, `"Minor"`, `"conformance only"`, `"Decisions taken during brainstorm"`, `"never waived"`, `"## Implementation review"`, `"immediately before the terminal `## Deliberate complexity`"`, `"append a new dated entry below the previous review entry"`, `"provider"`, `"date"`, `"`tasks.simple`, `tasks.medium`, or `tasks.high`"`, `"/omc:check"` and `"once before the lenses start"`. In the verdict step: `"an Important conformance finding"` and `"has no disposition"` (fails the stage), and `"names conformance first"`. The conformance slice contains neither `**Missing**` nor `**Present**` (other review tests split on them).
  - `test_finish_skill_contract`: keep the needle list minus nothing (`grug` and `regardless of `"configured"`` stay via the stage-running rules). Replace the order block: `step3 = text.split("## Step 3", 1)[1].split("## Step 4", 1)[0]`; `body.index("`build`") < body.index("`check`")`; `"`review`"` not in body; `create-mr` after Step 3; the TRACKED-files and Unconfigured slices keep their assertions.
  - Replace `test_finish_runs_verify_before_review_and_last` with `test_finish_publishes_and_skips_a_verified_tree`: `"## Step 3 — build → check (skipped on a verified tree)"` in text; in `flat3`: `"omc internal gate verify"`, `'"reason": "unchanged"'`, `"build and check skipped — tree unchanged since the last green verify"`, `"stage-running rules"`, `"after each stage run"`, `"known tool drift"`, `"never discard unknown work"`, `"before its reruns"`, `"stage-gate rule"`, `"bounded fix-forward, then a CRITICAL stop"`, `"restarts from `build`"`, `"nothing is pushed"`; gone from `flat`: `"pre-review verify"`, `"final verify"`, `"HEAD^{tree}"`, `"at most two"`, `"stop at that stage"`; Step 0 list `"rebase-main --squash → build → check"` and `"create-mr (MR gate, describe, push)"`; Step 5 names `"whether a review ran and why"`, `"suppressed by `needs_verify`"`, `"skipped as unchanged"`, `"`pre_push`"`; the "Address review comments" item contains `"/omc:check"` and `"re-run `create-mr`"` and `"the MR gate inside `create-mr`"`; the frontmatter description contains `"MR gate"` and no `"final verify"`, keeping squash before rebase (`test_finish_starts_with_rebase_main`).
  - `test_create_mr_skill_contract` extended, or a new `test_create_mr_runs_the_mr_gate`. `## Step 0 — the MR gate` comes before `## Step 1`. Ordered indices: `"omc internal gate review"` < `"needs_review"` < `"omc internal gate verify"` < `"needs_verify"` < `"get-mr-description"` < `"pre_push"` < `"git push --force-with-lease"`. Also `"omc internal gate record review"`, `"omc internal gate record verify"`, `"OMC_GATE"`, `"the `hook` skill"`, `'an `error` verdict leaves `run` true'`, `"resumes at the verify question"`, `"never the review question"`, `"after the describe amend"`, `"finish's stage-running rules"`, `"Nothing is pushed while"`, `"no `OMC_GATE` line"`, `"does not exit 0"`, `"its own conductor run"` (the Step 5 re-push), and `"relay"` with the gate's `message`. The frontmatter keeps `"not meant for direct invocation"`; `"Do not create the MR/PR"` stays.

- [x] **Step 2: Red run.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "audit or review or finish or create_mr or rebase_conflicts or publish_route"`. Expected: FAIL on the new headings and needles.

- [x] **Step 3 (code fan-out, four agents, one skill each; each gets spec §5.2–§5.5 and the Interfaces block).**
  - `skills/audit/SKILL.md`, per spec §5.2. Phase -1's list: gates → dirty-file classification → rebase-main --squash → build → check → review (conformance first) → record review → verify → record verify → finish. Phase 1 is today's dirty-file paragraph, moved: "Before the squash" replaces "Before invoking `finish`", and it states that the squash's `git add -A` would publish unrelated work. Phase 2 cites finish's Step 2 rc rules and keeps the "conflict resolution apply unchanged" phrase. Phases 3–5 are one conductor run with one fix-cycle budget, and the `finish` audit invokes is a second conductor run. In Phase 4, a red build/check/review restarts at `build` (general clause). In Phase 5, a red verify falls under the verify clause, the verify phase is announced on its own line, and a `record` exit other than 0 means nothing is published and the error is reported. The authority sentence matches the layer. The old Phases 1, 2, 2a and 2b leave the file.
  - `skills/review/SKILL.md`, per spec §5.3. Step 2 opens with the single `omc internal models` read (moved up). Then `2a. **Conformance.**` has today's audit Phase 1/2/2a/2b content, retargeted: the conformance Review worker, disposition, fix workers at the Complexity tiers, the trace, and one `/omc:check` once before the lenses start when code changed, with a red check failing the stage. Then `2b. Look for <project-root>/.omc/skills/review/SKILL.md` follows, unchanged below. Step 4's summary examples are prefixed with a conformance clause (`"conformance 0 findings; project stage ok; grug 0 findings; human-review …"`), so the pinned substrings still match.
  - `skills/finish/SKILL.md`, per spec §5.4. Step 3 is `build → check (skipped on a verified tree)`: run `omc internal gate verify` after Step 2, skip both stages on `"reason": "unchanged"` and say the pinned sentence, otherwise `build` then `check`. It holds the stage-running rules block, and red stages follow the stage-gate rule with fixes amended. Step 4 invokes `create-mr`, whose Step 0 is the MR gate, and finish and that `create-mr` are one conductor run. The Step 5 report and option 2 are as pinned. The completion contract keeps the CRITICAL-stop exception ("nothing is pushed").
  - `skills/create-mr/SKILL.md`, per spec §5.5: a new `## Step 0 — the MR gate` (review question, verify question), today's Steps 1–2 (describe, amend), a new `pre_push` step (`hook` with `pre_push base=<base> branch=<branch>`, absent → continue; red → D15 via the stage-gate rule, fix → build → check → amend → resume at the verify question), Step 3 push, and Step 4 report (adding what the gate did). It also covers the one-way-back rule, the bound sentence, and "a question with no `OMC_GATE` line or a `record` that does not exit 0 decided nothing: push nothing, report the error". The precondition (one squashed commit, clean tree) stays.

- [x] **Step 4: Build-and-fix pass.** `just build` then `just check` green; the whole `tests/unit/test_plugin_manifests.py` passes (including `test_conductors_externalize_their_steps_first`, `test_ticket_sync_names_finish_follow_ups_step`).

- [x] **Step 5: Commit.** `git commit -m "audit runs the fixed review and verify; create-mr gates every push"` with the four skills and the test file.

---

### Task 4: rebase-main's `post_rebase`, start routes through it, design's `post_design`, the plan shape

Complexity: high

**Files:**
- Modify: `skills/rebase-main/SKILL.md`, `skills/start/SKILL.md`, `skills/design/SKILL.md`, `skills/write-plan/SKILL.md`, `skills/implement/SKILL.md`
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Consumes: Task 2's `hook` skill (`post_rebase base=… old_merge_base=… new_base=… squash=…`, `post_design record=<path>`) and the layer's D15/D17 wording; Task 3's term "the fixed verify" (implement's sentence).
- Produces: the write-plan directive text that every later `/omc:write-plan` passes verbatim (exact copy in Step 3 below).

- [x] **Step 1 (test fan-out, one agent: `tests/unit/test_plugin_manifests.py`).**
  - New `test_rebase_main_runs_post_rebase_when_the_base_moved`, over the flattened text: `"before the first `omc internal rebase-main` run"` and `"git rev-parse HEAD"`; `"git merge-base <pre-run HEAD> origin/<base>"` and `"git rev-parse origin/<base>"` (the moved-HEAD test); `"a squash alone"` (no hook); `"`post_rebase`"`; `"squash=true|false"`; `"amended into the squashed commit"`; ordered `index("resolve") < index("re-run") < index("`post_rebase`") < index("/omc:check")` inside the post-conflict paragraph (split on `"When you resolved something"`); `'"before_implementation": true'` and `"the check is skipped"`; `"runs no build and no check either"`; the four callers `"standalone `/omc:rebase-main`"`, `"`/omc:start`'s freshness gate"`, `"`/omc:audit`'s squash"`, `"`/omc:finish`'s `--squash`"`; `"--squash` is `/omc:audit`'s and `/omc:finish`'s flag"`. Keep `test_rebase_main_skill_contract` and `test_rebase_conflicts_are_resolved_not_handed_back` as they are (their needles stay true). The frontmatter description names audit too.
  - `test_start_skill_contract` extended: Step 3 contains `"invoke the `rebase-main` skill with `--base <base>`"` on the not-an-ancestor branch, before any conflict; it contains no `"git rebase origin/<base>"`; `"dirty tree → stop"` stays.
  - New `test_design_runs_post_design_after_the_commit`: in the Step 5 slice, `index("Commit the record") < index("`post_design`") < index("Provider selection per step off")`; `'"go": false'`, `"back to the Design worker as a finding"`, `"commits, and the hook is called again"`, `"Interactive runs do not bound the loop"`, `"headless run stops after three no-gos"`, `"An `error` verdict is not a no-go"`, `"a second error is the CRITICAL question"`. Step 0's task list names `post_design`.
  - `test_write_plan_skill_contract`: add the verbatim directive sentences (Step 3 copy below) as needles, and keep `"The plan stores complexity, never model ids."`.
  - `test_implement_skill_contract`: replace the `"`finish` runs `verify`"` needle with `"`/omc:audit` runs the fixed `verify` after its review"`; keep `"/omc:verify"` and `"/omc:build"` absent; Phase 2 contains `"edit-only agents"`, `"in parallel at the task's Complexity tier"`, `"one build-and-fix pass"`, `"the main session runs the fan-out"`; the continuation statement contains `"To publish without an audit, type `/omc:finish`"` and `"no review unless the project's `needs_review` hook asks for one"`.

- [x] **Step 2: Red run.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "rebase or start or design or write_plan or implement"`. Expected: FAIL on the new needles only.

- [x] **Step 3 (code fan-out, five agents, one skill each).**
  - `skills/rebase-main/SKILL.md`, per spec §5.8. Moved-HEAD detection: note `git rev-parse HEAD` before the first `omc internal rebase-main` run. After the final `"ok": true` verdict (not the `note` verdict), the base moved exactly when `git merge-base <pre-run HEAD> origin/<base>` differs from `git rev-parse origin/<base>`. That holds after a conflict and its re-run too. A squash alone moves nothing. Then invoke the `hook` skill with `post_rebase` and its four keys. In squash mode the hook's commits are amended into the squashed commit. After a conflict the order is resolve → re-run → `post_rebase` → `/omc:check`, skipped on `"before_implementation": true`; with no hook, today's behavior. A red `post_rebase` follows the stage-gate rule (D15), confined to what the rebase broke, and with `before_implementation` true it runs no build and no check either. Name the four callers. Standalone runs start no review or verify; the MR gate catches up.
  - `skills/start/SKILL.md`, Step 3: the not-an-ancestor branch invokes the `rebase-main` skill with `--base <base>` from the outset (start keeps its own base resolution). A dirty tree still stops (rebase-main rc 1), and the authority paragraph stays true. Step 0.5 task 3's wording follows.
  - `skills/design/SKILL.md`, per spec §5.9: Step 5 runs the `post_design` loop between the commit and the close. The loop is: `hook` with `post_design record=<path>`; absent or `go: true` → close as today; `go: false` → reason to the Design worker, which re-hardens through Steps 2–4, the main session commits, and the hook is called again; unbounded when interactive; a headless stop after three no-gos with one CRITICAL question carrying the three reasons; an `error` → call once more, and a second error is the CRITICAL question. Step 0's list gains `post_design`.
  - `skills/write-plan/SKILL.md`, Step 3. The quoted directive keeps its three sentences and gains, verbatim: "Write about five to seven tasks, each a coherent chunk of the work; a small change may need fewer, and a plan is never padded to reach five. Inside a task, independent edits (different files) fan out to parallel edit-only agents that run no build and no test, followed by one build-and-fix pass; never one task per file or per page. The project's testing policy still governs the order inside a task."
  - `skills/implement/SKILL.md`. Phase 2: the per-task worker dispatches the task's edit-only agents in parallel at the task's Complexity tier, then runs one build-and-fix pass itself, then commits. Where a worker cannot dispatch agents (Claude Code subagents cannot), the main session runs the fan-out and the worker runs the build-and-fix pass. The per-task reviews and `/omc:check` stay. Replace "`finish` runs `verify` on the squashed commit" with the pinned sentence, and extend the continuation as pinned.

- [x] **Step 4: Build-and-fix pass.** `just build` then `just check` green; whole `tests/unit/test_plugin_manifests.py` passes.

- [x] **Step 5: Commit.** `git commit -m "post_rebase and post_design hooks; start rebases through rebase-main; five-to-seven-task plans"`

---

### Task 5: Proxy wording, integrate's hook slots, this repo's `needs_verify`

Complexity: medium

**Files:**
- Create: `.omc/hooks/needs_verify/SKILL.md`
- Modify: `skills/verify/SKILL.md`, `skills/check/SKILL.md`, `skills/integrate/SKILL.md`, `.omc/skills/verify/SKILL.md` (remove the sentence "Changes limited to documentation do not require repeating successful model calls; cite the last green run.")
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Consumes: Task 2's `OMC_HOOK` shape and context grammar (`previous=<sha> tree=<sha>`).
- Produces: `.omc/hooks/needs_verify/SKILL.md`, which Task 6's E2E fixture copies byte-for-byte into its test repositories.

- [x] **Step 1 (test fan-out, one agent).**
  - Rename `test_verify_proxy_runs_only_in_finish_and_reruns_subsets` → `test_verify_proxy_runs_after_audit_review_and_in_the_mr_gate`: the description contains `"after `/omc:audit`'s review and in the MR gate"` and `"the user or the project may run it at any time"`; the body contains `"two places only"`, `"`/omc:audit`'s fixed verify after its review"`, `"the MR gate in `create-mr`"`; `"only within"` absent from both; check contains `"`verify` (audit and the MR gate only)"`; the subset and `OMC_STAGE` needles stay.
  - `test_integrate_skill_contract`: the verify paragraph contains `"two places only"` instead of `"only within `/omc:finish`"`. A new `test_integrate_inventories_the_hook_slots`: each of the five `.omc/hooks/<name>/SKILL.md` paths appears in Phase 1's inventory and as a `### ` slot in Phase 2. Each slot contains its `OMC_HOOK` verdict line, what omc does when it is absent, and `"absence is the correct default"`, and `post-watch.sh` stays.
  - `test_dogfood_verify_stage_gates_on_parallel_e2e_only`: add `assert "Changes limited to documentation" not in text`.
  - New `test_dogfood_needs_verify_hook`: the file has frontmatter `name: needs_verify` and contains `"previous=<sha> tree=<sha>"`, `"git diff --name-only <previous> <tree>"`, `` "`docs/`" ``, `` "`README.md`" ``, `'OMC_HOOK {"hook": "needs_verify", "run": false'`, `'"run": true'`, `"`skills/`"` (named as product), and `"exactly one"`; and `"error"` / `"run": true` for a failing diff.

- [x] **Step 2: Red run.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "verify_proxy or integrate or dogfood or stage_proxy"`. Expected: FAIL.

- [x] **Step 3 (code fan-out, four agents: verify+check proxies; integrate; the new hook; `.omc/skills/verify`).**
  - Proxies: the intended-semantics wording per spec §5.10 / §5.6.
  - Integrate: the inventory and Phase 2 slots per spec §5.10. The verify paragraph names the two places. Phase 3 item 2 reads "`/omc:audit` and `/omc:finish` run the stage gates you just designed; the MR gate asks your hooks".
  - Hook: run `git diff --name-only <previous> <tree>` from the project root. `"run": false` exactly when every listed path is under `docs/` or is `README.md`, and `"run": true` otherwise, with a reason naming the first product path. A failing diff → `"run": true` with the error as reason. End with exactly one plain `OMC_HOOK` line. `skills/*.md` is product here.
  - `.omc/skills/verify/SKILL.md`: delete the one sentence; nothing else changes.

- [x] **Step 4: Build-and-fix pass.** `just build` then `just check` green.

- [x] **Step 5: Commit.** `git commit -m "Verify proxy wording, integrate hook slots, this repo's needs_verify hook"`

---

### Task 6: E2E stage order and hook scenarios; README

Complexity: high

**Files:**
- Modify: `tests/e2e/lifecycle_helpers.py`, `tests/unit/test_e2e_lifecycle_stages.py`, `tests/unit/test_e2e_lifecycle_fixture.py`, `tests/e2e/variations/test_from_agreed.py`, `tests/e2e/test_e2e_finish.py`, `tests/e2e/harness.py` (host `add_stage`/`add_hook`), `.claude/skills/regenerate-readme/SKILL.md`, `README.md` (regenerated only), `tests/unit/test_cli.py`
- Create: `tests/e2e/test_e2e_hooks.py`

**Interfaces:**
- Consumes: everything above; `.omc/hooks/needs_verify/SKILL.md` from Task 5 (read on the host as `ROOT / ".omc/hooks/needs_verify/SKILL.md"`).
- Produces: `lifecycle_helpers._assert_audit_stage_order(markers)` and `_assert_check_only_before_audit(markers)`, which replace `_assert_finish_stage_order` / `_assert_check_only_before_finish` (the rename covers every caller: `lifecycle_helpers._assert_audited_artifacts`, `variations/test_from_agreed.py`, the unit test). In `harness.py`, `add_stage(container, repo, stage, body)` (moved from `test_e2e_finish._add_stage`) and `add_hook(container, repo, name, body)` write `.omc/{skills,hooks}/<name>/SKILL.md`.

- [x] **Step 1 (test fan-out, three agents: unit fixtures; `test_cli.py`; fixture unit test).**
  - `tests/unit/test_e2e_lifecycle_stages.py`: rewrite the finish parametrizations as audit ones, with ids naming the case.
    - Accept (`_assert_audit_stage_order`): `check\nbuild\ncheck\nreview\nverify\n`; `check\ncheck\nreview\ncheck\nbuild\ncheck\nreview\nverify\n` (an implementation-time review); the red check repair `check\nbuild\ncheck\nbuild\ncheck\nreview\nverify\n`; the red review repair `check\nbuild\ncheck\nreview\nbuild\ncheck\nreview\nverify\n`; the red verify repair `…review\nverify\nbuild\ncheck\nverify\n` and twice; retries collapsing `…verify\nverify\n`; a trailing check `…verify\ncheck\n`.
    - Reject: empty; the old finish order `check\nbuild\nverify\nreview\n`; no check after build `check\nbuild\nreview\nverify\n`; check before build only `check\nreview\nverify\n`; no verify `check\nbuild\ncheck\nreview\n`; review after verify (D3) `…review\nverify\nreview\n`; a verify repair without build (D2) `…review\nverify\ncheck\nverify\n`; a verify repair without check `…review\nverify\nbuild\nverify\n`; a trailing build `…verify\nbuild\ncheck\n`.
    - `_assert_check_only_before_audit` keeps its accept/reject sets, rewritten to the new order.
  - `tests/unit/test_e2e_lifecycle_fixture.py`: new `test_fixture_ships_this_repos_needs_verify_hook`, the same local transport. The fixture writes `.omc/hooks/needs_verify/SKILL.md` byte-identical to the repo's.
  - `tests/unit/test_cli.py`: replace `test_readme_documents_verify_only_in_finish` with `test_readme_documents_verify_after_audit_review_and_mr_gate`.
    - The recipe lifecycle slot (flattened) contains `"build → check → review → verify"`, `"MR gate"`, `"`needs_review`"`, `"`needs_verify`"`, `"`pre_push`"`, `"up to ten fix cycles"`, `"an agent starts verify only in audit and in the MR gate"` and the new repair sentence `'a red verify first reruns only its failed pieces after build and check, then one full verify, when the project\'s verify skill has a "Running a subset" section; otherwise fix → build → check → one full verify'`.
    - The recipe `| Lifecycle |` sources row adds `skills/create-mr/SKILL.md`, `skills/hook/SKILL.md`, `skills/rebase-main/SKILL.md`, and the `How omc is put together` row adds `src/omc/gate.py`.
    - The README lifecycle (flattened) contains `"build → check → review → verify"`, `"MR gate"`, `"needs_verify"`, `"needs_review"`, `"pre_push"` and the repair sentence, and the README contains `"OMC_GATE"` and `"OMC_HOOK"`.
    - The README has no `"final verify"`, `"check → build → verify → review"`, `"two-fix bound"` or `"only inside finish"`. The `| `.omc/skills/verify`` row keeps `"subset"`, and a Development row starts with `` "| `.omc/hooks/needs_verify`" ``.

- [x] **Step 2: Red run.** `uv run --frozen python -m pytest tests/unit/test_e2e_lifecycle_stages.py tests/unit/test_e2e_lifecycle_fixture.py tests/unit/test_cli.py -q -p no:cacheprovider`. Expected: FAIL on the import of the renamed helpers, the missing hook file in the fixture, and the README needles.

- [x] **Step 3 (code fan-out, four agents on different files).**
  - Agent A — `tests/e2e/lifecycle_helpers.py`.
    - Replace `_REPAIRED_VERIFY` / `_VERIFY_AFTER_BUILD` / `_FINAL_VERIFY_TAIL` with `_AUDIT_VERIFY_TAIL = re.compile(r"verify( build check verify)*")`.
    - `_assert_audit_stage_order`: from the last review, the last build before it is followed by exactly `check`. After it comes `_AUDIT_VERIFY_TAIL`, then only trailing checks, so the golden finish on an unchanged tree adds no build, check, review or verify.
    - `_assert_check_only_before_audit` is today's logic with a new docstring.
    - `_assert_audited_artifacts` uses both. It also asserts that `git -C <worktree> rev-parse --git-path omc/verified-tree` (joined to the worktree) holds `git rev-parse HEAD^{tree}`, which is the gate's `unchanged` evidence, and that `omc/reviewed-tree` exists.
    - `_fixture(container, *, failing_stage=None, path=…)` passes the host-read hook text as `sys.argv[2]` to its payload, which writes `.omc/hooks/needs_verify/SKILL.md`.
    - `variations/test_from_agreed.py` uses the renamed helpers. New `@pytest.mark.expensive` `@pytest.mark.timeout(3700)` `test_post_design_no_go_then_go` from `agreed`: commit an `add_hook` `post_design` hook answering `"go": true` exactly when the record has a `## Rollback` section, otherwise `"go": false` with a reason saying to add one. Then `/omc:design`; assert the committed record contains `## Rollback` and `git log --format=%H -- <record>` lists at least two commits.
  - Agent B — `tests/e2e/harness.py` + `tests/e2e/test_e2e_finish.py`.
    - Move `_add_stage` to `harness.add_stage` and add `harness.add_hook`.
    - Rename `test_finish_runs_passing_check_and_build_stages_then_pushes` → `test_finish_runs_passing_build_and_check_stages_then_pushes`, with the check stage requiring `/tmp/omc-build-ran` and writing `/tmp/omc-check-ran` (D2). Its messages follow.
    - Rewrite the two red-stage tests' comments for D17: one cycle without progress stops, so this is not measured on this host and the next token-bearing verify re-measures against 300 s. Their assertions stay.
  - Agent C — `tests/e2e/test_e2e_hooks.py` (new, default tier, `require_token("claude")`, `configure_omc`, `make_work_repo`, `claude -p <cmd> --output-format text --allowed-tools …`). Each test is one model run, and each asserts only artifacts.
    - `test_docs_only_change_skips_verify_by_needs_verify`: a feature branch with a product commit plus committed stages, where verify writes `/tmp/omc-verify-ran`, and a copy of this repo's `needs_verify`. Run `omc internal gate record verify` in the repo, commit `docs/notes.md`, then `/omc:finish`. Assert the branch is on origin with `docs/notes.md` and that `/tmp/omc-verify-ran` is absent.
    - `test_product_change_runs_verify_by_needs_verify`: the same setup, with the change to the product file instead. Assert `/tmp/omc-verify-ran` exists and the verified-tree file equals the pushed tree.
    - `test_red_pre_push_is_fixed_then_pushed`: a `pre_push` hook answering `"ok": true` exactly when `CHANGELOG.md` contains the line `- pushed through the MR gate`, with allowed tools `Bash Read Edit Write Skill Agent Task`. Assert the pushed commit's `CHANGELOG.md` has the line, the hook file in the pushed tree equals the original, and the verified-tree file equals the pushed tree.
    - `test_post_rebase_before_implementation_skips_the_check`: main advanced with a change conflicting with the branch's single commit, a check stage writing `/tmp/omc-check-ran`, and a `post_rebase` hook that runs `touch /tmp/omc-post-rebase-ran` and answers `"ok": true, "before_implementation": true`. Run standalone `/omc:rebase-main`. Assert `git merge-base origin/main HEAD == git rev-parse origin/main`, no conflict markers, `/tmp/omc-post-rebase-ran` present and `/tmp/omc-check-ran` absent. This folds spec §6's "conflicted start rebase": start enters the same skill, which Task 4 pins.
  - Agent D — `.claude/skills/regenerate-readme/SKILL.md`. Change the Lifecycle and How-omc-is-put-together source rows as pinned. The lifecycle slot states the new flow: audit squashes, rebases, then build → check → review (conformance first) → verify; finish rebases, then build → check (skipped on a verified tree), then the MR gate (`needs_review`, `needs_verify`, `pre_push`) and the push. It contains the pinned repair sentence and "up to ten fix cycles", and drops the "two-fix bound" and finish-stage sentences. The project-stages wording becomes "verify is full E2E, run by agents only in audit and in the MR gate". Replace "an agent starts verify only inside finish" with the pinned "an agent starts verify only in audit and in the MR gate". The Review bullet names the fixed review and verify. The Development slot gains the `.omc/hooks/needs_verify` row (docs-only changes need no verify), and "State this repo has no post-watch hook" stays.

- [x] **Step 4: Regenerate the README.** After Agent D, invoke `/regenerate-readme` and follow it. In its check step use `uv run --frozen python -m pytest tests/unit/test_cli.py -q -p no:cacheprovider -k 'readme or shell_integration_documents'`. Review the diff: only the verify, review, gate and hook placement facts, the bound, and the contract list should change meaningfully.

- [x] **Step 5: Build-and-fix pass.** `just build` then `just check` green. `uv run --frozen python -m pytest --collect-only -q tests/e2e/test_e2e_hooks.py tests/e2e/test_e2e_finish.py tests/e2e/variations/test_from_agreed.py tests/e2e/golden/test_golden_claude.py` collects without error (a missing Docker token fails at run time, not at collection).

- [x] **Step 6: Commit.** `git commit -m "E2E: audit stage order and hook scenarios; README for the MR gate"` (helpers, unit fixtures, E2E files, recipe, README, test_cli).

---

## Decisions (plan calls, 2026-11-16)

1. **Stage-running rules live once, in finish.** Audit and `create-mr` cite "finish's stage-running rules" the way audit already cites finish's Step 2 rc rules. Spec §5.2 says the rules "move with the stages". All three skills now run stages, and one statement cited twice beats three copies.
2. **Review's conformance step is `2a`, the project lookup `2b`.** This keeps the record's "Step 2a" name and the existing `3. **Grug lens.**` anchor; the models read moves ahead of 2a.
3. **The `post_rebase` E2E runs standalone `/omc:rebase-main`.** The spec allows folding (§6, "the builder may fold them"). A conflicted `omc design` start in a container needs a moving origin during a seeded session, and both paths run the same skill code.
4. **`post_design` E2E is `expensive`.** Two hardening passes cannot fit the 300 s default ceiling. It runs with `just e2e-rest -m "e2e and expensive" … test_from_agreed.py`, as the chain test already does, and is not a `just e2e-tests` gate.
5. **The golden fixture ships this repo's `needs_verify`.** It is never asked on the golden path (the finish gate answers `unchanged`). It is there so that variations forked from snapshots exercise the real hook.
