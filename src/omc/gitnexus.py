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
from .toolctx import ToolContext
from .wikirun import (
    _WIKI_POLL_SECONDS,
    _WIKI_STALL_SECONDS,
    GitNexusProgress,
    PageCountTracker,
    wiki_failure_excerpt,
)

if TYPE_CHECKING:  # annotation-only; ToolContext stays the subprocess boundary
    import subprocess

    from .config.schema import Config

# Index-only analyze: no AGENTS.md/CLAUDE.md writes, no agent-skill installs —
# same flags the gitnexus-index skill prescribes.
ANALYZE_ARGS = ("analyze", "--skip-agents-md", "--skip-skills")

# The ONLY source ever updated — mirrors the gitnexus-ensure skill's rule.
GITNEXUS_ORIGIN = "https://github.com/chris-husse/GitNexus.git"
_CXX_STD = "-std=c++20"  # see _native_build_env


def gitnexus_cli(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"


def gitnexus_argv(ctx: ToolContext, *args: str) -> list[str]:
    return ["node", str(gitnexus_cli(ctx)), *args]


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


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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


def _destroy_and_rebuild(ctx: ToolContext, root: Path, base: str, say) -> tuple[bool, bool]:
    """Destroy the store and index from scratch. Returns (healed, mirror_cleared);
    the second flag is what a "docs mirror cleared" hint may claim.

    Narrates only FAILURE. Getting the branch stamp right is a necessary step,
    not the goal — the recomputed verdict is the goal, so `✓ index rebuilt` is
    the CALLER's line, printed only once that verdict is clean (spec §2)."""
    mirror_cleared = _clear_mirror(root, say)
    if not _destroy(ctx, root, say):
        return False, mirror_cleared
    cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=str(root))
    if cp.returncode != 0:
        say(f"✗ full analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
    if flat_store_branch(root) != base:
        say(f"✗ rebuilt index is not owned by {base!r} — not claiming success")
        return False, mirror_cleared
    return True, mirror_cleared


def _run_wiki(ctx: ToolContext, cfg: Config, root: Path, say) -> bool:
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
        say("→ resetting the knowledge snapshot (--reset-gitnexus)")
        did_anything = True
        mirror_cleared = _clear_mirror(rootp, say)
        if not _destroy(ctx, rootp, say):
            return finish(verdict())

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
            healed, cleared = _destroy_and_rebuild(ctx, rootp, base, say)
            mirror_cleared = mirror_cleared or cleared
            if not healed:
                return finish(verdict())
            rebuilt = True
        else:
            say("→ refreshing GitNexus index (incremental)")
            cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=str(rootp))
            if cp.returncode != 0:
                # Narrate only — the verdict below decides whether to escalate.
                say(f"✗ analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
            else:
                say("✓ index refreshed")
            v = verdict()
            if v.index_codes():
                codes = ",".join(v.index_codes())
                say(f"✗ index still stale after analyze ({codes}) — destroying and rebuilding")
                healed, cleared = _destroy_and_rebuild(ctx, rootp, base, say)
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

    if not documentation:
        return finish(v)

    if v.wiki_codes():
        did_anything = True
        if not _run_wiki(ctx, cfg, rootp, say):
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
    return 0
