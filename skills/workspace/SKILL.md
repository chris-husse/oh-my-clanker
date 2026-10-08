---
name: workspace
description: Use when the user wants to list a multi-repository workspace's branches and compare URLs, or close its worktrees after merging.
---

# omc workspace

User entry point: `/omc:workspace list|close` (`$omc:workspace` on Codex).
The CLI owns the ledger and removal checks; this skill renders its verdicts.

## User Input

```text
$ARGUMENTS
```

Accept `list` or `close`. Empty input means `list`; other input gets the usage
`/omc:workspace list|close`. Never edit `~/.omc/workspaces.json` by hand.

## List

Run `omc internal workspace list` in the current worktree. Consume its one
single-line `OMC_WORKSPACE` JSON verdict; a missing/malformed verdict or
`ok: false` is a failure: report the message and stop.

For non-empty `repositories`, render one row per entry in returned order
(dependencies first, master last):

| Column | Verdict field |
| --- | --- |
| Role | `role` |
| Branch | `branch` |
| Worktree | `worktree` |
| Record status | `record`: committed when `ok: true`; otherwise `reason` and `message` |
| Ahead | `ahead` |
| Behind | `behind` |
| Compare URL | `compare_url` |

Render null counts as **unknown**, never zero; null compare URLs as unavailable.
An empty list means no registered workspace for this worktree: say so without
creating one. Do not use a forge API or claim a compare URL proves an MR exists.

## Close

First run the List step to retain `current_role`, `master_worktree` and the
master's `primary` path before removal. An empty workspace has nothing to close.
Run `omc internal workspace close` from the current worktree and consume its
single `OMC_WORKSPACE` verdict. Do not bypass the CLI's merge or role checks:

- `reason: unmerged` → report the identifying `repository` key and `branch`
  plus the message, and stop. The remaining entries are preserved; retry
  close after that branch is merged to resume the chain.
- `reason: not-master` → report the current repository and the master_worktree
  from List, tell the user to run close there, and stop. Never automatically
  relocate to the master to bypass a refusal from a dependency.
- Any other failure, missing or malformed verdict → report it and stop;
  do not attempt manual removal or ledger repair.
- `ok: true` → report `removed` and remaining `repositories`. Move the session
  out of the removed directory into the saved master primary checkout.

The CLI removes merged dependencies in registration order, then the master.
A partial failure is resumable; never say the workspace is closed on failure.
