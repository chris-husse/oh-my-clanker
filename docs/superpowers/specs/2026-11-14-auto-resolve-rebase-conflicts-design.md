# Rebase conflicts are resolved by the agent, never handed back

Date: 2026-11-14 · Branch: `feature/auto-resolve-rebase-conflicts` · Slug: `auto-resolve-rebase-conflicts`

## 1. Problem

When an agent-run rebase stops on a conflict, the agent asks the user whether
it should keep resolving, whether the user wants to do it, or whether to give
up. User: "There is only one possible answer here: Go fix the conflicts and
do it right."

The agent asks because the skills tell it to:

- `skills/rebase-main/SKILL.md`, rc 3 bullet: "Show the conflicted files and
  help the user resolve them (… or `git rebase --abort` if they choose to
  back out …). Never resolve conflicts silently, never abort on their
  behalf." Its rc 2 bullet: "Relay the stderr line and stop: the user
  finishes it … or backs out".
- `skills/finish/SKILL.md` Step 2: "rc 3 bail (conflicts, rebase left
  paused): hand control to the user per that skill — never resolve conflicts
  silently, never `--abort` on their behalf."
- `skills/audit/SKILL.md` Phase 3: "Its failing-stage behavior and conflict
  handoff apply unchanged."
- `skills/start/SKILL.md` Step 3: "Dirty tree or conflicts → STOP and
  surface; never force it."

The handoff was written when a finish replayed N commits and could stop N
times. Since the 2026-11-12 squash-before-rebase record the lifecycle
rebases only before work starts (`/omc:start` Step 3) and after the work is
squashed (`/omc:finish`, `omc internal rebase-main --squash`), so a run meets
at most one conflict stop — and the session that meets it wrote the branch
and has the design record, which makes it the best-placed party to merge.

The CLI already does its half. `src/omc/internal.py:_rebase_main` leaves the
rebase paused on a conflict, never aborts, and reports the files in the rc 3
verdict `OMC_REBASE_MAIN {"ok": false, "conflicts": [...], "knowledge": …}`.
Exit code 3 is the project's "bail" code — "inconclusive, caller falls back
to its own judgment" (`.omc/config/AGENTS.md`). The caller is the agent; only
the skill prose turns its judgment into a question for the user.

## 2. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | Skill text only. No change to `internal.py`, the rc codes, or `OMC_REBASE_MAIN`. | The CLI resolving with git strategies (`-X ours` / `-X theirs`): blind, cannot know intent, and is the anti-goal. The CLI embedding a resolution recipe in the verdict: a second source of truth for prose the skill already owns. |
| D2 | All three agent-run rebase sites change: `/omc:finish` (and `/omc:audit` through it), standalone `/omc:rebase-main`, `/omc:start` Step 3. `omc watch --rebase` keeps aborting. | Finish only (the other two sites would keep asking the same question). Changing watch (no agent is behind it to merge anything). |
| D3 | One rule, owned by `skills/rebase-main/SKILL.md`. Finish already invokes that skill; audit goes through finish; start invokes it when its own rebase pauses. No skill carries a copy. | Repeating the procedure in each skill (four copies to keep in step). A pointer from start to text it never loaded (start runs an inline `git rebase`; the rule would not be in its context). |
| D4 | Ambiguity is never a question: write the merge that keeps both intents, flag it in the report, let the gates catch a mistake. | Asking when the merge is unclear (reintroduces the prompt this record removes; "unclear" is the agent's own judgment, so it would ask often). |
| D5 | rc 2 — an operation already in progress before the command ran — is finished by the agent the same way, then the command is re-run. | Relay and stop (the commonest cause is this lifecycle's own rebase left paused by an interrupted session; stopping there is the same question again — and it is the path start's paused rebase arrives on, D3). |
| D6 | A dirty tree at `/omc:start` Step 3 still stops. | Auto-commit or stash (uncommitted work is the user's and no conflict; folding it is finish's job, with notice). |
| D7 | After resolving, validate: `/omc:check` whenever the `rebase-main` skill resolved something outside finish (standalone, and start through it); finish's own check → build → verify → review stages at finish. | No validation (a wrong merge would surface only at publication). Full `/omc:verify` after every resolution (verify is milestone-only by the validation cadence). |
| D8 | No new rebase at `/omc:implement` start. | Rebasing there too (a third rebase per run, and a second possible conflict stop mid-lifecycle). |
| D9 | A unit test pins the wording: the handoff phrasing must be gone from the lifecycle skills and rebase-main must carry the rule. | No test (nothing would stop a later skill edit from putting the question back). |
| D10 | The squash-before-rebase record's "automatic conflict resolution" non-goal (its D4, §3, §7) is superseded by a dated note in that record; the rest of it stays as history — including its non-goal that start's freshness rebase stays an inline `git rebase`. | Rewriting the old record's sections (it documents what was decided then). Replacing start's inline rebase with the `rebase-main` skill on every stale base (one route instead of two, but it changes start's clean path — a docs mirror and knowledge relay on every stale start — which nobody asked for). |

All ten are the brainstorm's outcome; the user accepted the recommended
default on the four open questions (D4, D5, D6, D9) by invoking
`/omc:design`.

## 3. Goals and non-goals

**Goal.** Every agent-run rebase that stops on a conflict is resolved,
continued and validated by the agent, with no prompt about the conflict. The
stops that remain are the ones that exist today and are not about the
conflict: the stage-gate rule's CRITICAL question when the gates stay red
after two fix cycles, and finish's stop when nothing is left over the base.

**Non-goals.**

- `omc watch --rebase` (`src/omc/watch.py`): keeps abort-on-conflict.
- A rebase at `/omc:implement` start (D8).
- Any change to the CLI, the rc 1/2/3 codes or the `OMC_REBASE_MAIN` verdict
  (D1).

**Anti-goals** — failure even when the rebase "completes":

- A blind `-X ours` / `-X theirs`, `git checkout --ours/--theirs` on a whole
  file as a shortcut, or `git rebase --skip` to get past the stop.
- Conflict markers left in a resolved file.
- `git rebase --abort` on the agent's own initiative.
- Any "continue / you do it / give up?" prompt.

## 4. Design

### 4.1 The rule — `skills/rebase-main/SKILL.md`

The rc 3 bullet becomes "you resolve it — this is not a question", followed
by one block. Finish follows it through the skill it already invokes;
start reaches it by invoking the skill (§4.4):

1. For each conflicted path (the verdict's `conflicts` on the first stop;
   `git status` after a later stop or when there is no verdict), read both
   sides before editing: what the base did
   to the file since the merge-base
   (`git log -p <merge-base>..origin/<base> -- <file>`) and what the branch
   did (the commit being replayed, `git show REBASE_HEAD -- <file>`).
2. Write the merge that keeps both intents. Where the base renamed, moved or
   reshaped code, carry the branch's change onto the new shape. Never drop
   either side's intent.
3. `git add` the resolved paths and confirm mechanically that they carry no
   conflict markers — `git diff --cached --check -- <resolved paths>`, where
   only its `leftover conflict marker` lines count (not its exit code, not
   its whitespace reports), and a flagged line already present in `HEAD:`'s
   or `REBASE_HEAD:`'s version of the file is content (e.g. a Markdown
   `=======` underline) — and that git lists no unmerged path; then
   `GIT_EDITOR=true git rebase --continue`.
4. Stopped again (a standalone, non-squash rebase replays several commits) →
   repeat from 1. When the rebase completes, re-run the command that stopped
   with the same arguments, so the `.omc/docs` mirror still happens (with
   `--squash` the re-run is idempotent, as today).
5. Report in a few lines what conflicted and how each path was resolved, and
   flag any merge where the two intents were hard to reconcile (D4). In
   squash mode the report carries the stderr `pre-squash tip <old> (undo:
   git reset --hard <old>)` line of the run that hit the conflict — and its
   `folded uncommitted changes stay in <wip>` clause when present — as the
   user's way back to the branch as it was. (The re-run prints the line
   again with the already-rebased tip; that one is not the way back.) This
   is a report, not a question; the flow continues.
6. Never `git rebase --abort`, never `--skip`, never ask whether to
   continue, hand over or give up. Where no textual merge exists (a binary)
   or the file is generated (a lockfile), keeping one side whole or
   regenerating it is the reasoned merge; it is flagged in the report (D4).

The only stop after a conflict is one that already exists: the stage-gate
rule's bounded fix-forward (two fix-and-rerun cycles, then one CRITICAL
question) when the validating stages stay red (§4.5).

**rc 2** (D5). Something is already in progress. The agent finishes
whatever `git status` shows by the same rule — both intents, no markers,
then that operation's own `--continue` — and re-runs the command. It is
reported like any other resolution. The skill does not spell out a
procedure per operation kind; the rule and `git status` are enough.
Unmerged paths with no operation in progress (a conflicted `git stash pop`)
are resolved and staged; outside `--squash` the re-run then ends at rc 1
with the changes staged, which is reported; the stash entry is kept and
named, never dropped or popped.

**After a resolution outside finish** (D7), the skill runs `/omc:check`
before it returns. When `/omc:finish` invoked it (`--squash`), it does not:
finish's stages follow.

**rc 1** is unchanged: the fetch failed, git refused before starting, or the
squash commit failed. Nothing is paused, so there is nothing to resolve;
relay git's message.

The existing text about `git rebase --abort` as a user option goes away with
the handoff. The pre-squash-tip sentence survives, moved into step 5.

### 4.2 `skills/finish/SKILL.md` Step 2

- rc 3: "hand control to the user … never resolve conflicts silently" becomes
  "resolve per the `rebase-main` skill's rule, then re-run it with
  `--squash`". The idempotent re-run sentence stays.
- The rc 1 / rc 2 bullet splits: rc 1 still surfaces stderr and stops; rc 2
  follows rebase-main's rc 2 (finish it, re-run).
- The `note` bullet is unchanged, its "stop and report it" included (§6).
- The count gate (`origin/<base>..HEAD` must be 1) is unchanged and still
  stops on 0. It is the backstop for a resolution that left nothing over
  the base.

Step 3's check → build → verify → review then run on the resolved tree, as
they do after any finish rebase. No new validation step.

### 4.3 `skills/audit/SKILL.md` Phase 3

"Its failing-stage behavior and conflict handoff apply unchanged" becomes
"its failing-stage behavior and conflict resolution apply unchanged". Audit
adds no behavior of its own.

### 4.4 `skills/start/SKILL.md` Step 3

"Dirty tree or conflicts → STOP and surface; never force it" splits:

- **Dirty tree** → stop and surface, as today (D6). Git refuses before
  starting, nothing is paused.
- **Conflicts** (the inline `git rebase origin/<base>` paused) → invoke the
  `rebase-main` skill. Start always runs in a worktree the CLI created, so
  `omc internal rebase-main` finds the paused rebase and exits rc 2; the
  skill's rc 2 path finishes it by the rule, re-runs (a no-op rebase plus
  the `.omc/docs` mirror), runs `/omc:check` and reports. Start then
  continues to Step 4. Start carries one sentence, no copy of the rule and
  no check clause of its own.

Start composes other skills this way already (`ticket-sync`, `omc:plan`).
Its opening authority statement ("does not authorize product edits …") and
its closing line "it never designs or writes code itself" both gain the
exception: merging the branch's existing commits onto the base, and
committing a repair of that merge when `/omc:check` is red, are part of the
freshness gate. "Never brainstorm on a stale base" stays.

A conflict here is rare: a freshly cut branch has no commits to replay; it
takes a resumed branch with commits and a moved base.

### 4.5 Validation after a resolution (D7)

| Site | Validation | Why this much |
|---|---|---|
| `/omc:finish` (and audit) | Step 3's four stages, already there | Publication gate; nothing to add. |
| `rebase-main` skill outside finish (standalone, or invoked by start) | `/omc:check`, only when something was resolved | Quick "is the merge sane" gate; a clean rebase adds no check. |

A red check after a resolution falls under the existing stage-gate rule, and
its fix-forward is confined to repairing the merge — the conflicted paths
and what the merge broke — never new feature work (at start, nothing else is
authorized yet). The gate is check-only: each fix-and-rerun cycle re-runs
`/omc:check` alone, never `/omc:build` or `/omc:verify`; the cycle bound and
the CRITICAL stop are the stage-gate rule's. Merge-repair edits are
committed on the branch (only the repair's own paths) so the skill returns
a clean tree.

### 4.6 Prose and generated files

- `docs/superpowers/specs/2026-11-12-squash-before-rebase-design.md` gains
  one dated note (D10): its "automatic conflict resolution" / "resolving
  conflicts on the user's behalf" non-goals are superseded by this record.
  The implementer adds it; nothing else in that record changes.
- `README.md` is generated. After the skill edits, `/regenerate-readme`
  runs; the expected diff is the start sentence ("rebases or stops on
  conflicts", line 181) and the finish paragraph (line 233, "stops the
  rebase at most once"). If the recipe does not pick the change up from the
  skill sources, the recipe (`.claude/skills/regenerate-readme/SKILL.md`)
  gets the clauses (in its start and finish paragraphs) that make the fact
  durable. The `omc watch` paragraph (line 141,
  abort on conflict) must come out unchanged.

### 4.7 Unchanged

- `src/omc/internal.py`: `_rebase_main`, the rc 2 guard and its stderr
  message ("finish it … or back out … first"), which still reads correctly
  for a person at the shell; the skill is what an agent follows.
- `src/omc/watch.py --rebase`: autostash, abort on conflict.
- `src/omc/distribution/AGENTS.md` (the behavior layer): its stage-gate rule
  is reused for its bound and CRITICAL stop; no conflict rule is added there (D3 — one owner).
- Finish's gate, count gate and stages; `create-mr`; the mirror.

## 5. Testing (red first, per `.omc/config/AGENTS.md`)

Unit, `tests/unit/test_plugin_manifests.py` — the file that already holds
the skill-contract tests (`test_rebase_main_skill_contract`,
`test_finish_starts_with_rebase_main`). The brainstorm named
`tests/unit/test_skills_source.py`; that file tests the `skill_text` loader
only, so the test goes next to its siblings.

1. New `test_rebase_conflicts_are_resolved_not_handed_back` (D9):
   - across every `skills/*/SKILL.md`, whitespace-flattened and lowercased,
     none of `never resolve conflicts silently`, `hand control to the user`,
     `conflicts → stop`, `conflict handoff` appears;
   - rebase-main carries the rule: `not a question`, `REBASE_HEAD`,
     `GIT_EDITOR=true git rebase --continue`, and a never-abort sentence;
   - finish Step 2 says to resolve per the `rebase-main` skill; start
     Step 3 invokes the `rebase-main` skill on conflicts and still stops on
     a dirty tree.
   It fails today on all three parts. The banned phrases only catch the old
   sentences coming back verbatim; the second and third parts are what pin
   the behavior.
2. `test_rebase_main_skill_contract` is edited, not just extended: its
   needle `git merge --abort` (the rc 2 back-out option) becomes
   `git merge --continue`. The `git reset --hard <old>` and
   `folded uncommitted changes stay in <wip>` needles stay — that text moves
   into the report step (§4.1 step 5).
3. `test_finish_starts_with_rebase_main` stays green as written: the
   `note` → stop sentence, the count gate and its wording are kept (§4.2).

`tests/unit/test_internal.py`: unchanged, the rc 3 / rc 2 tests included —
the CLI contract is the same.

README: regenerated, and the existing README guards in
`tests/unit/test_cli.py` must stay green.

What these tests prove is the wording, not the behavior: whether a model
merges well is only observable end to end. No E2E is added — it needs a
Claude token this host does not have, so it could not be watched failing
first; the finish stages are the runtime check on every real resolution.

## 6. Risks and side effects

- **A wrong merge that passes the gates.** The agent can write a merge that
  compiles, passes the tests and still loses an intent. Mitigations are the
  ones that exist: the report names every resolved path and flags the hard
  ones (D4), finish's review stage reads the diff, the MR is reviewed by a
  person, and in squash mode the pre-squash tip restores the branch as it
  was. This is accepted; it is the cost of not asking.
- **The agent concludes operations it did not start (D5).** A merge or
  cherry-pick the user left half-done in the worktree is finished by the
  agent on the next rebase-main. It is reported, never silent.
- **One paused rebase is still handed back.** In a plain clone's primary a
  paused rebase is a detached HEAD, so the CLI answers with the no-op `note`
  instead of rc 2 and finish stops and reports it. Reaching it takes a plain
  clone and a session interrupted mid-resolution; the agent resolves in the
  same turn as the rc 3, so an uninterrupted run never sees it. Not worth a
  second meaning for `note`.
- **The 0-count stop remains.** If the base already contains everything the
  branch changed, the replayed commit ends up empty and finish stops with
  "nothing is left over `<base>`". That is a result, not a question.
- **Standalone check cost.** A standalone `/omc:rebase-main` that resolved
  something now also runs `/omc:check`. Clean rebases are unaffected.
- **Harness caches.** Skill text changes reach installed plugins with the
  merge-time version stamp, as for any skill change.

## 7. Out of scope

`omc watch --rebase`; a rebase at `/omc:implement` start; CLI, rc-code or
verdict changes; a conflict rule in the behavior layer; an E2E that drives a
real conflict; rewriting the squash-before-rebase record beyond the D10
note; exact skill wording and its placement inside each skill's existing
section order (the builder's).

## Implementation review

2026-11-14 · auditor: claude (opus, Review worker) · base `origin/main` (a031408)

1. Important · record is stale · §3 anti-goals, §4.1 steps 2/6 ·
   `skills/rebase-main/SKILL.md` step 6 — whole-file `--ours`/`--theirs` banned
   only as a blind shortcut; reasoned keep-one-side for binaries/lockfiles is
   allowed and flagged. **Amended** (framed as the reasoned merge under D4); skill
   step 2 now reads "either side's intent".
2. Important · record is stale · §4.5, §4.7 · `skills/rebase-main/SKILL.md`
   after-resolution check paragraph — gate is check-only and merge repair is
   committed. **Amended** (consistent with D7).
3. Minor · record is stale · §4.4 · `skills/start/SKILL.md` authority header
   exception. **Amended.**
4. Minor · record is stale · §4.1 steps 1/3 · `skills/rebase-main/SKILL.md`
   steps 1 and 3 — `git status` on later stops; scoped mechanical marker check.
   **Amended.**
5. Minor · record is stale · §4.1 rc 2 · `skills/rebase-main/SKILL.md` rc 2 —
   conflicted `git stash pop` case. **Amended.**
6. Minor · code deviates · D6 / §4.5 · `skills/rebase-main/SKILL.md` check
   paragraph — "commit the repair" could commit a user's staged stash-pop work.
   **Fixed:** commit only the merge-repair paths; a staged stash-pop resolution
   stays staged.
7. Minor · record is stale · §4.6 · `.claude/skills/regenerate-readme/SKILL.md`
   — two recipe clauses, not one. **Amended.**

Post-fix gates: check identical to the 247-item host baseline; build clean;
verify token-free subset green (token-gated E2E unavailable on this host).

## Deliberate complexity

- `grug:locality` @ §4.4 — start keeps its inline `git rebase` and invokes
  the `rebase-main` skill only when that rebase pauses, instead of routing
  every stale base through the skill. Waived per D10 (and D2's scope): this
  record changes conflict handling only; start's clean path stays as the
  squash-before-rebase record left it.
