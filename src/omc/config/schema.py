from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    model: str = field(
        default="", metadata={"label": "Session model", "help": "Blank uses the provider default."}
    )
    notifications: bool = field(
        default=True,
        metadata={
            "label": "Native notifications",
            "help": "Use this provider's native session alerts.",
        },
    )
    # Model for documentation/wiki generation (bulk grounded summarization).
    # Blank = the provider's docs default — the standard-coding-tier floor —
    # NEVER the session model above (a thinking-heavy session model makes
    # hours-long silent wiki runs; see 2026-07-23 docs-model spec).
    docs_model: str = field(
        default="",
        metadata={
            "label": "Documentation model",
            "help": "Blank uses the provider's documentation default.",
        },
    )


@dataclass
class DocsConfig:
    """How documentation (GitNexus wiki) is generated — spec 2026-10-01 §2.1.
    provider: which configured provider documents; blank = llm.default.
    backend: "cli" (the provider CLI, today's path) or "api" (the provider's
    HTTP API with the key stored in secrets.yaml)."""

    provider: str = field(
        default="",
        metadata={"label": "Documentation provider", "help": "Blank follows the default provider."},
    )
    backend: str = field(
        default="cli",
        metadata={"label": "Documentation backend", "help": "Use the provider CLI or its API."},
    )


@dataclass
class LLMConfig:
    default: str = field(
        default="claude",
        metadata={"label": "Default provider", "help": "Provider for new sessions."},
    )
    docs: DocsConfig = field(
        default_factory=DocsConfig,
        metadata={"label": "Documentation", "help": "Provider and backend for wiki generation."},
    )
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {"claude": ProviderConfig()},
        metadata={"label": "Providers", "help": "Configured LLM providers."},
    )


@dataclass
class WorktreeConfig:
    branch_prefix: str = field(
        default="feature/",
        metadata={
            "label": "Branch prefix",
            "help": "Prefix for new worktree branches; blank uses none.",
        },
    )
    base_branch: str = field(
        default="main", metadata={"label": "Base branch", "help": "Branch new work starts from."}
    )


@dataclass
class SecretsConfig:
    """Persisted at <home>/secrets.yaml, mode 0600 — API keys per provider
    (spec 2026-10-01 §2.2). repr=False: a traceback, print(cfg) or a pytest
    assertion diff must never show a key."""

    schema_version: int = 1
    api_keys: dict[str, str] = field(
        default_factory=dict,
        repr=False,
        metadata={"label": "API key", "help": "Stored in secrets.yaml for API documentation."},
    )


@dataclass
class Config:
    """Runtime composite of GlobalConfig + ProjectConfig; also the hydration
    shape of the legacy combined ~/.omc/config.json. Never persisted as one
    file anymore."""

    schema_version: int = 1
    llm: LLMConfig = field(
        default_factory=LLMConfig,
        metadata={"label": "LLM", "help": "Session and documentation models."},
    )
    worktree: WorktreeConfig = field(
        default_factory=WorktreeConfig,
        metadata={"label": "Worktree", "help": "Branch naming and base branch."},
    )
    # Composed from <home>/secrets.yaml by resolve.load_effective; never persisted
    # as part of this composite. repr=False: see SecretsConfig.
    secrets: SecretsConfig = field(
        default_factory=SecretsConfig,
        repr=False,
        metadata={"label": "Secrets", "help": "Provider API keys."},
    )


@dataclass
class GlobalConfig:
    """Persisted at ~/.omc/config.yaml — personal settings."""

    schema_version: int = 1
    llm: LLMConfig = field(
        default_factory=LLMConfig,
        metadata={"label": "LLM", "help": "Session and documentation models."},
    )


@dataclass
class ProjectConfig:
    """Persisted at <repo>/.omc/config.yaml (committed) — project settings."""

    schema_version: int = 1
    worktree: WorktreeConfig = field(
        default_factory=WorktreeConfig,
        metadata={"label": "Worktree", "help": "Branch naming and base branch."},
    )
