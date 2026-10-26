import pytest

from omc.config.schema import Config, ProviderConfig
from omc.session import session_plan
from omc.toolctx import ToolContext

from ._stubs import DEFAULT_CODEX_CATALOG, make_codex_stub, stub_env


def _ctx():
    return ToolContext.from_env({"HOME": "/nowhere", "PATH": "/nowhere", "SHELL": "/bin/bash"})


@pytest.mark.parametrize("direnv, prefix", [(False, []), (True, ["direnv", "exec", "."])])
def test_session_plan_prefixes_provider_argv_only_when_enabled(direnv, prefix):
    plan = session_plan(
        _ctx(),
        Config(),
        seed="/omc:implement",
        slug="proj-1",
        session_name="proj-1-implement",
        title="feature/proj-1",
        cwd="/w/tree",
        direnv=direnv,
    )
    assert plan.session_argv == [
        *prefix,
        "claude",
        "-n",
        "proj-1-implement",
        "--model",
        "opus",
        "/omc:implement",
    ]


def test_session_plan_defaults_to_unprefixed_provider_argv():
    plan = session_plan(
        _ctx(), Config(), seed="seed", slug="s", session_name="s", title="t", cwd="."
    )
    assert plan.session_argv == ["claude", "-n", "s", "--model", "opus", "seed"]


@pytest.mark.parametrize("direnv, prefix", [(False, []), (True, ["direnv", "exec", "."])])
def test_run_headless_prefixes_provider_argv_only_when_enabled(direnv, prefix):
    from types import SimpleNamespace

    from omc.session import run_headless

    captured = {}

    class FakeCtx:
        def run(self, argv, *, cwd=None, extra_env=None):
            captured["argv"] = argv
            captured["cwd"] = cwd
            return SimpleNamespace(stdout="", stderr="", returncode=0)

    assert (
        run_headless(
            FakeCtx(), Config(), "/omc:start", "/w/tree", "s", allowed_tools=["Bash"], direnv=direnv
        )
        == 0
    )
    assert captured == {
        "argv": [
            *prefix,
            "claude",
            "-p",
            "/omc:start",
            "--output-format",
            "text",
            "-n",
            "s",
            "--model",
            "opus",
            "--allowed-tools",
            "Bash",
        ],
        "cwd": "/w/tree",
    }


def test_session_plan_is_pure_and_names_the_session():
    plan = session_plan(
        _ctx(),
        Config(),
        seed="/omc:implement",
        slug="proj-1",
        session_name="proj-1-implement",
        title="feature/proj-1",
        cwd="<worktree>",
    )
    assert plan.session_argv[0] == "claude" and plan.session_argv[4:5] == ["opus"]
    assert plan.session_argv[plan.session_argv.index("-n") + 1] == "proj-1-implement"
    assert plan.session_argv[-1] == "/omc:implement"
    assert plan.env["OMC_SLUG"] == "proj-1"  # the slug, not the session name
    assert plan.env["OMC_PROVIDER"] == "claude"
    assert plan.env["GITNEXUS_SHARED_STORE"] == "off"
    assert plan.env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"
    assert plan.title_seq == "\033]0;feature/proj-1\007"
    assert plan.title_argv[-2:] == ["-m", "omc.terminal_title"]
    assert plan.shell_argv[0] == "bash"  # bash carries `cd <cwd>` in its rc file, not argv


def test_session_plan_wires_notifications_for_the_provider_that_takes_argv():
    cfg = Config()
    cfg.llm.default = "codex"
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-6-sol:high")
    plan = session_plan(
        _ctx(), cfg, seed="/omc:start", slug="s", session_name="s", title="t", cwd="."
    )
    assert plan.session_argv[0] == "codex"
    joined = " ".join(plan.session_argv)
    assert "tui.notifications=true" in joined and "notify=" not in joined
    assert plan.env == {"OMC_SLUG": "s", "OMC_PROVIDER": "codex", "GITNEXUS_SHARED_STORE": "off"}


def test_session_plan_resolves_codex_family_from_cli_catalog(tmp_path):
    bindir = tmp_path / "bin"
    calls = tmp_path / "codex-argv"
    make_codex_stub(bindir, catalog=DEFAULT_CODEX_CATALOG, argv_log=calls)
    ctx = ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash"))
    cfg = Config()
    cfg.llm.default = "codex"
    cfg.llm.providers["codex"] = ProviderConfig(model="sol:high")

    plan = session_plan(
        ctx, cfg, seed="/omc:implement", slug="s", session_name="s", title="t", cwd="."
    )
    assert plan.session_argv == [
        "codex",
        "-c",
        "tui.terminal_title=[]",
        "-m",
        "gpt-6-sol",
        "-c",
        "tui.notifications=true",
        "-c",
        "model_reasoning_effort=high",
        "/omc:implement",
    ]
    assert calls.read_text().splitlines() == ["debug models"]


def test_run_headless_keeps_its_shape_and_defaults():
    from types import SimpleNamespace

    from omc.session import START_ALLOWED_TOOLS, run_headless
    from omc.start import _run_headless

    assert _run_headless is run_headless  # start keeps the monkeypatch target

    captured = {}

    class FakeCtx:
        def run(self, argv, cwd=None, extra_env=None):
            captured["argv"], captured["env"] = argv, extra_env
            return SimpleNamespace(stdout="", stderr="", returncode=0)

    assert _run_headless(FakeCtx(), Config(), "/omc:start X", ".", "proj-1") == 0
    argv = captured["argv"]
    assert argv[argv.index("-n") + 1] == "proj-1"
    assert argv[argv.index("--allowed-tools") + 1 :] == START_ALLOWED_TOOLS
    assert captured["env"] == {
        "OMC_SLUG": "proj-1",
        "OMC_PROVIDER": "claude",
        "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
    }

    assert (
        _run_headless(
            FakeCtx(),
            Config(),
            "/omc:implement",
            ".",
            "proj-1",
            session_name="proj-1-implement",
            allowed_tools=["Bash", "Edit"],
        )
        == 0
    )
    argv = captured["argv"]
    assert argv[argv.index("-n") + 1] == "proj-1-implement"
    assert argv[argv.index("--allowed-tools") + 1 :] == ["Bash", "Edit"]
    assert captured["env"] == {
        "OMC_SLUG": "proj-1",
        "OMC_PROVIDER": "claude",
        "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
    }


@pytest.mark.parametrize("configured,expected", [(True, "true"), (False, "false"), (None, "true")])
def test_codex_session_native_flag_for_each_config_state(configured, expected):
    cfg = Config()
    cfg.llm.default = "codex"
    if configured is not None:
        cfg.llm.providers["codex"] = ProviderConfig(
            model="gpt-6-sol:high", notifications=configured
        )
    else:
        cfg.llm.providers["codex"] = ProviderConfig(model="gpt-6-sol:high")
    plan = session_plan(_ctx(), cfg, seed="seed", slug="s", session_name="s", title="t", cwd=".")
    assert plan.session_argv == [
        "codex",
        "-c",
        "tui.terminal_title=[]",
        "-m",
        "gpt-6-sol",
        "-c",
        f"tui.notifications={expected}",
        "-c",
        "model_reasoning_effort=high",
        "seed",
    ]
