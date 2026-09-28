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

    def build_invocation(self, *, cwd, title, startup_argv, title_seq, title_argv=None):
        if title_argv:
            helper = shlex.join(title_argv)
            parts = [
                f"set -g __omc_desired_title {shlex.quote(title)}",
                "set -g __omc_last_attempt ''",
                'function fish_title; printf "%s\\n" "$__omc_desired_title"; end',
                "function __omc_refresh_title",
                "set -l saved_status $status",
                "set -l branch (command git branch --show-current 2>/dev/null)",
                'if test $status -eq 0; and test -n "$branch"; '
                'set -g __omc_desired_title "$branch"; end',
                'if test "$__omc_last_attempt" != "$__omc_desired_title"',
                'set -g __omc_last_attempt "$__omc_desired_title"',
                f'{helper} "$__omc_desired_title"; or true',
                "end",
                "return $saved_status",
                "end",
                "function __omc_title_preexec --on-event fish_preexec; __omc_refresh_title; end",
                "function __omc_title_prompt --on-event fish_prompt; __omc_refresh_title; end",
                "function __omc_title_pwd --on-variable PWD; __omc_refresh_title; end",
                f"cd {shlex.quote(cwd)}",
                "__omc_refresh_title",  # -C startup need not emit fish_preexec.
            ]
        else:
            parts = [
                f"function fish_title; echo {shlex.quote(title)}; end",
                f"cd {shlex.quote(cwd)}",
                f"printf '%s' {shlex.quote(title_seq)}",
            ]
        startup = joined_startup(startup_argv)
        if startup:
            parts.append(startup)
        return ["fish", "-i", "-C", "; ".join(parts)], {}
