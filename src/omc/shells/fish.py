from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping

from ..fish_integration import fish_hook_path
from .base import Shell, joined_startup

# The spec §2 activation predicate plus the per-session opt-out, evaluated by fish at
# startup. The builder itself never reads the environment (pure; --dry-run identical
# in every terminal).
_ITERM2_HOOK_PREDICATE = (
    "status is-interactive; and string match -rq -- "
    "'^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$' \"$ITERM_SESSION_ID\"; "
    'and begin; test "$TERM_PROGRAM" = iTerm.app; or test "$LC_TERMINAL" = iTerm2; end; '
    'and not set -q TMUX; and not set -q STY; and test "$OMC_FISH_TITLE_DISABLE" != 1'
)


class FishShell(Shell):
    name = "fish"

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> bool:
        return os.path.basename(env.get("SHELL", "")) == "fish"

    def build_invocation(self, *, cwd, title, startup_argv, title_seq, title_argv=None):
        if title_argv:
            helper = shlex.join(title_argv)
            inline = [
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
            # In iTerm2 the packaged hook (sourced here even when conf.d never got it or
            # was disabled) owns the title; the explicit refresh is the shell's first,
            # so the cd's PWD event is ignored and the first prompt after the provider
            # exits is a no-op on an unchanged branch (spec §6).
            hooked = [
                f"set -g __omc_title_helper {shlex.join([sys.executable, '-m', 'omc'])}",
                f"source {shlex.quote(str(fish_hook_path()))}",
                f"cd {shlex.quote(cwd)}",
                "__omc_title_refresh",
            ]
            parts = [f"if {_ITERM2_HOOK_PREDICATE}", *hooked, "else", *inline, "end"]
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
