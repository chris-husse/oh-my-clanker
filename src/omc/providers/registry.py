from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ConfigError, OmcError
from .base import Provider
from .claude import ClaudeProvider
from .codex import CodexProvider

_PROVIDERS: dict[str, Provider] = {p.name: p for p in (ClaudeProvider(), CodexProvider())}


def provider_names() -> list[str]:
    return list(_PROVIDERS)


def get_provider(name: str) -> Provider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise OmcError(f"unknown provider {name!r}; known: {', '.join(_PROVIDERS)}") from None


# The env var GitNexus's llm-client reads first (ahead of OPENAI_API_KEY and its
# saved config). Env, never --api-key: the flag is visible in `ps` for the whole
# run and GitNexus persists it to ~/.gitnexus/config.json; the env key is not.
GITNEXUS_API_KEY_ENV = "GITNEXUS_API_KEY"
_MASK = "******"


@dataclass(frozen=True)
class DocsRun:
    """One resolved documentation run (spec 2026-10-01 §4), consumed by both
    wiki call sites so they cannot drift."""

    provider: str
    backend: str
    model: str
    wiki_args: tuple[str, ...]
    extra_env: dict[str, str] = field(repr=False)
    label: str = ""

    def redact(self, text: str) -> str:
        """Exact-key replacement; no-op on cli. Callers redact BEFORE truncating."""
        key = self.extra_env.get(GITNEXUS_API_KEY_ENV)
        return text.replace(key, _MASK) if key else text


def docs_llm_for(cfg) -> DocsRun:
    """Provider = llm.docs.provider or llm.default. Model = that provider's
    docs_model or its docs default (standard-coding-tier floor); the SESSION
    model is deliberately never consulted. cli → today's argv byte for byte.
    api → GitNexus's `custom` provider against the adapter's base URL with the
    stored key in the child env; the model must already be a full id (configure
    writes it), so this does no alias mapping."""
    name = cfg.llm.docs.provider or cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = (pcfg.docs_model if pcfg else "") or provider.docs_model_default()
    if cfg.llm.docs.backend != "api":
        args = ("wiki", "--provider", name, *(("--model", model) if model else ()))
        label = f"{name} cli ({model})" if model else f"{name} cli"
        return DocsRun(name, "cli", model, args, {}, label)
    key = cfg.secrets.api_keys.get(name, "")
    if not key:
        raise ConfigError(
            f"api documentation backend configured for {name} but no key is stored — "
            "run omc configure"
        )
    # `not api_base_url()` is belt-and-braces: validate_llm already refuses
    # backend=api for such a provider on load, set and save.
    if not model or provider.api_model_family(model) or not provider.api_base_url():
        raise ConfigError(
            f"api documentation backend needs a full model id for {name} (got {model!r}) — "
            "run omc configure"
        )
    # Live-verified 2026-10-01: Anthropic's OpenAI-compatible endpoint returns 400
    # "`temperature` is deprecated for this model" for the 5.x models. GitNexus sends
    # `temperature: 0` unless `--reasoning-model` is given; with it, `temperature` is
    # omitted (`max_completion_tokens` is already used). GitNexus persists
    # `isReasoningModel: true` in ~/.gitnexus/config.json, which is harmless.
    args = (
        "wiki",
        "--provider",
        "custom",
        "--base-url",
        provider.api_base_url(),
        "--model",
        model,
        "--reasoning-model",
    )
    return DocsRun(name, "api", model, args, {GITNEXUS_API_KEY_ENV: key}, f"{name} api ({model})")
