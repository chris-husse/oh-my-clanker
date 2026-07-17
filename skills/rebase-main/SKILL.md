---
name: rebase-main
description: Refresh this worktree onto the latest base branch AND re-mirror the knowledge snapshot (.gitnexus, .omc/docs) from the primary checkout. Run anytime; /omc:finish runs it first automatically.
---

# omc rebase-main

A worktree is a snapshot of main at cut time — code AND knowledge. This
refreshes both, via the deterministic Python subcommand (an LLM is never
trusted with delete-semantics mirroring):

```sh
omc internal rebase-main
```

(`--base <branch>` overrides the configured base.)

## Interpreting the outcome

The last line is machine-readable:

- `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...]}` —
  report what moved and which snapshot dirs were re-mirrored, then continue.
- `OMC_REBASE_MAIN {"ok": true, ..., "note": "primary checkout — nothing to
  rebase"}` — you're in the main checkout; `omc watch` owns freshness here.
- **rc 3 (bail)** with `{"ok": false, "conflicts": [...]}` — the rebase hit
  conflicts and is LEFT PAUSED. Show the conflicted files and help the user
  resolve them (`git status`, resolve, `git add`, `git rebase --continue` —
  or `git rebase --abort` if they choose to back out). Never resolve
  conflicts silently, never abort on their behalf. After the rebase
  completes, run `omc internal rebase-main` again so the snapshot mirror
  still happens.

## When to use

- Anytime the base branch has moved and you want current code + a current
  `/omc:explain` graph in this worktree.
- `/omc:finish` invokes this as its FIRST working step.
