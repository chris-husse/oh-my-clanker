import json

import pytest

from omc.errors import OmcError
from omc.providers.registry import get_provider, provider_names


def test_provider_names():
    assert provider_names() == ["claude", "codex"]


def test_unknown_provider():
    with pytest.raises(OmcError, match="unknown provider.*claude.*codex"):
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
    assert p.session_argv(session_name="n", model="", seed="s") == [
        "codex", "-c", "tui.terminal_title=[]", "s",
    ]  # fmt: skip
    assert p.title_env() == {}


def test_install_hints():
    for name in provider_names():
        assert "npm install -g" in get_provider(name).install_hint()


def test_headless_session_name():
    # claude names headless sessions (-n works with -p; resumable by name —
    # verified live); codex has no naming flag and ignores it.
    c = get_provider("claude").headless_argv("x", model="", session_name="s-1")
    assert c[: c.index("-n") + 2] == ["claude", "-p", "x", "--output-format", "text", "-n", "s-1"]
    assert "-n" not in get_provider("codex").headless_argv("x", model="", session_name="s-1")


SINK = ["omc", "internal", "notify", "--provider", "X"]


def test_claude_notification_setup_settings_file():
    files = get_provider("claude").notification_setup(
        ["omc", "internal", "notify", "--provider", "claude"]
    )
    assert list(files) == [".claude/settings.local.json"]
    settings = json.loads(files[".claude/settings.local.json"])
    cmd = "omc internal notify --provider claude"
    for event in ("Notification", "Stop"):
        (group,) = settings["hooks"][event]
        assert group["hooks"] == [{"type": "command", "command": cmd}]
    # Notification is UNFILTERED (no matcher key): all attention events ping
    assert "matcher" not in settings["hooks"]["Notification"][0]


def test_codex_notify_sink_argv_before_seed():
    p = get_provider("codex")
    sink = ["omc", "internal", "notify", "--provider", "codex"]
    argv = p.session_argv(session_name="n", model="m", seed="s", notify_sink_argv=sink)
    # -c value is TOML; a JSON array of strings is valid TOML array syntax,
    # and the flag must come BEFORE the trailing positional seed
    assert argv == [
        "codex", "-c", "tui.terminal_title=[]", "-m", "m",
        "-c", f"notify={json.dumps(sink)}", "s",
    ]  # fmt: skip
    assert p.session_argv(session_name="n", model="", seed="s") == [
        "codex", "-c", "tui.terminal_title=[]", "s",
    ]  # fmt: skip
    assert p.notification_setup(sink) == {}  # codex wiring is argv-only


def test_notification_setup_defaults_and_purity(tmp_path, monkeypatch):
    # default is {}; and no provider touches the filesystem or spawns anything
    monkeypatch.chdir(tmp_path)
    for name in provider_names():
        p = get_provider(name)
        p.notification_setup(SINK)
        p.session_argv(session_name="n", model="", seed="s", notify_sink_argv=SINK)
    assert list(tmp_path.iterdir()) == []


def test_claude_ignores_notify_sink_argv():
    # Claude's wiring is a file; argv must stay identical with/without the param.
    p = get_provider("claude")
    with_arg = p.session_argv(session_name="n", model="m", seed="s", notify_sink_argv=SINK)
    without = p.session_argv(session_name="n", model="m", seed="s")
    assert with_arg == without


def test_plugin_update_argvs_are_pure_and_per_provider():
    from omc.providers.registry import get_provider

    claude = get_provider("claude").plugin_update_argvs()
    assert ["claude", "plugin", "marketplace", "update", "oh-my-clanker"] in claude
    assert ["claude", "plugin", "update", "omc@oh-my-clanker"] in claude
    codex = get_provider("codex").plugin_update_argvs()
    assert codex == [["codex", "plugin", "marketplace", "upgrade"]]


def test_claude_plugin_update_prepends_marketplace_add():
    from omc.providers.claude import ClaudeProvider

    argvs = ClaudeProvider().plugin_update_argvs("chris-husse/oh-my-clanker")
    assert argvs[0] == ["claude", "plugin", "marketplace", "add", "chris-husse/oh-my-clanker"]
    assert ["claude", "plugin", "marketplace", "update", "oh-my-clanker"] in argvs
    assert ["claude", "plugin", "update", "omc@oh-my-clanker"] in argvs


def test_claude_plugin_update_without_source_omits_add():
    from omc.providers.claude import ClaudeProvider

    argvs = ClaudeProvider().plugin_update_argvs()
    assert not any("add" in a for a in argvs)  # no source → no marketplace add


def test_codex_ignores_marketplace_source():
    from omc.providers.codex import CodexProvider

    assert CodexProvider().plugin_update_argvs("anything") == [
        ["codex", "plugin", "marketplace", "upgrade"]
    ]


# Captured from a real `claude -p --output-format stream-json --verbose` run
# (2026-07-19 probe) — shapes, not verbatim transcripts.
_SJ_ASSISTANT_TEXT = (
    '{"type":"assistant","message":{"content":[{"type":"text",'
    '"text":"Build stage passed.\\nOMC_STAGE {\\"passed\\": true}"}]}}'
)
_SJ_TOOL_USE = (
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
    '"input":{"command":"just build","description":"Run build"}}]}}'
)
_SJ_TOOL_RESULT_STR = (
    '{"type":"user","message":{"content":[{"type":"tool_result",'
    '"content":"Compiling foo (12/1288)\\nok"}]}}'
)
_SJ_TOOL_RESULT_LIST = (
    '{"type":"user","message":{"content":[{"type":"tool_result",'
    '"content":[{"type":"text","text":"251 passed"}]}]}}'
)
_SJ_RESULT = '{"type":"result","result":"done\\nOMC_STAGE {\\"passed\\": true}"}'
_SJ_SYSTEM = '{"type":"system","subtype":"init"}'
_SJ_THINKING = '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"hmm"}]}}'


def test_claude_stream_argv_uses_stream_json():
    p = get_provider("claude")
    argv = p.headless_stream_argv("do it", model="m1", allowed_tools=["Bash"])
    assert argv[:3] == ["claude", "-p", "do it"]
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv
    assert argv[-2:] == ["--allowed-tools", "Bash"]  # allowed-tools stays LAST (variadic)


def test_claude_decode_assistant_text_splits_lines():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_ASSISTANT_TEXT) == [
        "Build stage passed.",
        'OMC_STAGE {"passed": true}',
    ]


def test_claude_decode_tool_use_echoes_command():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_TOOL_USE) == ["$ just build"]


def test_claude_decode_tool_result_string_and_list():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_TOOL_RESULT_STR) == ["Compiling foo (12/1288)", "ok"]
    assert p.decode_stream_line(_SJ_TOOL_RESULT_LIST) == ["251 passed"]


def test_claude_decode_result_event_carries_final_text():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_RESULT) == ["done", 'OMC_STAGE {"passed": true}']


def test_claude_decode_skips_system_and_thinking():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_SYSTEM) == []
    assert p.decode_stream_line(_SJ_THINKING) == []


def test_claude_decode_passes_non_json_through():
    p = get_provider("claude")
    assert p.decode_stream_line("plain warning line") == ["plain warning line"]
    assert p.decode_stream_line("   ") == []  # blank noise dropped


def test_codex_stream_defaults_are_identity():
    p = get_provider("codex")
    assert p.headless_stream_argv("x", model="") == p.headless_argv("x", model="")
    assert p.decode_stream_line("anything") == ["anything"]


def test_notifies_natively_flags():
    # claude: the harness posts its own clickable desktop notification;
    # codex: no native channel — omc's alert is its only one.
    assert get_provider("claude").notifies_natively() is True
    assert get_provider("codex").notifies_natively() is False
