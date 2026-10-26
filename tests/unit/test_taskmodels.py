import json
import shlex

import pytest

from omc import taskmodels
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

from . import _stubs
from ._stubs import make_stub, stub_env


def _ctx(tmp_path):
    return ToolContext.from_env(
        {
            "HOME": str(tmp_path),
            "CODEX_HOME": str(tmp_path / "codex"),
            "PATH": str(tmp_path / "bin"),
        }
    )


def _codex_stub(tmp_path, models):
    return _stubs.make_codex_stub(tmp_path / "bin", catalog={"models": models})


def _plant_cache(tmp_path, models):
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


def test_other_client_cache_cannot_choose_launch_model(tmp_path):
    from omc import reconcile
    from omc.config import store
    from omc.config.schema import GlobalConfig
    from omc.session import session_plan

    bindir = tmp_path / "bin"
    make_stub(
        bindir,
        "codex",
        stdout=json.dumps({"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}),
    )
    ctx = ToolContext.from_env(stub_env(bindir))
    cache = tmp_path / ".codex" / "models_cache.json"
    cache.parent.mkdir()
    cache.write_text(
        json.dumps(
            {"client_version": "0.159.2", "models": [_model("gpt-6.1-sol", "GPT-6.1-Sol", 0)]}
        )
    )
    cfg = Config()
    cfg.llm.default = "codex"
    store.save_global(ctx.home, GlobalConfig(llm=cfg.llm))
    config_path = ctx.home / "config.yaml"
    before = config_path.read_bytes()

    plan = session_plan(
        ctx, cfg, seed="/omc:implement", slug="s", session_name="s", title="t", cwd="."
    )
    assert plan.session_argv[plan.session_argv.index("-m") + 1] == "gpt-6-sol"
    assert "gpt-6.1-sol" not in plan.session_argv
    lines = []
    assert reconcile.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False) is cfg
    assert lines == ["✓ codex models current (1 listed)"]
    assert config_path.read_bytes() == before


def test_make_codex_stub_routes_debug_models_and_records_calls(tmp_path):
    bindir = tmp_path / "bin"
    log = tmp_path / "argv"
    catalog = {"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}
    _stubs.make_codex_stub(bindir, catalog=catalog, stdout="launch reply", argv_log=log)
    ctx = ToolContext.from_env(stub_env(bindir))

    listed = ctx.run_bounded(["codex", "debug", "models"], timeout=1)
    launched = ctx.run_bounded(["codex", "exec", "hello"], timeout=1)
    assert listed.returncode == 0 and json.loads(listed.stdout) == catalog
    assert launched.returncode == 0 and launched.stdout.strip() == "launch reply"
    assert log.read_text().splitlines() == ["debug models", "exec hello"]


def test_debug_models_prefers_visible_priority_and_efforts(tmp_path):
    bindir = tmp_path / "bin"
    _stubs.make_codex_stub(
        bindir,
        catalog={
            "models": [
                _model("gpt-hidden-sol", "GPT Sol", 0, visibility="hide"),
                _model("gpt-solar", "GPT Solar", 0),
                _model("gpt-6-sol", "GPT-6-Sol", 1, levels=("high",)),
                _model("gpt-old-sol", "GPT Sol", 9),
            ]
        },
    )
    ctx = ToolContext.from_env(stub_env(bindir))
    assert resolve_choice(ctx, "codex", "sol:high", validate_effort=True) == ModelChoice(
        "gpt-6-sol", "high"
    )


@pytest.mark.parametrize("body", ["", "{broken", "[]", '{"models":[{"slug":1}]}'])
def test_debug_models_invalid_output_never_uses_cache(tmp_path, body):
    bindir = tmp_path / "bin"
    _stubs.make_codex_stub(bindir, catalog=body)
    _plant_cache(tmp_path, [_model("gpt-6.1-sol", "GPT-6.1-Sol", 0)])
    ctx = ToolContext.from_env(stub_env(bindir, CODEX_HOME=str(tmp_path / "codex")))
    with pytest.raises(ConfigError, match="codex debug models"):
        resolve_choice(ctx, "codex", "sol")


def test_debug_models_nonzero_and_timeout_never_use_cache(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    _stubs.make_codex_stub(bindir, catalog="CLI failed", catalog_rc=17)
    _plant_cache(tmp_path, [_model("gpt-6.1-sol", "GPT-6.1-Sol", 0)])
    ctx = ToolContext.from_env(stub_env(bindir, CODEX_HOME=str(tmp_path / "codex")))
    with pytest.raises(ConfigError, match=r"codex debug models.*17.*CLI failed"):
        resolve_choice(ctx, "codex", "sol")

    def timed_out(*args, **kwargs):
        raise TimeoutError("deadline exceeded")

    monkeypatch.setattr(ctx, "run_bounded", timed_out)
    with pytest.raises(ConfigError, match=r"codex debug models.*timed out"):
        taskmodels.catalog(ctx, "codex")


def test_debug_models_undecodable_output_names_command(tmp_path, monkeypatch):
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))

    def undecodable(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(ctx, "run_bounded", undecodable)
    with pytest.raises(ConfigError, match="codex debug models"):
        resolve_choice(ctx, "codex", "sol")


def test_debug_models_missing_binary_has_install_hint(tmp_path):
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    with pytest.raises(ConfigError, match=r"codex debug models.*npm install -g @openai/codex"):
        resolve_choice(ctx, "codex", "sol")


def test_debug_models_uses_bundled_stdout_and_ignores_stderr(tmp_path):
    bindir = tmp_path / "bin"
    script = _stubs.make_codex_stub(
        bindir, catalog={"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}
    )
    script.write_text(
        script.read_text().replace("#!/bin/sh\n", "#!/bin/sh\nprintf 'not logged in\\n' >&2\n")
    )
    ctx = ToolContext.from_env(stub_env(bindir))
    assert resolve_choice(ctx, "codex", "sol") == ModelChoice("gpt-6-sol", "")


def test_debug_models_passes_codex_home(tmp_path):
    bindir = tmp_path / "bin"
    script = _stubs.make_codex_stub(
        bindir, catalog={"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}
    )
    seen = tmp_path / "seen-codex-home"
    script.write_text(
        script.read_text().replace(
            "#!/bin/sh\n", f"#!/bin/sh\nprintf '%s' \"$CODEX_HOME\" > {shlex.quote(str(seen))}\n"
        )
    )
    codex_home = tmp_path / "custom-codex-home"
    ctx = ToolContext.from_env(stub_env(bindir, CODEX_HOME=str(codex_home)))
    assert resolve_choice(ctx, "codex", "sol") == ModelChoice("gpt-6-sol", "")
    assert seen.read_text() == str(codex_home)


def test_debug_models_memo_is_per_context(tmp_path):
    bindir = tmp_path / "bin"
    log = tmp_path / "calls"
    _stubs.make_codex_stub(
        bindir,
        catalog={
            "models": [
                _model("gpt-6-sol", "GPT-6-Sol", 1),
                _model("gpt-6-astra", "GPT-6-Astra", 1),
            ]
        },
        argv_log=log,
    )
    cfg = Config()
    cfg.llm.default = "codex"
    ctx = ToolContext.from_env(stub_env(bindir))
    for task in taskmodels.TASKS:
        assert task_choice(ctx, cfg, task).model_arg in {"gpt-6-sol", "gpt-6-astra"}
    assert log.read_text().splitlines() == ["debug models"]
    task_choice(ToolContext.from_env(stub_env(bindir)), cfg, "orchestrator")
    assert log.read_text().splitlines() == ["debug models", "debug models"]


def test_codex_resolves_newest_visible_exact_family_word(tmp_path):
    _codex_stub(
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


def test_missing_binary_and_unavailable_family_offer_actionable_choices(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ConfigError, match="npm install -g @openai/codex"):
        resolve_choice(ctx, "codex", "sol")
    _codex_stub(tmp_path, [_model("gpt-6-astra", "GPT-6-Astra", 1)])
    with pytest.raises(ConfigError, match="astra"):
        resolve_choice(_ctx(tmp_path), "codex", "sol")


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
def test_malformed_debug_models_names_command(tmp_path, body):
    _stubs.make_codex_stub(tmp_path / "bin", catalog=body)
    with pytest.raises(ConfigError, match="codex debug models"):
        resolve_choice(_ctx(tmp_path), "codex", "sol")


def test_model_effort_checked_only_for_configure_probe(tmp_path):
    _codex_stub(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1, levels=("medium",))])
    ctx = _ctx(tmp_path)
    assert resolve_choice(ctx, "codex", "sol:high") == ModelChoice("gpt-6-sol", "high")
    with pytest.raises(ConfigError, match="medium"):
        resolve_choice(ctx, "codex", "sol:high", validate_effort=True)


def test_task_defaults_and_explicit_values(tmp_path):
    _codex_stub(
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
    _codex_stub(
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


def test_codex_picker_falls_back_to_families_without_binary(tmp_path):
    warnings = []
    options = model_options(_ctx(tmp_path), "codex", "design", warn=warnings.append)
    assert list(options.values()) == ["", "astra", "sol", "__other__"]
    assert len(warnings) == 1 and "codex debug models" in warnings[0]


def test_codex_picker_falls_back_to_families_on_invalid_cli_output(tmp_path):
    _stubs.make_codex_stub(tmp_path / "bin", catalog="{broken")
    warnings = []
    options = model_options(_ctx(tmp_path), "codex", "design", warn=warnings.append)
    assert list(options.values()) == ["", "astra", "sol", "__other__"]
    assert len(warnings) == 1 and "codex debug models" in warnings[0]
    with pytest.raises(ConfigError, match="codex debug models"):
        resolve_choice(_ctx(tmp_path), "codex", "sol")


def test_reasoning_levels_need_only_an_effort(tmp_path):
    model = _model("gpt-6-sol", "GPT-6-Sol", 1)
    model["supported_reasoning_levels"] = [{"effort": "high"}]
    _codex_stub(tmp_path, [model])
    assert resolve_choice(_ctx(tmp_path), "codex", "sol:high", validate_effort=True) == (
        ModelChoice("gpt-6-sol", "high")
    )


def test_codex_family_selection_reads_catalog_without_model_turn(tmp_path):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    bindir = tmp_path / "bin"
    argv_log = tmp_path / "argv"
    _stubs.make_codex_stub(
        bindir,
        catalog={"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]},
        stdout="OK",
        argv_log=argv_log,
    )
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "CODEX_HOME": str(codex_home), "PATH": str(bindir)}
    )
    said = []
    assert validate_selection(
        ctx, "codex", "sol:high", task="medium", say=said.append
    ) == ModelChoice("gpt-6-sol", "high")
    assert argv_log.read_text().splitlines() == ["debug models"]
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


def test_codex_selection_requires_catalog_and_supported_effort(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "omc.docsllm.cli_model_probe", lambda *a, **kw: (True, "default model works")
    )
    with pytest.raises(ConfigError, match="codex debug models"):
        validate_selection(ctx, "codex", "sol", task="simple", say=lambda _: None)
    _codex_stub(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1, levels=("medium",))])
    with pytest.raises(ConfigError, match="supported efforts: medium"):
        validate_selection(_ctx(tmp_path), "codex", "sol:high", task="medium", say=lambda _: None)


def test_codex_selection_reuses_catalog_and_full_id_probes_itself(tmp_path, monkeypatch):
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
    # A family resolves against the context's already-read catalog without a turn.
    log = tmp_path / "codex-argv"
    _stubs.make_codex_stub(
        tmp_path / "bin",
        catalog={"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]},
        argv_log=log,
    )
    model_options(ctx, "codex", "medium")
    _stubs.make_codex_stub(
        tmp_path / "bin",
        catalog={"models": [_model("gpt-6.1-sol", "GPT-6.1-Sol", 0)]},
        argv_log=log,
    )
    assert validate_selection(
        ctx, "codex", "sol:high", task="medium", say=lambda _: None
    ) == ModelChoice("gpt-6-sol", "high")
    assert len(calls) == 1
    assert log.read_text().splitlines() == ["debug models"]


def test_probe_key_drops_claude_task_effort_but_keeps_orchestrator_effort():
    from omc.taskmodels import probe_key

    assert probe_key("claude", "opus:high", "plan") == ("opus:high", "")
    assert probe_key("claude", "opus:high", "orchestrator") == ("opus:high", "high")
    assert probe_key("claude", "", "simple") == ("sonnet", "")
    assert probe_key("codex", "sol:medium", "simple") == ("sol:medium", "medium")


def test_default_clear_resolves_effective_value(tmp_path, monkeypatch):
    _codex_stub(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
    calls = []
    monkeypatch.setattr(
        "omc.docsllm.cli_model_probe", lambda *a, **kw: (calls.append((a, kw)) or True, "OK")
    )
    assert validate_selection(
        _ctx(tmp_path), "codex", "", task="medium", say=lambda _: None
    ) == ModelChoice("gpt-6-sol", "high")
    assert calls == []


def test_session_and_headless_launch_use_orchestrator_and_provider_env(tmp_path):
    from types import SimpleNamespace

    from omc.session import run_headless, session_plan

    _codex_stub(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
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
        runtime_cache = {}

        def run(self, argv, cwd=None, extra_env=None):
            self.argv = argv
            self.extra_env = extra_env
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        def run_bounded(self, argv, *, timeout):
            assert argv == ["codex", "debug", "models"]
            return SimpleNamespace(
                stdout=json.dumps({"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}),
                stderr="",
                returncode=0,
            )

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

    _codex_stub(tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1)])
    cfg = Config()
    cfg.llm.default = "codex"

    class CaptureCtx:
        env = _ctx(tmp_path).env
        runtime_cache = {}

        def run_bounded(self, argv, *, timeout):
            assert argv == ["codex", "debug", "models"]
            return SimpleNamespace(
                stdout=json.dumps({"models": [_model("gpt-6-sol", "GPT-6-Sol", 1)]}),
                stderr="",
                returncode=0,
            )

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
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
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
    assert payload["ok"] is False and "codex debug models" in payload["message"]
    _codex_stub(
        tmp_path, [_model("gpt-6-sol", "GPT-6-Sol", 1), _model("gpt-6-astra", "GPT-6-Astra", 1)]
    )
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


def test_codex_catalog_orders_visible_exact_families_and_keeps_supported_efforts(tmp_path):
    _codex_stub(
        tmp_path,
        [
            _model("gpt-5-sol", "GPT Sol", 9, levels=("medium",)),
            _model("gpt-6-sol", "GPT-6-SoL", 2, levels=("high", "xhigh")),
            _model("gpt-hidden-sol", "GPT Sol", 0, visibility="hide"),
            _model("gpt-solar", "GPT Solar", 0),
            _model("gpt-6-astra", "GPT Astra", 1, levels=("medium", "high")),
            _model("gpt-6-nova", "GPT Nova", 3, levels=("high",)),
            _model("gpt-luna", "GPT Luna", 0),
        ],
    )
    listed = taskmodels.catalog(_ctx(tmp_path), "codex")
    assert listed == [
        taskmodels.CatalogModel("gpt-solar", "", ("medium", "high")),
        taskmodels.CatalogModel("gpt-6-astra", "astra", ("medium", "high")),
        taskmodels.CatalogModel("gpt-6-sol", "sol", ("high", "xhigh")),
        taskmodels.CatalogModel("gpt-6-nova", "", ("high",)),
        taskmodels.CatalogModel("gpt-5-sol", "sol", ("medium",)),
    ]
    assert taskmodels.compatible("gpt-6-nova", "high", listed)
    assert not taskmodels.compatible("nova", "", listed)
    assert taskmodels.compatible("sol", "high", listed)
    assert not taskmodels.compatible("sol", "medium", listed)
    assert taskmodels.compatible("gpt-5-sol", "medium", listed)
    assert not taskmodels.compatible("gpt-5.1-sol", "", listed)
    assert not taskmodels.compatible("gpt-6-sol", "medium", listed)


def test_codex_catalog_refresh_replaces_missing_and_malformed_results(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ConfigError, match="codex debug models"):
        taskmodels.catalog(ctx, "codex")
    _stubs.make_codex_stub(tmp_path / "bin", catalog='{"models": ["not a model"]}')
    with pytest.raises(ConfigError, match="codex debug models") as exc:
        taskmodels.catalog(ctx, "codex")
    assert not isinstance(exc.value, taskmodels.CatalogMissing)
    _codex_stub(tmp_path, [_model("gpt-6-sol", "GPT Sol", 1)])
    assert taskmodels.catalog(ctx, "codex") == [
        taskmodels.CatalogModel("gpt-6-sol", "sol", ("medium", "high"))
    ]


def _claude_catalog(models, fetched_at):
    # Claude Code 2.1.301 writes both timestamps as epoch milliseconds.
    return {
        "version": 2,
        "fetchedAt": fetched_at,
        "staleAt": fetched_at + 3_304_030,
        "catalog": {
            "surface": "cc",
            "config": {"id": "models", "models": models, "settings_vocabulary": {}},
            "state": {},
        },
    }


def _claude_model(model_id, short_name, section, efforts=("medium", "high")):
    return {
        "id": model_id,
        "name": model_id,
        "short_name": short_name,
        "section": section,
        "thinking": {"effort_options": [{"id": effort} for effort in efforts]},
    }


def _claude_catalog_ctx(tmp_path, *, org_id="org-current"):
    config_dir = tmp_path / "claude-config"
    catalog_dir = config_dir / "cache" / "model-catalog"
    catalog_dir.mkdir(parents=True)
    bindir = tmp_path / "bin"
    calls = tmp_path / "auth-argv"
    make_stub(
        bindir,
        "claude",
        stdout=json.dumps({"loggedIn": True, "orgId": org_id, "configDirectory": str(config_dir)}),
        argv_log=calls,
    )
    return ToolContext.from_env(stub_env(bindir)), catalog_dir, calls


def test_claude_catalog_uses_current_org_newest_file_and_both_sections(tmp_path, monkeypatch):
    ctx, directory, calls = _claude_catalog_ctx(tmp_path)
    old = _claude_catalog([_claude_model("claude-opus-old", "Opus", "main")], 1790812800000)
    current = _claude_catalog(
        [
            _claude_model("claude-opus-5-5", "Opus", "main", ("high", "max")),
            _claude_model("claude-sonnet-5-5", "SONNET", "overflow", ("medium",)),
            _claude_model("claude-haiku-5", "Haiku", "main", ("low",)),
            _claude_model("claude-opus-5", "Opus", "overflow", ("high",)),
            _claude_model("claude-nova-5-5", "Nova", "overflow", ("high",)),
            # A model without effort levels has no effort_options at all.
            _claude_model("claude-sonnet-4-5", "Sonnet", "overflow")
            | {"thinking": {"type": "none"}},
        ],
        1792022400000,
    )
    (directory / "org-current-old-cc.json").write_text(json.dumps(old))
    (directory / "org-current-new-cc.json").write_text(json.dumps(current))
    other = _claude_catalog([_claude_model("claude-opus-other", "Opus", "main")], 1793491200000)
    (directory / "org-other-new-cc.json").write_text(json.dumps(other))
    visited = []
    real_list_dir = ctx.list_dir
    monkeypatch.setattr(ctx, "list_dir", lambda path: visited.append(path) or real_list_dir(path))

    listed = taskmodels.catalog(ctx, "claude")
    assert visited == [directory]
    assert calls.read_text().strip() == "auth status"
    assert listed == [
        taskmodels.CatalogModel("claude-opus-5-5", "opus", ("high", "max")),
        taskmodels.CatalogModel("claude-sonnet-5-5", "sonnet", ("medium",)),
        taskmodels.CatalogModel("claude-opus-5", "opus", ("high",)),
        taskmodels.CatalogModel("claude-nova-5-5", "", ("high",)),
        taskmodels.CatalogModel("claude-sonnet-4-5", "sonnet", ()),
    ]
    assert taskmodels.compatible("claude-nova-5-5", "high", listed)
    assert not taskmodels.compatible("nova", "", listed)
    assert taskmodels.compatible("claude-opus-5-5[1m]", "high", listed)
    assert not taskmodels.compatible("claude-opus-5-5[2m]", "high", listed)


def test_compatible_ignores_effort_only_when_catalog_efforts_are_unknown():
    unknown = [taskmodels.CatalogModel("claude-opus-5-5", "opus", None)]
    none_listed = [taskmodels.CatalogModel("claude-opus-5-5", "opus", ())]

    assert taskmodels.compatible("opus", "max", unknown)
    assert taskmodels.compatible("claude-opus-5-5", "max", unknown)
    assert taskmodels.compatible("claude-opus-5-5[1m]", "high", unknown)
    assert not taskmodels.compatible("sonnet", "max", unknown)
    assert not taskmodels.compatible("claude-opus-4-1", "max", unknown)
    # A source that lists effort data and offers none still rejects an effort.
    assert not taskmodels.compatible("opus", "max", none_listed)
    assert not taskmodels.compatible("claude-opus-5-5", "max", none_listed)
    assert taskmodels.compatible("claude-opus-5-5", "", none_listed)


def test_claude_catalog_missing_has_open_once_hint(tmp_path):
    ctx, directory, _ = _claude_catalog_ctx(tmp_path)
    with pytest.raises(taskmodels.CatalogMissing, match="open claude once") as exc:
        taskmodels.catalog(ctx, "claude")
    assert str(directory) in str(exc.value)


@pytest.mark.parametrize("fetched_at", ["2026-10-15T00:00:00Z", "1792022400000", True, None])
def test_claude_catalog_rejects_non_epoch_fetched_at(tmp_path, fetched_at):
    ctx, directory, _ = _claude_catalog_ctx(tmp_path)
    data = _claude_catalog([_claude_model("claude-opus-5-5", "Opus", "main")], 0)
    data["fetchedAt"] = fetched_at
    (directory / "org-current-hash-cc.json").write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="org-current-hash-cc.json: expected fetchedAt"):
        taskmodels.catalog(ctx, "claude")


@pytest.mark.parametrize(
    "models",
    [
        "not a list",
        [{"id": "claude-opus-5-5", "short_name": "Opus"}],
        [
            _claude_model("claude-opus-5-5", "Opus", "main", ("high",))
            | {"thinking": {"effort_options": ["high"]}}
        ],
    ],
)
def test_claude_catalog_malformed_shape_names_its_file(tmp_path, models):
    ctx, directory, _ = _claude_catalog_ctx(tmp_path)
    path = directory / "org-current-hash-cc.json"
    path.write_text(json.dumps(_claude_catalog(models, 1792022400000)))
    with pytest.raises(ConfigError, match="org-current-hash-cc.json") as exc:
        taskmodels.catalog(ctx, "claude")
    assert not isinstance(exc.value, taskmodels.CatalogMissing)


def test_claude_api_key_auth_uses_live_models_when_org_and_cache_are_absent(tmp_path):
    """API-key auth omits orgId, and Claude Code 2.1.286 writes no CLI catalog."""
    bindir = tmp_path / "bin"
    make_stub(
        bindir,
        "claude",
        stdout=json.dumps(
            {
                "loggedIn": True,
                "authMethod": "api_key",
                "apiKeySource": "ANTHROPIC_API_KEY",
                "configDirectory": str(tmp_path / "claude-config"),
            }
        ),
    )
    ctx = ToolContext.from_env(stub_env(bindir, ANTHROPIC_API_KEY="private-key"))
    seen = []

    def fake_http_get(url, *, headers=None, timeout=30.0):
        seen.append((url, dict(headers or {})))
        return 200, json.dumps(
            {
                "data": [
                    {"id": "claude-opus-5-5", "created_at": "2026-05-01T00:00:00Z"},
                    {"id": "claude-haiku-5", "created_at": "2026-06-01T00:00:00Z"},
                ]
            }
        )

    ctx.http_get = fake_http_get
    listed = taskmodels.catalog(ctx, "claude")

    assert [model.id for model in listed] == ["claude-opus-5-5"]
    # The models endpoint has no effort data: unknown, not "none supported".
    assert listed[0].efforts is None
    assert taskmodels.compatible("claude-opus-5-5", "high", listed)
    assert taskmodels.compatible("opus", "max", listed)
    assert not taskmodels.compatible("sonnet", "high", listed)
    assert not taskmodels.compatible("claude-opus-4-1", "high", listed)
    assert seen == [
        (
            "https://api.anthropic.com/v1/models?limit=1000",
            {"x-api-key": "private-key", "anthropic-version": "2023-06-01"},
        )
    ]


def test_api_catalog_reuses_http_probe_filters_haiku_and_orders_newest(tmp_path):
    ctx = _ctx(tmp_path)
    seen = []

    def fake_http_get(url, *, headers=None, timeout=30.0):
        seen.append((url, dict(headers or {})))
        return 200, json.dumps(
            {
                "data": [
                    {"id": "claude-sonnet-5", "created_at": "2026-01-01T00:00:00Z"},
                    {"id": "claude-opus-5-5", "created_at": "2026-05-01T00:00:00Z"},
                    {"id": "claude-sonnet-5-5", "created_at": "2026-06-01T00:00:00Z"},
                    {"id": "claude-haiku-5", "created_at": "2026-07-01T00:00:00Z"},
                    {"id": "foreign-model", "created_at": "2026-08-01T00:00:00Z"},
                    {"id": "claude-nova-5-5", "created_at": "2026-09-01T00:00:00Z"},
                ]
            }
        )

    ctx.http_get = fake_http_get
    listed = taskmodels.catalog(ctx, "claude", docs_api_key="private-key")
    assert listed == [
        taskmodels.CatalogModel("claude-nova-5-5", "", None),
        taskmodels.CatalogModel("foreign-model", "", None),
        taskmodels.CatalogModel("claude-sonnet-5-5", "sonnet", None),
        taskmodels.CatalogModel("claude-opus-5-5", "opus", None),
        taskmodels.CatalogModel("claude-sonnet-5", "sonnet", None),
    ]
    assert taskmodels.compatible("claude-nova-5-5", "", listed)
    assert not taskmodels.compatible("nova", "", listed)
    assert seen == [
        (
            "https://api.anthropic.com/v1/models?limit=1000",
            {"x-api-key": "private-key", "anthropic-version": "2023-06-01"},
        )
    ]


def test_api_catalog_failure_keeps_key_redacted(tmp_path):
    ctx = _ctx(tmp_path)
    key = "sk-ant-api03-SECRETKEYabcdefghijklmnop1234"
    ctx.http_get = lambda url, *, headers=None, timeout=30.0: (0, f"transport echoed {key}")
    with pytest.raises(ConfigError, match="could not reach api.anthropic.com") as exc:
        taskmodels.catalog(ctx, "claude", docs_api_key=key)
    assert key not in str(exc.value)
    assert "******" in str(exc.value)
