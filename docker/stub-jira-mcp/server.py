#!/usr/bin/env python3
"""Hermetic stdio MCP server impersonating a Jira tracker for omc's E2E tests.

Modes (env STUB_JIRA_MODE):
  ok          serve fixture tickets from tickets.json
  auth-error  every tools/call fails like an expired credential (HTTP 401 flavor)

Stdlib only; line-delimited JSON-RPC 2.0 over stdio.
"""

import json
import os
import sys
from pathlib import Path

MODE = os.environ.get("STUB_JIRA_MODE", "ok")
MUTATIONS_LOG = os.environ.get("STUB_JIRA_MUTATIONS_LOG", "")
# Each E2E case has its own container, so a fixed path lets tests distinguish
# reads made by the skill under test from earlier setup calls.
READS_LOG = os.environ.get("STUB_JIRA_READS_LOG", "/tmp/stub-jira-reads.jsonl")
TICKETS = json.loads((Path(__file__).parent / "tickets.json").read_text())

TRANSITIONS = [
    {"id": "11", "name": "To Do"},
    {"id": "21", "name": "In Progress"},
    {"id": "31", "name": "In Review"},
    {"id": "41", "name": "Done"},
]

TOOLS = [
    {
        "name": "getIssue",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Fetch a Jira issue by key (e.g. PROJ-1).",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Issue key"}},
            "required": ["key"],
        },
    },
    {
        "name": "getCurrentUser",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Get the currently authenticated Jira user.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "assignIssue",
        "description": "Assign a Jira issue to a user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Issue key"},
                "accountId": {"type": "string", "description": "Assignee account id"},
            },
            "required": ["key", "accountId"],
        },
    },
    {
        "name": "listTransitions",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "List available workflow transitions for a Jira issue.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Issue key"}},
            "required": ["key"],
        },
    },
    {
        "name": "transitionIssue",
        "description": "Transition a Jira issue to a new status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Issue key"},
                "transitionId": {"type": "string", "description": "Transition id"},
            },
            "required": ["key", "transitionId"],
        },
    },
]


def log_mutation(tool: str, key: str, arguments: dict) -> None:
    if not MUTATIONS_LOG:
        return
    with open(MUTATIONS_LOG, "a") as f:
        f.write(json.dumps({"tool": tool, "key": key, "arguments": arguments}) + "\n")


def log_read(tool: str, key: str | None, arguments: dict, result: dict) -> None:
    if not READS_LOG:
        return
    with open(READS_LOG, "a") as f:
        f.write(
            json.dumps({"tool": tool, "key": key, "arguments": arguments, "result": result}) + "\n"
        )


def tool_result(text: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    if method == "initialize":
        return {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stub-jira", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        if MODE == "auth-error":
            return tool_result(
                "Authentication failed (HTTP 401): OAuth token expired or revoked. "
                "Re-authenticate this MCP server and retry.",
                is_error=True,
            )
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return tool_result("invalid params", is_error=True)
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}

        if name == "getCurrentUser":
            user = {"accountId": "stub-user-1", "displayName": "Stub User"}
            log_read(name, None, arguments, user)
            return tool_result(json.dumps(user))

        if name == "getIssue":
            key = str(arguments.get("key", "")).upper()
            ticket = TICKETS.get(key)
            if ticket is None:
                return tool_result(f"Issue {key} not found (404).", is_error=True)
            issue = {"key": key, "fields": ticket}
            log_read(name, key, arguments, issue)
            return tool_result(json.dumps(issue, indent=2))

        if name == "listTransitions":
            key = str(arguments.get("key", "")).upper()
            if key not in TICKETS:
                return tool_result(f"Issue {key} not found (404).", is_error=True)
            return tool_result(json.dumps({"transitions": TRANSITIONS}))

        if name == "assignIssue":
            key = str(arguments.get("key", "")).upper()
            ticket = TICKETS.get(key)
            if ticket is None:
                return tool_result(f"Issue {key} not found (404).", is_error=True)
            account_id = arguments.get("accountId", "")
            ticket["assignee"] = {"accountId": account_id, "displayName": account_id}
            log_mutation("assignIssue", key, arguments)
            return tool_result(json.dumps({"key": key, "assignee": ticket["assignee"]}))

        if name == "transitionIssue":
            key = str(arguments.get("key", "")).upper()
            ticket = TICKETS.get(key)
            if ticket is None:
                return tool_result(f"Issue {key} not found (404).", is_error=True)
            transition_id = arguments.get("transitionId", "")
            transition = next((t for t in TRANSITIONS if t["id"] == transition_id), None)
            if transition is None:
                return tool_result(f"unknown transition {transition_id!r}", is_error=True)
            ticket["status"] = transition["name"]
            log_mutation("transitionIssue", key, arguments)
            return tool_result(json.dumps({"key": key, "status": ticket["status"]}))

        return tool_result(f"unknown tool {name!r}", is_error=True)
    if method == "ping":
        return {}
    return None  # notifications etc.


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = handle(msg)
        if msg.get("id") is None:
            continue  # notification: no response
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        if result is not None:
            reply["result"] = result
        else:
            reply["error"] = {
                "code": -32601,
                "message": f"Method not found: {msg.get('method', '')}",
            }
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
