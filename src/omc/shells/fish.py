from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

from .base import Shell, joined_startup


class FishShell(Shell):
    name = "fish"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "fish"

    def build_invocation(self, *, cwd, title, startup_argv, title_seq):
        parts = [
            f"function fish_title; echo {shlex.quote(title)}; end",
            f"cd {shlex.quote(cwd)}",
            f"printf '%s' {shlex.quote(title_seq)}",
        ]
        startup = joined_startup(startup_argv)
        if startup:
            parts.append(startup)
        return ["fish", "-i", "-C", "; ".join(parts)], {}
