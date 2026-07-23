"""`omc dependency-watch` — keep ~/.omc dependency indexes + docs reconciled.

Foreground polling loop like watch.py (omc never creates daemons). Each tick
reconciles manifest <-> disk and delegates EVERY mutation to an
`omc internal dependency …` subprocess — the loop only scans and schedules
(which is exactly what the unit tests assert). Runs from anywhere: it
operates on ~/.omc, not on a project checkout.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from .dependency import load_manifest, parse_git_url
from .errors import OmcError
from .toolctx import ToolContext

_HASH_DIR = re.compile(r"\A[0-9a-f]{40}\Z")


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
        _say(f"✗ failed (exit {cp.returncode}): {(cp.stderr or '').strip()[:200]}")
    else:
        _say("✓ done")


def _tick(ctx: ToolContext) -> int:
    """One reconciliation pass; returns the number of actions taken."""
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
    for checkout in _scan_disk(ctx.home):
        if str(checkout) in known:
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
                # Warn-and-skip a malformed entry rather than KeyError out of the
                # loop (watch.py _chain_tick doctrine: warn and skip, never crash).
                url = dep.get("url")
                if not url:
                    _say(f"· {key}@{commit} has no url in the manifest; skipping")
                    continue
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
                _spawn(
                    ctx,
                    ["omc", "internal", "dependency", "document", "--git", f"{key}@{commit}"],
                )
                actions += 1
    return actions


def run_dependency_watch(ctx: ToolContext, *, interval: int = 30, once: bool = False) -> int:
    _say(
        f"→ watching {ctx.home} dependencies (every {interval}s) — Ctrl-C stops"
        if not once
        else f"→ reconciling {ctx.home} dependencies (single pass)"
    )
    last_idle = False
    try:
        while True:
            actions = _tick(ctx)
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
