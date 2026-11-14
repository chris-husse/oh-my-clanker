import os
import shlex
import shutil
import subprocess
import sys

import pytest

from omc.config.schema import Config
from omc.fish_integration import fish_hook_path
from omc.herdr import osc7_report
from omc.session import session_plan
from omc.shells.base import TMPDIR_PLACEHOLDER
from omc.shells.registry import detect_shell
from omc.toolctx import ToolContext

ARGS = dict(
    cwd="/w/tree",
    title="proj-1-fix",
    startup_argv=["claude", "-n", "proj-1-fix", "/omc:start PROJ-1"],
    title_seq="\033]0;proj-1-fix\007",
)


def fish_herdr(origin=None):
    parts = [
        'if test "$TERM_PROGRAM" = herdr',
        "function __omc_herdr_report; "
        "printf '\\e]7;file://%s\\a' (string escape --style=url -- $argv[1]); end",
        "__omc_herdr_report $PWD",
        "function __omc_herdr_pwd --on-variable PWD; __omc_herdr_report $PWD; end",
    ]
    if origin:
        parts.append(
            f"function __omc_herdr_exit --on-event fish_exit; "
            f"__omc_herdr_report {shlex.quote(origin)}; end"
        )
    return "; ".join([*parts, "end"])


def zsh_herdr(cwd, origin=None):
    lines = [
        'if [ "${TERM_PROGRAM-}" = herdr ]; then',
        f"printf '%s' {shlex.quote(osc7_report(cwd))}",
    ]
    if origin:
        lines += [
            f"__omc_herdr_exit() {{ printf '%s' {shlex.quote(osc7_report(origin))} }}",
            "zshexit_functions+=(__omc_herdr_exit)",
        ]
    return "\n".join([*lines, "fi"])


def bash_herdr(cwd, origin=None):
    lines = [
        'if [ "${TERM_PROGRAM-}" = herdr ]; then',
        f"printf '%s' {shlex.quote(osc7_report(cwd))}",
    ]
    if origin:
        lines += [
            "__omc_herdr_prev_exit=$(trap -p EXIT)",
            'eval "__omc_herdr_prev_exit=(${__omc_herdr_prev_exit})"',
            f"trap -- \"printf '%s' {shlex.quote(osc7_report(origin))}; "
            '${__omc_herdr_prev_exit[2]-}" EXIT',
        ]
    return "\n".join([*lines, "fi"])


def test_detect_by_shell_env():
    assert detect_shell({"SHELL": "/usr/bin/fish"}).name == "fish"
    assert detect_shell({"SHELL": "/bin/zsh"}).name == "zsh"
    assert detect_shell({"SHELL": "/bin/bash"}).name == "bash"
    assert detect_shell({}).name == "sh"


@pytest.mark.parametrize("shell_name", ["fish", "zsh", "bash", "sh"])
def test_direnv_launch_follows_worktree_cd(shell_name, monkeypatch):
    ctx = ToolContext.from_env({"HOME": "/nowhere", "PATH": "/nowhere", "SHELL": shell_name})
    plan = session_plan(
        ctx,
        Config(),
        seed="seed",
        slug="s",
        session_name="s",
        title="t",
        cwd="/w/tree",
        direnv=True,
    )
    if shell_name == "fish":
        assert plan.shell_argv[:3] == ["fish", "-i", "-C"]
        body = plan.shell_argv[3]
    elif shell_name == "sh":
        import omc.shells.base as base

        seen = []
        monkeypatch.setattr(base.os, "chdir", lambda path: seen.append(("cd", path)))
        monkeypatch.setattr(base.os, "execvp", lambda program, argv: seen.append(("exec", argv)))
        detect_shell(ctx.env).exec_interactive(
            cwd="/w/tree",
            title="t",
            startup_argv=plan.session_argv,
            title_seq=plan.title_seq,
            title_argv=plan.title_argv,
        )
        assert seen[0] == ("cd", "/w/tree")
        assert seen[1][1][2].endswith("direnv exec . claude -n s --model opus seed")
        return
    else:
        _, files = detect_shell(ctx.env).build_invocation(
            cwd="/w/tree",
            title="t",
            startup_argv=plan.session_argv,
            title_seq=plan.title_seq,
            title_argv=plan.title_argv,
        )
        body = next(iter(files.values()))
    assert body.index("cd /w/tree") < body.index("direnv exec . claude")


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
    startup = shlex.join(ARGS["startup_argv"])
    assert body.endswith("; end; " + fish_herdr() + "; " + startup)
    body = body.replace("; " + fish_herdr(), "", 1)
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


def test_fish_without_title_argv_exact_text():
    argv, _ = detect_shell({"SHELL": "fish"}).build_invocation(**ARGS)
    assert "; else; " not in argv[3] and "__omc_title_helper" not in argv[3]
    assert argv[3] == (
        "function fish_title; echo proj-1-fix; end; cd /w/tree; "
        f"printf '%s' {shlex.quote(ARGS['title_seq'])}; {fish_herdr()}; "
        f"{shlex.join(ARGS['startup_argv'])}"
    )


def test_fish_dry_run_output_is_terminal_independent():
    # The builder never reads the environment: identical output whatever the terminal.
    shell = detect_shell({"SHELL": "fish"})
    first, _ = shell.build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    second, _ = shell.build_invocation(**ARGS, title_argv=FISH_TITLE_ARGV)
    assert first == second


@pytest.mark.parametrize("title_argv", [None, FISH_TITLE_ARGV])
def test_fish_herdr_block_sits_between_title_code_and_startup(title_argv):
    argv, _ = detect_shell({"SHELL": "fish"}).build_invocation(
        **ARGS, title_argv=title_argv, origin_cwd="/launch dir"
    )
    body = argv[3]
    block = fish_herdr("/launch dir")
    assert (
        body.index("cd /w/tree") < body.index(block) < body.index(shlex.join(ARGS["startup_argv"]))
    )
    if title_argv == FISH_TITLE_ARGV:
        assert "; end; " + block + "; " in body


def test_fish_herdr_exit_handler_only_with_origin():
    argv, _ = detect_shell({"SHELL": "fish"}).build_invocation(**ARGS, origin_cwd=None)
    body = argv[3]
    assert fish_herdr() in body and "__omc_herdr_exit" not in body


@pytest.mark.parametrize("origin", [None, "/launch dir"])
@pytest.mark.parametrize("shell_name", ["zsh", "bash"])
def test_zsh_and_bash_herdr_block_after_cd_before_startup(shell_name, origin):
    _, files = detect_shell({"SHELL": shell_name}).build_invocation(**ARGS, origin_cwd=origin)
    rc = next(iter(files.values()))
    block = (zsh_herdr if shell_name == "zsh" else bash_herdr)("/w/tree", origin)
    startup = shlex.join(ARGS["startup_argv"])
    assert rc.index("cd /w/tree") < rc.index(block) < rc.index(startup)
    if origin is None:
        assert "__omc_herdr_exit" not in rc and "trap -- " not in rc


def test_sh_ignores_origin_cwd():
    shell = detect_shell({})
    assert shell.build_invocation(**ARGS) == shell.build_invocation(
        **ARGS, origin_cwd="/launch dir"
    )


def test_exec_interactive_forwards_origin_cwd(monkeypatch):
    import omc.shells.base as base
    from omc.shells.fish import FishShell

    monkeypatch.setattr(base.os, "chdir", lambda path: None)
    executed = []
    monkeypatch.setattr(base.os, "execvp", lambda program, argv: executed.append(argv))
    FishShell().exec_interactive(**ARGS, origin_cwd="/o")
    assert executed and "__omc_herdr_report /o; end" in executed[0][3]


def _run_bash_session(
    tmp_path, *, wt, bashrc, startup_argv, origin_cwd, term_program=None
) -> subprocess.CompletedProcess:
    """Run an interactive bash on omc's generated rcfile for worktree ``wt`` and exit."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.fail("bash is required: sudo apt-get install bash")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text(bashrc)
    rcdir = tmp_path / "rc"
    rcdir.mkdir()
    argv, files = detect_shell({"SHELL": "bash"}).build_invocation(
        cwd=str(wt),
        title="t",
        startup_argv=startup_argv,
        title_seq="",
        origin_cwd=origin_cwd,
    )
    for name, contents in files.items():
        (rcdir / name).write_text(contents)
    rc = next(a for a in argv if TMPDIR_PLACEHOLDER in a).replace(TMPDIR_PLACEHOLDER, str(rcdir))
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    if term_program is not None:
        env["TERM_PROGRAM"] = term_program
    return subprocess.run(
        [bash, "--rcfile", rc, "-i"],
        input="exit\n",
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )


@pytest.mark.parametrize(
    ("bashrc", "user_tail"),
    [
        ("", ""),
        ('trap -- "echo USER_BYE" EXIT\n', "USER_BYE\n"),
        ("set -u\n", ""),
    ],
    ids=["no-user-trap", "user-exit-trap", "set-u-no-trap"],
)
def test_bash_exit_report_keeps_user_exit_trap(tmp_path, bashrc, user_tail):
    wt = tmp_path / "wt dir"
    wt.mkdir()
    launch = tmp_path / "launch dir"
    launch.mkdir()
    proc = _run_bash_session(
        tmp_path,
        wt=wt,
        bashrc=bashrc,
        startup_argv=["true"],
        origin_cwd=str(launch),
        term_program="herdr",
    )
    assert proc.stdout == osc7_report(str(wt)) + osc7_report(str(launch)) + user_tail
    assert "unbound variable" not in proc.stderr


def test_bash_herdr_guard_tolerates_set_u_without_term_program(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    proc = _run_bash_session(
        tmp_path,
        wt=wt,
        bashrc="set -u\n",
        startup_argv=["printf", "STARTED\\n"],
        origin_cwd=str(tmp_path),
    )
    assert "unbound variable" not in proc.stderr
    assert "\x1b]7;" not in proc.stdout
    assert proc.stdout == "STARTED\n"
