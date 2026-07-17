from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

from .base import TMPDIR_PLACEHOLDER, Shell, joined_startup

_RC_NAME = "rc.bash"


class BashShell(Shell):
    name = "bash"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "bash"

    def build_invocation(self, *, cwd, title, startup_argv, title_seq):
        rcfile = f"{TMPDIR_PLACEHOLDER}/{_RC_NAME}"
        lines = [
            '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"',
            f"PROMPT_COMMAND={shlex.quote(f'printf %s {shlex.quote(title_seq)}')}",
            f"cd {shlex.quote(cwd)}",
            # PROMPT_COMMAND only fires at the first prompt — AFTER the startup
            # session exits — so emit the title once, up front, before it runs.
            f"printf '%s' {shlex.quote(title_seq)}",
        ]
        startup = joined_startup(startup_argv)
        if startup:
            lines.append(startup)
        return ["bash", "--rcfile", rcfile, "-i"], {_RC_NAME: "\n".join(lines) + "\n"}
