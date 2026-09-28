from __future__ import annotations

from collections.abc import Mapping

from .base import Shell, joined_startup, joined_title
from .bash import BashShell
from .fish import FishShell
from .zsh import ZshShell


class ShShell(Shell):
    """POSIX-sh fallback: no portable prompt hook; just run the startup command."""

    name = "sh"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return True

    def build_invocation(self, *, cwd, title, startup_argv, title_seq, title_argv=None):
        startup = joined_startup(startup_argv)
        title_command = joined_title(title_argv, title)
        command = (
            f"{title_command} || :; {startup or 'exec sh'}"
            if title_command
            else startup or "exec sh"
        )
        return ["sh", "-c", command], {}


_SHELLS: tuple[type[Shell], ...] = (FishShell, ZshShell, BashShell)


def detect_shell(env: Mapping[str, str]) -> Shell:
    for cls in _SHELLS:
        if cls.detect(env):
            return cls()
    return ShShell()
