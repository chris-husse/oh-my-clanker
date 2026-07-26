"""Shared GitNexus CLI location + invocation helpers.

Python owns install/heal (`ensure_gitnexus`) and install-or-update of the
managed clone (`update_gitnexus`), plus locating the built CLI and driving the
deterministic commands watch/rebase-main need.
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


def _clone_if_missing(ctx: ToolContext, root: Path, approved_origin: str) -> int:
    """Ensure <root> is a clone of the approved origin. Clone when absent;
    refuse (never re-point) when an existing clone has a different origin."""
    git = ctx.git_bin
    if not (root / ".git").exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        cp = ctx.run([git, "clone", approved_origin, str(root)])
        if cp.returncode != 0:
            print(
                f"error: GitNexus clone failed: {(cp.stderr or '').strip()[:400]}",
                file=sys.stderr,
            )
            return 1
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
    return 0


def _build(ctx: ToolContext, root: Path) -> int:
    """Two-step npm build; order matters (gitnexus-shared is a plain sibling
    package compiled by the main build with its own node_modules)."""
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
    return 0


def ensure_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int:
    """Install/heal the managed GitNexus clone. Silent no-op when the CLI is
    already healthy (the common path start/watch hit every run). Does NOT
    fetch/ff main — updating a healthy install is `omc update`'s job."""
    if _cli_version(ctx) is not None:
        return 0  # healthy: silent no-op
    root = gitnexus_root(ctx)
    rc = _clone_if_missing(ctx, root, approved_origin)
    if rc:
        return rc
    rc = _build(ctx, root)
    if rc:
        return rc
    ver = _cli_version(ctx)
    if ver is None:
        print(
            "error: GitNexus built but the CLI won't report --version — not claiming success",
            file=sys.stderr,
        )
        return 1
    print(f"✓ GitNexus installed ({ver})", file=sys.stderr)
    return 0


def update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int:
    """Deterministic install-or-update of the managed GitNexus clone
    (`omc update`). Installs when missing, else forces main and rebuilds."""
    root = gitnexus_root(ctx)
    # A fresh install must always build: an origin that carries a prebuilt
    # tree would otherwise land at origin/main with a working CLI and falsely
    # short-circuit as "up to date" before the first build ever runs.
    freshly_cloned = not (root / ".git").exists()
    rc = _clone_if_missing(ctx, root, approved_origin)
    if rc:
        return rc
    git = ctx.git_bin
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
        and old is not None
        and not freshly_cloned
    ):
        print(f"✓ GitNexus up to date{f' ({old})' if old else ''}", file=sys.stderr)
        return 0
    verb = "installing" if freshly_cloned else "updating"
    print(f"→ {verb} GitNexus…", file=sys.stderr)
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
    rc = _build(ctx, root)
    if rc:
        return rc
    new = _cli_version(ctx)
    if new is None:
        print(
            "error: GitNexus built but the CLI won't report --version — not claiming success",
            file=sys.stderr,
        )
        return 1
    if freshly_cloned:
        print(f"✓ GitNexus installed ({new})", file=sys.stderr)
    else:
        print(
            f"✓ GitNexus updated{f': {old} → {new}' if old and old != new else f' ({new})'}",
            file=sys.stderr,
        )
    return 0
