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
TICKETS = json.loads((Path(__file__).parent / "tickets.json").read_text())

TOOLS = [
    {
        "name": "getIssue",
        "description": "Fetch a Jira issue by key (e.g. PROJ-1).",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Issue key"}},
            "required": ["key"],
        },
    }
]


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
        if params.get("name") != "getIssue":
            return tool_result(f"unknown tool {params.get('name')!r}", is_error=True)
        key = str(params.get("arguments", {}).get("key", "")).upper()
        ticket = TICKETS.get(key)
        if ticket is None:
            return tool_result(f"Issue {key} not found (404).", is_error=True)
        return tool_result(json.dumps({"key": key, "fields": ticket}, indent=2))
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
        reply["result"] = result if result is not None else {}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
