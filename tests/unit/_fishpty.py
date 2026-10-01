"""Type commands at a real `fish -i` prompt under a PTY; prompts are counted via OMC_PROMPT_LOG."""

from __future__ import annotations

import os
import pty
import select
import signal
import time
from pathlib import Path


def drive_fish(
    fish: str,
    env: dict[str, str],
    commands: list[str],
    prompt_log: Path,
    *,
    cwd: Path | None = None,
    timeout: float = 20,
) -> str:
    """env must make fish_prompt append one line to $OMC_PROMPT_LOG per prompt."""
    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - child
        if cwd is not None:
            os.chdir(cwd)
        os.execve(fish, [fish, "-i"], env)
    transcript = bytearray()

    def prompts() -> int:
        return len(prompt_log.read_text().splitlines()) if prompt_log.exists() else 0

    try:
        expected = 1
        for command in [*commands, "exit"]:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and prompts() < expected:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        transcript.extend(os.read(master, 65536))
                    except OSError:
                        break
            assert prompts() >= expected, (
                f"prompt {expected} never came:\n{transcript.decode(errors='replace')[-800:]}"
            )
            os.write(master, (command + "\n").encode())
            expected += 1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG)[0]:
                pid = 0
                break
            if select.select([master], [], [], 0.05)[0]:
                try:
                    transcript.extend(os.read(master, 65536))
                except OSError:
                    pass
        assert pid == 0, f"fish did not exit:\n{transcript.decode(errors='replace')[-800:]}"
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not os.waitpid(pid, os.WNOHANG)[0]:
                time.sleep(0.02)
        os.close(master)
    return transcript.decode(errors="replace")
