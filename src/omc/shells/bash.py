from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

from ..herdr import osc7_report
from .base import TMPDIR_PLACEHOLDER, Shell, joined_startup, joined_title

_RC_NAME = "rc.bash"


def _herdr_block(cwd: str, origin_cwd: str | None) -> list[str]:
    """Spec 2026-11-14 §4.2: host-less OSC 7 reports for herdr's sidebar, guarded in bash.

    Reports the worktree now and the origin from an EXIT trap that chains the user's
    own EXIT trap (word 2 of `trap -p EXIT`, re-split by eval) after the report.
    """
    lines = [
        'if [ "${TERM_PROGRAM-}" = herdr ]; then',
        f"printf '%s' {shlex.quote(osc7_report(cwd))}",
    ]
    if origin_cwd:
        lines += [
            "__omc_herdr_prev_exit=$(trap -p EXIT)",
            'eval "__omc_herdr_prev_exit=(${__omc_herdr_prev_exit})"',
            f"trap -- \"printf '%s' {shlex.quote(osc7_report(origin_cwd))}; "
            '${__omc_herdr_prev_exit[2]-}" EXIT',
        ]
    return [*lines, "fi"]


class BashShell(Shell):
    name = "bash"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "bash"

    def build_invocation(
        self, *, cwd, title, startup_argv, title_seq, title_argv=None, origin_cwd=None
    ):
        rcfile = f"{TMPDIR_PLACEHOLDER}/{_RC_NAME}"
        lines = [
            '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"',
            f"PROMPT_COMMAND={shlex.quote(f'printf %s {shlex.quote(title_seq)}')}",
            f"cd {shlex.quote(cwd)}",
            # PROMPT_COMMAND only fires at the first prompt — AFTER the startup
            # session exits — so emit the title once, up front, before it runs.
            f"printf '%s' {shlex.quote(title_seq)}",
        ]
        title_command = joined_title(title_argv, title)
        if title_command:
            lines.append(f"{title_command} || :")
        lines += _herdr_block(cwd, origin_cwd)
        startup = joined_startup(startup_argv)
        if startup:
            lines.append(startup)
        return ["bash", "--rcfile", rcfile, "-i"], {_RC_NAME: "\n".join(lines) + "\n"}
