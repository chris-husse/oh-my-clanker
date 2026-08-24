---
name: start
description: Session-side half of `omc start` - gather ticket context, verify base freshness, and hand off to omc:plan. Seeded automatically by the omc CLI; invoked cold it redirects to the shell command.
---

# omc start (session side)

## User Input

```text
$ARGUMENTS
```

`$ARGUMENTS` is the work context: a ticket key (e.g. `PROJ-123`), a ticket URL,
or a free-text task description.

## Step 0 — which path am I on?

Check the environment variable `OMC_SLUG` (e.g. `echo "$OMC_SLUG"`) and the
current branch (`git rev-parse --abbrev-ref HEAD`).

- **Prepared path**: `OMC_SLUG` is set to a non-empty value and the current branch ends with it —
  the omc CLI created this worktree and seeded this session. Continue below.
- **Cold path**: otherwise. STOP and tell the user: work starts from the shell
  with `omc start <ticket-or-description>` — the CLI names the session, sets
  the tab title, and creates the worktree, none of which a skill can do from
  inside a session. Do not continue.

## Step 0.5 — externalize the flow (before Step 1, no exceptions)

On the prepared path, **write the remaining steps into the task list now**, as
your next action, before gathering any context:

1. Gather ticket context and pass the context gate (Step 2)
2. Claim the ticket via ticket-sync (Step 2.5)
3. Base freshness gate — fetch + rebase onto `origin/<base>` (Step 3)
4. Summarize and hand off to `omc:plan` (Step 4)

Mark each completed as you pass it. This is not bookkeeping. Steps 2.5 and 4
invoke *other skills*, whose bodies arrive as fresh instruction blocks that read
like new user requests and push this one out of view; the task list is the only
representation of "what still has to happen" that survives that. Skipping this
step is the known, repeated cause of `/omc:start` stopping dead after
ticket-sync and never reaching `omc:plan`.

## Step 1 — superpowers present?

Verify the superpowers plugin is available (its skills, e.g.
`superpowers:brainstorming`, are listed/loadable). If not: STOP and point the
user at https://github.com/obra/superpowers for this harness's install steps.

## Step 2 — gather context

If `$ARGUMENTS` contains a ticket key or URL, fetch it with whatever configured
read tool the session has (Jira MCP, GitHub/GitLab MCP or CLI, …):

- The ticket itself: title, description, status, type.
- Its surroundings where the tracker exposes them: parent/epic, linked issues.
- Linked documents: summarize each (title + a few sentences + link). A doc that
  cannot be fetched is listed with "couldn't fetch — <reason>"; never hard-fail
  on a document. Step 2 is read-only — ticket writes happen only via ticket-sync (Step 2.5).

If `$ARGUMENTS` is a free-text description, it IS the context.

**Context gate**: is there a clear problem + goal, specific enough to
brainstorm from? If not, tell the user exactly what's missing and ask them to
improve the ticket (or paste the missing context). Re-check when they say it's
done. Loop until it passes or they exit.

## Step 2.5 — claim the ticket (ticket-sync)

Only when the work context is a ticket key or URL (free-text work skips this
step entirely): invoke the internal **`ticket-sync`** skill with phase
`start` and the ticket reference. It assigns the ticket (asking first when
someone else holds it) and moves it to an "In Progress"-equivalent status.

ticket-sync finishes by emitting an `OMC_TICKET {…}` verdict line. **That line
is an argument to YOU, not the end of your turn** — the start flow is only half
done when it appears. Read it, apply the branching below, and continue. Whatever
the verdict says, your very next action after it is a tool call: mark task 2
complete and start Step 3. Ending the turn on the verdict is the failure mode
this flow is most prone to.

- Verdict reason `user-declined` → STOP the start flow: the user chose not
  to take over someone else's ticket.
- Any other failure (`mcp-missing`, `mcp-unauthenticated`,
  `no-matching-transition`, `assigned-elsewhere`, …) → report the one-line
  message so the user knows the ticket was NOT moved, and continue — ticket
  sync is best-effort, never a gate.
- Success (`ok: true`) → note it in one line and continue.

In every case except `user-declined`, proceed to Step 3 in the same turn.

## Step 3 — base freshness gate (HARD REQUIREMENT)

Determine the base branch: `git remote show origin` HEAD branch, or the repo's
default. Then:

1. `git fetch origin <base>` — always, first.
2. `git merge-base --is-ancestor origin/<base> HEAD`:
   - ancestor → report "branch is on current origin/<base>", continue.
   - not an ancestor → `git rebase origin/<base>`. Dirty tree or conflicts →
     STOP and surface; never force it. Never brainstorm on a stale base.

## Step 4 — hand off to plan

1. Print a compact summary: ticket (key, title, 2–3 sentences),
   surroundings, doc list, and the workspace (branch + worktree path).
2. Invoke the `omc:plan` skill with the gathered context recap. `plan`
   runs the explain pass, asks the user for their seed, and starts the
   primed brainstorm.

This skill prepares and hands off — it never designs or writes code itself.

## Completion contract

`/omc:start` is complete only when `omc:plan` has actually been invoked. Before
you end the turn, check the task list: if any of the four steps is still
pending, you are not done — continue with the first pending one instead of
stopping. Handing off is the deliverable; a claimed ticket on a fresh branch
with no brainstorm started is a failed run, not a partial success.
