import pytest

from omc.config import store
from omc.config.schema import Config, ProviderConfig
from omc.errors import ConfigError


def test_load_missing_returns_none(tmp_path):
    assert store.load(tmp_path) is None


def test_round_trip(tmp_path):
    cfg = Config()
    cfg.llm.default = "codex"
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-x")
    cfg.worktree.branch_prefix = "wip/"
    store.save(tmp_path, cfg)
    loaded = store.load(tmp_path)
    assert loaded.llm.default == "codex"
    assert loaded.llm.providers["codex"].model == "gpt-x"
    assert loaded.worktree.branch_prefix == "wip/"
    assert loaded.schema_version == 1


def test_unknown_key_rejected(tmp_path):
    (tmp_path / "config.json").write_text('{"schema_version": 1, "bogus": true}')
    with pytest.raises(ConfigError, match="bogus"):
        store.load(tmp_path)


def test_bad_json_rejected(tmp_path):
    (tmp_path / "config.json").write_text("{nope")
    with pytest.raises(ConfigError):
        store.load(tmp_path)


def test_set_key():
    cfg = Config()
    store.set_key(cfg, "llm.default", "opencode")
    store.set_key(cfg, "worktree.base_branch", "master")
    assert cfg.llm.default == "opencode"
    assert cfg.worktree.base_branch == "master"


def test_set_key_provider_model():
    cfg = Config()
    store.set_key(cfg, "llm.providers.claude.model", "claude-fable-5")
    assert cfg.llm.providers["claude"].model == "claude-fable-5"


def test_set_key_rejects_unknown_and_sections():
    cfg = Config()
    with pytest.raises(ConfigError):
        store.set_key(cfg, "llm.bogus", "x")
    with pytest.raises(ConfigError):
        store.set_key(cfg, "llm", "x")
    with pytest.raises(ConfigError):
        store.set_key(cfg, "schema_version", "9")


def test_section_must_be_object(tmp_path):
    (tmp_path / "config.json").write_text('{"schema_version": 1, "llm": "oops"}')
    with pytest.raises(ConfigError, match="llm"):
        store.load(tmp_path)


def test_provider_entry_must_be_object(tmp_path):
    (tmp_path / "config.json").write_text('{"llm": {"providers": {"claude": 5}}}')
    with pytest.raises(ConfigError, match="claude"):
        store.load(tmp_path)


def test_notifications_defaults(tmp_path):
    cfg = Config()
    assert cfg.notifications.enabled is False
    assert cfg.notifications.backend == "macos"
    store.save(tmp_path, cfg)
    loaded = store.load(tmp_path)
    assert loaded.notifications.enabled is False
    assert loaded.notifications.backend == "macos"


def test_notifications_missing_key_defaults(tmp_path):
    # configs written before this feature carry no notifications key at all
    (tmp_path / "config.json").write_text('{"schema_version": 1}')
    loaded = store.load(tmp_path)
    assert loaded.notifications.enabled is False
    assert loaded.notifications.backend == "macos"


def test_notifications_round_trip(tmp_path):
    cfg = Config()
    cfg.notifications.enabled = True
    cfg.notifications.backend = "file:///tmp/omc-notifications.log"
    store.save(tmp_path, cfg)
    loaded = store.load(tmp_path)
    assert loaded.notifications.enabled is True
    assert loaded.notifications.backend == "file:///tmp/omc-notifications.log"


def test_set_key_notifications_enabled_coerces_bool():
    cfg = Config()
    store.set_key(cfg, "notifications.enabled", "true")
    assert cfg.notifications.enabled is True
    store.set_key(cfg, "notifications.enabled", "false")
    assert cfg.notifications.enabled is False
    with pytest.raises(ConfigError, match="true or false"):
        store.set_key(cfg, "notifications.enabled", "yes")


def test_set_key_notifications_backend_validated():
    cfg = Config()
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
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "notifications": {"enabled": "true"}}'
    )
    with pytest.raises(ConfigError, match="notifications.enabled"):
        store.load(tmp_path)
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "notifications": {"backend": "slack"}}'
    )
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.load(tmp_path)


def test_set_key_notifications_rejects_trailing_segments():
    cfg = Config()
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.enabled.extra", "true")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.backend.extra", "macos")
