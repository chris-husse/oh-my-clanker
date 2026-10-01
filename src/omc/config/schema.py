from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    model: str = ""  # blank = provider default
    # Model for documentation/wiki generation (bulk grounded summarization).
    # Blank = the provider's docs default — the standard-coding-tier floor —
    # NEVER the session model above (a thinking-heavy session model makes
    # hours-long silent wiki runs; see 2026-07-23 docs-model spec).
    docs_model: str = ""


@dataclass
class DocsConfig:
    """How documentation (GitNexus wiki) is generated — spec 2026-10-01 §2.1.
    provider: which configured provider documents; blank = llm.default.
    backend: "cli" (the provider CLI, today's path) or "api" (the provider's
    HTTP API with the key stored in secrets.yaml)."""

    provider: str = ""
    backend: str = "cli"


@dataclass
class LLMConfig:
    default: str = "claude"
    docs: DocsConfig = field(default_factory=DocsConfig)
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {"claude": ProviderConfig()}
    )


@dataclass
class WorktreeConfig:
    branch_prefix: str = "feature/"
    base_branch: str = "main"


@dataclass
class NotificationsConfig:
    enabled: bool = False  # opt-in
    backend: str = "macos"  # "macos" | "file://<absolute path>"


@dataclass
class SecretsConfig:
    """Persisted at <home>/secrets.yaml, mode 0600 — API keys per provider
    (spec 2026-10-01 §2.2). repr=False: a traceback, print(cfg) or a pytest
    assertion diff must never show a key."""

    schema_version: int = 1
    api_keys: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass
class Config:
    """Runtime composite of GlobalConfig + ProjectConfig; also the hydration
    shape of the legacy combined ~/.omc/config.json. Never persisted as one
    file anymore."""

    schema_version: int = 1
    llm: LLMConfig = field(default_factory=LLMConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    # Composed from <home>/secrets.yaml by resolve.load_effective; never persisted
    # as part of this composite. repr=False: see SecretsConfig.
    secrets: SecretsConfig = field(default_factory=SecretsConfig, repr=False)


@dataclass
class GlobalConfig:
    """Persisted at ~/.omc/config.yaml — personal settings."""

    schema_version: int = 1
    llm: LLMConfig = field(default_factory=LLMConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


@dataclass
class ProjectConfig:
    """Persisted at <repo>/.omc/config.yaml (committed) — project settings."""

    schema_version: int = 1
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
