# Dependency Watch Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `omc dependency watch` documentation runs visibly alive (progress bar + resume line) and wedge-proof (kill after 300 s of no progress), per `docs/superpowers/specs/2026-07-23-fix-dependency-watch-never-completes-design.md`.

**Architecture:** A stdlib-only progress-bar component (`src/omc/cli/progress_bar.py`: pure `render_bar` + TTY-gated `BarThread`) fed by a disk-polling `PageCountTracker` (wiki page counts); a `ToolContext.run_supervised` liveness guard (kill process group after 300 s without heartbeat/output progress); `run_document` wires all three; the watch passes document children's stderr through so the bar reaches the terminal.

**Tech Stack:** Python 3.14, stdlib only (subprocess/threading/json/pathlib), pytest, ruff.

## Global Constraints

- No new dependencies — everything is stdlib (spec: keep `progress_bar.py` importable dependency-free).
- One uniform bar format for every mode: `[====>       ]  30% (00:12:34)`; counts are internal only (they feed the percent), never rendered.
- Existing golden render tests (`tests/unit/test_buildprogress.py:65,71`) must keep passing byte-for-byte.
- Machine contract unchanged: the `OMC_DEPENDENCY {json}` verdict stays on stdout; progress/narration is stderr only.
- POSIX-only process-group kill (`os.killpg`) — matches the rest of `src/omc/` (no win32 handling anywhere).
- Watch doctrine: progress plumbing degrades (indeterminate bar, contained OSErrors), it never crashes a loop or a run.
- Gate for every task: `just build` (ruff format --check, ruff check, `pytest -m "not e2e" -q`) from the repo root.

---

### Task 1: Convert `cli.py` into the `cli/` package

**Model:** standard coding tier

**Files:**
- Move: `src/omc/cli.py` → `src/omc/cli/__init__.py`
- Test: existing suite (`tests/unit/test_cli.py`, `test_configure.py`, `test_depwatch.py` import `omc.cli`)

**Interfaces:**
- Consumes: nothing new.
- Produces: package `omc.cli` whose `__init__` exports `main` and `build_parser` exactly as before; the `omc.cli:main` entry point in `pyproject.toml` keeps resolving. Task 2 adds `src/omc/cli/progress_bar.py` next to it.

- [ ] **Step 1: Move the module into a package**

```bash
mkdir src/omc/cli_tmp && git mv src/omc/cli.py src/omc/cli_tmp/__init__.py && git mv src/omc/cli_tmp src/omc/cli
```

(Two-step because `git mv src/omc/cli.py src/omc/cli/__init__.py` cannot create the directory that shadows the file's own name in one go. If your git handles `mkdir src/omc/cli && git mv src/omc/cli.py src/omc/cli/__init__.py`, that's equivalent.)

- [ ] **Step 2: Shift every single-dot relative import one level up**

The file moved one package level down, so `.X` now resolves inside `omc.cli` instead of `omc`. Edit `src/omc/cli/__init__.py`: every `from . import X` becomes `from .. import X`, every `from .mod import X` becomes `from ..mod import X`. That covers the module-top imports (`from .. import __version__`, `..config`, `..errors`, `..start`, `..toolctx`) and ALL lazy in-function imports (`..internal`, `..installsrc` ×2, `..watch`, `..depwatch` ×2, `..configure`, `..installer` ×3 — and any others).

- [ ] **Step 3: Verify no single-dot imports remain**

Run: `grep -n "from \.[a-z]" src/omc/cli/__init__.py`
Expected: no output (every relative import is now `from ..`).

Run: `grep -c "from \.\." src/omc/cli/__init__.py`
Expected: 15 (±1 — matches the count of relative imports found in Step 2).

- [ ] **Step 4: Run the gate**

Run: `just build`
Expected: ruff clean, all unit tests PASS (the suite imports `omc.cli` heavily — `tests/unit/test_cli.py`, `test_configure.py`, `test_depwatch.py:282,294`).

- [ ] **Step 5: Commit**

```bash
git add -A src/omc/cli
git commit -m "refactor: convert omc.cli module into a package"
```

---

### Task 2: `progress_bar` component — `render_bar` + `BarThread`

**Model:** heavy coding tier

**Files:**
- Create: `src/omc/cli/progress_bar.py`
- Modify: `src/omc/buildprogress.py` (render delegates to `render_bar`; `_format_elapsed` moves out)
- Modify: `src/omc/watch.py` (delete `_BarThread` class at lines 71–99, import `BarThread`, drop the now-unused `import threading`)
- Test: `tests/unit/test_progress_bar.py` (new), `tests/unit/test_buildprogress.py` (must pass unchanged)

**Interfaces:**
- Consumes: nothing outside stdlib (hard requirement — importing `omc.cli.progress_bar` executes `cli/__init__.py`; the component itself must not import omc modules).
- Produces:
  - `render_bar(percent: int | None, elapsed: float, *, width: int = 18, spin: int = 0) -> str`
  - `class BarThread` with `__init__(self, tracker, out=None)`, `start() -> None`, `stop() -> None`; tracker protocol: `render(now=None) -> str` required, `refresh() -> None` optional (called before each redraw when present). TTY-gated on `out.isatty()` (default `sys.stderr`); non-TTY construction yields a no-op.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_progress_bar.py`:

```python
import io
import time

from omc.cli.progress_bar import BarThread, render_bar


def test_render_bar_matches_build_golden():
    assert render_bar(21, 48802.0) == "[====>             ]  21% (13:33:22)"


def test_render_bar_complete():
    assert render_bar(100, 61.0) == "[==================] 100% (00:01:01)"


def test_render_bar_fresh_zero_shows_arrow_head():
    assert render_bar(0, 0.0).startswith("[>")


def test_render_bar_indeterminate_bounces_with_spin():
    first = render_bar(None, 0.0, spin=0)
    second = render_bar(None, 0.0, spin=1)
    assert "<=>" in first and "<=>" in second and first != second
    assert " --% " in first


class _NonTTY(io.StringIO):
    pass  # StringIO.isatty() is False


class _TTY(io.StringIO):
    def isatty(self):
        return True


class _Tracker:
    def __init__(self):
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def render(self, now=None):
        return "RENDERED"


def test_bar_thread_is_noop_without_tty():
    out = _NonTTY()
    bar = BarThread(_Tracker(), out=out)
    bar.start()
    bar.stop()
    assert out.getvalue() == ""


def test_bar_thread_refreshes_and_redraws_on_tty():
    out = _TTY()
    tracker = _Tracker()
    bar = BarThread(tracker, out=out)
    bar.start()
    time.sleep(1.3)  # one 1s redraw beat
    bar.stop()
    assert tracker.refreshed >= 1
    assert "\rRENDERED" in out.getvalue()
    assert out.getvalue().endswith("\r\x1b[K")  # stop clears the bar line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_progress_bar.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'omc.cli.progress_bar'`

- [ ] **Step 3: Create the component**

Create `src/omc/cli/progress_bar.py`:

```python
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
```

- [ ] **Step 4: Delegate `buildprogress` rendering to the component**

In `src/omc/buildprogress.py`: add `from .cli.progress_bar import render_bar` to the imports; delete the `_format_elapsed` function (lines 49–53); replace the whole `ProgressTracker.render` method (lines 87–99) with:

```python
    def render(self, now: float | None = None, width: int = 18) -> str:
        spin = self._spin
        if self._percent is None:
            self._spin += 1  # bounce advances one slot per redraw, as before
        return render_bar(self._percent, self.elapsed(now), width=width, spin=spin)
```

Update the module docstring's first paragraph to say rendering lives in `omc.cli.progress_bar` (this module keeps the parsers, `follow_log`, and the sentinel).

- [ ] **Step 5: Point `watch.py` at the component**

In `src/omc/watch.py`: delete the `_BarThread` class (lines 71–99); add `from .cli.progress_bar import BarThread` to the imports; change the construction site (line 193) from `bar = _BarThread(tracker)` to `bar = BarThread(tracker)`; remove `import threading` if nothing else in the file uses it (it doesn't after the deletion).

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit/test_progress_bar.py tests/unit/test_buildprogress.py -q`
Expected: ALL PASS — the buildprogress goldens (`[====>             ]  21% (13:33:22)`, `[==================] 100% (00:01:01)`) unchanged.

- [ ] **Step 7: Run the gate**

Run: `just build`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/omc/cli/progress_bar.py src/omc/buildprogress.py src/omc/watch.py tests/unit/test_progress_bar.py
git commit -m "feat: shared progress-bar component (render_bar + BarThread) in omc.cli"
```

---

### Task 3: `PageCountTracker` — wiki progress from disk

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/dependency.py` (new class after `save_manifest`, around line 131)
- Test: `tests/unit/test_dependency.py` (append)

**Interfaces:**
- Consumes: `render_bar` from Task 2 (`from .cli.progress_bar import render_bar`).
- Produces (Task 5 relies on these exact names):
  - `class PageCountTracker` with `__init__(self, wiki_dir: Path, clock: Callable[[], float] = time.monotonic)`
  - `refresh() -> None` — re-reads disk; all I/O errors contained (degrade to indeterminate, never raise)
  - `state() -> tuple[int | None, int]` — `(total, done)`; the heartbeat comparison token
  - `beat() -> tuple[int | None, int]` — `refresh()` then `state()`; THE heartbeat callable for `run_supervised` (in non-TTY runs no BarThread exists to refresh — the heartbeat must self-refresh or every headless run would false-stall)
  - `percent -> int | None` property — None until the module tree exists; clamped to 100
  - `render(now=None, width=18) -> str` — satisfies the BarThread tracker protocol

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dependency.py`:

```python
# ─── PageCountTracker ────────────────────────────────────────────────


def _tree(tmp_path, nodes):
    (tmp_path / "first_module_tree.json").write_text(json.dumps(nodes))


def test_tracker_indeterminate_without_tree(tmp_path):
    from omc.dependency import PageCountTracker

    t = PageCountTracker(tmp_path, clock=lambda: 0.0)
    t.refresh()
    assert t.percent is None
    assert t.state() == (None, 0)
    assert " --% " in t.render(now=0.0)


def test_tracker_counts_modules_pages_and_overview(tmp_path):
    from omc.dependency import PageCountTracker

    # 3 modules (a, its child b, c) + 1 overview page = 4 total
    _tree(tmp_path, [{"slug": "a", "children": [{"slug": "b"}]}, {"slug": "c"}])
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.md").write_text("x")
    t = PageCountTracker(tmp_path, clock=lambda: 0.0)
    assert t.beat() == (4, 2)
    assert t.percent == 50
    assert t.render(now=0.0) == "[=========>        ]  50% (00:00:00)"


def test_tracker_corrupt_tree_degrades_to_indeterminate(tmp_path):
    from omc.dependency import PageCountTracker

    _tree(tmp_path, [{"slug": "a"}])
    (tmp_path / "first_module_tree.json").write_text("{not json")
    t = PageCountTracker(tmp_path, clock=lambda: 0.0)
    t.refresh()  # must not raise
    assert t.percent is None


def test_tracker_clamps_overshoot(tmp_path):
    from omc.dependency import PageCountTracker

    _tree(tmp_path, [{"slug": "a"}])  # total = 1 module + 1 overview = 2
    for name in ("a.md", "overview.md", "stale-extra.md"):
        (tmp_path / name).write_text("x")
    t = PageCountTracker(tmp_path, clock=lambda: 0.0)
    t.refresh()
    assert t.percent == 100  # 3 pages / 2 expected — clamped, never >100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dependency.py -q -k tracker`
Expected: FAIL with `ImportError: cannot import name 'PageCountTracker'`

- [ ] **Step 3: Implement the tracker**

In `src/omc/dependency.py`: add `import time` and `from collections.abc import Callable` to the imports, plus `from .cli.progress_bar import render_bar`. Insert after `save_manifest` (around line 131):

```python
class PageCountTracker:
    """Wiki-generation progress read from DISK, not child output (gitnexus's
    own bar is TTY-gated and emits nothing through a pipe): total = modules in
    first_module_tree.json + 1 overview page, done = *.md pages present.
    Missing/corrupt state degrades to indeterminate (percent None) — progress
    plumbing must never crash a documentation run."""

    def __init__(self, wiki_dir: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self._dir = wiki_dir
        self._clock = clock
        self._start = clock()
        self._done = 0
        self._total: int | None = None
        self._spin = 0

    @staticmethod
    def _count(nodes: object) -> int:
        if not isinstance(nodes, list):
            return 0
        return sum(
            1 + PageCountTracker._count(node.get("children"))
            for node in nodes
            if isinstance(node, dict)
        )

    def refresh(self) -> None:
        try:
            tree = json.loads((self._dir / "first_module_tree.json").read_text())
            modules = self._count(tree)
            done = sum(1 for p in self._dir.iterdir() if p.suffix == ".md")
        except (OSError, json.JSONDecodeError):
            self._total = None
            return
        self._total = modules + 1 if modules else None  # +1: the final overview page
        self._done = done

    def state(self) -> tuple[int | None, int]:
        """Progress token — run_supervised compares successive values."""
        return (self._total, self._done)

    def beat(self) -> tuple[int | None, int]:
        """Heartbeat for run_supervised: self-refreshing, because in non-TTY
        runs no BarThread exists to call refresh() for us."""
        self.refresh()
        return self.state()

    @property
    def percent(self) -> int | None:
        if not self._total:
            return None
        return min(100, round(100 * self._done / self._total))

    def render(self, now: float | None = None, width: int = 18) -> str:
        spin = self._spin
        if self.percent is None:
            self._spin += 1
        elapsed = (self._clock() if now is None else now) - self._start
        return render_bar(self.percent, elapsed, width=width, spin=spin)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dependency.py -q -k tracker`
Expected: 4 PASS.

- [ ] **Step 5: Run the gate and commit**

Run: `just build` — expected PASS. Then:

```bash
git add src/omc/dependency.py tests/unit/test_dependency.py
git commit -m "feat: PageCountTracker — dependency wiki progress from disk state"
```

---

### Task 4: `ToolContext.run_supervised` — the stall guard

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/toolctx.py` (new method after `run`, before `stream`; add `import signal`, `import time`)
- Test: `tests/unit/test_toolctx.py` (exists — append; it already imports `ToolContext`. Add the `_ctx` helper below if no equivalent exists, keeping any imports it needs at the top of the file)

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 5 relies on this exact signature):
  - `run_supervised(argv, *, heartbeat: Callable[[], object], stall_after: float = 300.0, poll: float = 1.0, cwd=None, extra_env=None) -> tuple[subprocess.CompletedProcess[str], bool]` — captured like `run()`, but killed (whole process group, SIGKILL) only after `stall_after` seconds with NO progress; progress = heartbeat token changed OR any stdout/stderr bytes arrived. Returns `(completed, stalled)`. No overall deadline.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_toolctx.py` (merge the imports with the file's existing ones):

```python
import os
import time

from omc.toolctx import ToolContext


def _ctx(tmp_path):
    return ToolContext(home=tmp_path / "home", env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]})


def test_supervised_kills_stalled_child(tmp_path):
    ctx = _ctx(tmp_path)
    t0 = time.monotonic()
    cp, stalled = ctx.run_supervised(
        ["sleep", "30"], heartbeat=lambda: 0, stall_after=0.4, poll=0.05
    )
    assert stalled is True
    assert cp.returncode != 0
    assert time.monotonic() - t0 < 10  # killed long before sleep 30 finishes


def test_supervised_output_counts_as_progress(tmp_path):
    ctx = _ctx(tmp_path)
    script = "for i in 1 2 3 4 5 6; do echo tick; sleep 0.2; done"
    cp, stalled = ctx.run_supervised(
        ["sh", "-c", script], heartbeat=lambda: 0, stall_after=0.5, poll=0.05
    )
    assert stalled is False
    assert cp.returncode == 0
    assert cp.stdout.count("tick") == 6


def test_supervised_heartbeat_counts_as_progress(tmp_path):
    ctx = _ctx(tmp_path)
    beats = iter(range(10_000))
    cp, stalled = ctx.run_supervised(
        ["sleep", "1.2"], heartbeat=lambda: next(beats), stall_after=0.5, poll=0.05
    )
    assert stalled is False
    assert cp.returncode == 0


def test_supervised_broken_heartbeat_never_kills_an_active_child(tmp_path):
    ctx = _ctx(tmp_path)

    def boom():
        raise RuntimeError("heartbeat exploded")

    script = "for i in 1 2 3 4 5 6; do echo tick; sleep 0.2; done"
    cp, stalled = ctx.run_supervised(
        ["sh", "-c", script], heartbeat=boom, stall_after=0.5, poll=0.05
    )
    assert stalled is False  # output alone kept it alive; heartbeat error contained
    assert cp.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_toolctx.py -q -k supervised`
Expected: FAIL with `AttributeError: 'ToolContext' object has no attribute 'run_supervised'`

- [ ] **Step 3: Implement**

In `src/omc/toolctx.py`: add `import signal` and `import time` to the imports. Insert this method on `ToolContext` between `run` and `stream`:

```python
    def run_supervised(
        self,
        argv: Sequence[str],
        *,
        heartbeat: Callable[[], object],
        stall_after: float = 300.0,
        poll: float = 1.0,
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        """Run argv captured like run(), supervised for LIVENESS, not deadline:
        the child (its whole process group — LLM grandchildren included) is
        killed only after ``stall_after`` seconds with NO progress, where
        progress = the heartbeat() token changed OR any output bytes arrived.
        No overall timeout — a healthy wiki run may take 40+ minutes.
        Returns (completed, stalled). POSIX-only (killpg), like this module.

        heartbeat runs on the supervising thread once per ``poll``; its
        exceptions count as "no change" — a broken heartbeat must neither
        kill a healthy child nor crash the supervisor."""
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "errors": "replace",
            "start_new_session": True,  # own process group so killpg is precise
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        proc = subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - argv list, no shell
        chunks: dict[str, list[str]] = {"out": [], "err": []}
        activity = [0]  # bytes seen across both pipes; GIL-atomic appends/adds

        def pump(pipe, key: str) -> None:
            try:
                for raw in pipe:
                    chunks[key].append(raw)
                    activity[0] += len(raw)
            finally:
                pipe.close()

        readers = [
            threading.Thread(target=pump, args=(p, k), daemon=True)
            for p, k in ((proc.stdout, "out"), (proc.stderr, "err"))
        ]
        for t in readers:
            t.start()
        stalled = False
        last_token: object = object()  # sentinel: never equal to a real token
        last_activity = -1
        stamp = time.monotonic()
        while proc.poll() is None:
            time.sleep(poll)
            try:
                token = heartbeat()
            except Exception:  # noqa: BLE001 - heartbeat failure is not the child's fault
                token = last_token
            if token != last_token or activity[0] != last_activity:
                last_token, last_activity = token, activity[0]
                stamp = time.monotonic()
            elif time.monotonic() - stamp >= stall_after:
                stalled = True
                # SIGKILL, not TERM-then-KILL: the gitnexus child tree installs no
                # graceful handlers (plain fs.writeFile throughout), so TERM buys
                # nothing. A page truncated mid-write is the same pre-existing risk
                # a user Ctrl-C has today; resume-skip tolerates it.
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass  # child won the race and exited; the stall verdict stands
                break
        for t in readers:
            t.join()
        rc = proc.wait()
        return (
            subprocess.CompletedProcess(
                list(argv), rc, "".join(chunks["out"]), "".join(chunks["err"])
            ),
            stalled,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_toolctx.py -q -k supervised`
Expected: 4 PASS (allow ~5 s total — the tests run real children with sub-second stall windows).

- [ ] **Step 5: Run the gate and commit**

Run: `just build` — expected PASS. Then:

```bash
git add src/omc/toolctx.py tests/unit/test_toolctx.py
git commit -m "feat: ToolContext.run_supervised — kill process group after no-progress stall"
```

---

### Task 5: Wire `run_document` — resume line, bar, stall guard

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/dependency.py:run_document` (the wiki spawn block, currently lines 306–320) and the module imports
- Test: `tests/unit/test_dependency.py` (append; existing document tests must pass unchanged)

**Interfaces:**
- Consumes: `PageCountTracker` (Task 3: `state()`, `beat`, `refresh`), `BarThread` (Task 2), `ctx.run_supervised` (Task 4).
- Produces: `_WIKI_STALL_SECONDS = 300` module constant (tests monkeypatch it); resume line format `· resuming — {done}/{total} pages already on disk` on stderr; stall error `error: gitnexus wiki stalled — no progress for 300s; killed` on stderr with exit 1 and no manifest flip.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dependency.py`:

```python
def test_document_announces_resume_when_pages_exist(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    # with_wiki=False: _seed_indexed's default writes overview.md, which would
    # skew the done-count; build the wiki dir by hand instead.
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])  # 3 modules + overview = 4
    (wiki / "a.md").write_text("x")
    (wiki / "b.md").write_text("x")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    assert "· resuming — 2/4 pages already on disk" in capsys.readouterr().err


def test_document_no_resume_line_on_fresh_run(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)  # wiki dir exists with overview.md but no module tree
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    assert "resuming" not in capsys.readouterr().err


def test_document_stall_kill_exits_1_and_keeps_documented_false(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", 0.3)
    # Replace the node stub with a silent sleeper: no output, no page writes.
    node = tmp_path / "bin" / "node"
    node.write_text("#!/bin/sh\nsleep 30\n")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    err = capsys.readouterr().err
    assert "stalled — no progress" in err
    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["documented"] is False
```

(`_tree` is the Task 3 helper; give it `wiki.mkdir(parents=True, exist_ok=True)` semantics — adjust the helper to create the directory: `def _tree(dirpath, nodes): dirpath.mkdir(parents=True, exist_ok=True); (dirpath / "first_module_tree.json").write_text(json.dumps(nodes))`. `load_manifest` is already imported at the top of the test file — check, and import locally if not.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dependency.py -q -k "resume or stall"`
Expected: FAIL — no resume line printed, no `_WIKI_STALL_SECONDS` attribute.

- [ ] **Step 3: Implement the wiring**

In `src/omc/dependency.py`: add `from .cli.progress_bar import BarThread` to the imports and a module constant near `PIN_BRANCH`:

```python
# Liveness window for wiki generation: the run may take 40+ minutes, but 300s
# with zero progress (no new page on disk, no child output) marks a wedge.
_WIKI_STALL_SECONDS = 300.0
```

Replace the wiki spawn in `run_document` — currently:

```python
    cp = ctx.run(gitnexus_argv(ctx, *wiki_args), cwd=dest)
    wiki = dest / ".gitnexus" / "wiki"
    if cp.returncode != 0 or not wiki.is_dir():
```

with:

```python
    wiki = dest / ".gitnexus" / "wiki"
    tracker = PageCountTracker(wiki)
    tracker.refresh()
    total, done = tracker.state()
    if total and done:
        print(f"· resuming — {done}/{total} pages already on disk", file=sys.stderr, flush=True)
    bar = BarThread(tracker)  # TTY-gated: headless callers see no bar
    bar.start()
    try:
        cp, stalled = ctx.run_supervised(
            gitnexus_argv(ctx, *wiki_args),
            cwd=dest,
            heartbeat=tracker.beat,  # self-refreshing: no BarThread on non-TTY
            stall_after=_WIKI_STALL_SECONDS,
        )
    finally:
        bar.stop()
    if stalled:
        print(
            "error: gitnexus wiki stalled — no progress for "
            f"{int(_WIKI_STALL_SECONDS)}s; killed",
            file=sys.stderr,
        )
        return 1
    if cp.returncode != 0 or not wiki.is_dir():
```

(`state()` returns `(total, done)` — mind the unpack order. Everything after the guard — error print, mirror, manifest flip, verdict — stays byte-identical.)

- [ ] **Step 4: Run the dependency suite**

Run: `uv run pytest tests/unit/test_dependency.py -q`
Expected: ALL PASS — including the pre-existing document tests (the node stub exits fast, so `run_supervised` behaves exactly like `run` for them).

- [ ] **Step 5: Run the gate and commit**

Run: `just build` — expected PASS. Then:

```bash
git add src/omc/dependency.py tests/unit/test_dependency.py
git commit -m "feat: dependency document runs show resume state, progress bar, stall guard"
```

---

### Task 6: Watch passthrough — `capture="stdout"` + `_spawn` wiring

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/toolctx.py:run` (capture mode), `src/omc/depwatch.py:_spawn` and its document call site
- Test: `tests/unit/test_toolctx.py`, `tests/unit/test_depwatch.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ToolContext.run(..., capture: bool | Literal["stdout"] = True)` — `"stdout"` pipes stdout (text), inherits stderr, stdin DEVNULL; `depwatch._spawn(ctx, argv, *, passthrough: bool = False)` — document actions pass `passthrough=True`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_toolctx.py`:

```python
def test_run_capture_stdout_inherits_stderr(tmp_path, capfd):
    ctx = _ctx(tmp_path)
    cp = ctx.run(["sh", "-c", "echo OUT; echo ERR >&2"], capture="stdout")
    assert cp.stdout == "OUT\n"
    assert cp.stderr is None  # not piped — inherited
    assert "ERR" in capfd.readouterr().err  # child wrote to OUR stderr fd
```

Append to `tests/unit/test_depwatch.py`:

```python
def test_document_child_stderr_reaches_terminal(tmp_path, capfd):
    """Document actions run with stderr passed through (the progress bar and
    resume line render live); ensure actions stay fully captured."""
    ctx, calls = _ctx(tmp_path)
    omc = tmp_path / "bin" / "omc"
    omc.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\necho "CHILD-PROGRESS" >&2\nexit 0\n')
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    assert "CHILD-PROGRESS" in capfd.readouterr().err


def test_document_failure_points_at_output_above(tmp_path, capfd):
    ctx, calls = _ctx(tmp_path)
    omc = tmp_path / "bin" / "omc"
    omc.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 3\n')
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    assert "✗ failed (exit 3): see output above" in capfd.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_toolctx.py -k stdout tests/unit/test_depwatch.py -k "terminal or above" -q`
Expected: FAIL — `capture="stdout"` currently truthy → full capture (cp.stderr is a str, child stderr never reaches capfd).

- [ ] **Step 3: Implement the capture mode**

In `src/omc/toolctx.py`: add `from typing import Literal` to the imports. Change `run`'s signature parameter `capture: bool = True` to `capture: bool | Literal["stdout"] = True`, and replace the capture block:

```python
        if capture == "stdout":
            # stdout piped (machine contracts like OMC_DEPENDENCY live there);
            # stderr INHERITED so a child's live narration/progress bar reaches
            # the user's terminal. stdin=DEVNULL as always: prompts must fail fast.
            kwargs["stdout"] = subprocess.PIPE
            kwargs["text"] = True
            kwargs["stdin"] = subprocess.DEVNULL
        elif capture:
            kwargs["capture_output"] = True
            kwargs["text"] = True
            kwargs["stdin"] = subprocess.DEVNULL
```

Extend the `run` docstring with one line: `capture="stdout"` pipes stdout only and inherits stderr.

- [ ] **Step 4: Implement the passthrough in `depwatch`**

In `src/omc/depwatch.py`, replace `_spawn`:

```python
def _spawn(ctx: ToolContext, argv: list[str], *, passthrough: bool = False) -> None:
    """passthrough=True inherits the child's stderr (document runs render a
    live progress bar and resume line there); stdout stays captured either way."""
    _say(f"→ {' '.join(argv)}")
    # Contain a missing/unlaunchable omc (FileNotFoundError is an OSError): the
    # loop must warn and continue, never crash (watch.py _chain_tick doctrine).
    try:
        cp = ctx.run(argv, capture="stdout" if passthrough else True)
    except OSError as exc:
        _say(f"✗ cannot run {argv[0]}: {exc}")
        return
    if cp.returncode != 0:
        detail = "see output above" if passthrough else (cp.stderr or "").strip()[:200]
        _say(f"✗ failed (exit {cp.returncode}): {detail}")
    else:
        _say("✓ done")
```

And change ONLY the document call site in `_tick` (the `elif not entry.get("documented")` branch) to:

```python
                _spawn(
                    ctx,
                    ["omc", "internal", "dependency", "document", "--git", f"{key}@{commit}"],
                    passthrough=True,
                )
```

(Both `ensure` call sites and the adopt site keep the default — fully captured.)

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/test_toolctx.py tests/unit/test_depwatch.py -q`
Expected: ALL PASS (existing depwatch tests unaffected — their stub writes nothing to stderr on the document path except the new tests').

- [ ] **Step 6: Run the full gate**

Run: `just build`
Expected: PASS — this is the final task; the whole feature is in.

- [ ] **Step 7: Commit**

```bash
git add src/omc/toolctx.py src/omc/depwatch.py tests/unit/test_toolctx.py tests/unit/test_depwatch.py
git commit -m "feat: dependency watch streams document progress to the terminal"
```

---

# v2 addendum — reporter/renderer split (post-PR #19 rebase)

Main gained parallel documents (`_document_batch`, up to 8 jobs) mid-build; the
user redirected the display architecture: document jobs REPORT (percent 0–100
on stdout + per-job log + exit code), the watch RENDERS (one bar line per
dependency). See the spec's "Display architecture (v2)" section. Tasks 7–8
implement it and remove the superseded v1 passthrough surface.

**Additional global constraints for v2:**
- New machine contract line: `OMC_PROGRESS {"percent": N}` — single-line JSON
  on stdout, same family as `OMC_DEPENDENCY`; N is an int 0–100.
- Main's `test_documents_missing_dependencies_in_parallel` (concurrency
  choreography) must keep passing unchanged.
- MultiBarThread block height is FIXED for a given batch (rows() returns the
  same number of lines every call) — document the contract, don't engineer
  around it.

---

### Task 7: run_document reports progress; tracker becomes a pure data source

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/dependency.py` (run_document wiki block; PageCountTracker; module constants; drop BarThread/render_bar imports)
- Test: `tests/unit/test_dependency.py`

**Interfaces:**
- Consumes: `ctx.run_supervised(..., poll=...)` (existing param).
- Produces (Task 8 relies on): `OMC_PROGRESS {"percent": N}` lines on run_document's stdout — emitted once up front when a resumed percent is already known, whenever the integer percent changes during the run, and a final `{"percent": 100}` on success immediately before the `OMC_DEPENDENCY` verdict. `_WIKI_POLL_SECONDS = 1.0` module constant (monkeypatchable). `PageCountTracker` loses `render()`, `_spin`, and the `clock` constructor param — it keeps `refresh()`, `state()`, `beat()`, `percent`.

- [ ] **Step 1: Update/extend the tests (they must fail first)**

In `tests/unit/test_dependency.py`: the tracker tests drop every `clock=lambda: 0.0` argument and every `render(...)` assertion (delete the golden-render assert in `test_tracker_counts_modules_pages_and_overview` and the `" --% " in t.render(now=0.0)` assert in `test_tracker_indeterminate_without_tree`). Then append:

```python
def _progress_values(capsys):
    out = capsys.readouterr().out
    vals = []
    verdict_seen = False
    for ln in out.splitlines():
        if ln.startswith("OMC_PROGRESS "):
            assert not verdict_seen, "progress line after the verdict"
            vals.append(json.loads(ln.split(" ", 1)[1])["percent"])
        elif ln.startswith("OMC_DEPENDENCY "):
            verdict_seen = True
    assert verdict_seen
    return vals


def test_document_reports_initial_percent_when_resuming(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])  # 3 + overview = 4
    (wiki / "a.md").write_text("x")
    (wiki / "b.md").write_text("x")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    vals = _progress_values(capsys)
    assert vals[0] == 50  # 2/4 known at start
    assert vals[-1] == 100  # deterministic completion signal


def test_document_reports_only_final_100_on_fresh_run(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)  # wiki exists, no tree -> percent unknown throughout
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    assert _progress_values(capsys) == [100]


def test_document_emits_progress_as_pages_land(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    (wiki / "a.md").write_text("x")  # 1/4 at start
    monkeypatch.setattr(dep, "_WIKI_POLL_SECONDS", 0.05)
    # node stub: write one more page mid-run (cwd is the checkout), then linger
    # long enough for a 0.05s poll to observe it.
    node = tmp_path / "bin" / "node"
    node.write_text('#!/bin/sh\nprintf x > .gitnexus/wiki/b.md\nsleep 0.4\nexit 0\n')
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    vals = _progress_values(capsys)
    assert vals[0] == 25 and vals[-1] == 100
    assert 50 in vals  # the mid-run beat saw b.md land


def test_document_stall_emits_no_100(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", 0.3)
    monkeypatch.setattr(dep, "_WIKI_POLL_SECONDS", 0.05)
    node = tmp_path / "bin" / "node"
    node.write_text("#!/bin/sh\nsleep 30\n")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    out = capsys.readouterr().out
    assert 'OMC_PROGRESS {"percent": 100}' not in out
    assert "OMC_DEPENDENCY" not in out
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/unit/test_dependency.py -q -k "progress or tracker or resum or stall or fresh"`
Expected: new tests FAIL (no OMC_PROGRESS lines; tracker tests fail on removed `clock` param only AFTER Step 3 — at this point tracker tests still pass, the four new ones fail).

- [ ] **Step 3: Implement**

In `src/omc/dependency.py`:

1. Remove the `from .cli.progress_bar import BarThread, render_bar` import (nothing in this module renders anymore).
2. Add next to `_WIKI_STALL_SECONDS`:

```python
# One disk poll per second drives BOTH the stall-guard heartbeat and progress
# reporting; monkeypatchable in tests.
_WIKI_POLL_SECONDS = 1.0
```

3. In `PageCountTracker`: delete the `clock` parameter, `self._clock`, `self._start`, `self._spin`, and the whole `render()` method; `__init__` becomes `def __init__(self, wiki_dir: Path) -> None` keeping `_dir`, `_done`, `_total`. Delete the now-unused `time` and `Callable` imports if nothing else uses them (check first — `time` may be used elsewhere in the file). Update the class docstring: it is a pure data source (refresh/state/beat/percent); rendering lives with the caller.
4. Add the progress emitter next to `_verdict`:

```python
def _progress(percent: int) -> None:
    """OMC_PROGRESS: machine-readable progress on stdout (contract sibling of
    OMC_DEPENDENCY). The watch parses these; rendering is the caller's job."""
    print(f"OMC_PROGRESS {json.dumps({'percent': percent})}", flush=True)
```

5. Replace the wiki-spawn block in `run_document` (from `tracker = PageCountTracker(wiki)` through `bar.stop()`) with:

```python
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
```

6. After `update_manifest(...)` and before `_verdict(...)`, add:

```python
    _progress(100)  # deterministic completion signal — the last poll may have missed the final page
```

- [ ] **Step 4: Run the dependency suite**

Run: `uv run pytest tests/unit/test_dependency.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Gate and commit**

Run: `just build` — expected PASS. Then:

```bash
git add src/omc/dependency.py tests/unit/test_dependency.py
git commit -m "feat: document jobs report OMC_PROGRESS; tracker is a pure data source"
```

---

### Task 8: watch renders per-dependency bars; remove the v1 passthrough surface

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/cli/progress_bar.py` (add MultiBarThread)
- Modify: `src/omc/depwatch.py` (job runner + per-job logs + block rendering; revert `_spawn`)
- Modify: `src/omc/toolctx.py` (remove `capture="stdout"` mode and `Literal` import; restore `capture: bool = True` and drop the docstring line)
- Test: `tests/unit/test_progress_bar.py`, `tests/unit/test_depwatch.py`, `tests/unit/test_toolctx.py`

**Interfaces:**
- Consumes: `OMC_PROGRESS {"percent": N}` stdout lines (Task 7), `render_bar` (Task 2), `ctx.stream` (existing).
- Produces: `MultiBarThread(rows: Callable[[], list[str]], out=None)` with `start()`/`stop()` — repaints a FIXED-height block once per second on a TTY, no-op otherwise, erases the block on stop.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_progress_bar.py`:

```python
def test_multibar_paints_block_and_clears_on_stop():
    from omc.cli.progress_bar import MultiBarThread

    out = _TTY()
    bar = MultiBarThread(lambda: ["ROW-A", "ROW-B"], out=out)
    bar.start()
    time.sleep(1.3)  # one beat
    bar.stop()
    text = out.getvalue()
    assert "ROW-A" in text and "ROW-B" in text
    assert "\r\x1b[K" in text  # line-erase repaint discipline
    assert "\x1b[2A" in text  # cursor-up over the 2-line block (stop's erase)


def test_multibar_is_noop_without_tty():
    from omc.cli.progress_bar import MultiBarThread

    out = _NonTTY()
    bar = MultiBarThread(lambda: ["ROW"], out=out)
    bar.start()
    bar.stop()
    assert out.getvalue() == ""
```

In `tests/unit/test_toolctx.py`: DELETE `test_run_capture_stdout_inherits_stderr` (mode removed).

In `tests/unit/test_depwatch.py`: DELETE `test_document_child_stderr_reaches_terminal` and `test_document_failure_points_at_output_above` (v1 passthrough tests). Append:

```python
def test_document_job_logs_output_and_parses_progress(tmp_path, capsys):
    ctx, calls = _ctx(tmp_path)
    omc = tmp_path / "bin" / "omc"
    omc.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\n'
        'echo \'OMC_PROGRESS {"percent": 42}\'\n'
        "echo NARRATION >&2\n"
        'echo \'OMC_PROGRESS not-json\'\n'  # malformed: must be ignored, not crash
        "exit 0\n"
    )
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    assert "✓ done github.com/foo/bar@" in err
    assert "log: " in err
    log_path = err.split("log: ", 1)[1].split()[0].rstrip(")")
    logged = open(log_path).read()
    assert 'OMC_PROGRESS {"percent": 42}' in logged and "NARRATION" in logged
    assert "\x1b[" not in err  # non-TTY: no ANSI bar bytes


def test_document_job_failure_names_exit_and_log(tmp_path, capsys):
    ctx, calls = _ctx(tmp_path)
    omc = tmp_path / "bin" / "omc"
    omc.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 3\n')
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    assert "✗ failed (exit 3) github.com/foo/bar@" in err
    assert "log: " in err
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/unit/test_progress_bar.py tests/unit/test_depwatch.py -q`
Expected: new tests FAIL (`MultiBarThread` missing; ✓/✗ lines lack ref/log).

- [ ] **Step 3: Add MultiBarThread to the component**

Append to `src/omc/cli/progress_bar.py`:

```python
class MultiBarThread:
    """Repaints a FIXED-height block of caller-rendered lines once per second,
    in place (cursor-up ANSI). Same doctrine as BarThread: TTY-gated no-op,
    daemon thread, and stop() erases the block — narration owns permanent
    output. ``rows()`` must return the same number of lines on every call for
    the lifetime of the thread; it runs on the redraw thread, so it must not
    raise."""

    def __init__(self, rows, out=None) -> None:
        self._rows = rows
        self._out = out if out is not None else sys.stderr
        self._enabled = bool(getattr(self._out, "isatty", lambda: False)())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._height = 0  # lines currently painted

    def start(self) -> None:
        if not self._enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _paint(self) -> None:
        lines = self._rows()
        if self._height:
            self._out.write(f"\x1b[{self._height}A")
        for line in lines:
            self._out.write("\r\x1b[K" + line + "\n")
        self._height = len(lines)
        self._out.flush()

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            self._paint()

    def stop(self) -> None:
        if not self._enabled or self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        if self._height:
            # erase the block and park the cursor back at its top
            self._out.write(f"\x1b[{self._height}A" + "\r\x1b[K\n" * self._height)
            self._out.write(f"\x1b[{self._height}A")
            self._out.flush()
```

- [ ] **Step 4: Rewire depwatch**

In `src/omc/depwatch.py`:

1. Revert `_spawn` to its pre-passthrough form (no `passthrough` param, plain `ctx.run(argv)`, failure detail `(cp.stderr or "").strip()[:200]`).
2. Add imports: `import json`, `import tempfile`, `import time` (keep existing), `from .cli.progress_bar import render_bar, MultiBarThread`.
3. Add the job model and runner:

```python
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
```

(`import os` is needed for `os.fdopen` — add it.)

4. Replace `_document_batch`:

```python
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
```

5. In `src/omc/toolctx.py`: remove the `capture == "stdout"` branch, restore `capture: bool = True`, remove `from typing import Literal`, and drop the `capture="stdout"` docstring line — back to the pre-Task-6 surface.

- [ ] **Step 5: Run the suites**

Run: `uv run pytest tests/unit/test_progress_bar.py tests/unit/test_depwatch.py tests/unit/test_toolctx.py -q`
Expected: ALL PASS, including main's `test_documents_missing_dependencies_in_parallel` (its stub writes nothing to stdout — jobs just show indeterminate rows on a TTY, and the test runs non-TTY).

- [ ] **Step 6: Full gate and commit**

Run: `just build`
Expected: PASS.

```bash
git add src/omc/cli/progress_bar.py src/omc/depwatch.py src/omc/toolctx.py tests/unit/test_progress_bar.py tests/unit/test_depwatch.py tests/unit/test_toolctx.py
git commit -m "feat: watch renders per-dependency progress bars from OMC_PROGRESS reports"
```
