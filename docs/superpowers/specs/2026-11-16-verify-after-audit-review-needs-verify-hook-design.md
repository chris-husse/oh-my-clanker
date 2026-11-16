# Verify once after the audit review, then an MR gate; project hooks (needs_review, needs_verify, post_design, post_rebase, pre_push)

Date: 2026-11-16 · Branch: `feature/verify-after-audit-review-needs-verify-hook` · Slug: `verify-after-audit-review-needs-verify-hook`

Partially supersedes `2026-11-15-run-verify-only-before-push-design.md`
(section 8 names the superseded decisions).

## 1. Problem

As of 0.1.50 the lifecycle validates in this order.

- `/omc:audit`: design-record gate → conformance review (a Review worker
  walks the record against the diff, before any rebase) → disposition →
  post-fix `/omc:check` → trace and commit → `finish`.
- `/omc:finish`: `rebase-main --squash` → `check` → `build` → pre-review
  `verify` → `review` → final `verify` (skipped when `HEAD^{tree}` equals the
  tree the task list recorded at the last green full verify) → `create-mr` →
  ticket-sync → follow-ups.
- `create-mr` is ungated: it describes, amends and pushes. finish's Step 5
  "address review comments" loop re-runs it and starts no stage, so a re-push
  publishes a tree no verify saw.

Four things are wrong with that.

- **Verify runs before the review.** The pre-review verify is a full E2E run
  on a tree the review is about to change. The 2026-11-15 record put it there
  so that verify fixes get reviewed; the user now calls that "exactly wrong":
  verify runs after the review, on an organized codebase, and its findings are
  not review material.
- **The review is split in two and the first half runs on the wrong tree.**
  Conformance runs in audit before the squash and rebase; the project review,
  grug and human-review run later in finish on the rebased tree. Conformance
  fixes change the code the lenses then judge, and conformance never sees the
  rebased result.
- **The second verify is decided by omc alone.** "Any tree change reruns
  verify" is exact but blunt: a waiver line in a design record costs a full
  Docker E2E run. Whether a change is substantial depends on the project, and
  omc cannot decide that reliably. This repo's verify skill already carries a
  prose escape ("Changes limited to documentation do not require repeating
  successful model calls"), which is a judgment call made inside a stage that
  is supposed to report facts.
- **The state lives in the task list.** The verified tree is a line in one
  session's task list. A new session (a re-push the next day, a re-audit)
  starts with nothing and cannot tell a verified tree from an unverified one.

The only project hook today is `.omc/hooks/post-watch.sh`, a CLI-side script
`omc watch` runs through `ToolContext` (`_post_watch_hook` in
`src/omc/watch.py`). A project has no way to tell a session "this change
needs no verify", "review this again", or "this must hold before a push".

The user's request, verbatim: "Make sure we run /verify only once AFTER the
grug/human review step in /audit. and then only once more if there were tree
changes, before the MR assembly. This should be part of the MR gate now. I.e.
/verify ONCE after FIRST /audit review (successive reviews or rebases don't
trigger /verify yet). We settle that changes first. Then MR construction
triggers /verify ONLY if there were tree changes AND the `needs_verify` hook
reported `yes`. Don't run the hook if there were no tree changes since the
last verify. There should be a hook added `needs_verify` that will let each
project decide that more granulary. Then we need a green /verify before the
MR can go through."

The seed, verbatim: "build, check. Not the other way around. … verify does
NOT trigger a review. It is run AFTER the review on an organized codebase.
Whatever is found in /verify should never be cause for another review cycle
unless they caused design changes. There should likely now also be a
`needs_verify` hook plus the `needs_review` hook. So each project can define
with a simple yes/no answer. It depends a lot on the project, we can't
reliably make this decision and our flow is optimized for simplicity. It goes
about like this: implement task -> check -> implement task -> check -> rebase
-> review -> verify -> make MR -> push.... We do this sequence ONCE. The only
extra step we have is in `make MR` which always requires an unmodified tree
over the last successful /verify before the MR can be filed. A project can
customize this via the `needs_verify` hook to suppress verify if nothing
substantial has been changed but it can never request a verify if the tree is
unmodified. For the review, we do that ONCE after the initial rebase. Any
additional reviews can be triggered after rebase or before MR through the
`needs_review` hook, which would then also trigger the verify gate (tree
change check and needs_verify)."

Later: "The hooks always decide aside from the fixed review step after rebase
in /audit and the fixed /verify step after review in /audit. Everything else
is opt in by each repo."

On red hooks: "you don't stop unless you don't know how to fix it. This is a
general rule for everything because there is literally nothing more annoying
than an agent stopping to wait for the human to say 'continue'. You stop when
you can't make a high confidence decision on your own."

A second branch, from another omc-managed repository, asked for three more
hooks, folded in here: `post_design`, `post_rebase` and `pre_push`. It also
asked that `rebase-main`'s post-conflict `/omc:check` be skipped before an
implementation exists (that repository's rule "B16: no check or verify before
implementation"), decided by the hook's answer and not by omc's own "nothing
to audit" test, because that project's own test also excludes
`docs/reviews/`.

**Owner rulings from the apuntalo memory review, 2026-11-16.** Two more
changes to omc come from the owner's rulings recorded in that repository and
are folded in here.

- **Fix-cycle bound: ten for check, build and verify.** Today the layer's
  stage-gate rule allows "at most two fix-and-rerun cycles … per conductor
  run, shared by every stage in it", then a CRITICAL stop. Wanted: a red
  check, build or verify keeps getting fix cycles as long as each one makes
  progress or brings a new idea, up to ten per run, shared by those stages;
  the run stops earlier, with the CRITICAL question, when a cycle brings
  neither. The owner, 2026-11-10, with a verify red and two cycles spent:
  "dude expand the fix cycle". Two is too tight for real gate failures.
- **Plans: about five to seven coherent tasks.** Today
  `skills/write-plan/SKILL.md` passes `superpowers:writing-plans` only the
  Complexity directive and no limit on the number of tasks, and
  `/omc:implement` runs one subagent per task with its reviews and a
  `/omc:check` after each, so every task carries a fixed cost. Wanted: about
  five to seven tasks, each a coherent chunk of the work; inside a task,
  independent edits fan out to parallel edit-only agents (no build, no
  test), followed by one build-and-fix pass; never one task per file or per
  page. The owner, 2026-11-10, stopped a 29-task sequential plan for that
  reason: each task costs a fresh worker, a review and a check run, and the
  run would have taken all day.

## 2. Goal / non-goals / anti-goals

**Goal.** One sequence, run once: implement task → check → … → rebase →
review → verify → make MR → push.

- `/omc:audit` holds the two **fixed** steps, after its own squash and
  rebase: `build → check → review → verify`. They are the only review and the
  only verify omc starts unconditionally.
- Every push goes through an **MR gate** in `create-mr`. The gate asks two
  questions, each answered from recorded state and a project hook: does this
  tree need a review (`needs_review`), does it need a verify
  (`needs_verify`)? A tree that is unchanged since the last green verify is
  never verified again and no hook is asked about it.
- A project gets five hook slots, all the same kind of thing (a project
  skill with a one-line verdict): `needs_review`, `needs_verify`,
  `post_design`, `post_rebase`, `pre_push`. Every one is opt-in; an absent
  hook leaves omc's default.
- A red verdict, from a stage or a hook, is repaired forward. The agent
  stops for the human only when it cannot make a high-confidence decision on
  its own.
- A red gate gets up to ten fix cycles while each one makes progress or
  brings a new idea, not two.
- A plan has about five to seven coherent tasks, and the parallelism lives
  inside a task.

**Non-goals.**

- Forbidding the user or a project from running `/omc:verify` or
  `/omc:review` by hand. Only runs the agent starts are placed.
- Changing `.omc/hooks/post-watch.sh`. It stays a CLI-side script.
- Installing or replacing a project's git hooks. A project's own versioned
  git `pre-push` hook keeps guarding manual pushes beside omc's `pre_push`.
- Any change to the `OMC_STAGE` or `OMC_REBASE_MAIN` line formats.
- A review on a direct `/omc:finish` of an unaudited branch. Review outside
  audit is opt-in through `needs_review`.

**Anti-goals.**

- A verify that triggers a review, or a review → verify → review loop.
- A verify on a tree a green verify already covers.
- A hook that can demand a verify on an unchanged tree, or suppress the only
  verify a branch ever gets.
- An agent that stops on a red verdict it could have repaired.

## 3. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | The two fixed steps live in `/omc:audit`, after its squash and rebase: `build → check → review → verify`. They are the only unconditional review and verify. | Keep them in finish (a direct finish and every re-push would repeat them); keep a pre-review verify (verify on a tree review still changes). |
| D2 | `build` runs before `check` wherever both run: in the stage sequence and in fix loops (fix → build → check → rerun). | `check → build`, today's order (user: "build, check. Not the other way around."). |
| D3 | Verify findings and verify fixes never trigger a review. | Re-review after verify fixes, the 2026-11-15 position (user: "exactly wrong"; verify runs after the review on an organized codebase). |
| D4 | The conformance review folds into the review stage as its first pass, sequentially: conformance → its fixes → then project review, grug and human-review in parallel. It runs only when `omc internal design-record` is ok. It keeps its own disposition (no waivers; a record amendment only when it contradicts no Decisions-table row; otherwise a batched CRITICAL question) and writes the dated `## Implementation review` trace before `## Deliberate complexity`. An unresolved Important conformance finding fails the stage. | A separate pre-rebase conformance step (today; it judges a tree the rebase replaces); conformance in parallel with grug (grug would judge code conformance is about to change). |
| D5 | The MR gate lives in `create-mr`, so it guards every push, finish's Step 5 re-push included. One pass: review question → verify question → describe (amend) → `pre_push` → push; a `pre_push` fix that changed the tree resumes at the verify question, never at the review question. The user's seed invariant decides this: make-MR "always requires an unmodified tree over the last successful /verify before the MR can be filed". | A gate only in finish's Step 4 (the re-push path would stay ungated); a strictly linear pass (a `pre_push` fix would be pushed on a tree no verify saw). |
| D6 | `needs_review` is asked only in the MR gate, and only when the tree changed since the last recorded review. With no record, "previous" is the tree of the merge-base with `origin/<base>`. An absent hook means no review. | Also asking after every rebase (the MR gate follows every rebase anyway). |
| D7 | `needs_verify` is asked only when the tree differs from the last recorded green-verify tree. An absent hook means verify runs. With no verified record, verify runs without asking. A hook can never request a verify on an unchanged tree. | A hook that may suppress the only verify; a hook asked on an unchanged tree. |
| D8 | The deterministic half of the gate is a CLI helper. `omc internal gate review\|verify` prints one `OMC_GATE {"gate", "run", "ask", "reason", "tree", "previous", "message"}` line. `omc internal gate record review\|verify` writes the current `HEAD^{tree}` and refuses (exit 2) on a dirty tracked tree. State is two files at `git rev-parse --git-path omc/reviewed-tree` and `omc/verified-tree`: per worktree, never committed, surviving sessions, gone with the worktree. The helper runs no hook: when the tree changed and the hook file exists it answers `"ask": true` and the session runs the hook proxy. | Skill text only (the comparison would live in prose and be covered only by text assertions, against the project's "assert on artifacts"). |
| D9 | All five hooks are project skills at `.omc/hooks/<name>/SKILL.md`, run as a black box by one new internal proxy skill, `skills/hook/SKILL.md`. The proxy reads the file, follows it from the project root and relays its single `OMC_HOOK {…}` line. Absent file → `OMC_HOOK {"hook": "<name>", "configured": false}`. A missing, malformed or wrong-name verdict → `OMC_HOOK {"hook": "<name>", "configured": true, "error": "<why>"}`. A `needs_*` hook is given the previous and the current tree and answers `OMC_HOOK {"hook": "needs_verify", "run": true\|false, "reason": "…"}`; a failed `needs_*` counts as `"run": true`. `.omc/hooks/post-watch.sh` stays a CLI-side script. `OMC_HOOK` and `OMC_GATE` join every machine-contract list. | Shell-script hooks answering yes/no on stdout (two session-side hook mechanisms; a skill hook can wrap a script when a project wants determinism). |
| D10 | finish skips its `build → check` when `HEAD^{tree}` equals the recorded verified tree, and reports the skip. | Always rebuilding a tree that was built, checked and verified as it stands. |
| D11 | This repo ships `.omc/hooks/needs_verify/SKILL.md`: `"run": false` exactly when every path that differs between the two trees is under `docs/` or is `README.md` (`skills/*.md` is product here). It ships no `needs_review` hook. The documentation sentence in `.omc/skills/verify/SKILL.md` goes. | Keeping the judgment as prose inside the verify stage. |
| D12 | `post_design` runs after `/omc:design` commits the record and answers `OMC_HOOK {"hook": "post_design", "go": true\|false, "reason": "…"}`. No-go → harden again, commit, call it again. Unbounded when interactive; a headless run stops after 3 no-gos with a CRITICAL question. Go → continue (implement, or the stated continuations). | A bound in interactive runs too (the user is present and can interrupt). |
| D13 | `post_rebase` runs inside `rebase-main` whenever a rebase actually moved HEAD. Callers: standalone `/omc:rebase-main`, `/omc:start`'s freshness gate, audit's squash, finish's `--squash`. It is given the base branch, the old merge-base, the new base and the squash flag. In squash mode its commits are amended into the squashed commit, so the MR gate later sees the tree change. Verdict: `OMC_HOOK {"hook": "post_rebase", "ok": true\|false, "before_implementation": true\|false, "reason": "…"}`. After a conflict the order is resolve → re-run → `post_rebase` → `/omc:check`, and the check is skipped when `before_implementation` is true: the hook's answer replaces omc's "nothing to audit" test. Absent hook → today's behaviour. | omc deciding "before implementation" itself (the requesting project's own test excludes paths omc does not know). |
| D14 | `pre_push` runs in `create-mr` after the describe amend, so it sees the final commit, and before the push. A project's versioned git `pre-push` hook stays beside it for manual pushes. | Running it before the describe amend (it would judge a `wip:` commit message). |
| D15 | A red `post_rebase` or `pre_push` (`"ok": false`, or a failed or malformed verdict) follows the stage-gate rule like a red stage: a fix subagent briefed with the hook's `reason` → build → check → the hook again → continue. For `pre_push`, "the hook again" is reached through the verify question when the fix changed the tree (D5). A fix repairs the code, never the hook's own file; this extends the stage-definition guard. The agent stops only when no high-confidence fix exists: at once, or under the fix-cycle bound (D17), when a cycle brings neither progress nor a new idea or the tenth is spent. Nothing is pushed while `pre_push` is red. | Stop and report a red hook (user: never stop to wait for "continue"). |
| D16 | The behavior layer states the general rule: every red verdict, stage or hook, is repaired forward; the agent stops for the human only when it cannot make a high-confidence decision on its own. This sharpens "Stop for required answers or genuine blockers". | Leaving the rule implied by the stage-gate rule alone (hooks and future verdicts would each need their own sentence). |
| D17 | Fix-cycle bound: up to ten fix cycles per conductor run, one budget shared by `check`, `build`, `verify` and the gate hooks `post_rebase` and `pre_push` (their repairs are gate repairs too). Each cycle must make progress (fewer or different failing pieces) or bring a new idea, and the conductor states each cycle in one line, e.g. "fix cycle 3/10: progress — 4 → 2 failing pieces". A cycle that brings neither → the CRITICAL question at once, without spending the rest. Review does not draw on this budget. The stage-definition and hook-file guard is unchanged. Supersedes 2026-11-15 D6. | Keep two (owner: "dude expand the fix cycle"); a separate budget per stage (up to fifty blind fixes in one run); unbounded. |
| D18 | Plan shape: `write-plan`'s directive to `writing-plans` also asks for about five to seven tasks, each a coherent chunk of the work; inside a task, independent edits fan out to parallel edit-only agents (no build, no test), followed by one build-and-fix pass; never one task per file or per page. `/omc:implement`'s per-task worker does the fan-out, and the edit-only agents use the task's Complexity tier. The project's testing policy still governs inside a task. The per-task review and the per-task `/omc:check` stay. | No limit (the 29-task plan); a hard cap (the owner said "about"). |

## 4. Approaches considered

- **A (chosen): a CLI helper decides, skills act, hooks are project
  skills.** The tree comparison and the absent-hook defaults are code
  (`omc internal gate`), tested against real git repositories. Running a
  review, a verify or a hook is session work and stays in skills. A hook is a
  project skill behind one proxy, the same shape as the four project stages
  behind their proxies.
- **B: skill text only.** `create-mr` would compare trees and keep the state
  itself, as finish does today in its task list. Rejected (D8): the decision
  that guards every push would be prose, covered only by text assertions, and
  the state would die with the session.
- **C: shell-script hooks.** `.omc/hooks/<name>.sh`, exit code or a yes/no
  line, like `post-watch.sh`. Rejected (D9): a session has no `ToolContext`,
  so the skill would run `bash <path>` itself and parse stdout, a second hook
  mechanism beside the stage-style proxy; and three of the five hooks
  (`post_design`, `post_rebase`, `pre_push`) are judgment a script often
  cannot make. A skill hook wraps a script in two lines when the project
  wants a deterministic answer.
- **D: the gate in finish only.** Rejected (D5): the Step 5 re-push runs
  `create-mr` without finish's stages, which is the hole this record closes.

## 5. Design

The flow after this change, with the owner of each step:

```
/omc:audit    gates → classify dirty files → rebase-main --squash [post_rebase]
              → build → check → review (conformance first) → record review
              → [build → check when the review changed the tree]
              → verify → record verify → finish
/omc:finish   gate → anything to finish → rebase-main --squash [post_rebase]
              → build → check (skipped on a verified tree) → create-mr
              → ticket-sync → follow-ups
create-mr     MR gate: review question [needs_review] → verify question
              [needs_verify] → describe (amend) → [pre_push] → push
```

### 5.1 `omc internal gate` (new `src/omc/gate.py`)

The helper answers one question per call and runs nothing but git. It is
wired into `src/omc/internal.py` (the usage string and the `run_internal`
dispatch) the way `design-record` is, and reaches git only through
`ToolContext`. It resolves the base branch as `rebase-main` does (project
config).

`omc internal gate review` and `omc internal gate verify` print one line and
exit 0:

`OMC_GATE {"gate": "review"|"verify", "run": true|false, "ask": true|false, "reason": "…", "tree": "<sha>", "previous": "<sha>"|null, "message": "…"}`

`tree` is `HEAD^{tree}`. `previous` is the recorded tree for that gate.
`run` is the answer that stands unless a hook overrides it; `ask` says the
session must put the question to the hook, passing `previous` and `tree`.

| Gate | State | `run` | `ask` | `reason` |
|---|---|---|---|---|
| either | `tree` equals `previous` | false | false | `unchanged` |
| review | changed, no `needs_review` hook file | false | false | `no-hook` |
| review | changed, hook file present | true | true | `hook` |
| verify | no record | true | false | `no-record` |
| verify | changed, no `needs_verify` hook file | true | false | `no-hook` |
| verify | changed, hook file present | true | true | `hook` |

For the review gate a branch with no record is compared against the tree of
its merge-base with `origin/<base>` (D6), so `previous` is never null there
and the same three rows apply. `run` is true whenever `ask` is true, so a
hook that fails to answer leaves the stage running (D9). `message` is one sentence a session can relay as it
stands; for a review that does not run on a never-reviewed branch it says
that no review has run and no `needs_review` hook is configured.

`omc internal gate record review|verify` writes `HEAD^{tree}` to the gate's
state file, narrates that on stderr and exits 0. It prints no verdict: the
exit code is the whole answer, so `OMC_GATE` keeps one shape. With
uncommitted changes to tracked files it writes nothing and exits 2: the tree
that was reviewed or verified would not be the tree it records. Untracked
files do not count, as stages leave artifacts behind.

Exit codes follow `.omc/config/AGENTS.md`: 0 ok, 2 for a refusal or a usage
error, 1 for a git failure. A question that yields no `OMC_GATE` line, or a
`record` that does not exit 0, has decided nothing: the session pushes nothing and reports the error, as it
does for `rebase-main`'s rc 1.

The state is keyed by tree, not by commit, so it survives what the lifecycle
does to commits without changing content: a squash, a rebase onto an unmoved
base, the describe amend. A stale file (another branch checked out in the
same worktree) can only make the trees differ, which asks the question again;
it can never report a different tree as verified.

The helper also looks for the hook file, which the proxy (5.7) does again
when asked. That is deliberate (D8): the absent-hook defaults differ per
gate, and deciding them here puts them under the artifact tests of section 6
and not in `create-mr`'s prose.

### 5.2 `skills/audit/SKILL.md`

Audit becomes the conductor of the fixed sequence. Its phases:

1. **Gates and sources**, unchanged: the design-record gate, base resolution,
   the "nothing to audit" refusal, the optional plan.
2. **Dirty-file classification**, moved here from the end of today's Phase
   2a, because the next step's squash runs `git add -A`. Same rule: unrelated
   or undisposed user work is a batched CRITICAL question, never staged,
   stashed or published by default.
3. **Squash and rebase**: the `rebase-main` skill with `--squash`, read by
   the rules finish's Step 2 states for rc 1, 2, 3, the commit count and a
   `note` verdict. Audit cites those rules; it does not restate them.
4. **`build` → `check`** on the squashed, rebased commit.
5. **`review`**, the fixed review, conformance first (5.3). When the stage
   returns, whatever its verdict, its tracked changes (fixes, record
   amendments, the trace, waivers) are amended into the squashed commit. On
   a passing verdict `omc internal gate record review` then runs. When the
   review's amend changed the tree step 4 built and checked, `build` →
   `check` run again on the amended commit before the verify, so the tree a
   verify records was built and checked as it stands; a red one there is
   repaired under the general clause and never returns to review. The rule
   is stated once, in finish's stage-running rules, and cited by audit and
   create-mr.
6. **`verify`**, the fixed verify. The conductor announces it on its own
   line before it starts (a silent minute is a bug). A red verify is repaired
   by the layer's verify clause and never returns to review (D3). On green,
   known tool drift is restored, fixes are amended, and
   `omc internal gate record verify` runs.
7. **`finish`**.

The contents of today's Phase 1 (conformance), 2 (disposition), 2a (fix
worker tiers) and 2b (trace) move to the review stage.

Two things today's phases protect are kept, in a different place.

- Phase 2b commits the trace and the record amendments before publishing,
  because the design-record gate needs a committed, clean record when
  someone invokes `/omc:audit` again after a stop. The amend in step 5 does
  that job: it happens on a failing verdict too, so a run that stops on a
  CRITICAL question leaves the record committed.
- Phase 2a's post-fix `/omc:check` proves conformance fixes broke no unit
  test. That check moves into the review stage with the fixes (5.3).

Conformance ran before the rebase only because audit did not rebase; nothing
depended on judging the unrebased tree.

`finish` repeats the squash and rebase audit just did. That costs one fetch
on an already squashed branch, keeps finish usable on its own, and is where
a base that moved in the meantime is picked up for the MR gate.

The stage-running rules finish's Step 3 states today move with the stages:
restore known tool drift after each stage run, amend stage-made tracked
changes and fixes into the squashed commit before a rerun, leave untracked
artifacts alone.

Steps 4 to 6 are one conductor run and share one fix-cycle budget (D17). The
`finish` audit then invokes is a second conductor run with its own budget,
as today.

A red stage follows the layer's stage-gate rule. Under its general clause a
red `build`, `check` or `review` is fixed and the sequence resumes at
`build`; a red `verify` falls under the verify clause.

### 5.3 `skills/review/SKILL.md`

One step is added before the project stage.

- **Step 2a, conformance.** Run `omc internal design-record`. Not ok →
  report "no design record — conformance skipped" and continue; this is not a
  failure. Ok → dispatch the Review worker that audit's Phase 1 describes
  today (record, plan when present, branch diff against `origin/<base>`;
  finding kinds "code deviates from the record" and "record is stale";
  severities as grug's). The main session then applies audit's disposition
  order to every Important finding: fix the code (conformance only, fix
  workers at the complexity tiers audit names today); amend the record when
  the amendment contradicts no Decisions-table row; otherwise batch a
  CRITICAL question. Conformance findings are never waived. It then writes
  the dated `## Implementation review` entry immediately before
  `## Deliberate complexity`, appending below earlier entries on a re-run.
  When the disposition changed code, `/omc:check` runs once before the
  lenses start (audit's post-fix check today). A red check fails the stage;
  the caller's stage-gate rule repairs it.
- The project stage, the index hygiene paragraph and the two lenses follow,
  unchanged, and run on the code conformance left behind. That is why the
  pass is sequential and first.
- The stage fails while an Important conformance finding has no disposition.
  The `OMC_STAGE` summary names conformance first, including the skipped
  case.

Conformance belongs here because the review stage is now where a branch is
reviewed, once, on the rebased tree; audit keeps only the conducting. A
direct `/omc:review` or a gate-requested review on a branch with a committed
record runs it too, which is the same judgment on the same inputs.

### 5.4 `skills/finish/SKILL.md`

finish loses its stages and keeps publication.

- **Order.** gate → anything to finish → squash and rebase → `build` →
  `check` → `create-mr` → ticket-sync → follow-ups.
- **Removed.** The review stage, both verify runs, the task-list tree record
  and the final-verify skip rule. Step 0's task list and the frontmatter
  description follow.
- **Skip on a verified tree (D10).** After the squash and rebase, finish
  runs `omc internal gate verify`. `"reason": "unchanged"` means this exact
  tree was built, checked and verified: finish skips `build → check` and
  says so. Any other answer runs both. This is the path audit takes when the
  base did not move between its rebase and finish's.
- **Red stages** follow the stage-gate rule as today. finish and the
  `create-mr` it invokes are one conductor run.
- **Step 5.** The report states what the MR gate did: whether a review ran
  and why, whether a verify ran, was suppressed by `needs_verify`, or was
  skipped as unchanged, and the `pre_push` outcome. "Address review comments"
  still amends and re-runs `create-mr`; applying the comments is work, so
  the cadence's `/omc:check` applies before the amend, and the gate inside
  `create-mr` now decides review and verify for the re-push.

### 5.5 `skills/create-mr/SKILL.md`: the MR gate

A new Step 0 runs before the description. The precondition stays: one
squashed commit, clean working tree.

1. **Review question.** `omc internal gate review`. `ask` → the hook proxy
   with `needs_review`, `previous` and `tree`; its `run` replaces the
   gate's, and an `error` verdict leaves `run` true. If `run`: `/omc:review`,
   amend its tracked changes, `omc internal gate record review`. When that
   amend changed the tree, `/omc:build` → `/omc:check` run before the verify
   question (the rule in 5.2); a red one there never returns to the review
   question.
2. **Verify question**, asked after the review so that it sees the review's
   changes. `omc internal gate verify`, the hook proxy with `needs_verify`
   in the same way. If `run`: announce it, `/omc:verify`, restore known tool
   drift, amend, and on green `omc internal gate record verify`.
3. **Describe**: `get-mr-description`, amended into the commit (today's
   Steps 1 and 2). The amend changes the message, not the tree.
4. **`pre_push`**: the hook proxy. Absent → continue.
5. **Push** (today's Step 3), then the report.

The pass runs once, with one way back. After a `pre_push` fix (D15: fix →
build → check, amended), the gate resumes at step 2, the verify question:
`omc internal gate verify`, `needs_verify` when asked, verify when it runs,
then the describe amend and `pre_push` again. No new rule decides whether
the fix changed the tree: the helper answers `unchanged` when it did not,
and the flow goes straight on to `pre_push`. This keeps the user's
invariant that the tree pushed is the tree the last green verify saw,
unless `needs_verify` said the change needs none. Nothing ever sends the
flow back to step 1: a verify the gate ran, a verify fix and a `pre_push`
fix never re-ask the review question (D3, D5). A red `review` or
`verify` follows the stage-gate rule (general clause and verify clause); a
red `pre_push` follows it as D15 says. Nothing is pushed while any of them
is red.

**Bound.** Inside finish the gate shares finish's fix-cycle budget (D17). A
Step 5 re-push is its own conductor run. `pre_push` fixes and the verify
fixes that may follow them draw on that one budget, so the way back is
finite, and it ends sooner when a cycle brings neither progress nor a new
idea.

What the gate does in the common cases:

| Situation | Review | Verify |
|---|---|---|
| finish straight after audit, base unmoved | unchanged, skipped | unchanged, skipped |
| finish after audit, base moved or `post_rebase` changed files | `needs_review` decides; absent → none | `needs_verify` decides; absent → runs |
| direct finish, branch never audited | `needs_review` decides; absent → none, and the gate's message says so | runs (no record) |
| Step 5 re-push after review comments | `needs_review` decides; absent → none | `needs_verify` decides; absent → runs |

### 5.6 Behavior layer (`src/omc/distribution/AGENTS.md`)

- **Lifecycle scope.** `/omc:audit` authorizes the fixed review and verify
  (conformance inside the review), their fixes and record amendments, then
  publication through finish.
- **Worktrees.** `/omc:rebase-main` is audit's and finish's first working
  step.
- **Publish through `/omc:finish`.** Squash, rebase, `/omc:build` →
  `/omc:check`, the MR gate (`needs_review`, `needs_verify`), `pre_push`,
  described push.
- **Validation cadence.** An agent starts `/omc:verify` in two places only:
  audit's fixed verify after its review, and the MR gate when the gate says
  run. The user or the project may run it at any time.
- **Stage-gate rule.** The general clause becomes fix → `/omc:build` →
  `/omc:check` → the remaining stages in order. The verify clause becomes
  fix → build → check → targeted rerun → full verify; without a "Running a
  subset" section, fix → build → check → full verify. "Nothing before verify
  is rerun" stays and now also carries D3. A red hook verdict is a red gate
  under this rule (D15), and the definition guard covers a hook's own file
  (`.omc/hooks/<name>/`) beside `.omc/skills/<stage>/`. "No later phase runs
  on a red gate" reads: implement makes no handoff on a red check, audit
  invokes no `finish`, `create-mr` pushes nothing.
- **The bound (D17)** replaces "at most two fix-and-rerun cycles". Up to
  ten fix cycles per conductor run, one budget for `check`, `build`,
  `verify`, `post_rebase` and `pre_push`. A cycle is still one fix dispatch
  with whatever reruns follow it; reruns are never counted on their own; a
  fix whose rerun passes by flake still spent its cycle. Each cycle must
  make progress (fewer or different failing pieces than before it) or try an
  idea no earlier cycle tried. The conductor says which, in one line per
  cycle: "fix cycle 3/10: progress — 4 → 2 failing pieces". A cycle that
  brings neither is the CRITICAL question at once; so is the tenth cycle
  ending red. The question names the red gate, its failure summary, what
  the fixes tried and the answer needed, as today. A red review's own repair
  is not counted, though a red build or check after it is; the review stage
  already turns what it cannot settle into its batched question.
  `rebase-main`'s check-only citation reads the same with the new number.
  The per-cycle line is also what a compacted context keeps of the count.
- **The general rule (D16)**, one sentence beside "Stop for required answers
  or genuine blockers": every red verdict, stage or hook, is repaired
  forward; stop for the human only when no high-confidence decision is
  possible.
- **Machine contracts.** `OMC_GATE` and `OMC_HOOK` join the list.

Check-only gates stay check-only: implement's per-task check, `rebase-main`'s
post-conflict check and review's per-fix check run no build, so D2 has
nothing to order there.

### 5.7 The hook proxy (`skills/hook/SKILL.md`, new, internal)

Frontmatter description carries "not meant for direct invocation". Input: the
hook name, then the context its caller passes (trees, base, flags). Steps, in
the shape of the stage proxies:

1. Resolve the project root. Look for `.omc/hooks/<name>/SKILL.md`.
2. Missing → `OMC_HOOK {"hook": "<name>", "configured": false}`.
3. Present → read it, follow it from the project root as a black box, and
   relay its single `OMC_HOOK {…}` line unchanged.
4. No line, an unparseable line, a `hook` field that is not `<name>`, or a
   missing answer field for that hook → `OMC_HOOK {"hook": "<name>",
   "configured": true, "error": "<why>"}`.

The answer field per hook, which the proxy lists in one table: `run` for
`needs_review` and `needs_verify`, `go` for `post_design`, `ok` for
`post_rebase` and `pre_push`. `reason` is always present. What a caller does
with an absent or failed hook is the caller's rule (D9, D12, D13, D15); the
proxy only reports.

### 5.8 `skills/rebase-main/SKILL.md`

- `--squash` is audit's and finish's flag.
- **`post_rebase` (D13).** After a successful outcome in which the rebase
  moved HEAD onto a different base commit, the skill invokes the hook proxy
  with `post_rebase`, the base branch, the old merge-base, the new base and
  whether `--squash` was passed. A run that replayed nothing (the branch was
  already on the fetched base; a squash alone rewrites the commit but moves
  nothing) does not call it. In squash mode, commits the hook makes are
  amended into the squashed commit, so the branch stays one commit and the
  MR gate sees the tree change.
- **After a conflict** the order is resolve → re-run → `post_rebase` →
  `/omc:check`. The check is skipped when the hook answered
  `"before_implementation": true`. With no hook the check runs as today.
  Under `--squash` the caller's stages follow, as today.
- A red `post_rebase` follows D15, with the fix confined as the
  post-conflict check's fix is today: to what the rebase broke. When the
  hook answered `"before_implementation": true`, that loop runs no build and
  no check either (fix → the hook again): the answer that skips the
  post-conflict check skips them for the same reason.
- **Callers.** All four are named: standalone `/omc:rebase-main`,
  `/omc:start`'s freshness gate, audit's squash, finish's `--squash`.
  `/omc:start` today runs `git rebase` itself and enters this skill only on
  a conflict; its "not an ancestor" branch now invokes this skill with
  `--base <base>` from the outset, so one path rebases and one place calls
  the hook. A dirty tree still stops start (rc 1).

### 5.9 `skills/design/SKILL.md`

Step 5 gains the `post_design` loop (D12) between the commit and the close.

- After the commit, invoke the hook proxy with `post_design` and the record
  path. Absent → close as today.
- `"go": true` → close as today (continue into `/omc:implement`, or state
  the continuations).
- `"go": false` → the `reason` goes back to the Design worker as a finding;
  it hardens again (Steps 2 to 4), the main session commits, and the hook is
  called again.
- Interactive runs do not bound the loop. A headless run stops after three
  no-gos with one CRITICAL question carrying the three reasons.
- An `error` verdict is not a no-go: nothing in the record repairs a hook
  that does not answer. The proxy is called once more; a second error is the
  CRITICAL question.

### 5.10 Siblings, this repo's hook, docs

- `skills/verify/SKILL.md`, `skills/check/SKILL.md`: the intended-semantics
  wording names the two places an agent starts verify (5.6).
- `skills/implement/SKILL.md`: its sentences about what audit and finish do
  ("`finish` runs `verify` on the squashed commit", "To publish without an
  audit, type `/omc:finish`") describe the new placement; the second says a
  direct finish gets the MR gate and no review unless the project's
  `needs_review` asks for one.
- `skills/start/SKILL.md`: Step 3 routes its rebase through `rebase-main`
  (5.8).
- `skills/integrate/SKILL.md`: the inventory and Phase 2 gain the five hook
  slots, each with its verdict line, what omc does when it is absent, and
  "absence is the correct default". The verify paragraph's "only within
  `/omc:finish`" wording follows 5.6.
- **This repo (D11).** `.omc/hooks/needs_verify/SKILL.md` lists the paths
  that differ between the two trees it is given and answers `"run": false`
  exactly when every one is under `docs/` or is `README.md`. The sentence
  "Changes limited to documentation do not require repeating successful
  model calls; cite the last green run." leaves `.omc/skills/verify/SKILL.md`.
  The contract lists in `.omc/config/AGENTS.md` and
  `.omc/skills/explain-context/SKILL.md`, and the former's "used by
  `/omc:finish`" line, are brought up to date.

### 5.11 Plan shape (`skills/write-plan/SKILL.md`, `skills/implement/SKILL.md`)

Every task in `/omc:implement` costs a fresh worker, its reviews and a
`/omc:check`, whatever its size. The plan's shape therefore decides how long
a build takes, and today nothing shapes it.

- **`write-plan`.** The directive it passes to `superpowers:writing-plans`
  verbatim keeps its Complexity sentences and gains these: the plan has
  about five to seven tasks, each a coherent chunk of the work; inside a
  task, independent edits fan out to parallel edit-only agents that run no
  build and no test, followed by one build-and-fix pass; never one task per
  file or per page. "About" is meant: a small change may need fewer, and a
  plan is not padded to reach five.
- **`implement`.** Phase 2 says what the per-task worker does with such a
  task: dispatch the edit-only agents in parallel at the task's Complexity
  tier, then run one build-and-fix pass itself, then commit. The task's
  reviews and the `/omc:check` after it stay as they are.
- **Testing policy.** The project's policy still governs inside a task. On
  this repo (red → green) that is a test fan-out, a red run, the code
  fan-out, then the build-and-fix pass.

The fan-out is described in the plan directive and not left to the worker,
because the plan is where edits are known to be independent. Independent
means the agents touch different files: they share one worktree, and none
of them builds or tests, so nothing but the files themselves is shared.

## 6. Tests (red first, per `.omc/config/AGENTS.md`)

No skips. The helper is covered by artifact assertions on real repositories;
skill and layer prose by text assertions; the flow by E2E markers and git
state.

- **`tests/unit/test_gate.py` (new)**, real git repositories and worktrees
  under pytest's tmp:
  - unchanged tree → `run` false, `ask` false, for both gates;
  - absent-hook defaults: review does not run, verify runs;
  - hook file present and tree changed → `ask` true;
  - review with no record uses the merge-base tree as `previous`;
  - verify with no record → `run` true, `ask` false;
  - `record` writes the `--git-path` file, and refuses with exit 2 on a
    dirty tracked tree;
  - two worktrees of one repository keep separate state;
  - a question prints exactly one `OMC_GATE` line with the seven fields;
    `record` prints none.
- **`tests/unit/test_plugin_manifests.py`**, text assertions:
  - audit: the new order, and no conformance phase before the squash;
  - review: conformance first, the skipped-case sentence;
  - finish: no review or verify stage, the D10 skip;
  - create-mr: the gate order, `pre_push` after the describe amend, and a
    `pre_push` fix resuming at the verify question, never the review
    question;
  - hook proxy: the absent and error shapes, the internal marker;
  - rebase-main: the moved-HEAD condition, the check skipped on
    `before_implementation`, the four callers;
  - design: the `post_design` loop and the headless bound;
  - D15: fix-forward for red hooks, the hook-file guard;
  - D17: "ten", progress or a new idea, the per-cycle line, review not
    counted, the two hooks included; needles pinning "two cycles" are
    removed;
  - D18: write-plan's directive carries the five-to-seven, fan-out and
    never-one-task-per-file rules; implement names the fan-out and the
    build-and-fix pass;
  - layer: the stage arrows, both clauses, the D16 sentence, the contract
    lists;
  - integrate: the five hook slots.
  - Needles for the old order are removed with the sentences they pinned.
- **`tests/e2e/lifecycle_helpers.py` and
  `tests/unit/test_e2e_lifecycle_stages.py`**: the stage order becomes
  build → check → review → verify inside audit; after audit's verify no
  review or verify marker appears unless a gate said so (fix loops allowed);
  the golden path's finish gate passes as "unchanged".
  `tests/e2e/variations/test_from_agreed.py` follows.
- **E2E variations driving real hook skills** (the builder may fold them
  into existing scenarios):
  - a finish re-run after a documentation-only change on an audited branch,
    with this repo's `needs_verify`: pushed, no new verify marker; and its
    product-change counterpart: a verify marker;
  - `post_rebase` answering `"before_implementation": true` on a conflicted
    start rebase: no check marker;
  - a red `pre_push` that a fix turns green: the push happened;
  - `post_design` no-go, then go: two record commits. It runs in the
    `expensive` tier (`just lifecycle-full`), not the verify gate: two
    hardening passes cannot fit the 300 s ceiling.
- **Existing red-stage E2Es stay.**
  `tests/e2e/test_e2e_finish.py::test_finish_stops_before_push_on_failing_stage`
  and `::test_finish_stops_before_push_on_failing_check_stage` assert that
  the branch never reaches origin. That holds: a direct finish still runs
  build and check, and its gate runs verify because no verified tree is
  recorded. Their path changes with D17: on a stage that cannot be fixed
  the run should now stop after the first cycle that brings no progress; the
  builder re-measures both against their timeouts when a token is available.
  `tests/unit/test_cli.py`'s README needles follow the regenerated README.
- E2E timings could not be measured on the design host (no token). The next
  token-bearing verify run measures the new and changed scenarios against
  the 300 s ceiling.

## 7. Docs

- `README.md` is regenerated with `/regenerate-readme`, never hand-edited.
  Its lifecycle, finish, audit, project-integration and contract-name
  passages describe the old placement; the regenerate skill's sources change
  where the regenerated text would not otherwise state the new one.

## 8. Relation to the 2026-11-15 record

That record stays in the tree as history. Its decisions, against this one:

- old D1 (implement is check-only): retained.
- old D2 (audit's post-fix gate is check only): superseded. Audit now runs
  `build → check → review → verify` (D1).
- old D3 (finish's stage order): superseded by D1 and D5.
- old D4 (the verify repair loop): retained, build first (D2).
- old D9 (the final-verify skip on an unchanged tree): superseded by the MR
  gate (D7, D8), which keeps the comparison and moves its state out of the
  task list.
- old D10 (build once after a verify fix): folded into D2.
- old D12 (final-verify fixes go unreviewed): retained and generalized by
  D3 to every verify.
- old D6 (two fix cycles, one counter): superseded by D17. One counter per
  conductor run stays; the number becomes ten, gated by progress or a new
  idea, with review outside it.
- old D7, D8, D13: retained. D13's guard is extended to hook files (D15).
- old D5 and D11 are untouched by this record.

## 9. Risks and what is left to the builder

**Risks.**

- **A direct `/omc:finish` on an unaudited branch gets no review** unless
  the project's `needs_review` asks for one. Intended (review outside audit
  is opt-in); the gate's `message` says so in the run.
- **Audit now squashes.** A failed audit leaves the branch squashed.
  finish's rebase is idempotent on a squashed branch, so a re-audit or a
  later finish is safe; the pre-squash tip is in the stderr undo line.
- **`needs_review` sees verify fixes as a tree change.** omc never starts a
  review because of a verify (D3), but a project that ships `needs_review`
  is asked at the next gate about any change since the last review, verify
  fixes included. That is the project's opt-in deciding (D6).
- **A `pre_push` fix can cost a second verify in one gate pass.** The fix
  changes the tree after the gate's verify, so the verify question is asked
  again (D5). A project keeps that cheap with `needs_verify`; the shared
  fix-cycle budget (D17) keeps it finite. A `pre_push` fix is not reviewed, like a
  verify fix (D3).
- **Layer and skills install separately.** A session with the new skills and
  an old layer falls back to the general fix loop in the old order: slower,
  still correct.
- **Skill hooks cost an LLM turn and can be nondeterministic.** A project
  that needs a deterministic answer wraps a script in its hook skill, as
  this repo's `needs_verify` does with a `git diff --name-only`.
- **`post_design` is unbounded in interactive runs.** A hook that never says
  go loops until the user interrupts.
- **Red runs can get much longer.** Ten cycles of a multi-minute verify is
  hours. The progress-or-new-idea rule is the brake: a stage that cannot be
  fixed (a broken environment, a missing token) shows no progress on its
  first cycle and stops there, earlier than today's two. "Progress" and "a
  new idea" are the conductor's judgment; the one-line statement per cycle
  makes that judgment visible to the user.
- **Red-stage E2Es against the 300 s ceiling.** The two red-stage tests in
  `tests/e2e/test_e2e_finish.py` use a stage that cannot be fixed, so they
  should stop after one cycle; a model that keeps finding "new ideas" would
  run them into the ceiling. Not measurable on the design host.
- **Bigger tasks, later feedback.** With five to seven tasks a task's review
  and check cover more code at once than today. The build-and-fix pass
  inside the task is what catches what the edit-only agents could not see.

**Follow-up outside this branch.** The apuntalo repository's verify skill
overrides the bound ("up to ten cycles while each one makes progress, not
omc's two"). Once this ships, that override is removed there.

**Left to the builder.**

- The exact skill and layer prose that meets the assertions in section 6.
- Where `gate` goes in `internal.py`'s usage string.
- How `rebase-main` detects that the rebase moved the branch onto a
  different base (the `rebased` field alone cannot tell: a squash changes
  the commit without a rebase).
- The exact E2E fixtures, and whether the new variations fold into existing
  scenarios.
- Who fans out inside a task where a harness's workers cannot dispatch
  subagents: the main session does it there, and the builder finds out
  which harnesses that is.
- Whether standalone `/omc:rebase-main` needs a note that it starts no
  review or verify; the gate catches up at the MR.

## Implementation review

### 2026-11-16 — Claude (opus), `/omc:audit` on base 7f5d9cd

- Minor, record is stale, §5 / §5.2 / §5.5, `skills/audit/SKILL.md` Phase 5,
  `skills/create-mr/SKILL.md` Step 0: the build → check after a review that
  changed the tree was undescribed. Disposition: record amended.
- Minor, record is stale, §7: the `.omc/docs` / `/omc:document` bullet
  described a feature the base removed. Disposition: bullet removed.
- Minor, record is stale, §6, `tests/e2e/variations/test_from_agreed.py`:
  the `post_design` variation runs in the `expensive` tier. Disposition:
  record amended.

## Deliberate complexity

- `grug:say-no` @ §2 / §5.7 / §5.10: five hook slots ship although this repo
  uses one (`needs_verify`); `needs_review` has no user here, and
  `post_design`, `post_rebase` and `pre_push` have one requesting repository.
  Waived per D6, D9, D12, D13 and D14: the user decided that the hooks decide
  everything outside audit's two fixed steps and that each repo opts in.
- `grug:locality` @ §5.1 / §5.7: both the gate helper and the hook proxy look
  for `.omc/hooks/<name>/SKILL.md`. Waived per D8: the helper answers
  `"ask": true` only when the hook file exists, which keeps the absent-hook
  defaults in tested code.
- `grug:evidence-perf` @ §5.4: finish's skip of `build → check` on a verified
  tree is an optimisation with no measurement behind it. Waived per D10.
- `grug:cut-point` @ §5.5: `create-mr`, a describe-and-push skill, now also
  conducts a review, a verify and a hook. Waived per D5: the gate must guard
  every push, and `create-mr` is the one skill every push passes through.
- `grug:small-refactor` @ §5.2 / §5.3: moving conformance from audit into the
  review stage is a refactor bundled with the verify change. Waived per D4.
- `grug:small-refactor` @ §5.8: `/omc:start`'s own `git rebase` is rerouted
  through `rebase-main`. Waived per D13: start's freshness gate is a named
  `post_rebase` caller, and its clean rebase never reaches the skill today.
- `grug:simple-concurrency` @ §5.11: parallel edit-only agents write in one
  worktree where a sequential worker would do. Waived per D18: the owner
  asked for the fan-out inside a task; the agents touch different files and
  run no build or test.
- `grug:small-refactor` @ §5.11: the plan-shape change is unrelated to the
  verify gate and is bundled into this record. Waived per D18: the owner
  ruled it be folded in here.
