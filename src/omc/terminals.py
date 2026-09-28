"""Terminal title adapters: iTerm2 sets an explicit API title; others use OSC 0.

OSC 0 remains the best-effort fallback when the iTerm2 API cannot pin a tab.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .toolctx import ToolContext


class Terminal(ABC):
    name: str

    @classmethod
    @abstractmethod
    def detect(cls, env: Mapping[str, str]) -> bool: ...

    @abstractmethod
    def title_sequence(self, title: str) -> str: ...

    @abstractmethod
    def set_title(self, ctx: ToolContext, title: str) -> bool: ...


class OscTerminal(Terminal):
    name = "osc"

    @classmethod
    def detect(cls, env):
        return True

    def title_sequence(self, title: str) -> str:
        return f"\033]0;{title}\007"

    def set_title(self, ctx: ToolContext, title: str) -> bool:
        print(self.title_sequence(title), end="", flush=True)
        return True


class Iterm2Terminal(OscTerminal):
    name = "iterm2"

    @classmethod
    def detect(cls, env):
        return env.get("TERM_PROGRAM") == "iTerm.app" or env.get("LC_TERMINAL") == "iTerm2"

    def set_title(self, ctx: ToolContext, title: str) -> bool:
        import re
        import sys
        import uuid

        raw_id = ctx.env.get("ITERM_SESSION_ID", "")
        prefix, separator, suffix = raw_id.partition(":")
        session_id = suffix if separator else raw_id
        try:
            parsed = uuid.UUID(session_id)
            valid = str(parsed).lower() == session_id.lower() and (
                not separator or re.fullmatch(r"w\d+t\d+p\d+", prefix) is not None
            )
        except ValueError:
            valid = False
        if valid:
            try:
                result = ctx.run_bounded(
                    [
                        sys.executable,
                        "-m",
                        "omc.iterm2_title",
                        "--session-id",
                        session_id,
                        "--",
                        title,
                    ],
                    timeout=5,
                )
                if result.returncode == 0:
                    return True
            except (TimeoutError, OSError):
                pass
        print(
            "omc: iTerm2 API could not pin this tab title; check Python API authorization "
            "and ITERM_SESSION_ID. Using OSC fallback (cannot pin title).",
            file=sys.stderr,
        )
        super().set_title(ctx, title)
        return False


def detect_terminal(env: Mapping[str, str]) -> Terminal:
    if Iterm2Terminal.detect(env):
        return Iterm2Terminal()
    return OscTerminal()
