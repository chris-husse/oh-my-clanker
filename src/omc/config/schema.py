from dataclasses import dataclass, field


@dataclass
class TaskModelsConfig:
    design: str = field(
        default="", metadata={"label": "Design", "help": "Model for design agents."}
    )
    plan: str = field(default="", metadata={"label": "Plan", "help": "Model for planning agents."})
    review: str = field(
        default="", metadata={"label": "Review", "help": "Model for review agents."}
    )
    simple: str = field(
        default="",
        metadata={
            "label": "Simple Complexity Task",
            "help": "Model for simple implementation tasks.",
        },
    )
    medium: str = field(
        default="",
        metadata={
            "label": "Medium Complexity Task",
            "help": "Model for medium implementation tasks.",
        },
    )
    high: str = field(
        default="",
        metadata={"label": "High Complexity Task", "help": "Model for high implementation tasks."},
    )


@dataclass
class ProviderConfig:
    model: str = field(
        default="",
        metadata={"label": "Orchestrator model", "help": "Blank uses the provider default."},
    )
    notifications: bool = field(
        default=True,
        metadata={
            "label": "Native notifications",
            "help": "Use this provider's native session alerts.",
        },
    )
    tasks: TaskModelsConfig = field(
        default_factory=TaskModelsConfig,
        metadata={
            "label": "Task models",
            "help": "Models for design, planning, review, and implementation tasks.",
        },
    )


@dataclass
class LLMConfig:
    default: str = field(
        default="claude",
        metadata={"label": "Default provider", "help": "Provider for new sessions."},
    )
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {"claude": ProviderConfig()},
        metadata={"label": "Providers", "help": "Configured LLM providers."},
    )
    # Spec 2026-11-12: off (the default) lets the lifecycle conductors continue
    # design → implement → audit → finish → push without a typed word per stage.
    provider_selection_per_step: bool = field(
        default=False,
        metadata={
            "label": "Provider Selection Per Step",
            "help": "Stop after each stage to offer another provider; "
            "off = continue automatically through to the push.",
        },
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
class Config:
    """Runtime composite of GlobalConfig + ProjectConfig; also the hydration
    shape of the legacy combined ~/.omc/config.json. Never persisted as one
    file anymore."""

    schema_version: int = 1
    llm: LLMConfig = field(
        default_factory=LLMConfig,
        metadata={"label": "LLM", "help": "Session and task models."},
    )
    worktree: WorktreeConfig = field(
        default_factory=WorktreeConfig,
        metadata={"label": "Worktree", "help": "Branch naming and base branch."},
    )


@dataclass
class GlobalConfig:
    """Persisted at ~/.omc/config.yaml — personal settings."""

    schema_version: int = 1
    llm: LLMConfig = field(
        default_factory=LLMConfig,
        metadata={"label": "LLM", "help": "Session and task models."},
    )


@dataclass
class ProjectConfig:
    """Persisted at <repo>/.omc/config.yaml (committed) — project settings."""

    schema_version: int = 1
    worktree: WorktreeConfig = field(
        default_factory=WorktreeConfig,
        metadata={"label": "Worktree", "help": "Branch naming and base branch."},
    )
