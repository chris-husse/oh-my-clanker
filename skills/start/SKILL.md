---
name: start
description: Session-side half of `omc start` - gather ticket context, verify base freshness, and hand off to superpowers:brainstorming. Seeded automatically by the omc CLI; invoked cold it redirects to the shell command.
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

- **Prepared path**: `OMC_SLUG` is set and the current branch ends with it —
  the omc CLI created this worktree and seeded this session. Continue below.
- **Cold path**: otherwise. STOP and tell the user: work starts from the shell
  with `omc start <ticket-or-description>` — the CLI names the session, sets
  the tab title, and creates the worktree, none of which a skill can do from
  inside a session. Do not continue.

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
  on a document. Never write to the tracker.

If `$ARGUMENTS` is a free-text description, it IS the context.

**Context gate**: is there a clear problem + goal, specific enough to
brainstorm from? If not, tell the user exactly what's missing and ask them to
improve the ticket (or paste the missing context). Re-check when they say it's
done. Loop until it passes or they exit.

## Step 3 — base freshness gate (HARD REQUIREMENT)

Determine the base branch: `git remote show origin` HEAD branch, or the repo's
default. Then:

1. `git fetch origin <base>` — always, first.
2. `git merge-base --is-ancestor origin/<base> HEAD`:
   - ancestor → report "branch is on current origin/<base>", continue.
   - not an ancestor → `git rebase origin/<base>`. Dirty tree or conflicts →
     STOP and surface; never force it. Never brainstorm on a stale base.

## Step 4 — hand off to brainstorming

1. Print a compact summary: ticket (key, title, 2–3 sentences), surroundings,
   doc list, and the workspace (branch + worktree path).
2. Ask the user for their initial thinking / seed for this work.
3. Invoke `superpowers:brainstorming` with: the user's seed, the gathered
   context recap, and this doc-naming directive: "Use the topic slug
   `$OMC_SLUG` so the design doc lands at
   `docs/superpowers/specs/YYYY-MM-DD-$OMC_SLUG-design.md` and the plan at
   `docs/superpowers/plans/YYYY-MM-DD-$OMC_SLUG-plan.md`."

This skill prepares and hands off — it never designs or writes code itself.
