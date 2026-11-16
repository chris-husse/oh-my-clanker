---
name: rebase-main
description: Refresh this worktree onto the latest base branch. The .gitnexus graph stays primary. Run anytime; standalone it keeps your commits. /omc:finish runs it first, with --squash.
---

# omc rebase-main

A worktree is a snapshot of main's code at cut time. The `.gitnexus/` graph
stays in the primary checkout, where `/omc:explain` queries it. This
refreshes the code via the deterministic Python subcommand:

```sh
omc internal rebase-main
```

(`--base <branch>` overrides the configured base.)

`--squash` is `/omc:finish`'s flag. Before rebasing it folds uncommitted
changes (narrated on stderr) and squashes the branch to one
`wip: squash of <branch>` commit over `git merge-base origin/<base> HEAD`,
so the rebase replays one commit and stops at most once. Project hooks run
on that commit (they see the pre-rebase tree).

A standalone `/omc:rebase-main` always keeps history: never pass `--squash`
unless `/omc:finish` invoked this skill with it.

## Interpreting the outcome

The last line is machine-readable:

- `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>"}` — report what
  moved, then continue. With `--squash` the verdict also carries
  `"squashed": <n>` — the branch commits folded (0 when there was
  nothing to squash, or only uncommitted changes were folded) — and `<old>`
  is the pre-squash tip.
- `OMC_REBASE_MAIN {"ok": true, ..., "note": "primary checkout — nothing to
  rebase"}` — you're in the main checkout; `omc watch` owns freshness here.
  The primary keeps this no-op unless `/omc:finish` passes `--squash` on a
  feature branch there (a plain clone): then it squashes and rebases there —
  the normal verdict with `"squashed"`. A detached HEAD there — a rebase
  still paused in the primary included — gets this no-op too, never rc 2.
- **rc 3 (bail)** with `{"ok": false, "conflicts": [...]}` — the rebase hit
  conflicts and is LEFT PAUSED. You resolve it — this is not a question:
  1. For each conflicted path — on the first stop the verdict's
     `conflicts`; after each later stop, and when there is no verdict,
     `git status` (the verdict's list is stale by then) — read both sides
     before editing. The base's side —
     what the base did to the file since the branch forked:
     `git log -p $(git merge-base origin/<base> REBASE_HEAD)..origin/<base> -- <file>`
     (the merge-base is taken against `REBASE_HEAD`: against `HEAD` it is
     `origin/<base>` itself while the rebase is paused). The branch's side —
     the commit being replayed: `git show REBASE_HEAD -- <file>`.
  2. Write the merge that keeps both intents. Where the base renamed, moved
     or reshaped the code, carry the branch's change onto the new shape.
     Never drop either side's intent.
  3. Leave no conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) in the
     resolved paths and `git add` them. Then confirm mechanically:
     `git diff --cached --check -- <resolved paths>` prints no
     `leftover conflict marker` line — only those lines count, not its exit
     code and not its whitespace reports — and
     `git diff --name-only --diff-filter=U` prints nothing. A flagged line
     already present in either side's version of the file
     (`git show HEAD:<file>`, `git show REBASE_HEAD:<file>`) is content —
     e.g. a Markdown `=======` heading underline — not a leftover; keep it.
     Only then `GIT_EDITOR=true git rebase --continue`.
  4. Stopped again (a standalone rebase replays several commits) → repeat
     from 1. When the rebase completes, re-run `omc internal rebase-main`
     with the same arguments so the run ends with its success verdict (with
     `--squash` the re-run is idempotent: the branch is already one commit
     over the base).
  5. Report in a few lines what conflicted and how you resolved each path;
     flag any merge where the two intents were hard to reconcile. In squash
     mode, carry the stderr line
     `pre-squash tip <old> (undo: git reset --hard <old>)` from the run that
     hit the conflict into the report as the way back to the branch as it
     was; when uncommitted changes were folded, that reset drops them from
     the tree, and the line adds
     `folded uncommitted changes stay in <wip>` — the squash commit that
     keeps them; carry that clause too. The re-run prints the line again
     with the already-rebased tip: that one is not the way back. This is a
     report, not a question; the flow continues.
  6. Never `git rebase --abort`, never `git rebase --skip`, no `-X ours` /
     `-X theirs`, and no whole-file `git checkout --ours` / `--theirs` as a
     shortcut. When keeping one side whole or regenerating the file (a
     binary, a lockfile) is the reasoned merge, do it and flag it in the
     report. Never ask whether to continue, hand over or give up.

  When you resolved something and `/omc:finish` did not invoke this skill
  with `--squash` (standalone, or invoked by `/omc:start`), run `/omc:check`
  before returning; a clean rebase adds no check. A red check falls under
  the behavior layer's stage-gate rule, its fix-forward confined to
  repairing the merge — the conflicted paths and what the merge broke —
  never new feature work. This gate is check-only: each fix-and-rerun cycle
  re-runs `/omc:check` alone, never `/omc:build` or `/omc:verify`. Commit
  only the merge-repair paths on the branch (`git commit -- <paths>`), so
  you return a clean tree; a staged stash-pop resolution (rc 2, no
  operation in progress) stays staged — it is the user's work. The only
  stop after a conflict is that rule's existing CRITICAL question.
- **rc 2** (no verdict) — a rebase, merge, cherry-pick or revert is
  already in progress (even with its conflicts resolved), or unmerged paths
  remain; nothing was fetched or changed. (In the primary, the no-op check
  runs first: a paused rebase is a detached HEAD there and gets the `note`
  verdict instead.) Finish whatever `git status` shows by the same rule —
  both intents, no markers, then that operation's own `--continue`, with
  `GIT_EDITOR=true` (`git rebase --continue` / `git merge --continue`; a
  cherry-pick or revert takes its own `--continue`) — then re-run the
  command with the same arguments and report it like any other resolution,
  the `/omc:check` above included. Unmerged paths with no operation in
  progress (e.g. a conflicted `git stash pop`) are resolved by the same
  rule and `git add`ed — there is no `--continue` to run — then the command
  is re-run. Outside `--squash` that re-run ends at rc 1 like any dirty
  tree (git refuses the rebase), leaving the resolved changes staged;
  report that. Under `/omc:finish`'s `--squash` the re-run folds them. A
  conflicted `git stash pop` keeps its stash entry, and it is left in place
  — never drop or pop it (the stash stack is shared across worktrees and
  sessions); name it (its `git stash list` line) in the report. This is not
  a question either.
- **rc 1** (no verdict) — the fetch failed, git refused the rebase before
  starting (e.g. uncommitted changes without `--squash`), or the squash
  failed (e.g. a hook rejected the squash commit: the `error:` line names
  the pre-squash tip and the original commits were restored, uncommitted
  changes left staged; if even that restore failed, stderr gives the
  `git reset --soft <old>` that restores them). Relay git's message from
  stderr; nothing was left paused.

Every `OMC_REBASE_MAIN` payload carries `"knowledge": {…}` (the primary's
freshness verdict, same schema as `OMC_KNOWLEDGE`). When `knowledge.fresh` is
false, relay its `reasons` and `fix` — the primary graph may be stale.

## When to use

- Anytime the base branch has moved and you want current code in this
  worktree. `/omc:explain` queries the primary graph; this command does not
  copy or refresh it.
- `/omc:finish` invokes this as its FIRST working step, with `--squash`.
