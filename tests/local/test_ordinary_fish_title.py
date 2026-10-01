"""Ordinary fish prompts with the hook in the private iTerm2.

Branch transitions, then plain codex, codex resume and claude typed at the prompt.
"""

import asyncio
import shlex
import shutil
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

from tests.local.private_iterm import (
    claude_private_auth,
    connect,
    ensure_window,
    init_repo,
    provision_hook,
    require_sdk,
    require_tool,
    until,
)
from tests.local.test_iterm2_title import (
    ANSWER,
    PROMPT,
    _cleanup,
    _codex_account,
    _exit_provider_tui,
    _report_cleanup_errors,
    _screen,
    _tab,
    _title,
    _wait_screen,
)

pytestmark = [
    pytest.mark.local_iterm2,
    pytest.mark.filterwarnings(
        r"ignore:In 3\.13 classes created inside an enum will not become a member:"
        r"DeprecationWarning:iterm2\.mainmenu"
    ),
    # The SDK still imports websockets.legacy; not ours to fix.
    pytest.mark.filterwarnings(r"ignore:websockets\.legacy is deprecated:DeprecationWarning"),
]

BRANCH = "feature/ordinary-fish-provider"


# `just` loads .env and `open` hands the caller's environment to the private app, so its
# tabs inherit these; the same set _provider_env_file clears (dedicated auth wins).
INHERITED_AUTH = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
    "OPENAI_API_KEY",
)


def _fish_tab_command(fish, env):
    """Only environment and `fish -i`; everything else is typed at the prompt."""
    unset = [arg for name in INHERITED_AUTH for arg in ("-u", name)]
    assignments = [f"{k}={v}" for k, v in env.items()]
    return shlex.join(["/usr/bin/env", *unset, *assignments, fish, "-i"])


async def _typed(session, text):
    await session.async_send_text(text + "\n")


async def _marker(session, path):
    """Type a marker write; wait for the file: the previous foreground command is done."""
    await _typed(session, f"echo done > {shlex.quote(str(path))}")

    async def exists():
        return path.exists()

    await until(f"marker {path.name}", exists, timeout=30)


async def _back_at_prompt(session):
    """The provider TUI is gone and fish shows its prompt on the branch (input typed while
    a TUI is still exiting is swallowed by it)."""

    async def prompt():
        lines = [line.rstrip() for line in (await _screen(session)).split("\n") if line.strip()]
        return bool(lines) and lines[-1].endswith(f"({BRANCH})>")

    await until("the fish prompt after the provider exits", prompt, timeout=30)


async def _submit(session, text):
    """Type, wait until the TUI renders it, then Enter as its own event (a burst is a paste)."""
    await session.async_send_text(text)
    await _wait_screen(session, text, timeout=30)
    await asyncio.sleep(0.5)
    await session.async_send_text("\r")


async def _codex_ready(session, repo):
    """A fresh CODEX_HOME asks to trust the folder BEFORE the composer exists, and the banner
    can flash before that menu: wait for a composer that stays up, answering trust on sight."""
    seen = {"ready": 0, "answered": 0.0}

    async def settled_composer():
        screen = await _screen(session)
        if "Trust this folder?" in screen:
            seen["ready"] = 0
            menu = str(repo) in screen and "Trust and continue" in screen
            if menu and time.monotonic() - seen["answered"] > 3:
                seen["answered"] = time.monotonic()
                await session.async_send_text("\r")
            return False
        composer = "OpenAI Codex" in screen and "? for shortcuts" in screen
        seen["ready"] = seen["ready"] + 1 if composer else 0
        return seen["ready"] >= 3  # three consecutive polls, about a second

    await until("a settled Codex composer (folder trust answered)", settled_composer, timeout=90)


def test_branch_transitions_and_release_restores_dynamic_default(private_iterm):
    iterm2 = require_sdk()
    fish = require_tool("fish", "brew install fish")
    home = private_iterm.identity.home
    provision_hook(home)
    repo = init_repo(home / "repos" / "transitions", BRANCH)

    async def run():
        connection, app = await connect(iterm2, private_iterm)
        window = await ensure_window(iterm2, connection, app, fish)
        tabs = []
        try:
            tab = await _tab(window, tabs, _fish_tab_command(fish, {}))
            session = tab.current_session
            await _typed(session, f"cd {shlex.quote(str(repo))}")
            await _title(tab, BRANCH)  # branch → set
            await _typed(session, "git switch --detach -q")
            await _marker(session, home / "m-detached")
            await asyncio.sleep(1.0)
            assert await tab.async_get_variable("title") == BRANCH  # detached → remembered branch
            await _typed(session, "cd /")
            await _marker(session, home / "m-outside")

            async def released():  # outside → release: the API override is GONE, so an OSC
                await session.async_inject(b"\x1b]0;osc-moved\x07")  # write moves the title again
                # The dynamic default appends the job name ("osc-moved (fish)").
                return (await tab.async_get_variable("title")).startswith("osc-moved")

            await until("release to restore the dynamic default", released, timeout=20)
            await _typed(session, f"cd {shlex.quote(str(repo))}")
            await _title(tab, BRANCH)  # back into the (detached) worktree → remembered branch
            for code in (0, 1, 2):
                await session.async_inject(f"\x1b]{code};competing\x07".encode())
            await asyncio.sleep(0.5)
            assert await tab.async_get_variable("title") == BRANCH  # pin active again
            await _typed(session, f"git switch -q {shlex.quote(BRANCH)}")
            await _marker(session, home / "m-branch")
            assert await tab.async_get_variable("title") == BRANCH
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    asyncio.run(run())


def _codex_session_id(codex_home: Path) -> str:
    rollouts = sorted(
        codex_home.glob("sessions/**/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime
    )
    assert rollouts, f"no Codex rollout under {codex_home}/sessions"
    return "-".join(rollouts[-1].stem.split("-")[-5:])  # rollout-<timestamp>-<uuid>


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_ordinary_prompt_keeps_branch_through_plain_provider_runs(private_iterm, provider):
    iterm2 = require_sdk()
    fish = require_tool("fish", "brew install fish")
    binary = require_tool(provider, f"install the {provider} CLI")
    home = private_iterm.identity.home
    provision_hook(home)
    repo = init_repo(home / "repos" / f"ordinary-{provider}", BRANCH)
    venv_bin = private_iterm.identity.checkout / ".venv" / "bin"
    # This checkout's omc first: the provider's bin dir may hold an installed (older) omc.
    env = {"PATH": f"{venv_bin}:{Path(binary).parent}:/usr/bin:/bin"}
    config = None
    auth_file = None
    if provider == "codex":
        config = home / "codex-config-ordinary"
        config.mkdir(mode=0o700)
        env["CODEX_HOME"] = str(config)
        flags = "--sandbox read-only --ask-for-approval never"
    else:
        auth_file = claude_private_auth(home, repo, binary)
        flags = '--safe-mode --tools "" --strict-mcp-config --no-chrome'

    async def run():
        connection, app = await connect(iterm2, private_iterm)
        window = await ensure_window(iterm2, connection, app, fish)
        tabs = []
        try:
            tab = await _tab(window, tabs, _fish_tab_command(fish, env))
            session = tab.current_session
            if auth_file is not None:
                await _typed(session, f"source {shlex.quote(str(auth_file))}")
            await _typed(session, f"cd {shlex.quote(str(repo))}")
            await _title(tab, BRANCH)
            # Plain provider, typed at the prompt; it writes its own activity titles.
            await _typed(session, f"{provider} {flags}")
            if provider == "codex":
                await _codex_ready(session, repo)
            else:
                await _wait_screen(session, "Claude Code", timeout=60)
                await asyncio.sleep(1.0)
            await _submit(session, PROMPT)
            await _wait_screen(session, ANSWER, timeout=120)
            assert await tab.async_get_variable("title") == BRANCH  # during provider activity
            await _exit_provider_tui(session, provider)
            await _back_at_prompt(session)
            await _marker(session, home / f"m-{provider}-exit")
            assert await tab.async_get_variable("title") == BRANCH  # after exit
            if provider == "codex":
                session_id = _codex_session_id(config)
                await _typed(session, f"codex resume {shlex.quote(session_id)} {flags}")
                await _wait_screen(session, ANSWER, timeout=90)  # the resumed transcript
                assert await tab.async_get_variable("title") == BRANCH
                await _exit_provider_tui(session, provider)
                await _back_at_prompt(session)
                await _marker(session, home / "m-codex-resume-exit")
                assert await tab.async_get_variable("title") == BRANCH
        finally:
            _report_cleanup_errors(await _cleanup(tabs))

    try:
        with _codex_account(config) if provider == "codex" else nullcontext():
            asyncio.run(run())
    finally:
        if auth_file is not None:
            auth_file.unlink(missing_ok=True)
        if config is not None:
            shutil.rmtree(config, ignore_errors=True)
