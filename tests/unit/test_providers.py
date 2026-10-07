import json

import pytest

from omc.errors import OmcError
from omc.providers.registry import get_provider, provider_names


def test_provider_names():
    assert provider_names() == ["claude", "codex"]


def test_unknown_provider():
    with pytest.raises(OmcError, match="unknown provider.*claude.*codex"):
        get_provider("cursor")


@pytest.mark.parametrize(
    ("name", "families", "efforts", "defaults"),
    [
        (
            "claude",
            ["fable", "opus", "sonnet"],
            ["low", "medium", "high", "xhigh", "max"],
            {
                "orchestrator": "opus",
                "design": "fable",
                "plan": "fable",
                "review": "fable",
                "simple": "sonnet",
                "medium": "opus",
                "high": "fable",
            },
        ),
        (
            "codex",
            ["astra", "sol"],
            ["low", "medium", "high", "xhigh", "max", "ultra"],
            {
                "orchestrator": "sol:high",
                "design": "astra",
                "plan": "astra",
                "review": "astra",
                "simple": "sol:medium",
                "medium": "sol:high",
                "high": "astra",
            },
        ),
    ],
)
def test_provider_task_contracts(name, families, efforts, defaults):
    provider = get_provider(name)
    assert provider.families() == families
    assert provider.effort_levels() == efforts
    assert {task: provider.default_task_model(task) for task in defaults} == defaults
    with pytest.raises(ValueError, match="unknown task"):
        provider.default_task_model("bogus")


def test_codex_model_list_path_uses_supplied_environment_only(tmp_path):
    provider = get_provider("codex")
    assert provider.model_list_path({"CODEX_HOME": str(tmp_path / "chosen")}) == (
        tmp_path / "chosen" / "models_cache.json"
    )
    assert provider.model_list_path({"HOME": str(tmp_path)}) == (
        tmp_path / ".codex" / "models_cache.json"
    )
    assert provider.model_list_path({"CODEX_HOME": "~/custom", "HOME": str(tmp_path)}) == (
        tmp_path / "custom" / "models_cache.json"
    )


@pytest.mark.parametrize("name", ("claude", "codex"))
def test_effort_argv_builders_append_provider_specific_flag(name):
    provider = get_provider(name)
    flag = ["--effort", "high"] if name == "claude" else ["-c", "model_reasoning_effort=high"]
    headless = provider.headless_argv("prompt", model="", effort="high")
    stream = provider.headless_stream_argv("prompt", model="", effort="high")
    session = provider.session_argv(session_name="", model="", seed="seed", effort="high")
    for argv in (headless, stream, session):
        assert argv[-2:] == flag or argv[-3:-1] == flag
        assert argv.count(flag[1]) == 1
    for argv in (
        provider.headless_argv("prompt", model="", effort=""),
        provider.headless_stream_argv("prompt", model="", effort=""),
        provider.session_argv(session_name="", model="", seed="seed", effort=""),
    ):
        assert flag[0] not in argv or (name == "codex" and flag[1] not in argv)


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
        "codex", "-c", "tui.terminal_title=[]", "-c", "tui.notifications=true", "s",
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


def test_api_backend_adapter_defaults_and_claude_values():
    claude, codex = get_provider("claude"), get_provider("codex")
    # Trailing slash is deliberate: GitNexus strips it; omc joins without "//".
    assert claude.api_base_url() == "https://api.anthropic.com/v1/"
    assert codex.api_base_url() == ""  # no API documentation backend → configure refuses `api`
    assert claude.auth_status_argv() == ["claude", "auth", "status"]
    assert codex.auth_status_argv() == []


@pytest.mark.parametrize(
    ("alias", "prefix"),
    [
        ("fable", "claude-fable-"),
        ("opus", "claude-opus-"),
        ("sonnet", "claude-sonnet-"),
        ("claude-sonnet-5-5", ""),  # a full id is not a family alias
        ("haiku", ""),  # never a family: the cheap tier is never used (tier policy)
        ("", ""),
    ],
)
def test_claude_api_model_family(alias, prefix):
    assert get_provider("claude").api_model_family(alias) == prefix
    assert get_provider("codex").api_model_family(alias) == ""


@pytest.mark.parametrize(
    "name,override,filename,default_dir",
    [
        ("claude", "CLAUDE_CONFIG_DIR", "CLAUDE.md", ".claude"),
        ("codex", "CODEX_HOME", "AGENTS.md", ".codex"),
    ],
)
def test_global_instruction_path_uses_supplied_env(tmp_path, name, override, filename, default_dir):
    provider = get_provider(name)
    env = {"HOME": str(tmp_path)}
    assert provider.instructions_file(env) == tmp_path / default_dir / filename
    env[override] = str(tmp_path / "alternate")
    assert provider.instructions_file(env) == tmp_path / "alternate" / filename
    env[override] = "~/elsewhere"
    assert provider.instructions_file(env) == tmp_path / "elsewhere" / filename


def test_global_instruction_path_requires_home_when_no_override():
    with pytest.raises(OmcError, match="HOME"):
        get_provider("claude").instructions_file({})


@pytest.mark.parametrize("enabled,value", [(True, "true"), (False, "false")])
def test_codex_native_notification_argv(enabled, value):
    argv = get_provider("codex").session_argv(
        session_name="n", model="m", seed="s", notifications=enabled
    )
    assert argv == [
        "codex",
        "-c",
        "tui.terminal_title=[]",
        "-m",
        "m",
        "-c",
        f"tui.notifications={value}",
        "s",
    ]
    assert "notify=" not in " ".join(argv)


def test_codex_native_notification_defaults_on():
    assert get_provider("codex").session_argv(session_name="", model="", seed="s") == [
        "codex",
        "-c",
        "tui.terminal_title=[]",
        "-c",
        "tui.notifications=true",
        "s",
    ]


@pytest.mark.parametrize("enabled,channel", [(True, "auto"), (False, "notifications_disabled")])
def test_claude_native_notification_fragment_and_unchanged_argv(enabled, channel):
    p = get_provider("claude")
    assert json.loads(p.notification_setup(enabled)[".claude/settings.local.json"]) == {
        "preferredNotifChannel": channel
    }
    assert p.session_argv(session_name="n", model="m", seed="s", notifications=enabled) == [
        "claude",
        "-n",
        "n",
        "--model",
        "m",
        "s",
    ]
