"""Hermetic E2E coverage for the ticket-sync flow.

No real tracker is ever touched: the stub Jira MCP records every write as a JSON
line in $STUB_JIRA_MUTATIONS_LOG (harness.MUTATIONS_LOG), and these tests assert
on THAT log first. Transcript greps are a secondary signal only — LLM prose
varies run to run, the mutations log does not.
"""

import json

import pytest

from .harness import MUTATIONS_LOG, configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e

# ticket-sync's review phase picks an "In Review"-equivalent transition; "31" is
# the stub's In Review id (docker/stub-jira-mcp/server.py TRANSITIONS).
IN_REVIEW_TRANSITION = "31"

# ticket-sync's start phase picks an "In Progress"-equivalent transition; "21" is
# the stub's In Progress id (docker/stub-jira-mcp/server.py TRANSITIONS), verified
# against live-run evidence.
IN_PROGRESS_TRANSITION = "21"


def _mutations(container):
    """Every tracker write the stub recorded so far, oldest first.

    A missing log file means zero writes happened (the stub only creates it on
    the first mutation), which is a legitimate — and frequently asserted —
    outcome, so that is an empty list rather than a failure.
    """
    rc, out = run_in(container, ["cat", MUTATIONS_LOG])
    if rc != 0:
        return []
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _wt_entries(container, repo):
    """`wt list --format=json`, decoded.

    wt prefixes the array with a plain-text advisory (observed:
    `[list] json-schema = 1; to adopt the new schema, set json-schema = 2`), and
    that line starts with `[` — so "slice from the first bracket" does not work.
    Try raw_decode at each `[` and keep the first one that yields a list.
    """
    rc, wtout = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert rc == 0, wtout
    decoder = json.JSONDecoder()
    for idx, char in enumerate(wtout):
        if char != "[":
            continue
        try:
            entries, _ = decoder.raw_decode(wtout, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(entries, list):
            return entries, wtout
    pytest.fail(f"wt list emitted no JSON array:\n{wtout}")


def _worktree_path(container, repo, branch_prefix):
    """Resolve a worktree's on-disk path from wt's own JSON.

    Matched by PREFIX, not equality: the slug skill appends a summary to the
    ticket key, so the branch is `feature/proj-2-add-rate-limiting-withdrawal-api`
    rather than `feature/proj-2` (verified live). The `-` boundary keeps
    `feature/proj-2` from matching a `feature/proj-21-…` branch. The path comes
    out of the entry, so wt's `<repo>.<branch>` directory naming is never guessed.
    """
    entries, wtout = _wt_entries(container, repo)
    for entry in entries:
        branch = entry.get("branch") or ""
        if branch == branch_prefix or branch.startswith(f"{branch_prefix}-"):
            path = entry.get("path")
            assert path, f"wt entry for {branch} carries no path: {entry}"
            return path
    pytest.fail(f"no worktree for {branch_prefix}* in:\n{wtout}")


def _drive_ticket_sync(container, cwd, instruction):
    """Invoke the ticket-sync skill directly, non-interactively, in `cwd`.

    Used where the full `omc start` / `omc finish` flow is out of scope or too
    variable: /omc:start's own context gate is free to stop the seeded session
    before Step 2.5 ever runs (it did, live, on a README-only work repo), which
    would silently stop testing ticket-sync at all.

    Bash/Read/Glob/Grep are granted because the skill derives the ticket key
    from the branch (`git rev-parse`) before it touches the tracker.
    AskUserQuestion is deliberately NOT granted — its absence is how the skill
    recognises a non-interactive run.
    """
    return run_in(
        container,
        [
            "claude",
            "-p",
            f"Invoke the ticket-sync skill with {instruction}.",
            "--output-format",
            "text",
            "--allowed-tools",
            "mcp__jira",
            "Bash",
            "Read",
            "Glob",
            "Grep",
        ],  # fmt: skip
        cwd=cwd,
        timeout=600,
    )


def test_start_unassigned_claims_and_transitions(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out
    muts = [m for m in _mutations(container) if m["key"] == "PROJ-1"]
    tools = [m["tool"] for m in muts]
    assert "assignIssue" in tools, f"ticket was not assigned: {muts}\n{out[-2000:]}"
    assert "transitionIssue" in tools, f"ticket was not transitioned: {muts}\n{out[-2000:]}"
    in_progress = [
        m
        for m in muts
        if m["tool"] == "transitionIssue"
        and m["arguments"].get("transitionId") == IN_PROGRESS_TRANSITION
    ]
    assert in_progress, f"no In Progress transition recorded: {muts}\n{out[-2000:]}"
    assert "OMC_TICKET" in out, out[-2000:]


def test_start_assigned_to_other_skips_writes_headless(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-3", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out  # flow continues; sync skipped
    muts = [m for m in _mutations(container) if m["key"] == "PROJ-3"]
    assert muts == [], f"whole headless start flow wrote to someone else's ticket: {muts}"

    # The zero-writes invariant above is the load-bearing one and covers the
    # whole flow. The verdict itself is driven directly so the assertion cannot
    # be made vacuous by /omc:start stopping early — see _drive_ticket_sync.
    worktree = _worktree_path(container, repo, "feature/proj-3")
    _, syncout = _drive_ticket_sync(container, worktree, "phase start for ticket PROJ-3")
    muts = [m for m in _mutations(container) if m["key"] == "PROJ-3"]
    assert muts == [], f"non-interactive ticket-sync wrote to someone else's ticket: {muts}"
    assert "assigned-elsewhere" in syncout, syncout[-3000:]


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
    worktree = _worktree_path(container, repo, "feature/proj-2")
    # Drive phase `review` directly in the worktree: the full finish flow needs a
    # pushable origin and is out of scope, so this exercises the skill's own
    # review path — including deriving PROJ-2 from the branch name — end to end.
    _, out3 = _drive_ticket_sync(container, worktree, "phase review for this branch")
    muts = [m for m in _mutations(container) if m["key"] == "PROJ-2"]
    reviews = [
        m
        for m in muts
        if m["tool"] == "transitionIssue"
        and m["arguments"].get("transitionId") == IN_REVIEW_TRANSITION
    ]
    assert reviews, f"no In Review transition recorded: {muts}\n{out3[-2000:]}"
