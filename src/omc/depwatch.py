"""`omc dependency` (watch/list) — the ~/.omc dependency cache, human side.

`watch`: foreground polling loop like watch.py (omc never creates daemons).
Each pass DRAINS: ticks re-run back-to-back until a scan finds no new work
(an attempted-set caps every action at once per pass, so failures cannot
spin), then the pass announces plainly — "Finished documenting all
dependencies!" or how many items are still pending. Every mutation is
delegated to an `omc internal dependency …` subprocess — the loop only scans
and schedules (which is exactly what the unit tests assert). Runs from
anywhere: it operates on ~/.omc, not on a project checkout.

`list`: read-only status table of the manifest (repo, commit, indexed,
documented) on stdout.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .cli.progress_bar import MultiBarThread, render_bar
from .dependency import load_manifest, parse_git_url
from .errors import OmcError
from .toolctx import ToolContext

_HASH_DIR = re.compile(r"\A[0-9a-f]{40}\Z")

# Wiki generation dominates wall-clock; document up to this many dependencies
# concurrently. The manifest stays consistent under this: every writer goes
# through dependency.update_manifest's flock.
_DOCUMENT_JOBS = 8


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _scan_disk(home: Path) -> list[Path]:
    """Checkout dirs (<40-hex name containing .git>) under <home>/dependencies.
    Any other git clone found on the way (e.g. the managed gitnexus tool at
    dependencies/gitnexus) is pruned — descending into working trees would be
    wasted I/O and could misread nested repos as dependencies."""
    root = home / "dependencies"
    found: list[Path] = []

    def walk(d: Path) -> None:
        # Contain OSError per-directory: concurrent dependency work can delete a
        # dir between iterdir() and the child stats below. Doctrine (watch.py
        # _chain_tick): a tick failure must warn and skip, never crash the loop.
        try:
            children = sorted(d.iterdir())
        except OSError as exc:
            _say(f"· cannot scan {d} — {exc}; skipping")
            return
        for child in children:
            try:
                if not child.is_dir():
                    continue
                is_repo = (child / ".git").exists()
            except OSError as exc:
                _say(f"· cannot scan {child} — {exc}; skipping")
                continue
            if is_repo:
                if _HASH_DIR.match(child.name):
                    found.append(child)
                continue  # prune: tool clone or foreign repo
            walk(child)

    if root.is_dir():
        walk(root)
    return found


def _spawn(ctx: ToolContext, argv: list[str]) -> None:
    _say(f"→ {' '.join(argv)}")
    # Contain a missing/unlaunchable omc (FileNotFoundError is an OSError): the
    # loop must warn and continue, never crash (watch.py _chain_tick doctrine).
    try:
        cp = ctx.run(argv)
    except OSError as exc:
        _say(f"✗ cannot run {argv[0]}: {exc}")
        return
    if cp.returncode != 0:
        detail = (cp.stderr or "").strip()[:200]
        _say(f"✗ failed (exit {cp.returncode}): {detail}")
    else:
        _say("✓ done")


def _tick(ctx: ToolContext, attempted: set[tuple[str, str]]) -> int:
    """One reconciliation scan; returns the number of NEW actions taken.

    ``attempted`` caps every action at once per pass: a spawned action is
    recorded (success or not) and skipped on the pass's next scan, so a
    drain terminates even when an action fails or changes nothing."""
    try:
        manifest = load_manifest(ctx.home)
    except OmcError as exc:
        _say(f"✗ {exc}")
        return 0
    deps = manifest.get("dependencies", {})
    known = {
        entry.get("checkout") for dep in deps.values() for entry in dep.get("commits", {}).values()
    }
    actions = 0
    documents: list[str] = []
    for checkout in _scan_disk(ctx.home):
        if str(checkout) in known or ("adopt", str(checkout)) in attempted:
            continue
        cp = ctx.run([ctx.git_bin, "-C", str(checkout), "remote", "get-url", "origin"])
        url = (cp.stdout or "").strip()
        if cp.returncode != 0 or not url:
            _say(f"· cannot adopt {checkout} — no origin remote; skipping")
            continue
        # Parse to the credential-free clone URL: never let a token embedded in
        # the origin (https://oauth2:TOKEN@host/…) ride into the child argv or a
        # log line. An unparseable/file/local origin warns and skips — which also
        # stops re-spawning a doomed ensure on every tick.
        try:
            ref = parse_git_url(url)
        except OmcError as exc:
            _say(f"· cannot adopt {checkout} — {exc}; skipping")
            continue
        attempted.add(("adopt", str(checkout)))
        _spawn(
            ctx,
            [
                "omc",
                "internal",
                "dependency",
                "ensure",
                "--git",
                ref.url,
                "--commit",
                checkout.name,
            ],
        )
        actions += 1
    for key, dep in sorted(deps.items()):
        for commit, entry in dep.get("commits", {}).items():
            if not entry.get("indexed"):
                if ("ensure", f"{key}@{commit}") in attempted:
                    continue
                # Warn-and-skip a malformed entry rather than KeyError out of the
                # loop (watch.py _chain_tick doctrine: warn and skip, never crash).
                url = dep.get("url")
                if not url:
                    _say(f"· {key}@{commit} has no url in the manifest; skipping")
                    continue
                attempted.add(("ensure", f"{key}@{commit}"))
                _spawn(
                    ctx,
                    [
                        "omc",
                        "internal",
                        "dependency",
                        "ensure",
                        "--git",
                        url,
                        "--commit",
                        commit,
                    ],
                )
                actions += 1
            elif not entry.get("documented"):
                if ("document", f"{key}@{commit}") in attempted:
                    continue
                attempted.add(("document", f"{key}@{commit}"))
                documents.append(f"{key}@{commit}")
    actions += _document_batch(ctx, documents)
    return actions


class _DocumentJob:
    """One document child: percent parsed from its OMC_PROGRESS lines, full
    output teed to a log file, exit code from the pool future's ctx.stream."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        fd, self.log_path = tempfile.mkstemp(prefix="omc-dep-document-", suffix=".log")
        self.log = os.fdopen(fd, "w", encoding="utf-8")
        self.percent: int | None = None
        self.rc: int | None = None
        self._start = time.monotonic()
        self._spin = 0

    def feed(self, line: str) -> None:
        self.log.write(line + "\n")
        self.log.flush()
        if line.startswith("OMC_PROGRESS "):
            # malformed/foreign progress lines are ignored, never fatal
            try:
                pct = json.loads(line.split(" ", 1)[1])["percent"]
            except (ValueError, KeyError, TypeError):
                return
            if isinstance(pct, int) and 0 <= pct <= 100:
                self.percent = pct

    def row(self) -> str:
        spin = self._spin
        if self.percent is None:
            self._spin += 1
        return f"{self.ref} {render_bar(self.percent, time.monotonic() - self._start, spin=spin)}"

    def final_line(self) -> str:
        if self.rc == 0:
            return f"✓ done {self.ref} (log: {self.log_path})"
        return f"✗ failed (exit {self.rc}) {self.ref} — log: {self.log_path}"


def _document_batch(ctx: ToolContext, refs: list[str]) -> int:
    """Run the tick's document actions, up to _DOCUMENT_JOBS concurrently.
    Children are fully piped: each job's output tees to its own log file and
    its OMC_PROGRESS lines feed a per-dependency bar block (TTY only —
    headless runs get per-job start/finish lines instead)."""
    if not refs:
        return 0
    if len(refs) > 1:
        _say(f"→ documenting {len(refs)} dependencies (up to {_DOCUMENT_JOBS} in parallel)")
    jobs = {ref: _DocumentJob(ref) for ref in refs}
    tty = bool(getattr(sys.stderr, "isatty", lambda: False)())
    if not tty:
        for job in jobs.values():
            _say(f"→ omc internal dependency document --git {job.ref} — log: {job.log_path}")
    bar = MultiBarThread(lambda: [job.row() for job in jobs.values()])
    bar.start()

    def _document(ref: str) -> None:
        job = jobs[ref]
        # Contain a missing/unlaunchable omc (watch doctrine: warn+skip, never crash)
        try:
            job.rc = ctx.stream(
                ["omc", "internal", "dependency", "document", "--git", ref],
                on_line=job.feed,
            )
        except OSError as exc:
            # Recovery write must not itself crash the watch loop: a full disk
            # would make this write raise, escape pool.map and take the loop down
            # (violating warn-and-skip). Suppress — the exit code still records it.
            with contextlib.suppress(OSError):
                job.log.write(f"{exc}\n")
            job.rc = -1
        finally:
            try:
                job.log.close()
            except OSError:
                pass

    try:
        with ThreadPoolExecutor(max_workers=min(_DOCUMENT_JOBS, len(refs))) as pool:
            list(pool.map(_document, refs))
    finally:
        bar.stop()
    for job in jobs.values():
        _say(job.final_line())
    return len(refs)


def _manifest_status(home: Path) -> tuple[int, int, int]:
    """(dependencies, commits, remaining). Remaining counts manifest commit
    entries not yet indexed AND documented, plus disk checkouts the manifest
    doesn't know (a failed adoption must not read as completion)."""
    try:
        deps = load_manifest(home).get("dependencies", {})
    except OmcError:
        return 0, 0, 0
    commits = [e for dep in deps.values() for e in dep.get("commits", {}).values()]
    remaining = sum(1 for e in commits if not (e.get("indexed") and e.get("documented")))
    known = {e.get("checkout") for e in commits}
    remaining += sum(1 for checkout in _scan_disk(home) if str(checkout) not in known)
    return len(deps), len(commits), remaining


def _pass(ctx: ToolContext, *, once: bool) -> int:
    """Drain: re-tick immediately until a scan finds no new work, then say
    where things stand. Returns the number of actions the pass took."""
    attempted: set[tuple[str, str]] = set()
    total = 0
    while True:
        actions = _tick(ctx, attempted)
        total += actions
        if actions == 0:
            break
    if total:
        ndeps, ncommits, remaining = _manifest_status(ctx.home)
        if remaining == 0:
            dep_word = "dependency" if ndeps == 1 else "dependencies"
            commit_word = "commit" if ncommits == 1 else "commits"
            _say(
                "✓ Finished documenting all dependencies! "
                f"({ndeps} {dep_word}, {ncommits} {commit_word})"
            )
        else:
            retry = "re-run to retry" if once else "retrying next tick"
            _say(
                f"· pass complete — {remaining} item(s) still pending (see ✗ lines above); {retry}"
            )
    return total


def run_dependency_watch(ctx: ToolContext, *, interval: int = 30, once: bool = False) -> int:
    _say(
        f"→ watching {ctx.home} dependencies (every {interval}s) — Ctrl-C stops"
        if not once
        else f"→ reconciling {ctx.home} dependencies (single pass)"
    )
    last_idle = False
    try:
        while True:
            actions = _pass(ctx, once=once)
            if actions == 0:
                if not last_idle:
                    _say("· all dependencies reconciled — waiting for work")
                last_idle = True
            else:
                last_idle = False
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        _say("· stopped")
        return 0


def run_dependency_list(home: Path) -> int:
    """`omc dependency list` — human status table on stdout (read-only)."""
    try:
        deps = load_manifest(home).get("dependencies", {})
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    rows = [
        (
            key,
            commit[:7],
            entry.get("ref") or "-",
            "✓" if entry.get("indexed") else "✗",
            "✓" if entry.get("documented") else "✗",
            (entry.get("created") or "")[:10] or "-",
        )
        for key, dep in sorted(deps.items())
        for commit, entry in sorted(dep.get("commits", {}).items())
    ]
    if not rows:
        print("no dependencies cached yet — /omc:explain-dependency <name> <question> indexes one")
        return 0
    header = ("DEPENDENCY", "COMMIT", "REF", "INDEXED", "DOCUMENTED", "CREATED")
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]
    for row in (header, *rows):
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0
