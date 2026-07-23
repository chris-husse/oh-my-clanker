"""Shared GitNexus CLI location + invocation helpers.

Install/ensure stays in skill prose (gitnexus-ensure); Python only LOCATES the
built CLI and drives the deterministic commands watch/rebase-main need, and
updates an existing managed clone (`omc update`); first install stays in skill
prose.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .toolctx import ToolContext

if TYPE_CHECKING:  # annotation-only; ToolContext stays the subprocess boundary
    import subprocess

# Index-only analyze: no AGENTS.md/CLAUDE.md writes, no agent-skill installs —
# same flags the gitnexus-index skill prescribes.
ANALYZE_ARGS = ("analyze", "--skip-agents-md", "--skip-skills")

# The ONLY source ever updated — mirrors the gitnexus-ensure skill's rule.
GITNEXUS_ORIGIN = "https://github.com/chris-husse/GitNexus.git"


def gitnexus_cli(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"


def gitnexus_argv(ctx: ToolContext, *args: str) -> list[str]:
    return ["node", str(gitnexus_cli(ctx)), *args]


def gitnexus_root(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus"


def redact_userinfo(url: str) -> str:
    # Never echo credentials embedded in a remote URL.
    return re.sub(r"//[^/@]*@", "//[REDACTED]@", url)


_redact_userinfo = redact_userinfo  # back-compat alias


def _run_tool(
    ctx: ToolContext, argv: list[str], *, cwd: str | None = None
) -> subprocess.CompletedProcess[str] | None:
    """subprocess boundary for OPTIONAL tools (node/npm) — a missing binary is
    an expected failure, reported like any other failed step (installer._uv idiom).
    Git stays unwrapped: the repo treats git as guaranteed (watch.py does too).
    """
    try:
        return ctx.run(argv, cwd=cwd)
    except FileNotFoundError:
        return None


def _cli_version(ctx: ToolContext) -> str | None:
    cli = gitnexus_cli(ctx)
    if not cli.is_file():
        return None
    cp = _run_tool(ctx, ["node", str(cli), "--version"])
    if cp is None:
        return None
    return (cp.stdout or "").strip() or None if cp.returncode == 0 else None


def update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int:
    """Deterministic update of the managed GitNexus clone (`omc update`).

    Forces main — the clone is not a dev workspace. First install stays in
    the gitnexus-ensure skill; a missing clone is a skip, not an error.
    """
    root = gitnexus_root(ctx)
    git = ctx.git_bin
    if not (root / ".git").exists():
        print(
            "GitNexus not installed — /omc:index installs it on first use; skipping.",
            file=sys.stderr,
        )
        return 0
    cp = ctx.run([git, "-C", str(root), "remote", "get-url", "origin"])
    origin = (cp.stdout or "").strip()
    if cp.returncode != 0 or origin != approved_origin:
        shown = redact_userinfo(origin) or "<unknown>"
        print(
            f"error: {root} origin is {shown!r}, not the approved GitNexus source — "
            "refusing to update",
            file=sys.stderr,
        )
        return 1
    old = _cli_version(ctx)
    cp = ctx.run([git, "-C", str(root), "fetch", "origin", "--prune"])
    if cp.returncode != 0:
        print(f"error: GitNexus fetch failed: {(cp.stderr or '').strip()[:400]}", file=sys.stderr)
        return 1
    head = ctx.run([git, "-C", str(root), "rev-parse", "HEAD"])
    remote = ctx.run([git, "-C", str(root), "rev-parse", "origin/main"])
    if (
        head.returncode == 0
        and remote.returncode == 0
        and head.stdout.strip() == remote.stdout.strip()
    ):
        print(f"✓ GitNexus up to date{f' ({old})' if old else ''}", file=sys.stderr)
        return 0
    print("→ updating GitNexus…", file=sys.stderr)
    for argv in (
        [git, "-C", str(root), "checkout", "main"],
        [git, "-C", str(root), "merge", "--ff-only", "origin/main"],
    ):
        cp = ctx.run(argv)
        if cp.returncode != 0:
            print(
                f"error: GitNexus {' '.join(argv[3:])} failed: {(cp.stderr or '').strip()[:400]}",
                file=sys.stderr,
            )
            return 1
    # Two-step build; order matters (gitnexus-shared is a plain sibling package
    # compiled by the main build with its own node_modules).
    for argv, cwd in (
        (["npm", "install", "--no-audit", "--no-fund"], root / "gitnexus-shared"),
        (["npm", "ci"], root / "gitnexus"),
        (["npm", "run", "build"], root / "gitnexus"),
    ):
        cp = _run_tool(ctx, argv, cwd=str(cwd))
        if cp is None:
            print(f"error: {argv[0]} not found on PATH", file=sys.stderr)
            return 1
        if cp.returncode != 0:
            print(
                f"error: {' '.join(argv)} in {cwd.name}/ failed:\n"
                f"{(cp.stderr or cp.stdout or '').strip()[:800]}",
                file=sys.stderr,
            )
            return 1
    new = _cli_version(ctx)
    if new is None:
        print(
            "error: GitNexus built but the CLI won't report --version — not claiming success",
            file=sys.stderr,
        )
        return 1
    print(
        f"✓ GitNexus updated{f': {old} → {new}' if old and old != new else f' ({new})'}",
        file=sys.stderr,
    )
    return 0
