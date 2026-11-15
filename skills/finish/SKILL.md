---
name: finish
description: Finish the current feature branch - squash to one commit, rebase onto the base, run the project's check/build/verify/review stages and a final verify, describe the commit as the MR and push, then offer to close the worktree, address review comments, or discuss. Use when work on a ticket is done and ready for review.
---

# omc finish

Finish the current feature branch. Normally run inside an `omc design` worktree,
but any feature branch works.

## Step 0 — externalize the flow (before the gate, no exceptions)

**Write every step below into the task list now**, as your first action:
gate → anything-to-finish → rebase-main --squash → check → build →
pre-review verify → review → final verify (skipped when the tree is unchanged)
→ create-mr (describe + push) → ticket-sync (review phase) → offer follow-ups.
Mark each completed as you pass it.

This is the longest composed flow in omc: eight sub-skills, each arriving as a
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

## Step 2 — squash and rebase onto the fresh base + refresh the snapshot

Invoke the **`rebase-main`** skill **with `--squash`** (`omc internal
rebase-main --squash`) — it folds uncommitted changes (with notice),
squashes the branch to one `wip:` commit over
`merge-base origin/<base> HEAD`, rebases that single commit onto
`origin/<base>`, and re-mirrors `.omc/docs` from the primary checkout
(in the primary checkout itself — a plain clone — it squashes and rebases
the same way and mirrors nothing). Only one commit is replayed, so a
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

## Step 3 — project stages: check → build → verify → review → final verify

Invoke the **`check`**, **`build`**, **`verify`**, and **`review`** skills,
in that order, then **`verify`** once more: `check` → `build` → `verify` →
`review` → final `verify`. Check comes first, as the cheap fail-fast gate: a
broken unit test dies in seconds, not after a world-build. The first verify
run is the **pre-review verify**; the second is the **final verify**, the
last stage before the push. This step is the only place an agent starts
`verify`. Each stage is a proxy for the project's own
`.omc/skills/<stage>/SKILL.md`:

- **Announce each verify run.** Before each verify run, say on its own line
  that the project's `verify` stage is starting and which of the two runs it
  is (pre-review verify or final verify) — it can run for minutes, and a
  silent minute is a bug.
- **Restore tool drift after each stage run** (check, build and verify alike,
  targeted reruns included), before anything is amended: restore only known
  tool drift, such as a lockfile changed by `uv run` or `uv build`; never
  discard unknown work. Amended drift would land in the published commit and
  change the tree verify just passed, and `create-mr` needs a clean working
  tree.
- **Record the verified tree.** Each time a full verify passes — the
  pre-review verify, the final verify, or a full run inside a repair loop —
  record `git rev-parse HEAD^{tree}` at once, before anything is amended, in
  the task list entry for the final verify. No file is written; the task list
  carries the value through this flow.
- **Skip rule for the final verify.** After `review` and its amend, compare
  `git rev-parse HEAD^{tree}` with the recorded value. Equal → skip the final
  verify and report "final verify skipped — tree unchanged since verify
  passed". Different → run the final verify. Any change counts: a review that
  only wrote a waiver into the design record changes the tree, so the final
  verify runs; what a documentation-only change needs is the project's
  `verify` skill's call.
- A stage that FAILED (`"passed": false`, regardless of `"configured"`, or no
  verdict) → follow the behavior layer's **stage-gate rule — bounded
  fix-forward, then a CRITICAL stop**. Each fix is amended into the squashed
  commit (`git add -u && git commit --amend --no-edit`), as stage-made
  tracked changes are, before its reruns — so the tree a passing verify
  records is the tree it actually tested. A red `check`, `build` or `review`
  falls under the rule's general clause: after the fix and `/omc:check`, the
  sequence restarts from `build` and passes through the pre-review verify,
  `review` and the final verify (with its skip rule) again. A red verify —
  pre-review or final — is repaired by the rule's verify clause, which
  reruns nothing before verify: a repaired pre-review verify continues with
  `review`, and final-verify fixes are amended and pushed without another
  `review`. At the bound, ask the rule's one CRITICAL question: the branch
  stays squashed and nothing is pushed. The user's answer resumes the
  remaining steps.
- A stage that changed TRACKED files (formatters, autofixes,
  behavior-preserving fixes from grug and human-review, or their waivers
  written into the design record)
  → amend those changes into the squashed commit (`git add -u && git commit
  --amend --no-edit`) — only what remains once known tool drift is
  restored; leave untracked artifacts alone.
- Unconfigured (`"configured": false`) → the project half was skipped; note
  it and move on once `"passed"` (read first, above) is `true`. The `review`
  proxy also runs omc's own grug and human-review lenses on every project,
  so `review` can report `"configured": false, "passed": false`.

## Step 4 — describe and push

Invoke **`create-mr`** — it generates the MR description
(via `get-mr-description`), amends it into the squashed commit, and pushes
with `--force-with-lease`. The user creates the actual MR/PR from the forge;
the commit carries the full description.

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
uncommitted changes were folded; rebased onto `<base>`, stage outcomes
(both verify runs; whether the final verify ran or was skipped), pushed,
title),
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

## Completion contract

`finish` is complete only once Step 5 has run and the branch is pushed with a
real description. Before ending the turn, read the task list: any step still
pending means you are not done — continue with it. A branch left squashed under
a `wip:` message, or pushed but with the ticket never moved, is a failed run,
not a partial success. The one exception is the stage-gate rule's CRITICAL
stop at the bound: the branch stays squashed, nothing is pushed, and the run
waits for the user's answer — that is a valid stop, not a failed run.
Phase-to-phase transitions are NOT gates: do not ask permission between
steps.
