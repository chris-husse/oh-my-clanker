# Run the verify stage before handoff: implement's milestone gate and audit's post-fix gate

Date: 2026-10-06 · Branch: `feature/run-verify-in-implement-phase` · Slug: `run-verify-in-implement-phase`

## 1. Problem

The project's `verify` stage (full E2E) runs in exactly one place today:
`finish` Step 4, after `rebase-main` and `squash`. `/omc:implement` runs only
`/omc:check` after each task and says so explicitly ("Full E2E (`/omc:verify`)
is NOT part of the per-task loop — it belongs to major milestones (finish runs
it)"). `/omc:audit` runs one `/omc:check` after its conformance fixes, then
calls `finish`.

Consequence: when `omc review` launches a fresh audit session, the first
`verify` run of the whole lifecycle happens inside that reviewer's `finish`,
on an already-squashed branch. A failure the implementing session could have
fixed is discovered by the wrong session, after the squash, and leaves the
branch under a `wip:` message. The same shape repeats inside audit: a
conformance fix that breaks E2E is first seen by `finish`.

## 2. Goal and non-goals

**Goal.** A branch is never handed from implement to audit, and never from
audit to finish, with a red `verify` that the responsible session could have
fixed. The session that produced the change proves it passes the project's
E2E before passing it on.

**Non-goals.**

- Running `verify` per task. The user's seed keeps `verify` a once-per-phase
  milestone gate; the per-task loop stays `check`-only.
- Changing `finish`. It still runs `check → build → verify → review` on the
  squashed commit; the new gates are pre-squash guards, not replacements.
- Any CLI or config surface. `omc implement` / `omc review` stay stage-blind;
  the change is skill text, tests and docs.
- The flow skills' "task list" fallback wording when the harness has no task
  tool. Separate, smaller branch.

## 3. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | `verify` runs once at the end of implement, after the last task's `check` passes and before the handoff commit. | Start of audit only (does not meet the goal); both (three runs per lifecycle); opt-in config (new surface for a switch every project leaves on). |
| D2 | implement's end-of-build gate is `check → build → verify`, mirroring finish's order without `review`. | `verify` alone (a lint or packaging break would still reach the reviewer via finish's `build`). |
| D3 | A red gate is fixed forward and re-run, bounded to two fix-and-rerun cycles. At the bound the conductor stops with a CRITICAL question. Nothing is handed off, and no handoff commit is made, while the gate is red. | Hand off with the red stage labelled (explicitly rejected by the user: "no passing on to audit without passing verify"); unbounded fixing (the bound exists so an agent does not spend hours on blind fixes before asking). |
| D4 | audit always runs `check → build` after its disposition pass, and runs `verify` as well only when its conformance fixes changed tracked product files. Same bound, same CRITICAL stop. | Leave audit `check`-only (rejected: audit must prove `verify` still passes after its own modifications). |
| D5 | Per-task commits inside the subagent build remain `check`-gated. "Nothing committed red" applies to the handoff, not to each task commit. | Verify after every task (rejected with D1). |

## 4. Design

### 4.1 implement: Phase 2b, the milestone gate

`skills/implement/SKILL.md` gains a phase between the subagent build (Phase 2)
and the handoff (Phase 3).

- **Externalization.** Phase -1 lists four steps instead of three: plan →
  subagent build → milestone gate → handoff. The gate is a named task so the
  task list keeps it alive the way it keeps every other phase alive.
- **Trigger.** After the last task's subagent has completed, its reviews have
  passed and its `/omc:check` is green.
- **Body.** Invoke `/omc:build`, then `/omc:verify`, each as the black-box
  project-stage proxy, reading its single `OMC_STAGE` line. `"passed": true`
  (including `"configured": false`) on both → Phase 3. Before launching
  `verify` the conductor announces it on its own line and names the project's
  `verify` stage, because on some projects (this one) it is a multi-minute
  Docker run and a silent minute is a bug. A red stage follows the behavior
  layer's stage-gate rule (4.2): the skill cites it as "bounded fix-forward,
  then a CRITICAL stop" and does not restate the bound. That phrase is enough
  for a session whose global instructions file is malformed (the CLI launches
  anyway, `tests/unit/test_implement.py::test_implement_continues_when_global_section_is_malformed`)
  to stop on red rather than loop.
- **Wording change.** The sentence "Full E2E (`/omc:verify`) is NOT part of the
  per-task loop — it belongs to major milestones (finish runs it)" becomes:
  `verify` is not part of the per-task loop; it runs once in the milestone gate
  below, and `finish` runs all four stages again on the squashed commit.
- **Phase 3 unchanged** except that it is reachable only through a green gate.
  Its drift restore (lockfiles dirtied by `uv run`) now also covers drift the
  `verify` run leaves behind, since the gate runs before it.

### 4.2 The stage-gate rule: bounded fix-forward, then a CRITICAL stop

Both conductors (4.1, 4.3) need the same rule, so it is defined once, in the
behavior layer's "Validation cadence" bullet (`src/omc/distribution/AGENTS.md`,
installed into every omc session on both providers), and each skill cites it
the way they already cite the layer's model-tier policy instead of restating
it. The bound therefore lives in one place.

The rule:

- A gate stage whose `OMC_STAGE` line says `"passed": false`, or that emits no
  verdict line, dispatches one fix subagent at the heavy coding tier with the
  stage's `summary` as its brief, then runs `/omc:check`, then the gate again
  from `build`.
- **Bound:** at most two fix-and-rerun cycles, i.e. three gate runs. A flake
  that passes on rerun consumes a cycle like any other failure. A `/omc:check`
  that fails after a fix blocks the restart from `build`; repairing it consumes
  the remaining budget and never resets the bound.
- **At the bound:** the conductor stops with one CRITICAL question stating
  which stage is red, the stage's failure summary, what the two fixes tried,
  and what it needs from the user. The phase after the gate does not run:
  implement makes no handoff commit and states no continuations; audit does
  not invoke `finish`. The user's answer resumes the remaining phases without
  a new command, which is the contract implement and audit already carry.
- **Headless runs** (`omc implement --headless`, `omc review --headless`) end
  with that question as their final output. The provider exits 0 on a turn
  that ends in a question, so the CLI's return code is 0 too; the question in
  stdout is the signal. The worktree is left at the last task commit with the
  plan file still uncommitted (Phase 3 is what commits it). The user continues
  in a session in that worktree. This is the intended outcome of an unfixable
  red gate, not a failure of the tool.

### 4.3 audit: Phase 2 post-fix gate

`skills/audit/SKILL.md` Phase 2 today ends with "After any code fixes, invoke
`/omc:check` once." It becomes:

- After the disposition pass, always invoke `/omc:check` then `/omc:build`,
  whether or not anything was changed.
- If disposition step 1 fixed code (the conductor knows, it performed the
  fix; no diff computation), also invoke `/omc:verify`.
- A red stage follows the behavior layer's stage-gate rule (4.2), cited the
  same way as in 4.1: bounded fix-forward, then a CRITICAL stop folded into
  audit's existing batched-CRITICAL-question mechanism. `finish` is not
  invoked while the gate is red. The existing sentence "After any code fixes,
  invoke `/omc:check` once" is replaced by this gate; it is not pinned by
  `test_audit_skill_contract`, whose `/omc:check` and `CRITICAL` needles
  survive. A gate repair that itself changes tracked product files makes
  `verify` part of that rerun.
- The Phase -1 externalization line names the gate: gates → conformance →
  disposition → post-fix gate → trace and commit → finish.

`finish` then runs all four stages on the squashed commit as before.

### 4.4 Behavior layer and README

`src/omc/distribution/AGENTS.md`, "Validation cadence": the sentence that
`/omc:verify` is full E2E, run only after major milestones and never as a
routine dev-loop gate, stays. Two additions follow it:

- the placement clause: the end of `/omc:implement`'s build is such a
  milestone and runs `/omc:check` → `/omc:build` → `/omc:verify` once before
  handoff; `/omc:audit` re-runs check and build after its fixes, and verify
  when it fixed code; `/omc:finish` runs all four on the squashed commit;
- the **stage-gate rule** of 4.2, in full: red stage → one heavy-tier fix,
  check, gate again from build; at most two fix-and-rerun cycles; then a
  CRITICAL stop, nothing handed on.

The pinned needles ("Validation cadence", "/omc:check", "builds the world",
"major milestones", the four-stage arrow) survive. The layer reaches projects
only via `omc update`, as always.

`README.md` is not changed. The branch originally added the clause to the
old README's "intended split" sentence; main rewrote the README (PR #55)
while this branch was in flight, and the user chose at audit time to take
the rewritten README as-is. Its project-stages paragraph already says
`verify` is full E2E at major milestones; the gate placement is documented in
the behavior layer and the two skills.

### 4.5 Unchanged

- `skills/finish/SKILL.md`, `skills/check|build|verify|review/SKILL.md`: the
  proxies and their `OMC_STAGE` contract are consumed, not altered.
- `src/omc/implement.py`, `src/omc/review.py`: seeds stay `/omc:implement` and
  `/omc:audit`; no stage knowledge enters the CLI.
- `.omc/docs/gitnexus/docs/*`: generated, never hand-edited; refreshes on the
  next `/omc:document` run in the primary checkout.

## 5. Tests (red first, per `.omc/config/AGENTS.md`)

- **`tests/unit/test_plugin_manifests.py::test_implement_skill_contract`**:
  needles `/omc:build`, `/omc:verify`, the Phase 2b heading, and the citation
  of the layer's stage-gate rule; an order assertion that the gate's
  `/omc:verify` mention sits after `subagent-driven-development` and before
  `## Phase 3`; a negative assertion that "finish runs it" is gone.
- **`test_audit_skill_contract`**: needles for `/omc:build`, the conditional
  `/omc:verify` rule and the stage-gate citation. **Behavior-layer test**:
  needles for the placement clause and for the stage-gate rule (the bound and
  `CRITICAL`), since the layer is now the rule's only home.
- **`tests/e2e/lifecycle_helpers.py::_assert_implement_stage_order(markers)`**:
  the stage marker file holds a contiguous `build, verify` pair with a `check`
  before it. Called from `_assert_implemented_artifacts`, so the golden
  `implemented` stage and the headless CLI path assert on an artifact, not a
  transcript. Parametrized in `tests/unit/test_e2e_lifecycle_stages.py` like
  the finish-order test, including a case where finish's later block also
  appears.
- **Audited stage**: the golden audit did not fix code on its own (the
  pressure-test found no drift in the golden path), so the golden `audited`
  stage now commits the greeting drift explicitly before `omc review`, the
  way `test_audit_repairs_committed_greeting_drift` already does.
  `_assert_audited_artifacts` takes `drift_repair=False` by default and, when
  a scenario passes `drift_repair=True`, additionally requires at least three
  `verify` markers (implement's, audit's, finish's) via
  `_assert_audit_verify_count`. Audits that legitimately change nothing keep
  the unconditional finish-order check only. `_assert_finish_stage_order`
  already tolerates earlier blocks (unit case `implementation-review-before-finish`).
- **Red path variation**, forked from the `recorded` snapshot in
  `tests/e2e/variations/test_from_recorded.py`: `_fixture` writes a
  `verify` sentinel check next to the existing `build` one (unconditionally,
  like `build`'s), and `failing_stage="verify"` touches the sentinel file; the
  variation touches it in the forked container before sending the in-session
  implement command. The verify script's failure text says the E2E environment
  is unavailable so fix attempts are short. Assertions: no handoff (task
  commits exist, the plan file is present but uncommitted, nothing pushed),
  the marker file shows `verify` ran at least once, and the turn text asks a
  question naming `verify` (a transcript quality no artifact carries, per the
  project's test policy). It starts in the `expensive` tier like its template
  `test_implement_commits_without_publication`; a measured run under the
  240 s budget promotes it.
- **Timing**: the golden `implemented` stage records its turn seconds; the
  fixture's `build` and `verify` are `compileall` and `unittest`, so the
  expected cost is single-digit seconds over today's 38 s. The stage's timing
  comment is updated from the measured run.

## 6. Risks and side effects

- **Slow `verify` projects.** On this repo `verify` is `just e2e-tests`
  (Docker, minutes). The gate adds one such run per implement and, when audit
  changed code, one per audit. The announcement line (4.1) is the mitigation
  for "silent minute"; the once-per-phase cadence is what keeps it from being
  a routine gate.
- **Headless runs can now end on a question.** By design (D3). The final
  output carries the question with exit code 0; the branch is left at the last
  task commit with the plan file uncommitted.
- **Lockfile drift.** `uv run` inside `verify` dirties `uv.lock` in worktrees;
  Phase 3's existing restore step runs after the gate and handles it.
- **Marker-based assertions** depend on the fixture's stage scripts, which
  already append a marker per stage; no fixture redesign.
- **Behavior layer as the rule's home.** The layer applies only in repos with
  `.omc/`, which is exactly where implement and audit can run (the design-record
  gate requires an omc worktree). The CLI writes the layer before every
  implement/review launch (`agentsmd.ensure_global_section`, asserted by
  `test_implement_override_ensures_global_section_before_launch`), so E2E
  containers carry the version under test. A malformed global file is the one
  gap, covered by the skills' one-phrase citation above.
- **Codex plugin payload** copies the conductor skills from the checkout at
  test time (`tests/e2e/codex_plugin_payload.py`, `OMC_SKILLS`); no payload
  regeneration is needed for skill-text changes.

## 7. Out of scope

CLI flags or config keys; changes to `finish`; the task-list fallback wording
across the flow skills; regenerating the GitNexus docs on this branch.

## Implementation review

Auditor: Claude (Fable 5.1), 2026-10-06, `/omc:audit` in the implementation
worktree. Branch diff against `origin/main` walked against sections 2–7.

- **Important · record is stale · §5 "Audited stage"** —
  `tests/e2e/golden/test_golden_claude.py:235-268`,
  `tests/e2e/lifecycle_helpers.py:644-648,663-682`. The record assumed the
  golden audit already fixed code and asked for an unconditional three-verify
  requirement. The golden path had no drift, so the implementation commits
  the greeting drift explicitly in the `audited` stage and gates the
  three-marker requirement behind `drift_repair=True` (passed only by the two
  drift scenarios). Correct implementation; contradicts no brainstorm decision.
  Disposition: record amended (this audit).
- **Minor · record is stale · §4.2 / §4.3** —
  `src/omc/distribution/AGENTS.md:50-52`, `skills/audit/SKILL.md:86-87`. The
  layer spells out that a failed post-fix `/omc:check` blocks the restart from
  `build` and never resets the bound; the audit skill adds that a gate repair
  touching tracked product files brings `verify` into the rerun. Both are
  consistent refinements of D3/D4. Disposition: one sentence added to each
  section (this audit).
- **Minor · record is stale · work outside §4** —
  `tests/e2e/conversation.py:139-205`,
  `tests/e2e/test_e2e_global_instructions.py:19,193-201`. Two harness repairs
  surfaced by the live `verify` run and authorized by the user during
  implementation: the Claude stream parser now correlates background
  `task_started` events with their Agent tool when the tool input lacks
  `run_in_background`, and the Codex global-instructions oracle tolerates
  only the observed nonfatal `ultrafast_mode` feature diagnostic. Both carry
  red-first unit regressions (`tests/unit/test_e2e_conversation.py`,
  `tests/unit/test_e2e_global_instructions.py`). Disposition: recorded here;
  the plan's "Authorized repair" and "Gate repair" sections hold the
  authorization. No record section change.
- **Minor · structure · §4.3** — `skills/audit/SKILL.md:75`. The post-fix
  gate has its own `## Phase 2a` heading where the record phrases it as the
  tail of Phase 2. The externalization line and the check → build → verify
  order match the record; `test_audit_skill_contract` spans both headings.
  Disposition: listed, no change.

- **Minor · record is stale · §4.4 README** — `README.md` (rebase onto
  `origin/main` at `finish`). Main's README rewrite (PR #55) conflicted with
  the branch's one-sentence README addition; the user chose to keep main's
  README unchanged. Disposition: section 4.4 amended to say the README is
  not changed (this audit, after the user's answer).

Sections 4.1, 4.5, 7 and the remaining §5 tests conform. Post-audit
gate: `/omc:check` green (1,283 passed), `/omc:build` green; no code was
changed by this audit, so `verify` is left to `finish`.

## Deliberate complexity

None.
