"""Native smoke: launch the private iTerm2 copy, attach over its socket, run this checkout's omc."""

import asyncio
import shlex
from pathlib import Path

import pytest

from tests.local.private_iterm import (
    _ps,
    connect,
    require_sdk,
    require_tool,
    server_processes,
    until,
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

CHECKOUT = Path(__file__).resolve().parents[2]
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def test_probe_tab_runs_this_checkouts_omc(private_iterm):
    iterm2 = require_sdk()
    fish = require_tool("fish", "brew install fish")
    home = private_iterm.identity.home
    out = home / "probe-install-path"
    done = home / "probe-done"
    env_out, leaked, plain_done = home / "probe-home", home / "probe-leaked", home / "probe-plain"

    async def run():
        connection, app = await connect(iterm2, private_iterm)
        windows_before = len(app.terminal_windows)  # earlier cases may leave their window
        window = await asyncio.wait_for(
            iterm2.Window.async_create(connection, command=shlex.join([fish, "-i"])), 30
        )
        assert window is not None, "iTerm2 did not create the private window"
        tab = window.current_tab
        try:
            session = tab.current_session
            await session.async_send_text(
                f"omc print-install-path > {shlex.quote(str(out))}; "
                f"echo ok > {shlex.quote(str(done))}\n"
            )

            async def finished():
                return done.exists()

            await until("the probe tab to run omc", finished, timeout=30)
            # A tab WITHOUT command= uses the private default profile: private HOME, and none
            # of the runner's credentials (names only are written, never values).
            plain = await asyncio.wait_for(window.async_create_tab(select=False), 15)
            try:
                await plain.current_session.async_send_text(
                    # Shell-neutral (fish or a login zsh), so a regression fails on HOME.
                    f"printf '%s\\n' \"$HOME\" > {shlex.quote(str(env_out))}; "
                    f"/usr/bin/env | /usr/bin/grep -oE '^({'|'.join(CREDENTIAL_VARS)})=' "
                    f"> {shlex.quote(str(leaked))}; echo ok > {shlex.quote(str(plain_done))}\n"
                )

                async def plain_finished():
                    return plain_done.exists()

                await until("the default-profile tab to report", plain_finished, timeout=30)
            finally:
                await asyncio.wait_for(plain.async_close(force=True), 10)
            await app.async_refresh()
            assert len(app.terminal_windows) == windows_before + 1
            assert server_processes(_ps("pid", "command"), private_iterm.identity) == [], (
                "runJobsInServers NO did not take effect"
            )
        finally:
            await asyncio.wait_for(tab.async_close(force=True), 10)

    asyncio.run(run())
    # PATH inside the app is <checkout>/.venv/bin:/usr/bin:/bin (spec §7 step 3), so
    # `omc` is this checkout's console script and the package is src/omc.
    assert Path(out.read_text().strip()).resolve() == (CHECKOUT / "src" / "omc").resolve()
    assert env_out.read_text() == f"{home}\n"
    assert leaked.read_text() == "", "runner credentials reached the private app"
    # iTerm2 writes two harmless keys to com.googlecode.iterm2 on launch; the probe
    # records that fact here and never reads or repairs that domain.
