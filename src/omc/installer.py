from __future__ import annotations

from .errors import OmcError
from .toolctx import ToolContext


def run_install(ctx: ToolContext, path: str) -> int:
    raise OmcError("install is not implemented yet")


def run_update(ctx: ToolContext) -> int:
    raise OmcError("update is not implemented yet")


def run_uninstall(ctx: ToolContext) -> int:
    raise OmcError("uninstall is not implemented yet")
