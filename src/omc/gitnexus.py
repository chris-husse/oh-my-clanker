"""Shared GitNexus CLI location + invocation helpers.

Python owns install/heal (`ensure_gitnexus`) and install-or-update of the
managed clone (`update_gitnexus`), plus locating the built CLI and driving the
deterministic commands watch/rebase-main need.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import OmcError
from .mirror import DOCS_MIRROR_REL, clear_docs_mirror, mirror_dir
from .toolctx import ToolContext, gitnexus_env
from .wikirun import (
    _WIKI_POLL_SECONDS,
    _WIKI_STALL_SECONDS,
    GitNexusProgress,
    PageCountTracker,
    wiki_failure_excerpt,
)
from .wtconfig import current_branch

if TYPE_CHECKING:  # annotation-only; ToolContext stays the subprocess boundary
    import subprocess
    from collections.abc import Callable

    from .config.schema import Config, GlobalConfig

# The ONLY source ever updated — mirrors the gitnexus-ensure skill's rule.
GITNEXUS_ORIGIN = "https://github.com/chris-husse/GitNexus.git"
_CXX_STD = "-std=c++20"  # see _native_build_env


def gitnexus_cli(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"


def gitnexus_argv(ctx: ToolContext, *args: str) -> list[str]:
    return ["node", str(gitnexus_cli(ctx)), *args]


def analyze_argv(ctx: ToolContext, base: str, *, embeddings: bool = False) -> list[str]:
    # Verified fork contract: --branch refuses a mismatched checkout before
    # writing. Placement: no label -> flat; absent/unstamped flat -> adopt flat;
    # same owner -> flat; foreign owner -> branch sub-slot. omc cleans inversion
    # first and checks metadata absence, so it can only reach flat placement.
    # Keep index-only behavior: no AGENTS.md/CLAUDE.md or agent-skill writes.
    # --embeddings (spec 2026-10-27 §6): GitNexus's default 50 000-node cap is
    # kept; an analyze WITHOUT the flag preserves vectors already present.
    argv = gitnexus_argv(ctx, "analyze", "--skip-agents-md", "--skip-skills", "--branch", base)
    if embeddings:
        argv.append("--embeddings")
    return argv


class CheckoutMoved(Exception):
    def __init__(self, branch: str, base: str):
        super().__init__(branch, base)
        self.branch = branch
        self.base = base


@dataclass
class ResetProgress:
    """Whether a requested reset entered destructive work, even if repair aborts."""

    attempted: bool = False


def _require_base(ctx: ToolContext, root: Path, base: str) -> None:
    branch = current_branch(ctx, str(root)) or ""
    if branch != base:
        raise CheckoutMoved(branch, base)


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


_EMBED_WRITE_ENV = {"GITNEXUS_LBUG_EXTENSION_INSTALL": "auto"}  # Decision 9: write paths only


def _run_analyze(
    ctx: ToolContext,
    root: Path,
    base: str,
    *,
    embed: bool = False,
    say=_say,
    on_fallback: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """The single analyze call site for the incremental step and the rebuild.
    embed=True runs narrated (minutes of CPU, a model download) and falls back
    ONCE to today's keyword-only analyze when it fails: a model download dying
    on a plane must not cost the index (spec §6). The retry's result is what the
    caller's "still stale → destroy and rebuild" escalation judges.
    on_fallback is called when that retry happens, so refresh_knowledge can
    skip the backfill sync in a run where embedding demonstrably failed.

    The embedding phase prints nothing off a TTY (verified notes in the
    Embeddings section below), so liveness is the store changing under the long
    _EMBED_STALL_SECONDS budget, not bytes under the wiki one — bytes alone
    would kill a healthy multi-minute pass."""
    _require_base(ctx, root, base)
    if embed:
        budget = _EMBED_STALL_SECONDS
        cp, stalled = _run_narrated(
            ctx,
            analyze_argv(ctx, base, embeddings=True),
            cwd=str(root),
            say=say,
            extra_env=_EMBED_WRITE_ENV,
            heartbeat=_store_heartbeat(root),
            stall_after=budget,
        )
        reason = _failure_reason(cp, stalled, budget)
        if reason is None:
            return cp
        say(f"✗ analyze with embeddings {reason} — retrying keyword-only")
        if on_fallback is not None:
            on_fallback()
        _require_base(ctx, root, base)
    cp = ctx.run(analyze_argv(ctx, base), cwd=str(root))
    if cp.returncode != 0:
        _require_base(ctx, root, base)
    return cp


def _index_stat(root: Path, key: str) -> int:
    """stats.<key> from the index metadata (gitnexus.json first, then
    meta.json — read_index_meta's rule); absent, unreadable or non-int → 0."""
    meta = read_index_meta(root) or {}
    stats = meta.get("stats")
    count = stats.get(key) if isinstance(stats, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def _embedding_count(root: Path) -> int:
    return _index_stat(root, "embeddings")


def _backfill_embeddings(ctx: ToolContext, root: Path, say) -> None:
    """GitNexus's analyze short-circuits "Already up to date" whatever flags it
    carries, so an index that turns the option on would gain vectors one commit
    at a time, forever. `embeddings sync` adds the missing vectors to a healthy
    index, checkpoints for resume, writes stats.embeddings and never touches
    lastCommit (verified, see the Embeddings comment below) — it cannot move the
    freshness verdict either way. It may print nothing for long stretches while
    it embeds, so it is supervised on the store heartbeat under
    _EMBED_STALL_SECONDS, not on bytes. Failure is narrated only."""
    say("→ embedding the existing index (one-time)…")
    budget = _EMBED_STALL_SECONDS
    cp, stalled = _run_narrated(
        ctx,
        gitnexus_argv(ctx, "embeddings", "sync", str(root)),
        cwd=str(root),
        say=say,
        extra_env=_EMBED_WRITE_ENV,
        heartbeat=_store_heartbeat(root),
        stall_after=budget,
    )
    reason = _failure_reason(cp, stalled, budget)
    if reason is not None:
        say(f"✗ embeddings sync {reason}")
        return
    count = _embedding_count(root)
    if count > 0:
        say(f"✓ index embedded ({count} vectors)")
    else:
        say("✗ embeddings sync exited 0 but stats.embeddings is still 0")


# ─── Embeddings (spec 2026-10-27-enable-embeddings-for-gitnexus) ───────────
# Verified against the managed fork CLI (gitnexus 1.6.12 @ 20c559d, 2026-10-27); the
# E2E test_embeddings_opt_in_refresh_embeds_the_real_repo re-confirmed the flat-slot
# stats.embeddings > 0, lastCommit == HEAD, runtime under ~/.omc and an exiting query
# at the Dockerfile.e2e pin d8683be6 (2026-10-28):
# - `embeddings install` honours GITNEXUS_EMBEDDING_RUNTIME_DIR and lands
#   node_modules/@huggingface/transformers + node_modules/onnxruntime-node there;
#   nothing is written to ~/.gitnexus/embedding-runtime. Its output is plain
#   lines, each echoed again as a pino JSON line ({"level":30,…}).
# - `embeddings sync [path]` takes no --branch and writes the FLAT slot
#   (<path>/.gitnexus; no branches/ sub-slot appeared). It writes
#   stats.embeddings into BOTH gitnexus.json and meta.json and leaves lastCommit
#   alone, so it can never move the freshness verdict.
# - `analyze … --embeddings` embeds new nodes incrementally; an analyze without
#   the flag afterwards (up-to-date or incremental) preserves the vectors.
# - Off a TTY, analyze prints only its banner, "\x1b[2K\r"-prefixed phase lines
#   ("Scope: …", "Incremental: …") and the "Repository indexed successfully"
#   summary; no embedding progress line and no pino JSON. Lines starting with
#   "{" are pino JSON and are not narrated.
# - With no runtime: analyze --embeddings auto-installs into the runtime dir
#   ("Local embedding runtime is not installed. Downloading it now …") and
#   exits 0 — omc owns the install so that download is narrated, not silent.

# The embedding phase prints nothing off a TTY (verified above), so bytes alone
# would kill a healthy multi-minute pass; the embedding analyze and
# `embeddings sync` pass this with a store heartbeat.
_EMBED_STALL_SECONDS = 1800.0

# Line-start ANSI CSI escapes ("\x1b[2K" clear-line, colours) and carriage returns.
_LINE_START_ESCAPES = re.compile(r"^(?:\x1b\[[0-?]*[ -/]*[@-~]|\r)+")


def _dir_heartbeat(path: Path) -> Callable[[], object]:
    """Heartbeat for run_supervised: fingerprint the tree under `path` as
    (file count, newest mtime_ns, total size), None while the dir is absent.
    Swallows OSError — a broken heartbeat counts as "no change" anyway."""

    def beat() -> object:
        if not path.is_dir():
            return None
        count = newest = size = 0
        for dirpath, _dirs, files in os.walk(path):
            for name in files:
                try:
                    st = os.stat(os.path.join(dirpath, name))
                except OSError:
                    continue  # vanished mid-walk (atomic rename); next poll sees it
                count += 1
                newest = max(newest, st.st_mtime_ns)
                size += st.st_size
        return (count, newest, size)

    return beat


def _store_heartbeat(root: Path) -> Callable[[], object]:
    """The index store <root>/.gitnexus as the liveness signal of the embedding
    analyze and `embeddings sync`."""
    return _dir_heartbeat(root / ".gitnexus")


def _run_narrated(
    ctx: ToolContext,
    argv: list[str],
    *,
    cwd: str,
    say: Callable[[str], None],
    extra_env: dict[str, str] | None = None,
    heartbeat: Callable[[], object] | None = None,
    stall_after: float | None = None,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run a long GitNexus step supervised for liveness and narrate its lines.

    Serves the embedding runtime install, the embedding analyze and
    `embeddings sync`: minutes of CPU and downloads that a captured `ctx.run`
    would hide (a silent minute is a bug). By default byte activity is the only
    progress signal (constant heartbeat) and the stall window is
    _WIKI_STALL_SECONDS; a silent phase passes a real heartbeat (the runtime
    install) and, when it runs for minutes, a longer stall_after (the embedding
    pass). No progress bar (GITNEXUS_PROGRESS is a wiki-only feature of
    the fork). Off a TTY analyze prints "\\x1b[2K\\r"-prefixed phase lines; the
    escapes are stripped first. Pino JSON lines start with "{" and are not news.
    Returns (completed, stalled) like run_supervised; raises OSError like ctx.run."""
    last = [""]

    def on_line(line: str) -> None:
        text = _LINE_START_ESCAPES.sub("", line).strip()
        if not text or text.startswith("{") or text == last[0]:
            return
        last[0] = text
        say(f"· {text[:200]}")

    return ctx.run_supervised(
        argv,
        cwd=cwd,
        heartbeat=heartbeat if heartbeat is not None else (lambda: None),
        stall_after=_WIKI_STALL_SECONDS if stall_after is None else stall_after,
        poll=_WIKI_POLL_SECONDS,
        extra_env=extra_env,
        on_line=on_line,
    )


def _failure_reason(
    cp: subprocess.CompletedProcess[str], stalled: bool, budget: float
) -> str | None:
    """How a _run_narrated step failed, for its caller's `✗ <step> …` line:
    stalled under `budget` — the stall_after the caller actually passed, so the
    message cannot lie — or a non-zero exit with the output tail (where the
    error is). None when it succeeded; "exited 0 but …" is the caller's call."""
    if stalled:
        return f"stalled — no progress for {int(budget)}s; killed"
    if cp.returncode != 0:
        excerpt = (cp.stderr or cp.stdout or "").strip()[-400:]
        return f"failed (exit {cp.returncode}): {excerpt}"
    return None


def embedding_runtime_dir(ctx: ToolContext) -> Path:
    """The one source of the runtime path is gitnexus_env (spec §4)."""
    return Path(gitnexus_env(ctx.home)["GITNEXUS_EMBEDDING_RUNTIME_DIR"])


def _embeddings_ready(ctx: ToolContext) -> bool:
    """GitNexus's own two-package rule (resolveEmbeddingRuntime): probing
    transformers alone would misread an interrupted install as ready."""
    modules = embedding_runtime_dir(ctx) / "node_modules"
    return all(
        (modules / pkg / "package.json").is_file()
        for pkg in ("@huggingface/transformers", "onnxruntime-node")
    )


def ensure_embeddings(
    ctx: ToolContext,
    cfg: Config | GlobalConfig | None,
    *,
    reinstall: bool = False,
    say=_say,
) -> bool:
    """Is the local embedding runtime ready to be spent? The ONLY reader of
    `gitnexus.embeddings` (spec §3/§5). Off (or no config) → False, silently:
    the hot path for everyone who never opts in. On → install under the §4
    runtime dir when the probe fails or `reinstall` asks (the clone's pins may
    have moved). GitNexus's CPU install is `npm install --prefix <runtime>
    --ignore-scripts` of its pinned stack; re-running it moves the packages to
    the current pins, so `--force` is never passed. Never raises, never returns
    an exit code: a failed install degrades to keyword-only (Decision 5)."""
    if cfg is None or not cfg.gitnexus.embeddings:
        return False
    if _embeddings_ready(ctx) and not reinstall:
        return True
    say("→ installing GitNexus embedding runtime…")
    tail = " — indexing keyword-only"
    try:
        # cwd=home: the install needs no repo, and home exists whenever the CLI does.
        # npm may print nothing for minutes while it downloads, so liveness is the
        # runtime dir filling up, not bytes; the budget stays the wiki one.
        budget = _WIKI_STALL_SECONDS
        cp, stalled = _run_narrated(
            ctx,
            gitnexus_argv(ctx, "embeddings", "install"),
            cwd=str(ctx.home),
            say=say,
            heartbeat=_dir_heartbeat(embedding_runtime_dir(ctx)),
            stall_after=budget,
        )
    except OSError as exc:  # node missing or not runnable: an expected failure
        say(f"✗ embedding runtime install failed: {exc}{tail}")
        return False
    reason = _failure_reason(cp, stalled, budget)
    if reason is not None:
        say(f"✗ embedding runtime install {reason}{tail}")
        return False
    ready = _embeddings_ready(ctx)
    if ready:
        say("✓ GitNexus embedding runtime installed")
    else:
        say(f"✗ embedding runtime install exited 0 but the runtime is incomplete{tail}")
    return ready


def gitnexus_root(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus"


def flat_store_branch(root: Path) -> str | None:
    """Branch stamped in the flat (default) GitNexus store, or None.

    None covers: store absent, meta unreadable/unparseable, branch field
    missing/empty/non-string. All of those are states GitNexus's adoption
    rule resolves on the next analyze — only a non-empty FOREIGN stamp
    (see store_inverted) needs the heal.
    """
    meta = Path(root) / ".gitnexus" / "meta.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    branch = data.get("branch") if isinstance(data, dict) else None
    return branch if isinstance(branch, str) and branch else None


def store_inverted(root: Path, base: str) -> bool:
    """True when the flat store belongs to a branch other than ``base``.

    GitNexus keys its default store to the branch a repo was FIRST indexed
    on; analyzes on any other branch land in .gitnexus/branches/<slug>/,
    which the MCP server, staleness hints, and `wiki` never read. Inverted
    means: incremental refreshes are being delivered where nothing looks.
    """
    owner = flat_store_branch(root)
    return owner is not None and owner != base


# ─── Freshness verdict (spec 2026-09-27-stale-knowledge-snapshot-self-heal §1) ──

# Index metadata: upstream GitNexus renamed meta.json → gitnexus.json and keeps
# meta.json as a legacy mirror; the fork still writes only meta.json. Read the
# new name first; when both exist the legacy file is ignored entirely.
INDEX_META_NAMES = ("gitnexus.json", "meta.json")
WIKI_META_REL = Path(".gitnexus") / "wiki" / "meta.json"


def _read_json_object(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_index_meta(root: Path) -> dict | None:
    for name in INDEX_META_NAMES:
        p = Path(root) / ".gitnexus" / name
        if p.is_file():
            return _read_json_object(p)  # unreadable counts as missing, no fallback
    return None


def read_wiki_meta(root: Path) -> dict | None:
    return _read_json_object(Path(root) / WIKI_META_REL)


@dataclass(frozen=True)
class Reason:
    code: str
    text: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reasons: tuple[Reason, ...]
    fix: str
    run_in: str
    basis: str

    def to_json(self) -> dict:
        return {
            "fresh": self.fresh,
            "basis": self.basis,
            "reasons": [
                {"code": r.code, "text": r.text, "detail": dict(r.detail)} for r in self.reasons
            ],
            "fix": self.fix,
            "run_in": self.run_in,
        }

    def codes(self) -> list[str]:
        return [r.code for r in self.reasons]

    def index_codes(self) -> list[str]:
        return [c for c in self.codes() if not c.startswith("wiki-")]

    def wiki_codes(self) -> list[str]:
        return [c for c in self.codes() if c.startswith("wiki-")]


def _git_ok(ctx: ToolContext, root: Path, *args: str) -> bool:
    return ctx.run([ctx.git_bin, *args], cwd=str(root)).returncode == 0


def _git_out(ctx: ToolContext, root: Path, *args: str) -> str:
    cp = ctx.run([ctx.git_bin, *args], cwd=str(root))
    return (cp.stdout or "").strip() if cp.returncode == 0 else ""


def _same_checkout(recorded: str, root: Path) -> bool:
    a = os.path.realpath(recorded)
    b = os.path.realpath(str(root))
    if sys.platform == "darwin":
        return a.casefold() == b.casefold()
    return a == b


def _is_commit(ctx: ToolContext, root: Path, sha: object) -> bool:
    return (
        isinstance(sha, str)
        and bool(sha)
        and _git_ok(ctx, root, "cat-file", "-e", f"{sha}^{{commit}}")
    )


def snapshot_freshness(
    ctx: ToolContext,
    primary_root: Path | str,
    base: str,
    *,
    ref: str | None = None,
    documentation: bool = True,
) -> Freshness:
    """Is the primary's knowledge snapshot trustworthy? Pure: reads two metadata
    files and asks git; never repairs. Distances are measured against ``ref``
    (default origin/<base>; watch passes HEAD). Callers gate on a resolved
    primary root and fetch first when they can.

    ``index-unknown`` (a known-JSON-shape check: is ``lastCommit`` an object in
    this clone) does not need ``ref`` to resolve; only ``index-diverged`` and
    ``index-behind`` compare against ``ref`` and are skipped when it is
    unresolved (spec §1: "the distance codes ... are skipped" lists only
    those two plus wiki-behind, and wiki-behind compares against the index
    commit, not ``ref``, so it is unaffected either way).
    """
    root = Path(primary_root).resolve()
    ref = ref or f"origin/{base}"
    reasons: list[Reason] = []
    ref_ok = _git_ok(ctx, root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    basis = ref if ref_ok else "unresolved"

    meta = read_index_meta(root)
    if meta is None:
        reasons.append(Reason("index-missing", "no knowledge snapshot yet (no GitNexus index)"))
    else:
        # Inversion from the CHOSEN metadata file (store_inverted reads only
        # meta.json; it stays as-is for its other callers).
        owner = meta.get("branch")
        inverted = isinstance(owner, str) and bool(owner) and owner != base
        if inverted:
            reasons.append(
                Reason(
                    "store-inverted",
                    f"index is owned by branch {owner!r}, not {base!r}",
                    {"owner": owner},
                )
            )
        recorded = meta.get("repoPath")
        if isinstance(recorded, str) and recorded and not _same_checkout(recorded, root):
            reasons.append(
                Reason(
                    "index-foreign",
                    f"index was built for another checkout ({recorded})",
                    {"repoPath": recorded},
                )
            )
        dirty = meta.get("incrementalInProgress")
        if dirty:
            started = dirty.get("startedAt") if isinstance(dirty, dict) else None
            reasons.append(
                Reason("index-dirty", "previous analyze did not finish", {"startedAt": started})
            )
        last = meta.get("lastCommit")
        if not inverted:
            if not _is_commit(ctx, root, last):
                reasons.append(
                    Reason(
                        "index-unknown",
                        f"index commit {str(last)[:12]} is not in this clone",
                        {"lastCommit": last},
                    )
                )
            elif ref_ok:
                if not _git_ok(ctx, root, "merge-base", "--is-ancestor", last, ref):
                    reasons.append(
                        Reason(
                            "index-diverged",
                            f"index commit {last[:12]} is not reachable from {ref} in this clone",
                            {"lastCommit": last},
                        )
                    )
                else:
                    n = int(_git_out(ctx, root, "rev-list", "--count", f"{last}..{ref}") or 0)
                    if n > 0:
                        reasons.append(
                            Reason(
                                "index-behind", f"index is {n} commits behind {ref}", {"count": n}
                            )
                        )
        if documentation:
            wiki = read_wiki_meta(root)
            if wiki is None:
                reasons.append(Reason("wiki-missing", "no generated docs yet"))
            else:
                fc = wiki.get("fromCommit")
                if not _is_commit(ctx, root, fc):
                    reasons.append(
                        Reason(
                            "wiki-unknown",
                            "docs were generated from a commit not in this clone",
                            {"fromCommit": fc},
                        )
                    )
                elif _is_commit(ctx, root, last) and not _git_ok(
                    ctx, root, "merge-base", "--is-ancestor", last, fc
                ):
                    n = int(_git_out(ctx, root, "rev-list", "--count", f"{fc}..{last}") or 0)
                    reasons.append(
                        Reason(
                            "wiki-behind",
                            f"docs are {n} commits behind the index",
                            {"fromCommit": fc, "count": n},
                        )
                    )

    codes = [r.code for r in reasons]
    if not codes:
        fix = ""
    elif any(c.startswith("wiki-") for c in codes):
        fix = "omc watch --once --enable-documentation"
    else:
        fix = "omc watch --once"
    return Freshness(
        fresh=not reasons, reasons=tuple(reasons), fix=fix, run_in=str(root), basis=basis
    )


# ─── The one repair path (spec §2) ──────────────────────────────────────────


def _clear_mirror(root: Path, say) -> bool:
    try:
        cleared = clear_docs_mirror(root)
    except OSError as exc:
        # rmtree can fail (permissions, racing reader); the index is what we are
        # here for — warn and continue, the next documentation run re-mirrors.
        say(f"✗ could not delete the stale docs mirror: {exc}")
        return False
    if cleared:
        say("· stale docs mirror deleted")
    return cleared


def _destroy(ctx: ToolContext, root: Path, say) -> bool:
    """`clean --force` judged by POST-CONDITION (it exits 0 when deletion fails)."""
    cp = ctx.run(gitnexus_argv(ctx, "clean", "--force"), cwd=str(root))
    if read_index_meta(root) is not None:
        say(f"✗ clean did not remove the index: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return False
    return True


def _destroy_and_rebuild(
    ctx: ToolContext,
    root: Path,
    base: str,
    say,
    *,
    embed: bool = False,
    on_fallback: Callable[[], None] | None = None,
) -> tuple[bool, bool]:
    """Destroy the store and index from scratch. Returns (healed, mirror_cleared);
    the second flag is what a "docs mirror cleared" hint may claim.

    Narrates only FAILURE. Getting the branch stamp right is a necessary step,
    not the goal — the recomputed verdict is the goal, so `✓ index rebuilt` is
    the CALLER's line, printed only once that verdict is clean (spec §2)."""
    _require_base(ctx, root, base)
    mirror_cleared = _clear_mirror(root, say)
    if not _destroy(ctx, root, say):
        return False, mirror_cleared
    cp = _run_analyze(ctx, root, base, embed=embed, say=say, on_fallback=on_fallback)
    if cp.returncode != 0:
        say(f"✗ full analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
    if flat_store_branch(root) != base:
        say(f"✗ rebuilt index is not owned by {base!r} — not claiming success")
        return False, mirror_cleared
    return True, mirror_cleared


def _run_wiki(ctx: ToolContext, cfg: Config, root: Path, base: str, say) -> bool:
    _require_base(ctx, root, base)
    # Lazy like docs_llm_for below: a module-level import of omc.cli.* from here
    # is a cycle (omc.cli → start → gitnexus). wikirun.render() does the same.
    from .cli.progress_bar import BarThread
    from .providers.registry import docs_llm_for

    try:
        run = docs_llm_for(cfg)
    except OmcError as exc:
        # ConfigError (no key / alias model) or get_provider's plain OmcError —
        # reachable only by hand-edited files. Return False like the stall path:
        # `omc watch`'s loop has no OmcError guard and must never crash.
        say(f"✗ documentation: {exc}")
        return False
    say(f"→ regenerating documentation via {run.label}")
    tracker = PageCountTracker(root / ".gitnexus" / "wiki")
    gn = GitNexusProgress(fallback=tracker)
    bar = BarThread(gn)  # TTY-gated: constructing on a piped stderr yields a no-op
    narrated = ""

    def on_line(line: str) -> None:
        # Runs on run_supervised's reader thread. Off a TTY the bar is silent,
        # so narrate GitNexus's phase changes instead (a silent minute is a
        # bug; depwatch narrates headless runs the same way). Heartbeats repeat
        # the phase and are not news; an unchanged detail is not either.
        nonlocal narrated
        gn.feed(run.redact(line))
        if bar.enabled or gn.phase in ("", "heartbeat") or gn.detail == narrated:
            return
        narrated = gn.detail
        say(f"· {gn.detail}")

    bar.start()
    try:
        cp, stalled = ctx.run_supervised(
            gitnexus_argv(ctx, *run.wiki_args),
            cwd=str(root),
            heartbeat=tracker.beat,
            stall_after=_WIKI_STALL_SECONDS,
            poll=_WIKI_POLL_SECONDS,
            extra_env=run.extra_env,  # the key reaches exactly this node child
            on_line=on_line,
        )
    finally:
        bar.stop()  # clear the bar line before any ✓/✗ narration
    if stalled:
        say(f"✗ wiki stalled — no progress for {int(_WIKI_STALL_SECONDS)}s; killed")
        return False
    if cp.returncode != 0:
        say(f"✗ wiki failed (exit {cp.returncode}): {wiki_failure_excerpt(cp, run.redact)}")
    return True  # the recomputed verdict, not the exit code, decides


def refresh_knowledge(
    ctx: ToolContext,
    cfg: Config,
    root: Path | str,
    base: str,
    *,
    documentation: bool,
    reset: bool,
    ref: str = "HEAD",
    say=_say,
    reset_progress: ResetProgress | None = None,
) -> Freshness:
    """The ONLY code that repairs the knowledge snapshot (spec §2). Cheapest
    step first, escalate only when the recomputed verdict says so. Exit codes
    are narrated, never trusted."""
    rootp = Path(root)

    def verdict() -> Freshness:
        return snapshot_freshness(ctx, rootp, base, ref=ref, documentation=documentation)

    did_anything = False
    mirror_cleared = False

    def finish(v: Freshness) -> Freshness:
        """EVERY exit goes through here: the docs mirror is cleared before the
        index work and nothing downstream is guaranteed to run, so the promise
        "a fresh verdict implies a present mirror" has to be kept on the way
        out — including from the early aborts."""
        mirror = rootp / DOCS_MIRROR_REL
        if v.fresh and not mirror.is_dir() and read_wiki_meta(rootp) is not None:
            # Surviving readable wiki content is useful even when documentation
            # freshness is deliberately excluded from this refresh's verdict.
            if mirror_dir(rootp / ".gitnexus" / "wiki", mirror):
                say("✓ docs mirror restored")
        if not documentation and mirror_cleared and not mirror.is_dir():
            say("· docs mirror cleared — run omc watch --once --enable-documentation to regenerate")
        if not did_anything:
            say("✓ knowledge is current")
        return v

    if reset:
        _require_base(ctx, rootp, base)
        if reset_progress is not None:
            reset_progress.attempted = True
        say("→ resetting the knowledge snapshot (--reset-gitnexus)")
        did_anything = True
        mirror_cleared = _clear_mirror(rootp, say)
        if not _destroy(ctx, rootp, say):
            return finish(verdict())

    # Decided once per run; true only when the option is on AND the runtime is
    # ready after any install attempt. Off emits nothing and changes no argv.
    embed = ensure_embeddings(ctx, cfg, say=say)
    # Set when an embedding analyze fell back keyword-only this run; read at
    # each later call site so a rebuild does not retry --embeddings (another
    # stall budget under the busy lock) and the backfill is skipped.
    embed_failed = False

    def fell_back() -> None:
        nonlocal embed_failed
        embed_failed = True

    v = verdict()
    if v.index_codes():
        had_shared_pointer = (rootp / ".gitnexus" / "store.json").exists()
        if had_shared_pointer:
            say(
                "· GitNexus shared-store pointer found — re-indexing locally; "
                "sharing is off under omc"
            )
        did_anything = True
        rebuilt = False
        if "store-inverted" in v.index_codes():
            owner = flat_store_branch(rootp)
            say(f"✗ GitNexus index is owned by {owner!r}, not {base!r} — destroying and rebuilding")
            healed, cleared = _destroy_and_rebuild(
                ctx, rootp, base, say, embed=embed and not embed_failed, on_fallback=fell_back
            )
            mirror_cleared = mirror_cleared or cleared
            if not healed:
                return finish(verdict())
            rebuilt = True
        else:
            say("→ refreshing GitNexus index (incremental)")
            cp = _run_analyze(ctx, rootp, base, embed=embed, say=say, on_fallback=fell_back)
            if cp.returncode != 0:
                # Narrate only — the verdict below decides whether to escalate.
                say(f"✗ analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
            else:
                say("✓ index refreshed")
            v = verdict()
            if v.index_codes():
                codes = ",".join(v.index_codes())
                say(f"✗ index still stale after analyze ({codes}) — destroying and rebuilding")
                healed, cleared = _destroy_and_rebuild(
                    ctx, rootp, base, say, embed=embed and not embed_failed, on_fallback=fell_back
                )
                mirror_cleared = mirror_cleared or cleared
                if not healed:
                    return finish(verdict())
                rebuilt = True
        v = verdict()
        if v.index_codes():
            say(f"✗ index still stale after rebuild: {','.join(v.index_codes())}")
            return finish(v)
        if rebuilt:
            # Earned only here: the rebuild's branch stamp was necessary, the
            # clean verdict is what makes it a success.
            say(f"✓ index rebuilt for {base}")
        if had_shared_pointer:
            say("→ collecting orphaned GitNexus shared stores (clean --gc)")
            cp = ctx.run(gitnexus_argv(ctx, "clean", "--gc", "--force"), cwd=str(rootp))
            excerpt = (cp.stderr or cp.stdout or "").strip()[-400:]
            if cp.returncode != 0:
                say(f"✗ clean --gc failed (exit {cp.returncode}): {excerpt}")
            elif excerpt:
                say(f"· {excerpt}")

    if embed and _embedding_count(rootp) == 0 and _index_stat(rootp, "nodes") > 0:
        # Clean index verdict (every stale path above returned), option on, no
        # vectors yet: the one-time backfill (spec §6). Once stats.embeddings is
        # positive this is a single JSON read. An index with no nodes has
        # nothing to embed: sync would exit 0 with 0 vectors on every refresh.
        # Not after a fallback this run: embedding is unavailable (offline,
        # broken model download) and a stalled sync would hold the busy lock for
        # the whole budget for nothing.
        if embed_failed:
            say(
                "· skipping the one-time embedding backfill — embedding failed this run; "
                "the next refresh retries"
            )
        else:
            did_anything = True
            _backfill_embeddings(ctx, rootp, say)

    if not documentation:
        return finish(v)

    if v.wiki_codes():
        did_anything = True
        if not _run_wiki(ctx, cfg, rootp, base, say):
            return finish(verdict())
        v = verdict()
        if v.wiki_codes():
            say("✗ documentation still behind after regeneration")
            return finish(v)
        mirror_dir(rootp / ".gitnexus" / "wiki", rootp / DOCS_MIRROR_REL)
        say("✓ documentation refreshed → .omc/docs/gitnexus/docs")
    return finish(v)


def redact_userinfo(url: str) -> str:
    # Never echo credentials embedded in a remote URL.
    return re.sub(r"//[^/@]*@", "//[REDACTED]@", url)


_redact_userinfo = redact_userinfo  # back-compat alias


def _run_tool(
    ctx: ToolContext,
    argv: list[str],
    *,
    cwd: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """subprocess boundary for OPTIONAL tools (node/npm) — a missing binary is
    an expected failure, reported like any other failed step (installer._uv idiom).
    Git stays unwrapped: the repo treats git as guaranteed (watch.py does too).
    """
    try:
        return ctx.run(argv, cwd=cwd, extra_env=extra_env)
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


def _native_build_env(ctx: ToolContext) -> dict[str, str]:
    """Node >= 26 headers need C++20, but GitNexus's pinned tree-sitter 0.21.1
    hardcodes -std=c++17 in binding.gyp. Where no prebuilt binary matches
    (linux-arm64) node-gyp compiles from source and dies on that. gyp appends
    $CXXFLAGS after its own flags, so a trailing -std=c++20 wins; harmless on
    older Node and on platforms that use the prebuild."""
    current = ctx.env.get("CXXFLAGS", "").strip()
    return {"CXXFLAGS": f"{current} {_CXX_STD}".strip()}


def _build(ctx: ToolContext, root: Path) -> int:
    """Two-step npm build; order matters (gitnexus-shared is a plain sibling
    package compiled by the main build with its own node_modules)."""
    extra_env = _native_build_env(ctx)
    for argv, cwd in (
        (["npm", "install", "--no-audit", "--no-fund"], root / "gitnexus-shared"),
        (["npm", "ci"], root / "gitnexus"),
        (["npm", "run", "build"], root / "gitnexus"),
    ):
        cp = _run_tool(ctx, argv, cwd=str(cwd), extra_env=extra_env)
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


def update_gitnexus(
    ctx: ToolContext,
    *,
    approved_origin: str = GITNEXUS_ORIGIN,
    cfg: Config | GlobalConfig | None = None,
) -> int:
    """Deterministic install-or-update of the managed GitNexus clone
    (`omc update`). Installs when missing, else forces main and rebuilds.

    With the embeddings option on, a rebuilt clone re-pins the embedding runtime
    (its package.json gitnexusEmbeddingStack may have moved); an up-to-date clone
    only probes. Neither outcome touches the rc (Decision 5)."""
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
        ensure_embeddings(ctx, cfg)
        return 0
    verb = "installing" if freshly_cloned else "updating"
    print(f"→ {verb} GitNexus…", file=sys.stderr)
    argv = [git, "-C", str(root), "checkout", "-f", "-B", "main", "origin/main"]
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
    ensure_embeddings(ctx, cfg, reinstall=True)
    return 0
