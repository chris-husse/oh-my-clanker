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
class LLMConfig:
    default: str = "claude"
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
class Config:
    """Runtime composite of GlobalConfig + ProjectConfig; also the hydration
    shape of the legacy combined ~/.omc/config.json. Never persisted as one
    file anymore."""

    schema_version: int = 1
    llm: LLMConfig = field(default_factory=LLMConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


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
