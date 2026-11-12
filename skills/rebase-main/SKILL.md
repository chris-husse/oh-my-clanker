---
name: rebase-main
description: Refresh this worktree onto the latest base branch AND re-mirror .omc/docs from the primary checkout. The .gitnexus graph stays primary. Run anytime; standalone it keeps your commits. /omc:finish runs it first, with --squash.
---

# omc rebase-main

A worktree is a snapshot of main at cut time — code and generated knowledge
docs (`.omc/docs/`). The `.gitnexus/` graph stays in the primary checkout,
where `/omc:explain` queries it. This refreshes code and docs via the
deterministic Python subcommand (an LLM is never trusted with
delete-semantics mirroring):

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

- `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...],
  "shared": [...]}` — report what moved and whether `.omc/docs` was
  re-mirrored, then continue. `synced` and `shared` never list `.gitnexus`.
  An entry under `shared` was already present via a shared `.omc` (symlinked
  across checkouts) — nothing was copied,
  and that is success, not a warning. With `--squash` the verdict also
  carries `"squashed": <n>` — the branch commits folded (0 when there was
  nothing to squash, or only uncommitted changes were folded) — and `<old>`
  is the pre-squash tip.
- `OMC_REBASE_MAIN {"ok": true, ..., "note": "primary checkout — nothing to
  rebase"}` — you're in the main checkout; `omc watch` owns freshness here.
  The primary keeps this no-op unless `/omc:finish` passes `--squash` on a
  feature branch there (a plain clone): then it squashes and rebases but
  mirrors nothing — the normal verdict with `"squashed"`, `synced` and
  `shared` empty. A detached HEAD there — a rebase still paused in the
  primary included — gets this no-op too, never rc 2.
- **rc 3 (bail)** with `{"ok": false, "conflicts": [...]}` — the rebase hit
  conflicts and is LEFT PAUSED. Show the conflicted files and help the user
  resolve them (`git status`, resolve, `git add`, `git rebase --continue` —
  or `git rebase --abort` if they choose to back out; after `--squash`,
  abort lands on the single `wip:` commit, and the stderr line
  `pre-squash tip <old> (undo: git reset --hard <old>)` is the way back to
  the original commits; when uncommitted changes were folded, that reset
  drops them from the tree, and the line adds
  `folded uncommitted changes stay in <wip>` — the squash commit that keeps
  them). Never resolve conflicts silently, never abort on
  their behalf. After the rebase completes, re-run it with the same
  arguments so the snapshot mirror still happens (with `--squash` the
  re-run is idempotent).
- **rc 2** (no verdict) — a rebase, merge, cherry-pick or revert is
  already in progress (even with its conflicts resolved), or unmerged paths
  remain; nothing was fetched or changed. (In the primary, the no-op check
  runs first: a paused rebase is a detached HEAD there and gets the `note`
  verdict instead.) Relay the stderr line and stop:
  the user finishes it (`git rebase --continue` / `git merge --continue`)
  or backs out (`git rebase --abort` / `git merge --abort`) first — a
  cherry-pick or revert takes its own `--continue` / `--abort`.
- **rc 1** (no verdict) — the fetch failed, git refused the rebase before
  starting (e.g. uncommitted changes without `--squash`), or the squash
  failed (e.g. a hook rejected the squash commit: the `error:` line names
  the pre-squash tip and the original commits were restored, uncommitted
  changes left staged; if even that restore failed, stderr gives the
  `git reset --soft <old>` that restores them). Relay git's message from
  stderr; nothing was left paused.

Every `OMC_REBASE_MAIN` payload carries `"knowledge": {…}` (the primary's
freshness verdict, same schema as `OMC_KNOWLEDGE`). When `knowledge.fresh` is
false, relay its `reasons` and `fix` — the mirrored docs may be stale, and
the primary graph may also be stale.

## When to use

- Anytime the base branch has moved and you want current code and generated
  docs in this worktree. `/omc:explain` queries the primary graph; this
  command does not copy or refresh it.
- `/omc:finish` invokes this as its FIRST working step, with `--squash`.
