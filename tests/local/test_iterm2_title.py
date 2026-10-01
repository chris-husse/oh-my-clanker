"""Host acceptance on the private iTerm2 instance: tabs, generated fish startup, provider TUIs.

Run explicitly with ``just iterm2-tests`` on a logged-in macOS desktop. Every tab lives in
the private copy (never the user's iTerm2) and is recorded and closed, even on failure.
"""

import asyncio
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from omc.providers.registry import get_provider
from omc.shells.fish import FishShell
from omc.terminal_title import terminal_title_argv
from tests.local.private_iterm import (
    claude_private_auth,
    connect,
    ensure_window,
    init_repo,
    require_sdk,
    require_tool,
    until,
)

_until = until  # one bounded poller; test_ordinary_fish_title imports until directly

pytestmark = [
    pytest.mark.local_iterm2,
    pytest.mark.filterwarnings(
        r"ignore:In 3\.13 classes created inside an enum will not become a member:"
        r"DeprecationWarning:iterm2\.mainmenu"
    ),
    # The SDK still imports websockets.legacy; not ours to fix.
    pytest.mark.filterwarnings(r"ignore:websockets\.legacy is deprecated:DeprecationWarning"),
]

TITLE = "feature/iterm2-host-acceptance"
PROMPT = "What is 173 plus 286? Reply with only the number."
ANSWER = "459"


def _requirements(provider=None):
    iterm2 = require_sdk()
    for tool in ("fish", "git", provider):
        if tool:
            require_tool(tool, f"install {tool} and put it on PATH")
    return iterm2


def _provider_env_file(tmp_path, provider, adapter, config_root=None):
    values = {**adapter.title_env()}
    clear = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
    if provider == "claude":
        config = None
        clear.append("CLAUDE_CONFIG_DIR")
    else:
        config = (config_root or tmp_path) / f"{provider}-config"
        config.mkdir(mode=0o700)
        clear.append("OPENAI_API_KEY")  # dedicated ChatGPT account auth wins
        values["CODEX_HOME"] = str(config)
    path = tmp_path / f"{provider}-private-env.fish"
    path.write_text(
        "".join(f"set -e {name}\n" for name in clear)
        + "".join(f"set -gx {name} {shlex.quote(value)}\n" for name, value in values.items())
    )
    path.chmod(0o600)
    return path, config


def _provider_tab_command(shell_argv, private_env, returned, auth_file=None):
    """Have fish create evidence only after the provider subprocess returns."""
    argv = list(shell_argv)
    prefix = f"source {shlex.quote(str(private_env))}; "
    if auth_file is not None:
        prefix += f"source {shlex.quote(str(auth_file))}; "
    argv[3] = prefix + argv[3] + f"; printf returned > {shlex.quote(str(returned))}"
    return shlex.join(argv)


async def _exit_provider_tui(session, provider):
    # TUI input handling needs Enter as a distinct event; a text+newline burst
    # can leave a draft instead of submitting the slash command.
    command = "/exit" if provider == "claude" else "/quit"
    await session.async_send_text(command)
    await asyncio.sleep(0.25)
    await session.async_send_text("\r")


@contextmanager
def _codex_account(config):
    """Serialize the dedicated E2E volume, with private copy-in and copy-back."""
    import fcntl  # macOS-only host tier; do not import during other platforms' collection

    volume = os.environ.get("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume):
        pytest.fail("CODEX_AUTH_VOLUME must name a Docker volume, not a path")
    if not shutil.which("docker"):
        pytest.fail("Codex host test needs Docker and the omc-e2e-codex-auth volume")
    lock_dir = Path(os.environ.get("OMC_E2E_AUTH_LOCK_DIR", tempfile.gettempdir()))
    lock_path = lock_dir / f"omc-codex-auth-{volume}.lock"

    def transfer(source, destination, script):
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "-v",
            f"{volume}:/auth:rw",
            "-v",
            f"{config}:/private:rw",
            "node:22-bookworm-slim",
            "sh",
            "-c",
            script,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        if result.returncode:
            pytest.fail(
                f"Codex dedicated auth transfer {source}→{destination} failed; "
                "run just codex-login and verify the Docker volume"
            )

    with lock_path.open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pytest.fail(f"Codex auth volume {volume} is already in use by another test")
        transfer(
            "volume",
            "test home",
            "test -s /auth/auth.json && cp /auth/auth.json /private/auth.json "
            "&& chmod 600 /private/auth.json",
        )
        status = subprocess.run(
            [shutil.which("codex"), "login", "status"],
            env={**os.environ, "CODEX_HOME": str(config)},
            capture_output=True,
            timeout=15,
            check=False,
        )
        if status.returncode:
            pytest.fail("Dedicated Codex account auth is unusable; run just codex-login")
        try:
            yield
        finally:
            transfer(
                "test home",
                "volume",
                "test -s /private/auth.json && cp /private/auth.json /auth/.auth-omc-host.tmp "
                "&& chmod 600 /auth/.auth-omc-host.tmp "
                "&& mv /auth/.auth-omc-host.tmp /auth/auth.json",
            )


async def _connect(iterm2, instance, fish):
    connection, app = await connect(iterm2, instance)
    window = await ensure_window(iterm2, connection, app, fish)
    return connection, app, window


async def _tab(window, tabs, command):
    tab = await asyncio.wait_for(window.async_create_tab(command=command, select=False), 15)
    if tab is None:
        pytest.fail("iTerm2 did not create a disposable test tab")
    tabs.append((tab.tab_id, tab))
    return tab


async def _screen(session):
    contents = await session.async_get_screen_contents()
    # iTerm2 represents some TUI spaces as NUL cells.
    return "\n".join(
        contents.line(i).string.replace("\x00", " ") for i in range(contents.number_of_lines)
    )


async def _title(tab, expected):
    async def matches():
        actual = await tab.async_get_variable("title")
        return actual == expected

    await _until(f"iTerm2 tab title {expected!r}", matches)


async def _cleanup(tabs):
    failures = []
    for tab_id, tab in reversed(tabs):
        try:
            assert tab.tab_id == tab_id
            await asyncio.wait_for(tab.async_close(force=True), 10)
        except Exception as exc:
            failures.append(f"tab {tab_id}: {type(exc).__name__}")
    return failures


def _report_cleanup_errors(failures):
    if not failures:
        return
    message = f"iTerm2 test cleanup: {', '.join(failures)}"
    if active := sys.exception():
        active.add_note(message)
    else:
        pytest.fail(message)


async def _wait_screen(session, needle, timeout=120):
    async def seen():
        screen = await _screen(session)
        if "managed settings require a first-party login" in screen:
            pytest.fail("native Claude profile needs first-party login: run claude auth login")
        if "API key login is disabled by this workspace" in screen:
            pytest.fail(
                "Codex requires ChatGPT account auth in this workspace: run just codex-login"
            )
        return needle in screen

    await _until(f"terminal output containing {needle!r}", seen, timeout=timeout)


def test_caller_target_split_pane_and_competing_osc(private_iterm):
    iterm2 = _requirements()
    fish = shutil.which("fish")

    async def run():
        _connection, _app, window = await _connect(iterm2, private_iterm, fish)
        tabs = []
        try:
            # The session-wide private HOME may hold the hook (provisioned by another case);
            # its prompt-time dispatches would race the explicit titles asserted here.
            unhooked = shlex.join(["/usr/bin/env", "OMC_FISH_TITLE_DISABLE=1", fish, "-i"])
            target = await _tab(window, tabs, unhooked)
            other = await _tab(window, tabs, unhooked)
            await other.async_set_title("omc-control")
            # The split gets the same hook-disabled fish (the default profile would not).
            private_fish = iterm2.LocalWriteOnlyProfile()
            private_fish.set_use_custom_command("Yes")
            private_fish.set_command(unhooked)
            split = await target.current_session.async_split_pane(
                vertical=True, profile_customizations=private_fish
            )
            assert split is not None, "iTerm2 did not create a split test pane"
            hostile = "feature/cost$USD(parent)\\(session.name)"
            command = shlex.join(["omc", "title", "set", "--", hostile])
            await split.async_send_text(command + "; printf '\\nOMC_TITLE_DONE:%s\\n' $status\n")
            await _wait_screen(split, "OMC_TITLE_DONE:0")
            await _title(target, hostile)  # the split pane targets ITS tab, never the other one
            assert await other.async_get_variable("title") == "omc-control"
            for code in (0, 1, 2):
                await split.async_inject(f"\x1b]{code};competing-title\x07".encode())
            await asyncio.sleep(0.5)
            assert await target.async_get_variable("title") == hostile
            assert await other.async_get_variable("title") == "omc-control"
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_provider_title_during_real_response_and_after_exit(private_iterm, tmp_path, provider):
    iterm2 = _requirements(provider)
    fish = shutil.which("fish")
    home = private_iterm.identity.home
    repo = init_repo(home / "repos" / f"start-{provider}", TITLE)
    adapter = get_provider(provider)
    provider_argv = adapter.session_argv(
        session_name="iterm2-host-acceptance", model="", seed=PROMPT
    )
    provider_argv[0] = shutil.which(provider)
    if provider == "claude":
        provider_argv[1:1] = ["--safe-mode", "--tools", "", "--strict-mcp-config", "--no-chrome"]
    else:
        provider_argv[1:1] = [
            "--no-alt-screen",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
        ]
    # The same generated startup command as omc start; only seed and cwd differ.
    shell_argv, _ = FishShell().build_invocation(
        cwd=str(repo), title=TITLE, startup_argv=provider_argv,
        title_seq=f"\x1b]0;{TITLE}\x07", title_argv=terminal_title_argv(),
    )  # fmt: skip
    shell_argv[0] = fish
    private_env, config = _provider_env_file(tmp_path, provider, adapter, config_root=home)
    auth_file = claude_private_auth(home, repo, provider_argv[0]) if provider == "claude" else None
    returned = tmp_path / f"{provider}-shell-returned"
    command = _provider_tab_command(shell_argv, private_env, returned, auth_file=auth_file)

    async def run():
        _connection, _app, window = await _connect(iterm2, private_iterm, fish)
        tabs = []
        try:
            tab = await _tab(window, tabs, command)
            session = tab.current_session
            await _title(tab, TITLE)
            if provider == "codex":
                await _codex_trust_or_answer(session, repo)
            await _wait_screen(session, ANSWER, timeout=90)
            assert await tab.async_get_variable("title") == TITLE
            await _exit_provider_tui(session, provider)

            async def shell_returned():
                return returned.exists()

            await _until("provider exit back to fish", shell_returned, timeout=20)
            assert returned.read_text() == "returned"
            assert await tab.async_get_variable("title") == TITLE
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    try:
        with _codex_account(config) if provider == "codex" else nullcontext():
            asyncio.run(run())
    finally:
        private_env.unlink(missing_ok=True)
        if auth_file is not None:
            auth_file.unlink(missing_ok=True)
        if config is not None:
            shutil.rmtree(config, ignore_errors=True)


async def _codex_trust_or_answer(session, repo):
    async def answer_or_trust():
        screen = await _screen(session)
        if "API key login is disabled by this workspace" in screen:
            pytest.fail(
                "Codex requires ChatGPT account auth in this workspace: run just codex-login"
            )
        if (
            str(repo) in screen
            and "Trust this folder?" in screen
            and "Trust and continue" in screen
        ):
            return "trust"
        if ANSWER in screen:
            return "answer"
        return None

    state = await _until("Codex trust prompt or model answer", answer_or_trust, timeout=90)
    if state == "trust":
        await session.async_send_text("\r")
