from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

from ..herdr import osc7_report
from .base import Shell, joined_startup, joined_title


def _herdr_block(cwd: str, origin_cwd: str | None) -> list[str]:
    """Spec 2026-11-14 §4.2: host-less OSC 7 reports for herdr's sidebar, guarded in zsh.

    Reports the worktree now and, appended to the user's zshexit hooks, the origin on exit.
    """
    lines = [
        'if [ "${TERM_PROGRAM-}" = herdr ]; then',
        f"printf '%s' {shlex.quote(osc7_report(cwd))}",
    ]
    if origin_cwd:
        lines += [
            f"__omc_herdr_exit() {{ printf '%s' {shlex.quote(osc7_report(origin_cwd))} }}",
            "zshexit_functions+=(__omc_herdr_exit)",
        ]
    return [*lines, "fi"]


class ZshShell(Shell):
    name = "zsh"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "zsh"

    def build_invocation(
        self, *, cwd, title, startup_argv, title_seq, title_argv=None, origin_cwd=None
    ):
        lines = [
            '[ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc"',
            f"precmd() {{ printf '%s' {shlex.quote(title_seq)} }}",
            f"cd {shlex.quote(cwd)}",
            # precmd only fires at the first prompt — AFTER the startup session
            # exits — so emit the title once, up front, before the session runs.
            f"printf '%s' {shlex.quote(title_seq)}",
        ]
        title_command = joined_title(title_argv, title)
        if title_command:
            lines.append(f"{title_command} || :")
        lines += _herdr_block(cwd, origin_cwd)
        startup = joined_startup(startup_argv)
        if startup:
            lines.append(startup)
        return ["zsh", "-i"], {".zshrc": "\n".join(lines) + "\n"}

    def exec_env_overrides(self, tmpdir):
        return {"ZDOTDIR": tmpdir} if tmpdir else {}
