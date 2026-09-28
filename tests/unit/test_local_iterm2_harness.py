"""Deterministic host-harness ordering and cleanup contracts (no iTerm2 API)."""

import asyncio
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from omc.shells.fish import FishShell


def test_iterm2_connections_do_not_reuse_app_from_closed_event_loop():
    from tests.local.test_iterm2_title import _connect

    state = SimpleNamespace(instance=None, created=0, invalidations=0)

    async def create():
        state.created += 1
        return SimpleNamespace(number=state.created)

    async def get_app(connection):
        if state.instance is not None:
            raise ConnectionError("cached app belongs to a closed event loop")
        state.instance = SimpleNamespace(current_window=connection)
        return state.instance

    def invalidate_app():
        state.invalidations += 1
        state.instance = None

    sdk = SimpleNamespace(
        Connection=SimpleNamespace(async_create=create),
        async_get_app=get_app,
        app=SimpleNamespace(invalidate_app=invalidate_app),
    )
    first = asyncio.run(_connect(sdk))
    second = asyncio.run(_connect(sdk))
    assert first[0].number == 1
    assert second[0].number == 2
    assert state.invalidations == 2


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


def test_native_claude_checkout_requires_preexisting_trust(tmp_path):
    from tests.local.test_iterm2_title import _trusted_claude_checkout

    checkout = tmp_path / "primary"
    git_dir = checkout / ".git"
    checkout.mkdir()
    git_dir.mkdir()
    trusted = {str(checkout): {"hasTrustDialogAccepted": True}}
    assert _trusted_claude_checkout(git_dir, trusted) == checkout
    with pytest.raises(pytest.fail.Exception, match="already trusted"):
        _trusted_claude_checkout(git_dir, {})


def test_native_claude_preflight_uses_same_auth_env_as_tab():
    from tests.local.test_iterm2_title import _native_claude_env

    env = {
        "PATH": "/test/bin",
        "CLAUDE_CONFIG_DIR": "/isolated",
        "ANTHROPIC_API_KEY": "test-api-key",
        "CLAUDE_CODE_OAUTH_TOKEN": "test-oauth",
    }
    assert _native_claude_env(env) == {"PATH": "/test/bin"}


def test_native_claude_branch_uses_branch_show_current(tmp_path, monkeypatch):
    from tests.local import test_iterm2_title as host

    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return type("Result", (), {"returncode": 0, "stdout": "feature/full-branch\n"})()

    monkeypatch.setattr(host.subprocess, "run", run)
    assert host._current_branch(tmp_path) == "feature/full-branch"
    assert seen == [["git", "-C", str(tmp_path), "branch", "--show-current"]]


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

    class OriginalTab:
        selected = False

        async def async_select(self):
            self.selected = True

    original = OriginalTab()

    async def fail_and_cleanup():
        try:
            raise ValueError("provider failed")
        finally:
            _report_cleanup_errors(await _cleanup([("disposable", ClosedTab())], original))

    with pytest.raises(ValueError, match="provider failed") as raised:
        asyncio.run(fail_and_cleanup())
    assert original.selected
    assert any("RuntimeError" in note for note in raised.value.__notes__)
