from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    model: str = ""  # blank = provider default


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
    schema_version: int = 1
    llm: LLMConfig = field(default_factory=LLMConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
