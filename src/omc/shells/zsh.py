from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

from .base import Shell, joined_startup


class ZshShell(Shell):
    name = "zsh"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "zsh"

    def build_invocation(self, *, cwd, title, startup_argv, title_seq):
        lines = [
            '[ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc"',
            f"precmd() {{ printf '%s' {shlex.quote(title_seq)} }}",
            f"cd {shlex.quote(cwd)}",
        ]
        startup = joined_startup(startup_argv)
        if startup:
            lines.append(startup)
        return ["zsh", "-i"], {".zshrc": "\n".join(lines) + "\n"}

    def exec_env_overrides(self, tmpdir):
        return {"ZDOTDIR": tmpdir} if tmpdir else {}
