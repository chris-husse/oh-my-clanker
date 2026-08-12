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


def _start(mode="ok", extra_env=None):
    env = {"STUB_JIRA_MODE": mode, "PATH": "/usr/bin:/bin"}
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )


def test_initialize_and_tools_list():
    proc = _start()
    try:
        init = _rpc(proc, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        assert init["result"]["serverInfo"]["name"] == "stub-jira"
        tools = _rpc(proc, "tools/list", id_=2)
        names = [t["name"] for t in tools["result"]["tools"]]
        assert names == [
            "getIssue",
            "getCurrentUser",
            "assignIssue",
            "listTransitions",
            "transitionIssue",
        ]
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
        res = _call(proc, "transitionIssue", {"key": "PROJ-1", "transitionId": "21"}, id_=7)[
            "result"
        ]
        assert not res.get("isError")
        res = _call(proc, "getIssue", {"key": "PROJ-1"}, id_=8)["result"]
        fields = json.loads(res["content"][0]["text"])["fields"]
        assert fields["status"] == "In Progress"
        assert fields["assignee"]["accountId"] == "stub-user-1"
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        assert [e["tool"] for e in entries] == ["assignIssue", "transitionIssue"]
        assert all(e["key"] == "PROJ-1" for e in entries)
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
        for key, expected in [
            ("PROJ-1", None),
            ("PROJ-2", "stub-user-1"),
            ("PROJ-3", "other-user-9"),
        ]:
            res = _call(proc, "getIssue", {"key": key})["result"]
            assignee = json.loads(res["content"][0]["text"])["fields"]["assignee"]
            assert (assignee or {}).get("accountId") == expected or (
                assignee is None and expected is None
            )
    finally:
        proc.terminate()
