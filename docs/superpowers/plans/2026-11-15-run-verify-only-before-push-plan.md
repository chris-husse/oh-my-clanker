# Run verify only inside finish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** An agent starts the project's `verify` stage only inside `/omc:finish` (once before `review`, once last before the push, skipped on an unchanged tree), repairs a red verify with a targeted-rerun loop, and gates everything else with `/omc:check`.

**Architecture:** Skill text, the distributed behavior layer, tests and the generated README change; no CLI, config or `OMC_STAGE` contract change. The verify repair loop lives once in the behavior layer's stage-gate rule; `finish` cites it. One small `scripts/e2e.sh` change makes `just e2e-tests <node ids>` a working subset command for this repo's own verify stage.

**Tech Stack:** Markdown skills, Python/pytest text assertions, bash (`scripts/e2e.sh`), just, uv.

**Spec:** `docs/superpowers/specs/2026-11-15-run-verify-only-before-push-design.md` (partially supersedes `2026-10-06-run-verify-in-implement-phase-design.md`; read §3 decisions D1–D12 before any task).

## Global Constraints

- Red first: every task writes or rewrites its assertions, runs them and sees them FAIL for the expected reason, then changes the prose/code (`.omc/config/AGENTS.md`). No `skip`/`skipif`.
- Run tests from the worktree as `uv run python -m pytest <paths> -q -p no:cacheprovider` (plain `uv run pytest` / `just check` in a worktree can test the primary checkout's code). Run only the task's test files; the full suite has host-environment baseline failures (in the files this plan touches, only `tests/unit/test_cli.py::test_aws_credential_process_runs_bannerless_and_needs_no_config` fails on this host before any change).
- After any `uv run`, restore `uv.lock` if it is the only drift (`git checkout uv.lock`); never commit it.
- No E2E run is possible on this host (no token). E2E file edits (`tests/e2e/**`) are validated by the unit tests that exercise their helpers and by `uv run python -m pytest --collect-only -q <module>` on each touched module; never by a live run.
- No change to `src/omc/*.py`, the `OMC_STAGE` line format, `skills/review`, `skills/create-mr`, `skills/rebase-main`, `skills/build`.
- The bound and the verify loop are stated once, in `src/omc/distribution/AGENTS.md`; `finish`, `implement`, `audit` cite "stage-gate rule" / "bounded fix-forward, then a CRITICAL stop" and never restate "at most two".
- `README.md` is regenerated with `/regenerate-readme` (Task 8), never hand-edited.
- Each task commits its own test + prose together; the conductor runs `/omc:check` after each task.
- Complexity labels: implementation workers are dispatched by the `Complexity:` line; the plan stores no model ids.

## Review Focus

1. **A fix that "repairs" the stage instead of the code.** Under D7 finish now fixes red stages; a fix subagent could edit `.omc/skills/<stage>/SKILL.md` or the script it runs to turn the stage green (the red-stage E2Es in `tests/e2e/test_e2e_finish.py` commit the broken stage on the feature branch). Expected: the stage definition is left alone and the bound is reached. Task 1 now states the guard in the layer's general clause and pins it in its test; the live E2Es (not runnable here) remain the behavioral check. See Decisions below.
2. **Final verify skipped wrongly.** A review that amended a fix or a waiver must change `HEAD^{tree}` and so trigger the final verify; only a byte-identical tree skips. Task 2's assertions pin the `git rev-parse HEAD^{tree}` comparison and the skip sentence; reviewers check the tree is recorded after the *most recent* passing full verify (including a full run inside a repair loop).
3. **Tool drift after a verify run.** `uv run` dirties `uv.lock`; amending it would change the tree the final verify passed and break `create-mr`'s clean-tree requirement. Task 2 pins "known tool drift" restore in Step 3.
4. **Selector that matches nothing in one runner pass.** `just e2e-tests <golden node id>` exits 5 in the parallel pass today. Task 6 pins it with a stub-runner test and a collect-only `just` test; `-k` stays unsupported (it deselects the golden path, so no snapshots exist for variations) and the subset section says node ids only.
5. **Old layer + new finish skill.** A host that updated skills but not the layer cites a rule without the verify clause; finish must still stop on red rather than loop. Task 2 keeps the literal citation "bounded fix-forward, then a CRITICAL stop" in finish.

## Pressure-test evidence

One `/omc:explain` pass per task; the GitNexus snapshot is stale (index 9 commits behind, wiki 120) and Python-only, so every finding below was confirmed against the files.

- Task 1: `src/omc/agentsmd.py:_render` installs the layer bytes verbatim; `tests/unit/test_agentsmd.py` / `test_configure.py` and the E2E global-instructions test compare against the live file (no pinned hash) and pin no needle in the three bullets. They join Task 1's green run as guards.
- Task 2: no tree-identity mechanism exists anywhere (`HEAD^{tree}` is new, prose-only); the drift rule to mirror is implement Phase 3's "Restore only known tool drift … never discard unknown work"; finish's TRACKED-files `git add -u` amend would otherwise sweep `uv.lock` in. Use the module's `_flat` helper.
- Task 3: the CLI seeds (`src/omc/implement.py:IMPLEMENT_SEED`, `src/omc/review.py:REVIEW_SEED`) are bare commands; nothing else reads implement's Phase 2b. Audit has its own unrelated "Phase 2b — trace and commit".
- Task 4: the two expensive red-build scenarios do not depend on audit meeting the red build; they keep passing when finish meets it.
- Task 5: skill inputs follow `## User Input` + fenced `$ARGUMENTS` + "The whole input selects the mode:" (`skills/write-plan/SKILL.md`).
- Task 6: `tests/unit/test_e2e_image_holders.py:_runner_with_docker_stub` is the established harness for the runner's control flow (stub `uv` can return 5 on the parallel pass); the collect-only `_collected` test adds the real-pytest check.
- Task 7: collapsing adjacent duplicate markers is the established retry tolerance; every `_assert_implemented_artifacts` caller stops at implement's handoff; the stale graph misses `test_from_agreed.py`'s caller.
- Task 8: the recipe reads only frontmatter of implement/audit/verify and carries old README facts forward, so the recipe itself must state the new placement.

## Repo facts the tasks rely on

- `scripts/e2e.sh all` always passes `tests/e2e/golden` to the golden pass, so any extra node id still runs the whole golden chain there; a variation node id then runs in the parallel pass (`rc 0`, measured with `OMC_E2E_NO_DOCKER=1 … --collect-only`). A golden node id makes the parallel pass collect nothing → pytest exit 5 → `set -e` fails the run. A `-k` expression that matches no golden test exits 5 in the golden pass.
- A fix changes the working tree, so the E2E source id changes and the golden stage snapshots must be rebuilt anyway; a targeted variation rerun therefore always needs the golden pass first (which `all` does).
- `tests/e2e/harness.py` configures E2E containers with `llm.provider_selection_per_step=true`, so `/omc:implement` in a variation stops at its handoff (no audit, no finish).
- The project stage markers file `/tmp/omc-lifecycle-stages` is cumulative across phases in one container and in forked snapshots.

---

### Task 1: Behavior layer — finish-only verify, the verify clause, one counter

Complexity: medium

**Files:**
- Modify: `src/omc/distribution/AGENTS.md` (bullets "Publish through `/omc:finish`", "Validation cadence", "stage-gate rule")
- Test: `tests/unit/test_plugin_manifests.py::test_distribution_agents_validation_cadence` (rewrite in place)

**Interfaces:**
- Produces: the rule name "stage-gate rule — bounded fix-forward, then a CRITICAL stop" (unchanged) and its new verify clause, which Tasks 2–4 cite by name. Other tests that read the layer must keep passing unchanged: `test_publish_route_squashes_before_rebasing` ("squash, rebase"), `test_design_skill_contract` (the "valid stop" quote), `test_behavior_layer_*`, `test_distribution_agents_task_model_policy`. `skills/rebase-main/SKILL.md`'s check-only citation is not edited.

- [x] **Step 1: Rewrite the test.** Replace the body of `test_distribution_agents_validation_cadence` with assertions over `prose = " ".join(text.split())`, and slices `cadence = prose.split("**Validation cadence**", 1)[1].split("**stage-gate rule", 1)[0]`, `gate = prose.split("**stage-gate rule", 1)[1].split("**Ask the graph", 1)[0]`:
  - present in `text`: `"Validation cadence"`, `"/omc:check"`, `"builds the world"`;
  - present in `prose`: `"`/omc:check` → `/omc:build` → `/omc:verify` → `/omc:review` → final `/omc:verify`"`, `"skipped when the tree is unchanged since verify passed"`;
  - present in `cadence`: `"only within `/omc:finish`"`, `"before review, and last before the push"`, `"the user or the project may run it at any time"`, `"end of `/omc:implement`"`, `"post-fix pass"`;
  - general clause kept, present in `gate`: `"`OMC_STAGE`"`, `'"passed": false'`, `"no verdict"`, `'"configured": false'`, `"heavy coding tier"`, `"at most two fix-and-rerun cycles"`, `"restart from `/omc:build`"`, `"failed `/omc:check`"`, `"never resets the bound"`, `"CRITICAL"`, `"headless"`;
  - verify clause, ordered in `gate`: `gate.index("A red `/omc:verify`") < gate.index("rerun only the failed pieces") < gate.index("`/omc:build` once") < gate.index("the full `/omc:verify`")`; plus `"`/omc:verify rerun only: <pieces>`"` (the exact invocation, Task 5's input), `"briefed with verify's failure output"`, `"pieces that are red now"`, `"nothing before it is rerun"`;
  - stage-definition guard in `gate` (conductor call, see Decisions): `"a fix repairs the code under test, never the stage's own definition"`, `"`.omc/skills/<stage>/`"`, `"a stage that only passes by editing itself is the CRITICAL question"`;
  - one counter in `gate`: `"a cycle is one fix dispatch"`, `"never counted on their own"`, `"shared by every stage"`;
  - red-gate list in `gate`: `"implement makes no handoff on a red check"`, `"audit invokes no `finish`"`, `"finish pushes nothing"`;
  - gone from `prose`: `"major milestones"`, `"three gate runs"`, `"`/omc:build` → `/omc:verify` before handoff"`, `"no handoff commit, continuation offer or `/omc:audit` invocation"`, `"also `/omc:verify` when its fixes changed"`.

- [x] **Step 2: Run it red.** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "validation_cadence"` → FAIL on the new stage-order string.

- [x] **Step 3: Edit the three bullets** per spec §5.1. Publish bullet: the new arrow with "final `/omc:verify`" and the skip note. Cadence bullet: one statement — `/omc:check` gates everything while working, including the end of `/omc:implement`'s build and `/omc:audit`'s post-fix pass; `/omc:build` builds the world, no tests; an agent starts `/omc:verify` only within `/omc:finish` (before review, and last before the push); the user or the project may run it at any time. Delete the implement/audit milestone sentences. Stage-gate bullet: keep the general clause; drop ", or three gate runs total when checks pass"; add the verify clause (spec §5.1 first sub-bullet, verbatim in substance), the one-counter sentences ("a cycle is one fix dispatch with whatever reruns follow it, targeted or full; reruns are never counted on their own; the two cycles are per conductor run, shared by every stage in it"), and replace the "No later phase runs on a red gate" tail with the implement/audit/finish triple. Keep the "even a flaky pass on rerun consumes a cycle" sentence. Add to the general clause, worded to contain the three pinned needles: a fix repairs the code under test, never the stage's own definition (`.omc/skills/<stage>/`) or the script/command it runs; a stage that only passes by editing itself is the CRITICAL question.

- [x] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_agentsmd.py tests/unit/test_configure.py -q -p no:cacheprovider` → all pass (`test_agentsmd.py` / `test_configure.py` render the layer from the live file and pin only needles outside these bullets — guards, not targets; this task touches no skill, so the skill contract tests Tasks 2–4 later rewrite still pass unchanged).

- [x] **Step 5: Commit.** `git add src/omc/distribution/AGENTS.md tests/unit/test_plugin_manifests.py && git commit -m "Run verify only inside finish: behavior layer verify clause"`

---

### Task 2: finish — two verify runs, skip rule, fix-forward on every red stage

Complexity: high

**Files:**
- Modify: `skills/finish/SKILL.md` (frontmatter description, Step 0, Step 3, Step 5)
- Test: `tests/unit/test_plugin_manifests.py::test_finish_skill_contract` (extend), new `test_finish_runs_verify_before_review_and_last`

**Interfaces:**
- Consumes: Task 1's rule name and verify clause (cited, not restated).
- Produces: the terms "pre-review verify" and "final verify" that Tasks 4 and 8 reuse; the skip report sentence `final verify skipped — tree unchanged since verify passed`.

- [x] **Step 1: Write the failing tests.**
  - In `test_finish_skill_contract`: replace the order block with one over Step 3's body (`step3 = text.split("## Step 3", 1)[1].split("## Step 4", 1)[0]`, `body = step3.split("\n", 1)[1]` to skip the heading) — `order = [text.index("`rebase-main`"), …]` keeps `rebase-main` before Step 3, then inside `body`: `` body.index("`check`") < body.index("`build`") < body.index("`verify`") < body.index("`review`") < body.index("final `verify`", body.index("`review`")) ``, and `text.index("`create-mr`") > text.index("## Step 3")`. Keep the "A stage that changed TRACKED files" / "Unconfigured" slice assertions (both bullets stay).
  - New `test_finish_runs_verify_before_review_and_last` over `flat = _flat(text)`, `flat3 = _flat(step3)` (reuse the module's existing `_flat` helper, `tests/unit/test_plugin_manifests.py:_flat`), `step0`, `step5`, frontmatter `desc`:
    - `"## Step 3 — project stages: check → build → verify → review → final verify"` in `text`;
    - in `flat3`: `"pre-review verify"`, `"final verify"`, `"git rev-parse HEAD^{tree}"`, `"task list"`, `"final verify skipped — tree unchanged since verify passed"`, `"that the project's `verify` stage is starting"`, `"known tool drift"`, `"never discard unknown work"`, `"stage-gate rule"`, `"bounded fix-forward, then a CRITICAL stop"`, `"without another `review`"`, `"restarts from `build`"`, `"nothing is pushed"`;
    - gone from `flat`: `"stop at that stage"`, `"so the user can fix and re-run"`, `"the four project stages"`, `"at most two"`;
    - `"final verify"` in `" ".join(step0.split())`; `"skipped"` in `step5`; `"final verify"` in `desc`;
    - Step 5 option 2 unchanged: `"re-run `create-mr`"` still in step5 and `"verify"` not in the "Address review comments" item.

- [x] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "finish"` → FAIL (new heading missing).

- [x] **Step 3: Edit `skills/finish/SKILL.md`** per spec §5.2:
  - Frontmatter: "run the project's check/build/verify/review stages and a final verify" (keep "squash … rebase" order in the description: `test_finish_starts_with_rebase_main` checks it).
  - Step 0 list: `… → rebase-main --squash → check → build → pre-review verify → review → final verify (skipped when the tree is unchanged) → create-mr …`.
  - Step 3 heading as pinned. Body: the order sentence spells the skills literally as `` `check` → `build` → `verify` → `review` → final `verify` `` (Step 1's index assertion reads those backticked names), check first as the fail-fast gate; announcement line before each verify run naming which of the two it is; after a verify run restore only known tool drift (e.g. `uv.lock` from `uv run`) before anything is amended, and never discard unknown work — the same rule implement's Phase 3 applies, worded the same way; the TRACKED-files bullet must say it amends what remains after that restore (otherwise its `git add -u` sweeps the lockfile into the commit and changes the tree the verify just passed); each time a full verify passes record `git rev-parse HEAD^{tree}` in the task list entry for the final verify (no file is written); after `review` and its amend compare `HEAD^{tree}` with the recorded value — equal → skip and report the pinned sentence, different → run the final verify (a waiver-only change counts as different; the project skill decides what a docs-only change needs). Replace the "stop at that stage" bullet: every red stage follows the behavior layer's stage-gate rule — bounded fix-forward, then a CRITICAL stop; fixes are amended into the squashed commit (`git add -u && git commit --amend --no-edit`); a red `review` restarts from `build` (through verify, review and the final verify again); a red verify is repaired by the rule's verify clause, which reruns nothing before verify, so final-verify fixes are amended and pushed without another `review`. At the bound: one CRITICAL question, branch stays squashed, nothing is pushed; the answer resumes the remaining steps. Keep the TRACKED-files and Unconfigured bullets.
  - Step 5 report: "stage outcomes (both verify runs; whether the final verify ran or was skipped)". Option 2 text unchanged.

- [x] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider` → whole file passes (includes `test_conductors_externalize_their_steps_first`: Step 0 must stay before the first "Invoke the").

- [x] **Step 5: Commit.** `git commit -m "finish: verify before review and last before push, fix-forward on red"` with the two files.

---

### Task 3: implement — remove the milestone gate

Complexity: medium

**Files:**
- Modify: `skills/implement/SKILL.md` (intro "Four phases", Phase -1, Phase 2 per-task paragraph, delete Phase 2b, Phase 3 drift sentence)
- Test: `tests/unit/test_plugin_manifests.py::test_implement_skill_contract` (edit), `::test_implement_keeps_task_commits_before_a_red_milestone_gate` (rename + rewrite)

**Interfaces:**
- Consumes: Task 1's red-gate wording "implement makes no handoff on a red check".

- [x] **Step 1: Rewrite the tests.**
  - `test_implement_skill_contract`: drop needle `"## Phase 2b — milestone gate"`; keep `"stage-gate rule"`, `"bounded fix-forward, then a CRITICAL stop"`, `'"passed": true'`, `'"configured": false'` (the verdict sentence moves into Phase 2). Order list becomes record gate → `/omc:write-plan` → `omc internal models` → `subagent-driven-development` → `"## Phase 3 — hand off"`. Delete `assert "finish runs it" not in text`. Replace the `gate`-slice assertion with `build = text.split("## Phase 2 — build", 1)[1].split("## Phase 3 — hand off", 1)[0]` and `"a red `/omc:check` never reaches `/omc:audit`" in " ".join(build.split())`. Add: `"/omc:verify" not in text`, `"/omc:build" not in text`, `"Phase 2b" not in text`, `"milestone" not in text`, `"Full E2E never runs in implement" in flat`, `"`finish` runs `verify`" in flat`, `"Three phases, strictly in order" in flat`, and Phase -1 slice contains `"plan → subagent build → handoff"`.
  - Rename `test_implement_keeps_task_commits_before_a_red_milestone_gate` → `test_implement_keeps_task_commits_before_the_handoff`: `"commit each task's tested changes" in build`; `"keep those task commits" in build`; `"the plan was already committed by `/omc:write-plan`" in build`; `"plan uncommitted" not in text`; handoff (`## Phase 3` → end) contains `"at least one task commit"` and `"touches a path outside `docs/superpowers/`"` (flattened).

- [x] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "implement"` → FAIL.

- [x] **Step 3: Edit `skills/implement/SKILL.md`** per spec §5.3. Intro: "Three phases, strictly in order". Phase -1: "Write the three phases … plan → subagent build → handoff: …". Phase 2: "The task commits belong to Phase 2, before the handoff"; per-task paragraph ends "Full E2E never runs in implement; `finish` runs `verify` on the squashed commit."; after it, the last task's green `/omc:check` is the final gate before the handoff: read its single-line `OMC_STAGE` verdict (only `"passed": true` permits progression, including `"configured": false`; a missing verdict is a failure); a red check follows the behavior layer's stage-gate rule: bounded fix-forward, then a CRITICAL stop; no handoff commit on red; with provider selection per step off, a red `/omc:check` never reaches `/omc:audit`; on red keep those task commits — the plan was already committed by `/omc:write-plan`; a required answer resumes under the same authorization. Delete Phase 2b entirely (its task-commit precondition is Phase 3's). Phase 3: "such as a lockfile changed by `uv run`" (drop "or the milestone verify run").

- [x] **Step 4: Run green.** Whole `tests/unit/test_plugin_manifests.py` passes (`test_conductors_*`, `test_implement_always_routes_the_plan_through_write_plan`, `test_task_model_skill_routing_contract` included).

- [x] **Step 5: Commit.** `git commit -m "implement: check is the last gate before handoff"`.

---

### Task 4: audit — post-fix gate is check only

Complexity: simple

**Files:**
- Modify: `skills/audit/SKILL.md` (Phase -1 list wording if it says "post-fix gate" — keep the name; Phase 2a; Phase 3)
- Test: `tests/unit/test_plugin_manifests.py::test_audit_skill_contract`

**Interfaces:**
- Consumes: Task 2's term "final verify"; Task 1's "audit invokes no `finish`".

- [x] **Step 1: Edit the test.** Remove needles `"/omc:build"`, `"/omc:verify"`, `"tracked product files"`. Replace `gate.index("/omc:check") < gate.index("/omc:build") < gate.index("/omc:verify")` with: `"/omc:check" in gate`, `"/omc:build" not in gate`, `"/omc:verify" not in gate`, `"stage is starting" not in text`, `"run verify too" not in text`. Add `phase3 = text.split("## Phase 3 — publish", 1)[1].split("## Completion contract", 1)[0]`; `"check, build, verify, review, final verify, describe, push" in " ".join(phase3.split())`; `"fix-forward" in phase3`.

- [x] **Step 2: Run red.** `-k "audit_skill_contract"` → FAIL.

- [x] **Step 3: Edit `skills/audit/SKILL.md`** per spec §5.4: Phase 2a — after the disposition pass always invoke `/omc:check`; read its `OMC_STAGE` verdict as today; a red check follows the stage-gate rule and folds into the batched CRITICAL question; do not invoke `finish` while it is red. Drop build, conditional verify, the announcement and "If a gate repair itself changes tracked product files, run verify too". Phase 3: the pinned order string, and "Its failing-stage behavior (the stage-gate rule's fix-forward) and conflict resolution apply unchanged." Add one sentence: audit's post-fix check and the `finish` it invokes are two conductor runs, each with its own two-cycle bound.

- [x] **Step 4: Run green.** Whole `tests/unit/test_plugin_manifests.py` passes.

- [x] **Step 5: Commit.** `git commit -m "audit: post-fix gate is check only"`.

---

### Task 5: Stage proxies and integrate — finish-only wording, "rerun only" input

Complexity: medium

**Files:**
- Modify: `skills/verify/SKILL.md` (frontmatter + body), `skills/check/SKILL.md` (one phrase), `skills/integrate/SKILL.md` (the `.omc/skills/verify` paragraph)
- Test: `tests/unit/test_plugin_manifests.py` — new `test_verify_proxy_runs_only_in_finish_and_reruns_subsets`; extend `test_integrate_skill_contract`

**Interfaces:**
- Produces: the proxy input `rerun only: <pieces>` (invoked as `/omc:verify rerun only: <pieces>`, the spelling Task 1's layer clause names) read through the repo's established skill-input convention — a `## User Input` section with a fenced `$ARGUMENTS` block followed by "The whole input selects the mode:" rules, as in `skills/write-plan/SKILL.md` — and the project-skill section title `Running a subset` that Task 6 writes.

- [x] **Step 1: Write the failing tests.** New test reading `verify = skills/verify/SKILL.md`, `check = skills/check/SKILL.md`, frontmatter via the existing `re.match(r"\A---\n(.*?)\n---\n", …)` pattern:
  - in flattened `verify` body and in its description: `"only within finish"` (body may spell it ``only within `finish` `` — assert on the description with plain text and on the body with `"only within `finish`"`), and `"the user or the project may run it at any time"` in the body;
  - `"major milestones" not in verify` and `not in check`; `"`verify` (finish only)" in check`;
  - body: `"## User Input"`, `"$ARGUMENTS"` (and `verify.index("## User Input") < verify.index("## Steps")`), `"rerun only: <pieces>"`, `"Empty, or anything not starting with `rerun only:`"`, `"Running a subset"`, `"runs the full stage and says so"`, `"names the failing pieces"`, `"describes the subset"`;
  - the OMC_STAGE template line is byte-identical to today: `'`OMC_STAGE {"stage": "verify", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`' in verify`.
  - `test_integrate_skill_contract`: slice `para = text.split("### `.omc/skills/verify`", 1)[1].split("### ", 1)[0]`; `"Running a subset" in para`; `"major milestones" not in para`; `"only within `/omc:finish`" in " ".join(para.split())`.

- [x] **Step 2: Run red.** `-k "verify_proxy or integrate_skill_contract"` → FAIL.

- [x] **Step 3: Edit the three skills** per spec §5.5. verify proxy: semantics paragraph (agent runs it only within `finish`, before review and last before the push; the user or the project may run it at any time); a `## User Input` section before `## Steps` with the fenced `$ARGUMENTS` block and "The whole input selects the mode:" — Empty, or anything not starting with `rerun only:` → run the full stage (other text is context); `rerun only: <pieces>` → follow the project skill's "Running a subset" section; without one, run the full stage and say so; Step 3's failure `summary` names the failing pieces when the output identifies them (it is what the stage-gate rule hands the fix subagent and what the conductor passes back as `rerun only`); a subset run's verdict describes the subset. check proxy: "full E2E to `verify` (finish only)". integrate: replace "Reserved for after major milestones — never the routine dev loop." with the finish-only sentence and one sentence suggesting a "Running a subset" section when the project's E2E runner can select tests. `skills/build/SKILL.md` unchanged.

- [x] **Step 4: Run green.** Whole `tests/unit/test_plugin_manifests.py` passes (`test_stage_proxy_contract` and `test_skills_have_frontmatter` included — keep the frontmatter one line, valid YAML).

- [x] **Step 5: Commit.** `git commit -m "verify proxy: finish-only semantics and targeted reruns"`.

---

### Task 6: This repo's verify stage — "Running a subset" and a node-id-safe runner

Complexity: medium

**Files:**
- Modify: `scripts/e2e.sh` (`all` mode only), `.omc/skills/verify/SKILL.md` (new section)
- Test: `tests/unit/test_e2e_image_holders.py` (extend the existing `_runner_with_docker_stub` harness; two new tests), `tests/unit/test_lifecycle_command.py` (extend `_collected`, three new tests), `tests/unit/test_plugin_manifests.py::test_dogfood_verify_stage_gates_on_parallel_e2e_only` (extend)

**Interfaces:**
- Consumes: Task 5's section title `Running a subset`.
- Produces: `_collected(recipe: str, *args: str) -> set[str]` in `tests/unit/test_lifecycle_command.py` (existing callers unchanged); Task 7 uses it for its rename assertion.

- [x] **Step 1: Write the failing tests.**
  - Runner control flow, in the established stub harness: give `_runner_with_docker_stub` two keyword parameters, `extra_args: tuple[str, ...] = ()` (appended to argv after `mode`) and `uv_body: str = "#!/bin/sh\nexit 0\n"` (the `uv` stub's script). New `test_runner_all_tolerates_an_empty_parallel_pass_only_with_a_selection`: `uv_body` = `#!/bin/sh` + `case "$*" in *"not golden"*) exit 5 ;; esac` + `exit 0` (builtins only — stub PATH rule); `mode="all", extra_args=("tests/e2e/golden/test_golden_claude.py::test_stage_recorded",)` → `returncode == 0` and stderr names the empty parallel pass; the same stub with no `extra_args` → `returncode == 5`. New `test_runner_rest_mode_still_fails_on_an_empty_selection`: same stub, `mode="rest"` with the node id → `returncode == 5`. Use `scenario="default"` (any non-special value) so the images stub reports a current snapshot.
  - `_collected(recipe, *args)`: argv `["just", recipe, *args, "--collect-only"]`; everything else unchanged.
  - `test_e2e_tests_subset_runs_a_golden_node_id`: `_collected("e2e-tests", "tests/e2e/golden/test_golden_claude.py::test_stage_recorded") == GOLDEN` (today: rc 5 from the parallel pass → the helper's rc assertion fails).
  - `test_e2e_tests_subset_runs_a_variation_after_the_golden_path`: `_collected("e2e-tests", "tests/e2e/variations/test_from_recorded.py::test_write_plan_commits_plan_only") == GOLDEN | {"test_write_plan_commits_plan_only"}` (passes today; it pins the behavior the section documents).
  - `test_e2e_tests_subset_still_fails_when_nothing_matches`: run `just e2e-tests tests/e2e/variations/test_from_recorded.py::test_no_such_test --collect-only` directly (same env as `_collected`) and assert `returncode != 0`.
  - dogfood test: `"## Running a subset" in text`, `"just e2e-tests tests/e2e/" in text`, `"node id" in text`, `"-k" in text` (the section warns against it), `"just codex-gate tests/e2e/" in text`.

- [x] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_e2e_image_holders.py tests/unit/test_lifecycle_command.py tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "empty or subset or dogfood_verify"` → the `all`-mode stub test, the golden-node-id collect test and the dogfood test FAIL; the `rest`-mode and variation tests pass already (they pin behavior that must not change).

- [x] **Step 3: Change `scripts/e2e.sh`.** Add `run_rest_selected() { local rc=0; run_rest "$@" || rc=$?; if [ "$rc" -eq 5 ] && [ $# -gt 0 ]; then echo "no non-golden test matched the selection; the golden pass ran it" >&2; return 0; fi; return "$rc"; }` and use it only in the `all` branch after `run_golden "$@"`. `golden`/`rest` modes are unchanged (a selector that matches nothing there still fails). Update the header usage comment with one line on node-id subsets.

- [x] **Step 4: Add "## Running a subset"** to `.omc/skills/verify/SKILL.md`: `just e2e-tests <pytest node ids…>` (e.g. `just e2e-tests tests/e2e/variations/test_from_recorded.py::test_write_plan_commits_plan_only`); the golden path always runs in full first (its stages chain and a fix changes the source id, so the snapshots are rebuilt), then only the named non-golden tests; a golden node id reruns the golden path alone; use node ids, never `-k` (it deselects the golden stages, so variations find no snapshots); a failed Codex-gate piece reruns with `just codex-gate tests/e2e/<file>::<test>`. Keep every existing sentence.

- [x] **Step 5: Run green.** Same command as Step 2 plus the whole `tests/unit/test_lifecycle_command.py` and `tests/unit/test_e2e_image_holders.py` (runner stub tests) → pass. Restore `uv.lock` drift.

- [x] **Step 6: Commit.** `git commit -m "verify stage: document node-id subsets; runner tolerates an empty parallel pass"`.

---

### Task 7: E2E lifecycle assertions follow the new placement

Complexity: high

**Files:**
- Modify: `tests/e2e/lifecycle_helpers.py`, `tests/e2e/golden/test_golden_claude.py`, `tests/e2e/variations/test_from_implemented.py`, `tests/e2e/variations/test_from_recorded.py`, `tests/e2e/variations/test_from_agreed.py`, `tests/e2e/test_e2e_finish.py` (comments only, see Step 6)
- Test: `tests/unit/test_e2e_lifecycle_stages.py`, `tests/unit/test_lifecycle_command.py`

**Interfaces:**
- Produces in `tests/e2e/lifecycle_helpers.py`:
  - `_stage_steps(markers: str) -> list[str]` — the marker lines with adjacent duplicates collapsed (lifted out of today's `_assert_finish_stage_order`, which already tolerates retries this way); both helpers below use it, plus `_FINISH_BLOCK = ("check", "build", "verify", "review")`.
  - `_assert_check_only_before_finish(markers: str) -> None` — replaces `_assert_implement_stage_order`. Works on `steps = _stage_steps(markers)`. If finish's block `("check", "build", "verify", "review")` occurs in `steps`, the steps before its first occurrence contain no `build` and no `verify`. If it does not occur, `steps` is non-empty, contains `check`, and contains no `build`/`verify`.
  - `_assert_finish_stage_order(markers: str) -> None` — same block requirement, now located at its first occurrence `i`; in `after = steps[i + 4:]` at most one `verify`, and when there is one, no `review` follows it.
  - `_assert_audit_verify_count` and the `drift_repair` keyword of `_assert_audited_artifacts` are deleted; `_assert_audited_artifacts` calls `_assert_check_only_before_finish(markers)` after `_assert_finish_stage_order(markers)`.
  - `_assert_implemented_artifacts` calls `_assert_check_only_before_finish(markers)`.
- Consumes: Task 6's `_collected(recipe, *args)`.
- Callers to update (grep, not the graph — the stale graph misses `test_from_agreed.py`): `_assert_implemented_artifacts` is called by `test_golden_claude.py::test_stage_implemented`, `_assert_successful_implementation`, `test_e2e_lifecycle_full.py::test_failing_build_blocks_publication`, `test_codex_handoff.py::test_codex_implement_handoff_from_claude_record`, `test_from_recorded.py::test_implement_commits_without_publication` — all stop at implement's handoff because `tests/e2e/harness.py` sets provider selection per step on, so check-only markers hold for each; `_assert_audited_artifacts` is called by the golden audited stage, `test_from_implemented.py`, `test_e2e_lifecycle_full.py` (twice) and `test_codex_handoff.py`.

- [x] **Step 1: Rewrite the unit tests** in `tests/unit/test_e2e_lifecycle_stages.py`:
  - accept `_assert_check_only_before_finish`: `"check\n"`, `"check\ncheck\nreview\ncheck\n"`, `"check\ncheck\nbuild\nverify\nreview\n"`, `"check\nreview\ncheck\nbuild\nverify\nreview\nverify\n"` (ids: implement-only, implement-review-audit, then-finish, then-finish-final-verify);
  - reject: `""`, `"review\n"`, `"check\nbuild\n"`, `"check\nverify\n"`, `"check\nbuild\nverify\ncheck\nbuild\nverify\nreview\n"` (old implement milestone), `"check\nverify\ncheck\nbuild\nverify\nreview\n"` (audit verify before finish) — ids accordingly;
  - finish accept list: keep the four existing cases, add `"check\nbuild\nverify\nreview\nverify\n"` (final-verify) and `"check\nbuild\nverify\nverify\nreview\nverify\nverify\n"` (retried-final-verify, collapses);
  - finish reject list: keep the nine existing cases, add `"check\nbuild\nverify\nreview\nverify\nreview\n"` (review-after-final-verify) and `"check\nbuild\nverify\nreview\nverify\ncheck\nverify\n"` (two-verifies-after-review);
  - delete `test_audit_verify_count_only_applies_to_explicit_drift_repair` and the old implement-order tests.
  - `tests/unit/test_lifecycle_command.py`: rename `test_default_e2e_collects_measured_failing_verify_variation` → `test_default_e2e_collects_implement_never_runs_verify_variation` asserting `"test_implement_never_runs_verify" in _collected("e2e-tests")`.

- [x] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_e2e_lifecycle_stages.py tests/unit/test_lifecycle_command.py -q -p no:cacheprovider` → import error for `_assert_check_only_before_finish`, rename assertion fails.

- [x] **Step 3: Implement the helpers** per the Interfaces block; update callers: `test_from_agreed.py` imports `_assert_check_only_before_finish` instead of `_assert_implement_stage_order`; `test_golden_claude.py::test_stage_audited` and `test_from_implemented.py::test_audit_repairs_committed_greeting_drift` drop `drift_repair=True`.

- [x] **Step 4: Rework the E2E scenarios.**
  - `test_from_recorded.py`: `test_failing_verify_blocks_implementation_handoff` → `test_implement_never_runs_verify` (still `@pytest.mark.variation("recorded")`, no `expensive`, `wait_turn(240)`). Keep the sentinel `touch`. After the turn: `_record_phase(session, m["repo"], m["worktree"], evidence, "implement", turn)` with `evidence = {"turns": []}`, then `_assert_implemented_artifacts(container, m["repo"], m["worktree"], m["branch"], evidence, recorded)` (new plan, product changed, record untouched, nothing pushed, clean tree, check-only markers — so no `verify` marker); keep the existing pathspec task-commit count (`:(exclude)docs/superpowers/plans`) because `_assert_implemented_artifacts` counts the record commit too. Drop the judge call and the `judge` import if nothing else in the file uses it. Comment: the sentinel would turn any verify red, so a run that started verify cannot pass by luck.
  - `test_from_agreed.py::test_design_auto_advances_to_push`: replace the `_assert_implement_stage_order(markers)` call and the `count("verify") >= 2` block with `_assert_check_only_before_finish(markers)`, `_assert_finish_stage_order(markers)`, and `1 <= markers.splitlines().count("verify") <= 2` with a message naming the markers.
  - `test_golden_claude.py`: the timing comment above `test_stage_implemented` says the 57.5–141.4 s figures were measured *with* the removed milestone gate (2026-10-06) and are an upper bound until the next measured run; no number is invented.

- [x] **Step 5: Run green + collect.** Unit files from Step 2 pass; then `uv run python -m pytest --collect-only -q -p no:cacheprovider tests/e2e/lifecycle_helpers.py tests/e2e/golden/test_golden_claude.py tests/e2e/variations/test_from_recorded.py tests/e2e/variations/test_from_agreed.py tests/e2e/variations/test_from_implemented.py tests/e2e/test_e2e_lifecycle_full.py tests/e2e/variations/test_codex_handoff.py` → collects with no errors; `grep -rn "_assert_implement_stage_order\|_assert_audit_verify_count\|drift_repair\|failing_verify_blocks" tests/` → no hits.

- [x] **Step 6: Red-stage finish E2Es.** `tests/e2e/test_e2e_finish.py::test_finish_stops_before_push_on_failing_stage` and `::test_finish_stops_before_push_on_failing_check_stage` keep their names (finish still stops before the push at the bound) and their artifact assertion. They cannot be re-measured on this host; add a comment above each saying finish now spends up to two fix attempts before its CRITICAL stop (spec D7) and that the default 300 s ceiling must be re-measured by the next token-bearing verify run. No timeout or marker change without a measurement. The two expensive red-build scenarios (`tests/e2e/variations/test_from_implemented.py::test_failing_build_blocks_publication`, `tests/e2e/test_e2e_lifecycle_full.py::test_failing_build_blocks_publication`) used to meet the red build in audit's Phase 2a; now audit's check passes and finish meets it after the squash. Their assertions (a `build` marker exists, the stage script and sentinel are untouched, nothing reaches origin) do not depend on where build goes red, so they stay unchanged; only the `"audit changed external build gate"` message in the variation may read "audit or finish changed …".

- [x] **Step 7: Commit.** `git commit -m "E2E: implement and audit are check-only; finish may run a final verify"`.

---

### Task 8: Regenerate the README

Complexity: medium

**Files:**
- Modify: `.claude/skills/regenerate-readme/SKILL.md` (lifecycle slot; Development row for `.omc/skills/verify`), `README.md` (regenerated only)
- Test: `tests/unit/test_cli.py` — new `test_readme_documents_verify_only_in_finish`

**Interfaces:**
- Consumes: final skill/layer text from Tasks 1–6 (run this task last).

- [x] **Step 1: Write the failing test** next to the other `test_readme_*` guards, in the style of `test_readme_documents_provider_selection_per_step` (recipe slot split by its `#### ` heading, README section split by its `## ` heading, both flattened): `recipe_lifecycle = recipe.split("#### `` ## The lifecycle:", 1)[1].split("\n#### ", 1)[0]` flattened contains `"final verify"` and `"only inside finish"`; README `lifecycle = readme.split("## The lifecycle", 1)[1].split("\n## ", 1)[0]` flattened contains `"check → build → verify → review → final verify"` and `"skipped when the tree is unchanged"`; `readme` contains none of `"`/omc:build` then `/omc:verify` before handoff"`, `"major milestones"`; the recipe's `| Lifecycle |` sources row contains `skills/implement/SKILL.md` and `skills/verify/SKILL.md`; the README `.omc/skills/verify` Development row contains `"subset"`.

- [x] **Step 2: Run red.** `uv run --frozen python -m pytest tests/unit/test_cli.py -q -p no:cacheprovider -k readme` → FAIL.

- [x] **Step 3: Update the recipe.** Why this is required, not optional (spec §9's open item): the recipe's Lifecycle sources row reads only the *frontmatter* of `skills/implement`, `skills/audit` and `skills/verify` and tells the writer to carry forward facts from the existing README, so without an explicit recipe statement the stale "`/omc:build` then `/omc:verify` before handoff" and "major milestones" sentences survive regeneration. Add `skills/implement/SKILL.md`, `skills/audit/SKILL.md` and `skills/verify/SKILL.md` to that row's "for authority and stage semantics" list. Then, in the lifecycle slot: implement ends on its last green `/omc:check` (no build/verify); audit's post-fix gate is `/omc:check`; finish runs check → build → verify → review → final verify (skipped when the tree is unchanged since verify passed), repairs red stages within the two-fix bound, and an agent starts verify only inside finish (the user or project may run it any time). Project-stages wording: "verify is full E2E, run by agents only inside finish". Development row: add the node-id "Running a subset".

- [x] **Step 4: Invoke `/regenerate-readme`.** Follow its steps; in step 5 use `uv run --frozen python -m pytest tests/unit/test_cli.py -q -p no:cacheprovider -k 'readme or shell_integration_documents'`. Review the diff: only verify/stage placement facts should change meaningfully.

- [x] **Step 5: Run green.** `tests/unit/test_cli.py` passes except the host-baseline `test_aws_credential_process_runs_bannerless_and_needs_no_config`.

- [x] **Step 6: Commit.** `git commit -m "README: verify runs only inside finish"` (recipe, README, test).

---

## Decisions (conductor calls, 2026-11-15)

1. **Fixes never edit a stage's own definition.** This is a conductor call grounded in tested behavior, not a user question. `tests/e2e/test_e2e_lifecycle_full.py::test_failing_build_blocks_publication` already asserts the agent never changes the external stage script ("agent changed external build gate"). Task 1 writes that rule into the layer's general stage-gate clause and pins it in its test: a fix repairs the code under test, never the stage's own definition (`.omc/skills/<stage>/`) or the script/command it runs; a stage that only passes by editing itself is the CRITICAL question.
2. **E2E timing.** Task 7 keeps existing timeouts and writes honest comments, since nothing can be measured on this host. The first token-bearing `/omc:verify` run measures them and moves any test over 300 s to `expensive`.
3. **Runner fix kept.** `scripts/e2e.sh` tolerates exit 5 from the parallel pass in `all` mode when a selection was given. Node ids only, never `-k` (Task 6).
