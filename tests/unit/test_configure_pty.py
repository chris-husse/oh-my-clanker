"""Drive configure through a real controlling terminal."""

import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from omc import configure
from omc.config import store
from omc.config.schema import GlobalConfig, ProjectConfig, SecretsConfig
from omc.toolctx import ToolContext

_PROJECT = Path(__file__).resolve().parents[2]


class MenuTerminal:
    def __init__(self, tmp_path, *, plain=True, ignore_hup=False, seed_config=True):
        self.output = bytearray()
        self.cursor = 0
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        initial = GlobalConfig()
        initial.llm.default = "codex"
        if seed_config:
            store.save_global(self.home, initial)
        source = f"""
import signal
import fcntl
import termios
import traceback
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
if {ignore_hup!r}:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
from pathlib import Path
from omc import configure
from omc.config import store
from omc.config.schema import GlobalConfig, ProjectConfig
from omc.errors import Refusal
from omc.toolctx import ToolContext
home = Path({str(self.home)!r})
ctx = ToolContext(home=home, env={{}})
gcfg = store.load_global(home) or GlobalConfig()
try:
    flags = configure._run_menu(
        ctx, Path({str(self.repo)!r}), gcfg, ProjectConfig(), store.load_secrets(home),
        plain_menu={plain!r}
    )
except Refusal as exc:
    (home / 'terminal-result.txt').write_text('Refusal: ' + str(exc))
    raise SystemExit(2) from None
except BaseException as exc:
    message = type(exc).__name__ + ': ' + str(exc) + '\\n' + traceback.format_exc()
    (home / 'terminal-result.txt').write_text(message)
    raise
print('RESULT:', flags, flush=True)
"""
        try:
            self.master, slave = pty.openpty()
        except OSError as exc:
            pytest.fail(f"PTY unavailable: provide a controlling terminal ({exc})")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-u", "-c", source],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=_PROJECT,
                env={**os.environ, "PYTHONPATH": str(_PROJECT / "src")},
                start_new_session=True,
            )
        finally:
            os.close(slave)

    def expect(self, marker: bytes, timeout=6):
        deadline = time.monotonic() + timeout
        while marker not in self.output[self.cursor :]:
            if time.monotonic() >= deadline:
                output = self.output.decode(errors="replace")
                pytest.fail(f"Timed out waiting for {marker!r}; terminal output:\n{output}")
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                self.output.extend(chunk)
        position = self.output.find(marker, self.cursor)
        assert position >= 0, self.output.decode(errors="replace")
        self.cursor = position + len(marker)

    def wait_input_ready(self):
        deadline = time.monotonic() + 2
        while termios.tcgetattr(self.master)[3] & termios.ICANON:
            assert time.monotonic() < deadline, "Menu prompt appeared before cbreak mode was ready"
            time.sleep(0.005)

    def send(self, data: bytes):
        self.wait_input_ready()
        os.write(self.master, data)

    def finish(self):
        self.expect(b"RESULT:")
        deadline = time.monotonic() + 5
        while self.proc.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("Menu child did not exit after RESULT")
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    self.output.extend(os.read(self.master, 65536))
                except OSError:
                    pass
        assert self.proc.returncode == 0, self.output.decode(errors="replace")

    def close(self):
        if self.master is not None:
            os.close(self.master)
            self.master = None
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pytest.fail(f"Could not reap PTY child {self.proc.pid} after SIGKILL")

    def release_terminal(self):
        os.close(self.master)
        self.master = None

    def wait_exit(self, timeout=5):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail("Menu child kept running after terminal loss")


@pytest.fixture
def terminal(tmp_path):
    terminals = []

    def make(*, plain=True, ignore_hup=False, seed_config=True):
        term = MenuTerminal(tmp_path, plain=plain, ignore_hup=ignore_hup, seed_config=seed_config)
        terminals.append(term)
        return term

    yield make
    for term in terminals:
        term.close()


def test_plain_menu_saves_nested_project_field_and_escape_leaves(terminal):
    term = terminal()
    prompt = b"Choose (Enter = ok): "
    term.expect(prompt)
    term.send(b"3\n")
    term.expect(b"Worktree (project)")
    term.expect(prompt)
    term.send(b"2\n")
    term.expect(b"Base branch main:")
    term.send(b"develop\n")
    term.expect(prompt)
    assert store.load_project(term.repo).worktree.base_branch == "develop"
    term.send(b"\x1b")
    term.expect(prompt)
    term.send(b"\x1b")
    term.finish()
    saved = store.load_global(term.home)
    assert saved.llm.default == "codex"
    assert store.load_project(term.repo).worktree.base_branch == "develop"


def test_default_renderer_can_navigate_nested_project_field(terminal):
    term = terminal(plain=False)
    term.expect(b"Worktree (project)")
    term.send(b"3")
    term.expect(b"Base branch")
    term.send(b"2")
    term.expect(b"Base branch main:")
    term.send(b"develop\n")
    term.expect(b"Updated ")
    assert store.load_project(term.repo).worktree.base_branch == "develop"
    term.send(b"\x1b")
    term.expect(b"LLM...", timeout=6)
    term.send(b"\x1b")
    term.finish()


def test_terminal_loss_is_an_error_and_does_not_loop(terminal):
    term = terminal(ignore_hup=True, seed_config=False)
    term.expect(b"Choose (Enter = ok): ")
    term.wait_input_ready()
    term.release_terminal()
    assert term.wait_exit() == 2, (term.home / "terminal-result.txt").read_text()
    assert not store.global_config_path(term.home).exists()


def test_default_renderer_terminal_loss_is_a_refusal_without_defaults(terminal):
    term = terminal(plain=False, ignore_hup=True, seed_config=False)
    term.expect(b"Worktree (project)")
    term.release_terminal()
    assert term.wait_exit() == 2, (term.home / "terminal-result.txt").read_text()
    assert not store.global_config_path(term.home).exists()


def test_ctrl_c_abandons_current_edit_without_losing_earlier_save(terminal):
    term = terminal()
    prompt = b"Choose (Enter = ok): "
    term.expect(prompt)
    term.send(b"3\n")
    term.expect(prompt)
    term.send(b"2\n")
    term.expect(b"Base branch main:")
    term.send(b"develop\n")
    term.expect(prompt)
    term.send(b"2\n")
    term.expect(b"Base branch develop:")
    term.send(b"\x03")
    term.expect(prompt)
    assert store.load_project(term.repo).worktree.base_branch == "develop"
    term.send(b"\x1b")
    term.expect(prompt)
    term.send(b"\x03")
    term.finish()
    assert store.load_global(term.home).llm.default == "codex"


def test_unbuffered_reader_preserves_utf8_input(terminal):
    term = terminal()
    prompt = b"Choose (Enter = ok): "
    term.expect(prompt)
    term.send(b"3\n")
    term.expect(prompt)
    term.send(b"2\n")
    term.expect(b"Base branch main:")
    term.send("café\n".encode())
    term.expect(prompt)
    term.send(b"\x1b")
    term.expect(prompt)
    term.send(b"\x1b")
    term.finish()
    assert store.load_project(term.repo).worktree.base_branch == "café"


def test_stdin_restored_when_renderer_raises(tmp_path, monkeypatch):
    original = sys.stdin

    class BrokenInterface:
        def form(self, root):
            assert sys.stdin is not original
            raise RuntimeError("renderer failed")

    monkeypatch.setattr(configure, "_text_interface", lambda plain: BrokenInterface())
    with pytest.raises(RuntimeError, match="renderer failed"):
        configure._run_menu(
            ToolContext(home=tmp_path, env={}),
            None,
            GlobalConfig(),
            ProjectConfig(),
            SecretsConfig(),
        )
    assert sys.stdin is original


def test_unrelated_renderer_assertion_preserves_stdin(tmp_path, monkeypatch):
    original = sys.stdin

    class BrokenInterface:
        def form(self, root):
            try:
                raise OSError(5, "Input/output error")
            except OSError:
                raise AssertionError("unrelated renderer assertion") from None

    monkeypatch.setattr(configure, "_text_interface", lambda plain: BrokenInterface())
    with pytest.raises(AssertionError, match="unrelated renderer assertion"):
        configure._run_menu(
            ToolContext(home=tmp_path, env={}),
            None,
            GlobalConfig(),
            ProjectConfig(),
            SecretsConfig(),
        )
    assert sys.stdin is original
