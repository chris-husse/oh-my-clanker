---
name: ticket-sync
description: Internal — used by /omc:start and /omc:finish; not meant for direct invocation. The ONLY skill that writes to the ticket tracker - assign on work start, transition between status columns on start and review. Best-effort by design; never blocks the caller except an explicit user decline.
---

# omc ticket-sync (internal)

Reflect work state into the ticket tracker. Input: a **phase** — `start` or
`review` — plus the ticket reference (key or URL) when the caller has one.

You MUST end your reply with exactly one `OMC_TICKET` verdict line (format at
the bottom) — it is parsed by machines and by the caller. No text after it.
Plain text, never wrapped in backticks or a code fence.

## Shared preamble (both phases)

1. **Resolve the ticket key.** Use the caller-provided reference when given.
   Otherwise derive it from the branch: `git rev-parse --abbrev-ref HEAD`,
   strip the project's branch prefix (`worktree.branch_prefix` from
   `.omc/config.yaml`, default `feature/`), then match a leading
   `<letters>-<digits>` pair (e.g. `proj-123-fix-login` → `PROJ-123`).
   `$OMC_SLUG` serves the same purpose when set. No key derivable → this work
   has no ticket; verdict `{"ok": false, "reason": "no-ticket", ...}` and end.
   Free-text work never touches the tracker.
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
verdict and end quietly. Never retry a failing write more than once.

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
     verdict and end.
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

## Verdict (REQUIRED, last line, exactly one)

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
