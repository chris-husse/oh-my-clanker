---
name: finish
description: Finish the current feature branch - rebase onto the base, squash to one commit whose message is the MR description, run the project's build/verify/review stages, push, then offer to close the worktree, address review comments, or discuss. Use when work on a ticket is done and ready for review.
---

# omc finish

Finish the current feature branch. Normally run inside an `omc start` worktree,
but any feature branch works.

## Step 0 — gate

- cwd is a git repo, on a **feature branch**: not detached HEAD, not the base
  branch. Determine the base from omc's config (`worktree.base_branch` in
  `~/.omc/config.json`) when readable; otherwise the repo's default branch
  (`git remote show origin`). Not on a feature branch → explain and stop.
- **Stacked branches are not supported**: if commits between
  `merge-base origin/<base> HEAD` and `HEAD` include another unmerged branch's
  tip (check `git branch --contains` on the earliest own commit), say "finish
  the parent branch first" and stop.

## Step 1 — anything to finish?

`git fetch origin <base>`, then count `git rev-list --count origin/<base>..HEAD`.
Zero commits and a clean tree → "nothing to finish on this branch", stop.

## Step 2 — rebase onto fresh base

`git rebase origin/<base>`. On **conflict**: stop with the rebase paused,
list the conflicted files, and hand control to the user — never resolve
conflicts silently, never `--abort` on their behalf.

## Step 3 — squash to one commit

Invoke the internal **`squash`** skill with the base — it folds uncommitted
changes (with notice), does the `reset --soft`, and leaves exactly one
temp-message commit on `origin/<base>..HEAD`. An `OMC_SQUASH {"ok": false}`
outcome → surface its message and stop.

## Step 4 — project stages: build → verify → review

Invoke the **`build`**, **`verify`**, and **`review`** skills, in that order.
Each is a proxy for the project's own `.omc/skills/<stage>/SKILL.md`:

- Unconfigured (`"configured": false`) → note it was skipped and move on.
- A stage that changed TRACKED files (formatters, autofixes) → amend those
  changes into the squashed commit (`git add -u && git commit --amend
  --no-edit`); leave untracked artifacts alone.
- A stage that FAILED (`"passed": false`) → **stop before pushing**: report
  which stage failed and why, and leave the branch squashed so the user can
  fix and re-run `finish`.

## Step 5 — describe and push

Invoke **`create-mr`** — it generates the MR description
(via `get-mr-description`), amends it into the squashed commit, and pushes
with `--force-with-lease`. The user creates the actual MR/PR from the forge;
the commit carries the full description.

## Step 6 — offer follow-ups

Report what happened (rebased onto `<base>`, squashed N→1, stage outcomes,
pushed, title),
then offer exactly these three options (interactively where the harness
supports it; in a non-interactive/headless run, list them and end):

1. **Close the worktree** — from the primary checkout (find it via
   `wt list --format=json`), run `wt remove` for this branch (`wt -C <primary>
   remove <branch>` if needed). `wt remove` deletes the branch only once it's
   merged, so the pushed branch survives until the MR lands. Then move the
   session out of the removed directory (e.g. into the primary checkout).
2. **Address review comments** — the user pastes review feedback; apply the
   changes, `git commit --amend` into the single commit, then re-run
   `create-mr` so the description reflects the final state and the branch is
   re-pushed (`--force-with-lease`).
3. **Chat about this** — discuss the change, the review, or what's next.
