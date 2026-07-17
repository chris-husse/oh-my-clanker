from __future__ import annotations

from .errors import OmcError
from .toolctx import ToolContext


def run_configure(ctx: ToolContext, *, defaults: bool, sets: list[str]) -> int:
    raise OmcError("configure is not implemented yet (installer task pending)")
