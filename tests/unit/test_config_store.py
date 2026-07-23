import pytest

from omc.config import store
from omc.config.schema import GlobalConfig, ProjectConfig, ProviderConfig
from omc.errors import ConfigError


def test_unknown_key_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text("schema_version: 1\nbogus: true\n")
    with pytest.raises(ConfigError, match="bogus"):
        store.load_global(tmp_path)


def test_set_key_provider_model():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.providers.claude.model", "claude-fable-5")
    assert cfg.llm.providers["claude"].model == "claude-fable-5"


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
    store.set_key(gcfg, "llm.default", "opencode")
    assert gcfg.llm.default == "opencode"
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
