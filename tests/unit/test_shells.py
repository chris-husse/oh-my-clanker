import shlex

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
