# JIRA Ticket Workflow Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** omc reflects work state back into JIRA — `omc start <ticket>` assigns the ticket (with a three-option ownership dialog when someone else holds it) and moves it to "In Progress"-equivalent; `omc finish` moves it to "In Review"-equivalent after the branch push.

**Architecture:** One new internal skill, `skills/ticket-sync/SKILL.md`, performs every tracker write in the codebase, invoked as a black box with a phase argument (`start` | `review`) from `skills/start` (new Step 2.5) and `skills/finish` (Step 5 trailing action). All tracker intelligence is prose; the only Python change is `_run_headless` passing `MCP_TOOL_PATTERNS` so headless seeded sessions can reach MCP tools like the slug path already does. Testing extends the hermetic stub Jira MCP with write tools and a mutations log.

**Tech Stack:** Skill prose (SKILL.md), Python 3 stdlib (stub server), pytest (unit + Docker-per-test E2E).

**Spec:** `docs/superpowers/specs/2026-08-10-jira-ticket-workflow-sync-design.md`

## Global Constraints

- All tracker writes live in `skills/ticket-sync/SKILL.md` — no other skill or Python module may write to a tracker.
- omc never calls a forge API; "MR published" is proxied by create-mr's successful push.
- Machine verdict (single line, last line of ticket-sync output, never fenced):
  `OMC_TICKET {"ok": true, "phase": "start", "key": "PROJ-123", "assigned": "self" | "kept" | "taken-over" | null, "transitioned": "In Progress" | null}`
  `OMC_TICKET {"ok": false, "phase": "...", "key": "...", "reason": "mcp-missing" | "mcp-unauthenticated" | "ticket-not-found" | "no-matching-transition" | "user-declined" | "assigned-elsewhere" | "no-ticket", "message": "<one actionable sentence>"}`
- Ticket sync is best-effort: only `user-declined` (interactive start) stops the caller; every other failure is report-and-continue.
- Stub server stays stdlib-only, line-delimited JSON-RPC 2.0 over stdio.
- E2E stays hermetic: no real Jira; assertions go through `$STUB_JIRA_MUTATIONS_LOG` and transcript greps.
- Model-tier policy: every task carries a `Model:` line naming a tier; the cheap/fast tier is never used.

## File Structure

- `docker/stub-jira-mcp/server.py` — gains 4 write-side tools + mutations log (Task 1)
- `docker/stub-jira-mcp/tickets.json` — fixture tickets gain `assignee`; two new tickets (Task 1)
- `tests/unit/test_stub_jira_mcp.py` — tests for the new tools (Task 1)
- `skills/ticket-sync/SKILL.md` — NEW: the only tracker-writing skill (Task 2)
- `skills/start/SKILL.md` — Step 2.5 + pledge scope amendment (Task 3)
- `skills/finish/SKILL.md` — Step 5 trailing action (Task 3)
- `src/omc/start.py` — `_run_headless` passes `MCP_TOOL_PATTERNS` (Task 4)
- `tests/unit/test_start.py` — headless allowed-tools test (Task 4)
- `tests/e2e/harness.py` — `wire_mcp` gains mutations-log env (Task 5)
- `tests/e2e/test_e2e_ticket_sync.py` — NEW: E2E scenarios (Task 5)

---

### Task 1: Stub Jira MCP write tools + mutations log + fixtures

**Model:** standard coding tier

**Files:**
- Modify: `docker/stub-jira-mcp/server.py`
- Modify: `docker/stub-jira-mcp/tickets.json`
- Test: `tests/unit/test_stub_jira_mcp.py`

**Interfaces:**
- Produces MCP tools (consumed by ticket-sync prose in E2E and by Task 5 tests):
  - `getCurrentUser()` → `{"accountId": "stub-user-1", "displayName": "Stub User"}`
  - `assignIssue(key, accountId)` → assigns; `{"key": ..., "assignee": {"accountId": ...}}`
  - `listTransitions(key)` → `{"transitions": [{"id": "11", "name": "To Do"}, {"id": "21", "name": "In Progress"}, {"id": "31", "name": "In Review"}, {"id": "41", "name": "Done"}]}`
  - `transitionIssue(key, transitionId)` → sets status to the transition's name
- Produces mutations log: every successful `assignIssue`/`transitionIssue` appends one JSON line `{"tool": "assignIssue", "key": "PROJ-1", "arguments": {...}}` to the path in `$STUB_JIRA_MUTATIONS_LOG` (skipped when the env var is unset).
- Fixtures: `PROJ-1` unassigned (`"assignee": null`), `PROJ-2` assigned to `stub-user-1`, `PROJ-3` assigned to `other-user-9` ("Someone Else"). `getIssue` keeps returning `{"key", "fields"}` with `assignee` now inside `fields`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_stub_jira_mcp.py` (reuse the existing `_start`/`_rpc` helpers; `_start` gains an optional `extra_env=None` merged into the env dict):

```python
def _call(proc, name, arguments, id_=5):
    return _rpc(proc, "tools/call", {"name": name, "arguments": arguments}, id_=id_)


def test_get_current_user():
    proc = _start()
    try:
        res = _call(proc, "getCurrentUser", {})["result"]
        body = json.loads(res["content"][0]["text"])
        assert body == {"accountId": "stub-user-1", "displayName": "Stub User"}
    finally:
        proc.terminate()


def test_assign_and_transition_mutate_and_log(tmp_path):
    log = tmp_path / "mutations.jsonl"
    proc = _start(extra_env={"STUB_JIRA_MUTATIONS_LOG": str(log)})
    try:
        res = _call(proc, "assignIssue", {"key": "PROJ-1", "accountId": "stub-user-1"})["result"]
        assert not res.get("isError")
        res = _call(proc, "listTransitions", {"key": "PROJ-1"}, id_=6)["result"]
        names = [t["name"] for t in json.loads(res["content"][0]["text"])["transitions"]]
        assert "In Progress" in names and "In Review" in names
        res = _call(proc, "transitionIssue", {"key": "PROJ-1", "transitionId": "21"}, id_=7)["result"]
        assert not res.get("isError")
        res = _call(proc, "getIssue", {"key": "PROJ-1"}, id_=8)["result"]
        fields = json.loads(res["content"][0]["text"])["fields"]
        assert fields["status"] == "In Progress"
        assert fields["assignee"]["accountId"] == "stub-user-1"
        lines = [json.loads(l) for l in log.read_text().splitlines()]
        assert [l["tool"] for l in lines] == ["assignIssue", "transitionIssue"]
        assert all(l["key"] == "PROJ-1" for l in lines)
    finally:
        proc.terminate()


def test_write_tools_respect_auth_error_mode():
    proc = _start(mode="auth-error")
    try:
        res = _call(proc, "assignIssue", {"key": "PROJ-1", "accountId": "stub-user-1"})["result"]
        assert res["isError"] and "401" in res["content"][0]["text"]
    finally:
        proc.terminate()


def test_fixture_assignees():
    proc = _start()
    try:
        for key, expected in [("PROJ-1", None), ("PROJ-2", "stub-user-1"), ("PROJ-3", "other-user-9")]:
            res = _call(proc, "getIssue", {"key": key})["result"]
            assignee = json.loads(res["content"][0]["text"])["fields"]["assignee"]
            assert (assignee or {}).get("accountId") == expected or (assignee is None and expected is None)
    finally:
        proc.terminate()
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/unit/test_stub_jira_mcp.py -v`. Expected: new tests FAIL (`unknown tool 'getCurrentUser'`, missing fixtures); the 5 existing tests still pass. Adjust `_start` to accept `extra_env` first if the helper change itself breaks collection.

- [ ] **Step 3: Implement** — in `docker/stub-jira-mcp/server.py`:
  - Add `MUTATIONS_LOG = os.environ.get("STUB_JIRA_MUTATIONS_LOG", "")` next to `MODE`; add a `TRANSITIONS` constant `[{"id": "11", "name": "To Do"}, {"id": "21", "name": "In Progress"}, {"id": "31", "name": "In Review"}, {"id": "41", "name": "Done"}]`.
  - Append the 4 tool descriptors to `TOOLS` (each with an object `inputSchema`; `assignIssue` requires `key` + `accountId`, `transitionIssue` requires `key` + `transitionId`, `listTransitions` requires `key`, `getCurrentUser` requires nothing).
  - Add `def log_mutation(tool, key, arguments)` that appends the JSON line when `MUTATIONS_LOG` is set.
  - Replace the single-tool branch in `tools/call` with a dispatch over tool name; keep the auth-error short-circuit FIRST (it must cover the new tools untouched). `assignIssue` sets `TICKETS[key]["assignee"] = {"accountId": ..., "displayName": ...}`; `transitionIssue` looks up the id in `TRANSITIONS` (unknown id → `isError` "unknown transition") and sets `TICKETS[key]["status"]` to its name. Unknown key → the existing 404-flavored `isError`. Both log via `log_mutation` after mutating.
  - `tickets.json`: add `"assignee": null` to `PROJ-1`; add `PROJ-2` ("Add rate limiting to withdrawal API", status "To Do", assignee `{"accountId": "stub-user-1", "displayName": "Stub User"}`) and `PROJ-3` ("Migrate session store to Redis", status "To Do", assignee `{"accountId": "other-user-9", "displayName": "Someone Else"}`).

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/unit/test_stub_jira_mcp.py -v`. Expected: ALL pass (old 5 + new 4).

- [ ] **Step 5: Commit** — `git add docker/stub-jira-mcp tests/unit/test_stub_jira_mcp.py && git commit -m "Extend stub Jira MCP with write tools, fixtures, and a mutations log"`

---

### Task 2: The ticket-sync skill

**Model:** heavy coding tier

**Files:**
- Create: `skills/ticket-sync/SKILL.md`

**Interfaces:**
- Consumes: MCP tracker tools discovered at runtime (Jira/Atlassian family; in E2E, Task 1's stub tools).
- Produces: the `OMC_TICKET` verdict contract from Global Constraints, consumed by Task 3's callers and Task 5's tests. Invocation shape (from callers): "Invoke the internal `ticket-sync` skill with phase `start` and the ticket reference" / "with phase `review`".

- [ ] **Step 1: Write the skill** — create `skills/ticket-sync/SKILL.md` with exactly this content:

````markdown
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
   - **Another user, interactive session** → ask with a question dialog,
     exactly three options:
     1. **Abort** — no writes; verdict `{"ok": false, "reason":
        "user-declined", ...}`. The caller stops its flow on this reason.
     2. **Reassign to me** — assign to the current user; `assigned:
        "taken-over"`; continue to step 5.
     3. **Continue without reassigning** — leave the assignee untouched;
        `assigned: "kept"`; continue to step 5.
   - **Another user, non-interactive/headless run** → zero writes (no
     assign, no transition — nobody is there to pick); verdict
     `{"ok": false, "reason": "assigned-elsewhere", ...}`; the caller
     notes it and continues.
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
````

- [ ] **Step 2: Sanity-check the packaging** — run `uv run pytest tests/unit/test_skills_source.py -v` (skill discovery/packaging tests). Expected: PASS — skills are auto-discovered from `skills/`, no registration needed. If that test file doesn't exist, run `rg -l "skills_source" tests/unit` and run the tests that cover `skills_source.py` instead.

- [ ] **Step 3: Commit** — `git add skills/ticket-sync && git commit -m "Add internal ticket-sync skill - the only tracker-writing surface"`

---

### Task 3: Wire ticket-sync into start and finish; amend the pledges

**Model:** standard coding tier

**Files:**
- Modify: `skills/start/SKILL.md` (Step 2 pledge line ~44; insert Step 2.5 after the context gate ~line 51)
- Modify: `skills/finish/SKILL.md` (Step 5, after the create-mr paragraph)

**Interfaces:**
- Consumes: the `ticket-sync` invocation shape and `OMC_TICKET` reasons from Task 2 (`user-declined` is the only flow-stopping reason).

- [ ] **Step 1: Amend the start pledge** — in `skills/start/SKILL.md`, replace the sentence `Never write to the tracker.` (end of the Step 2 bullet about linked documents) with: `Step 2 is read-only — ticket writes happen only via ticket-sync (Step 2.5).`

- [ ] **Step 2: Insert Step 2.5** — in `skills/start/SKILL.md`, immediately after the context-gate paragraph (before `## Step 3 — base freshness gate`), insert:

```markdown
## Step 2.5 — claim the ticket (ticket-sync)

Only when the work context is a ticket key or URL (free-text work skips this
step entirely): invoke the internal **`ticket-sync`** skill with phase
`start` and the ticket reference. It assigns the ticket (asking first when
someone else holds it) and moves it to an "In Progress"-equivalent status.

- Verdict reason `user-declined` → STOP the start flow: the user chose not
  to take over someone else's ticket.
- Any other failure (`mcp-missing`, `mcp-unauthenticated`,
  `no-matching-transition`, `assigned-elsewhere`, …) → report the one-line
  message so the user knows the ticket was NOT moved, and continue — ticket
  sync is best-effort, never a gate.
```

- [ ] **Step 3: Wire finish** — in `skills/finish/SKILL.md`, at the end of the Step 5 paragraph (after `the commit carries the full description.`), append:

```markdown
Then, once `create-mr` reports a successful push, invoke the internal
**`ticket-sync`** skill with phase `review` — it moves the ticket (key
derived from the branch name) to an "In Review"-equivalent column. Any
failure: report its one-line message and continue to Step 6 — the push
already succeeded, and the ticket can be moved by hand. The Step 6
"address review comments" loop re-runs `create-mr` WITHOUT re-invoking
ticket-sync (the ticket is already in review).
```

- [ ] **Step 4: Verify prose coherence** — read both modified files end-to-end; confirm step numbering flows (2 → 2.5 → 3), the slug and plan skills still carry their untouched read-only pledges (`rg "never write" skills/ -i` shows slug:read-only, plan:read-only, start's new scoped line), and no other skill references ticket-sync.

- [ ] **Step 5: Commit** — `git add skills/start/SKILL.md skills/finish/SKILL.md && git commit -m "Wire ticket-sync into start (Step 2.5) and finish (post-push review transition)"`

---

### Task 4: Headless seeded sessions get MCP tool allowance

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/start.py` (`_run_headless`, ~line 44)
- Test: `tests/unit/test_start.py`

**Interfaces:**
- Consumes: `MCP_TOOL_PATTERNS` from `src/omc/slug.py:29` (`["mcp__jira", "mcp__atlassian", "mcp__linear", "mcp__github", "mcp__gitlab"]`).
- Produces: headless seeded sessions whose provider argv includes `--allowed-tools mcp__jira ...` (claude provider), so the in-session start skill can call tracker MCP tools in E2E. Interactive sessions are untouched (they use the user's own permission flow).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_start.py` (match the file's existing import style for `Config`; a bare `Config()` defaults to the claude provider):

```python
def test_run_headless_allows_mcp_tool_patterns():
    from types import SimpleNamespace
    from omc.slug import MCP_TOOL_PATTERNS
    from omc.start import _run_headless

    captured = {}

    class FakeCtx:
        def run(self, argv, cwd=None, extra_env=None):
            captured["argv"] = argv
            return SimpleNamespace(stdout="", stderr="", returncode=0)

    rc = _run_headless(FakeCtx(), Config(), seed="/omc:start PROJ-1", cwd=".", slug="proj-1-x")
    assert rc == 0
    argv = captured["argv"]
    assert "--allowed-tools" in argv
    for pattern in MCP_TOOL_PATTERNS:
        assert pattern in argv
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/unit/test_start.py::test_run_headless_allows_mcp_tool_patterns -v`. Expected: FAIL — `"--allowed-tools" in argv` is False.

- [ ] **Step 3: Implement** — in `src/omc/start.py`, import `MCP_TOOL_PATTERNS` alongside the existing `from .slug import fetch_slug` import (`from .slug import MCP_TOOL_PATTERNS, fetch_slug`) and change the `_run_headless` argv construction to:

```python
    argv = provider.headless_argv(
        seed, model=model, session_name=slug, allowed_tools=MCP_TOOL_PATTERNS
    )
```

- [ ] **Step 4: Run the full unit suite** — `uv run pytest tests/unit -v`. Expected: all pass (existing headless tests don't assert the absence of `--allowed-tools`; if one does, update its expectation — the allowance is now intended behavior).

- [ ] **Step 5: Commit** — `git add src/omc/start.py tests/unit/test_start.py && git commit -m "Pass MCP tool patterns to headless seeded sessions, matching the slug path"`

---

### Task 5: E2E coverage (hermetic, Docker-per-test)

**Model:** heavy coding tier

**Files:**
- Modify: `tests/e2e/harness.py` (`wire_mcp`)
- Create: `tests/e2e/test_e2e_ticket_sync.py`

**Interfaces:**
- Consumes: Task 1's stub tools/fixtures and `$STUB_JIRA_MUTATIONS_LOG`; Task 2's `OMC_TICKET` verdict; Task 4's headless allowance; existing harness helpers `PROVIDERS, configure_omc, make_work_repo, require_token, run_in, wire_mcp` and the `container` fixture (same usage as `tests/e2e/test_e2e_start.py`).
- Produces: nothing downstream — terminal task.

- [ ] **Step 1: Extend `wire_mcp`** — in `tests/e2e/harness.py`, give the stub a fixed in-container mutations log. Add module constant `MUTATIONS_LOG = "/tmp/stub-jira-mutations.jsonl"`, and in `wire_mcp` add the env key to all three provider specs: claude's `jira_spec["env"]` becomes `{"STUB_JIRA_MODE": mode, "STUB_JIRA_MUTATIONS_LOG": MUTATIONS_LOG}`; codex's toml env line becomes `env = {{ STUB_JIRA_MODE = "{mode}", STUB_JIRA_MUTATIONS_LOG = "{MUTATIONS_LOG}" }}`; opencode's command list gains a second `env` assignment `f"STUB_JIRA_MUTATIONS_LOG={MUTATIONS_LOG}"` after `stub_env`.

- [ ] **Step 2: Write the E2E tests** — create `tests/e2e/test_e2e_ticket_sync.py`:

```python
import json

import pytest

from .harness import MUTATIONS_LOG, configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e


def _mutations(container):
    rc, out = run_in(container, ["cat", MUTATIONS_LOG])
    if rc != 0:
        return []
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_start_unassigned_claims_and_transitions(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out
    muts = _mutations(container)
    tools = [m["tool"] for m in muts if m["key"] == "PROJ-1"]
    assert "assignIssue" in tools, f"ticket was not assigned: {muts}\n{out[-2000:]}"
    assert "transitionIssue" in tools, f"ticket was not transitioned: {muts}\n{out[-2000:]}"
    assert "OMC_TICKET" in out, out[-2000:]


def test_start_assigned_to_other_skips_writes_headless(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-3", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out  # flow continues; sync skipped
    muts = [m for m in _mutations(container) if m["key"] == "PROJ-3"]
    assert muts == [], f"headless run wrote to someone else's ticket: {muts}"
    assert "assigned-elsewhere" in out, out[-2000:]


def test_start_auth_error_reports_and_continues(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "auth-error")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    # slug already tolerates auth-error fixtures upstream; the run must not
    # die because the tracker write failed
    assert _mutations(container) == []
    assert rc == 0 or "mcp-unauthenticated" in out, out[-2000:]


def test_start_free_text_never_touches_tracker(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(
        container,
        ["omc", "start", "add retry logic to the export job", "--headless"],
        cwd=repo,
        timeout=900,
    )
    assert rc == 0, out
    assert _mutations(container) == [], "free-text work must not write to the tracker"


def test_review_phase_transitions_to_in_review(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-2", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out
    worktree = f"{repo}.feature-proj-2"
    rc2, wtout = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert "feature/proj-2" in wtout, wtout
    # drive phase `review` directly in the worktree (full finish-flow E2E is
    # out of scope; this exercises the skill's own review path end to end)
    rc3, out3 = run_in(
        container,
        [
            "claude", "-p",
            "Invoke the ticket-sync skill with phase review for this branch.",
            "--output-format", "text",
            "--allowed-tools", "mcp__jira",
        ],
        cwd=worktree,
        timeout=600,
    )
    muts = [m for m in _mutations(container) if m["key"] == "PROJ-2"]
    reviews = [m for m in muts if m["tool"] == "transitionIssue" and m["arguments"].get("transitionId") == "31"]
    assert reviews, f"no In Review transition recorded: {muts}\n{out3[-2000:]}"
```

- [ ] **Step 3: Resolve the worktree path assumption** — before relying on `f"{repo}.feature-proj-2"` in the review test, check how `test_e2e_start.py`'s sibling tests locate worktrees (via `wt list --format=json` output). If the JSON carries paths, parse the path for `feature/proj-2` from `wtout` instead of assuming the naming scheme. Fix the test accordingly.

- [ ] **Step 4: Run the E2E suite** — `uv run pytest tests/e2e/test_e2e_ticket_sync.py -v` (requires Docker + a live claude token, same as the rest of `tests/e2e/`). Expected: all 5 pass. Transcript-dependent assertions (`OMC_TICKET` in output) may need one tolerance pass — LLM transcripts vary; assert on the mutations log first, transcripts second.

- [ ] **Step 5: Run unit suite as regression guard** — `uv run pytest tests/unit -v`. Expected: all pass (harness change is E2E-only).

- [ ] **Step 6: Commit** — `git add tests/e2e && git commit -m "Add hermetic E2E coverage for ticket-sync start and review phases"`

---

## Deferred / follow-ups (not in this plan)

- Full finish-flow E2E (needs a pushable origin in the harness work repo) — the review phase is covered by direct skill invocation instead.
- Plugin snapshot refresh for delivery (version-pin at 0.1.0): shipping `ticket-sync` to an installed harness needs plugin uninstall + install — release note, not a code task.
- Per-project status-mapping config; GitHub/GitLab/Linear fixtures — out of scope per spec.
