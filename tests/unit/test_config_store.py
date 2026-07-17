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
