"""Wiki-run supervision plumbing shared by dependency docs and the project wiki.

`ToolContext.run_supervised` kills a `gitnexus wiki` child only after
`_WIKI_STALL_SECONDS` with NO progress, where progress = the heartbeat token
changed OR output bytes arrived. `PageCountTracker` is that heartbeat: it reads
progress from DISK (gitnexus's own bar is TTY-gated and emits nothing through a
pipe). The heartbeat is indeterminate until gitnexus has written
first_module_tree.json (the grouping phase); output bytes carry liveness there.
`GitNexusProgress` is the second progress source: GitNexus's own lines, once it speaks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from subprocess import CompletedProcess

# Liveness window for wiki generation: the run may take 40+ minutes, but 300 s
# with zero progress (no new page on disk, no child output) marks a wedge.
# GitNexus heartbeats on stderr every 30 s while an LLM call is in flight
# (fork generator.ts/wiki-progress.ts), so silence this long means a wedge
# OUTSIDE any LLM call. A hung LLM call itself is deliberately NOT bounded by
# omc: GitNexus treats a timed-out call as a degraded result (directory-grouping
# fallback, `Failed: <page>`) and exits 0, so a `--timeout` would turn a hang
# into a silently degraded wiki marked fresh — user decision 2026-10-02 (spec
# 2026-10-01 fix-doc-false-stall-gitnexus-progress §4.2.3).
_WIKI_STALL_SECONDS = 300.0

# One disk poll per second drives BOTH the stall-guard heartbeat and progress
# reporting; monkeypatchable in tests.
_WIKI_POLL_SECONDS = 1.0

# Prefix of the progress lines the fork's wiki command prints on stderr when
# piped (fork src/cli/wiki-progress.ts): `GITNEXUS_PROGRESS {"phase","percent","detail"}`.
PROGRESS_PREFIX = "GITNEXUS_PROGRESS "


def wiki_failure_excerpt(cp: CompletedProcess[str], redact: Callable[[str], str]) -> str:
    """Show the useful tail of a failed wiki run, with secrets and progress removed."""
    lines = (
        line.strip()
        for stream in (cp.stderr, cp.stdout)
        for line in redact(stream or "").splitlines()
    )
    return "\n".join(line for line in lines if line and not line.startswith(PROGRESS_PREFIX))[-400:]


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


class GitNexusProgress:
    """GitNexus's own whole-run progress, read from the `GITNEXUS_PROGRESS {…}`
    lines its wiki command prints on stderr when piped (spec §4.1.1). Shaped
    like buildprogress.ProgressTracker so cli.progress_bar.BarThread can drive
    it: feed() / percent / render(). Single attribute writes under the GIL —
    fed on run_supervised's reader thread, read on the supervising or bar
    thread. Pure data source: no I/O, no rendering policy of its own.

    Malformed or foreign lines (pino records share the stream) are ignored,
    never fatal: progress plumbing must never crash a documentation run."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        fallback: PageCountTracker | None = None,
    ) -> None:
        self._clock = clock
        self._start = clock()
        self._percent: int | None = None
        self._phase = ""
        self._detail = ""
        self._spin = 0
        # The bar's percent before GitNexus speaks (and for an older GitNexus
        # that never does): the disk page count — same precedence as the
        # OMC_PROGRESS path in dependency.run_document.
        self._fallback = fallback

    def refresh(self) -> None:
        """BarThread calls this before each redraw; keep the fallback current."""
        if self._fallback is not None:
            self._fallback.refresh()

    def feed(self, line: str) -> None:
        if not line.startswith(PROGRESS_PREFIX):
            return
        try:
            data = json.loads(line[len(PROGRESS_PREFIX) :])
            pct = data["percent"]
        except (ValueError, KeyError, TypeError):
            return  # not JSON, not an object, or no percent
        # bool is an int subclass; a float is not a percent either
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
            return
        self._percent = pct
        self._phase = str(data.get("phase", ""))
        self._detail = str(data.get("detail", ""))

    @property
    def percent(self) -> int | None:
        return self._percent

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def detail(self) -> str:
        return self._detail

    def render(self, now: float | None = None, width: int = 18) -> str:
        # Lazy import: a module-level `from .cli.progress_bar import render_bar`
        # is an import cycle (omc.cli → start → gitnexus → wikirun). Verified.
        from .cli.progress_bar import render_bar

        percent = self._percent
        if percent is None and self._fallback is not None:
            percent = self._fallback.percent
        spin = self._spin
        if percent is None:
            self._spin += 1  # bounce advances one slot per redraw, like ProgressTracker
        elapsed = (self._clock() if now is None else now) - self._start
        return render_bar(percent, elapsed, width=width, spin=spin)
