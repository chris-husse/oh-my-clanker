"""`omc watch` — keep the primary checkout's base branch and knowledge fresh.

Foreground polling loop (omc never creates daemons/launchd/cron). Each tick:
fetch → ff-sync when safely possible → on new commits refresh the GitNexus
index directly (zero LLM cost) and, only with --enable-documentation, the
LLM-generated wiki. Never destructive: off-branch, dirty, or diverged
checkouts are warned about and left alone.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .agentsmd import chain_healthy, ensure_agents_chain, is_omc_link
from .config.schema import Config
from .errors import OmcError
from .gitnexus import ANALYZE_ARGS, gitnexus_argv, gitnexus_cli
from .mirror import mirror_dir
from .providers.registry import get_provider
from .skills_source import skill_prompt
from .toolctx import ToolContext
from .wtconfig import ensure_wt_config, primary_root, repo_root


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _out(ctx: ToolContext, argv: list[str], cwd: str) -> str:
    cp = ctx.run(argv, cwd=cwd)
    return (cp.stdout or "").strip() if cp.returncode == 0 else ""


def _decode(v: object) -> str:
    return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")  # type: ignore[union-attr]


_HOOK_TIMEOUT = 600  # seconds — a stuck project hook must not wedge the loop


def _write_hook_log(output: str, prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(output)
    return path


def _post_watch_hook(ctx: ToolContext, root: str, outcome: str) -> None:
    """Project extension point: .omc/hooks/post-watch.sh, fired only after
    ACTION ticks (synced/refreshed). Hooks never break work — failures and
    timeouts warn (with the captured log) and the loop continues."""
    hook = Path(root) / ".omc" / "hooks" / "post-watch.sh"
    if not hook.is_file():
        return
    _say("→ running project post-watch hook (.omc/hooks/post-watch.sh)")
    status: str | None = None
    try:
        cp = ctx.run(
            ["bash", str(hook)],
            cwd=root,
            timeout=_HOOK_TIMEOUT,
            extra_env={"OMC_WATCH_OUTCOME": outcome},
        )
        output = (cp.stdout or "") + (cp.stderr or "")
        if cp.returncode != 0:
            status = f"exit {cp.returncode}"
    except subprocess.TimeoutExpired as exc:
        # POSIX quirk: TimeoutExpired carries the partial output as BYTES
        # even in text mode — decode before logging.
        output = _decode(exc.stdout) + _decode(exc.stderr)
        status = "timeout"
    except UnicodeDecodeError as exc:
        output = str(exc)
        status = "undecodable output"
    except OSError as exc:
        output = str(exc)
        status = "failed to start"
    log = _write_hook_log(output, "omc-post-watch-")
    if status is None:
        _say("✓ post-watch hook done")
    else:
        _say(f"✗ post-watch hook failed ({status}) — log: {log}")


_BUILD_TIMEOUT = 1800  # seconds — an LLM build stage must not wedge the loop
_STAGE_RE = re.compile(r"^OMC_STAGE (\{.*\})\s*$", re.MULTILINE)


def _parse_stage(output: str) -> dict | None:
    matches = _STAGE_RE.findall(output)
    if not matches:
        return None
    try:
        # last verdict line wins — transcripts may echo earlier OMC_STAGE lines
        v = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def _auto_build(ctx: ToolContext, cfg: Config, root: str) -> None:
    """--auto-build: run the project's build stage via the default LLM after
    an action tick. Same doctrine as the hook: failures warn, never crash.
    The SKILL.md existence pre-check deliberately mirrors the build skill's
    own step 2 — a cost guard so an unconfigured project never spends an
    LLM call per tick to learn 'nothing to do'."""
    if not (Path(root) / ".omc" / "skills" / "build" / "SKILL.md").is_file():
        _say("· no project build stage configured — skipping auto-build")
        return
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    _say(f"→ running project build stage via {name} (LLM-heavy)")
    status: str | None = None
    try:
        cp = ctx.run(
            provider.headless_argv(
                skill_prompt("build"),
                model=model,
                allowed_tools=["Bash", "Read", "Glob", "Grep"],
            ),
            cwd=root,
            timeout=_BUILD_TIMEOUT,
            extra_env=provider.title_env(),
        )
        output = (cp.stdout or "") + (cp.stderr or "")
        verdict = _parse_stage(output)
        if cp.returncode != 0:
            status = f"exit {cp.returncode}"
        elif verdict is None:
            status = "no verdict"
        elif not verdict.get("passed"):
            status = str(verdict.get("summary") or "stage failed")
    except subprocess.TimeoutExpired as exc:
        output = _decode(exc.stdout) + _decode(exc.stderr)
        status = "timeout"
    except UnicodeDecodeError as exc:
        output = str(exc)
        status = "undecodable output"
    except OSError as exc:
        output = str(exc)
        status = "failed to start"
    log = _write_hook_log(output, "omc-auto-build-")
    if status is None:
        _say("✓ auto-build passed")
    else:
        _say(f"✗ auto-build failed ({status}) — log: {log}")


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


def _chain_tick(ctx: ToolContext, root: str, last: str | None) -> str:
    """REPAIR the AGENTS.md chain; never create it from nothing (that is
    configure/start's job — watch must not mutate repos it merely observes).
    Healthy: silent. Repair: narrates (action outcome). Blocked: warn once
    per state change (quiet-token doctrine, like _tick), never block the loop."""
    try:
        if chain_healthy(root):
            return "chain-ok"
        root_p = Path(root)
        names = ("AGENTS.md", "CLAUDE.md")
        if not any((root_p / n).exists() or (root_p / n).is_symlink() for n in names):
            return "chain-absent"  # never chain-managed — silently leave it alone
        if last == "chain-blocked":
            # Still blocked (foreign root files/symlinks don't vanish between
            # ticks) — re-running ensure would re-narrate the same warning
            # every tick.
            if any(
                ((root_p / n).exists() or (root_p / n).is_symlink()) and not is_omc_link(root_p / n)
                for n in names
            ):
                return "chain-blocked"
        status = ensure_agents_chain(ctx, root)
        return "chain-blocked" if status == "blocked" else "chain-ok"
    except OmcError as e:
        # chain_healthy()/ensure_agents_chain() call distribution_agents_md(),
        # which RAISES OmcError when the installed distribution/AGENTS.md is
        # missing (a broken install). Watch doctrine: a tick failure must warn
        # and skip, never crash the loop — narrate once per state change, same
        # quiet-token convention as everything else here.
        token = "chain-error"
        if token != last:
            _say(f"✗ chain check failed: {e}")
        return token


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
    auto_build: bool = False,
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
    chain_last: str | None = None
    try:
        while True:
            chain_last = _chain_tick(ctx, root, chain_last)
            last = _tick(
                ctx,
                cfg,
                root,
                enable_documentation=enable_documentation,
                force_refresh=once,
                last=last,
            )
            if last in ("synced", "refreshed"):
                _post_watch_hook(ctx, root, last)
                if auto_build:
                    _auto_build(ctx, cfg, root)
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        _say("· stopped")
        return 0
