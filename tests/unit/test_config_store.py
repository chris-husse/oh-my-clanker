import stat as _stat

import pytest

from omc.config import store
from omc.config.schema import GlobalConfig, ProjectConfig, ProviderConfig, SecretsConfig
from omc.errors import ConfigError


def test_unknown_key_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text("schema_version: 1\nbogus: true\n")
    with pytest.raises(ConfigError, match="bogus"):
        store.load_global(tmp_path)


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


def test_notifications_defaults(tmp_path):
    cfg = GlobalConfig()
    assert cfg.notifications.enabled is False
    assert cfg.notifications.backend == "macos"
    store.save_global(tmp_path, cfg)
    loaded = store.load_global(tmp_path)
    assert loaded.notifications.enabled is False
    assert loaded.notifications.backend == "macos"


def test_notifications_missing_key_defaults(tmp_path):
    # configs written before this feature carry no notifications key at all
    (tmp_path / "config.yaml").write_text("schema_version: 1\n")
    loaded = store.load_global(tmp_path)
    assert loaded.notifications.enabled is False
    assert loaded.notifications.backend == "macos"


def test_notifications_round_trip(tmp_path):
    cfg = GlobalConfig()
    cfg.notifications.enabled = True
    cfg.notifications.backend = "file:///tmp/omc-notifications.log"
    store.save_global(tmp_path, cfg)
    loaded = store.load_global(tmp_path)
    assert loaded.notifications.enabled is True
    assert loaded.notifications.backend == "file:///tmp/omc-notifications.log"


def test_set_key_notifications_enabled_coerces_bool():
    cfg = GlobalConfig()
    store.set_key(cfg, "notifications.enabled", "true")
    assert cfg.notifications.enabled is True
    store.set_key(cfg, "notifications.enabled", "false")
    assert cfg.notifications.enabled is False
    with pytest.raises(ConfigError, match="true or false"):
        store.set_key(cfg, "notifications.enabled", "yes")


def test_set_key_notifications_backend_validated():
    cfg = GlobalConfig()
    store.set_key(cfg, "notifications.backend", "file:///var/log/omc.log")
    assert cfg.notifications.backend == "file:///var/log/omc.log"
    store.set_key(cfg, "notifications.backend", "macos")
    assert cfg.notifications.backend == "macos"
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.set_key(cfg, "notifications.backend", "file://relative/path")
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.set_key(cfg, "notifications.backend", "slack")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.bogus", "x")


def test_hydrate_rejects_bad_notification_values(tmp_path):
    (tmp_path / "config.yaml").write_text('schema_version: 1\nnotifications:\n  enabled: "true"\n')
    with pytest.raises(ConfigError, match="notifications.enabled"):
        store.load_global(tmp_path)
    (tmp_path / "config.yaml").write_text("schema_version: 1\nnotifications:\n  backend: slack\n")
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.load_global(tmp_path)


def test_set_key_notifications_rejects_trailing_segments():
    cfg = GlobalConfig()
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.enabled.extra", "true")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.backend.extra", "macos")


# --- split YAML store ---


def test_load_global_missing_returns_none(tmp_path):
    assert store.load_global(tmp_path) is None


def test_global_round_trip_yaml(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.default = "codex"
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-x")
    cfg.notifications.enabled = True
    cfg.notifications.backend = "file:///tmp/omc.log"
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "schema_version" in text and "{" not in text  # YAML block style, not JSON
    loaded = store.load_global(tmp_path)
    assert loaded.llm.default == "codex"
    assert loaded.llm.providers["codex"].model == "gpt-x"
    assert loaded.notifications.enabled is True
    assert loaded.schema_version == 1


def test_global_has_no_worktree_key(tmp_path):
    (tmp_path / "config.yaml").write_text("schema_version: 1\nworktree:\n  base_branch: dev\n")
    with pytest.raises(ConfigError, match="worktree"):
        store.load_global(tmp_path)


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


def test_project_rejects_global_keys(tmp_path):
    (tmp_path / ".omc").mkdir()
    (tmp_path / ".omc" / "config.yaml").write_text("llm:\n  default: claude\n")
    with pytest.raises(ConfigError, match="llm"):
        store.load_project(tmp_path)


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
    assert gcfg.notifications.enabled is True
    assert pcfg.worktree.base_branch == "develop"


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
        "schema_version: 1\nbogus: 1\n",
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
        # a key pasted as a top-level key
        (f"schema_version: 1\n{_KEY}: 1\n", "only schema_version and api_keys are allowed"),
    ],
)
def test_load_secrets_never_echoes_a_hand_edited_key_name(tmp_path, body, match):
    (tmp_path / "secrets.yaml").write_text(body)
    with pytest.raises(ConfigError, match=match) as exc:
        store.load_secrets(tmp_path)
    assert "secrets.yaml" in str(exc.value)
    assert _KEY not in str(exc.value) and _KEY[:12] not in str(exc.value)


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
