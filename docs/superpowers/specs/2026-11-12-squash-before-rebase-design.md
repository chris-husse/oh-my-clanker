# Squash before rebase: `omc internal rebase-main --squash` replaces the LLM-run squash

Date: 2026-11-12 · Branch: `feature/squash-before-rebase` · Slug: `squash-before-rebase`

## 1. Problem

`/omc:finish` publishes a branch as one commit, but it gets there in the
wrong order. `skills/finish/SKILL.md` Step 2 invokes `rebase-main`
(`omc internal rebase-main`, `src/omc/internal.py:_rebase_main`), which runs
a plain `git rebase origin/<base>` over the branch's N commits, and only then
does Step 3 invoke the `squash` skill. When the base has moved, replaying N
commits one by one stops at every commit that touches a changed line: the
same conflict, resolved up to N times, each stop handed back to the user with
`git rebase --continue`. The squash that follows throws those N commits away
anyway. User request: "make sure to squash before rebase".

Two more things are wrong in the same corner:

- `skills/squash/SKILL.md` is an LLM-run skill that does
  `git reset --soft origin/<base>`. That is correct only *after* a rebase. Run
  before one, it points the branch at `origin/<base>` while keeping the
  worktree's tree, so the single commit it creates silently reverts every
  commit main gained since the branch was cut. A squash that runs first must
  reset to the merge-base, and a deterministic step with that failure mode
  belongs in Python, not in a skill prompt.
- `_rebase_main` treats every non-zero `git rebase` as a conflict: it reads
  `git diff --diff-filter=U` and bails rc 3 with the rebase "left paused".
  On a dirty tree git refuses before starting (`error: cannot rebase: You
  have unstaged changes.`, exit 1, no rebase state), so the verdict is
  rc 3 `"conflicts": []` and the skill tells the user to resolve conflicts
  that do not exist. `src/omc/watch.py:_rebase_in_progress` already tells
  the two apart by git's own state dirs; `internal.py` never used it.

## 2. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | `rebase-main` gains `--squash`. `/omc:finish` passes it; a standalone `/omc:rebase-main` never squashes and keeps history. | A separate `omc internal squash` verb (two commands, two verdicts, and finish would still have to order them); squashing on every rebase-main (a mid-work refresh must not rewrite the user's commits). |
| D2 | The squash moves into deterministic Python inside `_rebase_main`; the LLM never runs `reset --soft` again. | Keep `skills/squash` and swap the two finish steps (the skill would still compute the reset target, which is exactly the step that must not be improvised). |
| D3 | The squash resets to `git merge-base origin/<base> HEAD`, never to `origin/<base>`. | `reset --soft origin/<base>` before the rebase (reverts main's newer commits; the bug named in §1). |
| D4 | Scope is the ordering fix plus the dirty-tree misreport. Non-goals: `omc watch --rebase`, `/omc:start`'s freshness rebase, stacked branches, automatic conflict resolution. | Folding the watch and start rebases into the same verb (different callers, different failure handling, no shared bug). |
| D5 | The temp commit `wip: squash of <branch>` honors git hooks (no `--no-verify`). If the commit fails, `git reset --soft <old-tip>` restores the original commits and the verb exits rc 1 with git's message. | `--no-verify` (bypasses a project's own commit policy on the one commit that gets published). |
| D6 | `skills/squash` is deleted and `OMC_SQUASH` retired from every contract list; the folded count travels in `OMC_REBASE_MAIN` as `"squashed": <n>`. | Keep the skill as a thin wrapper that prints `OMC_SQUASH` from the rebase-main verdict (a second verdict for one operation). |
| D7 | A rebase already in progress (git's `rebase-merge`/`rebase-apply` dirs), unmerged index entries, or an unconcluded merge / cherry-pick / revert (`MERGE_HEAD` / `CHERRY_PICK_HEAD` / `REVERT_HEAD`, amended at audit) make `rebase-main` refuse with rc 2 before it fetches, in both modes. The state-dir check is `_rebase_in_progress`, moved to a shared helper in `src/omc/wtconfig.py` so `watch.py` and `internal.py` use one copy. | Let git refuse and report it as rc 1 (git's message for a stale `rebase-merge` dir is not actionable; and in squash mode `git add -A` would first stage conflict markers as resolved). |

D5 and D6 are the brainstorm's recommended defaults, taken when the user
invoked `/omc:design` without overriding them; D7 is the Design worker's
call on the guard's placement.

## 3. Goals and non-goals

**Goals.**

- `/omc:finish` resolves a conflict with the base at most once, regardless of
  how many commits the branch carries.
- Squashing before the rebase never changes what the branch contributes:
  `git diff origin/<base>..HEAD` after `rebase-main --squash` is the branch's
  own work and nothing of main is reverted.
- The dirty-tree misreport is gone in both modes: a `git rebase` that never
  started is rc 1 with git's own message, never a fake conflict bail.
- One deterministic verb, one verdict, one skill contract (`OMC_REBASE_MAIN`).

**Non-goals (D4).**

- `omc watch --rebase` keeps its `--autostash` + abort behavior in
  `watch.py`; only its state-dir helper moves.
- `/omc:start` Step 3's freshness rebase stays an inline `git rebase` in the
  skill text.
- Stacked branches (finish's gate still refuses them); resolving conflicts on
  the user's behalf; a `--no-squash` escape hatch for finish.

**Anti-goals.**

- Any path where the squash commit is created over a ref other than the
  merge-base.
- A standalone `/omc:rebase-main` rewriting the user's commits.

## 4. Design

### 4.1 `omc internal rebase-main [--squash]` — the flow

`run_internal` adds `--squash` (store-true) to the existing `rebase-main`
argparse parser; `_rebase_main(ctx, base_arg, squash: bool)` runs:

1. **Primary checkout** → the existing no-op verdict (`"note": "primary
   checkout — nothing to rebase"`), unchanged and emitted before anything
   else — **unless** `--squash` is given on a feature branch there (a plain
   clone with no worktree; amended at audit, user decision). Then the
   primary runs the full flow below — guard, fetch, squash, rebase, same
   rc 1/2/3 — and skips only the `.omc/docs` mirror (`"synced": []`,
   `"shared": []`: the primary is the mirror's source). On the base branch,
   detached (including a paused rebase, which shows as detached), or
   without `--squash`, the primary keeps the no-op; `omc watch` owns it
   there and leaves off-branch checkouts alone.
2. **Guard (new, both modes, D7).** `rebase_in_progress(ctx, root)` (the
   shared helper, §4.2), a non-empty `git ls-files -u`, or a resolving
   `MERGE_HEAD` / `CHERRY_PICK_HEAD` / `REVERT_HEAD` → stderr
   `error: a rebase or merge is in progress — …` naming the
   `git rebase --continue` / `git merge --continue` forms and their
   `--abort`, plus the cherry-pick/revert equivalents; rc 2, no verdict.
   This is the same shape as the existing "not inside a git repository"
   refusal. It runs before the fetch so a refusal never touches the network.
3. `git fetch origin <base>`; failure → rc 1 with git's stderr, as today.
   `knowledge = snapshot_freshness(...)` is computed here as today.
4. `old = HEAD` (short sha, the pre-squash tip; this is what `"rebased"`
   reports).
5. **Squash (only with `--squash`).**
   - `mb = git merge-base origin/<base> HEAD`;
     `n = git rev-list --count <mb>..HEAD`.
   - Dirty tree (`git status --porcelain` non-empty) → stderr
     `→ folding uncommitted changes into the squash`, then `git add -A`.
     Untracked files are folded too, ignored ones never (`.omc/docs/` and
     `.gitnexus/` are gitignored); this is exactly what the deleted skill
     did and what `audit` Phase 2 relies on when it says leaving a file
     unstaged does not keep it out of publication.
   - `n == 0` and a clean tree → nothing to squash; stderr `· nothing to
     squash`, continue to step 6 with `squashed = 0`.
   - Otherwise `git reset --soft <mb>` then
     `git commit -m "wip: squash of <branch>"` (branch from
     `wtconfig.current_branch`; hooks run, D5). Commit failure → stderr with
     git's message, `git reset --soft <old>` so the original commits are
     back (the index stays staged, which is at worst the user's own dirty
     changes now staged), rc 1, no verdict.
   - Commits and folded changes that together change nothing over `<mb>`
     (`git diff --cached --quiet <mb>`) → checked *before* the reset, so
     HEAD never moves: stderr `error: the branch changes nothing over the
     merge-base <mb> (pre-squash tip <old>) …`, rc 1, no verdict.
   - Any other failing git step in the squash (merge-base, `add -A`,
     reset) → rc 1 with git's message. If the automatic rollback after a
     failed commit itself fails, stderr says the original commits could not
     be restored, that HEAD is at `<mb>` with all changes staged, and gives
     `git reset --soft <old>` (amended at audit).
   - stderr `· pre-squash tip <old> (undo: git reset --hard <old>)`; when
     changes were folded the line adds `; folded uncommitted changes stay in
     <wip-sha>` — the squash commit as created, still reachable by sha after
     the rebase — because `--hard` drops them from the working tree.
6. `git rebase origin/<base>` — with `--squash` this replays exactly one
   commit, so it stops at most once. On non-zero exit:
   - `rebase_in_progress` true → a real conflict: the existing rc 3 verdict
     `{"ok": false, "conflicts": [...], "knowledge": ...}`, rebase left
     paused, never aborted here.
   - otherwise git refused before starting (dirty tree in standalone mode,
     or anything else git rejects up front) → stderr
     `error: git rebase origin/<base> failed: <git's stderr>`, rc 1, no
     verdict. This retires the `rc 3 "conflicts": []` misreport.
7. `mirror_snapshot` and the success verdict, as today.

Nothing in steps 5–6 is wrapped in a try/finally that aborts or resets
behind the user's back; the only automatic restore is the one in step 5 for a
failed commit, which leaves the commits as they were found (folded changes
stay staged), and says exactly how to restore when even that fails.

### 4.2 Shared helper: `wtconfig.rebase_in_progress`

`watch._rebase_in_progress(ctx, root)` moves to
`src/omc/wtconfig.py` as `rebase_in_progress(ctx, root) -> bool`, docstring
and the `rebase-merge` / `rebase-apply` state-dir check intact (it uses
`ctx.run` directly instead of watch's `_out`). `watch.py` imports it; its
`--rebase` branch is otherwise unchanged. `wtconfig.py` is already where
`repo_root`, `primary_root` and `current_branch` live and is imported by
`internal.py`, so no new module and no new import direction.

### 4.3 The verdict

`OMC_REBASE_MAIN` keeps its schema and gains one key in squash mode:

```
OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...],
                 "shared": [...], "squashed": <n>, "knowledge": {...}}
```

- `squashed` is present only when `--squash` was given: the number of
  branch commits folded over the merge-base (0 when there was nothing to
  squash, or when only uncommitted changes were committed — the fold is
  narrated on stderr, not counted). Finish's report says "squashed N→1"
  with N taken from its own Step 1 count (`origin/<base>..HEAD` right after
  the fetch), not from the verdict: after a conflict resume the re-run's
  verdict says `squashed: 1` (amended at audit).
- `rebased` is `<pre-squash tip>..<new HEAD>` — the range a user would
  inspect in the reflog to undo the whole operation.
- rc 3 and the primary no-op verdict are byte-for-byte what they are today;
  rc 1 and rc 2 paths print `error:` on stderr and no verdict, as the
  existing fetch failure and not-a-repo refusal already do.

### 4.4 Resuming after a conflict

After the rc 3 bail the user resolves, `git add`, `git rebase --continue`.
`/omc:finish` then re-runs `rebase-main --squash` exactly as the current
skill text already says ("after the rebase completes, run
`omc internal rebase-main` again so the snapshot mirror still happens"). The
re-run is idempotent: the branch is now one commit over `origin/<base>`, so
the merge-base is `origin/<base>`, `n == 1`, the squash recreates the same
one-commit tree with the same temp message, the rebase is a no-op, and the
mirror runs. The verdict reports `"squashed": 1`. No resume mode, no state
file.

### 4.5 `skills/finish/SKILL.md`

- **Frontmatter description** "rebase onto the base, squash to one commit
  whose message is the MR description" becomes "squash to one commit, rebase
  onto the base, …" — the README recipe reads every skill's frontmatter as a
  lifecycle source, so the regenerated finish paragraph follows from here.
- **Step 0** task list: `gate → anything-to-finish → rebase-main --squash →
  the four project stages → create-mr (describe + push) → ticket-sync (review
  phase) → offer follow-ups`; "nine sub-skills" becomes eight.
- **Step 2 — squash and rebase onto the fresh base.** Invoke the
  `rebase-main` skill **with `--squash`** — it folds uncommitted changes
  (with notice), squashes the branch to one `wip:` commit over the
  merge-base, rebases that single commit onto `origin/<base>`, and re-mirrors
  `.omc/docs`. rc 3 → hand control to the user per that skill, never resolve
  or abort on their behalf; once the rebase has completed, re-run the
  `rebase-main` skill with `--squash` (idempotent, §4.4) before continuing.
  rc 1 or rc 2 → surface the stderr message and stop. On an `"ok": true`
  verdict carrying `"squashed"`, count `origin/<base>..HEAD`: 1 → continue;
  0 → stop with "nothing is left over `<base>`" (the changes are already on
  the base, or a conflicting commit was skipped — the pre-squash-tip undo
  line restores the original commits), never amend or push; any other count
  → stop and report it. A primary-checkout `note` verdict can only mean
  finish ran on the base branch, which the gate refuses → stop and report
  it. (The count gate was added by the final review, amended at audit: an
  already-landed change would otherwise have `create-mr` amend and
  force-push the base's own tip.)
- **Step 3** (squash) is removed; Steps 4–6 become 3–5. Step 4's "leave the
  branch squashed so the user can fix and re-run `finish`" and the `wip:`
  warnings stay true as written.
- **Step 6 → 5** report: "squashed N→1", N being Step 1's count (§4.3);
  0 means only uncommitted changes were folded.
- `skills/ticket-sync/SKILL.md`'s reference to finish's follow-ups step
  follows the renumbering (Step 5).

### 4.6 `skills/rebase-main/SKILL.md`

Documents `omc internal rebase-main [--base <branch>] [--squash]`:
`--squash` is `/omc:finish`'s flag — a standalone `/omc:rebase-main` always
keeps history and never passes it; the verdict's `squashed` key; the rc 2
refusal (a rebase or merge in progress — relay the stderr line and stop);
rc 1 (git refused the rebase before starting, or a hook rejected the squash
commit — relay git's message, nothing was left paused); and the unchanged
rc 3 handoff, whose "run `omc internal rebase-main` again" sentence becomes
"re-run it with the same arguments", and whose `git rebase --abort` option
gains one clause: after `--squash`, abort lands on the single `wip:`
commit, and the narrated pre-squash tip (`git reset --hard <old>`) is the
way back to the original commits.

### 4.7 Removed: `skills/squash/`

Deleted outright (D6). The 2026-07-17 stages record extracted it from
`finish` "for future control over the process"; that control is now the
Python verb, so the skill has nothing left to own. Both plugins pick skills
up by directory, so no manifest edit; the merge-time version stamp refreshes
harness caches as for any skill change. No other skill invokes it (`finish`
was its only caller).

### 4.8 Contract lists, prose, README

`OMC_SQUASH` leaves every list that names the sacred contracts:
`src/omc/distribution/AGENTS.md` (the "Machine contracts are sacred"
bullet), `.omc/config/AGENTS.md`, `.omc/skills/explain-context/SKILL.md`,
`.omc/skills/review/SKILL.md`, `.claude/skills/regenerate-readme/SKILL.md`
(the contract list in the architecture sequence, "squash" moves from the
skill-emitted verdicts to the CLI's rebase-main verdict, and the lifecycle
recipe's "Finish rebases/mirrors, squashes with MR description" becomes
"Finish squashes, rebases/mirrors, describes"), and
`INTERNAL_SKILLS` in `tests/unit/test_plugin_manifests.py` (plus the
anti-stall comment's "finish -> squash/stages/create-mr").

"rebase, squash" becomes "squash, rebase" in `src/omc/distribution/AGENTS.md`
("Publish through `/omc:finish`") and `skills/audit/SKILL.md` Phase 3;
audit's Phase 2 sentence "`finish`'s squash uses `git add -A`" stays true
and is left as is.

`README.md` is generated: after the recipe edits, `/regenerate-readme` runs;
the finish paragraph ("rebases …, squashes to one commit") and the contract
list (eight names → seven, "squash" no longer a skill-emitted verdict) are
the expected diff. Historical specs (`2026-07-17-omc-stages-design.md`,
`2026-07-17-omc-finish-design.md`, `2026-10-06-run-verify-in-implement-phase-design.md`)
are left as written; this record supersedes their rebase-then-squash order.

### 4.9 Unchanged

- `watch.py --rebase` semantics (autostash, abort on conflict, "refused
  before starting" wording) — only the helper's home moves.
- `skills/start/SKILL.md` Step 3 (freshness rebase) and its dirty-tree
  handling.
- `create-mr` / `get-mr-description`: they amend the `wip:` commit exactly as
  before; the commit now predates the rebase but is still the single commit
  on `origin/<base>..HEAD`.
- `mirror_snapshot`, `snapshot_freshness`, the `knowledge` payload, the
  primary no-op path, and the rc 3 conflict verdict.
- `ToolContext` remains the only subprocess boundary: every new git call is
  `ctx.run([...])` with argv lists; the branch name enters a `-m` argument,
  never a shell.
- finish's Step 0.1 gate (feature branch, no stacked branches) and Step 1
  (`origin/<base>..HEAD` count) run before Step 2 as today, so the verb is
  never asked to squash a detached HEAD or the base branch.

## 5. Tests (red first, per `.omc/config/AGENTS.md`)

Unit, `tests/unit/test_internal.py`, real git through
`_setup_primary_with_origin` / `_add_worktree` / `_advance_main`:

1. `test_rebase_main_squash_hits_a_conflict_once` — three branch commits each
   touching `f.txt`, main changes `f.txt`: `--squash` bails rc 3 once; after
   resolving and `git rebase --continue` the branch is one commit over
   `origin/main` with no second stop (`git status` shows no rebase).
2. `test_rebase_main_squash_does_not_revert_main` — branch commits on
   `a.txt`, main adds `b.txt`: rc 0, `rev-list --count origin/main..HEAD`
   is 1, `b.txt` exists, `git diff --name-only origin/main..HEAD` is exactly
   `a.txt`, verdict `squashed` equals the pre-squash count.
3. `test_rebase_main_squash_folds_dirty_tree` — an unstaged edit and an
   untracked file are in the single commit; stderr carries the folding
   notice; tree clean afterwards.
4. `test_rebase_main_dirty_tree_without_squash_is_rc1_not_a_fake_conflict` —
   standalone mode on a dirty tree: rc 1, stderr contains git's "cannot
   rebase", no `OMC_REBASE_MAIN` line, no rebase in progress, HEAD unchanged.
   The test repo sets `rebase.autoStash false` (`ToolContext.child_env`
   inherits the host's global git config, and an autostashing host would
   otherwise rebase cleanly).
5. `test_rebase_main_refuses_mid_rebase` — with a rebase paused (set up via
   the existing conflict test's shape), both modes exit rc 2 before fetching;
   HEAD, index and the paused state are untouched.
6. `test_rebase_main_squash_restores_commits_when_hook_rejects` — a
   `commit-msg` hook that exits 1 (installed under the repo's own
   `core.hooksPath`, pinned so a host-wide hooks path cannot shadow it):
   rc 1, `git log` shows the original commits, HEAD equals the old tip,
   stderr carries the hook's message.
7. `test_rebase_main_without_squash_keeps_history` — N commits in, N commits
   over `origin/main` out, no `squashed` key in the verdict.
8. `test_rebase_main_in_primary_is_noop_with_squash` — the primary verdict
   is unchanged with `--squash`.
9. `test_rebase_main_squash_nothing_to_squash` — clean tree, no commits over
   the base: rc 0, `squashed` is 0, no commit created.
10. `test_rebase_main_squash_rerun_after_resume_is_idempotent` — the §4.4
    re-run: rc 0, still one commit, `squashed` is 1.

Unit, `tests/unit/test_plugin_manifests.py`:

- `INTERNAL_SKILLS` drops `"squash"`; `test_squash_skill_contract` is deleted.
- `test_finish_starts_with_rebase_main` asserts `--squash` appears in Step 2
  and that neither `` `squash` `` (the skill reference) nor `OMC_SQUASH`
  appears anywhere in the finish text.
- `test_finish_skill_contract`'s order check (which today starts at
  `` `squash` ``) becomes `` `rebase-main` `` → check → build → verify →
  review → create-mr.
- `test_rebase_main_skill_contract` adds the needles `--squash`,
  `squashed`, `rc 2`, and a sentence that standalone use never squashes.
- `test_machine_contract_listings_include_knowledge_and_models` (behavior
  layer, dogfood AGENTS.md, review, explain-context) also asserts
  `OMC_SQUASH` is absent from each listing.

Unit, `tests/unit/test_watch*.py`: no behavior change; a watch `--rebase`
test that monkeypatches the helper is repointed at `wtconfig` if one exists
(none does today).

E2E: `tests/e2e/test_e2e_finish.py::test_finish_squashes_describes_and_pushes`
already asserts exactly one pushed commit over `main` and stays valid
unchanged. It needs a Claude token, which this host does not have, so it is
not run on this branch; the ordering is covered by unit tests 1 and 2
against real git. Its plain-clone setup runs finish in a primary checkout,
which §4.1 step 1's audit amendment keeps working.

Added during implementation and review (beyond the list above): the
resolved-but-unconcluded merge/cherry-pick/revert refusal, the failed-rollback
report, the dirty-tree-without-commits fold, the branch-that-changes-nothing
refusal, the primary-checkout squash on a feature branch (and its no-op on
the base branch and without `--squash`), the folded-changes undo clause,
`test_rebase_in_progress_tracks_git_state_dirs`, `test_squash_skill_is_retired`,
`test_ticket_sync_names_finish_follow_ups_step`,
`test_publish_route_squashes_before_rebasing`, and the README guards in
`tests/unit/test_cli.py`.

## 6. Risks and side effects

- **The squash commit now predates the rebase.** Any project hook that runs
  on the `wip:` commit sees the pre-rebase tree. `create-mr` amends the
  message after the stages ran on the rebased tree, as today.
- **Two restore points, one of them automatic.** A failed squash commit is
  rolled back by the verb (D5); a conflicted rebase is left paused (today's
  contract). Users who want the pre-squash shape back after a successful
  run use the narrated `git reset --hard <old>`; the folded working-tree
  changes then exist only in the reflog's squash commit, which the stderr
  line says.
- **`git rebase --abort` no longer restores the original commits.** Today
  an abort after the rc 3 bail returns the branch untouched; with `--squash`
  it returns to the one `wip:` commit, because the squash happened first.
  The way back to the N commits is the same narrated `git reset --hard
  <old>`; the rebase-main skill's rc 3 text says so next to its abort
  option.
- **Standalone mode gets stricter.** A dirty tree used to bail rc 3 with
  `"conflicts": []` and the skill talked the user through phantom conflicts;
  it is now rc 1 with git's message. A paused rebase used to fall into the
  same rc 3 path; it is now rc 2, as is an unconcluded merge, cherry-pick or
  revert. These are the fixes asked for; nothing else about standalone
  behavior changes.
- **Finish in a plain clone.** Run in a primary checkout on a feature
  branch, finish used to skip the rebase silently (and the old squash could
  then revert newer base commits); it now squashes and rebases there too,
  without a docs mirror (§4.1 step 1).
- **Hooks that rewrite files.** A pre-commit formatter that modifies the
  tree and exits non-zero leaves those edits in the working tree after the
  rollback — the same outcome as running the hook by hand.
- **Removed verdict.** Anything outside this repo that parsed `OMC_SQUASH`
  loses it; nothing in `src/`, `tests/` or the skills reads it (verified by
  grep), and the README documents the new list after regeneration.

## 7. Out of scope

`omc watch --rebase`; `/omc:start`'s freshness rebase; stacked branches; a
`--no-squash` / history-preserving finish; automatic conflict resolution;
amending the historical specs that describe the old order; a JSON verdict on
the rc 1 / rc 2 paths.

## Implementation review

### 2026-11-12 — audit (Claude, Opus 5.5; Review worker on `tasks.review`)

Same provider as the implementation. Branch diff against `origin/main`
(0 behind). Eight findings; every Important one dispositioned.

| # | Severity | Kind | Record section | `file:line` | Disposition |
|---|---|---|---|---|---|
| 1 | Important | code deviates | §4.1 step 1, §4.5, §5 (E2E), §6 | `skills/finish/SKILL.md` Step 2; `src/omc/internal.py` `_rebase_main` primary check | User decision (CRITICAL question): `--squash` on a feature branch in the primary squashes and rebases, no mirror. Code fixed (d8a60cc), README regenerated (f87e0bb), §4.1 step 1 / §6 / §5 amended. |
| 2 | Important | code deviates | §6 "Two restore points", §4.1 step 5 | `src/omc/internal.py` `_squash` undo narration; `skills/rebase-main/SKILL.md` rc 3 | Code fixed (d8a60cc): with a dirty fold the undo line names the squash commit that keeps the folded changes; §4.1 step 5 amended. |
| 3 | Important | record stale | D7, §4.1 step 2, §6 | `src/omc/internal.py` guard; `skills/rebase-main/SKILL.md` rc 2 | Record amended: D7 extended to unconcluded merge/cherry-pick/revert (extends, contradicts no decision). |
| 4 | Important | record stale | §4.5 Step 2 | `skills/finish/SKILL.md` Step 2 count gate | Record amended; gate wording fixed in code (d8a60cc) for the skipped-conflict cause and "any other count → stop". |
| 5 | Minor | record stale | §4.1 step 5, closing paragraph | `src/omc/internal.py` `_squash` rc 1 paths | Record amended. |
| 6 | Minor | record stale | §4.3, §4.5 | `skills/finish/SKILL.md` report line; `tests/unit/test_plugin_manifests.py` comment | Record amended; test comment corrected (d8a60cc). |
| 7 | Minor | record stale | §4.5, §4.8, §5 | ticket-sync renumber, rebase-main description, extra tests | Record amended (§4.5 ticket-sync bullet, §5 added-tests paragraph). |
| 8 | Minor | code deviates | §6 first bullet | `skills/rebase-main/SKILL.md` hooks sentence | Fixed (d8a60cc): "(they see the pre-rebase tree)". |

Gates on this host (user ruling at the implement milestone): check is
judged against the base commit's 22 host-environment failures (identical
list, 0 new); verify runs only the token-free E2E subset — no Claude token
exists here, so `tests/e2e/test_e2e_finish.py` was not run.

## Deliberate complexity

None.
