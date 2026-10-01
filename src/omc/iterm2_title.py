"""Short-lived iTerm2 Python API worker for an explicitly identified session.

argv: --session-id <id> (--release | -- <title>). Stdout is never used; SDK errors are
swallowed into one fixed stderr line because they may carry authorization secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

USAGE = "usage: omc.iterm2_title --session-id ID (--release | -- TITLE)"


class TargetNotFound(Exception):
    """The launching session or its tab no longer exists."""


def worker_argv(session_id: str, *, title: str | None = None, release: bool = False) -> list[str]:
    argv = [sys.executable, "-m", "omc.iterm2_title", "--session-id", session_id]
    if release:
        return [*argv, "--release"]
    if title is None:
        raise ValueError("worker_argv needs a title or release=True")
    return [*argv, "--", title]


async def _target_tab(connection: object, session_id: str, sdk: object):
    app = await sdk.async_get_app(connection)
    if app is None:
        raise TargetNotFound
    session = app.get_session_by_id(session_id)
    if session is None:
        raise TargetNotFound
    _window, tab = app.get_window_and_tab_for_session(session)
    if tab is None:
        raise TargetNotFound
    return tab


async def set_title(connection: object, session_id: str, title: str, sdk: object) -> None:
    tab = await _target_tab(connection, session_id, sdk)
    await tab.async_set_variable("user.omc_title", title)
    await tab.async_set_title(r"\(user.omc_title)")


async def release_title(connection: object, session_id: str, sdk: object) -> None:
    tab = await _target_tab(connection, session_id, sdk)
    # "" restores iTerm2's live default title (SDK: Tab.async_set_title). Clear the
    # variable afterwards: the SDK sends json "null", which iTerm2's API handler maps to
    # nil and iTermVariables removes the key (verified against iTermAPIHelper.m).
    await tab.async_set_title("")
    await tab.async_set_variable("user.omc_title", None)


async def _run(session_id: str, title: str | None, release: bool) -> None:
    import iterm2  # lazy: macOS-only dependency, isolated from other terminals

    connection = await iterm2.Connection.async_create()
    if release:
        await release_title(connection, session_id, iterm2)
    else:
        await set_title(connection, session_id, title, iterm2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set or release an explicit iTerm2 tab title")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("title", nargs="?")
    args = parser.parse_args(argv)
    if args.release == (args.title is not None):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(args.session_id, args.title, args.release))
    except Exception:  # noqa: BLE001 - SDK errors may contain authorization secrets
        print("omc: iTerm2 title update failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
