import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "docker" / "stub-jira-mcp" / "server.py"


def _rpc(proc, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method, "id": id_}
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def _start(mode="ok"):
    return subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={"STUB_JIRA_MODE": mode, "PATH": "/usr/bin:/bin"},
    )


def test_initialize_and_tools_list():
    proc = _start()
    try:
        init = _rpc(proc, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        assert init["result"]["serverInfo"]["name"] == "stub-jira"
        tools = _rpc(proc, "tools/list", id_=2)
        names = [t["name"] for t in tools["result"]["tools"]]
        assert names == ["getIssue"]
    finally:
        proc.terminate()


def test_get_issue_ok_and_not_found():
    proc = _start()
    try:
        _rpc(proc, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        r = _rpc(
            proc,
            "tools/call",
            {"name": "getIssue", "arguments": {"key": "PROJ-1"}},
            id_=2,
        )
        text = r["result"]["content"][0]["text"]
        assert "Fix login timeout" in text
        r = _rpc(proc, "tools/call", {"name": "getIssue", "arguments": {"key": "PROJ-999"}}, id_=3)
        assert r["result"]["isError"] is True
        assert "not found" in r["result"]["content"][0]["text"].lower()
    finally:
        proc.terminate()


def test_auth_error_mode():
    proc = _start(mode="auth-error")
    try:
        _rpc(proc, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        r = _rpc(
            proc,
            "tools/call",
            {"name": "getIssue", "arguments": {"key": "PROJ-1"}},
            id_=2,
        )
        assert r["result"]["isError"] is True
        text = r["result"]["content"][0]["text"]
        assert "401" in text and "auth" in text.lower()
    finally:
        proc.terminate()


def test_unknown_method_gets_error():
    proc = _start()
    try:
        _rpc(proc, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        r = _rpc(proc, "resources/list", id_=2)
        assert r["error"]["code"] == -32601
        assert "result" not in r
    finally:
        proc.terminate()


def test_ping_returns_empty_result():
    proc = _start()
    try:
        _rpc(proc, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        r = _rpc(proc, "ping", id_=2)
        assert r["result"] == {}
        assert "error" not in r
    finally:
        proc.terminate()
