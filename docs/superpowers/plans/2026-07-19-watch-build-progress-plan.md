# Live Build Progress for `omc watch` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `omc watch --auto-build` observable: a live tail-able log announced up front, an in-place progress bar with elapsed time, no self-imposed timeout, and a standalone `omc internal build-progress <logfile>` viewer.

**Architecture:** A new streaming subprocess primitive on `ToolContext` (two line-reader threads, serialized callback); provider-level stream variants (claude switches to `--output-format stream-json --verbose` and decodes events back to human-readable text; codex/opencode already stream text); `_auto_build` tees decoded lines to a live log and feeds a pure `ProgressTracker` engine whose bar a once-per-second TTY thread redraws; the same engine powers the standalone follow-mode viewer.

**Tech Stack:** Python 3 stdlib only (subprocess, threading, re, tempfile), pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-watch-build-progress-design.md` — read it before starting any task.

## Global Constraints

- **No timeout on the build stage** — `_BUILD_TIMEOUT` and its `TimeoutExpired` branch are DELETED; `ToolContext.stream` has NO timeout parameter. `_HOOK_TIMEOUT` (600s, post-watch hook) STAYS.
- Bar format is EXACT: `[====>             ] 21% (00:13:22)` — 18 chars between brackets, `round(width*pct/100)` `=` chars followed by `>` (100% = 18 `=`, no `>`), percent right-aligned to 3 chars (`--%` when unknown), elapsed `HH:MM:SS`. Indeterminate bar: a 3-char `<=>` marker bouncing through the 18-char field.
- Sentinel line is EXACT: `--- omc: stage finished (rc N) ---` (N = child exit code, `?` when it never started).
- Log content must be human-readable decoded text, flushed after every line (tail -f-able); the `OMC_STAGE` verdict must survive decoding as its own line (`_parse_stage`'s `^OMC_STAGE …$` anchor, src/omc/watch.py:109, is unchanged).
- `ToolContext` stays the ONLY subprocess boundary (`.omc/skills/review/SKILL.md` rule) — no `subprocess` imports outside `src/omc/toolctx.py` in production code (tests may, matching existing test style).
- Bar renders to **stderr** only and only when `sys.stderr.isatty()`; `omc internal` stdout stays machine-clean.
- Existing narration style `→/✓/✗/·` via `_say`; existing auto-build failure-status ladder (`exit N` / `no verdict` / verdict summary) unchanged.
- Ctrl+C semantics unchanged (no new signal handling).
- Run tests: `uv run pytest tests/unit -q`; lint: `uvx ruff check src/omc/ tests/` and `uvx ruff format --check .` (line length 100). KNOWN TRAP: if test results look inconsistent with your edits, run `uv sync --reinstall` (worktree venv shebang trap), then re-run.
- Every commit message ends with the trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Model-tier policy: each task carries a `Model:` line; tiers resolve at dispatch time; the cheap/fast tier is never used.

---

### Task 1: `buildprogress` engine

**Model:** standard coding tier

**Files:**
- Create: `src/omc/buildprogress.py`
- Test: `tests/unit/test_buildprogress.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces (later tasks rely on these exact names):
  - `ProgressTracker(start: float | None = None, clock: Callable[[], float] = time.monotonic)` with `feed(line: str) -> None`, `percent -> int | None` (property), `elapsed(now: float | None = None) -> float`, `render(now: float | None = None, width: int = 18) -> str`
  - `sentinel_line(rc: int | None) -> str` and `SENTINEL_RE` (compiled regex matching sentinel lines)
  - `follow_log(path_str: str) -> int` is added in Task 5, same module.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_buildprogress.py`:

```python
from omc.buildprogress import SENTINEL_RE, ProgressTracker, sentinel_line


def test_cargo_counter_sets_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("   Building [=====>                   ] 271/1288: foo, bar")
    assert t.percent == 21  # round(100*271/1288)


def test_cargo_parenthesized_counter():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("Compiling serde v1.0.200 (12/1288)")
    assert t.percent == 1


def test_pytest_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("........................ [ 28%]")
    assert t.percent == 28


def test_generic_bare_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("downloading 73% done")
    assert t.percent == 73


def test_latest_match_wins():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("step (1/10)")
    t.feed("step (9/10)")
    assert t.percent == 90


def test_total_zero_is_ignored():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("weird (3/0)")
    assert t.percent is None


def test_done_beyond_total_is_ignored():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("(1288/12)")  # reversed / nonsense counter
    assert t.percent is None


def test_generic_over_100_is_ignored():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("999% cpu")  # generic parser: values beyond 100 are noise, not progress
    assert t.percent is None


def test_no_match_is_indeterminate():
    t = ProgressTracker(start=0.0, clock=lambda: 0.0)
    t.feed("Compiling serde v1.0.200")
    assert t.percent is None


def test_render_exact_bar_at_21_percent():
    t = ProgressTracker(start=0.0, clock=lambda: 48802.0)  # 13h33m22s -> exercises HH:MM:SS
    t.feed("(271/1288)")
    assert t.render() == "[====>             ]  21% (13:33:22)"


def test_render_100_percent_has_no_arrow_overflow():
    t = ProgressTracker(start=0.0, clock=lambda: 61.0)
    t.feed("(10/10)")
    assert t.render() == "[==================] 100% (00:01:01)"


def test_render_indeterminate_bounces():
    t = ProgressTracker(start=0.0, clock=lambda: 5.0)
    first = t.render()
    second = t.render()
    assert " --% (00:00:05)" in first
    assert "<=>" in first and "<=>" in second
    assert first != second  # marker moved between redraws


def test_elapsed_uses_injected_clock():
    now = {"t": 100.0}
    t = ProgressTracker(start=100.0, clock=lambda: now["t"])
    now["t"] = 163.0
    assert t.elapsed() == 63.0


def test_sentinel_roundtrip():
    line = sentinel_line(3)
    assert line == "--- omc: stage finished (rc 3) ---"
    assert SENTINEL_RE.match(line)
    assert sentinel_line(None) == "--- omc: stage finished (rc ?) ---"
    assert SENTINEL_RE.match(sentinel_line(None))
    assert not SENTINEL_RE.match("prefix --- omc: stage finished (rc 3) ---")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_buildprogress.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'omc.buildprogress'`

- [ ] **Step 3: Implement the engine**

Create `src/omc/buildprogress.py`:

```python
"""Build-progress engine: pure line-fed percent extraction + bar rendering.

Consumed by `omc watch --auto-build` (live bar while a build stage streams)
and by `omc internal build-progress <logfile>` (standalone follow-mode
viewer, Task 5). Parsers are an ordered registry — adding a build system is
one entry + tests. Latest match wins; no match yet renders an indeterminate
bouncing bar with elapsed time only.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

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

_SENTINEL_FMT = "--- omc: stage finished (rc {rc}) ---"
SENTINEL_RE = re.compile(r"^--- omc: stage finished \(rc (-?\d+|\?)\) ---$")


def sentinel_line(rc: int | None) -> str:
    return _SENTINEL_FMT.format(rc="?" if rc is None else rc)


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


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
        clock_part = f"({_format_elapsed(self.elapsed(now))})"
        pct = self._percent
        if pct is None:
            # bouncing <=> marker; advances one slot per redraw
            pos = self._spin % (width - 3)
            self._spin += 1
            bar = " " * pos + "<=>" + " " * (width - 3 - pos)
            return f"[{bar}]  --% {clock_part}"
        filled = round(width * pct / 100)
        bar = "=" * width if pct >= 100 else ("=" * filled + ">").ljust(width)[:width]
        # a fresh 0% still shows the arrow head: ">" alone at filled == 0
        return f"[{bar}] {pct:3d}% {clock_part}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_buildprogress.py -q`
Expected: ALL pass. If `test_render_exact_bar_at_21_percent` fails on spacing, fix the implementation (not the test) — the format is a Global Constraint.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format src/omc/buildprogress.py tests/unit/test_buildprogress.py
uvx ruff check src/omc/buildprogress.py tests/unit/test_buildprogress.py
git add src/omc/buildprogress.py tests/unit/test_buildprogress.py
git commit -m "feat: build-progress engine (parser registry + bar renderer)"
```

---

### Task 2: `ToolContext.stream()`

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/toolctx.py` (add `stream` below `run`, ~line 68; add `import threading` to the imports)
- Test: `tests/unit/test_toolctx.py`

**Interfaces:**
- Consumes: existing `child_env()` on `ToolContext`.
- Produces: `ToolContext.stream(argv: Sequence[str], *, on_line: Callable[[str], None], cwd: str | os.PathLike[str] | None = None, extra_env: dict[str, str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_toolctx.py` (read its imports first; it already builds `ToolContext` instances — follow the existing construction pattern in that file):

Match the file's existing convention: contexts via `ToolContext.from_env({"HOME": str(tmp_path)})`, children via `sys.executable -c` (absolute path — no PATH dependency; the file already imports `sys`).

```python
def test_stream_delivers_whole_lines_from_both_streams(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    code = (
        "import sys\n"
        "print('out-one')\n"
        "print('err-one', file=sys.stderr)\n"
        "sys.stdout.write('out-')\n"          # partial write, completed next
        "sys.stdout.write('two\\n')\n"
        "sys.exit(7)\n"
    )
    lines: list[str] = []
    rc = ctx.stream([sys.executable, "-u", "-c", code], on_line=lines.append)
    assert rc == 7
    assert "out-one" in lines and "err-one" in lines and "out-two" in lines
    # per-stream order preserved: out-one before out-two
    assert lines.index("out-one") < lines.index("out-two")
    # lines are whole — the partial write never surfaced alone
    assert "out-" not in lines


def test_stream_stdin_is_devnull(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    lines: list[str] = []
    # reading stdin with DEVNULL sees EOF immediately instead of hanging
    code = "import sys; sys.stdout.write(sys.stdin.read())"
    rc = ctx.stream([sys.executable, "-c", code], on_line=lines.append)
    assert rc == 0
    assert lines == []


def test_stream_extra_env_reaches_child(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    lines: list[str] = []
    code = "import os; print('v=' + os.environ.get('OMC_STREAM_TEST', ''))"
    rc = ctx.stream(
        [sys.executable, "-c", code],
        on_line=lines.append,
        extra_env={"OMC_STREAM_TEST": "42"},
    )
    assert rc == 0
    assert lines == ["v=42"]


def test_stream_serializes_on_line_calls(tmp_path):
    """Concurrent stdout+stderr chatter never interleaves INSIDE a callback."""
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    code = (
        "import sys\n"
        "for i in range(50):\n"
        "    print(f'out-{i}')\n"
        "    print(f'err-{i}', file=sys.stderr)\n"
    )
    seen: list[str] = []
    in_callback = {"depth": 0, "max": 0}

    def on_line(line):
        in_callback["depth"] += 1
        in_callback["max"] = max(in_callback["max"], in_callback["depth"])
        seen.append(line)
        in_callback["depth"] -= 1

    rc = ctx.stream([sys.executable, "-u", "-c", code], on_line=on_line)
    assert rc == 0
    assert len(seen) == 100
    assert in_callback["max"] == 1  # never reentered concurrently
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_toolctx.py -q -k stream`
Expected: 4 FAIL with `AttributeError: 'ToolContext' object has no attribute 'stream'`

- [ ] **Step 3: Implement `stream`**

In `src/omc/toolctx.py`, add `import threading` to the stdlib imports, then add below `run`:

```python
    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> int:
        """Run argv, delivering every stdout/stderr line to ``on_line`` live.

        stdout and stderr are SEPARATE pipes read by two threads — never
        merged into one pipe: pipe writes beyond PIPE_BUF are not atomic, so
        a large stdout line (e.g. a stream-json tool result) could splice
        with a stderr line mid-line. Two readers guarantee whole lines; a
        lock serializes ``on_line`` calls. Per-stream order is preserved;
        cross-stream order is best-effort.

        Deliberately NO timeout: used for LLM build stages that may run an
        hour — liveness is the user's call (visible elapsed time + Ctrl-C).
        """
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "errors": "replace",
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        proc = subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - argv list, no shell
        lock = threading.Lock()

        def pump(pipe) -> None:
            for raw in pipe:
                with lock:
                    on_line(raw.rstrip("\n"))
            pipe.close()

        readers = [
            threading.Thread(target=pump, args=(p,), daemon=True)
            for p in (proc.stdout, proc.stderr)
        ]
        for t in readers:
            t.start()
        for t in readers:
            t.join()
        return proc.wait()
```

`Callable` comes from `collections.abc` — check the file's existing imports and extend them (it already imports `Sequence` from there).

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: ALL pass.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format src/omc/toolctx.py tests/unit/test_toolctx.py
uvx ruff check src/omc/toolctx.py tests/unit/test_toolctx.py
git add src/omc/toolctx.py tests/unit/test_toolctx.py
git commit -m "feat: ToolContext.stream — line-streaming subprocess primitive"
```

---

### Task 3: Provider streaming variants + claude stream-json decode

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/providers/base.py` (two concrete methods with defaults, after `notification_setup`)
- Modify: `src/omc/providers/claude.py` (overrides; add `import json`)
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: existing `headless_argv` signatures (all three providers).
- Produces:
  - `Provider.headless_stream_argv(prompt: str, *, model: str, allowed_tools: list[str] | None = None) -> list[str]` — base default returns `self.headless_argv(prompt, model=model, allowed_tools=allowed_tools)`.
  - `Provider.decode_stream_line(line: str) -> list[str]` — base default `[line]`.
  - Claude overrides: stream argv uses `--output-format stream-json --verbose`; decode unwraps events to text fragments (one list entry per LINE — multi-line texts are split so `OMC_STAGE` stays line-anchored).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_providers.py` (match its existing import/style — it already imports the provider registry):

```python
# Captured from a real `claude -p --output-format stream-json --verbose` run
# (2026-07-19 probe) — shapes, not verbatim transcripts.
_SJ_ASSISTANT_TEXT = (
    '{"type":"assistant","message":{"content":[{"type":"text",'
    '"text":"Build stage passed.\\nOMC_STAGE {\\"passed\\": true}"}]}}'
)
_SJ_TOOL_USE = (
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
    '"input":{"command":"just build","description":"Run build"}}]}}'
)
_SJ_TOOL_RESULT_STR = (
    '{"type":"user","message":{"content":[{"type":"tool_result",'
    '"content":"Compiling foo (12/1288)\\nok"}]}}'
)
_SJ_TOOL_RESULT_LIST = (
    '{"type":"user","message":{"content":[{"type":"tool_result",'
    '"content":[{"type":"text","text":"251 passed"}]}]}}'
)
_SJ_RESULT = '{"type":"result","result":"done\\nOMC_STAGE {\\"passed\\": true}"}'
_SJ_SYSTEM = '{"type":"system","subtype":"init"}'
_SJ_THINKING = (
    '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"hmm"}]}}'
)


def test_claude_stream_argv_uses_stream_json():
    p = get_provider("claude")
    argv = p.headless_stream_argv("do it", model="m1", allowed_tools=["Bash"])
    assert argv[:3] == ["claude", "-p", "do it"]
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv
    assert argv[-2:] == ["--allowed-tools", "Bash"]  # allowed-tools stays LAST (variadic)


def test_claude_decode_assistant_text_splits_lines():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_ASSISTANT_TEXT) == [
        "Build stage passed.",
        'OMC_STAGE {"passed": true}',
    ]


def test_claude_decode_tool_use_echoes_command():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_TOOL_USE) == ["$ just build"]


def test_claude_decode_tool_result_string_and_list():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_TOOL_RESULT_STR) == ["Compiling foo (12/1288)", "ok"]
    assert p.decode_stream_line(_SJ_TOOL_RESULT_LIST) == ["251 passed"]


def test_claude_decode_result_event_carries_final_text():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_RESULT) == ["done", 'OMC_STAGE {"passed": true}']


def test_claude_decode_skips_system_and_thinking():
    p = get_provider("claude")
    assert p.decode_stream_line(_SJ_SYSTEM) == []
    assert p.decode_stream_line(_SJ_THINKING) == []


def test_claude_decode_passes_non_json_through():
    p = get_provider("claude")
    assert p.decode_stream_line("plain warning line") == ["plain warning line"]
    assert p.decode_stream_line("   ") == []  # blank noise dropped


def test_codex_and_opencode_stream_defaults_are_identity():
    for name in ("codex", "opencode"):
        p = get_provider(name)
        assert p.headless_stream_argv("x", model="") == p.headless_argv("x", model="")
        assert p.decode_stream_line("anything") == ["anything"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_providers.py -q -k "stream or decode"`
Expected: FAIL with `AttributeError: ... has no attribute 'headless_stream_argv'`

- [ ] **Step 3: Implement**

`src/omc/providers/base.py` — add after `notification_setup` (concrete defaults, NOT abstract):

```python
    def headless_stream_argv(
        self,
        prompt: str,
        *,
        model: str,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        """Like headless_argv, but for LIVE streaming consumption. Default:
        same argv — codex/opencode already emit incremental text. Providers
        that buffer their print mode (claude) override with a streaming
        output format."""
        return self.headless_argv(prompt, model=model, allowed_tools=allowed_tools)

    def decode_stream_line(self, line: str) -> list[str]:
        """Decode ONE raw child output line into human-readable text lines.
        Default: identity. Providers whose stream is an event protocol
        (claude stream-json) override to unwrap events; multi-line texts
        must be split so line-anchored contracts (OMC_STAGE) survive."""
        return [line]
```

`src/omc/providers/claude.py` — add `import json` at the top, then inside the provider class:

```python
    def headless_stream_argv(self, prompt, *, model, allowed_tools=None):
        # stream-json + --verbose emits one JSON event per line AS IT HAPPENS
        # (verified 2026-07-19: tool_use/tool_result arrive live; plain
        # `--output-format text` prints only at exit). Same flag-ordering
        # constraint as headless_argv: --allowed-tools stays LAST.
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if model:
            argv += ["--model", model]
        if allowed_tools:
            argv += ["--allowed-tools", *allowed_tools]
        return argv

    def decode_stream_line(self, line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return [line] if line.strip() else []
        if not isinstance(event, dict):
            return [line]
        out: list[str] = []
        kind = event.get("type")
        if kind == "assistant":
            for block in event.get("message", {}).get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    out.extend(str(block["text"]).splitlines())
                elif block.get("type") == "tool_use":
                    command = (block.get("input") or {}).get("command")
                    out.append(f"$ {command}" if command else f"[{block.get('name', 'tool')}]")
        elif kind == "user":
            for block in event.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, str):
                        out.extend(content.splitlines())
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                out.extend(str(part.get("text", "")).splitlines())
        elif kind == "result":
            result = event.get("result")
            if isinstance(result, str):
                out.extend(result.splitlines())
        # system / thinking / rate_limit events decode to nothing
        return out
```

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: ALL pass.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format src/omc/providers/base.py src/omc/providers/claude.py tests/unit/test_providers.py
uvx ruff check src/omc/providers/ tests/unit/test_providers.py
git add src/omc/providers/base.py src/omc/providers/claude.py tests/unit/test_providers.py
git commit -m "feat: provider streaming variants + claude stream-json decode"
```

---

### Task 4: `_auto_build` streaming rewrite

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/watch.py` (`_auto_build`, `_post_watch_hook`, delete `_BUILD_TIMEOUT`; module docstring)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `ctx.stream(argv, *, on_line, cwd, extra_env)` (Task 2); `provider.headless_stream_argv` / `provider.decode_stream_line` (Task 3); `ProgressTracker`, `sentinel_line` from `omc.buildprogress` (Task 1); existing `_parse_stage`, `_say`, `skill_prompt`, `get_provider`.
- Produces: `_BarThread(tracker, out=None)` (private to watch.py — start()/stop(); no-op when `out.isatty()` is false). Log line format consumed by Task 5's follow mode: decoded text lines + final sentinel.

- [ ] **Step 1: Adapt + extend the tests**

In `tests/unit/test_watch.py`, the existing auto-build tests keep passing UNCHANGED (the `_stub_claude` plain-text stub flows through claude's decode as non-JSON passthrough; the start-line assertion is a substring of the new line). Add these new tests after `test_no_auto_build_flag_means_no_build`:

```python
def test_auto_build_announces_log_path_up_front(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _stub_claude(
        tmp_path,
        'OMC_STAGE {"stage": "build", "configured": true, "passed": true, "summary": "ok"}',
    )
    assert _run_once_auto_build(repo, ctx) == 0
    err = capsys.readouterr().err
    m = re.search(r"→ running project build stage via claude \(LLM-heavy\) — log: (\S+)", err)
    assert m, f"missing up-front log announcement:\n{err}"
    log = Path(m.group(1))
    assert log.is_file()
    content = log.read_text()
    assert "OMC_STAGE" in content  # decoded transcript logged
    assert re.search(r"^--- omc: stage finished \(rc 0\) ---$", content, re.MULTILINE)


def test_auto_build_decodes_stream_json_lines(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    # stub emits stream-json: a tool result with a cargo counter, then the
    # final result event carrying the verdict — the log must hold DECODED text
    _stub_claude(
        tmp_path,
        '{"type":"user","message":{"content":[{"type":"tool_result",'
        '"content":"Compiling foo (12/1288)"}]}}\n'
        '{"type":"result","result":"OMC_STAGE {\\"stage\\": \\"build\\", '
        '\\"configured\\": true, \\"passed\\": true, \\"summary\\": \\"ok\\"}"}',
    )
    assert _run_once_auto_build(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "✓ auto-build passed" in err
    m = re.search(r"— log: (\S+)", err)
    content = Path(m.group(1)).read_text()
    assert "Compiling foo (12/1288)" in content       # decoded, not raw JSON
    assert '"type":"user"' not in content              # raw event NOT in log
    assert re.search(r"^OMC_STAGE ", content, re.MULTILINE)


def test_auto_build_stream_call_has_no_timeout(tmp_path, monkeypatch):
    """The no-timeout requirement, pinned: _auto_build must call ctx.stream
    (which has no timeout parameter), never ctx.run with a timeout."""
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _stub_claude(
        tmp_path,
        'OMC_STAGE {"stage": "build", "configured": true, "passed": true, "summary": "ok"}',
    )
    calls = {}
    real_stream = type(ctx).stream

    def spy(self, argv, **kwargs):
        calls["kwargs"] = kwargs
        return real_stream(self, argv, **kwargs)

    monkeypatch.setattr(type(ctx), "stream", spy)
    assert _run_once_auto_build(repo, ctx) == 0
    assert "kwargs" in calls, "auto-build no longer uses ctx.stream"
    assert "timeout" not in calls["kwargs"]
    assert calls["kwargs"]["extra_env"]["CARGO_TERM_PROGRESS_WHEN"] == "always"
    assert calls["kwargs"]["extra_env"]["CARGO_TERM_PROGRESS_WIDTH"] == "80"


def test_build_timeout_constant_is_gone():
    import omc.watch as watch_mod

    assert not hasattr(watch_mod, "_BUILD_TIMEOUT")


def test_hook_announces_log_path_up_front(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "echo hello\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    m = re.search(
        r"→ running project post-watch hook \(\.omc/hooks/post-watch\.sh\) — log: (\S+)", err
    )
    assert m, f"hook start line lacks log path:\n{err}"
    assert Path(m.group(1)).is_file()
```

Also UPDATE one existing assertion: `test_hook_failure_links_log_and_keeps_once_rc_zero` and `test_hook_timeout_is_a_failure_and_loop_survives` keep working unchanged (failure narration still links the log) — verify, don't modify.

- [ ] **Step 2: Run new tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -q -k "announces or decodes or no_timeout or constant_is_gone"`
Expected: FAIL (no log announcement in narration; `_BUILD_TIMEOUT` still exists).

- [ ] **Step 3: Rewrite `_auto_build` (and touch `_post_watch_hook`)**

In `src/omc/watch.py`:

Imports: add `import threading`, and `from .buildprogress import ProgressTracker, sentinel_line`.

Delete `_BUILD_TIMEOUT = 1800` and the `TimeoutExpired`/`UnicodeDecodeError` branches of `_auto_build` (the hook keeps its own — do not touch `_HOOK_TIMEOUT` or `_post_watch_hook`'s exception ladder).

Add near `_write_hook_log`:

```python
_CARGO_PROGRESS_ENV = {
    # cargo suppresses its (12/1288) counters when piped; these force them so
    # the progress parsers see real numbers. Harmless for non-cargo projects.
    "CARGO_TERM_PROGRESS_WHEN": "always",
    "CARGO_TERM_PROGRESS_WIDTH": "80",
}


def _make_live_log(prefix: str) -> tuple[object, str]:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".log")
    return os.fdopen(fd, "w", encoding="utf-8"), path


class _BarThread:
    """Redraws the progress bar once per second on stderr, in place (\\r).
    TTY-gated: constructing on a non-TTY yields a no-op. Watch narration is
    sequential — nothing else writes to stderr while a stage streams."""

    def __init__(self, tracker: ProgressTracker, out=None) -> None:
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
        while not self._stop.wait(1.0):
            self._out.write("\r" + self._tracker.render())
            self._out.flush()

    def stop(self) -> None:
        if not self._enabled or self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._out.write("\r\x1b[K")  # clear the bar line before normal narration resumes
        self._out.flush()
```

Rewrite `_auto_build`:

```python
def _auto_build(ctx: ToolContext, cfg: Config, root: str) -> None:
    """--auto-build: run the project's build stage via the default LLM after
    an action tick, STREAMING: decoded transcript tees to a live log
    (announced up front, tail -f-able) and feeds the progress bar. No
    timeout — a build may run an hour; the elapsed clock + Ctrl-C replace
    it. Failures warn, never crash. The SKILL.md existence pre-check
    deliberately mirrors the build skill's own step 2 — a cost guard so an
    unconfigured project never spends an LLM call per tick to learn
    'nothing to do'."""
    if not (Path(root) / ".omc" / "skills" / "build" / "SKILL.md").is_file():
        _say("· no project build stage configured — skipping auto-build")
        return
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    log, log_path = _make_live_log("omc-auto-build-")
    _say(f"→ running project build stage via {name} (LLM-heavy) — log: {log_path}")
    tracker = ProgressTracker()
    collected: list[str] = []

    def on_line(raw: str) -> None:
        for text in provider.decode_stream_line(raw):
            log.write(text + "\n")
            log.flush()
            collected.append(text)
            tracker.feed(text)

    bar = _BarThread(tracker)
    status: str | None = None
    rc: int | None = None
    bar.start()
    try:
        rc = ctx.stream(
            provider.headless_stream_argv(
                skill_prompt("build"),
                model=model,
                allowed_tools=["Bash", "Read", "Glob", "Grep"],
            ),
            cwd=root,
            extra_env={**provider.title_env(), **_CARGO_PROGRESS_ENV},
            on_line=on_line,
        )
    except OSError as exc:
        log.write(f"{exc}\n")
        status = "failed to start"
    finally:
        bar.stop()
        log.write(sentinel_line(rc) + "\n")
        log.close()
    if status is None:
        verdict = _parse_stage("\n".join(collected))
        if rc != 0:
            status = f"exit {rc}"
        elif verdict is None:
            status = "no verdict"
        elif not verdict.get("passed"):
            status = str(verdict.get("summary") or "stage failed")
    if status is None:
        _say("✓ auto-build passed")
    else:
        _say(f"✗ auto-build failed ({status}) — log: {log_path}")
```

`_post_watch_hook` change (announce only): replace its `_say("→ running project post-watch hook (.omc/hooks/post-watch.sh)")` + post-hoc `_write_hook_log(output, "omc-post-watch-")` pair with an up-front `_make_live_log("omc-post-watch-")` whose path goes on the start line (`→ running project post-watch hook (.omc/hooks/post-watch.sh) — log: {path}`), writing the captured output into that handle at the end (content and failure narration unchanged: `✗ post-watch hook failed ({status}) — log: {path}`). Keep `_write_hook_log` only if the hook path still needs it — if nothing else uses it after this change, delete it.

Update the module docstring's tick description if it mentions the build timeout. Ensure `subprocess` import is still needed (the hook's `TimeoutExpired` uses it — keep).

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: ALL pass — new tests AND every pre-existing auto-build/hook test.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format src/omc/watch.py tests/unit/test_watch.py
uvx ruff check src/omc/watch.py tests/unit/test_watch.py
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "feat: stream auto-build to a live log with progress bar, drop build timeout"
```

---

### Task 5: `omc internal build-progress` follow mode

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/buildprogress.py` (add `follow_log`)
- Modify: `src/omc/internal.py` (`_USAGE` string + dispatch branch)
- Test: `tests/unit/test_buildprogress.py`, `tests/unit/test_internal.py`

**Interfaces:**
- Consumes: `ProgressTracker`, `SENTINEL_RE` (Task 1).
- Produces: `follow_log(path_str: str, *, poll: float = 0.5, out=None) -> int` — 0 on sentinel or Ctrl+C, 2 when the file never appears. CLI: `omc internal build-progress <logfile>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_buildprogress.py`:

```python
import threading
import time as _time

from omc.buildprogress import follow_log


def test_follow_log_reads_to_sentinel_and_exits_zero(tmp_path):
    log = tmp_path / "build.log"
    log.write_text("starting\n")

    def writer():
        _time.sleep(0.2)
        with log.open("a") as fh:
            fh.write("(5/10)\n")
            fh.write("--- omc: stage finished (rc 0) ---\n")

    t = threading.Thread(target=writer)
    t.start()
    rc = follow_log(str(log), poll=0.05)
    t.join()
    assert rc == 0


def test_follow_log_missing_file_is_usage_error(tmp_path):
    rc = follow_log(str(tmp_path / "nope.log"), poll=0.01)
    assert rc == 2
```

Append to `tests/unit/test_internal.py` (match its existing style for invoking `run_internal`):

```python
def test_internal_build_progress_usage_and_dispatch(tmp_path, capsys):
    from omc.internal import run_internal

    assert run_internal(["build-progress"]) == 2  # missing logfile -> usage
    log = tmp_path / "done.log"
    log.write_text("--- omc: stage finished (rc 0) ---\n")
    assert run_internal(["build-progress", str(log)]) == 0
    out = capsys.readouterr().out
    assert out == ""  # internal stdout stays machine-clean; bar goes to stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_buildprogress.py tests/unit/test_internal.py -q -k "follow or build_progress"`
Expected: FAIL with `ImportError: cannot import name 'follow_log'` / usage-exit mismatch.

- [ ] **Step 3: Implement**

Append to `src/omc/buildprogress.py` (extend the module imports with `import sys` and `from pathlib import Path`):

```python
def follow_log(path_str: str, *, poll: float = 0.5, out=None) -> int:
    """`omc internal build-progress <logfile>`: follow a live stage log
    tail -f-style, rendering the bar in place on stderr (TTY only). Exits 0
    at the sentinel line or on Ctrl-C; 2 if the file never appears (short
    grace wait so it can be started just before the stage)."""
    stream = out if out is not None else sys.stderr
    path = Path(path_str)
    deadline = time.monotonic() + 5.0
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
```

In `src/omc/internal.py`: extend `_USAGE` to mention `build-progress LOGFILE`, and add to `run_internal`'s if-chain (before the final usage fallback, mirroring `notify`):

```python
    if cmd == "build-progress":
        parser = argparse.ArgumentParser(prog="omc internal build-progress", add_help=False)
        parser.add_argument("logfile")
        try:
            args = parser.parse_args(rest)
        except SystemExit:
            print(_USAGE, file=sys.stderr)
            return 2
        from .buildprogress import follow_log

        return follow_log(args.logfile)
```

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: ALL pass.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format src/omc/buildprogress.py src/omc/internal.py tests/unit/test_buildprogress.py tests/unit/test_internal.py
uvx ruff check src/omc/buildprogress.py src/omc/internal.py tests/unit/
git add src/omc/buildprogress.py src/omc/internal.py tests/unit/test_buildprogress.py tests/unit/test_internal.py
git commit -m "feat: omc internal build-progress — follow-mode log viewer"
```

---

### Task 6: README + docstring updates

**Model:** standard coding tier

**Files:**
- Modify: `README.md` (watch prose paragraph ~line 68; `omc watch` command-table row ~line 106)

**Interfaces:**
- Consumes: shipped behavior from Tasks 1–5.
- Produces: user-facing docs only.

- [ ] **Step 1: Update the watch prose paragraph**

In the paragraph describing `--auto-build` (README.md ~line 68), after the sentence ending "…again linking the transcript log on failure.", insert:

```
Auto-build runs are fully observable: the log path is printed the moment the stage starts (tail it live with `tail -f`, or point `omc internal build-progress <logfile>` at it from any terminal for a progress bar), an in-place `[====>             ] 21% (00:13:22)` bar tracks build-system counters (cargo's `12/1288`, pytest's `[ 28%]`, plain `NN%`) with elapsed time, and there is deliberately no timeout — a build may legitimately run an hour; the visible clock plus Ctrl-C replaces it. The post-watch hook line prints its log path up front too.
```

- [ ] **Step 2: Update the command table row**

Extend the `omc watch` row's description (README.md ~line 106) so the `--auto-build` mention reads: `(and with --auto-build its build stage — streamed to a live log with an in-place progress bar, no timeout)`.

- [ ] **Step 3: Verify**

Run: `grep -n "build-progress\|tail -f" README.md`
Expected: both edits present.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document live auto-build progress + build-progress viewer"
```
