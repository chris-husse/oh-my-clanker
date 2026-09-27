"""Wiki-run supervision plumbing shared by dependency docs and the project wiki.

`ToolContext.run_supervised` kills a `gitnexus wiki` child only after
`_WIKI_STALL_SECONDS` with NO progress, where progress = the heartbeat token
changed OR output bytes arrived. `PageCountTracker` is that heartbeat: it reads
progress from DISK (gitnexus's own bar is TTY-gated and emits nothing through a
pipe). The heartbeat is indeterminate until gitnexus has written
first_module_tree.json (the grouping phase); output bytes carry liveness there.
"""

from __future__ import annotations

import json
from pathlib import Path

# Liveness window for wiki generation: the run may take 40+ minutes, but 300s
# with zero progress (no new page on disk, no child output) marks a wedge.
_WIKI_STALL_SECONDS = 300.0

# One disk poll per second drives BOTH the stall-guard heartbeat and progress
# reporting; monkeypatchable in tests.
_WIKI_POLL_SECONDS = 1.0


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
