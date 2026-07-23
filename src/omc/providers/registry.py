from __future__ import annotations

from ..errors import OmcError
from .base import Provider
from .claude import ClaudeProvider
from .codex import CodexProvider
from .opencode import OpencodeProvider

_PROVIDERS: dict[str, Provider] = {
    p.name: p for p in (ClaudeProvider(), CodexProvider(), OpencodeProvider())
}


def provider_names() -> list[str]:
    return list(_PROVIDERS)


def get_provider(name: str) -> Provider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise OmcError(f"unknown provider {name!r}; known: {', '.join(_PROVIDERS)}") from None


def docs_model_for(cfg, name: str) -> str:
    """Model for documentation/wiki runs: the user's docs_model, else the
    provider's docs default (standard-coding-tier floor). The session model
    (ProviderConfig.model) is deliberately never consulted here."""
    pcfg = cfg.llm.providers.get(name)
    return (pcfg.docs_model if pcfg else "") or get_provider(name).docs_model_default()
