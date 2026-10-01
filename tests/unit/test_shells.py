import shlex
import sys

import pytest

from omc.fish_integration import fish_hook_path
from omc.shells.base import TMPDIR_PLACEHOLDER
from omc.shells.registry import detect_shell

ARGS = dict(
    cwd="/w/tree",
    title="proj-1-fix",
    startup_argv=["claude", "-n", "proj-1-fix", "/omc:start PROJ-1"],
    title_seq="\033]0;proj-1-fix\007",
)


def test_detect_by_shell_env():
    assert detect_shell({"SHELL": "/usr/bin/fish"}).name == "fish"
    assert detect_shell({"SHELL": "/bin/zsh"}).name == "zsh"
    assert detect_shell({"SHELL": "/bin/bash"}).name == "bash"
    assert detect_shell({}).name == "sh"


def test_fish_invocation_inline():
    argv, files = detect_shell({"SHELL": "fish"}).build_invocation(**ARGS)
    assert argv[:3] == ["fish", "-i", "-C"]
    body = argv[3]
    assert "cd /w/tree" in body and "fish_title" in body
    assert shlex.join(ARGS["startup_argv"]) in body
    assert files == {}


def test_zsh_invocation_writes_zshrc():
    shell = detect_shell({"SHELL": "zsh"})
    argv, files = shell.build_invocation(**ARGS)
    assert argv == ["zsh", "-i"]
    rc = files[".zshrc"]
    assert 'source "$HOME/.zshrc"' in rc and "cd /w/tree" in rc
    assert shlex.join(ARGS["startup_argv"]) in rc
    assert shell.exec_env_overrides("/tmp/x") == {"ZDOTDIR": "/tmp/x"}


def test_bash_invocation_rcfile_placeholder():
    argv, files = detect_shell({"SHELL": "bash"}).build_invocation(**ARGS)
    assert argv[0] == "bash" and "--rcfile" in argv and "-i" in argv
    assert any(TMPDIR_PLACEHOLDER in a for a in argv)
    assert "rc.bash" in files


def test_sh_fallback_runs_startup():
    argv, files = detect_shell({}).build_invocation(**ARGS)
    assert argv[:2] == ["sh", "-c"]
    assert shlex.join(ARGS["startup_argv"]) in argv[2]
    assert files == {}


def test_zsh_and_bash_emit_title_before_startup():
    # The prompt hooks (precmd / PROMPT_COMMAND) only fire after the startup
    # session exits; the rc file must ALSO emit the title before the session.
    for shell_env in ({"SHELL": "zsh"}, {"SHELL": "bash"}):
        _, files = detect_shell(shell_env).build_invocation(**ARGS)
        rc = next(iter(files.values()))
        printf_at = rc.index("printf '%s' " + shlex.quote(ARGS["title_seq"]))
        startup_at = rc.index(shlex.join(ARGS["startup_argv"]))
        assert printf_at < startup_at, f"title must precede startup in:\n{rc}"


@pytest.mark.parametrize("shell_name", ["fish", "bash", "zsh", "sh"])
def test_title_helper_runs_before_startup_for_every_shell(shell_name):
    argv, files = detect_shell({"SHELL": shell_name}).build_invocation(
        **ARGS, title_argv=["/usr/bin/title-helper"]
    )
    body = (
        argv[3]
        if shell_name == "fish"
        else argv[2]
        if shell_name == "sh"
        else next(iter(files.values()))
    )
    helper_call = (
        '/usr/bin/title-helper "$__omc_desired_title"'
        if shell_name == "fish"
        else "/usr/bin/title-helper proj-1-fix"
    )
    assert body.index(helper_call) < body.index(shlex.join(ARGS["startup_argv"]))


def test_exec_interactive_forwards_title_helper(monkeypatch):
    import omc.shells.base as base

    shell = detect_shell({})
    monkeypatch.setattr(base.os, "chdir", lambda path: None)
    executed = []
    monkeypatch.setattr(base.os, "execvp", lambda program, argv: executed.append(argv))
    shell.exec_interactive(**ARGS, title_argv=["/usr/bin/title-helper"])
    assert executed and "/usr/bin/title-helper proj-1-fix" in executed[0][2]


FISH_TITLE_ARGV = ["/usr/bin/python3", "-m", "omc.terminal_title"]


def _inline_else(helper, title, cwd):
    """Today's inline fish title code, frozen byte for byte (spec §6)."""
    return "; ".join(
        [
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
            "__omc_refresh_title",
        ]
    )


def test_fish_generated_command_is_one_if_else_around_the_hook(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python3")
    argv, files = detect_shell({"SHELL": "fish"}).build_invocation(
        **ARGS, title_argv=FISH_TITLE_ARGV
    )
    assert files == {} and argv[:3] == ["fish", "-i", "-C"]
    body = argv[3]
    assert body.count("; else; ") == 1
    if_branch, rest = body.split("; else; ", 1)
    else_branch, startup = rest.rsplit("; end; ", 1)
    assert startup == shlex.join(ARGS["startup_argv"])
    predicate = if_branch.split("; set -g __omc_title_helper", 1)[0]
    assert predicate == (
        "if status is-interactive; and string match -rq -- "
        "'^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$' \"$ITERM_SESSION_ID\"; "
        'and begin; test "$TERM_PROGRAM" = iTerm.app; or test "$LC_TERMINAL" = iTerm2; end; '
        'and not set -q TMUX; and not set -q STY; and test "$OMC_FISH_TITLE_DISABLE" != 1'
    )
    steps = [
        "set -g __omc_title_helper /opt/py/bin/python3 -m omc",
        f"source {shlex.quote(str(fish_hook_path()))}",
        "cd /w/tree",
        "__omc_title_refresh",
    ]
    positions = [if_branch.index(step) for step in steps]
    assert positions == sorted(positions) and if_branch.endswith("__omc_title_refresh")
    assert else_branch == _inline_else(shlex.join(FISH_TITLE_ARGV), "proj-1-fix", "/w/tree")
    assert '/usr/bin/python3 -m omc.terminal_title "$__omc_desired_title"' in else_branch


def test_fish_without_title_argv_is_unchanged():
    argv, _ = detect_shell({"SHELL": "fish"}).build_invocation(**ARGS)
    assert "; else; " not in argv[3] and "__omc_title_helper" not in argv[3]
    assert argv[3] == (
        "function fish_title; echo proj-1-fix; end; cd /w/tree; "
        f"printf '%s' {shlex.quote(ARGS['title_seq'])}; {shlex.join(ARGS['startup_argv'])}"
    )


def test_fish_dry_run_output_is_terminal_independent():
    # The builder never reads the environment: identical output whatever the terminal.
    shell = detect_shell({"SHELL": "fish"})
    first, _ = shell.build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    second, _ = shell.build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    assert first == second
