import pytest

from omc.errors import OmcError
from omc.providers.registry import get_provider, provider_names


def test_provider_names():
    assert provider_names() == ["claude", "codex", "opencode"]


def test_unknown_provider():
    with pytest.raises(OmcError, match="unknown provider"):
        get_provider("cursor")


def test_claude_headless_tools_last():
    p = get_provider("claude")
    argv = p.headless_argv("do it", model="m1", allowed_tools=["mcp__jira"])
    assert argv[:3] == ["claude", "-p", "do it"]
    assert argv[-2:] == ["--allowed-tools", "mcp__jira"]  # variadic flag stays LAST
    assert "--model" in argv and argv[argv.index("--model") + 1] == "m1"
    # no allowed_tools -> flag omitted entirely (empty value parses as a bogus tool)
    assert "--allowed-tools" not in p.headless_argv("x", model="")


def test_claude_session():
    p = get_provider("claude")
    argv = p.session_argv(session_name="proj-1-fix", model="m1", seed="/omc:start PROJ-1")
    assert argv == ["claude", "-n", "proj-1-fix", "--model", "m1", "/omc:start PROJ-1"]
    assert p.session_argv(session_name="", model="", seed="s") == ["claude", "s"]
    assert p.title_env() == {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}


def test_codex_argv():
    p = get_provider("codex")
    assert p.headless_argv("x", model="m") == [
        "codex", "exec", "--skip-git-repo-check", "-m", "m", "x",
    ]  # fmt: skip
    assert p.headless_argv("x", model="", allowed_tools=["a"]) == [
        "codex", "exec", "--skip-git-repo-check", "x",
    ]  # fmt: skip
    # no session-name flag exists; seed is the trailing positional
    assert p.session_argv(session_name="n", model="", seed="s") == ["codex", "s"]
    assert p.title_env() == {}


def test_opencode_argv():
    p = get_provider("opencode")
    assert p.headless_argv("x", model="a/m") == ["opencode", "run", "-m", "a/m", "x"]
    # interactive positional is a DIRECTORY, so the seed rides on --prompt
    assert p.session_argv(session_name="n", model="a/m", seed="s") == [
        "opencode",
        "-m",
        "a/m",
        "--prompt",
        "s",
    ]
    assert p.title_env() == {"OPENCODE_DISABLE_TERMINAL_TITLE": "1"}


def test_install_hints():
    for name in provider_names():
        assert "npm install -g" in get_provider(name).install_hint()


def test_headless_session_name():
    # claude names headless sessions (-n works with -p; resumable by name —
    # verified live); codex/opencode have no naming flag and ignore it.
    c = get_provider("claude").headless_argv("x", model="", session_name="s-1")
    assert c[: c.index("-n") + 2] == ["claude", "-p", "x", "--output-format", "text", "-n", "s-1"]
    assert "-n" not in get_provider("codex").headless_argv("x", model="", session_name="s-1")
    assert "-n" not in get_provider("opencode").headless_argv("x", model="", session_name="s-1")
