"""Documentation run resolution (spec 2026-10-01 §4): one helper feeds both
wiki call sites. cli = today's argv byte for byte; api = GitNexus's custom
provider with the key ONLY in the child env."""

import pytest

from omc.config.schema import (
    Config,
    DocsConfig,
    LLMConfig,
    ProviderConfig,
    SecretsConfig,
)
from omc.config.store import set_key
from omc.errors import ConfigError
from omc.providers.registry import GITNEXUS_API_KEY_ENV, docs_llm_for, get_provider

KEY = "sk-ant-test-0123456789abcdef"


def _cfg(*, backend="cli", docs_provider="", default="claude", key=KEY, **provider_kwargs):
    return Config(
        llm=LLMConfig(
            default=default,
            docs=DocsConfig(provider=docs_provider, backend=backend),
            providers={default: ProviderConfig(**provider_kwargs)},
        ),
        secrets=SecretsConfig(api_keys={"claude": key} if key else {}),
    )


def test_provider_docs_defaults():
    assert get_provider("claude").docs_model_default() == "sonnet"
    assert get_provider("codex").docs_model_default() == ""


def test_cli_backend_is_byte_identical_to_today_and_ignores_the_key():
    run = docs_llm_for(_cfg(model="claude-fable-5"))  # session model must never leak
    assert run.wiki_args == ("wiki", "--provider", "claude", "--model", "sonnet")
    assert run.extra_env == {} and run.backend == "cli" and run.label == "claude cli (sonnet)"
    assert run.redact(f"x {KEY} y") == f"x {KEY} y"  # no-op on cli: nothing to hide
    run = docs_llm_for(_cfg(docs_model="opus"))
    assert run.wiki_args == ("wiki", "--provider", "claude", "--model", "opus")


def test_cli_backend_codex_without_model_passes_no_model_flag():
    cfg = Config(llm=LLMConfig(default="codex", providers={}))
    run = docs_llm_for(cfg)
    assert run.wiki_args == ("wiki", "--provider", "codex") and run.label == "codex cli"


def test_docs_provider_overrides_default():
    cfg = Config(
        llm=LLMConfig(
            default="codex",
            docs=DocsConfig(provider="claude"),
            providers={"codex": ProviderConfig(), "claude": ProviderConfig(docs_model="opus")},
        )
    )
    assert docs_llm_for(cfg).wiki_args == ("wiki", "--provider", "claude", "--model", "opus")


def test_api_backend_argv_env_label_and_redact():
    run = docs_llm_for(_cfg(backend="api", docs_model="claude-sonnet-5-5"))
    assert run.wiki_args == (
        "wiki",
        "--provider",
        "custom",
        "--base-url",
        "https://api.anthropic.com/v1/",
        "--model",
        "claude-sonnet-5-5",
        "--reasoning-model",
    )
    assert run.extra_env == {GITNEXUS_API_KEY_ENV: KEY}
    assert KEY not in " ".join(run.wiki_args)
    assert run.label == "claude api (claude-sonnet-5-5)"
    assert run.redact(f"LLM API error: {KEY} rejected") == "LLM API error: ****** rejected"
    assert KEY not in repr(run)


@pytest.mark.parametrize("docs_model", ["", "sonnet", "opus"])
def test_api_backend_requires_a_full_model_id(docs_model):
    with pytest.raises(ConfigError, match="run omc configure"):
        docs_llm_for(_cfg(backend="api", docs_model=docs_model))


def test_api_backend_requires_a_stored_key():
    with pytest.raises(ConfigError, match="no key is stored.*run omc configure"):
        docs_llm_for(_cfg(backend="api", docs_model="claude-sonnet-5-5", key=""))


def test_set_key_accepts_docs_model():
    cfg = LLMConfig()
    set_key(cfg, "providers.claude.docs_model", "claude-opus-4-8")
    assert cfg.providers["claude"].docs_model == "claude-opus-4-8"
    with pytest.raises(ConfigError):
        set_key(cfg, "providers.claude.nope", "x")
