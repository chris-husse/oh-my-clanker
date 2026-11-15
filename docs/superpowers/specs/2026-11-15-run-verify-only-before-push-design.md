# Run the verify stage only inside finish: once before review, once last before the push

Date: 2026-11-15 · Branch: `feature/run-verify-only-before-push` · Slug: `run-verify-only-before-push`

Partially supersedes `2026-10-06-run-verify-in-implement-phase-design.md`
(section 8 names the superseded decisions).

## 1. Problem

The project's `verify` stage is the expensive tier: on this repo it is
`just e2e-tests`, a Docker suite driving real LLMs. An agent starts it in
three places per lifecycle today:

1. `/omc:implement` Phase 2b, the milestone gate (`build → verify` after the
   last task's green check);
2. `/omc:audit` Phase 2a, the post-fix gate (`check → build`, plus `verify`
   when a conformance fix changed tracked product files);
3. `/omc:finish` Step 3 (`check → build → verify → review` on the squashed
   commit).

Two things are wrong with that.

- **Cost.** Up to three full E2E runs per lifecycle, two of them on trees
  that later phases still change (audit fixes, the squash and rebase, review
  fixes), so their green result is not the result that gets published.
- **A hole at the end.** `finish` runs `verify` *before* `review`. `review`
  fixes code (the grug and human-review "fix first" path) and writes waivers,
  and `finish` amends those changes into the commit and pushes. What is
  published is therefore a tree `verify` never saw.

**Why the two early gates exist.** The 2026-10-06 record added them because
`finish` stopped at the first red stage: a red verify was first seen by
whichever session ran finish (often a fresh reviewer session), after the
squash, and the branch was left under a `wip:` message for the user. That
reason is answered here differently: finish no longer stops on red, it
repairs (D7), so the session that meets the failure is also the one that
fixes it.

The user's request, verbatim: "The `verify` gate must only be run at the very
end. After all reviews are done, after everything else is fixed. The project
can invoke verify when it wants, but YOU only do it as the very last step
before pushing. Your job is to make sure it passes, fix issues, retry the
pieces that failed and once they pass run the whole thing again to ensure you
didn't break anything else. And that's it. For everything else you just use
/check". And the later correction: "It actually also needs to run once after
rebase but before the review, because it may turn up things that need to also
be reviewed".

## 2. Goal and non-goals

**Goal.** An agent starts `verify` only inside `finish`'s stage sequence,
after the squash and rebase, in two positions:

- the **pre-review verify**, after `check` and `build`, so that what it turns
  up and the fixes for it are part of what `review` reviews;
- the **final verify**, after `review` and immediately before describe + push,
  so that the published tree is a tree `verify` passed.

Everything else an agent validates with `/omc:check`.

A red `verify` is the agent's job to repair, within the two-fix bound: fix,
`/omc:check`, rerun only the pieces that failed when the project's verify
skill has a "Running a subset" section, then `/omc:build` once and the whole
stage again to prove the fix broke nothing else.

**Non-goals.**

- Forbidding the user or a project from running `verify`. `/omc:verify` typed
  by the user, and a project's own instructions that call its verify command,
  stay allowed at any time. Only runs the agent starts on its own initiative
  are restricted.
- Any CLI, config or `OMC_STAGE` contract change. The change is skill text,
  the behavior layer, tests and docs.
- Changing `/omc:rebase-main`'s standalone gate. It is already check-only.

**Anti-goals.**

- Unbounded blind fix loops. The two-fix bound and the CRITICAL stop stay.
- A second full E2E run on a tree `verify` already passed.

## 3. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | implement's Phase 2b milestone gate is removed. The last task's green `/omc:check` is the final gate before the handoff. | Keep `build → verify` at the end of implement (user: "for everything else you just use /check"). |
| D2 | audit's Phase 2a post-fix gate is `/omc:check` only: no `build`, no conditional `verify`. | Keep `check → build` and the conditional `verify` (same user sentence; finish runs build and both verifies on the tree that is actually published). |
| D3 | finish's stage order is `check → build → verify → review → final verify → describe + push`. | A single verify after review (the user's correction: fixes for verify failures would go unreviewed); today's order with no final verify (review's changes publish unverified). |
| D4 | The verify repair loop: one fix subagent briefed with verify's failure output → `/omc:check` → a targeted rerun of the failed pieces through the `/omc:verify` proxy → when that is green, `/omc:build` once and the full `verify`. A project whose verify skill has no "Running a subset" section skips the targeted rerun: `/omc:check` → `/omc:build` once → the full `verify`, one full run per cycle. A full run that comes back red needs the next fix. Fixes are amended into the squashed commit. The loop applies to both verify runs. | Rerun the full stage after every fix (minutes of Docker and LLM calls to learn whether one test is fixed); stop and hand a red verify to the user (user: "Your job is to make sure it passes"). |
| D5 | A project's verify skill MAY document how to run a subset, in a section titled "Running a subset". Without that section the repair loop skips the targeted rerun (D4), so a cycle never runs the full stage twice; the `/omc:verify` proxy still runs the full stage for a direct `rerun only:` call. | A required subset contract for every project (most projects' verify is one command; a mandatory section is integration work for nothing). |
| D6 | One counter: fix dispatches (the layer's "fix-and-rerun cycles"; a cycle is one fix plus the reruns that follow it). Two are allowed, then one CRITICAL question. Reruns, targeted or full, are not counted on their own. The two are shared across finish's whole stage sequence (the layer's existing "never resets the bound"). | A fresh budget per stage or per verify run (up to ten blind fixes in one finish); unbounded repair. |
| D7 | Every red stage in finish follows the behavior layer's bounded fix-forward stage-gate rule. finish's "stop at that stage … leave the branch squashed so the user can fix and re-run" wording goes. | Keep finish as the one conductor that stops on the first red stage (contradicts the user's "fix issues, retry"; implement and audit already fix forward). |
| D8 | User- or project-initiated verify runs remain allowed; only agent-initiated runs are restricted. | A hard refusal inside the `/omc:verify` proxy outside finish (the proxy cannot tell who invoked it, and the user said "the project can invoke verify when it wants"). |
| D9 | The final verify runs only when `git rev-parse HEAD^{tree}` differs from the tree recorded when the pre-review verify (the most recent full verify) passed. Otherwise it is skipped and reported as "final verify skipped — tree unchanged since verify passed". | Always run it (a second full E2E on a byte-identical tree, the anti-goal); skip it whenever review "made no code fixes" (a judgment call where a hash comparison is exact). |
| D10 | After a verify fix, `/omc:build` runs once before the full verify that follows. | Check only (this repo's check does not run ruff; build takes seconds and a lint break would otherwise publish). |
| D11 | finish's repair loop and skip rule get unit-level text-assertion coverage only; no live E2E drives them. | A live red-verify-in-finish variation (a multi-minute real-LLM scenario for prose a text assertion pins). |
| D12 | Fixes from the final verify's repair loop are amended without rerunning `review`. Fixes from the pre-review verify do get reviewed, by position. A red `review` under the general clause reruns `build → verify → review → final verify`. | Rerun review after every final-verify fix (review and verify could ping-pong with no end inside the bound; the user asked for verify to be the last step). |
| D13 | A fix repairs the code under test, never the stage's own definition (`.omc/skills/<stage>/`) or the script or command it runs. A stage that only passes by editing itself is the CRITICAL question, asked at once, without spending the remaining cycles. | Let fixes edit the stage (a red gate would turn green by being weakened; `tests/e2e/test_e2e_lifecycle_full.py::test_failing_build_blocks_publication` caught an agent that changed the external build gate). |

## 4. Approaches considered

- **A (chosen): the loop lives in the behavior layer's stage-gate rule.**
  The rule gains a verify clause; `finish` cites it for both verify runs the
  way implement, audit and rebase-main already cite the rule. One home for the
  bound and the loop.
- **B: the loop lives inside the `/omc:verify` proxy.** Rejected: the proxy
  also runs when the user or a project invokes it, and it would then start
  fixing code on its own, which D8 excludes. A stage proxy reports; the
  conductor decides.
- **C: a single verify after review.** Rejected by the user's correction: a
  verify failure can turn up things that need review, and its fix would be
  published unreviewed.

## 5. Design

### 5.1 Behavior layer (`src/omc/distribution/AGENTS.md`)

The layer is installed into every omc session on both providers and is where
implement, audit, rebase-main and (new) finish read the gate rule. Three
bullets change.

- **Publish through `/omc:finish`.** The stage arrow becomes `/omc:check` →
  `/omc:build` → `/omc:verify` → `/omc:review` → final `/omc:verify`, with
  the note that the final run is skipped when the tree is unchanged since
  verify passed.
- **Validation cadence.** Rewritten around one statement: `/omc:check` gates
  everything while working, including the end of implement's build and
  audit's post-fix pass; `/omc:build` builds the world; an agent starts
  `/omc:verify` only within `/omc:finish` (before review, and last before the
  push); the user or the project may run it at any time. The implement and
  audit milestone sentences are deleted.
- **Stage-gate rule.** The general clause stays as it is: verdict handling,
  one heavy-tier fix subagent, `/omc:check`, restart from `/omc:build` and
  rerun the remaining stages in order, the failed-check rule, the two-cycle
  bound, the CRITICAL question, headless behavior. Added to it:
  - **the verify clause.** A red `/omc:verify` is repaired this way instead of
    by the restart from build: the fix subagent is briefed with verify's
    failure output; then `/omc:check`; then `/omc:verify` asked to rerun only
    the failed pieces. Still red → the next fix. Green → `/omc:build` once,
    then the full `/omc:verify`. Without a "Running a subset" section in the
    project's verify skill the targeted rerun is skipped: `/omc:check`, then
    `/omc:build` once, then the full `/omc:verify`, one full run per cycle.
    A red full run needs the next fix, briefed with the pieces that are red
    now. A green full run continues with the stage after verify; nothing
    before it is rerun.
  - **the stage-definition guard (D13).** A fix repairs the code under test,
    never the stage's own definition or the script or command it runs; a
    stage that only passes by editing itself is the CRITICAL question at once.
  - **one counter.** The bound stays "at most two fix-and-rerun cycles",
    and a cycle is one fix dispatch with whatever reruns follow it, targeted
    or full. Reruns are never counted on their own. The parenthetical "or
    three gate runs total" goes, because a verify cycle can hold more than
    one run. `rebase-main`'s check-only citation of the rule reads the same
    as before. The two cycles are per conductor run, shared by every stage
    in it.
  - **"No later phase runs on a red gate"** loses its implement and audit
    verify wording and gains finish: implement makes no handoff on a red
    check, audit invokes no `finish`, finish pushes nothing.

The rule keeps one home. finish does not restate the bound or the loop; it
cites the rule by its name, "bounded fix-forward, then a CRITICAL stop", which
is enough for a session whose global file is malformed to stop on red rather
than loop (the reasoning the 2026-10-06 record gave for the same citation).

### 5.2 `skills/finish/SKILL.md`

Step 3 is retitled to the new order and becomes the only place an agent
starts `verify`.

- **Order.** `check` → `build` → `verify` → `review` → final `verify`. Check
  stays first as the cheap fail-fast gate.
- **Announcement.** Before each verify run the conductor says on its own line
  that the project's `verify` stage is starting and which of the two runs it
  is. On this repo it is a multi-minute Docker run; a silent minute is a bug.
- **Tool drift.** A stage run can dirty tracked files it does not mean to
  change (on this repo `uv run` touches `uv.lock` in a worktree). After each
  stage run (check, build and verify, targeted reruns included), known tool
  drift is restored, not amended: the rule implement's handoff applies today.
  Otherwise the final verify would change the tree it just passed, and
  `create-mr` requires a clean working tree.
- **Tree record.** Each time a full verify passes, the conductor records
  `git rev-parse HEAD^{tree}` in its task list entry for the final verify.
  The task list is the one place finish already relies on to survive a long
  flow; no file is written.
- **Skip rule (D9).** After `review` (and its amend), compare the current
  `HEAD^{tree}` with the recorded one. Equal → skip the final verify and
  report "final verify skipped — tree unchanged since verify passed".
  Different → run it. A review that only wrote a waiver into the design
  record changes the tree, so the final verify is invoked; what the project
  does with a documentation-only change is the project skill's call (this
  repo's says such changes need not repeat successful model calls).
- **Red stages (D7).** Every red stage follows the layer's stage-gate rule.
  The "stop at that stage" bullet is replaced. Fixes are amended into the
  squashed commit, as stage-made tracked changes already are. A red `review`
  falls under the general clause, so the sequence restarts from `build` and
  passes through `verify`, `review` and the final verify again. A red final
  verify falls under the verify clause, which reruns nothing before verify:
  its fixes are amended and pushed without another `review` (D12).
- **At the bound.** One CRITICAL question; the branch stays squashed and
  nothing is pushed. The user's answer resumes the remaining steps.
- **Bookkeeping.** The frontmatter description, the Step 0 task list (which
  today says "the four project stages") and the Step 5 report name both
  verify runs; the report states whether the final one ran or was skipped.

Step 5's "address review comments" loop is unchanged: it amends and re-runs
`create-mr`, and starts no stage. Running verify there is the user's call.

### 5.3 `skills/implement/SKILL.md`

- Phase 2b is removed. Phase -1 lists three phases again: plan → subagent
  build → handoff, and the intro's "Four phases" follows.
- The per-task paragraph says full E2E never runs in implement; `finish` runs
  verify.
- The task-commit rule stays in Phase 2 ("commit each task's tested changes
  before returning"). Phase 3's own precondition (clean tree, at least one
  task commit outside `docs/superpowers/`) already covers what Phase 2b's
  opening check guarded.
- A red final `/omc:check` still blocks the handoff under the stage-gate
  rule; only the verify-specific wording goes ("milestone verify run" in the
  drift-restore sentence, "a red gate never reaches `/omc:audit`" becomes a
  statement about a red check).

### 5.4 `skills/audit/SKILL.md`

- Phase 2a: after the disposition pass, always `/omc:check`. `build`, the
  conditional `verify`, the verify announcement and "run verify too" are
  dropped. A red check follows the stage-gate rule and folds into audit's
  batched CRITICAL question as today.
- Phase 3's description of finish's flow lists the new order: check, build,
  verify, review, final verify, describe, push. "Its failing-stage behavior …
  applies unchanged" now means the fix-forward rule.

Audit's post-fix check and the `finish` it then invokes are two conductor
runs, each with its own two-cycle bound.

### 5.5 The verify proxy and its sibling skills

- `skills/verify/SKILL.md`. The "intended semantics" wording, in the body
  and in the frontmatter description, becomes: an agent runs verify only
  within `finish`; the user or the project may run it at any time. The proxy
  takes one optional input, "rerun only: <pieces>". With it, the proxy
  follows the project skill's "Running a subset" section; when the project
  skill has none, it runs the full stage and says so. On a failure the
  `summary` names the failing pieces when the output identifies them: the
  summary is what the layer rule hands the fix subagent, and it is the one
  line of a long verify run that survives a compacted context, so it is
  where the conductor finds what to pass back as "rerun only". The
  `OMC_STAGE` line format is unchanged, and a subset run's verdict describes
  the subset.
- `skills/check/SKILL.md`: "verify (major milestones only)" becomes "verify
  (finish only)". `skills/build/SKILL.md` says only "full E2E belongs to
  `verify`", which stays true; it is not changed.
- `skills/integrate/SKILL.md`, the `.omc/skills/verify` paragraph: "reserved
  for after major milestones" becomes the finish-only wording, and the
  paragraph mentions the optional "Running a subset" section as something
  worth writing when the project's E2E runner can select tests.

### 5.6 This repo's verify stage (`.omc/skills/verify/SKILL.md`)

Gains a "Running a subset" section: `just e2e-tests <pytest node ids or -k
expression>`. `scripts/e2e.sh` already forwards extra arguments to pytest in
both its golden and its parallel pass, and `just codex-gate` forwards them
the same way for a failed Codex-gate piece. Two facts about that forwarding
need the builder's attention before the section is written (section 9).

### 5.7 Unchanged

- `skills/review/SKILL.md`, `skills/create-mr/SKILL.md`,
  `skills/rebase-main/SKILL.md`. `create-mr` amends only the commit message,
  so it never invalidates the final verify.
- `src/omc/*.py`. No stage knowledge enters the CLI.
- The `OMC_STAGE` contract and every other machine contract.

## 6. Tests (red first, per `.omc/config/AGENTS.md`)

Text assertions carry the skill and layer changes (D11); the E2E suites move
their marker expectations.

- **`tests/unit/test_plugin_manifests.py`**
  - finish: order `rebase-main` → check → build → verify → review → final
    verify → `create-mr`; the tree record and the skip sentence; the
    stage-gate citation; "stop at that stage" is gone.
  - implement: no `/omc:verify` and no milestone-gate phase; Phase -1 names
    three phases.
  - audit: Phase 2a is check-only (no `/omc:build`, no `/omc:verify` between
    the disposition heading and the trace heading).
  - layer: the verify clause (targeted rerun, build, full run after; a cycle
    is one fix dispatch), the new stage-order string, the finish-only
    cadence. The needles for sentences that go ("major milestones", "three
    gate runs", the implement/audit red-gate wording) go with them; "end of
    `/omc:implement`" stays a required cadence needle.
  - verify proxy: the "rerun only" input and the "Running a subset" fallback.
  - The old implement and audit verify assertions are removed (around lines
    1298–1341, 1446–1455, 1534–1552, 1785–1789 today).
- **`tests/e2e/lifecycle_helpers.py`**
  - implement's markers are check-only: `_assert_implement_stage_order`
    becomes `_assert_implement_check_only`, which requires a `check` marker
    and no `build` or `verify` marker at all.
  - pre-finish purity: `_assert_check_only_before_finish` requires no
    `verify` before finish's first `build`. Audit's check-only gate is
    pinned at text level (D2, D11), since the cumulative marker file cannot
    tell an audit build from a restarted finish build.
    `_assert_audit_verify_count` and its `drift_repair` parameter go, with
    their two callers (`tests/e2e/golden/test_golden_claude.py`,
    `tests/e2e/variations/test_from_implemented.py`).
  - `_assert_finish_stage_order` requires check → build → verify → review.
    Repair loops of fix → check → [targeted verify] → build → verify are
    allowed before review and after the last review; every repair group ends
    in build → verify (D4, D10). Trailing checks after the last stage are
    tolerated.
  - `tests/unit/test_e2e_lifecycle_stages.py` fixtures follow.
- **`tests/e2e/variations/test_from_recorded.py`**:
  `test_failing_verify_blocks_implementation_handoff` becomes
  `test_implement_never_runs_verify`. It keeps the red-verify sentinel, and
  asserts that implement hands off with task commits, writes no `verify`
  marker and pushes nothing. The judge is dropped: every assertion is now an
  artifact. `tests/unit/test_lifecycle_command.py:64` follows the rename.
- **`tests/e2e/variations/test_from_agreed.py::test_design_auto_advances_to_push`**:
  no `verify` marker before finish's first `build`, and the finish shape
  above (pre-review verify, the final verify, their repair loops, trailing
  checks).
- **Existing red-stage finish E2Es stay.**
  `tests/e2e/test_e2e_finish.py::test_finish_stops_before_push_on_failing_stage`
  and `::test_finish_stops_before_push_on_failing_check_stage` assert one
  artifact: the branch never reaches origin. That holds under D7, because at
  the bound nothing is pushed. What changes is the path: finish now attempts
  two fixes on an unfixable stage before it stops. The builder re-measures
  both against their timeouts and renames them if "stops" no longer reads
  true. `test_e2e_lifecycle_full.py::test_failing_build_blocks_publication`
  already asserts the agent leaves the failing stage's own script alone;
  it is unchanged.
- The golden implemented stage's timing comment
  (`tests/e2e/golden/test_golden_claude.py`) and the red-stage finish E2E
  timings above could not be measured on the build host (no E2E token); the
  comment marks its figures as an upper bound. The next token-bearing verify
  run re-measures them and moves any test over 300 s to `expensive`.

## 7. Docs

- `README.md` is regenerated with `/regenerate-readme`, never hand-edited.
  Its implement paragraph ("the build ends with `/omc:build` then
  `/omc:verify` before handoff"), its finish paragraph (stage order) and its
  project-stages paragraph ("verify is full E2E at major milestones") all
  describe the old placement.
- `.omc/docs/gitnexus/docs/*` is generated and refreshes on the next
  `/omc:document` run in the primary checkout.

## 8. Relation to the 2026-10-06 record

That record stays in the tree as history. Its decisions, against this one:

- old D1 (verify once at the end of implement): superseded by D1.
- old D2 (implement's gate is `check → build → verify`): superseded by D1.
- old D4 (audit runs `check → build`, plus conditional verify): superseded
  by D2.
- old D3 (bounded fix-forward, two cycles, CRITICAL stop, nothing handed off
  on red): retained. Its bound semantics are the base of D6; D4 adds the
  verify clause on top.
- old D5 (per-task commits stay check-gated): retained.

Its non-goal "Changing `finish`" no longer holds.

## 9. Risks and what is left to the builder

**Risks.**

- **Verify failures surface later.** A failure implement could have fixed is
  now first seen in finish, after the squash. This is the trade the user
  chose; D7 is what makes it acceptable, because finish now repairs instead
  of stopping.
- **The rare three-run path.** A red `review` that needs a stage-gate fix
  restarts from build and runs verify again, then the final verify if review
  changes the tree. This costs a fix from the shared budget, so it cannot
  repeat more than twice.
- **Red finish runs get longer.** A stage that cannot be fixed (a broken
  environment) now costs two fix attempts before the CRITICAL question
  instead of an immediate stop. The bound is what keeps that finite.
- **A subset that passes proves little.** That is why D4 always ends in a
  full run. A project without a "Running a subset" section skips the
  targeted rerun and goes straight to the full run, so it loses nothing but
  the shortcut and never runs the full stage twice in one cycle.
- **Layer and skills ship together but install separately.** The layer
  reaches a host via `omc update`. A session with the new finish skill and an
  old layer would cite a rule with no verify clause and fall back to the
  general restart-from-build repair, which is slower and still correct.

**Left to the builder.**

- The exact skill and layer prose that meets the assertions in section 6.
- `scripts/e2e.sh` with a selector. Read from the script: `all` forwards the
  same arguments to the golden pass and the parallel pass, each under its own
  `-m` marker filter and `set -e`. A selector that matches nothing in one
  pass makes pytest exit 5 there (a `-k` expression or a variation node id in
  the golden pass, a golden node id in the parallel pass), and variations
  need the golden stage snapshots to exist. Confirm what a selector-only run
  does and make the smallest change that lets "Running a subset" name one
  command that works for a golden piece and for a variation piece. If no
  small change does, the section documents the two commands
  (`just golden …`, `just e2e-rest …`) instead.
- Whether the regenerate-readme skill's sources need a wording change for the
  regenerated README to state the new placement.

## Implementation review

**2026-11-15 · auditor: claude (opus) · verdict: conforms; 7 Minor, 0 Important**

| # | Severity | Kind | Record section | Location | Disposition |
|---|---|---|---|---|---|
| F1 | Minor | record is stale | §2 Goal | this record, §2 | Amended: loop states the subset condition, build once, full stage, two-fix bound. |
| F2 | Minor | code deviates | §7 / D4 | `README.md:233`, `.claude/skills/regenerate-readme/SKILL.md:246`, `tests/unit/test_cli.py:399` | Fixed: "first reruns only its failed pieces, then build and one full verify, when …"; needle and stale list updated. |
| F3 | Minor | record is stale | §5.2 Tool drift | `skills/finish/SKILL.md:93` | Amended: drift restored after each stage run (implementation ruling accepted). |
| F4 | Minor | code deviates + record is stale | §6 lifecycle helpers | `tests/e2e/lifecycle_helpers.py:617-625`, `tests/unit/test_e2e_lifecycle_stages.py` | Fixed: every repair group must end in `build verify` (`_REPAIRED_VERIFY`); targeted-rerun-without-build cases now rejected. §6 amended to describe the repair-loop shape. |
| F5 | Minor | code deviates (test strength) | §6 pre-finish audit markers | `tests/e2e/lifecycle_helpers.py:645` | Amended §6: pre-finish purity is "no verify before finish's first build"; audit's check-only gate is pinned at text level (D2, D11). |
| F6 | Minor | code deviates (environment) | §6 timing bullets | `tests/e2e/golden/test_golden_claude.py:205`, `tests/e2e/test_e2e_finish.py:158,213` | Amended §6: not measurable on this host (no token); next token-bearing verify re-measures. |
| F7 | Minor | record is stale | §6 layer bullet | `tests/unit/test_plugin_manifests.py:1909` | Amended: "end of `/omc:implement`" is a required cadence needle, not a removed one. |

Post-fix gate: check baseline-identical (host env), build green, verify token-free subset 6 passed / 2 token-only failures (token-gated suite not runnable on this host).

## Deliberate complexity

- `grug:factor-late` @ §4 / §5.1 (`src/omc/distribution/AGENTS.md`): the
  verify clause lives in the shared stage-gate rule although finish is its
  only user. Waived per D7 and D6: every red stage in finish follows the
  layer's rule and shares its one two-cycle counter, so moving the loop into
  finish would split one behaviour from its bound across two files.
- `grug:evidence-perf` @ `.omc/skills/verify/SKILL.md:35` (+ `scripts/e2e.sh:150-178`):
  this repo's "Running a subset" section is unmeasured, and with the golden
  path always rerun in full a green repair cycle costs one extra golden run.
  Waived per D4/D5 and the user's verbatim request ("retry the pieces that
  failed and once they pass run the whole thing again"): while the failure is
  still red the subset skips the whole parallel variation pass; the next
  token-bearing verify run measures it.
