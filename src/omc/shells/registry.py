from __future__ import annotations

from collections.abc import Mapping

from .base import Shell, joined_startup
from .bash import BashShell
from .fish import FishShell
from .zsh import ZshShell


class ShShell(Shell):
    """POSIX-sh fallback: no portable prompt hook; just run the startup command."""

    name = "sh"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return True

    def build_invocation(self, *, cwd, title, startup_argv, title_seq):
        startup = joined_startup(startup_argv)
        return ["sh", "-c", startup or "exec sh"], {}


_SHELLS: tuple[type[Shell], ...] = (FishShell, ZshShell, BashShell)


def detect_shell(env: Mapping[str, str]) -> Shell:
    for cls in _SHELLS:
        if cls.detect(env):
            return cls()
    return ShShell()
