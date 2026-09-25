"""Ticket E2E assertions depend on tracker artifacts, not final-answer wording."""

from copy import deepcopy

import pytest

from tests.e2e import test_e2e_ticket_sync as ticket_tests


@pytest.mark.parametrize(
    "defect",
    [None, "missing-assignment", "wrong-assignee", "missing-transition", "wrong-transition"],
)
def test_unassigned_ticket_assertions_use_mutation_evidence(monkeypatch, defect):
    mutations = [
        {"key": "PROJ-1", "tool": "assignIssue", "arguments": {"accountId": "stub-user-1"}},
        {"key": "PROJ-1", "tool": "transitionIssue", "arguments": {"transitionId": "21"}},
    ]
    if defect == "missing-assignment":
        mutations.pop(0)
    elif defect == "wrong-assignee":
        mutations[0]["arguments"]["accountId"] = "someone-else"
    elif defect == "missing-transition":
        mutations.pop(1)
    elif defect == "wrong-transition":
        mutations[1]["arguments"]["transitionId"] = "31"

    monkeypatch.setattr(ticket_tests, "require_token", lambda *_: None)
    monkeypatch.setattr(ticket_tests, "configure_omc", lambda *_: None)
    monkeypatch.setattr(ticket_tests, "wire_mcp", lambda *_: None)
    monkeypatch.setattr(ticket_tests, "make_work_repo", lambda *_: "/disposable")
    # Correct writes must pass even though final-only CLI output omits the
    # intermediate verdict. Conversely, printing a verdict cannot cover bad writes.
    output = "Project primer. What is your seed?" if defect is None else "OMC_TICKET {}"
    monkeypatch.setattr(ticket_tests, "run_in", lambda *_, **__: (0, output))
    monkeypatch.setattr(ticket_tests, "_mutations", lambda *_: mutations)
    if defect is None:
        ticket_tests.test_start_unassigned_claims_and_transitions(object())
    else:
        with pytest.raises(AssertionError):
            ticket_tests.test_start_unassigned_claims_and_transitions(object())


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "missing-issue-read",
        "missing-user-read",
        "wrong-ticket",
        "wrong-assignee",
        "wrong-current-user",
        "stale-reads-only",
        "audit-replaced",
        "sync-failed",
        "start-wrote",
        "sync-wrote",
    ],
)
def test_assigned_elsewhere_assertions_require_fresh_reads_and_zero_writes(monkeypatch, defect):
    issue = {
        "tool": "getIssue",
        "key": "PROJ-3",
        "arguments": {"key": "PROJ-3"},
        "result": {
            "key": "PROJ-3",
            "fields": {"assignee": {"accountId": "other-user-9"}},
        },
    }
    user = {
        "tool": "getCurrentUser",
        "key": "",
        "arguments": {},
        "result": {"accountId": "stub-user-1"},
    }
    before = [issue, user]  # Earlier slug/start reads cannot prove direct sync ran.
    fresh = deepcopy(before)
    if defect == "missing-issue-read":
        fresh.pop(0)
    elif defect == "missing-user-read":
        fresh.pop(1)
    elif defect == "wrong-ticket":
        fresh[0]["key"] = fresh[0]["result"]["key"] = "PROJ-2"
    elif defect == "wrong-assignee":
        fresh[0]["result"]["fields"]["assignee"]["accountId"] = "stub-user-1"
    elif defect == "wrong-current-user":
        fresh[1]["result"]["accountId"] = "other-user-9"
    elif defect == "stale-reads-only":
        fresh = []
    after = before + fresh
    if defect == "audit-replaced":
        after = [user, issue] + fresh
    read_snapshots = iter([before, after])
    write = {"key": "PROJ-3", "tool": "transitionIssue", "arguments": {"transitionId": "21"}}
    mutation_snapshots = iter(
        [[write] if defect == "start-wrote" else [], [write] if defect == "sync-wrote" else []]
    )

    monkeypatch.setattr(ticket_tests, "require_token", lambda *_: None)
    monkeypatch.setattr(ticket_tests, "configure_omc", lambda *_: None)
    monkeypatch.setattr(ticket_tests, "wire_mcp", lambda *_: None)
    monkeypatch.setattr(ticket_tests, "make_work_repo", lambda *_: "/disposable")
    monkeypatch.setattr(ticket_tests, "run_in", lambda *_, **__: (0, "Project primer."))
    monkeypatch.setattr(ticket_tests, "_worktree_path", lambda *_: "/disposable.feature/proj-3")
    monkeypatch.setattr(ticket_tests, "_mutations", lambda *_: next(mutation_snapshots))
    monkeypatch.setattr(ticket_tests, "_reads", lambda *_: next(read_snapshots), raising=False)
    # Valid artifacts pass without a verdict; a verdict cannot hide missing
    # reads, a failed invocation, or writes to the protected ticket.
    output = (
        "The ticket belongs to Someone Else. No changes made."
        if defect is None
        else "assigned-elsewhere"
    )
    monkeypatch.setattr(
        ticket_tests,
        "_drive_ticket_sync",
        lambda *_: (7 if defect == "sync-failed" else 0, output),
    )
    if defect is None:
        ticket_tests.test_start_assigned_to_other_skips_writes_headless(object())
    else:
        with pytest.raises(AssertionError):
            ticket_tests.test_start_assigned_to_other_skips_writes_headless(object())
