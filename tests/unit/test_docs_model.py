"""Docs-model resolution: wiki runs use the standard-coding-tier floor,
never the session model (spec 2026-07-23-per-user-docs-model-config)."""

import pytest

from omc.config.schema import GlobalConfig, LLMConfig, ProviderConfig
from omc.config.store import set_key
from omc.errors import ConfigError
from omc.providers.registry import docs_model_for, get_provider


def _cfg(**provider_kwargs):
    return GlobalConfig(
        llm=LLMConfig(default="claude", providers={"claude": ProviderConfig(**provider_kwargs)})
    )


def test_provider_docs_defaults():
    assert get_provider("claude").docs_model_default() == "claude-sonnet-5"
    # codex/opencode ids are deliberately free-text -> CLI default coding model
    assert get_provider("codex").docs_model_default() == ""
    assert get_provider("opencode").docs_model_default() == ""


def test_docs_model_for_falls_back_to_provider_default():
    assert docs_model_for(_cfg(), "claude") == "claude-sonnet-5"
    # the SESSION model must never leak into docs resolution
    assert docs_model_for(_cfg(model="claude-fable-5"), "claude") == "claude-sonnet-5"


def test_docs_model_for_configured_value_wins():
    assert docs_model_for(_cfg(docs_model="claude-opus-4-8"), "claude") == "claude-opus-4-8"


def test_docs_model_for_unknown_provider_entry():
    cfg = GlobalConfig(llm=LLMConfig(default="codex", providers={}))
    assert docs_model_for(cfg, "codex") == ""


def test_set_key_accepts_docs_model():
    cfg = LLMConfig()
    set_key(cfg, "providers.claude.docs_model", "claude-opus-4-8")
    assert cfg.providers["claude"].docs_model == "claude-opus-4-8"
    set_key(cfg, "providers.codex.model", "o-something")  # existing leaf still works
    assert cfg.providers["codex"].model == "o-something"
    with pytest.raises(ConfigError):
        set_key(cfg, "providers.claude.nope", "x")
