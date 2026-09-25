"""Regression tests for the container conversation protocol and artifact snapshot."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import struct
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

_server_path = Path(__file__).resolve().parents[2] / "docker" / "conversation.py"
_spec = importlib.util.spec_from_file_location("omc_conversation_server", _server_path)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
EventTail = _module.EventTail
TurnTracker = _module.TurnTracker
snapshot_repo = _module.snapshot_repo
PTYSession = _module.PTYSession
_secret_values = _module._secret_values


def test_save_evidence_preserves_each_sanitized_run(monkeypatch, tmp_path):
    from tests.e2e import conversation

    monkeypatch.setattr(conversation, "ARTIFACTS", tmp_path)
    monkeypatch.setenv("OMC_TEST_TOKEN", "private-test-token")

    first = conversation.save_evidence(
        "codex-lifecycle", {"run": 1, "secret": "private-test-token"}
    )
    second = conversation.save_evidence(
        "codex-lifecycle", {"run": 2, "secret": "private-test-token"}
    )

    assert first != second
    assert first.parent == second.parent == tmp_path
    assert sorted(tmp_path.iterdir()) == sorted([first, second])
    assert json.loads(first.read_text()) == {"run": 1, "secret": "[redacted]"}
    assert json.loads(second.read_text()) == {"run": 2, "secret": "[redacted]"}


def test_tui_submission_ack_is_bounded_by_task_started_event():
    session = PTYSession.__new__(PTYSession)
    session.tracker = TurnTracker()
    session.output = bytearray(b"COMPOSER STILL OPEN")
    session._poll = lambda: None
    with pytest.raises(TimeoutError, match="no task_started"):
        session.wait_started(0.01)
    session.tracker.feed(
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}}
    )
    assert session.wait_started(0.01) == "turn-1"


def test_codex_bridge_waits_for_input_ack_before_long_completion_wait():
    from tests.e2e.conversation import Conversation

    bridge = Conversation.__new__(Conversation)
    bridge.provider = "codex"
    seen = []
    bridge._request = lambda action, **kwargs: seen.append((action, kwargs)) or {}
    bridge.send("$omc:implement")
    assert seen == [
        ("send", {"message": "$omc:implement"}),
        ("wait_started", {"timeout": 12, "response_timeout": 17}),
    ]


def test_trust_prompt_is_answered_after_session_metadata_arrives(monkeypatch):
    session = PTYSession.__new__(PTYSession)
    session.trust_answered = False
    session.fd = 99
    session.session_id = "early-session-meta"
    session.tracker = TurnTracker()
    session.cwd = Path("/work/native-slash")
    session.events = []
    session.output = bytearray(
        b"/work/native-slash Trust this folder? 1. Trust and continue enter esc quit"
    )
    writes = []
    monkeypatch.setattr(_module.os, "write", lambda fd, data: writes.append((fd, data)))
    session._maybe_answer_bootstrap_trust()
    assert writes == [(99, b"\r")]
    assert session.trust_answered


def test_trust_prompt_is_not_answered_after_actor_turn_started(monkeypatch):
    session = PTYSession.__new__(PTYSession)
    session.trust_answered = False
    session.fd = 99
    session.session_id = "actor-session"
    session.tracker = TurnTracker()
    session.tracker.feed(
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}}
    )
    session.cwd = Path("/work/native-slash")
    session.events = []
    session.output = bytearray(
        b"/work/native-slash Trust this folder? 1. Trust and continue enter esc quit"
    )
    writes = []
    monkeypatch.setattr(_module.os, "write", lambda fd, data: writes.append((fd, data)))
    session._maybe_answer_bootstrap_trust()
    assert writes == []
    session.tracker.next_turn()
    session._maybe_answer_bootstrap_trust()
    assert writes == [], "a later turn must not re-enable bootstrap trust handling"


def test_bare_codex_skill_mention_inserts_then_submits(monkeypatch):
    session = PTYSession.__new__(PTYSession)
    session.fd = 99
    session.pid = 1
    session.tracker = TurnTracker()
    session.events = []
    writes = []
    monkeypatch.setattr(_module.os, "write", lambda fd, data: writes.append((fd, data)))
    monkeypatch.setattr(_module.time, "sleep", lambda _: None)
    session.send("$omc:implement")
    assert writes == [
        (99, b"\x1b[200~$omc:implement\x1b[201~"),
        (99, b"\r"),  # select the displayed skill completion
        (99, b"\r"),  # submit the inserted token as a user turn
    ]


def test_codex_skill_read_requires_successful_installed_file_result():
    from tests.e2e.test_e2e_lifecycle import _assert_skill_reads

    path = "/root/.codex/plugins/cache/oh-my-clanker/omc/0.1.7/skills/start/SKILL.md"
    tracker = _module.SkillReadTracker({"omc:start": path})
    call = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "read-1",
            "arguments": json.dumps({"cmd": f"cat {path}"}),
        },
    }
    tracker.feed(call)
    with pytest.raises(AssertionError, match="omc:start"):
        _assert_skill_reads(tracker.events, {"omc:start": path})
    tracker.feed(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "read-1",
                "output": "Process exited with code 1\ncat: permission denied",
            },
        }
    )
    with pytest.raises(AssertionError, match="omc:start"):
        _assert_skill_reads(tracker.events, {"omc:start": path})
    tracker.feed({**call, "payload": {**call["payload"], "call_id": "read-2"}})
    tracker.feed(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "read-2",
                "output": "Process exited with code 0\nFinal output:\n---\nname: start",
            },
        }
    )
    _assert_skill_reads(tracker.events, {"omc:start": path})


def test_codex_subagent_skill_reads_are_collected_from_child_session(tmp_path):
    path = "/root/.codex/plugins/cache/oh-my-clanker/omc/0.1.7/skills/start/SKILL.md"
    home = tmp_path / "codex"
    sessions = home / "sessions" / "2026" / "09" / "24"
    sessions.mkdir(parents=True)
    child_log = sessions / "child.jsonl"
    records = [
        {
            "type": "session_meta",
            "payload": {
                "id": "child-1",
                "source": {
                    "subagent": {"thread_spawn": {"parent_thread_id": "root-session", "depth": 1}}
                },
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "r1",
                "arguments": json.dumps({"cmd": f"head -10 {path}"}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "r1",
                "output": "Process exited with code 0\n---\nname: start\n",
            },
        },
    ]
    child_log.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    session = PTYSession()
    session.home = home
    session.session_id = "root-session"
    session.existing = set()
    session.skill_reads = _module.SkillReadTracker({"omc:start": path})
    session._poll_child_reads()
    assert {"type": "skill_read", "skill": "omc:start", "path": path} in session.events


def test_installed_skill_access_observer_records_only_read_access():
    path = "/root/.codex/plugins/cache/omc/skills/start/SKILL.md"
    session = PTYSession()
    session.skill_watch_descriptors = {7: ("omc:start", path)}
    session._consume_skill_access_records(struct.pack("iIII", 7, 0x20, 0, 0))
    assert session.events == []  # open alone does not prove bytes were read
    session._consume_skill_access_records(struct.pack("iIII", 7, 0x01, 0, 0))
    assert session.events == [
        {"type": "skill_read", "skill": "omc:start", "path": path, "evidence": "file_access"}
    ]


def test_claude_skill_read_requires_matching_read_tool_result():
    from tests.e2e.conversation import parse_claude_stream
    from tests.e2e.test_e2e_lifecycle import _assert_skill_reads

    path = "/root/.claude/plugins/cache/oh-my-clanker/omc/0.1.7/skills/start/SKILL.md"
    records = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": path}}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "r1", "content": "---\nname: start"}
                ]
            },
        },
        {"type": "result", "result": "I read omc:start", "session_id": "session"},
    ]
    parsed = parse_claude_stream(
        "\n".join(json.dumps(row) for row in records) + "\n", {"omc:start": path}
    )
    _assert_skill_reads(parsed["events"], {"omc:start": path})
    records[1]["message"]["content"][0]["is_error"] = True
    failed = parse_claude_stream(
        "\n".join(json.dumps(row) for row in records) + "\n", {"omc:start": path}
    )
    with pytest.raises(AssertionError, match="omc:start"):
        _assert_skill_reads(failed["events"], {"omc:start": path})


START = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new"}}
DONE = {
    "type": "event_msg",
    "payload": {"type": "task_complete", "turn_id": "new", "last_agent_message": "done"},
}


def test_jsonl_tail_keeps_truncated_record_until_complete(tmp_path):
    path = tmp_path / "session.jsonl"
    tail = EventTail(path)
    line = json.dumps(START) + "\n"
    path.write_text(line[:-3])
    assert tail.read() == []
    with path.open("a") as out:
        out.write(line[-3:])
    assert tail.read() == [START]
    assert tail.read() == []


def test_turn_tracker_ignores_old_and_repeated_completions():
    tracker = TurnTracker()
    tracker.feed(
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "old", "last_agent_message": "stale"},
        }
    )
    assert tracker.completed is None
    tracker.feed(START)
    tracker.feed(
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "old", "last_agent_message": "stale"},
        }
    )
    assert tracker.completed is None
    tracker.feed(DONE)
    assert tracker.completed == "done"
    tracker.feed(DONE)
    assert len(tracker.events) == 2


def test_required_event_malformed_fails():
    tracker = TurnTracker()
    with pytest.raises(ValueError, match="turn_id"):
        tracker.feed({"type": "event_msg", "payload": {"type": "task_started"}})


def test_snapshot_detects_source_index_head_and_remote_mutations(tmp_path):
    bare = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "greeting.py").write_text("def greeting(): return 'wrong'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    before = snapshot_repo(repo)
    (repo / "greeting.py").write_text("def greeting(): return 'right'\n")
    after = snapshot_repo(repo)
    assert before["source"] != after["source"]
    assert before["index"] == after["index"]
    assert before["head"] == after["head"]
    assert before["remote_refs"] == after["remote_refs"]
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    staged = snapshot_repo(repo)
    assert before["index"] != staged["index"]
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fix"], check=True)
    committed = snapshot_repo(repo)
    assert before["head"] != committed["head"]
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    pushed = snapshot_repo(repo)
    assert before["remote_refs"] != pushed["remote_refs"]


def test_pty_drains_bursty_output_and_reports_process_exit():
    session = PTYSession()
    session.start(["python3", "-c", "print('X' * 200000, flush=True)"], "/tmp", "dummy")
    try:
        with pytest.raises(RuntimeError, match="exited before task_complete"):
            session.wait_turn(5)
        assert len(session.output) > 0
    finally:
        session.close()


def test_pty_has_real_terminal_dimensions(tmp_path):
    dimensions = tmp_path / "size"
    code = f"import os; open({str(dimensions)!r}, 'w').write(str(os.get_terminal_size(1)))"
    session = PTYSession()
    session.start(["python3", "-c", code], "/tmp", "dummy")
    try:
        with pytest.raises(RuntimeError, match="exited before task_complete"):
            session.wait_turn(5)
        assert "columns=120" in dimensions.read_text()
        assert "lines=40" in dimensions.read_text()
    finally:
        session.close()


def test_pty_answers_terminal_queries(tmp_path):
    answers = tmp_path / "answers"
    code = (
        "import os,select,tty; tty.setraw(0); "
        "os.write(1,b'\\x1b[6n\\x1b]10;?\\x1b\\\\\\x1b]11;?\\x1b\\\\\\x1b[c'); "
        "ready,_,_=select.select([0],[],[],2); "
        f"open({str(answers)!r},'wb').write(os.read(0,1024) if ready else b'')"
    )
    session = PTYSession()
    session.start(["python3", "-c", code], "/tmp", "dummy")
    try:
        with pytest.raises(RuntimeError, match="exited before task_complete"):
            session.wait_turn(5)
        received = answers.read_bytes()
        assert b"\x1b[1;1R" in received
        assert b"\x1b]10;rgb:" in received
    finally:
        session.close()


def test_bootstrap_trust_responds_only_to_exact_work_folder_prompt():
    read_fd, write_fd = os.pipe()
    session = PTYSession()
    session.fd = write_fd
    session.cwd = Path("/work/repo")
    try:
        session.output.extend(b"Trust this folder? /work/repo 1. Trust and continue")
        session._maybe_answer_bootstrap_trust()
        assert session.events == []  # incomplete frame: Enter would be too early
        session.output.extend(b" enter continue esc quit")
        session._maybe_answer_bootstrap_trust()
        session._maybe_answer_bootstrap_trust()
        assert os.read(read_fd, 2) == b"\r"
        assert session.events == [{"type": "bootstrap_trust", "scope": "/work"}]
    finally:
        os.close(read_fd)
        os.close(write_fd)

    read_fd, write_fd = os.pipe()
    session = PTYSession()
    session.fd = write_fd
    session.cwd = Path("/work/repo")
    try:
        session.output.extend(b"Approve command? 1. Trust and continue /work/repo")
        session._maybe_answer_bootstrap_trust()
        assert session.events == []
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_pty_no_response_timeout_and_cleanup():
    session = PTYSession()
    session.start(["python3", "-c", "import time; time.sleep(60)"], "/tmp", "dummy")
    pid = session.pid
    with pytest.raises(TimeoutError, match="no task_complete"):
        session.wait_turn(0.2)
    start = time.monotonic()
    session.close()
    assert time.monotonic() - start < 4
    assert session.pid is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_pty_close_kills_child_after_leader_exits(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    ready_file = tmp_path / "ready"
    child_code = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
        f"Path({str(ready_file)!r}).write_text('ready'); time.sleep(60)"
    )
    code = (
        "import subprocess,time; from pathlib import Path; "
        f"p=subprocess.Popen(['python3','-c',{child_code!r}]); "
        f"Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
        f"ready=Path({str(ready_file)!r}); "
        "[time.sleep(.01) for _ in range(300) if not ready.exists()]"
    )
    session = PTYSession()
    session.start(["python3", "-c", code], "/tmp", "dummy")
    try:
        with pytest.raises(RuntimeError, match="exited before task_complete"):
            session.wait_turn(5)
        child_pid = int(child_pid_file.read_text())
        os.kill(child_pid, 0)
        session.close()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        session.close()
        if child_pid_file.exists():
            try:
                os.kill(int(child_pid_file.read_text()), 9)
            except ProcessLookupError:
                pass


def test_claude_timed_out_turn_cancels_container_group(monkeypatch):
    from tests.e2e.conversation import ClaudeConversation

    class FakeProcess:
        pid = 12345

        def communicate(self, timeout=None):
            if timeout != 5:
                raise subprocess.TimeoutExpired("docker exec", timeout)
            return "", ""

    session = ClaudeConversation(object(), "claude-fable-5-1")
    session.process = FakeProcess()
    cancelled = []
    monkeypatch.setattr(session, "_cancel_container_group", lambda: cancelled.append(True))
    monkeypatch.setattr(os, "killpg", lambda *_: None)
    with pytest.raises(TimeoutError, match="no process/result boundary"):
        session.wait_turn(0.01)
    assert cancelled == [True]


def test_container_managed_exec_cancel_terminates_provider(tmp_path):
    marker = tmp_path / "turn.pid"
    wrapper = subprocess.Popen(
        [
            "python3",
            str(_server_path),
            "managed-exec",
            str(marker),
            "python3",
            "-c",
            "import time; time.sleep(60)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "managed provider did not publish its group handle"
        child_pid = int(marker.read_text())
        _module.cancel_managed_exec(str(marker))
        assert wrapper.wait(timeout=3) != 0
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            wrapper.wait(timeout=3)


def test_managed_exec_normal_completion_kills_term_resistant_descendant(tmp_path):
    marker = tmp_path / "normal-turn.pid"
    child_pid_file = tmp_path / "descendant.pid"
    ready_file = tmp_path / "descendant-ready"
    child_code = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(ready_file)!r}).write_text('ready'); time.sleep(60)"
    )
    provider_code = (
        "import subprocess,time; from pathlib import Path; "
        f"child=subprocess.Popen(['python3','-c',{child_code!r}]); "
        f"Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        f"ready=Path({str(ready_file)!r}); "
        "[time.sleep(.01) for _ in range(300) if not ready.exists()]"
    )
    wrapper = subprocess.Popen(
        [
            "python3",
            str(_server_path),
            "managed-exec",
            str(marker),
            "python3",
            "-c",
            provider_code,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _, stderr = wrapper.communicate(timeout=5)
        assert wrapper.returncode == 0, stderr
        child_pid = int(child_pid_file.read_text())
        assert not marker.exists(), "normal turn left a stale process-group handle"
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            wrapper.wait(timeout=3)
        if child_pid_file.exists():
            try:
                os.kill(int(child_pid_file.read_text()), 9)
            except ProcessLookupError:
                pass


def test_managed_group_probe_eperm_retries_until_group_disappears(monkeypatch):
    calls = []

    def killpg(_pgid, sig):
        calls.append(sig)
        if sig == 0 and calls.count(0) == 1:
            raise PermissionError(1, "transient macOS group probe")
        if sig == 0:
            raise ProcessLookupError(3, "group disappeared")

    monkeypatch.setattr(_module.os, "killpg", killpg)
    _module._stop_managed_group(12345)
    assert calls == [signal.SIGTERM, 0, 0]


def test_managed_group_eperm_does_not_hide_unsignalable_survivor(monkeypatch):
    calls = []

    def killpg(_pgid, sig):
        calls.append(sig)
        if sig == 0:
            raise PermissionError(1, "probe indeterminate")
        if sig == signal.SIGKILL:
            raise PermissionError(1, "termination denied")

    monkeypatch.setattr(_module.os, "killpg", killpg)
    with pytest.raises(PermissionError, match="termination denied"):
        _module._stop_managed_group(12345)
    assert signal.SIGKILL in calls


def test_auth_cache_values_selected_for_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "secret-access-token-value"}})
    )
    assert "secret-access-token-value" in _secret_values()


def test_e2e_image_override_requires_existing_image(monkeypatch):
    import docker as docker_sdk

    from tests.e2e.conftest import e2e_image

    class Images:
        def get(self, name):
            assert name == "omc-e2e:lifecycle"
            raise docker_sdk.errors.ImageNotFound("absent")

    monkeypatch.setenv("OMC_E2E_PREBUILT_IMAGE", "omc-e2e:lifecycle")
    monkeypatch.setenv("OMC_E2E_PREBUILT_SOURCE", "7a9eeff")
    monkeypatch.setenv("DOCKER_CONFIG", "/tmp")
    monkeypatch.setattr(docker_sdk, "from_env", lambda: type("Client", (), {"images": Images()})())
    with pytest.raises(pytest.fail.Exception, match="is absent"):
        next(e2e_image.__wrapped__())


def test_e2e_image_default_builds_checkout(monkeypatch):
    import testcontainers.core.image as image_module

    from tests.e2e.conftest import e2e_image

    seen = {}

    class FakeImage:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return "fresh-image"

        def __exit__(self, *_):
            return False

    monkeypatch.delenv("OMC_E2E_PREBUILT_IMAGE", raising=False)
    monkeypatch.setenv("DOCKER_CONFIG", "/tmp")
    monkeypatch.setattr(image_module, "DockerImage", FakeImage)
    gen = e2e_image.__wrapped__()
    assert next(gen) == "fresh-image"
    with pytest.raises(StopIteration):
        next(gen)
    assert seen["dockerfile_path"] == "docker/Dockerfile.e2e"


def test_codex_actor_policy_is_top_level_even_with_plugin_tables(tmp_path, monkeypatch):
    from tests.e2e import test_e2e_lifecycle

    config = tmp_path / "config.toml"
    config.write_text('[plugins."omc@oh-my-clanker"]\nenabled = true\n')

    def local_run(_container, argv):
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env={**os.environ, "CODEX_HOME": str(tmp_path)},
        )
        return result.returncode, result.stdout + result.stderr

    monkeypatch.setattr(test_e2e_lifecycle, "run_in", local_run)
    test_e2e_lifecycle._set_write_capability(object())
    parsed = tomllib.loads(config.read_text())
    assert parsed["approval_policy"] == "never"
    assert parsed["sandbox_mode"] == "danger-full-access"
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["plugins"]["omc@oh-my-clanker"] == {"enabled": True}


def test_codex_actor_policy_uses_default_home_without_codex_home(tmp_path, monkeypatch):
    from tests.e2e import test_e2e_lifecycle

    home = tmp_path / "disposable-home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    config = codex_home / "config.toml"
    config.write_text(
        'model = "existing-model"\n'
        '[plugins."omc@oh-my-clanker"]\nenabled = true\n'
        '[plugins."superpowers@superpowers-marketplace"]\nenabled = true\n'
    )

    def local_run(_container, argv):
        env = {**os.environ, "HOME": str(home)}
        env.pop("CODEX_HOME", None)
        result = subprocess.run(argv, capture_output=True, text=True, env=env)
        return result.returncode, result.stdout + result.stderr

    monkeypatch.setattr(test_e2e_lifecycle, "run_in", local_run)
    test_e2e_lifecycle._set_write_capability(object())
    parsed = tomllib.loads(config.read_text())
    assert parsed["approval_policy"] == "never"
    assert parsed["sandbox_mode"] == "danger-full-access"
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["model"] == "existing-model"
    assert parsed["plugins"] == {
        "omc@oh-my-clanker": {"enabled": True},
        "superpowers@superpowers-marketplace": {"enabled": True},
    }


def test_claude_stream_result_and_child_tool_records():
    from tests.e2e.conversation import parse_claude_stream

    records = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Task"}]}},
        {"type": "result", "result": "Finished", "session_id": "real-session-id"},
    ]
    parsed = parse_claude_stream("\n".join(json.dumps(row) for row in records) + "\n")
    assert parsed["text"] == "Finished"
    assert parsed["provider_session_id"] == "real-session-id"
    assert parsed["events"] == [{"type": "tool_use", "name": "Task"}]


def _claude_background_result_records():
    """Observed Claude stream shape after a background Agent completes."""
    return [
        {"type": "system", "subtype": "init", "session_id": "session-1"},
        {
            "type": "assistant",
            "session_id": "session-1",
            "message": {
                "model": "claude-fable-5-1",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Agent",
                        "id": "toolu-agent",
                        "input": {"run_in_background": True},
                    }
                ],
            },
        },
        {
            "type": "system",
            "subtype": "task_started",
            "session_id": "session-1",
            "task_id": "agent-task",
            "tool_use_id": "toolu-agent",
            "is_backgrounded": True,
        },
        {
            "type": "system",
            "subtype": "task_notification",
            "session_id": "session-1",
            "task_id": "agent-task",
            "tool_use_id": "toolu-agent",
            "status": "completed",
        },
        {"type": "system", "subtype": "init", "session_id": "session-1"},
        {
            "type": "assistant",
            "session_id": "session-1",
            "message": {"model": "claude-fable-5-1", "content": []},
        },
        {
            "type": "result",
            "result": "The requested turn answered",
            "session_id": "session-1",
            "result_index": 0,
            "uuid": "primary-result",
            "is_error": False,
        },
        {
            "type": "result",
            "result": "The background task finished",
            "session_id": "session-1",
            "result_index": 1,
            "uuid": "notification-result",
            "is_error": False,
            "origin": {"kind": "task-notification"},
        },
    ]


def test_claude_stream_keeps_requested_turn_when_background_notification_auto_resumes():
    from tests.e2e.conversation import parse_claude_stream

    records = _claude_background_result_records()
    parsed = parse_claude_stream("\n".join(json.dumps(row) for row in records) + "\n")
    assert parsed == {
        "text": "The requested turn answered",
        "provider_session_id": "session-1",
        "model_observed": "claude-fable-5-1",
        "events": [{"type": "tool_use", "name": "Agent"}],
    }


def test_claude_stream_rejects_reused_auxiliary_result_identity():
    from tests.e2e.conversation import parse_claude_stream

    records = _claude_background_result_records()
    records[1]["message"]["content"].append(
        {
            "type": "tool_use",
            "name": "Agent",
            "id": "toolu-agent-2",
            "input": {"run_in_background": True},
        }
    )
    records.insert(
        4,
        {
            "type": "system",
            "subtype": "task_notification",
            "session_id": "session-1",
            "task_id": "agent-task-2",
            "tool_use_id": "toolu-agent-2",
            "status": "completed",
        },
    )
    records.append({**records[-1], "result": "Another follow-up", "result_index": 2})

    with pytest.raises(ValueError, match="identity"):
        parse_claude_stream("\n".join(json.dumps(row) for row in records) + "\n")


@pytest.mark.parametrize(
    ("variant", "error"),
    [
        ("repeated-primary", "repeated"),
        ("duplicate-notification", "repeated"),
        ("error-notification", "error"),
        ("malformed-notification", "malformed"),
        ("cross-session", "session"),
        ("unknown-origin", "repeated"),
        ("missing-notification", "notification"),
        ("wrong-task", "notification"),
        ("out-of-order-index", "result_index"),
    ],
)
def test_claude_stream_rejects_invalid_followup_results(variant, error):
    from tests.e2e.conversation import parse_claude_stream

    records = _claude_background_result_records()
    followup = records[-1]
    if variant == "repeated-primary":
        del followup["origin"]
    elif variant == "duplicate-notification":
        records.append(followup.copy())
    elif variant == "error-notification":
        followup["is_error"] = True
    elif variant == "malformed-notification":
        followup["result"] = None
    elif variant == "cross-session":
        followup["session_id"] = "session-2"
    elif variant == "unknown-origin":
        followup["origin"] = {"kind": "other"}
    elif variant == "missing-notification":
        del records[3]
    elif variant == "wrong-task":
        records[3]["tool_use_id"] = "unrelated-tool"
    elif variant == "out-of-order-index":
        followup["result_index"] = 2

    with pytest.raises(ValueError, match=error):
        parse_claude_stream("\n".join(json.dumps(row) for row in records) + "\n")


def test_claude_stream_rejects_truncated_or_missing_result():
    from tests.e2e.conversation import parse_claude_stream

    with pytest.raises(ValueError, match="truncated"):
        parse_claude_stream('{"type":"result"')
    with pytest.raises(ValueError, match="result event"):
        parse_claude_stream(json.dumps({"type": "assistant", "message": {"content": []}}) + "\n")


def test_published_tree_requires_committed_fix_and_mutation_sensitive_unittest(
    tmp_path, monkeypatch
):
    from tests.e2e import test_e2e_lifecycle as lifecycle

    bare = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("user.email", "test@example.test"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    old_source = "def greeting():\n    return 'Goodbye, world!'\n"
    (repo / "greeting.py").write_text(old_source)
    (repo / "test_greeting.py").write_text(
        "import unittest\nfrom greeting import greeting\n\n"
        "class TestGreeting(unittest.TestCase):\n"
        "    def test_is_text(self): self.assertIsInstance(greeting(), str)\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "switch", "-qc", "feature/fix"], check=True)
    (repo / "README.md").write_text("unrelated\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "unrelated"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "feature/fix"], check=True)
    (repo / "greeting.py").write_text("def greeting():\n    return 'Hello, world!'\n")

    def local_run(_container, argv, *, cwd=None, **_):
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

    monkeypatch.setattr(lifecycle, "run_in", local_run)
    with pytest.raises(AssertionError):
        lifecycle._assert_published_fix(None, str(repo), str(repo), "feature/fix", old_source)

    (repo / "test_greeting.py").write_text(
        "import unittest\nfrom greeting import greeting\n\n"
        "class TestGreeting(unittest.TestCase):\n"
        "    def test_exact(self): self.assertEqual(greeting(), 'Hello, world!')\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "greeting.py", "test_greeting.py"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--amend", "-qm", "fix", "-m", "Exact value regression"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "push", "-qf", "origin", "feature/fix"], check=True)
    lifecycle._assert_published_fix(None, str(repo), str(repo), "feature/fix", old_source)
    (repo / "greeting.py").write_text("def greeting():\n    return 'Hello, world!'\n\n")
    with pytest.raises(AssertionError, match="greeting.py differs"):
        lifecycle._assert_published_fix(None, str(repo), str(repo), "feature/fix", old_source)
    (repo / "greeting.py").write_text("def greeting():\n    return 'Hello, world!'\n")
    (repo / "test_greeting.py").write_text(
        "import unittest\nfrom greeting import greeting\n\n"
        "class TestGreeting(unittest.TestCase):\n"
        "    def test_is_text(self): self.assertIsInstance(greeting(), str)\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "test_greeting.py"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--amend", "-qm", "smoke only", "-m", "No exact value"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "push", "-qf", "origin", "feature/fix"], check=True)
    with pytest.raises(AssertionError, match="regression"):
        lifecycle._assert_published_fix(None, str(repo), str(repo), "feature/fix", old_source)


def test_worktree_branch_is_normalized_for_remote_checkout(monkeypatch):
    from tests.e2e import test_e2e_lifecycle as lifecycle

    listing = (
        "worktree /work/lifecycle\nbranch refs/heads/main\n\n"
        "worktree /work/lifecycle.feature-fix\nbranch refs/heads/feature/fix\n"
    )
    monkeypatch.setattr(lifecycle, "run_in", lambda *_: (0, listing))
    assert lifecycle._worktree(None, "/work/lifecycle") == (
        "/work/lifecycle.feature-fix",
        "feature/fix",
    )
