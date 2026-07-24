"""Build-progress engine: pure line-fed percent extraction.

Consumed by `omc watch --auto-build` (live bar while a build stage streams)
and by `omc internal build-progress <logfile>` (standalone follow-mode
viewer, Task 5). Parsers are an ordered registry — adding a build system is
one entry + tests. Latest match wins; no match yet renders an indeterminate
bouncing bar with elapsed time only. Bar rendering itself lives in
`omc.cli.progress_bar`; this module keeps the parsers, `follow_log`, and the
sentinel.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .cli.progress_bar import render_bar


# Ordered registry: (name, pattern, to_percent). First matching parser in
# registry order wins for a given line; across lines the latest match wins.
def _ratio(m: re.Match) -> int | None:
    done, total = int(m.group(1)), int(m.group(2))
    if total <= 0 or done > total:
        return None
    return round(100 * done / total)


def _percent(m: re.Match) -> int | None:
    value = int(m.group(1))
    return value if 0 <= value <= 100 else None


PARSERS: list[tuple[str, re.Pattern[str], Callable[[re.Match], int | None]]] = [
    ("cargo", re.compile(r"(\d+)/(\d+)"), _ratio),
    ("pytest", re.compile(r"\[\s*(\d{1,3})%\]"), _percent),
    ("generic", re.compile(r"\b(\d{1,3})%"), _percent),
]

_GRACE = 5.0  # seconds follow_log waits for the log file to appear before erroring

_SENTINEL_FMT = "--- omc: stage finished (rc {rc}) ---"
SENTINEL_RE = re.compile(r"^--- omc: stage finished \(rc (-?\d+|\?)\) ---$")


def sentinel_line(rc: int | None) -> str:
    return _SENTINEL_FMT.format(rc="?" if rc is None else rc)


class ProgressTracker:
    """Feed lines in; read percent/elapsed/bar out. No I/O, thread-tolerant
    (single attribute writes under the GIL; feed and render may run on
    different threads)."""

    def __init__(
        self,
        start: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._start = clock() if start is None else start
        self._percent: int | None = None
        self._spin = 0

    def feed(self, line: str) -> None:
        for _name, pattern, to_percent in PARSERS:
            m = pattern.search(line)
            if m:
                value = to_percent(m)
                if value is not None:
                    self._percent = value
                return  # first matching parser in registry order owns the line

    @property
    def percent(self) -> int | None:
        return self._percent

    def elapsed(self, now: float | None = None) -> float:
        return (self._clock() if now is None else now) - self._start

    def render(self, now: float | None = None, width: int = 18) -> str:
        spin = self._spin
        if self._percent is None:
            self._spin += 1  # bounce advances one slot per redraw, as before
        return render_bar(self._percent, self.elapsed(now), width=width, spin=spin)


def follow_log(path_str: str, *, poll: float = 0.5, out=None) -> int:
    """`omc internal build-progress <logfile>`: follow a live stage log
    tail -f-style, rendering the bar in place on stderr (TTY only). Exits 0
    at the sentinel line or on Ctrl-C; 2 if the file never appears (short
    grace wait so it can be started just before the stage)."""
    stream = out if out is not None else sys.stderr
    path = Path(path_str)
    deadline = time.monotonic() + _GRACE
    while not path.exists():
        if time.monotonic() >= deadline:
            print(f"error: no such log file: {path}", file=sys.stderr)
            return 2
        time.sleep(poll)
    stat = path.stat()
    birth = getattr(stat, "st_birthtime", stat.st_ctime)
    tracker = ProgressTracker(start=birth, clock=time.time)
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            pending = ""  # a writer mid-line: buffer the fragment, never feed torn lines
            while True:
                raw = fh.readline()
                if raw:
                    if raw.endswith("\n"):
                        line = (pending + raw).rstrip("\n")
                        pending = ""
                        tracker.feed(line)
                        if SENTINEL_RE.match(line):
                            return 0
                    else:
                        pending += raw
                        time.sleep(poll)
                else:
                    time.sleep(poll)
                if is_tty:
                    stream.write("\r" + tracker.render())
                    stream.flush()
    except KeyboardInterrupt:
        return 0
    finally:
        if is_tty:
            stream.write("\r\x1b[K")
            stream.flush()
