"""External dependency checkouts + their GitNexus indexes/docs (under ~/.omc).

Layout (spec 2026-07-22-gitnexus-dependency-index-explain-design.md):
  <home>/dependencies/<host>/<owner...>/<repo>/<commit>   checkout, branch omc-pin
  <home>/gitnexus/<host>/<owner...>/<repo>/<commit>/docs  mirrored wiki
  <home>/dependencies.json                                manifest (atomic writes)

GitNexus has no commit-hash concept — it indexes a WORKING TREE keyed by repo
+ branch slot. Pinning the fixed local branch omc-pin at the commit makes the
first-indexed branch own the default store, so queries stay deterministic.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import store
from .errors import OmcError
from .gitnexus import gitnexus_argv, gitnexus_cli, redact_userinfo
from .mirror import mirror_dir
from .toolctx import ToolContext

PIN_BRANCH = "omc-pin"

# Liveness window for wiki generation: the run may take 40+ minutes, but 300s
# with zero progress (no new page on disk, no child output) marks a wedge.
_WIKI_STALL_SECONDS = 300.0

# One disk poll per second drives BOTH the stall-guard heartbeat and progress
# reporting; monkeypatchable in tests.
_WIKI_POLL_SECONDS = 1.0

_HTTPS_RE = re.compile(r"\Ahttps://(?:[^/@]+@)?([^/:@]+)/(.+?)(?:\.git)?/?\Z")
_SSH_RE = re.compile(r"\Assh://(?:([^/@:]+)(?::[^/@]*)?@)?([^/:@]+)(?::(\d+))?/(.+?)(?:\.git)?/?\Z")
_SCP_RE = re.compile(r"\A(?:([^/@:]+)@)?([^/:@]+):(?!//)(.+?)(?:\.git)?/?\Z")


def _redact(s: str) -> str:
    # Strip any userinfo before echoing user input in an error message. Covers
    # scp-form too (redact_userinfo's //<info>@ pattern misses it), so a
    # token-bearing string that misparses can never leak into a message.
    return re.sub(r"[^@\s/]+@", "[REDACTED]@", s)


@dataclass(frozen=True)
class DepRef:
    host: str
    path: str  # owner/.../repo — arbitrary depth (GitLab subgroups)
    url: str  # credential-free clone URL

    @property
    def key(self) -> str:
        return f"{self.host}/{self.path}"


def _check_segments(host: str, path: str) -> None:
    segs = path.split("/")
    if not host or "." not in host or any(s in ("", ".", "..") for s in segs):
        raise OmcError(f"unsafe git URL components: host={_redact(host)!r} path={_redact(path)!r}")


def parse_git_url(url: str) -> DepRef:
    url = url.strip()
    for scheme in ("git://", "http://", "file://"):
        if url.startswith(scheme):
            raise OmcError(f"{scheme.rstrip('/')} URLs are not allowed — use https:// or ssh")
    if url.startswith(("/", ".", "~")):
        raise OmcError("local paths are not allowed — use https:// or ssh")
    if m := _HTTPS_RE.match(url):
        host, path = m.group(1), m.group(2)
        _check_segments(host, path)
        # userinfo (tokens/passwords) dropped entirely on https
        return DepRef(host=host, path=path, url=f"https://{host}/{path}.git")
    if m := _SSH_RE.match(url):
        user, host, port, path = m.group(1) or "git", m.group(2), m.group(3), m.group(4)
        _check_segments(host, path)
        # ssh keeps the USERNAME (required to authenticate); any :password is dropped
        hostport = f"{host}:{port}" if port else host
        return DepRef(host=host, path=path, url=f"ssh://{user}@{hostport}/{path}.git")
    if "://" not in url and (m := _SCP_RE.match(url)):
        user, host, path = m.group(1) or "git", m.group(2), m.group(3)
        _check_segments(host, path)
        return DepRef(host=host, path=path, url=f"{user}@{host}:{path}.git")
    raise OmcError(f"cannot parse git URL {_redact(url)!r} — use https:// or ssh")


def checkout_dir(home: Path, ref: DepRef, commit: str) -> Path:
    return home / "dependencies" / ref.host / Path(ref.path) / commit


def docs_dir(home: Path, ref: DepRef, commit: str) -> Path:
    return home / "gitnexus" / ref.host / Path(ref.path) / commit / "docs"


def manifest_path(home: Path) -> Path:
    return home / "dependencies.json"


def load_manifest(home: Path) -> dict:
    p = manifest_path(home)
    if not p.is_file():
        return {"version": 1, "dependencies": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise OmcError(f"corrupt dependency manifest {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise OmcError(f"corrupt dependency manifest {p}: expected a JSON object")
    data.setdefault("version", 1)
    data.setdefault("dependencies", {})
    return data


@contextmanager
def _manifest_lock(home: Path):
    """Serialize manifest read-modify-write ACROSS PROCESSES: dependency watch
    documents up to 8 dependencies in parallel, and two subprocesses saving
    near-simultaneously would lose one flip (a lost documented:true re-runs an
    entire LLM wiki). flock on a sibling lockfile; fcntl is stdlib on omc's
    platforms (macOS/Linux)."""
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "dependencies.json.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def update_manifest(home: Path, mutate) -> dict:
    """Locked read-modify-write: load fresh, apply ``mutate(manifest)``, save.
    Returns the saved manifest. All manifest WRITERS go through here; readers
    stay lock-free (they tolerate staleness by design)."""
    with _manifest_lock(home):
        manifest = load_manifest(home)
        mutate(manifest)
        save_manifest(home, manifest)
        return manifest


def save_manifest(home: Path, data: dict) -> None:
    p = manifest_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Unique per-writer tmp name (pid + mkstemp randomness): a fixed ".tmp" path
    # would let concurrent writers (ensure + dependency-watch) clobber each
    # other's half-written file before the atomic rename lands.
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".dependencies.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, p)  # atomic on POSIX
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PageCountTracker:
    """Wiki-generation progress read from DISK, not child output (gitnexus's
    own bar is TTY-gated and emits nothing through a pipe): total = modules in
    first_module_tree.json + 1 overview page, done = *.md pages present.
    Missing/corrupt state degrades to indeterminate (percent None) — progress
    plumbing must never crash a documentation run.

    A pure data source: refresh()/state()/beat()/percent only. Rendering
    (bars, spinners, elapsed clocks) is the caller's job, not this class's."""

    def __init__(self, wiki_dir: Path) -> None:
        self._dir = wiki_dir
        self._done = 0
        self._total: int | None = None

    @staticmethod
    def _count(nodes: object) -> int:
        # Iterative with an explicit stack — a pathologically deep children
        # chain must not blow the recursion limit.
        total = 0
        stack = [nodes]
        while stack:
            level = stack.pop()
            if not isinstance(level, list):
                continue
            for node in level:
                if isinstance(node, dict):
                    total += 1
                    stack.append(node.get("children"))
        return total

    def refresh(self) -> None:
        try:
            tree = json.loads((self._dir / "first_module_tree.json").read_text())
            modules = self._count(tree)
            done = sum(1 for p in self._dir.iterdir() if p.suffix == ".md")
        except (OSError, ValueError):
            # ValueError covers json.JSONDecodeError AND UnicodeDecodeError
            # (read_text() on invalid-UTF-8 bytes) — both are non-OSError
            # ways disk state can be unreadable, and both must degrade to
            # indeterminate rather than raise out of a caller's poll loop.
            self._total = None
            return
        self._total = modules + 1 if modules else None  # +1: the final overview page
        self._done = done

    def state(self) -> tuple[int | None, int]:
        """Progress token — run_supervised compares successive values."""
        return (self._total, self._done)

    def beat(self) -> tuple[int | None, int]:
        """Heartbeat for run_supervised: self-refreshing on every poll."""
        self.refresh()
        return self.state()

    @property
    def percent(self) -> int | None:
        if not self._total:
            return None
        return min(100, round(100 * self._done / self._total))


_FULL_HASH_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_HASH_SUFFIX_RE = re.compile(r"\A[0-9a-f]{7,40}\Z")
_ENSURE_HINT = "run `omc internal dependency ensure --git <url>` first"


def _verdict(payload: dict) -> None:
    print(f"OMC_DEPENDENCY {json.dumps(payload)}", flush=True)


def _progress(percent: int) -> None:
    """OMC_PROGRESS: machine-readable progress on stdout (contract sibling of
    OMC_DEPENDENCY). The watch parses these; rendering is the caller's job."""
    print(f"OMC_PROGRESS {json.dumps({'percent': percent})}", flush=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_ref(home: Path, ref_str: str) -> tuple[str, str, dict]:
    """(key, commit, entry) for a URL or manifest key, optional @<hash> suffix."""
    # Split at the LAST @, and only when the suffix looks like a commit hash —
    # otherwise scp-form refs (git@host:path) and credentialed https refs
    # (https://user:token@host/path) would be split at their userinfo @, both
    # breaking the "URL or manifest key" contract and leaking the token prefix.
    head, sep, tail = ref_str.rpartition("@")
    if sep and _HASH_SUFFIX_RE.match(tail):
        base, commit = head, tail
    else:
        base, commit = ref_str, ""
    try:
        key = parse_git_url(base).key
    except OmcError:
        key = base.strip().strip("/")
    dep = load_manifest(home)["dependencies"].get(key)
    if not dep or not dep.get("commits"):
        raise OmcError(f"unknown dependency {_redact(key)!r} — {_ENSURE_HINT}")
    commits = dep["commits"]
    if commit:
        if commit not in commits:
            raise OmcError(f"no commit {commit!r} for {_redact(key)!r} — {_ENSURE_HINT}")
        return key, commit, commits[commit]
    newest = max(commits, key=lambda c: commits[c].get("created") or "")
    return key, newest, commits[newest]


def _resolve_commit(ctx: ToolContext, ref: DepRef, commit: str | None) -> str:
    if commit:
        if not _FULL_HASH_RE.match(commit):
            raise OmcError(f"--commit must be the full 40-char hash, got {commit!r}")
        return commit
    cp = ctx.run([ctx.git_bin, "ls-remote", ref.url, "HEAD"])
    head = (cp.stdout or "").split()
    if cp.returncode != 0 or not head or not _FULL_HASH_RE.match(head[0]):
        detail = _redact((cp.stderr or "").strip())[:200]
        raise OmcError(f"cannot resolve HEAD of {redact_userinfo(ref.url)}: {detail}")
    return head[0]


def run_ensure(ctx: ToolContext, git_url: str, commit: str | None) -> int:
    """Clone at commit + index. NO LLM — documentation is `document`'s job."""
    try:
        ref = parse_git_url(git_url)
        commit = _resolve_commit(ctx, ref, commit)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not gitnexus_cli(ctx).is_file():
        print(
            "error: GitNexus is not installed — run /omc:index once in a session first",
            file=sys.stderr,
        )
        return 1
    manifest = load_manifest(ctx.home)
    dest = checkout_dir(ctx.home, ref, commit)
    docs = docs_dir(ctx.home, ref, commit)
    entry = manifest["dependencies"].get(ref.key, {}).get("commits", {}).get(commit)
    if entry and entry.get("indexed") and (dest / ".git").exists():
        _verdict(
            {
                "ok": True,
                "cached": True,
                "key": ref.key,
                "commit": commit,
                "checkout": str(dest),
                "docs": str(docs),
                "indexed": True,
                "documented": bool(entry.get("documented")),
            }
        )
        return 0
    if not (dest / ".git").exists():
        # clone into a tmp sibling, pin the branch, then atomically rename in
        tmp = dest.parent / f".tmp-{commit}"
        if tmp.exists():
            shutil.rmtree(tmp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cp = ctx.run([ctx.git_bin, "clone", "--no-checkout", ref.url, str(tmp)])
        if cp.returncode != 0:
            print(
                f"error: clone of {redact_userinfo(ref.url)} failed: "
                f"{_redact((cp.stderr or '').strip())[:400]}",
                file=sys.stderr,
            )
            return 1
        cp = ctx.run([ctx.git_bin, "-C", str(tmp), "checkout", "-b", PIN_BRANCH, commit])
        if cp.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            print(
                f"error: commit {commit} not found in {redact_userinfo(ref.url)}: "
                f"{_redact((cp.stderr or '').strip())[:400]}",
                file=sys.stderr,
            )
            return 1
        tmp.rename(dest)
    cp = ctx.run(
        gitnexus_argv(ctx, "analyze", "--index-only", "--name", f"{ref.key}@{commit[:7]}"),
        cwd=dest,
    )
    if cp.returncode != 0:
        detail = _redact((cp.stderr or cp.stdout or "").strip())[:400]
        print(f"error: gitnexus analyze failed: {detail}", file=sys.stderr)
        return 1
    # Locked read-modify-write: clone+index spent minutes, during which a
    # concurrent writer (e.g. a parallel document flipping documented:true) may
    # have rewritten the manifest — and other writers may race the save itself.
    documented = False

    def _record(manifest: dict) -> None:
        nonlocal documented
        dep = manifest["dependencies"].setdefault(ref.key, {"url": ref.url, "commits": {}})
        dep["url"] = ref.url
        c = dep["commits"].setdefault(commit, {})
        c.update(
            {
                "checkout": str(dest),
                "docs": str(docs),
                "indexed": True,
                "documented": bool(c.get("documented")),
                "created": c.get("created") or _now_iso(),
            }
        )
        documented = c["documented"]

    update_manifest(ctx.home, _record)
    _verdict(
        {
            "ok": True,
            "cached": False,
            "key": ref.key,
            "commit": commit,
            "checkout": str(dest),
            "docs": str(docs),
            "indexed": True,
            "documented": documented,
        }
    )
    return 0


def run_document(ctx: ToolContext, ref_str: str) -> int:
    """The LLM step, run separately from ensure (wiki is slow): gitnexus wiki
    in the checkout, mirror .gitnexus/wiki -> the docs tree, flip documented."""
    try:
        key, commit, entry = resolve_ref(ctx.home, ref_str)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # A falsy checkout must be treated as not-indexed BEFORE building a Path:
    # Path("") is Path("."), and "./.git" would spuriously pass the guard
    # whenever cwd happens to be a git repo (wrong-repo answers).
    checkout = entry.get("checkout")
    if not entry.get("indexed") or not checkout or not (Path(checkout) / ".git").exists():
        print(
            f"error: {_redact(key)}@{commit[:7]} is not indexed — {_ENSURE_HINT}",
            file=sys.stderr,
        )
        return 1
    dest = Path(checkout)
    cfg = store.load_global(ctx.home)
    if cfg is None:
        print("error: omc is not configured — run `omc configure` first.", file=sys.stderr)
        return 1
    name = cfg.llm.default
    wiki_args = ["wiki", "--provider", name]
    # Docs model, never the session model (see watch._refresh_index rationale).
    from .providers.registry import docs_model_for

    docs_model = docs_model_for(cfg, name)
    if docs_model:
        wiki_args += ["--model", docs_model]
    wiki = dest / ".gitnexus" / "wiki"
    tracker = PageCountTracker(wiki)
    tracker.refresh()
    total, done = tracker.state()
    if total and done:
        print(f"· resuming — {done}/{total} pages already on disk", file=sys.stderr, flush=True)
    last_pct = tracker.percent
    if last_pct is not None:
        _progress(last_pct)

    def _beat() -> object:
        # Heartbeat AND reporter: the same disk poll feeds the stall guard's
        # token and emits OMC_PROGRESS whenever the integer percent moves.
        nonlocal last_pct
        token = tracker.beat()
        pct = tracker.percent
        if pct is not None and pct != last_pct:
            last_pct = pct
            _progress(pct)
        return token

    cp, stalled = ctx.run_supervised(
        gitnexus_argv(ctx, *wiki_args),
        cwd=dest,
        heartbeat=_beat,
        stall_after=_WIKI_STALL_SECONDS,
        poll=_WIKI_POLL_SECONDS,
    )
    if stalled:
        print(
            f"error: gitnexus wiki stalled — no progress for {int(_WIKI_STALL_SECONDS)}s; killed",
            file=sys.stderr,
        )
        return 1
    if cp.returncode != 0 or not wiki.is_dir():
        print(
            f"error: gitnexus wiki failed: {_redact((cp.stderr or cp.stdout or '').strip())[:400]}",
            file=sys.stderr,
        )
        return 1
    # entry["docs"] is always set by run_ensure; the fallback derives the same
    # path straight from the key (host/owner/.../repo) without re-parsing a URL.
    docs = Path(entry.get("docs") or (ctx.home / "gitnexus" / Path(key) / commit / "docs"))
    mirror_dir(wiki, docs)
    update_manifest(
        ctx.home, lambda m: m["dependencies"][key]["commits"][commit].update(documented=True)
    )
    _progress(100)  # deterministic completion signal — the last poll may have missed the final page
    _verdict(
        {
            "ok": True,
            "key": key,
            "commit": commit,
            "checkout": str(dest),
            "docs": str(docs),
            "indexed": True,
            "documented": True,
        }
    )
    return 0


def run_list(home: Path) -> int:
    print(json.dumps(load_manifest(home), indent=2, sort_keys=True))
    return 0
