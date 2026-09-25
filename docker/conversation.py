"""Test-only PTY/JSONL conversation server, run inside a disposable E2E container.

stdin/stdout carry one JSON command/response per line. Provider TUI output stays
on the PTY and is continuously drained; completion comes from persisted events.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from collections import deque
from pathlib import Path


class EventTail:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.pending = b""

    def read(self) -> list[dict]:
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
        except FileNotFoundError:
            return []
        self.pending += chunk
        lines = self.pending.split(b"\n")
        self.pending = lines.pop()
        records = []
        for line in lines:
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"malformed complete JSONL record in {self.path.name}") from exc
            if not isinstance(obj, dict):
                raise ValueError("session JSONL record must be an object")
            records.append(obj)
        return records


class TurnTracker:
    def __init__(self):
        self.started = None
        self.ever_started = False
        self.completed = None
        self.events: list[dict] = []
        self.seen_complete: set[str] = set()

    def next_turn(self):
        self.started = None
        self.completed = None
        self.events = []

    def feed(self, event: dict):
        if event.get("type") != "event_msg":
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("event_msg payload is malformed")
        kind = payload.get("type")
        if kind not in ("task_started", "task_complete"):
            return
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError(f"{kind} event missing turn_id")
        if kind == "task_started":
            self.ever_started = True
            if self.started is None:
                self.started = turn_id
                self.events.append({"type": kind, "turn_id": turn_id})
        elif turn_id == self.started and turn_id not in self.seen_complete:
            message = payload.get("last_agent_message")
            if not isinstance(message, str):
                raise ValueError("task_complete missing last_agent_message")
            self.completed = message
            self.seen_complete.add(turn_id)
            self.events.append({"type": kind, "turn_id": turn_id})


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE
    ).stdout


def snapshot_repo(repo: Path) -> dict:
    """Represent product bytes, staged entries, HEAD, docs and bare remote refs."""
    repo = Path(repo)
    source = {}
    docs = {}
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or ".git" in path.parts or ".gitnexus" in path.parts:
            continue
        rel = path.relative_to(repo).as_posix()
        if rel.startswith(".omc/") or rel.startswith(".superpowers/"):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if rel.startswith("docs/superpowers/"):
            docs[rel] = digest
        elif rel.endswith((".py", ".sh", ".js", ".ts", ".md")):
            source[rel] = digest
    remote = _git(repo, "remote", "get-url", "origin").decode().strip()
    remote_refs = (
        subprocess.run(
            [
                "git",
                "-C",
                remote,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                "refs/heads",
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        .stdout.decode()
        .splitlines()
    )
    return {
        "source": source,
        "index": _git(repo, "ls-files", "-s").decode(),
        "head": _git(repo, "rev-parse", "HEAD").decode().strip(),
        "remote_refs": remote_refs,
        "spec_plan": docs,
    }


def _sanitized_event(event: dict) -> dict | None:
    kind = event.get("type")
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return None
    if kind == "event_msg" and payload.get("type") in ("task_started", "task_complete"):
        return {"type": payload["type"], "turn_id": payload.get("turn_id")}
    if kind == "response_item" and payload.get("type") == "function_call":
        return {
            "type": "function_call",
            "name": payload.get("name"),
            "namespace": payload.get("namespace"),
        }
    if kind == "turn_context" and isinstance(payload.get("model"), str):
        sandbox = payload.get("sandbox_policy")
        return {
            "type": "turn_context",
            "model": payload["model"],
            "approval_policy": payload.get("approval_policy"),
            "sandbox_policy": sandbox.get("type") if isinstance(sandbox, dict) else sandbox,
        }
    return None


class SkillReadTracker:
    """Emit path-only evidence after a successful tool read of installed skills."""

    def __init__(self, paths: dict[str, str]):
        self.paths = paths
        self.pending: dict[str, tuple[str, str]] = {}
        self.events: list[dict] = []

    def feed(self, event: dict) -> None:
        if event.get("type") != "response_item":
            return
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            return
        if kind == "function_call":
            arguments = payload.get("arguments", "")
            if not isinstance(arguments, str) or not re.search(
                r"\b(cat|sed|head|read_text)\b", arguments
            ):
                return
            for skill, path in self.paths.items():
                if path in arguments:
                    self.pending[call_id] = (skill, path)
        elif kind == "function_call_output" and call_id in self.pending:
            skill, path = self.pending.pop(call_id)
            output = payload.get("output", "")
            expected_name = skill.split(":", 1)[1]
            if (
                isinstance(output, str)
                and f"name: {expected_name}" in output
                and ("Process exited with code 0" in output or "Script completed" in output)
            ):
                self.events.append({"type": "skill_read", "skill": skill, "path": path})


def _secret_values() -> list[str]:
    """Read the disposable container's selected auth, for redaction only."""
    values = [
        value
        for key, value in os.environ.items()
        if ("KEY" in key or "TOKEN" in key) and len(value) > 12
    ]
    auth_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    try:
        auth = json.loads(auth_path.read_text())
    except (OSError, ValueError):
        return values

    def visit(value):
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and len(value) > 12:
            values.append(value)

    visit(auth)
    return values


class PTYSession:
    def __init__(self):
        self.pid: int | None = None
        self.pgid: int | None = None
        self.fd: int | None = None
        self.reader: threading.Thread | None = None
        self.output = deque(maxlen=4000)
        self.tail: EventTail | None = None
        self.tracker = TurnTracker()
        self.session_id: str | None = None
        self.events: list[dict] = []
        self.turn_event_start_index = 0
        self.home: Path | None = None
        self.cwd: Path | None = None
        self.provider: str | None = None
        self.existing: set[Path] = set()
        self.debug_bytes = 0
        self.trust_answered = False
        self.skill_reads = SkillReadTracker({})
        self.child_tails: dict[Path, EventTail] = {}
        self.child_read_trackers: dict[Path, SkillReadTracker] = {}
        self.child_ids: set[str] = set()
        self.skill_watch_fd: int | None = None
        self.skill_watch_descriptors: dict[int, tuple[str, str]] = {}
        self.seen_skill_access: set[str] = set()

    def start(self, argv: list[str], cwd: str, provider: str, skill_paths=None):
        if self.pid is not None:
            raise RuntimeError("session already started")
        self.provider = provider
        self.skill_reads = SkillReadTracker(skill_paths or {})
        if provider == "codex" and skill_paths:
            self._start_skill_access_observer(skill_paths)
        self.cwd = Path(cwd)
        self.home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        self.existing = set(self.home.glob("sessions/**/*.jsonl"))
        pid, fd = pty.fork()
        if pid == 0:
            # pty.fork's default Linux window can be 0×0. The real Docker
            # exec -it probe had a nonzero window; Codex TUI needs one.
            fcntl.ioctl(1, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.environ.setdefault("COLUMNS", "120")
            os.environ.setdefault("LINES", "40")
            os.environ["SHELL"] = "/bin/bash"
            os.execvp(argv[0], argv)
        self.pid, self.pgid, self.fd = pid, pid, fd
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        return {"pid": pid}

    def _drain(self):
        assert self.fd is not None
        fd = self.fd
        query_tail = b""
        while self.fd == fd:
            try:
                ready, _, _ = select.select([fd], [], [], 0.25)
                if not ready:
                    continue
                data = os.read(fd, 65536)
                if not data:
                    break
                self.output.extend(data[-4000:])
                # Container-local startup diagnostics. Never exported as a
                # scenario artifact; tests copy only selected/redacted records.
                if self.debug_bytes < 131072:
                    chunk = data[: 131072 - self.debug_bytes]
                    with open("/tmp/omc-tui-initial.raw", "ab") as diagnostic:
                        diagnostic.write(chunk)
                    self.debug_bytes += len(chunk)
                combined = query_tail + data
                for query, answer in (
                    (b"\x1b[6n", b"\x1b[1;1R"),
                    (b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
                    (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
                    (b"\x1b[c", b"\x1b[?1;2c"),
                    (b"\x1b[?u", b"\x1b[?0u"),
                ):
                    start = 0
                    while (at := combined.find(query, start)) != -1:
                        if at + len(query) > len(query_tail):
                            os.write(fd, answer)
                        start = at + len(query)
                query_tail = combined[-16:]
            except (OSError, ValueError):
                break

    def _root_session(self):
        assert self.home is not None
        for path in sorted(set(self.home.glob("sessions/**/*.jsonl")) - self.existing):
            try:
                with path.open("rb") as stream:
                    first = stream.readline()
                record = json.loads(first)
            except (OSError, ValueError):
                continue
            if record.get("type") != "session_meta":
                continue
            meta = record.get("payload", {})
            if (
                self.provider == "codex"
                and meta.get("originator") == "codex-tui"
                and meta.get("source") == "cli"
            ):
                self.session_id = str(meta.get("id", path.stem))
                self.tail = EventTail(path)
                return

    def _poll(self):
        self._maybe_answer_bootstrap_trust()
        self._poll_skill_access()
        if self.tail is None:
            self._root_session()
        if self.tail is None:
            return
        for event in self.tail.read():
            self.tracker.feed(event)
            prior_reads = len(self.skill_reads.events)
            self.skill_reads.feed(event)
            self.events.extend(self.skill_reads.events[prior_reads:])
            sanitized = _sanitized_event(event)
            if sanitized:
                self.events.append(sanitized)
        self._poll_child_reads()

    def _start_skill_access_observer(self, paths):
        """Watch exact installed files after setup, before the actor starts."""
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            init = libc.inotify_init1
            add = libc.inotify_add_watch
        except AttributeError as exc:
            raise RuntimeError("container lacks inotify for installed-skill readiness") from exc
        init.argtypes = [ctypes.c_int]
        init.restype = ctypes.c_int
        add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add.restype = ctypes.c_int
        fd = init(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.skill_watch_fd = fd
        try:
            for skill, path in paths.items():
                wd = add(fd, path.encode(), 0x01 | 0x20)  # IN_ACCESS | IN_OPEN
                if wd < 0:
                    raise OSError(ctypes.get_errno(), f"cannot watch installed {skill}")
                self.skill_watch_descriptors[wd] = (skill, path)
        except Exception:
            os.close(fd)
            self.skill_watch_fd = None
            raise

    def _consume_skill_access_records(self, blob: bytes):
        offset = 0
        while offset + 16 <= len(blob):
            wd, mask, _, name_length = struct.unpack_from("iIII", blob, offset)
            offset += 16 + name_length
            if not mask & 0x01 or wd not in self.skill_watch_descriptors:
                continue
            skill, path = self.skill_watch_descriptors[wd]
            if skill not in self.seen_skill_access:
                self.seen_skill_access.add(skill)
                self.events.append(
                    {"type": "skill_read", "skill": skill, "path": path, "evidence": "file_access"}
                )

    def _poll_skill_access(self):
        if self.skill_watch_fd is None:
            return
        while True:
            try:
                blob = os.read(self.skill_watch_fd, 65536)
            except BlockingIOError:
                return
            if not blob:
                return
            self._consume_skill_access_records(blob)

    def _poll_child_reads(self):
        """Follow only sessions whose recorded parent is this root or its child."""
        if self.home is None or self.session_id is None:
            return
        candidates = set(self.home.glob("sessions/**/*.jsonl")) - self.existing
        parents = {self.session_id} | self.child_ids
        for _ in range(len(candidates)):
            added = False
            for path in candidates - self.child_tails.keys():
                try:
                    with path.open("rb") as stream:
                        meta = json.loads(stream.readline())
                except (OSError, ValueError):
                    continue
                source = meta.get("payload", {}).get("source", {})
                if not isinstance(source, dict):
                    continue
                spawn = source.get("subagent", {}).get("thread_spawn", {})
                if not isinstance(spawn, dict) or spawn.get("parent_thread_id") not in parents:
                    continue
                child_id = meta.get("payload", {}).get("id")
                if not isinstance(child_id, str):
                    continue
                self.child_ids.add(child_id)
                parents.add(child_id)
                self.child_tails[path] = EventTail(path)
                self.child_read_trackers[path] = SkillReadTracker(self.skill_reads.paths)
                added = True
            if not added:
                break
        for path, tail in self.child_tails.items():
            tracker = self.child_read_trackers[path]
            for event in tail.read():
                previous = len(tracker.events)
                tracker.feed(event)
                self.events.extend(tracker.events[previous:])

    def _maybe_answer_bootstrap_trust(self):
        """Acknowledge only Codex's explicit disposable /work folder trust UI."""
        if self.trust_answered or self.fd is None or self.tracker.ever_started:
            return
        screen = bytes(self.output).decode(errors="replace")
        expected = str(self.cwd) if self.cwd is not None else ""
        if (
            expected.startswith("/work/")
            and expected in screen
            and "Trust this folder?" in screen
            and "Trust and continue" in screen
            and "enter" in screen
            and "esc" in screen
            and "quit" in screen
        ):
            os.write(self.fd, b"\r")
            self.trust_answered = True
            self.events.append({"type": "bootstrap_trust", "scope": "/work"})

    def wait_turn(self, timeout: float):
        if timeout <= 0 or timeout > 1800:
            raise ValueError("turn timeout must be in (0, 1800]")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._poll()
            if self.tracker.completed is not None:
                return {
                    "text": self.tracker.completed,
                    "turn_id": self.tracker.started,
                    "session_id": self.session_id,
                    "events": self.events[self.turn_event_start_index :],
                    "turn_events": self.tracker.events.copy(),
                    "terminal_tail": bytes(self.output).decode(errors="replace")[-1500:],
                }
            if self.pid is not None:
                status_pid, status = os.waitpid(self.pid, os.WNOHANG)
                if status_pid:
                    self.pid = None
                    tail = bytes(self.output).decode(errors="replace")[-800:]
                    raise RuntimeError(
                        f"TUI exited before task_complete (status {status}); tail: {tail}"
                    )
            time.sleep(0.1)
        tail = bytes(self.output).decode(errors="replace")[-800:]
        raise TimeoutError(
            f"no task_complete within {timeout}s; session={self.session_id}; tail={tail}"
        )

    def wait_started(self, timeout: float):
        """Acknowledge input separately from the potentially long model turn."""
        if timeout <= 0 or timeout > 60:
            raise ValueError("submission timeout must be in (0, 60]")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._poll()
            if self.tracker.started is not None:
                return self.tracker.started
            time.sleep(0.05)
        tail = bytes(self.output).decode(errors="replace")[-3000:]
        raise TimeoutError(f"no task_started within {timeout}s; tail={tail}")

    def send(self, message: str):
        if self.fd is None or self.pid is None:
            raise RuntimeError("TUI is not running")
        self.tracker.next_turn()
        self.turn_event_start_index = len(self.events)
        data = message.encode()
        # Codex TUI recognizes bracketed paste; separate Enter from the paste
        # event. Sending text+CR in one burst was observed to leave a draft.
        os.write(self.fd, b"\x1b[200~" + data + b"\x1b[201~")
        time.sleep(0.3)
        os.write(self.fd, b"\r")
        if message.startswith("$omc:") and " " not in message:
            # Bare Codex skill mentions open a completion menu. The first
            # Enter inserts the skill; the second submits that inserted token.
            time.sleep(0.3)
            os.write(self.fd, b"\r")
        return {"sent": True}

    def close(self):
        pid, self.pid = self.pid, None
        pgid, self.pgid = self.pgid, None
        fd, self.fd = self.fd, None
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if pid is not None:
                    try:
                        waited, _ = os.waitpid(pid, os.WNOHANG)
                        if waited:
                            pid = None
                    except ChildProcessError:
                        pid = None
                try:
                    os.killpg(pgid, 0)
                except (ProcessLookupError, PermissionError):
                    break
                time.sleep(0.05)
            else:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if pid is not None:
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
        if fd is not None:
            os.close(fd)
        if self.reader is not None:
            self.reader.join(timeout=1)
        if self.skill_watch_fd is not None:
            os.close(self.skill_watch_fd)
            self.skill_watch_fd = None
        return {"closed": True}


def main():
    session = PTYSession()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            action = request["action"]
            if action == "start":
                result = session.start(
                    request["argv"],
                    request["cwd"],
                    request["provider"],
                    request.get("skill_paths"),
                )
            elif action == "send":
                result = session.send(request["message"])
            elif action == "wait_turn":
                result = session.wait_turn(request["timeout"])
            elif action == "wait_started":
                result = session.wait_started(request["timeout"])
            elif action == "events":
                session._poll()
                result = {"session_id": session.session_id, "events": session.events}
            elif action == "snapshot":
                result = snapshot_repo(Path(request["repo"]))
            elif action == "close":
                result = session.close()
            else:
                raise ValueError(f"unknown action {action}")
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
        rendered = json.dumps(response, ensure_ascii=False)
        for secret in _secret_values():
            rendered = rendered.replace(secret, "[redacted]")
        print(rendered, flush=True)
    session.close()


def managed_exec(marker: str, argv: list[str]) -> int:
    """Give the host a container-side group handle for a Claude Docker turn."""
    marker_path = Path(marker)
    child = subprocess.Popen(argv, start_new_session=True)
    pending_marker = marker_path.with_suffix(".tmp")
    pending_marker.write_text(str(child.pid))
    pending_marker.replace(marker_path)
    try:
        return child.wait()
    finally:
        _stop_managed_group(child.pid)
        marker_path.unlink(missing_ok=True)


def _stop_managed_group(pgid: int) -> None:
    """Bound descendant cleanup after either normal completion or cancellation."""
    for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                # Darwin can report EPERM briefly after a descendant exits,
                # then ESRCH on the next probe. A zero-signal probe is only
                # status information; keep polling and escalate if needed.
                # Permission errors from TERM/KILL still propagate.
                pass
            time.sleep(0.05)


def cancel_managed_exec(marker: str) -> int:
    marker_path = Path(marker)
    try:
        pgid = int(marker_path.read_text())
    except (FileNotFoundError, ValueError):
        return 0
    _stop_managed_group(pgid)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "managed-exec":
        raise SystemExit(managed_exec(sys.argv[2], sys.argv[3:]))
    if len(sys.argv) == 3 and sys.argv[1] == "cancel-exec":
        raise SystemExit(cancel_managed_exec(sys.argv[2]))
    main()
