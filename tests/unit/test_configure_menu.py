"""The configure menu's saved-edit contract, exercised through real tags."""

from pathlib import Path

import pytest

from omc import configure, modelprobe
from omc.config import store
from omc.config.schema import GlobalConfig, ProjectConfig
from omc.errors import Refusal
from omc.toolctx import ToolContext

from ._stubs import DEFAULT_CODEX_CATALOG, make_codex_stub


@pytest.fixture(autouse=True)
def _stub_task_model_probe(monkeypatch):
    # Menu tests exercise persistence and tag replay. The subprocess/catalog
    # contract has its own restricted-PATH test in test_taskmodels.py.
    monkeypatch.setattr(configure.taskmodels, "validate_selection", lambda *a, **kw: None)


def _menu(tmp_path: Path, *, root: Path | None = None):
    bindir = tmp_path / "bin"
    make_codex_stub(bindir, catalog=DEFAULT_CODEX_CATALOG)
    ctx = ToolContext(home=tmp_path / "home", env={"HOME": str(tmp_path), "PATH": str(bindir)})
    gcfg, pcfg = GlobalConfig(), ProjectConfig()
    session = configure._compose_menu(ctx, root, gcfg, pcfg)
    return session, (ctx, gcfg, pcfg)


def _tags(section):
    for value in section.values():
        if isinstance(value, dict):
            yield from _tags(value)
        else:
            yield value


def test_provider_selection_per_step_toggle_saves_only_the_global_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (ctx, gcfg, _) = _menu(tmp_path, root=repo)
    tag = session.root["LLM"]["Provider Selection Per Step"]
    assert tag.val is False
    assert tag.description == (
        "Stop after each stage to offer another provider; "
        "off = continue automatically through to the push."
    )
    assert tag.update(True)
    assert gcfg.llm.provider_selection_per_step is True  # in-memory copy-back
    assert store.load_global(ctx.home).llm.provider_selection_per_step is True
    assert not store.project_config_path(repo).exists()
    assert session.flags == (True, False)
    assert tag.update(False)
    assert gcfg.llm.provider_selection_per_step is False
    assert store.load_global(ctx.home).llm.provider_selection_per_step is False


def test_menu_groups_schema_fields_and_registry_providers(tmp_path):
    session, _ = _menu(tmp_path)
    assert list(session.root) == ["LLM"]
    llm = session.root["LLM"]
    assert llm["Default provider"].description == "Provider for new sessions."
    assert set(llm) == {"Default provider", "Provider Selection Per Step", "claude", "codex"}
    assert set(llm["claude"]) == {
        "Configured",
        "Orchestrator model",
        "Native notifications",
        "Task models",
    }
    assert set(llm["Default provider"]._build_options().values()) == {"claude"}
    assert "Other (type a model id)" in llm["claude"]["Orchestrator model"]._build_options()
    assert set(llm["claude"]["Task models"]) == {
        "Design",
        "Plan",
        "Review",
        "Simple Complexity Task",
        "Medium Complexity Task",
        "High Complexity Task",
    }
    assert llm["claude"]["Native notifications"].description == (
        "Use this provider's native session alerts."
    )
    assert "Worktree (project)" not in session.root
    repo = tmp_path / "repo"
    repo.mkdir()
    in_repo, _ = _menu(tmp_path, root=repo)
    assert set(in_repo.root["Worktree (project)"]) == {"Branch prefix", "Base branch"}


def test_task_submenu_uses_nested_schema_labels_and_saves_a_leaf(tmp_path):
    session, (ctx, gcfg, _) = _menu(tmp_path)
    tag = session.root["LLM"]["codex"]["Task models"]["Medium Complexity Task"]
    assert tag.description
    assert tag.update("sol:high")
    assert gcfg.llm.providers["codex"].tasks.medium == "sol:high"
    assert store.load_global(ctx.home).llm.providers["codex"].tasks.medium == "sol:high"
    assert tag.update("")
    assert store.load_global(ctx.home).llm.providers["codex"].tasks.medium == ""


def test_task_picker_options_and_default_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(configure.taskmodels, "validate_selection", lambda *a, **kw: None)
    session, (ctx, gcfg, _) = _menu(tmp_path)
    codex = session.root["LLM"]["codex"]
    claude = session.root["LLM"]["claude"]
    assert list(codex["Orchestrator model"]._build_options())[0] == "Provider default (sol:high)"
    assert (
        list(codex["Task models"]["Medium Complexity Task"]._build_options())[0]
        == "Provider default (sol:high)"
    )
    assert list(claude["Task models"]["Plan"]._build_options())[-1] == "Other (type a model id)"
    assert "Opus (high)" not in claude["Task models"]["Plan"]._build_options()
    assert "Opus (high)" in claude["Orchestrator model"]._build_options()
    tag = codex["Task models"]["Medium Complexity Task"]
    assert tag.update("sol:high")
    assert tag.update("")
    assert gcfg.llm.providers["codex"].tasks.medium == ""
    assert store.load_global(ctx.home).llm.providers["codex"].tasks.medium == ""


def test_failed_codex_catalog_degrades_seven_pickers_and_warns_once(tmp_path, capsys):
    bindir = tmp_path / "bin"
    log = tmp_path / "codex-argv"
    make_codex_stub(bindir, catalog="{broken", argv_log=log)
    ctx = ToolContext(home=tmp_path / "home", env={"HOME": str(tmp_path), "PATH": str(bindir)})
    session = configure._compose_menu(ctx, None, GlobalConfig(), ProjectConfig())
    codex = session.root["LLM"]["codex"]
    assert list(codex["Orchestrator model"]._build_options()) == [
        "Provider default (sol:high)",
        "Astra",
        "Sol",
        "Other (type a model id)",
    ]
    for label in (
        "Design",
        "Plan",
        "Review",
        "Simple Complexity Task",
        "Medium Complexity Task",
        "High Complexity Task",
    ):
        assert list(codex["Task models"][label]._build_options())[1:3] == ["Astra", "Sol"]
    assert list(codex["Task models"]["Design"]._build_options()) == [
        "Provider default (astra)",
        "Astra",
        "Sol",
        "Other (type a model id)",
    ]
    assert "Opus (high)" in session.root["LLM"]["claude"]["Orchestrator model"]._build_options()
    err = capsys.readouterr().err
    assert err.count("codex debug models") == 1 and "families only" in err
    assert log.read_text().splitlines() == ["debug models"]


def test_missing_codex_binary_degrades_seven_pickers_and_warns_once(tmp_path, capsys):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ctx = ToolContext(home=tmp_path / "home", env={"HOME": str(tmp_path), "PATH": str(bindir)})
    session = configure._compose_menu(ctx, None, GlobalConfig(), ProjectConfig())
    codex = session.root["LLM"]["codex"]
    assert list(codex["Orchestrator model"]._build_options()) == [
        "Provider default (sol:high)",
        "Astra",
        "Sol",
        "Other (type a model id)",
    ]
    for tag in codex["Task models"].values():
        assert list(tag._build_options())[1:3] == ["Astra", "Sol"]
    err = capsys.readouterr().err
    assert err.count("codex debug models") == 1 and "families only" in err


def test_failed_task_picker_edit_and_renderer_replay_keep_prior_value(tmp_path, monkeypatch):
    session, (ctx, gcfg, _) = _menu(tmp_path)
    tag = session.root["LLM"]["claude"]["Task models"]["Design"]
    monkeypatch.setattr(configure.taskmodels, "validate_selection", lambda *a, **kw: None)
    assert tag.update("fable")
    before = store.global_config_path(ctx.home).read_bytes()
    monkeypatch.setattr(
        configure.taskmodels,
        "validate_selection",
        lambda *a, **kw: (_ for _ in ()).throw(modelprobe.ProbeFailed("rejected")),
    )
    tag._on_change_trigger("opus")
    assert not tag.update("opus")
    assert tag.val == "fable"
    assert gcfg.llm.providers["claude"].tasks.design == "fable"
    assert store.global_config_path(ctx.home).read_bytes() == before


def test_schema_field_and_section_metadata_drive_menu(tmp_path, monkeypatch):
    from types import SimpleNamespace

    original_fields = configure.fields
    monkeypatch.setattr(configure.ProviderConfig, "future_model_flag", "future", raising=False)

    def extended_fields(obj):
        result = list(original_fields(obj))
        if isinstance(obj, configure.ProviderConfig):
            result.append(
                SimpleNamespace(
                    name="future_model_flag",
                    metadata={"label": "Future model flag", "help": "Provider metadata."},
                )
            )
        elif isinstance(obj, GlobalConfig):
            result = [
                SimpleNamespace(name=f.name, metadata={**f.metadata, "label": "LLM settings"})
                if f.name == "llm"
                else f
                for f in result
            ]
        return result

    monkeypatch.setattr(configure, "fields", extended_fields)
    session, _ = _menu(tmp_path)
    assert "LLM settings" in session.root
    provider = session.root["LLM settings"]["claude"]
    assert provider["Future model flag"].description == "Provider metadata."


def test_select_update_persists_and_repeated_update_is_noop(tmp_path, monkeypatch):
    session, (ctx, gcfg, _) = _menu(tmp_path)
    tag = session.root["LLM"]["codex"]["Configured"]
    calls = []
    original = configure._apply_settings

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(configure, "_apply_settings", counted)
    assert tag.update(True)
    assert "codex" in gcfg.llm.providers
    assert "codex" in store.load_global(ctx.home).llm.providers
    assert len(calls) == 1
    assert tag.update(True)
    assert len(calls) == 1
    assert tag.update(False)
    assert "codex" not in store.load_global(ctx.home).llm.providers
    assert tag.update(True)
    assert "codex" in store.load_global(ctx.home).llm.providers


def test_invalid_edit_keeps_bytes_and_accepts_next_valid_edit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (_, _, pcfg) = _menu(tmp_path, root=repo)
    tag = session.root["Worktree (project)"]["Base branch"]
    path = store.project_config_path(repo)
    assert not tag.update("--invalid")
    assert not path.exists()
    assert pcfg.worktree.base_branch == "main"
    assert tag.update("develop")
    assert pcfg.worktree.base_branch == "develop"
    before = path.read_bytes()
    assert tag.update("")
    assert path.read_bytes() == before


def test_project_edit_writes_only_project_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (ctx, _, pcfg) = _menu(tmp_path, root=repo)
    tag = session.root["Worktree (project)"]["Base branch"]
    assert tag.update("develop")
    assert pcfg.worktree.base_branch == "develop"
    assert store.load_project(repo).worktree.base_branch == "develop"
    assert not store.global_config_path(ctx.home).exists()
    assert session.flags == (False, True)


def test_other_model_asks_for_id_and_keeps_custom_value_visible(tmp_path):
    session, (ctx, _, _) = _menu(tmp_path)

    class Interface:
        def ask(self, label):
            assert label == "Model id"
            return "my-custom-model"

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Orchestrator model"]
    assert tag.update("__other_model__")
    assert store.load_global(ctx.home).llm.providers["claude"].model == "my-custom-model"
    assert tag._get_selected_key() == "my-custom-model"


def test_task_model_other_input_saves_nested_leaf(tmp_path):
    session, (ctx, _, _) = _menu(tmp_path)

    class Interface:
        def ask(self, label):
            assert label == "Model id"
            return "claude-fable-5-1"

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Task models"]["Design"]
    assert tag.update("__other_model__")
    assert store.load_global(ctx.home).llm.providers["claude"].tasks.design == "claude-fable-5-1"


def test_real_renderer_two_calls_prompt_other_once_per_edit(tmp_path, monkeypatch):
    session, (ctx, _, _) = _menu(tmp_path)
    writes = []
    save_global = store.save_global

    def counted_save(*args):
        writes.append(None)
        return save_global(*args)

    monkeypatch.setattr(store, "save_global", counted_save)

    class Interface:
        calls = 0

        def ask(self, label):
            assert label == "Model id"
            self.calls += 1
            return f"custom-{self.calls}"

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Orchestrator model"]
    for expected in ("custom-1", "custom-2"):
        tag._on_change_trigger("__other_model__")
        assert tag.update("__other_model__")
        assert tag.val == expected
        assert store.load_global(ctx.home).llm.providers["claude"].model == expected
    assert session.interface.calls == 2
    assert len(writes) == 2


def test_reselect_model_after_provider_toggle_uses_actual_renderer_two_calls(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.default = "codex"
    gcfg.llm.providers["codex"] = configure.ProviderConfig()
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig())
    provider = session.root["LLM"]["claude"]
    model = provider["Orchestrator model"]
    configured = provider["Configured"]

    model._on_change_trigger("opus")
    assert model.update("opus")
    assert model.val == "opus"
    assert configured.update(False)
    assert configured.update(True)
    assert model.val == ""

    model._on_change_trigger("opus")
    assert model.update("opus")
    assert model.val == "opus"
    assert store.load_global(ctx.home).llm.providers["claude"].model == "opus"


@pytest.mark.parametrize("interrupt_at", ["probe", "other"])
def test_interrupted_renderer_edit_cannot_save_on_level_submit(tmp_path, monkeypatch, interrupt_at):
    from mininterface.tag import Tag

    session, (ctx, _, _) = _menu(tmp_path)
    notifications = session.root["LLM"]["claude"]["Native notifications"]
    assert notifications.update(False)
    before = store.global_config_path(ctx.home).read_bytes()
    calls = []

    def interrupted_probe(_ctx, provider, value, *, task, say):
        calls.append(value)
        raise KeyboardInterrupt

    if interrupt_at == "probe":
        monkeypatch.setattr(configure.taskmodels, "validate_selection", interrupted_probe)
        candidate = "sonnet"
        tag = session.root["LLM"]["claude"]["Orchestrator model"]
    else:

        class Interface:
            def ask(self, label):
                raise KeyboardInterrupt

        session.interface = Interface()
        candidate = "__other_model__"
        tag = session.root["LLM"]["claude"]["Orchestrator model"]
    with pytest.raises(KeyboardInterrupt):
        tag._on_change_trigger(candidate)
    assert tag.val == ""
    assert Tag._submit_values((leaf, leaf.val) for leaf in _tags(session.root["LLM"]["claude"]))
    assert store.global_config_path(ctx.home).read_bytes() == before
    assert store.load_global(ctx.home).llm.providers["claude"].notifications is False
    assert calls == (["sonnet"] if interrupt_at == "probe" else [])


def test_cancel_other_model_prompt_leaves_stored_value_unchanged(tmp_path):
    from mininterface.exceptions import Cancelled

    session, (ctx, _, _) = _menu(tmp_path)

    class Interface:
        def ask(self, label):
            raise Cancelled

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Orchestrator model"]
    assert tag.update("__other_model__")
    assert tag.val == ""
    assert not store.global_config_path(ctx.home).exists()


def test_run_menu_first_exit_seeds_defaults_and_returns_flags(tmp_path, monkeypatch):
    ctx = ToolContext(home=tmp_path / "home", env={})
    seen = []

    class Interface:
        def form(self, root):
            seen.append(root)

    monkeypatch.setattr(configure, "_text_interface", lambda plain_menu: Interface())
    result = configure._run_menu(ctx, None, GlobalConfig(), ProjectConfig())
    assert result == (True, False)
    assert store.load_global(ctx.home).llm.default == "claude"
    assert len(seen) == 1


def test_run_menu_cancel_still_seeds_missing_project_once(tmp_path, monkeypatch):
    from mininterface.exceptions import Cancelled

    root = tmp_path / "repo"
    root.mkdir()
    ctx = ToolContext(home=tmp_path / "home", env={})

    class Interface:
        def form(self, root):
            raise Cancelled

    monkeypatch.setattr(configure, "_text_interface", lambda plain_menu: Interface())
    result = configure._run_menu(ctx, root, GlobalConfig(), ProjectConfig())
    assert result == (True, True)
    assert store.load_project(root).worktree.base_branch == "main"


def test_disable_edit_reenable_updates_stable_configured_tag(tmp_path):
    session, (ctx, gcfg, _) = _menu(tmp_path)
    provider = session.root["LLM"]["claude"]
    toggle = provider["Configured"]
    assert toggle.update(False)
    assert toggle.val is False
    assert "claude" not in gcfg.llm.providers
    assert provider["Orchestrator model"].update("sonnet")
    assert toggle.val is True
    assert store.load_global(ctx.home).llm.providers["claude"].model == "sonnet"
    before = store.global_config_path(ctx.home).read_bytes()
    assert toggle.update(True)
    assert store.global_config_path(ctx.home).read_bytes() == before


def test_disabling_provider_with_saved_models_survives_level_revalidation(tmp_path, monkeypatch):
    from mininterface.tag import Tag

    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["claude"].model = "sonnet"
    gcfg.llm.providers["claude"].tasks.plan = "opus"
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig())
    provider = session.root["LLM"]["claude"]
    assert provider["Configured"].update(False)
    persisted = store.global_config_path(ctx.home).read_bytes()
    assert provider["Orchestrator model"].val == ""
    assert provider["Task models"]["Plan"].val == ""
    assert Tag._submit_values((tag, tag.val) for tag in _tags(provider))
    assert "claude" not in gcfg.llm.providers
    assert store.global_config_path(ctx.home).read_bytes() == persisted


def test_empty_provider_set_can_be_opened_and_provider_enabled(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers.clear()
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig())
    assert session.root["LLM"]["claude"]["Configured"].val is False
    assert session.root["LLM"]["codex"]["Configured"].update(True)
    assert list(store.load_global(ctx.home).llm.providers) == ["codex"]


def test_custom_stored_model_remains_selectable(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["claude"].model = "my-custom-model"
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig())
    tag = session.root["LLM"]["claude"]["Orchestrator model"]
    assert "my-custom-model" in tag._build_options().values()
    assert tag._get_selected_key() == "my-custom-model"


def test_first_menu_exit_carries_legacy_project_values(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    ctx = ToolContext(home=tmp_path / "home", env={})
    pcfg = ProjectConfig()
    pcfg.worktree.base_branch = "develop"

    class Interface:
        def form(self, root):
            pass

    monkeypatch.setattr(configure, "_text_interface", lambda plain_menu: Interface())
    assert configure._run_menu(ctx, root, GlobalConfig(), pcfg, legacy=True) == (True, True)
    assert store.load_project(root).worktree.base_branch == "develop"


def test_menu_maps_unavailable_text_interface_to_refusal(tmp_path, monkeypatch):
    from mininterface.exceptions import InterfaceNotAvailable

    def unavailable(plain_menu):
        raise InterfaceNotAvailable

    monkeypatch.setattr(configure, "_text_interface", unavailable)
    ctx = ToolContext(home=tmp_path / "home", env={})
    with pytest.raises(Refusal, match="TTY"):
        configure._run_menu(ctx, None, GlobalConfig(), ProjectConfig())


def test_interactive_poststeps_run_once_and_project_only_edit_keeps_legacy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text('{"schema_version": 1}')
    store.save_global(home, GlobalConfig())
    root = tmp_path / "repo"
    root.mkdir()
    calls = []
    monkeypatch.setattr(configure, "repo_root", lambda ctx: str(root))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def project_only(ctx, root, gcfg, pcfg, *, legacy=False, plain_menu=False):
        pcfg.worktree.base_branch = "develop"
        store.save_project(root, pcfg)
        return False, True

    monkeypatch.setattr(configure, "_run_menu", project_only)
    monkeypatch.setattr(
        configure, "_ensure_instructions", lambda *args: calls.append("instructions")
    )
    monkeypatch.setattr(configure, "_ensure_plugins", lambda *args: calls.append("plugins"))
    ctx = ToolContext(home=home, env={})
    assert configure.run_configure(ctx, defaults=False, sets=[]) == 0
    assert calls == ["instructions", "plugins"]
    assert (home / "config.json").exists()
    assert store.load_project(root).worktree.base_branch == "develop"
