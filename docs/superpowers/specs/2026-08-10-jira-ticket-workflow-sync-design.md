# JIRA Ticket Workflow Sync — Design

**Date:** 2026-08-10
**Slug:** `jira-ticket-workflow-sync`
**Status:** approved (brainstorm converged 2026-08-10)

## Problem & scope

omc reads tickets but never reflects work state back into JIRA. Starting work
(`omc start PROJ-123`) should claim the ticket — assign it and move it to an
"In Progress"-equivalent status. Publishing the MR (`omc finish`) should move
it to an "In Review"-equivalent status. omc never calls a forge API, so "MR
published" is proxied by create-mr's observable moment: branch pushed +
compare URL printed.

Three skills currently pledge "never write to the tracker"
(`skills/slug/SKILL.md:24`, `skills/start/SKILL.md:44`,
`skills/plan/SKILL.md:74-75`). This design amends that stance deliberately:
all tracker writes in the codebase are confined to one new internal skill.

Chosen approach (from three considered): a new internal `ticket-sync` skill
invoked session-side as a black box with a phase argument — zero Python
changes. Rejected: inlining the steps into `start`/`finish` prose (duplicated
tool discovery, status matching, and failure taxonomy that would drift), and
CLI-side headless sync in Python (confirmation can't be interactive in a
headless LLM run; violates "skill does the thinking, code does the plumbing";
doesn't help `finish`, which is already session-side).

## The `ticket-sync` skill (`skills/ticket-sync/SKILL.md`, internal)

**Input:** a phase — `start` or `review` — plus the ticket reference (key or
URL) when the caller has one. Frontmatter description follows the
internal-skill convention: "Internal — used by /omc:start and /omc:finish;
not meant for direct invocation."

### Shared preamble (both phases)

1. **Resolve the ticket key.** The caller passes it when known; the `review`
   phase derives it from the branch name: strip the configured branch prefix
   (`worktree.branch_prefix`, default `feature/`), then match the slug's
   leading `<project>-<number>` pattern (e.g. `proj-123-fix-login` →
   `PROJ-123`); `$OMC_SLUG` serves the same purpose when set. No key
   derivable → emit a skipped verdict (`no-ticket`) and end. Free-text work
   never touches the tracker.
2. **Find a tracker tool that can write** — the Jira/Atlassian MCP family,
   the same family the slug skill probes for reads. Missing → `mcp-missing`;
   auth failures → `mcp-unauthenticated` — the same typed-reason vocabulary
   as `OMC_SLUG`, each with one actionable sentence. All failures are
   non-fatal to the caller.

### Phase `start`

3. Fetch the ticket's current assignee and the current MCP user's identity
   (e.g. Atlassian `atlassianUserInfo`). The authenticated MCP user IS the
   current user — no omc-side identity config.
4. Branch on assignee:
   - **Unassigned** → assign to the current user (`assigned: "self"`).
   - **Already the current user** → nothing to assign (idempotent re-entry,
     matching `omc start`'s idempotent worktree re-entry).
   - **Another user** → three-option dialog (a genuine standalone fork, so a
     question dialog is appropriate):
     1. **Abort** — stop the whole start flow (`user-declined`); no writes.
     2. **Reassign to me** — assign to the current user, then transition
        (`assigned: "taken-over"`).
     3. **Continue without reassigning** — leave the assignee untouched,
        still transition (`assigned: "kept"`).
   - **Non-interactive/headless runs** skip the sync entirely for the
     assigned-to-other case (no assign, no transition — nobody is there to
     pick), note it, and let the flow continue — verdict reason
     `assigned-elsewhere`. This follows the established headless stance of
     `skills/integrate/SKILL.md:25` ("Non-interactive/headless runs: propose
     only — zero writes").
5. **Transition to "In Progress"-equivalent.** List the ticket's available
   transitions and pick by judgment: exact `In Progress` first, then close
   synonyms (`In Development`, `Doing`, `Started`, `Pending`). Already in
   such a status → no-op. No plausible match → `no-matching-transition`,
   skip. No config mapping in v1 — a per-project override would require a
   Python config-schema change and is deferred until a real workflow defeats
   name matching.

### Phase `review`

3. Transition to "In Review"-equivalent (`In Review`, `Code Review`,
   `Review`, `Awaiting Review`, …), same matching rules. No assignment
   changes in this phase. Re-runs (the finish → address-review-comments →
   re-push loop) find the ticket already in review and no-op.

### Verdict (machine contract, single line, last line)

Same convention as `OMC_SLUG` / `OMC_STAGE` / `OMC_SQUASH`:

```
OMC_TICKET {"ok": true, "phase": "start", "key": "PROJ-123", "assigned": "self" | "kept" | "taken-over" | null, "transitioned": "In Progress" | null}
OMC_TICKET {"ok": false, "phase": "...", "key": "...", "reason": "mcp-missing" | "mcp-unauthenticated" | "ticket-not-found" | "no-matching-transition" | "user-declined" | "assigned-elsewhere" | "no-ticket", "message": "<one actionable sentence>"}
```

## Caller wiring & pledge amendments

- **`skills/start/SKILL.md`** — new Step 2.5, after the context gate passes
  and before the base-freshness gate: invoke `ticket-sync` phase `start` with
  the ticket key (skipped entirely for free-text contexts). `user-declined` →
  stop the start flow; every other failure → report and continue. Placement
  rationale: fail the "someone else owns this" check before investing in
  rebase + brainstorm, and never claim tickets for work whose context gate
  failed.
- **`skills/finish/SKILL.md`** — Step 5 gains a trailing action: after
  `create-mr` reports a successful push, invoke `ticket-sync` phase `review`.
  The In Review transition fires on push (omc's MR-published proxy); no
  MR-exists confirmation — a pushed branch without an MR is transient. The
  Step 6 "address review comments" loop re-runs `create-mr` directly and
  deliberately does NOT re-invoke ticket-sync — the ticket is already in
  review, and a re-invocation would no-op anyway.
- **Pledge amendments:** `slug` stays fully read-only (naming only); `start`
  Step 2's line becomes "read-only during context gathering — ticket writes
  happen only via ticket-sync (Step 2.5)"; `plan` stays read-only.

## Failure policy

Tracker sync is best-effort, never load-bearing: a Jira outage, missing MCP,
expired auth, or unmappable workflow must never block starting or finishing
work. The single deliberate exception is `user-declined` in interactive
start — the user choosing to stop, not a failure. Every skip/failure is
surfaced in the caller's report so the user knows the ticket was NOT moved
and can move it by hand.

## Testing

- **Stub extensions** (`docker/stub-jira-mcp/server.py` + `tickets.json`):
  new tools `getCurrentUser` (fixed stub identity), `assignIssue`,
  `listTransitions`, `transitionIssue`. Fixture tickets gain `assignee`
  fields (one unassigned, one assigned to the stub user, one assigned to
  another user) and small per-ticket transition sets. Mutations append to a
  JSONL file at `$STUB_JIRA_MUTATIONS_LOG` so the hermetic harness asserts
  writes without talking to the stub. Existing `STUB_JIRA_MODE=auth-error`
  covers the `mcp-unauthenticated` path for writes too.
- **E2E** (Docker-per-test, `tests/e2e/`): start-on-unassigned → assert
  assign + transition mutations and an `ok: true` verdict;
  start-on-other-assignee headless → assert skip verdict and the flow
  continues; finish → assert In Review transition; auth-error → assert
  `mcp-unauthenticated` and the flow continues; free-text start → assert no
  tracker calls.
- **Permissions note (decide in plan):** headless seeded sessions currently
  get no `--allowed-tools` (`src/omc/start.py:_run_headless`, unlike
  `fetch_slug`, which passes `MCP_TOOL_PATTERNS`). In-session tracker writes
  in headless E2E therefore need either `_run_headless` to pass
  `MCP_TOOL_PATTERNS` too (a small Python change, consistent with the slug
  path) or container-side permission config in the harness. This is the one
  place "zero Python changes" may bend — runtime behavior is unaffected
  either way for interactive sessions, which use the user's own permission
  flow.
- **Unit:** none — no runtime Python changes in this design (see the
  permissions note for the possible one-line exception).

## Delivery note

Skills are auto-discovered from `skills/` (no per-skill registration in
`.claude-plugin/plugin.json`), but the plugin version is pinned at 0.1.0 and
harnesses cache the plugin snapshot — delivering the new `ticket-sync` skill
to an installed harness requires a plugin refresh (uninstall + install).

## Out of scope (YAGNI)

Per-project status-mapping config; moving tickets on MR merge (omc never
sees the merge); un-assigning or reverting status on abandoned work;
GitHub/GitLab/Linear issue sync (the skill's wording is tracker-agnostic,
but only Jira gets fixtures and testing in v1).
