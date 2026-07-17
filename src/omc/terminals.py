"""Terminal title adapters. OSC 0 sets BOTH tab and window title — the portable
"name this tab" sequence; iTerm2 honors it too (kept as a named adapter for
detection/telemetry parity)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class Terminal(ABC):
    name: str

    @classmethod
    @abstractmethod
    def detect(cls, env: Mapping[str, str]) -> bool: ...

    @abstractmethod
    def title_sequence(self, title: str) -> str: ...


class OscTerminal(Terminal):
    name = "osc"

    @classmethod
    def detect(cls, env):
        return True

    def title_sequence(self, title: str) -> str:
        return f"\033]0;{title}\007"


class Iterm2Terminal(OscTerminal):
    name = "iterm2"

    @classmethod
    def detect(cls, env):
        return env.get("TERM_PROGRAM") == "iTerm.app" or env.get("LC_TERMINAL") == "iTerm2"


def detect_terminal(env: Mapping[str, str]) -> Terminal:
    if Iterm2Terminal.detect(env):
        return Iterm2Terminal()
    return OscTerminal()
