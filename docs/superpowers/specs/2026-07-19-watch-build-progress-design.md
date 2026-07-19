# Live progress for `omc watch` LLM stages — streaming logs + progress bar

Approved 2026-07-19. Motivated by a real incident the same day: an
auto-build "timeout" where the build had actually finished in 56 seconds and
the `claude` CLI hung on exit for 29 minutes with zero stdout —
`claude -p --output-format text` prints only at exit (`src/omc/providers/
claude.py:19`), and the transcript log is written only after the child dies
(`src/omc/watch.py:_write_hook_log`). The user cannot tell a slow build from
a wedged one. This design makes every auto-build observable: a live,
tail-able log announced up front, an in-place progress bar with elapsed
time, and no self-imposed timeout (the user watches and cancels instead).

## 1. `ToolContext.stream()` — the streaming subprocess primitive

`ToolContext.run` is buffered by design (`src/omc/toolctx.py:40-67`) and the
repo rule is "ToolContext stays the only subprocess boundary"
(`.omc/skills/review/SKILL.md`), so streaming gets a sibling method there:

```python
def stream(self, argv, *, on_line, cwd=None, extra_env=None) -> int
```

- `subprocess.Popen`, text mode, `stdin=DEVNULL`, env =
  `child_env() + extra_env`.
- stdout and stderr are SEPARATE pipes read by two line-reader threads —
  NOT `stderr=STDOUT` into one pipe: pipe writes beyond PIPE_BUF are not
  atomic, so a >4KB stdout JSON line (a large tool result) could splice
  with a stderr warning MID-LINE and corrupt the event — worst case the
  final result event carrying the OMC_STAGE verdict. Two readers guarantee
  line integrity per stream; interleaving is at line granularity.
- Each complete line (newline-stripped) goes to `on_line(str)`; the
  callback is invoked from reader threads, one line at a time (a lock or
  queue keeps calls serialized — callers get whole lines, never torn ones).
- Returns the child's exit code. `on_line` exceptions are not swallowed —
  callers own their callbacks.
- **No timeout parameter.** Deliberate: builds may legitimately run an
  hour. Liveness is the user's call, made visible by the elapsed clock.

## 2. Provider streaming variants

Only claude buffers; codex and opencode already emit incremental text. Two
additions to the provider interface (base defaults, claude overrides):

- `headless_stream_argv(prompt, *, model, allowed_tools=None)` —
  claude: identical to `headless_argv` but `--output-format stream-json
  --verbose`; codex/opencode: same argv as `headless_argv`.
- `decode_stream_line(line) -> list[str]` — claude: parse the stream-json
  event and return human-readable text fragments (assistant text, a
  one-line `$ <command>` echo per tool_use, tool_result content, and the
  final `result` text); non-JSON lines pass through. codex/opencode:
  identity (`[line]`).

Empirically verified (2026-07-19 probe): stream-json events arrive live
(tool_use at +6.4s, tool_result at +14.5s of a 19s run), but a tool call's
output lands as ONE chunk when that call completes — intra-tool-call
progress is elapsed-only by nature.

The decoded text is what lands in the log: the log must be human-readable
(`tail -f` is a design goal), and `_parse_stage`'s line-anchored
`OMC_STAGE` regex (`src/omc/watch.py:109`) keeps working on decoded text —
raw stream-json would break it (the verdict would sit escaped inside a JSON
string).

## 3. `_auto_build` integration (src/omc/watch.py)

- Create the log file FIRST and announce it on the start line:
  `→ running project build stage via claude (LLM-heavy) — log: /tmp/omc-auto-build-….log`
- Stream via `ctx.stream(provider.headless_stream_argv(...), on_line=...)`:
  each decoded fragment is appended to the log and flushed immediately
  (tail-able mid-run), accumulated for verdict parsing, and fed to the
  progress engine.
- Bar rendering: TTY-gated (`sys.stderr.isatty()`); redraw in place with
  `\r` on stderr: `[====>             ]  21% (00:13:22)` (18-char bar,
  elapsed HH:MM:SS since stage start). Watch narration is sequential —
  nothing else writes to stderr while a stage runs — so in-place redraw is
  safe. Non-TTY: no bar; the log line suffices. On completion: clear the
  bar line, then the existing `✓ auto-build passed` / `✗ auto-build failed
  (…) — log: …` narration, unchanged.
- On finish, watch appends a sentinel line to the log:
  `--- omc: stage finished (rc N) ---` — a human marker for tailers and
  the termination signal for `omc internal build-progress` follow mode.
- Verdict: `_parse_stage` runs over the accumulated decoded text —
  contract unchanged (exit 0 AND `"passed": true` verdict required).
- **`_BUILD_TIMEOUT` is deleted** (with its `TimeoutExpired` branch). The
  stage child env additionally gets `CARGO_TERM_PROGRESS_WHEN=always` and
  `CARGO_TERM_PROGRESS_WIDTH=80` so piped cargo still emits its counters
  (it suppresses them on non-TTY otherwise); harmless for non-cargo
  projects. Injection point: the existing `extra_env` merge in
  `_auto_build` (`extra_env={**provider.title_env(), **_CARGO_PROGRESS_ENV}`,
  src/omc/watch.py:148) — `ToolContext` already layers extra_env over
  `child_env()`.
- `_HOOK_TIMEOUT` (post-watch hook, 600s) STAYS — different doctrine: "a
  stuck project hook must not wedge the loop". The hook gains only the
  up-front log-path announcement (log still written post-hoc there).
- Ctrl+C semantics unchanged: SIGINT hits the process group (child dies
  with watch), `KeyboardInterrupt` → `· stopped`.

## 4. `src/omc/buildprogress.py` — the progress engine

Pure, line-fed, unit-testable; no I/O. `ProgressTracker` consumes lines and
exposes `percent: int | None` and `elapsed: float`; a `render(width=18)`
helper produces the exact bar string.

Parser registry (ordered; latest match anywhere wins), first entries:

1. **cargo** — `(\d+)/(\d+)` counters as cargo prints them
   (`Building [=====>    ] 123/1288`, `(12/1288)`); percent =
   `100*done/total`, guarded against `total == 0`.
2. **pytest** — the right-margin `[ 28%]` progress percentages.
3. **generic** — any bare `NN%` (1–3 digits, word-bounded).

No match yet → indeterminate: `[      <=>         ] --% (00:13:22)` with the
marker bouncing per redraw. The registry is a module-level list of
`(name, regex, to_percent)` entries — adding a build system is one entry +
tests. (Deliberately NOT configurable per project yet — YAGNI until a real
project needs a custom pattern.)

## 5. `omc internal build-progress <logfile>`

Standalone viewer for a live log (any terminal, any time):

- Registered in `run_internal`'s if-chain (`src/omc/internal.py:114-148`)
  exactly like `notify` (own mini-ArgumentParser, lazy import).
- Follows the file tail -f-style (poll + read new lines), feeds the same
  `ProgressTracker`, renders the same bar in place on stderr.
- Elapsed counts from the log file's creation time.
- Exits 0 when it sees the sentinel `--- omc: stage finished (rc N) ---`;
  exits 0 on Ctrl+C (user is done watching); exits 2 on usage errors
  (missing file after a grace wait).

## 6. Out of scope

- The GitNexus wiki run keeps today's behavior (it has its own progress
  output; live pass-through is a separate follow-up).
- No per-project parser configuration.
- No change to `--enable-documentation`, hook execution, or stage
  semantics.

## 7. Testing (tests/unit/)

- **buildprogress engine**: cargo/pytest/generic extraction, latest-wins,
  total-zero guard, indeterminate state, exact bar rendering
  (`[====>             ]  21% (00:13:22)`), elapsed formatting (>1h).
- **ToolContext.stream**: real script child (every stdout AND stderr line
  reaches `on_line` whole — line-granular, never torn, serialized callback;
  per-stream order preserved; rc propagation; DEVNULL stdin).
- **claude decode_stream_line**: against captured REAL stream-json samples
  (checked-in fixture strings from the probe), incl. non-JSON passthrough
  and the final result event.
- **_auto_build streaming**: stub provider + script child — log exists and
  grows during the run, announced path matches, sentinel appended, verdict
  parsed from decoded text, `✓/✗` narration, no timeout kwarg in the call.
- **build-progress follow mode**: growing temp file → percent updates;
  sentinel → exit 0.
- README (watch section + command table) and module docstrings updated.
