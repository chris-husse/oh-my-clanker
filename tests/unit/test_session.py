from omc.config.schema import Config, NotificationsConfig
from omc.session import session_plan
from omc.toolctx import ToolContext


def _ctx():
    return ToolContext.from_env({"HOME": "/nowhere", "PATH": "/nowhere", "SHELL": "/bin/bash"})


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
    assert plan.session_argv[0] == "claude" and "--model" not in plan.session_argv
    assert plan.session_argv[plan.session_argv.index("-n") + 1] == "proj-1-implement"
    assert plan.session_argv[-1] == "/omc:implement"
    assert plan.env["OMC_SLUG"] == "proj-1"  # the slug, not the session name
    assert plan.env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"
    assert plan.title_seq == "\033]0;feature/proj-1\007"
    assert plan.title_argv[-2:] == ["-m", "omc.terminal_title"]
    assert plan.shell_argv[0] == "bash"  # bash carries `cd <cwd>` in its rc file, not argv


def test_session_plan_wires_notifications_for_the_provider_that_takes_argv():
    cfg = Config(notifications=NotificationsConfig(enabled=True))
    cfg.llm.default = "codex"
    plan = session_plan(
        _ctx(), cfg, seed="/omc:start", slug="s", session_name="s", title="t", cwd="."
    )
    assert plan.session_argv[0] == "codex"
    joined = " ".join(plan.session_argv)
    assert "notify=" in joined and "omc" in joined and "internal" in joined
    assert plan.env == {"OMC_SLUG": "s"}  # codex suppresses titles via argv, not env


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
    assert captured["env"]["OMC_SLUG"] == "proj-1"

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
    assert captured["env"]["OMC_SLUG"] == "proj-1"
