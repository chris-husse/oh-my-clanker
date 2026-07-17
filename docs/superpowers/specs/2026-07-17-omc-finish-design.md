# `/omc:finish` + the internal skill layer — design

Port of the chicken's `omk-end-work`, approved 2026-07-17. Finish the current
feature branch: squash to one commit whose message IS the MR description, push,
then offer follow-ups. No forge API is ever called — the user creates the MR/PR
themselves (forges auto-fill it from the squashed commit).

## Skill layering (new convention)

`skills/` now contains **internal skills**: composed by user-facing skills,
marked `Internal — used by /omc:finish; not meant for direct invocation` in
their frontmatter description, and absent from README's user-facing docs.
Layout stays flat (`skills/<name>/SKILL.md`) for plugin-manifest compatibility.

| Skill | Layer | Does |
|---|---|---|
| `finish` | user-facing | gate → floor → rebase → squash → delegate to create-mr → follow-up offers |
| `create-mr` | internal | get-mr-description → amend squash commit → `push --force-with-lease` → convenience URL |
| `get-mr-description` | internal | `origin/<base>..HEAD` diff+log → markdown: ≤72-char imperative title, blank line, What/Why/Notes body |

## `/omc:finish` flow

1. **Gate**: git repo, on a feature branch (not `<base>`, not detached). Base =
   omc config `worktree.base_branch` when readable, else the repo default.
2. **Floor**: `git merge-base origin/<base> HEAD`; zero own commits → "nothing
   to finish", stop. Stacked branches are out of scope: if HEAD builds on
   another branch's unmerged tip, say "finish the parent branch first", stop.
3. **Rebase**: `git fetch origin <base>` → `git rebase origin/<base>`.
   Conflicts → stop with the rebase paused, list conflicted files.
   Uncommitted changes are folded into the squash (stated to the user).
4. **Squash**: `git reset --soft origin/<base>` → one commit, temp message.
5. **Delegate**: `/omc:create-mr`.
6. **Follow-ups** (interactive; headless runs list them and end):
   [a] close worktree — `wt remove` for this branch from the primary checkout
   (`wt remove` deletes the branch only once merged, so the pushed branch
   survives until the MR lands), then move the session out of the removed dir;
   [b] address review comments — user pastes feedback, apply changes, amend
   into the single commit, re-run `create-mr` (refreshed description, re-push);
   [c] chat about this — open discussion.

## Deliberately dropped from the chicken

`.omk` stage-runner gates (no stage system in omc), `omk internal` bail
helpers (plain git in prose), stacked-branch floor arithmetic, GitLab-only MR
URL builder (replaced by best-effort GitHub/GitLab convenience URL, never an
API call).

## Testing

- Manifest/frontmatter unit tests cover the 5-skill catalog and the finish
  contract (squash, force-with-lease, create-mr delegation, follow-up offers).
- Hermetic live E2E (claude, token-gated): work repo with bare origin, feature
  branch with 2 commits, headless `/omc:finish`; asserts origin's branch is
  exactly ONE commit ahead of main with a title+body message; judge-scored
  transcript (also covers the no-forge fallback path).
