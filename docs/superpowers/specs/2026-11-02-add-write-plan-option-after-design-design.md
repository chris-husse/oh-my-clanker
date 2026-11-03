# Write the plan in the design session: `/omc:write-plan` as an optional step between design and implement

Date: 2026-11-02 · Branch: `feature/add-write-plan-option-after-design` · Slug: `add-write-plan-option-after-design`

## 1. Problem

Today the implementation plan is always written by whichever provider runs
`/omc:implement`. `skills/implement/SKILL.md` Phase 1 dispatches the Plan
worker, invokes `superpowers:writing-plans` on the committed record,
pressure-tests each major section with `/omc:explain`, and then Phase 2 starts
the build in the same turn. When the user hands off to another provider
(`omc implement --claude|--codex`), that fresh session plans and builds; the
session that held the whole design conversation — the one with the richest
context for turning the record into tasks — never gets to write the plan.

`/omc:design` ends with exactly two continuations: build here, or build on
another provider. There is no "plan here, build elsewhere".

## 2. Goals and non-goals

**Goals.**

- After `/omc:design` the user can type `/omc:write-plan` (`$omc:write-plan`
  on Codex) and get a committed plan for this slug without the build starting.
- A later `/omc:implement` — in the same session or in a fresh one from
  `omc implement --claude|--codex` — builds from that plan instead of writing
  a second one.
- The planning logic (Plan worker, `writing-plans`, the `Complexity:`
  directive, the per-section `/omc:explain` pressure test) exists in exactly
  one place. Each conductor still reads its own `OMC_MODELS` verdict for the
  workers it dispatches, as `audit` and `review` do today.

**Non-goals.**

- No change to the `omc implement` / `omc review` CLIs. The seeded session
  finds the plan on disk through the skill text; the CLI stays plan-blind.
- No rename of `/omc:plan` (the brainstorm setup skill). The naming adjacency
  is accepted (D4).
- No other new handoff points (for example switching provider mid-build).

**Anti-goals.**

- `/omc:write-plan` touching anything outside `docs/superpowers/plans/`, or
  starting the build.
- `/omc:implement` silently writing a second plan when one exists.

## 3. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | `/omc:write-plan` ALWAYS commits the plan file (and only the plan file) at its end — in direct use and when `/omc:implement` calls it. This supersedes the 2026-10-06 rule that the plan stays uncommitted while the milestone gate is red (user: "just commit it who cares"). That rule existed only because Phase 3 was the plan's sole committer; nothing in the CLI, `finish`, `squash` or `audit` depends on the plan being uncommitted (`squash` folds every commit and dirty file over `origin/<base>`; `audit` reads the plan as context; `run_headless` is plan-blind). | Commit only in direct use and leave implement's plan uncommitted until Phase 3 (two commit behaviours for one skill). |
| D2 | The planning logic lives only in `skills/write-plan/SKILL.md`; implement's Phase 1 invokes `/omc:write-plan` as a black box. | Duplicate Phase 1 into the new skill (two copies to keep aligned). |
| D3 | `/omc:design`'s closing statement offers three continuations: implement here, write-plan here, `omc implement` elsewhere. | Leave design's statement alone and let users discover the skill (an invisible option is no option). |
| D4 | The skill is named `write-plan`; `/omc:plan` keeps its name. | Rename `/omc:plan` to free the name (a rename across skills, tests, the behavior layer and the README for a naming nicety). |
| D5 | Validation is unit contract tests over the skill texts plus one E2E variation forked from the `recorded` snapshot. | No E2E (the skill's on-disk effect would be unproven); a full cross-provider E2E (D8). |
| D6 | `omc implement` and `omc review` CLIs are not changed; the seeded `/omc:implement` finds the plan on disk. | A CLI gate that refuses when the plan is missing (the skill already handles both cases). |
| D7 | A tracked plan for the slug (committed by an earlier write-plan, ticked boxes or not) is never rewritten; to re-plan, delete the file and run `/omc:write-plan` again. | A second dated plan (ambiguous for implement and audit); a `--force` style argument (new surface for a rare case). |
| D8 | No cross-provider E2E for write-plan-then-implement-elsewhere. | Another >300 s implement run; implement's plan-exists path is the same code either way. |

D6–D8 are the Design worker's calls, stated in the primer and unchallenged.

## 4. Design

### 4.1 The lifecycle after the change

```
omc design <context>      → brainstorm → /omc:design (record committed)
                                           │
                 ┌─────────────────────────┼──────────────────────────┐
                 ▼                         ▼                          ▼
        /omc:implement here      /omc:write-plan here        omc implement --claude|--codex
        (plans + builds)         (plan committed, stop)      (fresh session plans + builds)
                                           │
                                           ├── /omc:implement here (builds from the plan)
                                           └── omc implement --claude|--codex (builds from the plan)
```

`/omc:write-plan` is optional. The lifecycle with it skipped is unchanged
from today except for where the plan commit happens (D1).

### 4.2 New skill: `skills/write-plan/SKILL.md`

A user-facing conductor (it externalizes its flow into the task list before
its first sub-skill call, like every other conductor). It owns the plan the
way `design` owns the record.

- **Authority.** `/omc:write-plan` (`$omc:write-plan` on Codex) authorizes
  writing, pressure-testing and committing the implementation plan for this
  slug — nothing else. It never builds, never touches product code, never
  pushes. When it ends, the session stays open.
- **Gate.** Run `omc internal design-record` and read its single
  `OMC_DESIGN_RECORD` line. Anything but `"ok": true` → refuse with the
  verdict's `message` and stop (missing record → point at `/omc:design`;
  unclean → the design session died before its commit; ambiguous → a human
  decision). The same gate implement already uses.
- **Existing plan for the slug** (`docs/superpowers/plans/*-$OMC_SLUG-plan.md`),
  decided by whether git tracks the file, since after D1 every finished
  write-plan has committed its plan:
  - tracked (committed by an earlier write-plan, with or without the ticked
    boxes of a build in progress) → say that a plan exists at `path`, state
    the continuations, write nothing. Never a second dated plan; to re-plan,
    delete the file first (D7).
  - untracked (an interrupted `write-plan` died before its commit, or an
    implement run interrupted before its handoff commit) → inspect it; a
    plan that already has ticked tasks is committed as is (no new pressure
    test, ticks kept); otherwise finish the pressure test, then commit it.
- **Planning.** Implement's current Phase 1 text, moved here unchanged in
  substance: run `omc internal models` once and read `OMC_MODELS` (an
  `"ok": false` verdict is a blocker); dispatch a Plan worker with
  `tasks.plan.model` / `tasks.plan.effort` (fresh worker on Claude; fresh or
  partial-history fork on Codex for a model override; orchestrator model with
  a one-line note when the harness cannot pin one); the worker invokes
  `superpowers:writing-plans` on the committed record with the verbatim
  `Complexity: simple | medium | high` directive, then one `/omc:explain` per
  major plan section to pressure-test the implementation choices; it returns
  the plan path and any CRITICAL questions to the main session, which asks
  them in one batch and sends the answers back.
- **End — always commit.** Commit the plan file only
  (`docs/superpowers/plans/…-plan.md`), whether invoked directly or from
  implement (D1). Everything else in the tree is left exactly as found;
  write-plan runs no tests, so it produces no tool drift of its own, and
  anything already dirty is named in the summary, never committed or
  discarded.
- **Closing, direct use.** A short summary of the plan (task count, the
  pressure-test findings that changed it), then this statement — a
  statement, not a question:

  > The plan is committed. To build on this provider, type `/omc:implement`
  > here (`$omc:implement` on Codex). To build on another provider, exit this
  > session and run `omc implement --claude` or `omc implement --codex` in
  > this worktree.

- **Closing, called from `/omc:implement`.** Skip the summary and the
  statement; return the plan path to the caller, which continues in the
  same turn. The skill tells the two cases apart the way `grug` and
  `ticket-sync` already do: through a leading mode word in its `$ARGUMENTS`
  — `implement` when the implement conductor invokes it
  (`/omc:write-plan implement`); anything else is direct use, and direct-use
  free text is passed to the Plan worker as a late constraint, the way
  `/omc:design` treats its input. No new mechanism.
- **Completion contract.** Complete when the plan is committed and, in
  direct use, the two continuations have been stated. Waiting for the user's
  next command is a valid stop. Agreement or `ok` is not `/omc:implement`.

### 4.3 `skills/implement/SKILL.md`

- **Phase 0.** The record gate only; implement does not classify an
  existing plan. write-plan Step 2 is the one home for that (§4.2).
- **Phase 1 — plan.** Always invoke `/omc:write-plan implement` as a black
  box and take the plan path it returns. write-plan decides by git tracking
  (§4.2): a tracked plan returns its path and writes nothing; an untracked
  one is finished and committed; none means a new plan. Either way the plan
  is committed before Phase 2 (D1), and the build continues from the next
  unfinished task of the returned plan (task 1 when none are ticked). The
  Plan worker, `writing-plans` directive and per-section `/omc:explain` text
  move out of implement into write-plan (D2); the only thing that crosses
  the cut is the plan path. The sentence that Phase 1 → 2 is not a gate
  stays: the build starts immediately once the plan path is in hand.
- **Phase 2 — build.** Unchanged except for one opening line: run
  `omc internal models` once and read its `OMC_MODELS` verdict before the
  first task dispatch (an `"ok": false` verdict is a blocker). Today that
  call lives in Phase 1; moving it here keeps the plan-exists path — where
  write-plan returns before its Plan worker — supplied with the task choices
  Phase 2 dispatches on. write-plan runs the same cheap verb for its own
  Plan worker, as `audit` and `review` already do for theirs.
- **Phase 2b — milestone gate.** The red-gate sentence "keep those task
  commits and the plan uncommitted; the gate blocks only the handoff commit"
  becomes: keep those task commits; the plan was already committed by
  `/omc:write-plan`; the gate blocks only the handoff commit. This supersedes
  the 2026-10-06 record's "plan file still uncommitted" wording (D1). The
  old wording described what Phase 3 had not yet done, not a guarantee
  anything relied on; the headless outcome is now "last task commit, plan
  committed, no handoff commit". Because the plan commit now follows the
  record commit, the Phase 2b and Phase 3 "at least one task commit" guards
  define a task commit as one touching a path outside `docs/superpowers/`,
  so the record and plan commits never satisfy them.
- **Phase 3 — hand off.** Unchanged in substance: it commits what the build
  left behind — the plan's ticked checkboxes and any tracked product or
  documentation changes the workers did not commit — restores known tool
  drift, and states the three audit continuations.

### 4.4 `skills/design/SKILL.md`

Step 5's closing statement becomes:

> The design record is committed. To plan and build on this provider, type
> `/omc:implement` here (`$omc:implement` on Codex). To write the plan here
> and build on another provider, type `/omc:write-plan` here
> (`$omc:write-plan` on Codex). To plan and build on another provider, exit
> this session and run `omc implement --claude` or `omc implement --codex` in
> this worktree.

The completion contract's "two continuations" becomes "three continuations";
its Step 0 task list still ends at waiting for the user's next command. The
description's "first of the two authority words" phrasing is left as is:
write-plan is optional and does not change the order of the authority words
(4.5).

### 4.5 Behavior layer: `src/omc/distribution/AGENTS.md`

In the "Lifecycle scope is binding" bullet, after the `/omc:design` sentence:

> `$omc:write-plan` in Codex, `/omc:write-plan` in Claude, is optional: it
> requires a committed design record and authorizes writing, pressure-testing
> and committing the plan — nothing else; the session then stays open, and a
> later `/omc:implement` builds from that plan.

"Three direct user invocations carry authority, in order" stays true as
written: write-plan is an optional step between the first and the second
word, not a fourth word. In the "Externalize a composed flow" bullet,
`/omc:write-plan` joins the conductor list, with "write-plan lists end at the
committed plan and the stated continuations" added to the per-flow endings.
The "Lifecycle scope is binding" bullet's list of commands that `omc design`
context treats as data ("`/omc:design` or `/omc:implement`") also gains
`/omc:write-plan`.
The layer reaches other projects through `omc update`, as always.

### 4.6 Unchanged

- `src/omc/implement.py` (`run_implement`) and `src/omc/review.py`: the
  seeds stay `/omc:implement` and `/omc:audit`; no plan knowledge enters the
  CLI (D6). `wtconfig.find_design_record` keeps its record-only cleanliness
  check.
- `skills/audit/SKILL.md`: it already reads the plan when present.
- `skills/plan/SKILL.md`: untouched (D4).
- Model selection: write-plan uses the same `tasks.plan` choice implement
  used; no new task type.
- Skill registration: the Codex manifest points at the directory
  (`"skills": "./skills/"`) and the Claude plugin picks up `skills/` by
  convention, so the new directory is picked up without a manifest edit;
  the merge-time version stamp refreshes the harness caches as for every
  skill change.

### 4.7 README

`README.md` is generated and never hand-edited. Its lifecycle section is
dictated by the recipe `.claude/skills/regenerate-readme/SKILL.md` ("Name
both in-session and fresh-session paths"; "Implement: … plan/subagent
build"), so the recipe gains the optional write-plan path — the flowchart
edge Record → write-plan → Build, the three continuations in the Design
bullet, a short write-plan paragraph (needs the committed record,
pressure-tests via explain, commits only the plan, stops with the session
open), and the plan's source in the Implement bullet — and
`/regenerate-readme` runs after the skill lands. A new guard,
`tests/unit/test_cli.py::test_readme_documents_optional_write_plan`, pins
the recipe's Design bullet and the README lifecycle section.

## 5. Tests (red first, per `.omc/config/AGENTS.md`)

Unit (`tests/unit/test_plugin_manifests.py`):

- `USER_FACING_SKILLS` and `CONDUCTORS` gain `"write-plan"`, which pulls the
  new skill into the frontmatter, externalize-first and completion-contract
  checks.
- New `test_write_plan_skill_contract`: needles `omc internal design-record`,
  `OMC_DESIGN_RECORD`, `omc internal models`, `OMC_MODELS`, `writing-plans`,
  `` `Complexity:` ``, `simple | medium | high`, `/omc:explain`, the
  plan-already-exists handling (tracked → write nothing; untracked → finish
  and commit), the plan-file-only commit scope, both continuations
  (`/omc:implement`, `omc implement`), the called-from-implement return,
  `CRITICAL` and `main session`; and negative assertions that
  `subagent-driven-development` and `missing `Complexity:`` do not appear
  (the default belongs to the build, not the plan).
- `test_implement_skill_contract`: `writing-plans` leaves implement's
  needles, replaced by `/omc:write-plan`; `` `Complexity:` ``,
  `missing `Complexity:`` and `omc internal models` stay (Phase 2 reads the
  label and the verdict). The order check becomes record gate →
  `/omc:write-plan` → `omc internal models` → `subagent-driven-development`
  → milestone gate → `/omc:build` → `/omc:verify` → handoff.
- `test_implement_keeps_task_commits_before_a_red_milestone_gate`: the
  `"plan uncommitted"` needle becomes the new wording (the plan was already
  committed by write-plan; the gate blocks only the handoff commit).
- `test_task_model_skill_routing_contract`: the Plan-worker assertions
  (`Plan`, the `simple | medium | high` directive) are asserted on the
  write-plan text where they now live; implement keeps `omc internal models`,
  `missing `Complexity:``, `medium`, `Review` and `reasoning_effort`.
- `test_design_is_user_facing_and_owns_the_record`: also asserts
  `/omc:write-plan` appears in the design skill.
- `test_behavior_layer_names_three_authority_words`: also asserts the new
  write-plan sentence (`/omc:write-plan`, `$omc:write-plan`, "is optional")
  and `/omc:write-plan` in the "Externalize a composed flow" bullet.
- Codex plugin payload: `tests/e2e/codex_plugin_payload.py::OMC_SKILLS` and
  the fixture loop in `tests/unit/test_e2e_codex_auth.py::_plugin_payload_fixture`
  gain `write-plan`. `omc implement --codex` from a `recorded` worktree now
  invokes `/omc:write-plan` through the Codex plugin, so a stale cached copy
  of it would silently run the old text; the staleness check must cover it.
  `test_installed_write_plan_skill_is_required` pins that check.
- `tests/unit/test_lifecycle_command.py`: the default E2E run collects
  `test_write_plan_commits_plan_only` (a red test inside `just check` for the
  new variation).
- `tests/unit/test_cli.py::test_readme_documents_optional_write_plan` (§4.7).

E2E (`tests/e2e/`):

- `tests/e2e/variations/test_from_recorded.py::test_failing_verify_blocks_implementation_handoff`
  drops the "plan is present but uncommitted" assertion and instead asserts
  the new plan is committed at HEAD (planning ran through write-plan). The
  task-commit count, verify marker, no-publish and judge boundaries stay.
- New variation `test_write_plan_commits_plan_only` in the same file, forked
  from `recorded`: send `/omc:write-plan`, wait the turn, then assert exactly
  one new file under `docs/superpowers/plans/` is present and committed at
  HEAD; `git diff --name-only <recorded head>..HEAD` lists only paths under
  `docs/superpowers/plans/`; the working tree is clean; remote refs are
  unchanged; the design record digest is unchanged. Expected cost is the
  planning half of implement's measured 145 s run (~1–3 min of model time,
  unmeasured); it starts in the default tier with a 240 s wait like its
  neighbour and moves to `expensive` only if the measured run demands it.
- `tests/e2e/golden/test_golden_claude.py::test_stage_recorded` judge rubric:
  "names both ways to continue" becomes the three ways (implement here,
  write-plan here, `omc implement` with a provider flag).
- No cross-provider E2E (D8). `test_codex_handoff.py::test_codex_implement_handoff_from_claude_record`
  already drives implement's Phase 1 on Codex and `_assert_implemented_artifacts`
  already requires a new committed plan, so write-plan-called-from-implement
  on Codex is covered by the existing variation once the payload check (above)
  includes the skill.

## 6. Risks and side effects

- **Plan committed before a green gate.** By D1 the plan commit now precedes
  the build on every path. A red milestone gate leaves the branch at the last
  task commit with the plan committed; nothing else changes about the stop.
  The `finish` squash folds the plan commit in as it folds everything else.
  The task-commit guards exclude `docs/superpowers/` paths so the plan
  commit cannot stand in for a task commit (§4.3).
- **Two entry modes in one skill.** write-plan behaves slightly differently
  when implement calls it (no closing statement, returns the path). This is
  the price of one copy of the planning logic (D2) and is listed under
  Deliberate complexity.
- **Stale plan.** A plan written by write-plan, then a record amended by hand
  before `/omc:implement`, builds from the old plan. Implement reads the record
  in full in Phase 0 and audit treats the record as truth when the two differ,
  which is today's behaviour for an interrupted run; D7 gives the user the
  remedy (delete, re-plan).
- **Skill count.** One more user-facing skill, one more conductor in the
  layer's list. Both plugins pick it up by directory; the README regenerates.
- **E2E runtime.** One more variation from `recorded` (~1–3 min). The red-gate
  variation's cost is unchanged.

## 7. Out of scope

A CLI flag or config key for write-plan; renaming `/omc:plan`; a
mid-build provider switch; regenerating the GitNexus docs on this branch.

## Implementation review

**2026-11-02 — auditor: Claude (`/omc:audit`, Review worker on opus).** No
code deviations; D1–D8, the non-goals/anti-goals, §4.6 and §7 hold. Six
record-is-stale findings, all dispositioned by amending this record (none
contradicts a Decisions row):

1. Important · record stale · §4.2, §4.3 · `skills/implement/SKILL.md:45-61`,
   `skills/write-plan/SKILL.md:64-69` — Phase 0 routes on git tracking
   (tracked skips Phase 1; untracked goes through write-plan, which commits a
   ticked plan as is). Amended §4.2 and §4.3.
2. Minor · record stale · §4.3, §6 · `skills/implement/SKILL.md:114-116`,
   `:139-141` — task-commit guards exclude `docs/superpowers/`. Amended §4.3
   and §6.
3. Minor · record stale · §4.5 · `src/omc/distribution/AGENTS.md:9` —
   data-imperatives list names `/omc:write-plan`. Amended §4.5.
4. Minor · record stale · §4.7, §5 · `tests/unit/test_cli.py:318-336`,
   `tests/unit/test_lifecycle_command.py:67-68`,
   `tests/unit/test_e2e_codex_auth.py:398-405` — README guard and two extra
   red tests. Amended §4.7 and §5.
5. Minor · record stale · §4.2 · `skills/write-plan/SKILL.md:22-29`,
   `:131-134` — leading mode word `implement`, direct-use free text passed to
   the Plan worker. Amended §4.2. Riding: a hand-typed
   `/omc:write-plan implement` enters caller mode with no caller (the skill
   still never builds); not changed.
6. Minor · work not in the record · — · `tests/e2e/codex_plugin_update.py:112`,
   `tests/unit/test_e2e_codex_plugin_update_support.py`,
   `tests/e2e/test_e2e_gitnexus.py:391-473` — gate repairs for two E2E
   breakages main's e26eba6 introduced (stale `update_gitnexus` stub; the
   embeddings refresh budget, 150 s → 205 s within the same 295 s total).
   Accepted; named in the MR description.

Unverified: the expensive-tier Codex cross-provider evidence run
(`tests/e2e/variations/test_codex_handoff.py`) was not run on this branch;
the gating `just codex-gate` (10/10) and the default E2E run (golden 5/5,
66/66) passed.

## Deliberate complexity

- write-plan skips its closing summary and statement when `/omc:implement`
  invokes it and returns the plan path instead — two behaviours in one skill,
  kept so the planning logic has exactly one home (D2).
