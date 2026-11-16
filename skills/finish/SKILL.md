---
name: finish
description: Finish the current feature branch - squash to one commit, rebase onto the base, run the project's build and check stages (skipped on a tree the last green verify saw), pass the MR gate (a review only when the tree changed and `needs_review` asks; a verify whenever the tree changed since the last green verify, unless `needs_verify` says no), describe the commit as the MR and push, then offer to close the worktree, address review comments, or discuss. Use when work on a ticket is done and ready for review.
---

# omc finish

Finish the current feature branch. Normally run inside an `omc design` worktree,
but any feature branch works.

## Step 0 — externalize the flow (before the gate, no exceptions)

**Write every step below into the task list now**, as your first action:
gate → anything-to-finish → rebase-main --squash → build → check (skipped
on a verified tree) → create-mr (MR gate, describe, push) → ticket-sync
(review phase) → offer follow-ups. Mark each completed as you pass it.

This is the longest composed flow in omc: nine sub-skills, several nested
inside create-mr's MR gate, each arriving as a
fresh instruction block that reads like a new user request and pushes this one
out of view. Every one of them ends in a verdict line or an artifact, and every
one of those is an argument to the next step — never a place to stop. The task
list is what holds the stack; without it this flow reliably dies partway, most
often leaving the commit still reading `wip: squash of…`.

## Step 0.1 — gate

- cwd is a git repo, on a **feature branch**: not detached HEAD, not the base
  branch. Determine the base from the project's omc config
  (`worktree.base_branch` in the repo's `.omc/config.yaml`) when readable;
  otherwise the repo's default branch
  (`git remote show origin`). Not on a feature branch → explain and stop.
- **Stacked branches are not supported**: if commits between
  `merge-base origin/<base> HEAD` and `HEAD` include another unmerged branch's
  tip (check `git branch --contains` on the earliest own commit), say "finish
  the parent branch first" and stop.

## Step 1 — anything to finish?

`git fetch origin <base>`, then count `git rev-list --count origin/<base>..HEAD`.
Zero commits and a clean tree → "nothing to finish on this branch", stop.

## Step 2 — squash and rebase onto the fresh base

Invoke the **`rebase-main`** skill **with `--squash`** (`omc internal
rebase-main --squash`) — it folds uncommitted changes (with notice),
squashes the branch to one `wip:` commit over
`merge-base origin/<base> HEAD`, and rebases that single commit onto
`origin/<base>` (in the primary checkout itself — a plain clone — it
squashes and rebases the same way). Only one commit is replayed, so a
conflict with the base stops the rebase at most once — and this session
resolves that stop itself (rc 3 below). The `.gitnexus/` graph stays
primary for `/omc:explain` queries.

- **rc 3 bail** (conflicts, rebase left paused): resolve it per the
  `rebase-main` skill's rule — this is not a question. The rule's step-4
  re-run, with `--squash`, is finish's re-run — run it once (idempotent:
  the branch is already one commit over the base) before continuing.
- **rc 1** (the fetch failed, git refused the rebase, the squash failed —
  e.g. a hook rejected the squash commit): surface the stderr message and
  stop.
- **rc 2** (a rebase, merge, cherry-pick or revert already in progress):
  finish it per the `rebase-main` skill's rc 2; its re-run, with
  `--squash`, is finish's re-run — run it once.
- On an `"ok": true` verdict carrying `"squashed"`, count
  `git rev-list --count origin/<base>..HEAD`: **1** → continue to Step 3;
  **0** → say "nothing is left over <base> — either the branch's changes
  are already on <base>, or a conflict was skipped; the stderr
  `pre-squash tip … (undo: …)` line restores the original commits" and
  stop. Never amend or push then — that would publish the base's own tip
  as this branch. Any other count → stop and report it.
- A `note` verdict → stop and report it (it is reachable only on the base
  branch, which Step 0.1 refuses, or on a detached HEAD in the primary —
  e.g. a rebase still paused there after an rc 3 bail; finish the rebase
  first).

## Step 3 — build → check (skipped on a verified tree)

Run `omc internal gate verify` and read its single `OMC_GATE {…}` line.
`"reason": "unchanged"` means this exact tree was built, checked and verified
(audit's fixed verify, or an earlier MR gate, saw it): skip both stages and
say "build and check skipped — tree unchanged since the last green verify".
Any other reason → invoke the **`build`** skill, then the **`check`** skill:
`build` → `check`. Read only `reason` here; the verify question itself is
`create-mr`'s. A question that prints no `OMC_GATE` line decided nothing:
report the error and stop; nothing is pushed. Each stage is a proxy for the
project's stage hook `.omc/bin/<stage>`.

**Stage-running rules.** They hold for every stage run in this flow, the MR
gate's included; audit and `create-mr` cite them as finish's stage-running
rules.

- **Restore tool drift after each stage run** (build, check, review and
  verify alike, targeted reruns included), before anything is amended:
  restore only known tool drift, such as a lockfile changed by `uv run` or
  `uv build`; never discard unknown work. Amended drift would land in the
  published commit and change the tree a verify just passed, and `create-mr`
  needs a clean working tree.
- **Amend fixes before their reruns.** Each fix is amended into the squashed
  commit before its reruns, as stage-made tracked changes are: files a fix
  created are staged by path (`git add <new paths>`), then
  `git add -u && git commit --amend --no-edit` — so the tree a passing
  verify records is the tree it actually tested, and the tree pushed holds
  every file the review and verify judged. A fix worker names the files it
  created.
- A stage that changed TRACKED files (formatters, autofixes,
  behavior-preserving fixes from grug and human-review, or their waivers,
  conformance fixes, record amendments and the implementation-review trace
  written into the design record)
  → amend those changes into the squashed commit (`git add -u && git commit
  --amend --no-edit`) — only what remains once known tool drift is
  restored. Files those fixes created (a new test from a conformance fix,
  say) are staged by path first, as above; leave the untracked artifacts a
  stage itself leaves behind (reports, caches, build output) alone. A stage
  that staged a new file (e.g. `.gitnexusignore` from a review's index hygiene)
  changed the tree too: that staged file is not an untracked artifact and goes
  into the same amend.
- **A review that changed the tree.** Wherever a review runs (audit's
  fixed review, the MR gate's review question), note
  `git rev-parse HEAD^{tree}` before each review run. When the tree after
  the passing review's amend differs from the one noted before that review,
  the review's fixes changed the tree that was built and checked (its lens
  fixes ran a check, never a build): invoke the **`build`** skill, then the
  **`check`** skill, on the amended commit before the verify, so the tree a
  verify records was built and checked as it stands. A red build or check
  there falls under the stage-gate rule's general clause on the run's
  budget, then the verify follows; the flow never returns to review. An
  unchanged tree goes straight to the verify.
- Unconfigured (`"configured": false`) → the project half was skipped; note
  it and move on once `"passed"` (read first, below) is `true`. The review
  proxy also runs omc's own conformance pass and its grug and human-review
  lenses on every project, so a review can report
  `"configured": false, "passed": false`.

**A red stage.** A stage that FAILED (`"passed": false`,
regardless of `"configured"`, or no verdict) → follow the behavior layer's
**stage-gate rule — bounded fix-forward, then a CRITICAL stop**, with each
fix amended as above. Here a red `build` or `check` falls under the rule's
general clause: after the fix the sequence restarts from `build`. finish and
the `create-mr` it invokes are one conductor run with one fix-cycle budget.
When the rule stops, ask its one CRITICAL question: the branch stays
squashed and nothing is pushed. The user's answer resumes the remaining
steps.

## Step 4 — the MR gate, describe and push

Invoke **`create-mr`** — its Step 0 is the MR gate: the review question,
then the verify question, each answered by `omc internal gate` and, when the
tree changed, the project's `needs_review` or `needs_verify` hook. It then
generates the MR description (via `get-mr-description`), amends it into the
squashed commit, runs the project's `pre_push` hook, and pushes with
`--force-with-lease`. Its stages run by the stage-running rules above, on
this run's fix-cycle budget. The user creates the actual MR/PR from the
forge; the commit carries the full description.

Then, once `create-mr` reports a successful push, invoke the internal
**`ticket-sync`** skill with phase `review` — it moves the ticket (key
derived from the branch name) to an "In Review"-equivalent column. Any
failure: report its one-line message and continue to Step 5 — the push
already succeeded, and the ticket can be moved by hand. The Step 5
"address review comments" loop re-runs `create-mr` WITHOUT re-invoking
ticket-sync (the ticket is already in review).

Its `OMC_TICKET {…}` verdict is an argument to you, not the end of your turn:
whatever it says, your very next action after it is a tool call that begins
Step 5. `finish` is not complete until the user has been offered the
follow-ups.

## Step 5 — offer follow-ups

Report what happened (squashed N→1, N being Step 1's count — 0 means only
uncommitted changes were folded; rebased onto `<base>`; build and check
outcomes, or that they were skipped on a verified tree; what the MR gate
did: whether a review ran and why, whether a verify ran, was suppressed by
`needs_verify`, or was skipped as unchanged, and the `pre_push` outcome;
pushed, title),
then offer exactly these three options (interactively where the harness
supports it; in a non-interactive/headless run, list them and end):

1. **Close the worktree** — from the primary checkout (find it via
   `wt list --format=json`), run `wt remove` for this branch (`wt -C <primary>
   remove <branch>` if needed). `wt remove` deletes the branch only once it's
   merged, so the pushed branch survives until the MR lands. Then move the
   session out of the removed directory (e.g. into the primary checkout).
2. **Address review comments** — the user pastes review feedback; apply the
   changes. Applying them is work, so the cadence's `/omc:check` runs before
   the amend (a red check follows the stage-gate rule). Then
   `git commit --amend` into the single commit and re-run `create-mr` so the
   description reflects the final state and the branch is re-pushed
   (`--force-with-lease`); the MR gate inside `create-mr` decides review
   and verify for the re-push, as its own conductor run.
3. **Chat about this** — discuss the change, the review, or what's next.

## Completion contract

`finish` is complete only once Step 5 has run and the branch is pushed with a
real description. Before ending the turn, read the task list: any step still
pending means you are not done — continue with it. A branch left squashed under
a `wip:` message, or pushed but with the ticket never moved, is a failed run,
not a partial success. The one exception is the stage-gate rule's CRITICAL
stop: the branch stays squashed, nothing is pushed, and the run waits for the
user's answer — that is a valid stop, not a failed run.
Phase-to-phase transitions are NOT gates: do not ask permission between
steps.
