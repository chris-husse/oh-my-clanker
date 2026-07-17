---
name: squash
description: Internal — used by /omc:finish; not meant for direct invocation. Squash the current feature branch to one commit over origin/<base>, folding in uncommitted changes.
---

# omc squash (internal)

Precondition: the branch has already been rebased onto `origin/<base>` (that
is `/omc:finish`'s job); `origin/<base>..HEAD` is exactly the work to squash.

## Inputs

- **base** — the base branch (default: omc config `worktree.base_branch`,
  else the repo default).

## Steps

1. Count what's being squashed: `git rev-list --count origin/<base>..HEAD`,
   plus whether the working tree is dirty (`git status --porcelain`).
2. Uncommitted changes exist → tell the user they'll be folded into the
   squash, then stage them (`git add -A`).
3. `git reset --soft origin/<base>`, then ONE commit with a temporary message
   (e.g. `wip: squash of <branch>`). The real message is amended later by
   `create-mr`.
4. End with exactly one machine-readable line (plain text, no backticks):

   `OMC_SQUASH {"ok": true, "commits_folded": <N>}`

   On any failure (nothing to squash, reset error): `OMC_SQUASH {"ok": false,
   "message": "<why>"}` — and leave the repo in the safest state you can.
