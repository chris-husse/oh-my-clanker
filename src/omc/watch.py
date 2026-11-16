"""`omc watch` — keep the primary checkout's base branch and knowledge fresh.

Foreground polling loop (omc never creates daemons/launchd/cron). Each tick:
fetch → ff-sync when safely possible → on new commits refresh the GitNexus
index directly (zero LLM cost). Never destructive by default: off-branch,
dirty, or diverged checkouts are warned about and left alone — --rebase is the
explicit opt-in past the dirty/diverged skips (autostash rebase; conflicts
abort and restore); off-branch checkouts are never touched in any mode.

`--reset-gitnexus` force-clears the index and rebuilds it (base branch only);
up-to-date ticks heal a stale verdict (`healed`, no hooks) and never retry the
same failure every tick (`knowledge-stale:<codes>`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from . import stagedef, stageid, stagerun
from .buildprogress import ProgressTracker
from .cli.progress_bar import BarThread
from .config.schema import Config
from .errors import OmcError
from .gitnexus import (
    CheckoutMoved,
    Freshness,
    ResetProgress,
    ensure_gitnexus,
    refresh_knowledge,
    snapshot_freshness,
)
from .probe import require_tools
from .toolctx import ToolContext
from .watchlock import (
    WATCH_BAIL_MSG,
    acquire_busy_narrated,
    acquire_instance,
    alive_lock_path,
    hold_alive,
    release_alive,
    watch_locks,
)
from .wtconfig import current_branch, ensure_wt_config, primary_root, rebase_in_progress, repo_root


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _out(ctx: ToolContext, argv: list[str], cwd: str) -> str:
    cp = ctx.run(argv, cwd=cwd)
    return (cp.stdout or "").strip() if cp.returncode == 0 else ""


def _decode(v: object) -> str:
    return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")  # type: ignore[union-attr]


_HOOK_TIMEOUT = 600  # seconds — a stuck project hook must not wedge the loop


def _make_live_log(prefix: str) -> tuple[object, str]:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".log")
    return os.fdopen(fd, "w", encoding="utf-8"), path


def _post_watch_hook(ctx: ToolContext, root: str, outcome: str) -> None:
    """Project extension point: .omc/hooks/post-watch.sh, fired only after
    ACTION ticks (synced/refreshed). Hooks never break work — failures and
    timeouts warn (with the captured log) and the loop continues."""
    hook = Path(root) / ".omc" / "hooks" / "post-watch.sh"
    if not hook.is_file():
        return
    log, log_path = _make_live_log("omc-post-watch-")
    _say(f"→ running project post-watch hook (.omc/hooks/post-watch.sh) — log: {log_path}")
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
    log.write(output)
    log.close()
    if status is None:
        _say("✓ post-watch hook done")
    else:
        _say(f"✗ post-watch hook failed ({status}) — log: {log_path}")


_CARGO_PROGRESS_ENV = {
    # cargo suppresses its (12/1288) counters when piped; these force them so
    # the progress parsers see real numbers. Harmless for non-cargo projects.
    "CARGO_TERM_PROGRESS_WHEN": "always",
    "CARGO_TERM_PROGRESS_WIDTH": "80",
}


def _auto_build(ctx: ToolContext, root: str) -> None:
    """--auto-build: run the project's `.omc/bin/build` after an action tick,
    through the stage runner in this process (design 6.7) — no LLM. The log is
    announced once the run started, and its lines feed the progress bar. No
    timeout beyond the stage's own limits; Ctrl-C kills the run, then reaches
    run_watch. Watch holds the alive lock throughout, so the run's watch probe
    passes. Failures warn, never crash."""
    if stagedef.hook_state(Path(root), "build") == "absent":
        _say("· no project build stage configured — skipping auto-build")
        return
    _say("→ running project build stage (.omc/bin/build)")
    tracker = ProgressTracker()
    bar = BarThread(tracker)

    def started(answer: stagerun.RunAnswer) -> None:
        _say(f"— log: {answer.log}")
        bar.start()  # after the line: the bar redraws in place on the line below it

    try:
        answer = stagerun.run_foreground(  # in watch's cwd: the primary checkout, `root`
            ctx,
            "build",
            on_line=tracker.feed,
            on_start=started,
            extra_env=_CARGO_PROGRESS_ENV,
        )
    except (OmcError, OSError) as exc:  # OSError: e.g. a stage root it cannot write
        _say(f"✗ auto-build failed ({exc})")
        return
    finally:
        bar.stop()
    if answer.state == "passed":
        _say("✓ auto-build passed")
        return
    why = f"{answer.state}: {answer.reason}" if answer.reason else answer.state
    log = f" — log: {answer.log}" if answer.log else ""
    _say(f"✗ auto-build failed ({why}){log}")


def _refresh_index(
    ctx: ToolContext,
    cfg: Config,
    root: str,
    *,
    reset: bool = False,
    reset_progress: ResetProgress | None = None,
) -> Freshness | CheckoutMoved:
    """Repair the knowledge snapshot — the ONE code path (gitnexus.refresh_knowledge)."""
    try:
        return refresh_knowledge(
            ctx,
            cfg,
            root,
            cfg.worktree.base_branch,
            reset=reset,
            say=_say,
            reset_progress=reset_progress,
        )
    except CheckoutMoved as moved:
        _say(
            f"✗ primary moved to '{moved.branch}' mid-tick — knowledge snapshot NOT updated; "
            f"retrying when it is back on {moved.base}"
        )
        return moved


def _tick(
    ctx: ToolContext,
    cfg: Config,
    root: str,
    *,
    force_refresh: bool,
    last: str | None = None,
    rebase: bool = False,
    reset: bool = False,
    reset_progress: ResetProgress | None = None,
) -> str:
    """One tick; returns an outcome token. Repeatable QUIET outcomes (up to
    date, off-branch, dirty, diverged, fetch-fail, conflicted, rebase-failed,
    autostash-conflict) narrate only when the outcome CHANGED since the last
    tick — a 30s loop must not spam identical lines. Action outcomes (sync,
    refresh) always narrate. With rebase=True the dirty/diverged skips are
    replaced by `git rebase --autostash` (the user's explicit opt-in).

    An up-to-date tick whose freshness verdict is stale repairs it and returns
    the action token `healed` (narrates; fires no hooks — only knowledge
    changed); a repair that leaves the verdict stale returns the QUIET token
    `knowledge-stale:<codes>`, so the same failure is not retried every tick.
    reset=True makes every up-to-date/synced tick a full rebuild point,
    verdict or not."""
    base = cfg.worktree.base_branch

    def quiet(token: str, msg: str) -> str:
        if token != last:
            _say(msg)
        return token

    branch = current_branch(ctx, root) or ""
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
            # --once is the "check now" button: repair whatever is stale (and a
            # pending reset), narrate "current" when nothing is.
            result = _refresh_index(ctx, cfg, root, reset=reset, reset_progress=reset_progress)
            if isinstance(result, CheckoutMoved):
                return f"off-branch:{result.branch}"
            return "refreshed"
        verdict = snapshot_freshness(ctx, Path(root), base, ref="HEAD")
        if verdict.fresh and not reset:
            return quiet("up-to-date", f"· up to date — waiting for changes on origin/{base}")
        codes = ",".join(sorted(verdict.codes()))
        if not reset and last == f"knowledge-stale:{codes}":
            return last  # same failure as last tick: no retry hammer, no extra line
        _say("· up to date")
        after = _refresh_index(ctx, cfg, root, reset=reset, reset_progress=reset_progress)
        if isinstance(after, CheckoutMoved):
            return f"off-branch:{after.branch}"
        if after.fresh:
            return "healed"
        return "knowledge-stale:" + ",".join(sorted(after.codes()))
    if rebase:
        if _out(ctx, [ctx.git_bin, "ls-files", "-u"], root):
            return quiet("conflicted", "· unmerged paths in the tree — resolve them, skipping sync")
        old = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
        rebase_argv = [ctx.git_bin, "rebase", "--autostash", f"origin/{base}"]
        cp = ctx.run(rebase_argv, cwd=root)
        if (
            cp.returncode != 0
            and not rebase_in_progress(ctx, root)
            and stageid.yield_to_committed_id(ctx, root, base, cp.stderr or "")
        ):
            cp = ctx.run(rebase_argv, cwd=root)
        if cp.returncode != 0:
            why = (cp.stderr or "").strip()[:200]
            if rebase_in_progress(ctx, root):
                # A rebase actually started then hit a conflict: abort restores
                # HEAD and the autostash.
                abort = ctx.run([ctx.git_bin, "rebase", "--abort"], cwd=root)
                detail = (
                    "aborted, checkout restored"
                    if abort.returncode == 0
                    else f"abort ALSO failed: {(abort.stderr or '').strip()[:200]}"
                )
            else:
                # Refused before starting (canonical: an untracked local file the
                # replay would overwrite) — HEAD never moved, nothing to abort.
                detail = "refused before starting, checkout untouched"
            return quiet(
                "rebase-failed",
                f"✗ rebase onto origin/{base} failed — {detail}; resolve manually"
                + (f" ({why})" if why else ""),
            )
        # A conflicting autostash pop still exits 0: git rebases HEAD, leaves the
        # tree with conflict markers AND keeps the changes in stash@{0}. The exit
        # code cannot distinguish this from success — unmerged paths can.
        if _out(ctx, [ctx.git_bin, "ls-files", "-u"], root):
            return quiet(
                "autostash-conflict",
                "✗ rebased, but restoring your uncommitted changes conflicted — "
                "resolve the markers; your changes are also safe in git stash",
            )
        new = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
        _say(f"✓ rebased {base}: {old}..{new} ({behind} commits)")
        _refresh_index(ctx, cfg, root, reset=reset, reset_progress=reset_progress)
        return "synced"
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
    if cp.returncode != 0 and stageid.yield_to_committed_id(ctx, root, base, cp.stderr or ""):
        cp = ctx.run([ctx.git_bin, "merge", "--ff-only", f"origin/{base}"], cwd=root)
    if cp.returncode != 0:
        return quiet("merge-failed", f"✗ ff-merge failed: {(cp.stderr or '').strip()[:200]}")
    new = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
    _say(f"✓ synced {base}: {old}..{new} ({behind} commits)")
    _refresh_index(ctx, cfg, root, reset=reset, reset_progress=reset_progress)
    return "synced"


def run_watch(
    ctx: ToolContext,
    cfg: Config,
    *,
    interval: int = 30,
    once: bool = False,
    auto_build: bool = False,
    rebase: bool = False,
    clear_mutex: bool = False,
    reset_gitnexus: bool = False,
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
    # Prerequisites fail fast BEFORE the mutex (a miss must leave no lock);
    # the warn-and-skip doctrine applies only to ticks, never to boot checks.
    require_tools(ctx, cfg)  # git/wt/provider — raises OmcError on a miss
    base = cfg.worktree.base_branch
    if reset_gitnexus:
        # A rebuild anywhere else would stamp the store with THAT branch and
        # recreate the inversion — refuse before any node call and any lock.
        branch = current_branch(ctx, root) or ""
        if branch != base:
            print(
                f"error: --reset-gitnexus requires the primary checkout to be on {base} "
                f"(currently {branch})",
                file=sys.stderr,
            )
            return 1
    rc = ensure_gitnexus(ctx)
    if rc:
        return rc
    ensure_wt_config(ctx, root)
    locks = watch_locks(ctx, cwd=root)
    if locks is None:
        # Unreachable inside a repo in practice; doctrine says warn, never wedge.
        _say("✗ could not resolve the watch lock dir — running without the mutex")
        instance = busy = None
        alive_fd = None
    else:
        instance, busy = locks
        if not acquire_instance(instance, clear=clear_mutex):
            print(WATCH_BAIL_MSG, file=sys.stderr)
            return 1
        # Held for the whole lifetime: what a stage run probes (design 6.4).
        alive_path = alive_lock_path(ctx, cwd=root)
        alive_fd = None if alive_path is None else hold_alive(alive_path, clear=clear_mutex)
    _say(f"→ watching {root} (base {base}, every {interval}s) — Ctrl-C stops")
    last: str | None = None
    reset_pending = reset_gitnexus
    reset_note: str | None = None
    try:
        while True:
            # Busy lock held for the WHOLE busy portion (tick + hooks): free ⇔ idle.
            with acquire_busy_narrated(busy, _say) if busy is not None else nullcontext():
                reset_progress = ResetProgress()
                last = _tick(
                    ctx,
                    cfg,
                    root,
                    force_refresh=once,
                    last=last,
                    rebase=rebase,
                    reset=reset_pending,
                    reset_progress=reset_progress,
                )
                # Sync and reset are independent: checkout movement can abort
                # repair either before or after the destructive reset starts.
                if reset_pending and reset_progress.attempted:
                    reset_pending = False  # applied — or failed honestly; never re-run blindly
                elif reset_pending and once:
                    _say(
                        "· reset not applied — this tick could not refresh; "
                        "rerun on a clean base checkout"
                    )
                elif reset_pending and last != reset_note:
                    _say("· reset pending — waiting for a tick that can refresh")
                    reset_note = last
                if last in ("synced", "refreshed"):
                    _post_watch_hook(ctx, root, last)
                    if auto_build:
                        _auto_build(ctx, root)
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        _say("· stopped")
        return 0
    finally:
        if alive_fd is not None:
            release_alive(alive_fd)
        if instance is not None and instance.is_locked:
            instance.release()
