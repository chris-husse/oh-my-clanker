---
name: ticket-sync
description: Internal — used by /omc:start and /omc:finish; not meant for direct invocation. The ONLY skill that writes to the ticket tracker - assign on work start, transition between status columns on start and review. Best-effort by design; never blocks the caller except an explicit user decline.
---

# omc ticket-sync (internal)

Reflect work state into the ticket tracker. Input: a **phase** — `start` or
`review` — plus the ticket reference (key or URL) when the caller has one.

## The output contract (read this before anything else)

ticket-sync emits exactly one `OMC_TICKET` verdict line (format at the bottom):
plain text, never wrapped in backticks or a code fence, and the last *text*
ticket-sync itself writes.

**The verdict is not the end of your turn** — "last text of ticket-sync" and
"last thing in the turn" are different things. ticket-sync is never run on its
own — a caller (`/omc:start`, `/omc:finish`) always composes it
inline, in the caller's own turn. The verdict is a value handed BACK to that
caller, which then resumes its remaining steps. Below, "and end", "end quietly"
and "emit the verdict and end" all mean *stop ticket-sync and return to the
caller* — never *stop the turn*.

So the contract has two halves, and the second is the one that gets dropped:

1. Emit the verdict line as ticket-sync's final text.
2. **Then immediately make a tool call — in this same turn — continuing the
   caller's flow** (see "Final step" below). The verdict is an argument to the
   next step, not a deliverable.

A turn that ends on the `OMC_TICKET` line with no tool call after it is a
FAILED run: the caller's remaining steps silently never happen, and from the
user's side an unfinished flow is indistinguishable from a broken one. This is
the single most common way this skill goes wrong — it has happened repeatedly.
Prose reminders were tried and did not hold; the *mechanical* rule below is
what you follow.

## Shared preamble (both phases)

1. **Resolve the ticket key.** Use the caller-provided reference when given.
   Otherwise derive it from the branch: `git rev-parse --abbrev-ref HEAD`,
   strip the project's branch prefix (`worktree.branch_prefix` from
   `.omc/config.yaml`, default `feature/`), then match a leading
   `<letters>-<digits>` pair (e.g. `proj-123-fix-login` → `PROJ-123`).
   `$OMC_SLUG` serves the same purpose when set. No key derivable → this work
   has no ticket; verdict `{"ok": false, "reason": "no-ticket", ...}` and
   return to the caller. Free-text work never touches the tracker.
2. **Find a tracker tool that can write** — a Jira/Atlassian MCP server or
   similar (the same family the slug skill probes for reads), exposing
   assign/transition-style tools. Judge by the tools actually available;
   names vary by server (e.g. `editJiraIssue`/`transitionJiraIssue` on
   Atlassian's, `assignIssue`/`transitionIssue` elsewhere).
   - None available → `mcp-missing`; message names what to configure.
   - Calls fail with authentication/authorization errors →
     `mcp-unauthenticated`; message gives the exact re-auth step.
   - Ticket not found / not readable → `ticket-not-found`.

Failures here are the CALLER's to report, not yours to escalate: emit the
verdict quietly and return to the caller (see "Final step"). Never retry a
failing write more than once.

## Phase `start`

3. Fetch the ticket's current assignee and the current user's identity from
   the tracker tool (e.g. `atlassianUserInfo`, `getCurrentUser`). The
   authenticated MCP user IS the current user — never guess identity from
   git config or the environment.
4. Branch on assignee:
   - **Unassigned** → assign to the current user; `assigned: "self"`.
   - **Already the current user** → nothing to assign; `assigned: "self"`.
   - **Another user** → first decide whether you can actually ask. You may ask
     ONLY if a real interactive question tool (e.g. `AskUserQuestion`) is
     available to you in this session. Writing the options out as prose is NOT
     asking: in a headless run (`omc start --headless`, any `-p`/`exec`
     invocation) nobody will ever answer, so a prose "question" ends the turn
     with no verdict and the caller learns nothing. When in doubt, treat the
     session as non-interactive.
   - **Another user, interactive session** (question tool available) → ask with
     a question dialog, exactly three options:
     1. **Abort** — no writes; verdict `{"ok": false, "reason":
        "user-declined", ...}`. The caller stops its flow on this reason.
     2. **Reassign to me** — assign to the current user; `assigned:
        "taken-over"`; continue to step 5.
     3. **Continue without reassigning** — leave the assignee untouched;
        `assigned: "kept"`; continue to step 5.
   - **Another user, non-interactive/headless run** (no question tool) → zero
     writes (no assign, no transition — nobody is there to pick); verdict
     `{"ok": false, "reason": "assigned-elsewhere", ...}`; the caller
     notes it and continues. Do NOT print the three options instead — emit the
     verdict and return to the caller.
5. **Transition to "In Progress"-equivalent.** List the ticket's available
   transitions and pick by judgment: exact `In Progress` first, then close
   synonyms (`In Development`, `Doing`, `Started`, `Pending`). Ticket already
   in such a status → no transition needed (`transitioned: null`, still
   `ok: true`). No plausible match among the available transitions →
   `no-matching-transition`, skip (any assignment from step 4 stands and is
   reported in the verdict's `assigned` field).

## Phase `review`

3. **Transition to "In Review"-equivalent** (`In Review`, `Code Review`,
   `Review`, `Awaiting Review`), same matching rules as phase `start` step 5.
   No assignment changes in this phase, ever. Already in review → no-op
   verdict (`ok: true, transitioned: null`).

## Final step (BOTH phases, every outcome) — return to the caller

This step is not optional and has no exceptions: it runs on success, on every
failure reason, and on `user-declined`.

After writing the verdict line, **your very next action is a tool call, in this
same turn.** Concretely, in order of preference:

1. `TaskUpdate` — mark the caller's ticket-sync task completed. The caller
   created a task list before invoking you, so this is the normal path; the
   list then shows you the caller's next pending step.
2. No task list exists (headless, or a caller that skipped it) → make the first
   tool call of the caller's next step directly. For `/omc:start` that is Step 3
   (`git fetch origin <base>`); for `/omc:finish` it is Step 6.

Do not ask the user whether to continue — the caller's flow is already
authorized, and the verdict is not a checkpoint. Then carry on with the
caller's remaining steps until the caller's own completion contract is met.

## Verdict format (last line of ticket-sync's own text, exactly one)

Success: `OMC_TICKET {"ok": true, "phase": "start", "key": "PROJ-123", "assigned": "self" | "kept" | "taken-over" | null, "transitioned": "In Progress" | null}`

Failure: `OMC_TICKET {"ok": false, "phase": "start" | "review", "key": "PROJ-123" | null, "reason": "mcp-missing" | "mcp-unauthenticated" | "ticket-not-found" | "no-matching-transition" | "user-declined" | "assigned-elsewhere" | "no-ticket", "message": "<one actionable sentence>"}`

`assigned` in the success verdict: `"self"` (was unassigned or already
yours), `"taken-over"` (reassigned after confirmation), `"kept"` (left with
the other user), `null` (phase `review`).

The failure verdict carries no `assigned` key except in one case: phase
`start` failing with `reason: "no-matching-transition"` MAY additionally
include `"assigned": "self" | "taken-over" | "kept"` — same values as above,
omitted rather than `null` — reporting the assignment from step 4 that
already stands even though no transition was found.
