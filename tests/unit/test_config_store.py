import stat as _stat
from dataclasses import asdict, fields

import pytest

from omc.config import store
from omc.config.schema import (
    DocsConfig,
    GlobalConfig,
    LLMConfig,
    ProjectConfig,
    ProviderConfig,
    SecretsConfig,
    WorktreeConfig,
)
from omc.errors import ConfigError


def test_global_unknown_fields_warn_once_and_disappear_on_save(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(
        "schema_version: 1\nbogus: true\nllm:\n  default: codex\n"
        "  extra: 9\n  docs:\n    surprise: yes\n  providers:\n"
        "    codex:\n      model: gpt-x\n      future: value\n"
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
            "docs": {"provider": "", "backend": "cli"},
            "providers": {"claude": {"model": "", "notifications": True, "docs_model": ""}},
        },
    }
    assert asdict(ProjectConfig()) == {
        "schema_version": 1,
        "worktree": {"branch_prefix": "feature/", "base_branch": "main"},
    }
    for cls in (
        GlobalConfig,
        ProjectConfig,
        LLMConfig,
        DocsConfig,
        ProviderConfig,
        WorktreeConfig,
        SecretsConfig,
    ):
        for item in fields(cls):
            if item.name == "schema_version":
                continue
            assert set(item.metadata) == {"label", "help"}
            assert item.metadata["label"]


def test_remove_provider_persists_absence_even_when_default_and_docs_use_it(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-6")
    cfg.llm.default = "codex"
    cfg.llm.docs.provider = "codex"
    store.remove_provider(cfg, "codex")
    store.remove_provider(cfg, "codex")  # an already-absent entry is harmless
    store.save_global(tmp_path, cfg)
    loaded = store.load_global(tmp_path)
    assert loaded.llm.default == loaded.llm.docs.provider == "codex"
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


@pytest.mark.parametrize("leaf", ["model", "docs_model"])
def test_provider_model_leaves_require_strings(tmp_path, leaf):
    (tmp_path / "config.yaml").write_text(f"llm:\n  providers:\n    codex:\n      {leaf}: 1\n")
    with pytest.raises(ConfigError, match=f"llm.providers.codex.{leaf}"):
        store.load_global(tmp_path)


@pytest.mark.parametrize("leaf", ["model", "docs_model"])
def test_set_provider_model_leaves_require_strings(leaf):
    cfg = GlobalConfig()
    with pytest.raises(ConfigError, match=f"llm.providers.codex.{leaf}"):
        store.set_key(cfg, f"llm.providers.codex.{leaf}", 1)
    assert "codex" not in cfg.llm.providers


def test_set_key_provider_model():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.providers.claude.model", "claude-fable-5")
    assert cfg.llm.providers["claude"].model == "claude-fable-5"


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


def test_docs_defaults_and_round_trip(tmp_path):
    cfg = GlobalConfig()
    assert cfg.llm.docs.provider == "" and cfg.llm.docs.backend == "cli"
    cfg.llm.docs.backend = "api"
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "docs:" in text and "backend: api" in text
    assert store.load_global(tmp_path).llm.docs.backend == "api"


def test_config_without_docs_section_loads_with_defaults(tmp_path):
    # Every existing config.yaml predates `docs:` — omitted fields take defaults.
    (tmp_path / "config.yaml").write_text("schema_version: 1\nllm:\n  default: claude\n")
    cfg = store.load_global(tmp_path)
    assert cfg.llm.docs.backend == "cli" and cfg.llm.docs.provider == ""


def test_set_key_docs_leaves_and_blank_provider():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.docs.backend", "api")
    store.set_key(cfg, "llm.docs.provider", "claude")
    assert (cfg.llm.docs.backend, cfg.llm.docs.provider) == ("api", "claude")
    store.set_key(cfg, "llm.docs.provider", "")  # blank = follow llm.default
    assert cfg.llm.docs.provider == ""
    with pytest.raises(ConfigError, match="llm.docs.backend"):
        store.set_key(cfg, "llm.docs.backend", "http")
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.set_key(cfg, "llm.docs.provider", "retired")
    with pytest.raises(ConfigError, match="unknown config key: docs.nope"):
        store.set_key(cfg, "llm.docs.nope", "x")
    with pytest.raises(ConfigError, match="unknown config key: docs.backend.x"):
        store.set_key(cfg, "llm.docs.backend.x", "api")
    with pytest.raises(ConfigError, match="llm.docs is a section"):
        store.set_key(cfg, "llm.docs", "api")  # bare section is refused with the full dotted path


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("llm:\n  docs:\n    backend: http\n", "llm.docs.backend"),
        ("llm:\n  docs:\n    backend: 1\n", "llm.docs.backend"),
        ("llm:\n  docs:\n    provider: [a]\n", "llm.docs.provider"),
        ("llm:\n  docs:\n    provider: retired\n", "retired.*claude.*codex"),
        ("llm:\n  default: codex\n  docs:\n    backend: api\n", "not supported yet"),
        ("llm:\n  docs:\n    provider: codex\n    backend: api\n", "not supported yet"),
    ],
)
def test_load_rejects_bad_docs_values(tmp_path, body, match):
    (tmp_path / "config.yaml").write_text(body)
    with pytest.raises(ConfigError, match=match):
        store.load_global(tmp_path)


def test_api_for_codex_refused_on_save_regardless_of_set_order(tmp_path):
    # --set order is arbitrary: backend=api then default=codex passes every
    # set-time check; the SAVE path must refuse what the next load would reject.
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.docs.backend", "api")
    store.set_key(cfg, "llm.providers.codex.model", "o-x")
    store.set_key(cfg, "llm.default", "codex")
    with pytest.raises(ConfigError, match="codex: API documentation backend not supported yet"):
        store.save_global(tmp_path, cfg)
    assert not (tmp_path / "config.yaml").exists()
    with pytest.raises(ConfigError, match="codex: API documentation backend"):
        store.validate_llm(cfg.llm)


_KEY = "sk-ant-api03-EXAMPLEKEYabcdefghijklmnop1234"


def test_secrets_round_trip_mode_0600_and_missing_is_empty(tmp_path):
    assert store.load_secrets(tmp_path).api_keys == {}  # missing file, never an error
    store.save_secrets(tmp_path, SecretsConfig(api_keys={"claude": _KEY}))
    path = tmp_path / "secrets.yaml"
    assert _stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load_secrets(tmp_path).api_keys == {"claude": _KEY}
    assert not list(tmp_path.glob("secrets.yaml.*"))  # no temp file left behind


def test_save_secrets_creates_home_and_tightens_a_0644_file(tmp_path):
    home = tmp_path / "fresh-home"  # a first-ever --set api_key= may run before any save
    store.save_secrets(home, SecretsConfig(api_keys={"claude": _KEY}))
    assert (home / "secrets.yaml").exists()
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "secrets.yaml").write_text("schema_version: 1\napi_keys: {}\n")
    (loose / "secrets.yaml").chmod(0o644)
    store.save_secrets(loose, SecretsConfig(api_keys={"claude": _KEY}))
    assert _stat.S_IMODE((loose / "secrets.yaml").stat().st_mode) == 0o600


def test_secrets_repr_never_shows_the_key():
    cfg = SecretsConfig(api_keys={"claude": _KEY})
    assert _KEY not in repr(cfg) and _KEY not in str(cfg)


@pytest.mark.parametrize(
    "body",
    [
        "api_keys: [a]\n",
        "api_keys:\n  claude: 31\n",  # YAML reads 0x1F as an int
        "api_keys:\n  retired: sk-x\n",
        "api_keys:\n  claude: ''\n",
        "api_keys:\n  claude: 'sk-x y'\n",
        "api_keys:\n  claude: 'op://Employee/item/password'\n",
        "- not a mapping\n",
    ],
)
def test_load_secrets_rejects_bad_files(tmp_path, body):
    (tmp_path / "secrets.yaml").write_text(body)
    with pytest.raises(ConfigError, match="secrets.yaml"):
        store.load_secrets(tmp_path)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        # a key pasted where the provider NAME belongs (transposed by hand)
        (f"api_keys:\n  {_KEY}: claude\n", "unsupported provider name in api_keys"),
    ],
)
def test_load_secrets_never_echoes_a_hand_edited_key_name(tmp_path, body, match):
    (tmp_path / "secrets.yaml").write_text(body)
    with pytest.raises(ConfigError, match=match) as exc:
        store.load_secrets(tmp_path)
    assert "secrets.yaml" in str(exc.value)
    assert _KEY not in str(exc.value) and _KEY[:12] not in str(exc.value)


def test_secrets_unknown_top_level_warns_by_count_without_echoing_secrets(tmp_path, capsys):
    path = tmp_path / "secrets.yaml"
    path.write_text(
        f"schema_version: 1\napi_keys:\n  claude: {_KEY}\n{_KEY}: {_KEY}\nnull: stray\n1: stray\n"
    )
    assert store.load_secrets(tmp_path).api_keys == {"claude": _KEY}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"· config: ignoring 3 unknown keys in {path}\n"
    assert _KEY not in captured.err


def test_load_secrets_yaml_error_names_file_but_not_contents(tmp_path):
    # PyYAML quotes the offending line in its message — that line may BE the key.
    (tmp_path / "secrets.yaml").write_text(f"api_keys:\n  claude: {_KEY}\n  : bad\n\tx")
    with pytest.raises(ConfigError) as exc:
        store.load_secrets(tmp_path)
    assert "secrets.yaml" in str(exc.value) and _KEY not in str(exc.value)


def test_load_secrets_yaml_error_omits_a_short_key_pyyaml_would_quote_whole(tmp_path):
    # A long key is truncated by PyYAML's snippet, so the test above cannot catch a
    # regression to `{exc}`; a short key sits wholly inside the quoted snippet.
    short = "sk-q7Zx"
    (tmp_path / "secrets.yaml").write_text(f"api_keys:\n  claude: {short}\n  : bad\n\tx")
    with pytest.raises(ConfigError) as exc:
        store.load_secrets(tmp_path)
    assert "secrets.yaml" in str(exc.value) and short not in str(exc.value)


@pytest.mark.parametrize(
    "value", ["", "sk x", "sk\tx", "sk-x\n", "op://Employee/item/password", "sk-é", 7]
)
def test_validate_api_key_rejects_without_echoing(value):
    with pytest.raises(ConfigError) as exc:
        store.validate_api_key(value, "llm.providers.claude.api_key")
    assert "llm.providers.claude.api_key" in str(exc.value)
    assert str(value) not in str(exc.value) or value == ""
    if isinstance(value, str) and value.startswith("op://"):
        assert "paste the key itself" in str(exc.value)


def test_set_api_key_validates_provider_and_value():
    cfg = SecretsConfig()
    store.set_api_key(cfg, "claude", _KEY)
    assert cfg.api_keys == {"claude": _KEY}
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.set_api_key(cfg, "retired", _KEY)
    with pytest.raises(ConfigError):
        store.set_api_key(cfg, "claude", "")
    assert cfg.api_keys == {"claude": _KEY}  # failed sets never mutate


def test_set_api_key_refuses_a_provider_without_an_api_backend():
    cfg = SecretsConfig()
    with pytest.raises(ConfigError, match="codex: API documentation backend not supported yet"):
        store.set_api_key(cfg, "codex", _KEY)
    assert cfg.api_keys == {}  # a refused set never mutates


def test_load_secrets_refuses_a_key_for_a_provider_without_an_api_backend(tmp_path):
    (tmp_path / "secrets.yaml").write_text(f"api_keys:\n  codex: {_KEY}\n")
    with pytest.raises(ConfigError, match="codex: API documentation backend not supported") as exc:
        store.load_secrets(tmp_path)
    assert _KEY not in str(exc.value)


def test_save_secrets_rejects_bad_key_before_writing(tmp_path):
    with pytest.raises(ConfigError):
        store.save_secrets(tmp_path, SecretsConfig(api_keys={"claude": "op://x/y/z"}))
    assert not (tmp_path / "secrets.yaml").exists()
