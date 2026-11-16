"""Terminal progress bar: pure line rendering + a 1s redraw thread.

CLI presentation component. Stdlib-only ON PURPOSE: importing
omc.cli.progress_bar executes the cli package __init__, and trackers all
over src/omc import this module — it must never pull omc internals back in.
Trackers own progress state and satisfy a tiny protocol (render(), optional
refresh()); BarThread owns the redraw beat and is TTY-gated.
"""

from __future__ import annotations

import sys
import threading


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def render_bar(percent: int | None, elapsed: float, *, width: int = 18, spin: int = 0) -> str:
    """One uniform bar line for every omc progress mode:
    ``[====>             ]  30% (00:12:34)``.

    ``percent`` None renders the indeterminate bouncing ``<=>`` marker at
    position ``spin % (width - 3)`` — the CALLER advances ``spin`` per redraw
    (this function is pure)."""
    clock_part = f"({_format_elapsed(elapsed)})"
    if percent is None:
        pos = spin % (width - 3)
        bar = " " * pos + "<=>" + " " * (width - 3 - pos)
        return f"[{bar}]  --% {clock_part}"
    filled = round(width * percent / 100)
    bar = "=" * width if percent >= 100 else ("=" * filled + ">").ljust(width)[:width]
    # a fresh 0% still shows the arrow head: ">" alone at filled == 0
    return f"[{bar}] {percent:3d}% {clock_part}"


class BarThread:
    """Redraws the tracker once per second, in place (\\r). TTY-gated:
    constructing on a non-TTY yields a no-op — bar output must never land in
    captured logs. A tracker with a refresh() method gets it called before
    each redraw, so polling trackers share the redraw beat."""

    def __init__(self, tracker, out=None) -> None:
        self._tracker = tracker
        self._out = out if out is not None else sys.stderr
        self._enabled = bool(getattr(self._out, "isatty", lambda: False)())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        """True when the output stream is a TTY and redraws will happen.
        Callers that narrate instead of drawing off a TTY key off this."""
        return self._enabled

    def start(self) -> None:
        if not self._enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        refresh = getattr(self._tracker, "refresh", None)
        while not self._stop.wait(1.0):
            if refresh is not None:
                refresh()
            self._out.write("\r" + self._tracker.render())
            self._out.flush()

    def stop(self) -> None:
        if not self._enabled or self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._out.write("\r\x1b[K")  # clear the bar line before narration resumes
        self._out.flush()
