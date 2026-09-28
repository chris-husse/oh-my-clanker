"""Host acceptance: real iTerm2 tabs, generated fish startup, and provider TUIs.

Run explicitly with ``just iterm2-tests`` on a logged-in macOS desktop. Every
tab created here is recorded and closed, even when an assertion fails.
"""

import asyncio
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from omc.providers.registry import get_provider
from omc.shells.fish import FishShell
from omc.terminal_title import terminal_title_argv

pytestmark = [
    pytest.mark.local_iterm2,
    pytest.mark.filterwarnings(
        r"ignore:In 3\.13 classes created inside an enum will not become a member:"
        r"DeprecationWarning:iterm2\.mainmenu"
    ),
]

TITLE = "feature/iterm2-host-acceptance"
PROMPT = "What is 173 plus 286? Reply with only the number."
ANSWER = "459"


def _requirements(provider=None):
    if platform.system() != "Darwin":
        pytest.fail("local_iterm2 requires macOS with a running iTerm2 desktop")
    for tool in ("fish", "git", provider):
        if tool and not shutil.which(tool):
            pytest.fail(f"local_iterm2 requires {tool} on PATH")
    try:
        import iterm2
    except ImportError:
        pytest.fail("local_iterm2 requires the iTerm2 Python SDK: uv sync")
    return iterm2


def _trusted_claude_checkout(common_git_dir, projects):
    checkout = Path(common_git_dir).resolve().parent
    if not projects.get(str(checkout), {}).get("hasTrustDialogAccepted"):
        pytest.fail("Claude host test needs an already trusted primary checkout")
    return checkout


def _native_claude_env(env):
    return {
        name: value
        for name, value in env.items()
        if name not in {"CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
    }


def _current_branch(checkout):
    branch = subprocess.run(
        ["git", "-C", str(checkout), "branch", "--show-current"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if branch.returncode or not branch.stdout.strip():
        pytest.fail("primary checkout needs a named branch for Claude host title test")
    return branch.stdout.strip()


def _native_claude_repo():
    auth = subprocess.run(
        [shutil.which("claude"), "auth", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=_native_claude_env(os.environ),
    )
    try:
        status = json.loads(auth.stdout)
    except json.JSONDecodeError:
        pytest.fail("could not read native Claude auth status")
    if auth.returncode or not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
        pytest.fail("native Claude profile needs first-party login: run claude auth login")

    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if common.returncode:
        pytest.fail("could not find the primary checkout for native Claude host test")
    try:
        profile = json.loads((Path.home() / ".claude.json").read_text())
    except (OSError, json.JSONDecodeError):
        pytest.fail("could not read native Claude trusted-project metadata")
    checkout = _trusted_claude_checkout(common.stdout.strip(), profile.get("projects", {}))
    return checkout, _current_branch(checkout)


def _provider_env_file(tmp_path, provider, adapter):
    values = {**adapter.title_env()}
    clear = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
    if provider == "claude":
        config = None
        clear.append("CLAUDE_CONFIG_DIR")
    else:
        config = tmp_path / f"{provider}-config"
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


def _provider_tab_command(shell_argv, private_env, returned):
    """Have fish create evidence only after the provider subprocess returns."""
    argv = list(shell_argv)
    argv[3] = (
        f"source {shlex.quote(str(private_env))}; "
        + argv[3]
        + f"; printf returned > {shlex.quote(str(returned))}"
    )
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


async def _connect(iterm2):
    try:
        # Each pytest case uses a fresh asyncio.run loop. SDK 2.24 caches App
        # process-wide, so its previous connection belongs to a closed loop.
        iterm2.app.invalidate_app()
        connection = await asyncio.wait_for(iterm2.Connection.async_create(), 10)
        app = await asyncio.wait_for(iterm2.async_get_app(connection), 10)
    except Exception as exc:
        pytest.fail(f"iTerm2 Python API unavailable or unauthorized: {type(exc).__name__}")
    if app is None or app.current_window is None:
        pytest.fail("iTerm2 Python API needs an open desktop window")
    return connection, app, app.current_window


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


async def _until(description, predicate, *, timeout=30):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(0.35)
    pytest.fail(f"timed out waiting for {description}")


async def _title(tab, expected):
    async def matches():
        actual = await tab.async_get_variable("title")
        return actual == expected

    await _until(f"iTerm2 tab title {expected!r}", matches)


async def _cleanup(tabs, original):
    failures = []
    for tab_id, tab in reversed(tabs):
        try:
            assert tab.tab_id == tab_id
            await asyncio.wait_for(tab.async_close(force=True), 10)
        except Exception as exc:
            failures.append(f"tab {tab_id}: {type(exc).__name__}")
    if original is not None:
        try:
            await original.async_select()
        except Exception as exc:
            failures.append(f"restore focus: {type(exc).__name__}")
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


def _test_title():
    iterm2 = _requirements()

    async def run():
        _connection, _app, window = await _connect(iterm2)
        original = window.current_tab
        tabs = []
        try:
            fish = shutil.which("fish")
            target = await _tab(window, tabs, shlex.join([fish, "-i"]))
            other = await _tab(window, tabs, shlex.join([fish, "-i"]))
            await other.async_set_title("omc-control")
            split = await target.current_session.async_split_pane(vertical=True)
            assert split is not None, "iTerm2 did not create a split test pane"
            await other.async_select()
            hostile = "feature/cost$USD(parent)\\(session.name)"
            command = shlex.join([*terminal_title_argv(), hostile])
            await split.async_send_text(command + "; printf '\\nOMC_TITLE_DONE:%s\\n' $status\n")
            await _wait_screen(split, "OMC_TITLE_DONE:0")
            await _title(target, hostile)
            assert await other.async_get_variable("title") == "omc-control"
            for code in (0, 1, 2):
                await split.async_inject(f"\x1b]{code};competing-title\x07".encode())
            await asyncio.sleep(0.5)
            assert await target.async_get_variable("title") == hostile
            assert await other.async_get_variable("title") == "omc-control"
            assert window.current_tab.tab_id == other.tab_id
        finally:
            _report_cleanup_errors(await _cleanup(tabs, original))

    asyncio.run(run())


def test_caller_target_split_focus_literal_and_competing_osc():
    _test_title()


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_provider_title_during_real_response_and_after_exit(tmp_path, provider):
    iterm2 = _requirements(provider)
    if provider == "claude":
        repo, title = _native_claude_repo()
    else:
        repo = tmp_path / "host-acceptance"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", TITLE, str(repo)], check=True)
        title = TITLE
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
    # An interactive provider session needs a TTY. This is the same generated
    # startup command as omc start; only its seed and disposable cwd differ.
    shell_argv, _ = FishShell().build_invocation(
        cwd=str(repo),
        title=title,
        startup_argv=provider_argv,
        title_seq=f"\x1b]0;{title}\x07",
        title_argv=terminal_title_argv(),
    )
    shell_argv[0] = shutil.which("fish")
    private_env, config = _provider_env_file(tmp_path, provider, adapter)
    returned = tmp_path / f"{provider}-shell-returned"
    command = _provider_tab_command(shell_argv, private_env, returned)

    async def run():
        _connection, _app, window = await _connect(iterm2)
        original = window.current_tab
        tabs = []
        try:
            tab = await _tab(window, tabs, command)
            session = tab.current_session
            await _title(tab, title)
            if provider == "codex":

                async def answer_or_trust():
                    screen = await _screen(session)
                    if "API key login is disabled by this workspace" in screen:
                        pytest.fail(
                            "Codex requires ChatGPT account auth in this workspace: "
                            "run just codex-login"
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

                state = await _until(
                    "Codex trust prompt or model answer", answer_or_trust, timeout=90
                )
                if state == "trust":
                    await session.async_send_text("\r")
            await _wait_screen(session, ANSWER, timeout=90)
            assert await tab.async_get_variable("title") == title
            # Exit the TUI using its own command; fish stays alive and its
            # prompt hook must leave the full branch title in place.
            await _exit_provider_tui(session, provider)

            async def shell_returned():
                return returned.exists()

            await _until("provider exit back to fish", shell_returned, timeout=20)
            assert returned.read_text() == "returned"
            assert await tab.async_get_variable("title") == title
        finally:
            _report_cleanup_errors(await _cleanup(tabs, original))

    try:
        with _codex_account(config) if provider == "codex" else nullcontext():
            asyncio.run(run())
    finally:
        private_env.unlink(missing_ok=True)
        if config is not None:
            shutil.rmtree(config)
