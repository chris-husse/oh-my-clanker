"""Shared GitNexus CLI location + invocation helpers.

Install/ensure stays in skill prose (gitnexus-ensure); Python only LOCATES the
built CLI and drives the deterministic commands watch/rebase-main need.
"""

from __future__ import annotations

from pathlib import Path

from .toolctx import ToolContext

# Index-only analyze: no AGENTS.md/CLAUDE.md writes, no agent-skill installs —
# same flags the gitnexus-index skill prescribes.
ANALYZE_ARGS = ("analyze", "--skip-agents-md", "--skip-skills")


def gitnexus_cli(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"


def gitnexus_argv(ctx: ToolContext, *args: str) -> list[str]:
    return ["node", str(gitnexus_cli(ctx)), *args]
