import json
import shlex
import stat

import pytest

from omc.config.schema import Config, ProviderConfig
from omc.errors import ConfigError
from omc.taskmodels import (
    ModelChoice,
    model_options,
    orchestrator,
    resolve_choice,
    task_choice,
    validate_selection,
)
from omc.toolctx import ToolContext


def _ctx(tmp_path):
    return ToolContext.from_env({"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "codex")})


def _cache(tmp_path, models):
    path = tmp_path / "codex" / "models_cache.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"models": models}))
    return path


def _model(slug, display, priority, *, visibility="list", levels=("medium", "high")):
    return {
        "slug": slug,
        "display_name": display,
        "visibility": visibility,
        "priority": priority,
        "supported_reasoning_levels": [
            {"effort": level, "description": level.title()} for level in levels
        ],
    }


def test_codex_resolves_newest_visible_exact_family_word(tmp_path):
    _cache(
        tmp_path,
        [
            _model("gpt-old-sol", "GPT Sol", 5),
            _model("gpt-hidden-sol", "GPT Sol", 0, visibility="hide"),
            _model("gpt-astra", "GPT Astra", 1),
            _model("gpt-solar", "GPT Solar", 0),
            _model("gpt-new-sol", "GPT-6-SoL", 2),
        ],
    )
    assert resolve_choice(_ctx(tmp_path), "codex", "sol:high") == ModelChoice("gpt-new-sol", "high")
    assert resolve_choice(_ctx(tmp_path), "codex", "astra") == ModelChoice("gpt-astra", "")


def test_full_ids_and_claude_aliases_need_no_cache(tmp_path):
    ctx = _ctx(tmp_path)
    assert resolve_choice(ctx, "codex", "gpt-6-sol:high") == ModelChoice("gpt-6-sol", "high")
    assert resolve_choice(ctx, "claude", "fable") == ModelChoice("fable", "")
    assert resolve_choice(ctx, "claude", "claude-fable-5-1:xhigh") == ModelChoice(
        "claude-fable-5-1", "xhigh"
    )


def test_missing_cache_and_unavailable_family_offer_actionable_choices(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ConfigError, match="run omc configure"):
        resolve_choice(ctx, "codex", "sol")
    _cache(tmp_path, [_model("gpt-6-astra", "GPT-6-Astra", 1)])
    with pytest.raises(ConfigError, match="astra"):
        resolve_choice(ctx, "codex", "sol")


@pytest.mark.parametrize(
    "body",
    [
        "{broken",
        "[]",
        '{"models":{}}',
        (
            '{"models":[{"slug":1,"display_name":"GPT Sol","visibility":"list",'
            '"priority":1,"supported_reasoning_levels":[]}]}'
        ),
        (
            '{"models":[{"slug":"s","display_name":"GPT Sol","visibility":"list",'
            '"priority":"1","supported_reasoning_levels":[]}]}'
        ),
        (
            '{"models":[{"slug":"s","display_name":"GPT Sol","visibility":"list",'
            '"priority":1,"supported_reasoning_levels":["high"]}]}'
        ),
    ],
)
def test_malformed_cache_names_file(tmp_path, body):
    path = tmp_path / "codex" / "models_cache.json"
    path.parent.mkdir()
    path.write_text(body)
    with pytest.raises(ConfigError, match="models_cache.json"):
        resolve_choice(_ctx(tmp_path), "codex", "sol")


def test_model_effort_checked_only_for_configure_probe(tmp_path):
    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1, levels=("medium",))])
    ctx = _ctx(tmp_path)
    assert resolve_choice(ctx, "codex", "sol:high") == ModelChoice("gpt-6-sol", "high")
    with pytest.raises(ConfigError, match="medium"):
        resolve_choice(ctx, "codex", "sol:high", validate_effort=True)


def test_task_defaults_and_explicit_values(tmp_path):
    _cache(
        tmp_path,
        [
            _model("gpt-6-sol", "GPT-6-Sol", 1),
            _model("gpt-6-astra", "GPT-6-Astra", 1),
        ],
    )
    cfg = Config()
    cfg.llm.default = "codex"
    assert orchestrator(_ctx(tmp_path), cfg) == ModelChoice("gpt-6-sol", "high")
    assert task_choice(_ctx(tmp_path), cfg, "design") == ModelChoice("gpt-6-astra", "")
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-fixed:medium")
    cfg.llm.providers["codex"].tasks.design = "sol:high"
    assert orchestrator(_ctx(tmp_path), cfg) == ModelChoice("gpt-fixed", "medium")
    assert task_choice(_ctx(tmp_path), cfg, "design") == ModelChoice("gpt-6-sol", "high")
    assert task_choice(_ctx(tmp_path), cfg, "review", provider="claude") == ModelChoice("fable", "")


def test_model_options_default_first_other_last_and_live_efforts(tmp_path):
    _cache(
        tmp_path,
        [
            _model("gpt-6-sol", "GPT-6-Sol", 1, levels=("medium", "high")),
            _model("gpt-6-astra", "GPT-6-Astra", 1, levels=("high",)),
        ],
    )
    codex = model_options(_ctx(tmp_path), "codex", "simple")
    assert list(codex.items())[0] == ("Provider default (sol:medium)", "")
    assert codex["Sol (high)"] == "sol:high"
    assert codex["Astra (high)"] == "astra:high"
    assert list(codex)[-1] == "Other (type a model id)"
    assert list(model_options(_ctx(tmp_path), "codex", "plan"))[1] == "Astra"
    claude = model_options(_ctx(tmp_path), "claude", "simple")
    assert list(claude.items())[0] == ("Provider default (sonnet)", "")
    assert "Fable" in claude and "Fable (high)" not in claude
    assert "Opus (high)" in model_options(_ctx(tmp_path), "claude", "orchestrator")


def test_codex_picker_falls_back_to_families_without_cache(tmp_path):
    options = model_options(_ctx(tmp_path), "codex", "design")
    assert list(options.values()) == ["", "astra", "sol", "__other__"]


def test_codex_picker_falls_back_to_families_on_unreadable_cache(tmp_path):
    path = tmp_path / "codex" / "models_cache.json"
    path.parent.mkdir()
    path.write_text("{broken")
    warnings = []
    options = model_options(_ctx(tmp_path), "codex", "design", warn=warnings.append)
    assert list(options.values()) == ["", "astra", "sol", "__other__"]
    assert len(warnings) == 1 and "models_cache.json" in warnings[0]
    with pytest.raises(ConfigError, match="models_cache.json"):
        resolve_choice(_ctx(tmp_path), "codex", "sol")


def test_reasoning_levels_need_only_an_effort(tmp_path):
    model = _model("gpt-6-sol", "GPT-6-Sol", 1)
    model["supported_reasoning_levels"] = [{"effort": "high"}]
    _cache(tmp_path, [model])
    assert resolve_choice(_ctx(tmp_path), "codex", "sol:high", validate_effort=True) == (
        ModelChoice("gpt-6-sol", "high")
    )


def test_codex_selection_probes_default_model_and_reads_refreshed_cache(tmp_path):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_log = tmp_path / "argv"
    fixture = json.dumps({"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]})
    stub = bindir / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > {shlex.quote(str(argv_log))}\n"
        f"/bin/cat > {shlex.quote(str(codex_home / 'models_cache.json'))} <<'CACHE'\n"
        f"{fixture}\nCACHE\n"
        "printf 'OK\\n'\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "CODEX_HOME": str(codex_home), "PATH": str(bindir)}
    )
    said = []
    assert validate_selection(
        ctx, "codex", "sol:high", task="medium", say=said.append
    ) == ModelChoice("gpt-6-sol", "high")
    assert argv_log.read_text().strip() == "exec --skip-git-repo-check Reply with exactly OK."
    assert said[-1] == "✓ sol:high → gpt-6-sol (high)"


def test_claude_selection_asks_for_exact_id_once_without_storing_it(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    calls = []

    def probe(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "claude-fable-5-1"

    monkeypatch.setattr("omc.docsllm.cli_model_probe", probe)
    said = []
    assert validate_selection(
        ctx, "claude", "fable", task="design", say=said.append
    ) == ModelChoice("fable", "")
    assert len(calls) == 1
    assert calls[0][1]["prompt"] == "Reply with only your exact model id"
    assert said[-1] == "✓ fable → claude-fable-5-1"


def test_codex_selection_requires_refreshed_cache_and_supported_effort(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "omc.docsllm.cli_model_probe", lambda *a, **kw: (True, "default model works")
    )
    with pytest.raises(ConfigError, match="open codex once to fetch its model list"):
        validate_selection(ctx, "codex", "sol", task="simple", say=lambda _: None)
    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1, levels=("medium",))])
    with pytest.raises(ConfigError, match="supported efforts: medium"):
        validate_selection(ctx, "codex", "sol:high", task="medium", say=lambda _: None)


def test_codex_selection_without_refresh_reuses_cache_and_full_id_probes_itself(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        "omc.docsllm.cli_model_probe", lambda *a, **kw: (calls.append((a[2:], kw)) or True, "OK")
    )
    ctx = _ctx(tmp_path)
    # A full id never touches the list: probed directly, with its effort.
    assert validate_selection(
        ctx, "codex", "gpt-6-sol:high", task="medium", say=lambda _: None
    ) == ModelChoice("gpt-6-sol", "high")
    assert calls == [(("gpt-6-sol",), {"effort": "high"})]
    # refresh=False resolves against the list already on disk, no turn spent.
    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
    assert validate_selection(
        ctx, "codex", "sol:high", task="medium", say=lambda _: None, refresh=False
    ) == ModelChoice("gpt-6-sol", "high")
    assert len(calls) == 1


def test_probe_key_drops_claude_task_effort_but_keeps_orchestrator_effort():
    from omc.taskmodels import probe_key

    assert probe_key("claude", "opus:high", "plan") == ("opus:high", "")
    assert probe_key("claude", "opus:high", "orchestrator") == ("opus:high", "high")
    assert probe_key("claude", "", "simple") == ("sonnet", "")
    assert probe_key("codex", "sol:medium", "simple") == ("sol:medium", "medium")


def test_default_clear_resolves_effective_value(tmp_path, monkeypatch):
    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
    calls = []
    monkeypatch.setattr(
        "omc.docsllm.cli_model_probe", lambda *a, **kw: (calls.append((a, kw)) or True, "OK")
    )
    assert validate_selection(
        _ctx(tmp_path), "codex", "", task="medium", say=lambda _: None
    ) == ModelChoice("gpt-6-sol", "high")
    assert len(calls) == 1


def test_session_and_headless_launch_use_orchestrator_and_provider_env(tmp_path):
    from types import SimpleNamespace

    from omc.session import run_headless, session_plan

    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
    cfg = Config()
    cfg.llm.default = "codex"
    plan = session_plan(
        _ctx(tmp_path), cfg, seed="/omc:start", slug="s", session_name="s", title="t", cwd="."
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
        "/omc:start",
    ]
    assert plan.env == {"OMC_SLUG": "s", "OMC_PROVIDER": "codex", "GITNEXUS_SHARED_STORE": "off"}

    class CaptureCtx:
        env = _ctx(tmp_path).env

        def run(self, argv, cwd=None, extra_env=None):
            self.argv = argv
            self.extra_env = extra_env
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        def read_text(self, path):
            return path.read_text()

    ctx = CaptureCtx()
    assert run_headless(ctx, cfg, "/omc:implement", ".", "s") == 0
    assert ctx.argv[:8] == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-m",
        "gpt-6-sol",
        "-c",
        "model_reasoning_effort=high",
        "/omc:implement",
    ]
    assert ctx.extra_env == {"OMC_SLUG": "s", "OMC_PROVIDER": "codex"}


def test_claude_session_uses_effort(tmp_path):
    from omc.session import session_plan

    cfg = Config()
    cfg.llm.providers["claude"].model = "opus:xhigh"
    plan = session_plan(
        _ctx(tmp_path), cfg, seed="/omc:review", slug="s", session_name="review", title="t", cwd="."
    )
    assert plan.session_argv == [
        "claude",
        "-n",
        "review",
        "--model",
        "opus",
        "--effort",
        "xhigh",
        "/omc:review",
    ]
    assert plan.env["OMC_PROVIDER"] == "claude"


def test_claude_headless_and_slug_use_orchestrator_model_and_effort(tmp_path):
    from types import SimpleNamespace

    from omc.session import START_ALLOWED_TOOLS, run_headless
    from omc.slug import MCP_TOOL_PATTERNS, fetch_slug

    cfg = Config()
    cfg.llm.providers["claude"].model = "opus:xhigh"

    class CaptureCtx:
        env = _ctx(tmp_path).env

        def run(self, argv, cwd=None, extra_env=None):
            self.calls.append((argv, extra_env))
            return SimpleNamespace(
                stdout='OMC_SLUG {"ok":true,"slug":"issue-1"}', stderr="", returncode=0
            )

    ctx = CaptureCtx()
    ctx.calls = []
    assert run_headless(ctx, cfg, "/omc:implement", ".", "s") == 0
    assert ctx.calls[0] == (
        [
            "claude",
            "-p",
            "/omc:implement",
            "--output-format",
            "text",
            "-n",
            "s",
            "--model",
            "opus",
            "--effort",
            "xhigh",
            "--allowed-tools",
            *START_ALLOWED_TOOLS,
        ],
        {
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
            "OMC_SLUG": "s",
            "OMC_PROVIDER": "claude",
        },
    )
    assert fetch_slug(ctx, cfg, "ISSUE-1") == "issue-1"
    slug_argv, slug_env = ctx.calls[1]
    assert slug_argv[:2] == ["claude", "-p"]
    assert 'OMC_SLUG_CONTEXT_JSON: "ISSUE-1"' in slug_argv[2]
    assert slug_argv[3:] == [
        "--output-format",
        "text",
        "--model",
        "opus",
        "--effort",
        "xhigh",
        "--allowed-tools",
        *MCP_TOOL_PATTERNS,
    ]
    assert slug_env == {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}


def test_slug_uses_codex_orchestrator_model_and_effort(tmp_path):
    from types import SimpleNamespace

    from omc.slug import fetch_slug

    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
    cfg = Config()
    cfg.llm.default = "codex"

    class CaptureCtx:
        env = _ctx(tmp_path).env

        def read_text(self, path):
            return path.read_text()

        def run(self, argv, extra_env=None):
            self.argv = argv
            return SimpleNamespace(
                stdout='OMC_SLUG {"ok":true,"slug":"issue-1"}', stderr="", returncode=0
            )

    ctx = CaptureCtx()
    assert fetch_slug(ctx, cfg, "ISSUE-1") == "issue-1"
    assert ctx.argv[:7] == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-m",
        "gpt-6-sol",
        "-c",
        "model_reasoning_effort=high",
    ]


def test_internal_models_verdict_and_refusals(tmp_path, capsys, monkeypatch):
    from omc.config.schema import GlobalConfig
    from omc.config.store import save_global
    from omc.internal import run_internal

    monkeypatch.setenv("OMC_HOME", str(tmp_path / "omc"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("OMC_PROVIDER", raising=False)
    assert run_internal(["models"]) == 2
    line = capsys.readouterr().out.strip()
    assert line.startswith("OMC_MODELS ")
    assert json.loads(line.split(" ", 1)[1])["ok"] is False

    save_global(tmp_path / "omc", GlobalConfig())
    assert run_internal(["models"]) == 0
    line = capsys.readouterr().out.strip()
    payload = json.loads(line.split(" ", 1)[1])
    assert payload["provider"] == "claude"
    assert payload["tasks"] == {
        "orchestrator": {"model": "opus", "effort": ""},
        "design": {"model": "fable", "effort": ""},
        "plan": {"model": "fable", "effort": ""},
        "review": {"model": "fable", "effort": ""},
        "simple": {"model": "sonnet", "effort": ""},
        "medium": {"model": "opus", "effort": ""},
        "high": {"model": "fable", "effort": ""},
    }
    monkeypatch.setenv("OMC_PROVIDER", "codex")
    assert run_internal(["models"]) == 2
    payload = json.loads(capsys.readouterr().out.strip().split(" ", 1)[1])
    assert payload["ok"] is False and "run omc configure" in payload["message"]
    _cache(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1), _model("gpt-6-astra", "GPT-6-Astra", 1)])
    assert run_internal(["models"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().split(" ", 1)[1])
    assert payload["provider"] == "codex"
    assert payload["tasks"]["orchestrator"] == {"model": "gpt-6-sol", "effort": "high"}
    assert set(payload["tasks"]) == {
        "orchestrator",
        "design",
        "plan",
        "review",
        "simple",
        "medium",
        "high",
    }
    monkeypatch.setenv("OMC_PROVIDER", "bogus")
    assert run_internal(["models"]) == 2
    payload = json.loads(capsys.readouterr().out.strip().split(" ", 1)[1])
    assert payload["ok"] is False and "bogus" in payload["message"]


@pytest.mark.parametrize("bad_file", ("project", "secrets"))
def test_internal_models_uses_only_global_config(tmp_path, capsys, monkeypatch, bad_file):
    from omc.config.schema import GlobalConfig
    from omc.config.store import save_global
    from omc.internal import _models

    home = tmp_path / "omc"
    save_global(home, GlobalConfig())
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".omc").mkdir()
    (repo / ".omc" / "config.yaml").write_text(
        "worktree: []\n" if bad_file == "project" else "worktree: {}\n"
    )
    (home / "secrets.yaml").write_text("{broken\n" if bad_file == "secrets" else "{}\n")
    monkeypatch.chdir(repo)

    ctx = _ctx(tmp_path)
    ctx.home = home

    def no_process(*args, **kwargs):
        pytest.fail("Claude model lookup must not run a process")

    monkeypatch.setattr(ctx, "run", no_process)
    assert _models(ctx) == 0
    payload = json.loads(capsys.readouterr().out.strip().split(" ", 1)[1])
    assert payload["ok"] is True
    assert payload["provider"] == "claude"
    assert payload["tasks"]["review"] == {"model": "fable", "effort": ""}
