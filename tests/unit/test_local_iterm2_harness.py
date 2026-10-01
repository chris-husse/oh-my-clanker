"""Deterministic host-harness ordering and cleanup contracts (no iTerm2 API)."""

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from omc.shells.fish import FishShell


def test_private_connect_invalidates_app_and_scopes_env_per_call(tmp_path):
    from dataclasses import replace

    from tests.local.private_iterm import PrivateITerm, connect, new_identity

    identity = new_identity(tmp_path, "0123456789ab")
    fake_socket = tmp_path / "socket"
    fake_socket.touch()
    instance = PrivateITerm(replace(identity, socket=fake_socket), 1)
    state = SimpleNamespace(created=0, invalidations=0, homes=[])

    class Connection:
        websocket = SimpleNamespace(remote_address="/private/socket")

        @staticmethod
        async def async_create():
            os.environ["ITERM2_COOKIE"] = "private-cookie"
            state.created += 1
            state.homes.append(
                (os.environ["HOME"], os.environ["IT2_SUITE"], os.environ["IT2_APP_PATH"])
            )
            return Connection()

    async def get_app(connection):
        return SimpleNamespace(terminal_windows=[])

    def invalidate_app():
        state.invalidations += 1

    sdk = SimpleNamespace(
        Connection=Connection,
        async_get_app=get_app,
        app=SimpleNamespace(invalidate_app=invalidate_app),
    )
    before_home = os.environ.get("HOME")
    asyncio.run(connect(sdk, instance))
    asyncio.run(connect(sdk, instance))
    assert state.created == 2 and state.invalidations == 2
    assert state.homes == [(str(identity.home), identity.suite, str(identity.copy))] * 2
    assert os.environ.get("HOME") == before_home  # scoped around async_create, never process-wide
    assert "ITERM2_COOKIE" not in os.environ  # SDK auth writes are scrubbed with the scope


def test_private_connect_refuses_tcp_fallback(tmp_path):
    from dataclasses import replace

    from tests.local.private_iterm import PrivateITerm, connect, new_identity

    identity = new_identity(tmp_path, "0123456789ab")
    fake_socket = tmp_path / "socket"
    fake_socket.touch()
    instance = PrivateITerm(replace(identity, socket=fake_socket), 1)

    class Connection:
        websocket = SimpleNamespace(remote_address=("127.0.0.1", 1912))

        @staticmethod
        async def async_create():
            return Connection()

    sdk = SimpleNamespace(
        Connection=Connection,
        async_get_app=None,
        app=SimpleNamespace(invalidate_app=lambda: None),
    )
    with pytest.raises(pytest.fail.Exception, match="TCP"):
        asyncio.run(connect(sdk, instance))


def test_private_connect_refuses_missing_socket(tmp_path):
    from tests.local.private_iterm import PrivateITerm, connect, new_identity

    instance = PrivateITerm(new_identity(tmp_path, "0123456789ab"), 1)
    with pytest.raises(pytest.fail.Exception, match="socket missing"):
        asyncio.run(
            connect(SimpleNamespace(app=SimpleNamespace(invalidate_app=lambda: None)), instance)
        )


def test_native_claude_env_keeps_existing_login_out_of_test_commands(tmp_path, monkeypatch):
    from omc.providers.claude import ClaudeProvider
    from tests.local.test_iterm2_title import _provider_env_file

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key-must-not-be-copied")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-oauth-must-not-be-copied")
    private_env, config = _provider_env_file(tmp_path, "claude", ClaudeProvider())
    assert config is None
    script = private_env.read_text()
    assert "set -e CLAUDE_CONFIG_DIR" in script
    assert "set -e ANTHROPIC_API_KEY" in script
    assert "set -e CLAUDE_CODE_OAUTH_TOKEN" in script
    assert "CLAUDE_CODE_DISABLE_TERMINAL_TITLE" in script
    assert "test-api-key-must-not-be-copied" not in script
    assert "test-oauth-must-not-be-copied" not in script


@pytest.mark.parametrize("provider,command", [("claude", "/exit"), ("codex", "/quit")])
def test_provider_exit_is_one_command_and_separate_return(provider, command):
    from tests.local.test_iterm2_title import _exit_provider_tui

    class Session:
        def __init__(self):
            self.sent = []

        async def async_send_text(self, text):
            self.sent.append(text)

    session = Session()
    asyncio.run(_exit_provider_tui(session, provider))
    assert session.sent == [command, "\r"]


def test_provider_return_marker_is_written_only_after_process_exits(tmp_path):
    from tests.local.test_iterm2_title import _provider_tab_command

    fish = shutil.which("fish")
    if fish is None:
        pytest.fail("fish is required for shell integration tests: brew install fish")
    entered = tmp_path / "provider-entered"
    release = tmp_path / "release-provider"
    returned = tmp_path / "fish-returned"
    private_env = tmp_path / "private-env.fish"
    private_env.write_text("")
    provider = (
        "import pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text('entered'); "
        "gate=pathlib.Path(sys.argv[2]); "
        "exec('while not gate.exists(): time.sleep(0.02)')"
    )
    argv, _ = FishShell().build_invocation(
        cwd=str(tmp_path),
        title="feature/test",
        startup_argv=[sys.executable, "-c", provider, str(entered), str(release)],
        title_seq="",
    )
    argv[0] = fish
    command = _provider_tab_command(argv, private_env, returned)
    proc = subprocess.Popen(
        ["/bin/sh", "-c", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert entered.exists(), "provider did not start"
        assert not returned.exists(), "fish claimed provider return while it was still running"
        release.touch()
        _stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, stderr.decode(errors="replace")
        assert returned.read_text() == "returned"
    finally:
        release.touch()
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)


def test_cleanup_error_keeps_original_provider_failure():
    from tests.local.test_iterm2_title import _cleanup, _report_cleanup_errors

    class ClosedTab:
        tab_id = "disposable"

        async def async_close(self, force):
            raise RuntimeError("already closed")

    async def fail_and_cleanup():
        try:
            raise ValueError("provider failed")
        finally:
            _report_cleanup_errors(await _cleanup([("disposable", ClosedTab())]))

    with pytest.raises(ValueError, match="provider failed") as raised:
        asyncio.run(fail_and_cleanup())
    assert any("RuntimeError" in note for note in raised.value.__notes__)


def test_provider_env_file_can_place_codex_home_under_a_private_root(tmp_path):
    from omc.providers.codex import CodexProvider
    from tests.local.test_iterm2_title import _provider_env_file

    root = tmp_path / "private home"
    root.mkdir()
    private_env, config = _provider_env_file(tmp_path, "codex", CodexProvider(), config_root=root)
    assert config == root / "codex-config" and config.is_dir()
    assert f"set -gx CODEX_HOME {shlex.quote(str(config))}\n" in private_env.read_text()


def test_provider_tab_command_sources_auth_after_private_env(tmp_path):
    from tests.local.test_iterm2_title import _provider_tab_command

    command = _provider_tab_command(
        ["fish", "-i", "-C", "true"],
        tmp_path / "env.fish",
        tmp_path / "ret",
        auth_file=tmp_path / "auth.fish",
    )
    body = shlex.split(command)[3]
    assert body.index("env.fish") < body.index("auth.fish") < body.index("true")
    assert "printf returned" in body


def test_claude_private_auth_writes_trust_and_returns_none_when_logged_in(tmp_path, monkeypatch):
    import json

    from tests.local import private_iterm

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]["HOME"]))
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
        )

    monkeypatch.setattr(private_iterm.subprocess, "run", run)
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    assert private_iterm.claude_private_auth(home, repo, "/usr/local/bin/claude") is None
    profile = json.loads((home / ".claude.json").read_text())
    assert profile["hasCompletedOnboarding"] is True
    assert profile["projects"][str(repo)]["hasTrustDialogAccepted"] is True
    assert calls == [(["/usr/local/bin/claude", "auth", "status", "--json"], str(home))]


class _FakeInstance:
    def __init__(self, *, raises=None, leftovers=()):
        self.raises, self.leftovers, self.closed = raises, list(leftovers), 0

    def close(self):
        self.closed += 1
        if self.raises is not None:
            raise self.raises
        return self.leftovers


def test_teardown_error_is_a_note_on_the_pending_failure():
    from tests.local.private_iterm import close_reporting

    pending = ValueError("launch flags wrong")
    instance = _FakeInstance(raises=RuntimeError("kill failed"))
    close_reporting(instance, pending)  # never raises over the pending failure
    assert instance.closed == 1
    assert any("RuntimeError" in note and "kill failed" in note for note in pending.__notes__)


def test_teardown_leftovers_are_a_note_on_the_pending_failure():
    from tests.local.private_iterm import close_reporting

    pending = ValueError("test failed")
    close_reporting(_FakeInstance(leftovers=["pid 42"]), pending)
    assert any("pid 42" in note for note in pending.__notes__)


def test_teardown_without_a_pending_failure_raises_and_fails_on_leftovers():
    from tests.local.private_iterm import close_reporting

    close_reporting(_FakeInstance(), None)  # clean teardown: nothing to report
    with pytest.raises(RuntimeError, match="kill failed"):
        close_reporting(_FakeInstance(raises=RuntimeError("kill failed")), None)
    with pytest.raises(pytest.fail.Exception, match="pid 42"):
        close_reporting(_FakeInstance(leftovers=["pid 42"]), None)
