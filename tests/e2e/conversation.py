"""Host bridge for real provider conversations in disposable Docker E2E containers."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

ARTIFACTS = Path(__file__).parent / "scenario-artifacts"


def _copy_server(container_id: str) -> None:
    server = Path(__file__).resolve().parents[2] / "docker" / "conversation.py"
    subprocess.run(
        ["docker", "cp", str(server), f"{container_id}:/tmp/omc-conversation.py"],
        check=True,
        capture_output=True,
    )


class Conversation:
    def __init__(self, container):
        self.container = container
        self.process = None
        self.responses: queue.Queue[str | None] = queue.Queue()
        self.stderr: list[str] = []

    def __enter__(self):
        container_id = self.container.get_wrapped_container().id
        _copy_server(container_id)
        self.process = subprocess.Popen(
            ["docker", "exec", "-i", container_id, "python3", "-u", "/tmp/omc-conversation.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def _read_stdout(self):
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def _read_stderr(self):
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self.stderr.append(line[-2000:])
            if len(self.stderr) > 20:
                self.stderr.pop(0)

    def _request(self, action: str, *, response_timeout=30, **kwargs):
        assert self.process and self.process.stdin
        if self.process.poll() is not None:
            detail = "".join(self.stderr)[-1000:]
            raise RuntimeError(f"conversation server exited {self.process.returncode}: {detail}")
        self.process.stdin.write(json.dumps({"action": action, **kwargs}) + "\n")
        self.process.stdin.flush()
        try:
            line = self.responses.get(timeout=response_timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"conversation server did not answer {action} within {response_timeout}s"
            ) from exc
        if line is None:
            raise RuntimeError(
                f"conversation server exited during {action}: {''.join(self.stderr)[-1000:]}"
            )
        answer = json.loads(line)
        if not answer.get("ok"):
            raise RuntimeError(
                f"conversation {action}: {answer.get('error')}: {answer.get('detail')}"
            )
        return answer["result"]

    def start(self, argv: list[str], cwd: str, provider: str, *, skill_paths=None):
        result = self._request(
            "start", argv=argv, cwd=cwd, provider=provider, skill_paths=skill_paths or {}
        )
        self.provider = provider
        return result

    def send(self, message: str):
        result = self._request("send", message=message)
        if self.provider == "codex":
            self.wait_started()
        return result

    def wait_turn(self, timeout: float = 600):
        return self._request("wait_turn", timeout=timeout, response_timeout=timeout + 15)

    def wait_started(self, timeout: float = 12):
        return self._request("wait_started", timeout=timeout, response_timeout=timeout + 5)

    def events(self):
        return self._request("events")

    def snapshot(self, repo: str):
        return self._request("snapshot", repo=repo)

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self._request("close", response_timeout=5)
            finally:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)

    def __exit__(self, *_):
        self.close()


def parse_claude_stream(output: str, skill_paths: dict[str, str] | None = None) -> dict:
    """Require Claude's requested-turn result; retain only tool names and model ID."""
    events = []
    result = None
    model = None
    pending_reads = {}
    background_agents = {}
    completed_background_tasks = set()
    notification_results = 0
    result_uuids = set()
    skill_paths = skill_paths or {}
    lines = output.splitlines(keepends=True)
    for line in lines:
        if not line.strip():
            continue
        if not line.endswith("\n"):
            raise ValueError("truncated Claude stream JSONL record")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed Claude stream JSONL record") from exc
        if not isinstance(record, dict):
            raise ValueError("Claude stream record must be an object")
        if record.get("type") == "system":
            if record.get("subtype") == "task_notification" and record.get("status") == "completed":
                tool_id = record.get("tool_use_id")
                task_id = record.get("task_id")
                session_id = record.get("session_id")
                if (
                    isinstance(tool_id, str)
                    and isinstance(task_id, str)
                    and isinstance(session_id, str)
                    and background_agents.get(tool_id) == session_id
                ):
                    completed_background_tasks.add((session_id, task_id))
        elif record.get("type") == "assistant":
            message = record.get("message", {})
            if isinstance(message, dict):
                if isinstance(message.get("model"), str):
                    model = message["model"]
                for block in message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        events.append({"type": "tool_use", "name": block.get("name")})
                        if (
                            block.get("name") in ("Agent", "Task")
                            and isinstance(block.get("input"), dict)
                            and block["input"].get("run_in_background") is True
                            and isinstance(block.get("id"), str)
                            and isinstance(record.get("session_id"), str)
                        ):
                            background_agents[block["id"]] = record["session_id"]
                        if block.get("name") == "Read" and isinstance(block.get("input"), dict):
                            for skill, path in skill_paths.items():
                                if block["input"].get("file_path") == path:
                                    pending_reads[block.get("id")] = (skill, path)
        elif record.get("type") == "user":
            message = record.get("message", {})
            if isinstance(message, dict):
                for block in message.get("content", []):
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    read = pending_reads.pop(block.get("tool_use_id"), None)
                    if read is None or block.get("is_error"):
                        continue
                    skill, path = read
                    content = block.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(
                            part.get("text", "") for part in content if isinstance(part, dict)
                        )
                    if isinstance(content, str) and f"name: {skill.split(':', 1)[1]}" in content:
                        events.append({"type": "skill_read", "skill": skill, "path": path})
        elif record.get("type") == "result":
            if record.get("is_error"):
                raise ValueError("Claude result event reports an error")
            if not isinstance(record.get("result"), str) or not isinstance(
                record.get("session_id"), str
            ):
                raise ValueError("Claude result event is malformed")
            if result is None:
                if record.get("origin") is not None:
                    raise ValueError("Claude stream has no requested-turn result event")
                result = record
                if isinstance(record.get("uuid"), str):
                    result_uuids.add(record["uuid"])
            elif record.get("origin") == {"kind": "task-notification"}:
                session_id = record["session_id"]
                if session_id != result["session_id"]:
                    raise ValueError("Claude notification result changed session identity")
                if notification_results >= sum(
                    task_session == session_id for task_session, _ in completed_background_tasks
                ):
                    raise ValueError(
                        "repeated Claude notification result or no completed background task"
                    )
                primary_index = result.get("result_index")
                if (
                    type(primary_index) is not int
                    or type(record.get("result_index")) is not int
                    or record["result_index"] != primary_index + notification_results + 1
                ):
                    raise ValueError("Claude notification result_index is out of order")
                if (
                    not isinstance(result.get("uuid"), str)
                    or not isinstance(record.get("uuid"), str)
                    or record["uuid"] in result_uuids
                ):
                    raise ValueError("Claude notification result has repeated identity")
                result_uuids.add(record["uuid"])
                notification_results += 1
            else:
                raise ValueError("repeated Claude result event")
    if result is None:
        raise ValueError("Claude stream missing result event")
    return {
        "text": result["result"],
        "provider_session_id": result["session_id"],
        "model_observed": model,
        "events": events,
    }


class ClaudeConversation:
    """Claude's supported headless named-session interface across real turns."""

    def __init__(self, container, model: str):
        self.container = container
        self.model = model
        self.container_id = None
        self.process = None
        self.repo = None
        self.worktree = None
        self.slug = None
        self.provider_session_id = None
        self.first = True
        self.all_events = []
        self.turn_marker = None

    def __enter__(self):
        self.container_id = self.container.get_wrapped_container().id
        _copy_server(self.container_id)
        return self

    def _launch(self, argv: list[str], cwd: str):
        assert self.container_id
        if self.process is not None:
            raise RuntimeError("previous Claude turn has not completed")
        self.turn_marker = f"/tmp/omc-claude-turn-{uuid.uuid4().hex}.pid"
        self.process = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                "IS_SANDBOX=1",
                "-w",
                cwd,
                self.container_id,
                "python3",
                "-u",
                "/tmp/omc-conversation.py",
                "managed-exec",
                self.turn_marker,
                *argv,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            found = subprocess.run(
                ["docker", "exec", self.container_id, "test", "-f", self.turn_marker],
                capture_output=True,
                timeout=5,
            )
            if found.returncode == 0 or self.process.poll() is not None:
                return
            time.sleep(0.1)
        self._cancel_container_group()
        raise TimeoutError("Claude container group handle was not created within 10s")

    def _cancel_container_group(self):
        if not self.container_id or not self.turn_marker:
            return
        subprocess.run(
            [
                "docker",
                "exec",
                self.container_id,
                "python3",
                "/tmp/omc-conversation.py",
                "cancel-exec",
                self.turn_marker,
            ],
            capture_output=True,
            timeout=10,
            check=True,
        )

    def start(self, argv: list[str], cwd: str, provider: str):
        if provider != "claude" or argv[:2] != ["omc", "start"] or "--headless" not in argv:
            raise ValueError("Claude start requires the real omc start --headless launch")
        self.repo = cwd
        self._launch(argv, cwd)

    def _discover_worktree(self):
        assert self.container_id and self.repo
        listed = subprocess.run(
            [
                "docker",
                "exec",
                self.container_id,
                "git",
                "-C",
                self.repo,
                "worktree",
                "list",
                "--porcelain",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for block in listed.split("\n\n"):
            if "branch refs/heads/feature/" in block:
                self.worktree = next(
                    line.split(" ", 1)[1]
                    for line in block.splitlines()
                    if line.startswith("worktree ")
                )
                branch = next(
                    line.split(" ", 1)[1]
                    for line in block.splitlines()
                    if line.startswith("branch ")
                )
                self.slug = branch.split("feature/", 1)[1]
                return
        raise RuntimeError(f"Claude omc start created no feature worktree: {listed}")

    def send(self, message: str):
        if not self.slug or not self.worktree:
            raise RuntimeError("Claude named session is not ready to resume")
        self._launch(
            [
                "claude",
                "--resume",
                self.slug,
                "-p",
                message,
                "--output-format",
                "stream-json",
                "--verbose",
                "--dangerously-skip-permissions",
                "--model",
                self.model,
            ],
            self.worktree,
        )

    def wait_turn(self, timeout: float = 600):
        stdout, _ = self._finish_process(timeout)
        if self.first:
            self.first = False
            self._discover_worktree()
            assert self.slug
            turn = {
                "text": stdout.strip(),
                "session_id": self.slug,
                "provider_session_id": None,
                "events": [{"type": "process_complete"}],
                "model_observed": None,
            }
        else:
            parsed = parse_claude_stream(stdout)
            actual = parsed["provider_session_id"]
            if parsed["model_observed"] != self.model:
                raise ValueError(
                    f"Claude resumed with model {parsed['model_observed']}, requested {self.model}"
                )
            if self.provider_session_id and actual != self.provider_session_id:
                raise ValueError("Claude resume changed provider session identity")
            self.provider_session_id = actual
            turn = {**parsed, "session_id": self.slug}
        self.all_events.extend(turn["events"])
        return turn

    def _finish_process(self, timeout: float):
        if self.process is None:
            raise RuntimeError("no Claude turn is running")
        process, self.process = self.process, None
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                self._cancel_container_group()
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)
            raise TimeoutError(
                f"Claude produced no process/result boundary within {timeout}s"
            ) from exc
        finally:
            self.turn_marker = None
        if process.returncode:
            raise RuntimeError(f"Claude turn exited {process.returncode}: {stderr[-1200:]}")
        return stdout, stderr

    def run_raw(self, argv: list[str], cwd: str, timeout: float):
        """Run a capability turn with the same container-side cancellation."""
        self._launch(argv, cwd)
        stdout, _ = self._finish_process(timeout)
        return stdout

    def events(self):
        return {
            "session_id": self.slug,
            "provider_session_id": self.provider_session_id,
            "events": self.all_events.copy(),
        }

    def snapshot(self, repo: str):
        assert self.container_id
        script = (
            "import json,runpy,sys; from pathlib import Path; "
            "snapshot=runpy.run_path('/tmp/omc-conversation.py')['snapshot_repo']; "
            "print(json.dumps(snapshot(Path(sys.argv[1]))))"
        )
        result = subprocess.run(
            ["docker", "exec", self.container_id, "python3", "-c", script, repo],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self._cancel_container_group()
            finally:
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.process = None
        self.turn_marker = None

    def __exit__(self, *_):
        self.close()


def save_evidence(name: str, evidence: dict) -> Path:
    """Export one sanitized artifact per run, never provider homes."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"{name}-{uuid.uuid4().hex}.json"
    secrets = [v for key, v in os.environ.items() if ("KEY" in key or "TOKEN" in key) and v]
    rendered = json.dumps(evidence, indent=2, ensure_ascii=False)
    for secret in secrets:
        rendered = rendered.replace(secret, "[redacted]")
    with path.open("x") as artifact:
        artifact.write(rendered + "\n")
    return path
