from dataclasses import asdict, fields

import pytest

from omc.config import store
from omc.config.schema import (
    GitNexusConfig,
    GlobalConfig,
    LLMConfig,
    ProjectConfig,
    ProviderConfig,
    WorktreeConfig,
)
from omc.errors import ConfigError


def test_global_unknown_fields_warn_once_and_disappear_on_save(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 1\nbogus: true\nllm:\n  default: codex\n"
        "  extra: 9\n  providers:\n"
        "    codex:\n      model: gpt-x\n      future: value\n"
        "      tasks:\n        surprise: yes\n"
    )
    cfg = store.load_global(tmp_path)
    assert cfg.llm.default == "codex"
    assert cfg.llm.providers["codex"].model == "gpt-x"
    warning = capsys.readouterr()
    assert warning.out == ""
    assert warning.err.count("· config: ignoring unknown key(s)") == 1
    assert str(path) in warning.err
    for name in ("bogus", "extra", "surprise", "future"):
        assert name in warning.err
    store.save_global(tmp_path, cfg)
    saved = path.read_text()
    for name in ("bogus", "extra", "surprise", "future"):
        assert name not in saved


def test_unknown_mixed_type_keys_do_not_break_warning(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("schema_version: 1\n1: a\nnull: b\nx: c\n")
    assert store.load_global(tmp_path).schema_version == 1
    assert capsys.readouterr().err.count("· config: ignoring unknown key(s)") == 1


def test_provider_notifications_default_and_round_trip(tmp_path):
    assert ProviderConfig().notifications is True
    (tmp_path / "config.yaml").write_text("llm:\n  providers:\n    codex:\n      model: gpt-x\n")
    assert store.load_global(tmp_path).llm.providers["codex"].notifications is True
    cfg = GlobalConfig()
    cfg.llm.providers["codex"] = ProviderConfig(notifications=False)
    store.save_global(tmp_path, cfg)
    assert store.load_global(tmp_path).llm.providers["codex"].notifications is False


def test_set_provider_notifications_bool():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.providers.codex.notifications", "false")
    assert cfg.llm.providers["codex"].notifications is False
    store.set_key(cfg, "llm.providers.codex.notifications", "true")
    assert cfg.llm.providers["codex"].notifications is True
    for value in ("yes", "1"):
        with pytest.raises(ConfigError, match="true or false"):
            store.set_key(cfg, "llm.providers.codex.notifications", value)
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "llm.providers.codex.notifications.extra", "true")
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.set_key(cfg, "llm.providers.retired.notifications", "true")


@pytest.mark.parametrize("value", ['"false"', "1", "null", "[true]", "{enabled: true}"])
def test_provider_notifications_wrong_type(tmp_path, value):
    (tmp_path / "config.yaml").write_text(
        f"llm:\n  providers:\n    codex:\n      notifications: {value}\n"
    )
    with pytest.raises(ConfigError, match="llm.providers.codex.notifications"):
        store.load_global(tmp_path)


def test_schema_menu_metadata_does_not_change_persisted_defaults():
    assert asdict(GlobalConfig()) == {
        "schema_version": 1,
        "llm": {
            "default": "claude",
            "providers": {
                "claude": {
                    "model": "",
                    "notifications": True,
                    "tasks": {
                        "design": "",
                        "plan": "",
                        "review": "",
                        "simple": "",
                        "medium": "",
                        "high": "",
                    },
                }
            },
            "provider_selection_per_step": False,
        },
        "gitnexus": {"embeddings": False},
    }
    assert asdict(ProjectConfig()) == {
        "schema_version": 1,
        "worktree": {"branch_prefix": "feature/", "base_branch": "main"},
    }
    for cls in (
        GlobalConfig,
        ProjectConfig,
        LLMConfig,
        ProviderConfig,
        type(ProviderConfig().tasks),
        WorktreeConfig,
        GitNexusConfig,
    ):
        for item in fields(cls):
            if item.name == "schema_version":
                continue
            assert set(item.metadata) == {"label", "help"}
            assert item.metadata["label"]


def test_remove_provider_persists_absence_even_when_default_uses_it(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-6")
    cfg.llm.default = "codex"
    store.remove_provider(cfg, "codex")
    store.remove_provider(cfg, "codex")  # an already-absent entry is harmless
    store.save_global(tmp_path, cfg)
    loaded = store.load_global(tmp_path)
    assert loaded.llm.default == "codex"
    assert "codex" not in loaded.llm.providers
    assert "codex:" not in (tmp_path / "config.yaml").read_text()


def test_remove_provider_rejects_unknown_name_without_mutation():
    cfg = GlobalConfig()
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.remove_provider(cfg, "retired")
    assert list(cfg.llm.providers) == ["claude"]


@pytest.mark.parametrize("value", ["true", "false"])
def test_provider_notifications_bool_loads(tmp_path, value):
    (tmp_path / "config.yaml").write_text(
        f"llm:\n  providers:\n    codex:\n      notifications: {value}\n"
    )
    assert store.load_global(tmp_path).llm.providers["codex"].notifications is (value == "true")


def test_provider_model_leaf_requires_a_string(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  providers:\n    codex:\n      model: 1\n")
    with pytest.raises(ConfigError, match="llm.providers.codex.model"):
        store.load_global(tmp_path)


def test_set_provider_model_leaf_requires_a_string():
    cfg = GlobalConfig()
    with pytest.raises(ConfigError, match="llm.providers.codex.model"):
        store.set_key(cfg, "llm.providers.codex.model", 1)
    assert "codex" not in cfg.llm.providers


def test_set_key_provider_model():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.providers.claude.model", "claude-fable-5")
    assert cfg.llm.providers["claude"].model == "claude-fable-5"


def test_old_config_hydrates_blank_task_choices_and_saves_them_blank(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  providers:\n    codex:\n      model: ''\n")
    cfg = store.load_global(tmp_path)
    assert asdict(cfg.llm.providers["codex"].tasks) == dict.fromkeys(
        ("design", "plan", "review", "simple", "medium", "high"), ""
    )
    store.save_global(tmp_path, cfg)
    assert store.load_global(tmp_path) == cfg
    assert "design: ''" in (tmp_path / "config.yaml").read_text()


@pytest.mark.parametrize("task", ("design", "plan", "review", "simple", "medium", "high"))
def test_set_each_task_model_leaf(task):
    cfg = GlobalConfig()
    store.set_key(cfg, f"llm.providers.codex.tasks.{task}", "sol:high")
    assert getattr(cfg.llm.providers["codex"].tasks, task) == "sol:high"


@pytest.mark.parametrize(
    ("provider", "value"),
    [
        ("claude", "fable"),
        ("claude", "opus:xhigh"),
        ("claude", "claude-fable-5-1"),
        ("claude", "claude-sonnet-4-5[1m]"),
        ("codex", "astra"),
        ("codex", "sol:medium"),
        ("codex", "gpt-6-sol:ultra"),
        ("codex", "o3"),
        ("codex", "my-custom-model"),
        ("codex", ""),
    ],
)
def test_validate_task_model_accepts_families_and_full_ids(provider, value):
    assert store.validate_task_model(provider, value) == value


@pytest.mark.parametrize(
    ("provider", "value"),
    [
        ("claude", "haiku"),
        ("claude", "claude-haiku-4"),
        ("codex", "luna"),
        ("codex", "gpt-6-luna"),
        ("codex", "fable"),
        ("claude", "sol"),
        ("codex", "sol:extreme"),
        ("claude", "opus:ultra"),
        ("codex", "sol:"),
        ("codex", ":high"),
        ("codex", "sol high"),
        ("codex", "--model"),
        ("codex", 123),
    ],
)
def test_invalid_task_value_rejected_without_mutating_config(provider, value):
    cfg = GlobalConfig()
    before = asdict(cfg)
    with pytest.raises(ConfigError):
        store.set_key(cfg, f"llm.providers.{provider}.tasks.plan", value)
    assert asdict(cfg) == before


@pytest.mark.parametrize("suffix", ("tasks", "tasks.unknown", "tasks.plan.extra"))
def test_invalid_task_path_rejected_without_mutating_config(suffix):
    cfg = GlobalConfig()
    before = asdict(cfg)
    with pytest.raises(ConfigError):
        store.set_key(cfg, f"llm.providers.codex.{suffix}", "sol")
    assert asdict(cfg) == before


@pytest.mark.parametrize("leaf,value", (("model", "sol"), ("notifications", "true")))
def test_provider_scalar_trailing_dot_rejected_without_mutation(leaf, value):
    cfg = GlobalConfig()
    before = asdict(cfg)
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, f"llm.providers.codex.{leaf}.", value)
    assert asdict(cfg) == before


@pytest.mark.parametrize(
    "body",
    (
        "tasks: []",
        "tasks:\n        plan: 123",
        "tasks:\n        plan: luna",
    ),
)
def test_invalid_task_value_on_load_names_path(tmp_path, body):
    (tmp_path / "config.yaml").write_text(f"llm:\n  providers:\n    codex:\n      {body}\n")
    with pytest.raises(ConfigError, match="tasks"):
        store.load_global(tmp_path)


@pytest.mark.parametrize("key", ["llm.default", "llm.providers.retired.model"])
def test_set_key_rejects_unsupported_provider_without_mutating_config(key):
    cfg = GlobalConfig()
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.set_key(cfg, key, "retired" if key == "llm.default" else "model-x")
    assert cfg.llm.default == "claude"
    assert "retired" not in cfg.llm.providers


@pytest.mark.parametrize(
    "body",
    [
        "llm:\n  default: retired\n",
        "llm:\n  providers:\n    retired:\n      model: model-x\n",
    ],
)
def test_load_global_rejects_existing_unsupported_provider_with_supported_choices(tmp_path, body):
    (tmp_path / "config.yaml").write_text(body)
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.load_global(tmp_path)


def test_save_global_rejects_unsupported_provider_before_writing(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.providers["retired"] = ProviderConfig()
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.save_global(tmp_path, cfg)
    assert not (tmp_path / "config.yaml").exists()


def test_set_key_rejects_unknown_and_sections():
    cfg = GlobalConfig()
    with pytest.raises(ConfigError):
        store.set_key(cfg, "llm.bogus", "x")
    with pytest.raises(ConfigError):
        store.set_key(cfg, "llm", "x")
    with pytest.raises(ConfigError):
        store.set_key(cfg, "schema_version", "9")


def test_section_must_be_object(tmp_path):
    (tmp_path / "config.yaml").write_text("llm: oops\n")
    with pytest.raises(ConfigError, match="llm"):
        store.load_global(tmp_path)


def test_provider_entry_must_be_object(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  providers:\n    claude: 5\n")
    with pytest.raises(ConfigError, match="claude"):
        store.load_global(tmp_path)


def test_legacy_notifications_are_ignored_on_load_and_save(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("schema_version: 1\nnotifications:\n  enabled: true\n")
    cfg = store.load_global(tmp_path)
    assert "ignoring unknown key" in capsys.readouterr().err
    store.save_global(tmp_path, cfg)
    assert "\nnotifications:" not in (tmp_path / "config.yaml").read_text()
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.enabled", "true")


# --- split YAML store ---


def test_load_global_missing_returns_none(tmp_path):
    assert store.load_global(tmp_path) is None


def test_global_round_trip_yaml(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.default = "codex"
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-x")
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "schema_version" in text and "{" not in text  # YAML block style, not JSON
    loaded = store.load_global(tmp_path)
    assert loaded.llm.default == "codex"
    assert loaded.llm.providers["codex"].model == "gpt-x"
    assert loaded.schema_version == 1


def test_global_ignores_project_section(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("schema_version: 1\nworktree:\n  base_branch: dev\n")
    assert store.load_global(tmp_path).llm.default == "claude"
    assert "worktree" in capsys.readouterr().err


def test_project_round_trip_yaml(tmp_path):
    cfg = ProjectConfig()
    cfg.worktree.branch_prefix = "wip/"
    cfg.worktree.base_branch = "develop"
    store.save_project(tmp_path, cfg)
    assert (tmp_path / ".omc" / "config.yaml").is_file()
    loaded = store.load_project(tmp_path)
    assert loaded.worktree.branch_prefix == "wip/"
    assert loaded.worktree.base_branch == "develop"


def test_project_missing_returns_none(tmp_path):
    assert store.load_project(tmp_path) is None


def test_project_ignores_global_keys(tmp_path, capsys):
    (tmp_path / ".omc").mkdir()
    (tmp_path / ".omc" / "config.yaml").write_text("llm:\n  default: claude\n")
    assert store.load_project(tmp_path).worktree.base_branch == "main"
    assert "llm" in capsys.readouterr().err


def test_project_nested_unknown_warns_once_and_preserves_worktree(tmp_path, capsys):
    _write_project(tmp_path, "future: yes\nworktree:\n  base_branch: develop\n  extra: yes\n")
    cfg = store.load_project(tmp_path)
    assert cfg.worktree.base_branch == "develop"
    assert capsys.readouterr().err.count("· config: ignoring unknown key(s)") == 1
    store.save_project(tmp_path, cfg)
    assert "extra:" not in (tmp_path / ".omc" / "config.yaml").read_text()


def test_yaml_parse_error_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text("{nope")
    with pytest.raises(ConfigError, match="invalid YAML"):
        store.load_global(tmp_path)


def test_yaml_non_mapping_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="expected a mapping"):
        store.load_global(tmp_path)


def test_set_key_on_split_schemas():
    gcfg = GlobalConfig()
    store.set_key(gcfg, "llm.default", "codex")
    assert gcfg.llm.default == "codex"
    pcfg = ProjectConfig()
    store.set_key(pcfg, "worktree.base_branch", "master")
    assert pcfg.worktree.base_branch == "master"
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(gcfg, "worktree.base_branch", "master")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(pcfg, "llm.default", "claude")


# --- worktree value hardening (values flow into git argv) ---


def _write_project(tmp_path, body):
    (tmp_path / ".omc").mkdir(exist_ok=True)
    (tmp_path / ".omc" / "config.yaml").write_text(body)


def test_hydrate_rejects_option_injection_base_branch(tmp_path):
    _write_project(tmp_path, 'schema_version: 1\nworktree:\n  base_branch: "--upload-pack=/x"\n')
    with pytest.raises(ConfigError, match="worktree.base_branch"):
        store.load_project(tmp_path)


def test_hydrate_rejects_non_string_base_branch(tmp_path):
    _write_project(tmp_path, "schema_version: 1\nworktree:\n  base_branch: 1.0\n")
    with pytest.raises(ConfigError, match="worktree.base_branch"):
        store.load_project(tmp_path)


def test_hydrate_rejects_whitespace_branch_prefix(tmp_path):
    _write_project(tmp_path, 'schema_version: 1\nworktree:\n  branch_prefix: "a b"\n')
    with pytest.raises(ConfigError, match="worktree.branch_prefix"):
        store.load_project(tmp_path)


def test_hydrate_allows_empty_branch_prefix(tmp_path):
    _write_project(tmp_path, 'schema_version: 1\nworktree:\n  branch_prefix: ""\n')
    loaded = store.load_project(tmp_path)
    assert loaded.worktree.branch_prefix == ""


def test_hydrate_rejects_empty_base_branch(tmp_path):
    _write_project(tmp_path, 'schema_version: 1\nworktree:\n  base_branch: ""\n')
    with pytest.raises(ConfigError, match="worktree.base_branch"):
        store.load_project(tmp_path)


def test_set_key_worktree_rejects_option_like_value():
    pcfg = ProjectConfig()
    with pytest.raises(ConfigError, match="worktree.base_branch"):
        store.set_key(pcfg, "worktree.base_branch", "--x")
    with pytest.raises(ConfigError, match="worktree.branch_prefix"):
        store.set_key(pcfg, "worktree.branch_prefix", "a b")
    with pytest.raises(ConfigError, match="worktree.base_branch"):
        store.set_key(pcfg, "worktree.base_branch", "")
    # a clean value still sets
    store.set_key(pcfg, "worktree.base_branch", "develop")
    assert pcfg.worktree.base_branch == "develop"
    store.set_key(pcfg, "worktree.branch_prefix", "")  # empty prefix allowed
    assert pcfg.worktree.branch_prefix == ""


# --- legacy combined config.json (read by `omc configure` migration only) ---


def test_load_legacy_missing_returns_none(tmp_path):
    assert store.load_legacy(tmp_path) is None


def test_load_legacy_splits_sections(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "llm": {"default": "codex"},'
        ' "worktree": {"base_branch": "develop"},'
        ' "notifications": {"enabled": true, "backend": "macos"}}'
    )
    gcfg, pcfg = store.load_legacy(tmp_path)
    assert gcfg.llm.default == "codex"
    assert pcfg.worktree.base_branch == "develop"


def test_legacy_nested_unknown_warns_once(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(
        '{"unknown": 1, "llm": {"default": "codex", "extra": 2, '
        '"providers": {"codex": {"model": "gpt-x", "future": 3}}}, '
        '"worktree": {"base_branch": "develop", "extra": 4}}'
    )
    gcfg, pcfg = store.load_legacy(tmp_path)
    assert gcfg.llm.providers["codex"].model == "gpt-x"
    assert pcfg.worktree.base_branch == "develop"
    warning = capsys.readouterr().err
    assert warning.count("· config: ignoring unknown key(s)") == 1
    assert str(path) in warning


def test_load_legacy_rejects_bad_json(tmp_path):
    (tmp_path / "config.json").write_text("{nope")
    with pytest.raises(ConfigError):
        store.load_legacy(tmp_path)


# --- gitnexus.embeddings (spec 2026-10-27 §3) ---


def test_gitnexus_embeddings_defaults_off_everywhere(tmp_path):
    from omc.config.schema import Config

    assert GlobalConfig().gitnexus.embeddings is False
    assert Config().gitnexus.embeddings is False
    # a config.yaml written before the option existed hydrates to off
    (tmp_path / "config.yaml").write_text("schema_version: 1\nllm:\n  default: claude\n")
    assert store.load_global(tmp_path).gitnexus.embeddings is False


def test_gitnexus_embeddings_round_trips_and_is_typed(tmp_path):
    cfg = GlobalConfig()
    cfg.gitnexus.embeddings = True
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "gitnexus:\n  embeddings: true" in text
    assert store.load_global(tmp_path).gitnexus.embeddings is True


@pytest.mark.parametrize("value", ['"yes"', "1", "null", "[true]"])
def test_gitnexus_embeddings_wrong_type_is_a_config_error(tmp_path, value):
    (tmp_path / "config.yaml").write_text(f"gitnexus:\n  embeddings: {value}\n")
    with pytest.raises(ConfigError, match="gitnexus.embeddings"):
        store.load_global(tmp_path)


def test_set_gitnexus_embeddings_accepts_exactly_true_false():
    cfg = GlobalConfig()
    store.set_key(cfg, "gitnexus.embeddings", "true")
    assert cfg.gitnexus.embeddings is True
    store.set_key(cfg, "gitnexus.embeddings", "false")
    assert cfg.gitnexus.embeddings is False
    for value in ("1", "on", "True", "yes"):
        with pytest.raises(ConfigError, match="gitnexus.embeddings expects true or false"):
            store.set_key(cfg, "gitnexus.embeddings", value)
    assert cfg.gitnexus.embeddings is False
    with pytest.raises(ConfigError, match="gitnexus is a section, not a settable key"):
        store.set_key(cfg, "gitnexus", "true")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "gitnexus.nope", "true")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "gitnexus.embeddings.extra", "true")


def test_project_config_has_no_gitnexus_section():
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(ProjectConfig(), "gitnexus.embeddings", "true")


def test_load_effective_carries_gitnexus_from_the_global_file(tmp_path):
    from omc.config import resolve
    from omc.toolctx import ToolContext

    (tmp_path / "config.yaml").write_text(
        "llm:\n  default: claude\ngitnexus:\n  embeddings: true\n"
    )
    ctx = ToolContext(home=tmp_path, env={"HOME": str(tmp_path), "PATH": ""})
    cfg = resolve.load_effective(ctx)
    assert cfg is not None and cfg.gitnexus.embeddings is True


# --- llm.provider_selection_per_step (spec 2026-11-12 §4.2) ---


def test_provider_selection_per_step_defaults_off_everywhere(tmp_path):
    from omc.config.schema import Config

    assert LLMConfig().provider_selection_per_step is False
    assert GlobalConfig().llm.provider_selection_per_step is False
    assert Config().llm.provider_selection_per_step is False
    # D7: a config.yaml written before the option existed hydrates to off
    (tmp_path / "config.yaml").write_text("schema_version: 1\nllm:\n  default: claude\n")
    assert store.load_global(tmp_path).llm.provider_selection_per_step is False


def test_provider_selection_per_step_round_trips_and_is_typed(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.provider_selection_per_step = True
    store.save_global(tmp_path, cfg)
    assert "provider_selection_per_step: true" in (tmp_path / "config.yaml").read_text()
    assert store.load_global(tmp_path).llm.provider_selection_per_step is True


@pytest.mark.parametrize("value", ['"false"', "1", "null", "[true]"])
def test_provider_selection_per_step_wrong_type_is_a_config_error(tmp_path, value):
    (tmp_path / "config.yaml").write_text(f"llm:\n  provider_selection_per_step: {value}\n")
    with pytest.raises(ConfigError, match="llm.provider_selection_per_step"):
        store.load_global(tmp_path)


def test_set_provider_selection_per_step_accepts_exactly_true_false():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.provider_selection_per_step", "true")
    assert cfg.llm.provider_selection_per_step is True
    store.set_key(cfg, "llm.provider_selection_per_step", "false")
    assert cfg.llm.provider_selection_per_step is False
    for value in ("yes", "1", "", "True", "on"):
        with pytest.raises(
            ConfigError, match="llm.provider_selection_per_step expects true or false"
        ):
            store.set_key(cfg, "llm.provider_selection_per_step", value)
    assert cfg.llm.provider_selection_per_step is False
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "llm.provider_selection_per_step.extra", "true")


def test_legacy_config_carries_provider_selection_per_step(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "llm": {"default": "claude", "provider_selection_per_step": true}}'
    )
    gcfg, _ = store.load_legacy(tmp_path)
    assert gcfg.llm.provider_selection_per_step is True


def test_leftover_docs_keys_load_with_the_ignoring_notice(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n"
        "  default: claude\n"
        "  docs: {provider: '', backend: bogus}\n"
        "  providers:\n"
        "    claude: {model: '', docs_model: claude-sonnet-4-1}\n"
    )
    cfg = store.load_global(tmp_path)
    assert cfg.llm.default == "claude"
    err = capsys.readouterr().err
    assert (
        "· config: ignoring unknown key(s) ['llm.docs', 'llm.providers.claude.docs_model'] in"
        in err
    )
    store.save_global(tmp_path, cfg)
    assert "docs" not in path.read_text()


def test_legacy_secrets_key_is_an_ignored_unknown(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text('{"llm": {"default": "codex"}, "secrets": {}}')
    gcfg, _ = store.load_legacy(tmp_path)
    assert gcfg.llm.default == "codex"
    err = capsys.readouterr().err
    assert "· config: ignoring unknown key(s) ['secrets'] in" in err
