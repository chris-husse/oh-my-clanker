"""Short-lived iTerm2 Python API worker for an explicitly identified session."""

from __future__ import annotations

import argparse
import asyncio
import sys


class TargetNotFound(Exception):
    """The launching session or its tab no longer exists."""


async def set_title(connection: object, session_id: str, title: str, sdk: object) -> None:
    app = await sdk.async_get_app(connection)
    if app is None:
        raise TargetNotFound
    session = app.get_session_by_id(session_id)
    if session is None:
        raise TargetNotFound
    _window, tab = app.get_window_and_tab_for_session(session)
    if tab is None:
        raise TargetNotFound
    await tab.async_set_variable("user.omc_title", title)
    await tab.async_set_title(r"\(user.omc_title)")


async def _run(session_id: str, title: str) -> None:
    import iterm2  # lazy: macOS-only dependency, isolated from other terminals

    connection = await iterm2.Connection.async_create()
    await set_title(connection, session_id, title, iterm2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set explicit iTerm2 tab title")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("title")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args.session_id, args.title))
    except Exception:  # noqa: BLE001 - SDK errors may contain authorization secrets
        print("omc: iTerm2 title update failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
