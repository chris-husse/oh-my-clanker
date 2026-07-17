"""`omc watch` — keep the primary checkout's base branch and knowledge fresh.

Foreground polling loop (omc never creates daemons/launchd/cron). Each tick:
fetch → ff-sync when safely possible → on new commits refresh the GitNexus
index directly (zero LLM cost) and, only with --enable-documentation, the
LLM-generated wiki. Never destructive: off-branch, dirty, or diverged
checkouts are warned about and left alone.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .config.schema import Config
from .gitnexus import ANALYZE_ARGS, gitnexus_argv, gitnexus_cli
from .mirror import mirror_dir
from .toolctx import ToolContext
from .wtconfig import ensure_wt_config, primary_root, repo_root


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _out(ctx: ToolContext, argv: list[str], cwd: str) -> str:
    cp = ctx.run(argv, cwd=cwd)
    return (cp.stdout or "").strip() if cp.returncode == 0 else ""


def _refresh_index(ctx: ToolContext, cfg: Config, root: str, enable_documentation: bool) -> None:
    _say("→ refreshing GitNexus index (incremental)")
    cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=root)
    if cp.returncode != 0:
        _say(f"✗ analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return
    _say("✓ index refreshed")
    if not enable_documentation:
        return
    name = cfg.llm.default
    pcfg = cfg.llm.providers.get(name)
    wiki_args = ["wiki", "--provider", name]
    if pcfg and pcfg.model:
        wiki_args += ["--model", pcfg.model]
    _say(f"→ regenerating documentation via {name} (LLM-heavy)")
    cp = ctx.run(gitnexus_argv(ctx, *wiki_args), cwd=root)
    if cp.returncode != 0:
        _say(f"✗ wiki failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return
    wiki = Path(root) / ".gitnexus" / "wiki"
    if wiki.is_dir():
        mirror_dir(wiki, Path(root) / ".omc" / "docs" / "gitnexus" / "docs")
        _say("✓ documentation refreshed → .omc/docs/gitnexus/docs")


def _tick(
    ctx: ToolContext,
    cfg: Config,
    root: str,
    *,
    enable_documentation: bool,
    force_refresh: bool,
    last: str | None = None,
) -> str:
    """One tick; returns an outcome token. Repeatable QUIET outcomes (up to
    date, off-branch, dirty, diverged, fetch-fail) narrate only when the
    outcome CHANGED since the last tick — a 30s loop must not spam identical
    lines. Action outcomes (sync, refresh) always narrate."""
    base = cfg.worktree.base_branch

    def quiet(token: str, msg: str) -> str:
        if token != last:
            _say(msg)
        return token

    branch = _out(ctx, [ctx.git_bin, "rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch != base:
        return quiet(
            f"off-branch:{branch}",
            f"· not on {base} (on {branch!r}) — leaving the checkout alone",
        )
    cp = ctx.run([ctx.git_bin, "fetch", "origin", base], cwd=root)
    if cp.returncode != 0:
        return quiet("fetch-failed", f"✗ fetch failed: {(cp.stderr or '').strip()[:200]}")
    behind = _out(ctx, [ctx.git_bin, "rev-list", "--count", f"HEAD..origin/{base}"], root)
    ahead = _out(ctx, [ctx.git_bin, "rev-list", "--count", f"origin/{base}..HEAD"], root)
    if behind in ("", "0"):
        if force_refresh:
            _say("· up to date")
            # --once is the "refresh now" button: index (and docs, when enabled)
            # run unconditionally, not only when new commits arrived.
            _refresh_index(ctx, cfg, root, enable_documentation)
            return "refreshed"
        return quiet(
            "up-to-date",
            f"· up to date — waiting for changes on origin/{base}",
        )
    if ahead not in ("", "0"):
        return quiet(
            "diverged",
            f"· {base} has diverged from origin/{base} — resolve manually, skipping",
        )
    # -uno: only TRACKED modifications endanger an ff-merge (untracked files —
    # e.g. the wt.toml starter ensure_wt_config just seeded — must not block a
    # sync; a genuinely colliding untracked file makes the merge itself refuse).
    if _out(ctx, [ctx.git_bin, "status", "--porcelain", "-uno"], root):
        return quiet("dirty", "· working tree is dirty — skipping sync")
    old = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
    cp = ctx.run([ctx.git_bin, "merge", "--ff-only", f"origin/{base}"], cwd=root)
    if cp.returncode != 0:
        return quiet("merge-failed", f"✗ ff-merge failed: {(cp.stderr or '').strip()[:200]}")
    new = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
    _say(f"✓ synced {base}: {old}..{new} ({behind} commits)")
    _refresh_index(ctx, cfg, root, enable_documentation)
    return "synced"


def run_watch(
    ctx: ToolContext,
    cfg: Config,
    *,
    interval: int = 30,
    once: bool = False,
    enable_documentation: bool = False,
) -> int:
    root = repo_root(ctx)
    if root is None:
        print("error: omc watch must run inside a git repository", file=sys.stderr)
        return 1
    primary = primary_root(ctx)
    if primary and Path(primary).resolve() != Path(root).resolve():
        print(
            f"error: omc watch runs in the PRIMARY checkout ({primary}), not a worktree — "
            "worktrees refresh via /omc:rebase-main.",
            file=sys.stderr,
        )
        return 1
    if not gitnexus_cli(ctx).is_file():
        print(
            "error: GitNexus is not installed yet — run /omc:index once in a session "
            "first (it installs GitNexus), then start omc watch.",
            file=sys.stderr,
        )
        return 1
    ensure_wt_config(ctx, root)
    _say(
        f"→ watching {root} (base {cfg.worktree.base_branch}, every {interval}s"
        f"{', documentation enabled' if enable_documentation else ''}) — Ctrl-C stops"
    )
    last: str | None = None
    try:
        while True:
            last = _tick(
                ctx,
                cfg,
                root,
                enable_documentation=enable_documentation,
                force_refresh=once,
                last=last,
            )
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        _say("· stopped")
        return 0
