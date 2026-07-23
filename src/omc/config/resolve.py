"""Compose the persisted config files into the runtime view consumers use.

Global (~/.omc/config.yaml) is required by gated commands; the project file
(<repo>/.omc/config.yaml) is optional — absent file or no repo means
dataclass defaults, so un-integrated repos keep working.
"""

from __future__ import annotations

from pathlib import Path

from ..toolctx import ToolContext
from ..wtconfig import repo_root
from . import store
from .schema import Config, ProjectConfig


def project_config(ctx: ToolContext) -> ProjectConfig:
    root = repo_root(ctx)
    if root is None:
        return ProjectConfig()
    return store.load_project(Path(root)) or ProjectConfig()


def load_effective(ctx: ToolContext) -> Config | None:
    gcfg = store.load_global(ctx.home)
    if gcfg is None:
        return None
    return Config(
        llm=gcfg.llm,
        notifications=gcfg.notifications,
        worktree=project_config(ctx).worktree,
    )
