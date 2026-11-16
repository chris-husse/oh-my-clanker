"""The Codex long-poll E2E counts its wait calls in the session's own rollout only."""

import json
import subprocess
import sys

from tests.e2e import harness

from . import _stagekit

_SESSION = "0199aaaa-bbbb-cccc-dddd-eeeeffff0001"


def _call(command: str) -> dict:
    arguments = json.dumps({"cmd": command, "timeout_ms": 600000})
    payload = {"type": "function_call", "name": "exec_command", "arguments": arguments}
    return {"type": "response_item", "payload": payload}


def _rollout(path, session, *records, tail=""):
    meta = {"type": "session_meta", "payload": {"id": session}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in (meta, *records)) + tail)


def test_counts_wait_calls_in_the_sessions_own_rollout_only(tmp_path):
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "11" / "16"
    wait = harness.STAGE_WAIT
    _rollout(
        day / "rollout-a.jsonl",
        _SESSION,
        _call("omc internal stage start check"),
        _call(wait),
        _call(f"cd /work/stage-poll && {wait}"),
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    )
    # Another session: its calls never count, and its body is never parsed.
    _rollout(day / "rollout-b.jsonl", "other-session", _call(wait), tail="not json\n")
    done = subprocess.run(
        [sys.executable, "-c", harness.COUNT_CALLS, _SESSION, wait],
        env={**_stagekit.hermetic_env(), "CODEX_HOME": str(home)},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "2"
