"""Reusable title command for shell integrations."""

from __future__ import annotations

import argparse
import sys
import unicodedata

from .terminals import detect_terminal
from .toolctx import ToolContext


def terminal_title_argv() -> list[str]:
    return [sys.executable, "-m", "omc.terminal_title"]


def validate_title(title: str) -> str | None:
    """None when `title` may be sent to a terminal, else why not (C0/C1 control characters)."""
    if any(unicodedata.category(char) == "Cc" for char in title):
        return "control characters are not allowed"
    return None


def run_title(ctx: ToolContext, title: str) -> int:
    reason = validate_title(title)
    if reason is not None:
        print(f"omc: invalid title: {reason}", file=sys.stderr)
        return 1
    return 0 if detect_terminal(ctx.env).set_title(ctx, title) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set the current terminal tab title")
    parser.add_argument("title")
    args = parser.parse_args(argv)
    return run_title(ToolContext.from_env(), args.title)


if __name__ == "__main__":
    raise SystemExit(main())
