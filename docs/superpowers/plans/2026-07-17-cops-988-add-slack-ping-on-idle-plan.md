# Idle Notifications (COPS-988) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an omc-launched LLM session needs attention (question, permission, turn end), the user gets a native macOS notification or a line in a tail-able log file.

**Architecture:** Detection is per-harness (Claude Code hooks file, Codex `-c notify=` argv, OpenCode plugin file); providers *describe* wiring purely and `omc start` materializes it in the worktree. Delivery is shared: every hook invokes `omc internal notify --provider <name>`, which normalizes the payload and dispatches to the backend configured in `~/.omc/config.json` (`notifications.backend`: `"macos"` → osascript, `"file://<abs path>"` → append a tab-separated line). Invariant: notifications never break work — wiring failures warn and continue; the sink always exits 0.

**Tech Stack:** Python 3.12 stdlib only (json, re, time, argparse, pathlib), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-17-cops-988-add-slack-ping-on-idle-design.md` — read it before starting.

## Global Constraints

- `notifications.enabled` defaults to **False** (opt-in); `backend` defaults to `"macos"`; only `"macos"` or `file://` + absolute path are valid backends.
- Providers stay **pure**: `notification_setup()` and `session_argv()` never touch disk or spawn processes.
- Every subprocess goes through `ToolContext.run` (`src/omc/toolctx.py`). Plain file I/O (log append, wiring writes) is NOT a subprocess and uses pathlib directly.
- `omc internal notify` prints nothing to stdout (internal stdout is for machines; notify has no machine contract) and exits 0 on everything except usage errors (exit 2).
- Notification body: only harness status strings — never transcript content (`last_assistant_message` is ignored), never ticket text.
- No daemons/launchd/cron anywhere. All processes are foreground children of the harness or omc.
- Provider quirks get a comment at the exact code site that depends on them.
- Run tests with `uv run pytest tests/unit/<file> -q` from the repo root. Run `uv run ruff check src tests && uv run ruff format --check src tests` before each commit (ruff: line length 100, `E,F,I,UP,B`).
- When a test snippet below opens with `import`/`from` lines, put those in the target file's TOP import block (ruff E402/I001 reject mid-file imports), keeping the alias shown (e.g. `import json as _json`) so the snippet's references resolve.

## File Structure

- `src/omc/config/schema.py` — add `NotificationsConfig`, wire into `Config`.
- `src/omc/config/store.py` — backend validator, bool coercion in `set_key`, value validation in `_hydrate`.
- `src/omc/configure.py` — two walkthrough prompts (enable, backend).
- `src/omc/notify.py` — **new**: sink argv builder, payload normalizers, backends, `run_notify`, `wire_worktree` + Claude-settings merge.
- `src/omc/internal.py` — `notify` subcommand dispatch.
- `src/omc/providers/base.py|claude.py|codex.py|opencode.py` — `notification_setup()` + `notify_sink_argv` parameter on `session_argv()`.
- `src/omc/start.py` — wiring call after worktree creation, dry-run plan line.
- Tests: `tests/unit/test_config_store.py`, **new** `tests/unit/test_notify.py`, `tests/unit/test_providers.py`, `tests/unit/test_internal.py`, `tests/unit/test_start.py`, **new** `tests/e2e/test_e2e_notify.py`.
- `README.md` — a short "Notifications" subsection.

---

### Task 1: Config schema, store validation, configure prompts

**Files:**
- Modify: `src/omc/config/schema.py`
- Modify: `src/omc/config/store.py`
- Modify: `src/omc/configure.py` (walkthrough block, `# pragma: no cover`)
- Test: `tests/unit/test_config_store.py`

**Interfaces:**
- Produces: `Config.notifications: NotificationsConfig` with `enabled: bool = False`, `backend: str = "macos"`; `store.validate_backend(value: str) -> str` (returns value or raises `ConfigError`); `store.set_key` accepting `notifications.enabled=true|false` and `notifications.backend=<value>`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_config_store.py`:

```python
def test_notifications_defaults(tmp_path):
    cfg = Config()
    assert cfg.notifications.enabled is False
    assert cfg.notifications.backend == "macos"
    store.save(tmp_path, cfg)
    loaded = store.load(tmp_path)
    assert loaded.notifications.enabled is False
    assert loaded.notifications.backend == "macos"


def test_notifications_missing_key_defaults(tmp_path):
    # configs written before this feature carry no notifications key at all
    (tmp_path / "config.json").write_text('{"schema_version": 1}')
    loaded = store.load(tmp_path)
    assert loaded.notifications.enabled is False
    assert loaded.notifications.backend == "macos"


def test_notifications_round_trip(tmp_path):
    cfg = Config()
    cfg.notifications.enabled = True
    cfg.notifications.backend = "file:///tmp/omc-notifications.log"
    store.save(tmp_path, cfg)
    loaded = store.load(tmp_path)
    assert loaded.notifications.enabled is True
    assert loaded.notifications.backend == "file:///tmp/omc-notifications.log"


def test_set_key_notifications_enabled_coerces_bool():
    cfg = Config()
    store.set_key(cfg, "notifications.enabled", "true")
    assert cfg.notifications.enabled is True
    store.set_key(cfg, "notifications.enabled", "false")
    assert cfg.notifications.enabled is False
    with pytest.raises(ConfigError, match="true or false"):
        store.set_key(cfg, "notifications.enabled", "yes")


def test_set_key_notifications_backend_validated():
    cfg = Config()
    store.set_key(cfg, "notifications.backend", "file:///var/log/omc.log")
    assert cfg.notifications.backend == "file:///var/log/omc.log"
    store.set_key(cfg, "notifications.backend", "macos")
    assert cfg.notifications.backend == "macos"
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.set_key(cfg, "notifications.backend", "file://relative/path")
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.set_key(cfg, "notifications.backend", "slack")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "notifications.bogus", "x")


def test_hydrate_rejects_bad_notification_values(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "notifications": {"enabled": "true"}}'
    )
    with pytest.raises(ConfigError, match="notifications.enabled"):
        store.load(tmp_path)
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "notifications": {"backend": "slack"}}'
    )
    with pytest.raises(ConfigError, match="notifications.backend"):
        store.load(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_store.py -q`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'notifications'` (and collection succeeds; if `pytest`/`ConfigError` imports are missing at the top of the file, they are already imported there — check first).

- [ ] **Step 3: Implement schema** — in `src/omc/config/schema.py`, add before `Config`:

```python
@dataclass
class NotificationsConfig:
    enabled: bool = False  # opt-in
    backend: str = "macos"  # "macos" | "file://<absolute path>"
```

and in `Config`:

```python
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
```

- [ ] **Step 4: Implement store validation** — in `src/omc/config/store.py`:

Import `NotificationsConfig` alongside the existing schema imports. Add near the top:

```python
def validate_backend(value: str) -> str:
    """'macos' or file:// + absolute path; shared by load and set paths."""
    if value == "macos":
        return value
    if value.startswith("file://") and value[len("file://") :].startswith("/"):
        return value
    raise ConfigError(
        f"invalid notifications.backend {value!r}: use 'macos' or 'file:///absolute/path'"
    )
```

In `set_key`, after the `LLMConfig`/providers special case, add:

```python
    if isinstance(cfg, NotificationsConfig):
        # set_key values arrive as strings; enabled is a bool ("true" would be
        # truthy as a string even when the user meant false) and backend has a
        # closed scheme set — both need explicit handling.
        if head == "enabled":
            if value not in ("true", "false"):
                raise ConfigError(f"notifications.enabled expects true or false, got {value!r}")
            cfg.enabled = value == "true"
            return
        if head == "backend":
            cfg.backend = validate_backend(value)
            return
        raise ConfigError(f"unknown config key: notifications.{head}")
```

In `_hydrate`, change the final `return cls(**kwargs)` to validate hydrated values (JSON can carry any type):

```python
    obj = cls(**kwargs)
    if cls is NotificationsConfig:
        if not isinstance(obj.enabled, bool):
            raise ConfigError(
                f"invalid value for 'notifications.enabled' in {path}: expected true/false"
            )
        if not isinstance(obj.backend, str):
            raise ConfigError(f"invalid value for 'notifications.backend' in {path}")
        validate_backend(obj.backend)
    return obj
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_store.py -q`
Expected: PASS (all, including pre-existing).

- [ ] **Step 6: Add walkthrough prompts** — in `src/omc/configure.py`, `_walkthrough` (inside the existing `# pragma: no cover` function), after the base-branch prompt append:

```python
    enable = questionary.confirm(
        "Notify when a session needs attention (macOS notification / log file)?",
        default=cfg.notifications.enabled,
    ).ask()
    cfg.notifications.enabled = bool(enable)
    if enable:
        while True:
            backend = (
                questionary.text(
                    "Notification backend: 'macos' or file:///absolute/path.log",
                    default=cfg.notifications.backend,
                ).ask()
                or cfg.notifications.backend
            )
            try:
                cfg.notifications.backend = store.validate_backend(backend)
                break
            except ConfigError as exc:
                print(exc)
```

Add `from .errors import ConfigError, Refusal` (extend the existing `Refusal` import).

- [ ] **Step 7: Full unit suite + lint, then commit**

Run: `uv run pytest tests/unit -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS / clean.

```bash
git add src/omc/config/schema.py src/omc/config/store.py src/omc/configure.py tests/unit/test_config_store.py
git commit -m "feat: notifications config — opt-in enabled flag + validated backend (red->green)"
```

---

### Task 2: notify.py delivery core — payload normalizers and backends

**Files:**
- Create: `src/omc/notify.py`
- Test: `tests/unit/test_notify.py` (new)

**Interfaces:**
- Consumes: `Config.notifications` (Task 1), `ToolContext.run` / `ctx.env`.
- Produces: `notify.sink_argv(provider_name: str) -> list[str]` = `["omc", "internal", "notify", "--provider", <name>]`; `notify.payload_from_claude(stdin_text: str) -> tuple[str, str]` (event, body); `notify.payload_from_codex(arg: str | None) -> tuple[str, str]`; `notify.deliver(ctx, cfg, *, provider: str, event: str, body: str, cwd: str) -> None`; `notify.GENERIC_BODY = "needs your attention"`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_notify.py`:

```python
import sys

from omc import notify
from omc.config.schema import Config
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env


def _ctx(tmp_path, **env):
    bindir = tmp_path / "bin"
    make_stub(bindir, "osascript")
    return ToolContext.from_env(stub_env(bindir, **env))


def _file_cfg(path):
    cfg = Config()
    cfg.notifications.enabled = True
    cfg.notifications.backend = f"file://{path}"
    return cfg


def test_sink_argv():
    assert notify.sink_argv("claude") == ["omc", "internal", "notify", "--provider", "claude"]


def test_payload_from_claude():
    ev, body = notify.payload_from_claude(
        '{"hook_event_name": "Notification", "message": "Claude needs your permission"}'
    )
    assert (ev, body) == ("Notification", "Claude needs your permission")
    assert notify.payload_from_claude('{"hook_event_name": "Stop"}') == ("Stop", "turn complete")
    # malformed payloads degrade, never raise
    assert notify.payload_from_claude("{nope") == ("unknown", notify.GENERIC_BODY)
    assert notify.payload_from_claude("") == ("unknown", notify.GENERIC_BODY)
    assert notify.payload_from_claude("[1]") == ("unknown", notify.GENERIC_BODY)


def test_payload_from_codex():
    ev, body = notify.payload_from_codex('{"type": "agent-turn-complete"}')
    assert (ev, body) == ("agent-turn-complete", "turn complete")
    assert notify.payload_from_codex(None) == ("unknown", notify.GENERIC_BODY)
    assert notify.payload_from_codex("junk") == ("unknown", notify.GENERIC_BODY)


def test_file_backend_appends_formatted_line(tmp_path):
    log = tmp_path / "n.log"
    ctx = _ctx(tmp_path, OMC_SLUG="proj-1-fix")
    notify.deliver(
        _file_cfg(log), ctx=ctx, provider="claude", event="Notification",
        body="needs permission", cwd=str(tmp_path),
    )
    notify.deliver(
        _file_cfg(log), ctx=ctx, provider="codex", event="agent-turn-complete",
        body="turn complete", cwd=str(tmp_path),
    )
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    ts, slug, provider, event, body = lines[0].split("\t")
    assert slug == "proj-1-fix" and provider == "claude"
    assert event == "Notification" and body == "needs permission"
    assert "T" in ts  # ISO-8601-ish timestamp
    assert lines[1].split("\t")[2:] == ["codex", "agent-turn-complete", "turn complete"]


def test_file_backend_escapes_control_chars(tmp_path):
    log = tmp_path / "n.log"
    ctx = _ctx(tmp_path, OMC_SLUG="s")
    notify.deliver(
        _file_cfg(log), ctx=ctx, provider="claude", event="Notification",
        body="line1\nline2\tcol", cwd=str(tmp_path),
    )
    (line,) = log.read_text().splitlines()
    assert line.split("\t")[4] == "line1 line2 col"  # body stays ONE column


def test_file_backend_slug_falls_back_to_cwd_basename(tmp_path):
    log = tmp_path / "n.log"
    ctx = _ctx(tmp_path)  # no OMC_SLUG in env
    wt = tmp_path / "repo.feature-x"
    wt.mkdir()
    notify.deliver(
        _file_cfg(log), ctx=ctx, provider="claude", event="e", body="b", cwd=str(wt),
    )
    assert log.read_text().split("\t")[1] == "repo.feature-x"


def test_file_backend_failure_is_silent_no_raise(tmp_path, capsys):
    cfg = _file_cfg(tmp_path / "missing-dir" / "n.log")  # parent doesn't exist
    notify.deliver(
        cfg, ctx=_ctx(tmp_path), provider="claude", event="e", body="b", cwd=str(tmp_path),
    )  # must not raise
    assert "cannot append" in capsys.readouterr().err


def test_macos_backend_runs_osascript_on_darwin_only(tmp_path, monkeypatch):
    calls = []

    class FakeCtx:
        env = {"OMC_SLUG": "s-1"}

        def run(self, argv, **kwargs):
            calls.append(list(argv))

    cfg = Config()
    cfg.notifications.enabled = True  # backend stays "macos"
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(cfg, ctx=FakeCtx(), provider="claude", event="e",
                   body='say "hi" \\ there', cwd=str(tmp_path))
    (argv,) = calls
    assert argv[0] == "osascript" and argv[1] == "-e"
    # body/title are escaped AppleScript string literals — quotes/backslashes are data
    assert argv[2] == (
        'display notification "say \\"hi\\" \\\\ there" with title "omc: s-1"'
    )
    monkeypatch.setattr(sys, "platform", "linux")
    notify.deliver(cfg, ctx=FakeCtx(), provider="claude", event="e", body="b", cwd=str(tmp_path))
    assert len(calls) == 1  # non-darwin: no-op


def test_macos_backend_oserror_is_swallowed(tmp_path, monkeypatch):
    class BrokenCtx:
        env = {}

        def run(self, argv, **kwargs):
            raise OSError("no osascript")

    cfg = Config()
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(cfg, ctx=BrokenCtx(), provider="claude", event="e", body="b",
                   cwd=str(tmp_path))  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_notify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'omc.notify'` (import error at collection).

- [ ] **Step 3: Implement** — create `src/omc/notify.py`:

```python
"""Idle notifications (COPS-988): sessions that need attention ping the user.

Detection is per-harness — providers DESCRIBE their wiring
(`Provider.notification_setup`) and `wire_worktree` materializes it at
`omc start`. Delivery is shared: every hook invokes
`omc internal notify --provider <name>`, which normalizes the payload and
dispatches on `notifications.backend` ("macos" → osascript;
"file://<abs path>" → one tab-separated line, tail-friendly). Invariant:
notifications never break work — failures warn at most; the sink exits 0.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from .config.schema import Config
from .toolctx import ToolContext

GENERIC_BODY = "needs your attention"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def sink_argv(provider_name: str) -> list[str]:
    """The command every harness hook invokes (omc is on PATH as a uv tool)."""
    return ["omc", "internal", "notify", "--provider", provider_name]


def deliver(
    cfg: Config, *, ctx: ToolContext, provider: str, event: str, body: str, cwd: str
) -> None:
    """Send one notification to the configured backend. Never raises."""
    slug = ctx.env.get("OMC_SLUG") or Path(cwd).resolve().name
    backend = cfg.notifications.backend
    if backend == "macos":
        _deliver_macos(ctx, slug, body)
    elif backend.startswith("file://"):
        _deliver_file(Path(backend[len("file://") :]), slug, provider, event, body)


def _applescript_str(s: str) -> str:
    """AppleScript string literal — payload text is data, never code."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _deliver_macos(ctx: ToolContext, slug: str, body: str) -> None:
    if sys.platform != "darwin":
        return  # backend seam: notify-send etc. are later drop-ins
    script = (
        f"display notification {_applescript_str(body)}"
        f" with title {_applescript_str(f'omc: {slug}')}"
    )
    try:
        ctx.run(["osascript", "-e", script])
    except OSError:
        pass  # notifications never break work


def _clean(column: str) -> str:
    """One log column: control chars (incl. tab/newline) become spaces."""
    return _CTRL.sub(" ", column)


def _deliver_file(path: Path, slug: str, provider: str, event: str, body: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cols = [ts, _clean(slug), _clean(provider), _clean(event), _clean(body)]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\t".join(cols) + "\n")  # one write per line: append-atomic enough
    except OSError as exc:
        print(f"omc notify: cannot append to {path}: {exc}", file=sys.stderr)


def payload_from_claude(stdin_text: str) -> tuple[str, str]:
    """Claude hooks pass JSON on stdin; body comes from its `message` field.
    `last_assistant_message` is deliberately ignored — no transcript content
    reaches the notification center or log."""
    try:
        data = json.loads(stdin_text or "{}")
    except json.JSONDecodeError:
        return "unknown", GENERIC_BODY
    if not isinstance(data, dict):
        return "unknown", GENERIC_BODY
    event = str(data.get("hook_event_name") or "unknown")
    if event == "Stop":
        return event, "turn complete"
    return event, str(data.get("message") or GENERIC_BODY)


def payload_from_codex(arg: str | None) -> tuple[str, str]:
    # codex passes ONE JSON argument to its notify program; the only event it
    # emits today is agent-turn-complete (verified July 2026).
    try:
        data = json.loads(arg or "")
    except json.JSONDecodeError:
        return "unknown", GENERIC_BODY
    if not isinstance(data, dict):
        return "unknown", GENERIC_BODY
    event = str(data.get("type") or "unknown")
    return event, "turn complete" if event == "agent-turn-complete" else GENERIC_BODY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_notify.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`

```bash
git add src/omc/notify.py tests/unit/test_notify.py
git commit -m "feat: notification delivery core — macos + file backends, payload normalizers (red->green)"
```

---

### Task 3: `omc internal notify` entry point

**Files:**
- Modify: `src/omc/internal.py`
- Modify: `src/omc/notify.py` (add `run_notify`)
- Test: `tests/unit/test_internal.py`, `tests/unit/test_notify.py`

**Interfaces:**
- Consumes: `notify.payload_from_claude/from_codex`, `notify.deliver`, `store.load` (Task 1/2).
- Produces: CLI `omc internal notify --provider {claude,codex,opencode} [--event E] [--message M] [payload]`; `notify.run_notify(ctx: ToolContext, args: argparse.Namespace) -> int` (always 0).

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_internal.py`:

```python
def test_notify_usage_errors(capsys):
    assert run_internal(["notify"]) == 2  # --provider is required
    assert run_internal(["notify", "--provider", "cursor"]) == 2  # unknown provider
    assert "usage:" in capsys.readouterr().err


def test_notify_dispatches(tmp_path, monkeypatch):
    # the RED test: before the notify branch exists this hits the usage
    # fallthrough (exit 2); afterwards run_notify returns 0 (no config ->
    # silent no-op, stdin never read)
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    assert run_internal(["notify", "--provider", "claude"]) == 0
```

and append to `tests/unit/test_notify.py`:

```python
import argparse
import json as _json

from omc.config import store


def _notify_args(provider, payload=None, event="", message=""):
    return argparse.Namespace(provider=provider, payload=payload, event=event, message=message)


def _saved_cfg(home, *, enabled, log):
    cfg = Config()
    cfg.notifications.enabled = enabled
    cfg.notifications.backend = f"file://{log}"
    store.save(home, cfg)


def test_run_notify_disabled_is_silent_kill_switch(tmp_path):
    home = tmp_path / "home"
    log = tmp_path / "n.log"
    _saved_cfg(home, enabled=False, log=log)
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path)})
    rc = notify.run_notify(ctx, _notify_args("codex", payload='{"type":"agent-turn-complete"}'))
    assert rc == 0
    assert not log.exists()  # disabled config silences even wired worktrees


def test_run_notify_codex_and_opencode_paths(tmp_path):
    home = tmp_path / "home"
    log = tmp_path / "n.log"
    _saved_cfg(home, enabled=True, log=log)
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path), "OMC_SLUG": "s-9"})
    assert notify.run_notify(
        ctx, _notify_args("codex", payload=_json.dumps({"type": "agent-turn-complete"}))
    ) == 0
    assert notify.run_notify(
        ctx, _notify_args("opencode", event="session.idle", message="session ready")
    ) == 0
    lines = [ln.split("\t") for ln in log.read_text().splitlines()]
    assert [ln[2:] for ln in lines] == [
        ["codex", "agent-turn-complete", "turn complete"],
        ["opencode", "session.idle", "session ready"],
    ]
    assert all(ln[1] == "s-9" for ln in lines)


def test_run_notify_claude_reads_stdin(tmp_path, monkeypatch):
    import io

    home = tmp_path / "home"
    log = tmp_path / "n.log"
    _saved_cfg(home, enabled=True, log=log)
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path), "OMC_SLUG": "s"})
    stdin = io.StringIO('{"hook_event_name": "Notification", "message": "needs permission"}')
    stdin.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", stdin)
    assert notify.run_notify(ctx, _notify_args("claude")) == 0
    assert log.read_text().split("\t")[3:] == ["Notification", "needs permission\n"]


def test_run_notify_survives_broken_or_missing_config(tmp_path):
    home = tmp_path / "home"
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path)})
    assert notify.run_notify(ctx, _notify_args("claude")) == 0  # no config at all
    home.mkdir(parents=True)
    (home / "config.json").write_text("{broken")
    assert notify.run_notify(ctx, _notify_args("claude")) == 0  # ConfigError swallowed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_notify.py tests/unit/test_internal.py -q`
Expected: FAIL — `AttributeError: module 'omc.notify' has no attribute 'run_notify'`; internal usage test fails with exit 2 vs actual (unknown command already returns 2 — the `--provider cursor` case FAILS today because `notify` is not dispatched; verify the first assertion fails before implementing).

- [ ] **Step 3: Implement** — in `src/omc/notify.py` add (imports: `argparse`, `os`, and `from .config import store`, `from .errors import ConfigError` join the existing ones):

```python
def run_notify(ctx: ToolContext, args: argparse.Namespace) -> int:
    """`omc internal notify` body. Always exit 0 — a notification must never
    fail a session; usage errors (exit 2) are handled by the dispatcher."""
    try:
        cfg = store.load(ctx.home)
    except ConfigError:
        return 0
    if cfg is None or not cfg.notifications.enabled:
        return 0  # global kill switch — even for already-wired worktrees
    if args.provider == "claude":
        # hooks pipe the payload via stdin; a TTY means a human poking at it
        text = "" if sys.stdin.isatty() else sys.stdin.read()
        event, body = payload_from_claude(text)
    elif args.provider == "codex":
        event, body = payload_from_codex(args.payload)
    else:  # opencode: the generated plugin passes explicit flags
        event, body = (args.event or "unknown", args.message or GENERIC_BODY)
    deliver(cfg, ctx=ctx, provider=args.provider, event=event, body=body, cwd=os.getcwd())
    return 0
```

In `src/omc/internal.py`: extend `_USAGE` to

```python
_USAGE = (
    "usage: omc internal {rebase-main [--base BRANCH] | wt-template"
    " | notify --provider NAME [--event E] [--message M] [payload]}"
)
```

and add to `run_internal`, before the final fallthrough:

```python
    if cmd == "notify":
        parser = argparse.ArgumentParser(prog="omc internal notify", add_help=False)
        parser.add_argument("--provider", required=True, choices=("claude", "codex", "opencode"))
        parser.add_argument("--event", default="")
        parser.add_argument("--message", default="")
        parser.add_argument("payload", nargs="?", default=None)  # codex's single JSON arg
        try:
            args = parser.parse_args(rest)
        except SystemExit:
            print(_USAGE, file=sys.stderr)
            return 2
        from .notify import run_notify

        return run_notify(ToolContext.from_env(), args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_notify.py tests/unit/test_internal.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add src/omc/notify.py src/omc/internal.py tests/unit/test_notify.py tests/unit/test_internal.py
git commit -m "feat: omc internal notify — payload-normalizing sink, kill-switch aware (red->green)"
```

---

### Task 4: Provider wiring descriptions — `notification_setup()` + `notify_sink_argv`

**Files:**
- Modify: `src/omc/providers/base.py`
- Modify: `src/omc/providers/claude.py`
- Modify: `src/omc/providers/codex.py`
- Modify: `src/omc/providers/opencode.py`
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: `notify.sink_argv(name)` output shape (a plain `list[str]`, passed in — providers never import notify).
- Produces: `Provider.notification_setup(sink_argv: list[str]) -> dict[str, str]` (worktree-relative path → file content; base default `{}`); `Provider.session_argv(..., notify_sink_argv: list[str] | None = None)` on all providers.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_providers.py`:

```python
import json

SINK = ["omc", "internal", "notify", "--provider", "X"]


def test_claude_notification_setup_settings_file():
    files = get_provider("claude").notification_setup(
        ["omc", "internal", "notify", "--provider", "claude"]
    )
    assert list(files) == [".claude/settings.local.json"]
    settings = json.loads(files[".claude/settings.local.json"])
    cmd = "omc internal notify --provider claude"
    for event in ("Notification", "Stop"):
        (group,) = settings["hooks"][event]
        assert group["hooks"] == [{"type": "command", "command": cmd}]
    # Notification is UNFILTERED (no matcher key): all attention events ping
    assert "matcher" not in settings["hooks"]["Notification"][0]


def test_codex_notify_sink_argv_before_seed():
    p = get_provider("codex")
    sink = ["omc", "internal", "notify", "--provider", "codex"]
    argv = p.session_argv(session_name="n", model="m", seed="s", notify_sink_argv=sink)
    # -c value is TOML; a JSON array of strings is valid TOML array syntax,
    # and the flag must come BEFORE the trailing positional seed
    assert argv == ["codex", "-m", "m", "-c", f"notify={json.dumps(sink)}", "s"]
    assert p.session_argv(session_name="n", model="", seed="s") == ["codex", "s"]
    assert p.notification_setup(sink) == {}  # codex wiring is argv-only


def test_opencode_notification_setup_plugin_file():
    files = get_provider("opencode").notification_setup(
        ["omc", "internal", "notify", "--provider", "opencode"]
    )
    assert list(files) == [".opencode/plugin/omc-notify.js"]
    js = files[".opencode/plugin/omc-notify.js"]
    assert "omc internal notify --provider opencode" in js
    for event in ("session.idle", "permission.asked", "session.error"):
        assert event in js
    assert "generated by omc" in js  # foreign-content detection marker


def test_notification_setup_defaults_and_purity(tmp_path, monkeypatch):
    # default is {}; and no provider touches the filesystem or spawns anything
    monkeypatch.chdir(tmp_path)
    for name in provider_names():
        p = get_provider(name)
        p.notification_setup(SINK)
        p.session_argv(session_name="n", model="", seed="s", notify_sink_argv=SINK)
    assert list(tmp_path.iterdir()) == []


def test_claude_opencode_ignore_notify_sink_argv():
    # their wiring is a FILE; argv must stay identical with/without the param
    for name in ("claude", "opencode"):
        p = get_provider(name)
        with_arg = p.session_argv(session_name="n", model="m", seed="s", notify_sink_argv=SINK)
        without = p.session_argv(session_name="n", model="m", seed="s")
        assert with_arg == without
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_providers.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'notification_setup'` / `TypeError: session_argv() got an unexpected keyword argument`.

- [ ] **Step 3: Implement base** — in `src/omc/providers/base.py`, change the `session_argv` abstract signature to:

```python
    @abstractmethod
    def session_argv(
        self,
        *,
        session_name: str,
        model: str,
        seed: str,
        notify_sink_argv: list[str] | None = None,
    ) -> list[str]:
        """Interactive session seeded with ``seed``; named where the CLI supports it.

        ``notify_sink_argv``, when set, is the notification sink command; the
        provider that wires notifications via argv (codex) places it itself —
        flag ordering is provider-specific. File-wired providers ignore it.
        """
```

and add after it:

```python
    def notification_setup(self, sink_argv: list[str]) -> dict[str, str]:
        """Worktree-relative path -> file content wiring this provider's
        "needs attention" events to ``sink_argv``. {} = no file wiring.
        Pure like everything here — the caller writes the files."""
        return {}
```

- [ ] **Step 4: Implement claude.py** — add imports `import json` and `import shlex`; extend `session_argv` with `notify_sink_argv=None` in the signature (body unchanged — wiring is the settings file, not argv); add:

```python
    def notification_setup(self, sink_argv):
        # Notification stays UNFILTERED (all attention events) + Stop for turn
        # end — per the COPS-988 design. settings.local.json is Claude's
        # personal per-checkout settings file (conventionally gitignored).
        group = {"hooks": [{"type": "command", "command": shlex.join(sink_argv)}]}
        settings = {"hooks": {"Notification": [group], "Stop": [group]}}
        return {".claude/settings.local.json": json.dumps(settings, indent=2) + "\n"}
```

- [ ] **Step 5: Implement codex.py** — add `import json`; replace `session_argv`:

```python
    def session_argv(self, *, session_name, model, seed, notify_sink_argv=None):
        # No session-name flag exists — codex names sessions internally; omc's
        # terminal title carries the slug instead.
        argv = ["codex"]
        if model:
            argv += ["-m", model]
        if notify_sink_argv:
            # -c overrides one config.toml key for THIS session only (the global
            # config is never touched). The value is TOML — a JSON array of
            # strings happens to be valid TOML array syntax. Must precede the
            # seed: the prompt is a trailing positional.
            argv += ["-c", f"notify={json.dumps(notify_sink_argv)}"]
        argv.append(seed)
        return argv
```

- [ ] **Step 6: Implement opencode.py** — extend `session_argv` with `notify_sink_argv=None` (body unchanged); add:

```python
    def notification_setup(self, sink_argv):
        # A generated project plugin (no npm deps): opencode loads
        # .opencode/plugin/*.js and calls `event` for every bus event. The
        # "generated by omc" first line doubles as the foreign-content marker
        # wire_worktree checks before ever overwriting this file.
        cmd = " ".join(sink_argv)
        plugin = f"""\
// generated by omc — idle notifications (COPS-988); safe to delete.
export const OmcNotify = async ({{ $ }}) => ({{
  event: async ({{ event }}) => {{
    const bodies = {{
      "session.idle": "session ready",
      "permission.asked": "permission needed",
      "session.error": "session error",
    }};
    const body = bodies[event.type];
    if (!body) return;
    await $`{cmd} --event ${{event.type}} --message ${{body}}`.quiet().nothrow();
  }},
}});
"""
        return {".opencode/plugin/omc-notify.js": plugin}
```

- [ ] **Step 7: Run tests to verify they pass; run the whole unit suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS (existing `session_argv` call sites pass no `notify_sink_argv` and stay valid — the parameter is keyword-only with a default).

- [ ] **Step 8: Lint + commit**

```bash
git add src/omc/providers/base.py src/omc/providers/claude.py src/omc/providers/codex.py src/omc/providers/opencode.py tests/unit/test_providers.py
git commit -m "feat: providers describe notification wiring — settings file / -c notify / plugin (red->green)"
```

---

### Task 5: Worktree wiring executor + `omc start` integration

**Files:**
- Modify: `src/omc/notify.py` (add `merge_claude_settings`, `wire_worktree`)
- Modify: `src/omc/start.py`
- Test: `tests/unit/test_notify.py`, `tests/unit/test_start.py`

**Interfaces:**
- Consumes: `Provider.notification_setup` (Task 4), `notify.sink_argv` (Task 2), `Config.notifications` (Task 1).
- Produces: `notify.merge_claude_settings(existing_text: str, ours_text: str) -> str | None` (merged JSON text; None = existing unparseable); `notify.wire_worktree(provider, worktree: Path) -> list[str]` (relative paths written; warns+skips on failure — pure file I/O, no ToolContext or Config needed).

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_notify.py`:

```python
from pathlib import Path

from omc.providers.registry import get_provider


def _wire(tmp_path, provider_name="claude"):
    return notify.wire_worktree(get_provider(provider_name), tmp_path)


def test_wire_worktree_writes_fresh_files(tmp_path):
    written = _wire(tmp_path)
    assert written == [".claude/settings.local.json"]
    settings = _json.loads((tmp_path / ".claude/settings.local.json").read_text())
    assert "Notification" in settings["hooks"] and "Stop" in settings["hooks"]
    assert _wire(tmp_path, "opencode") == [".opencode/plugin/omc-notify.js"]
    assert _wire(tmp_path, "codex") == []  # argv-wired, no files


def test_wire_worktree_merges_existing_claude_settings(tmp_path):
    target = tmp_path / ".claude" / "settings.local.json"
    target.parent.mkdir(parents=True)
    target.write_text(_json.dumps({
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"Notification": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]},
    }))
    assert _wire(tmp_path) == [".claude/settings.local.json"]
    merged = _json.loads(target.read_text())
    assert merged["permissions"] == {"allow": ["Bash(ls:*)"]}  # foreign keys survive
    cmds = [h["command"] for g in merged["hooks"]["Notification"] for h in g["hooks"]]
    assert cmds == ["mine.sh", "omc internal notify --provider claude"]
    assert [h["command"] for g in merged["hooks"]["Stop"] for h in g["hooks"]] == [
        "omc internal notify --provider claude"
    ]


def test_wire_worktree_is_idempotent(tmp_path):
    _wire(tmp_path)
    first = (tmp_path / ".claude/settings.local.json").read_text()
    assert _wire(tmp_path) == []  # nothing to change on the second run
    assert (tmp_path / ".claude/settings.local.json").read_text() == first


def test_wire_worktree_leaves_corrupt_settings_alone(tmp_path, capsys):
    target = tmp_path / ".claude" / "settings.local.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    assert _wire(tmp_path) == []
    assert target.read_text() == "{not json"  # untouched
    assert "leaving it alone" in capsys.readouterr().err


def test_wire_worktree_leaves_foreign_plugin_alone(tmp_path, capsys):
    target = tmp_path / ".opencode" / "plugin" / "omc-notify.js"
    target.parent.mkdir(parents=True)
    target.write_text("// the user's own plugin\n")
    assert _wire(tmp_path, "opencode") == []
    assert target.read_text() == "// the user's own plugin\n"
    assert "leaving it alone" in capsys.readouterr().err
```

and append to `tests/unit/test_start.py`:

```python
def _notify_cfg():
    cfg = Config()
    cfg.notifications.enabled = True
    return cfg


def test_start_wires_notifications_when_enabled(tmp_path, capsys):
    ctx = full_env(tmp_path)
    wt = tmp_path / "wtree"
    wt.mkdir()
    rc = run_start(ctx, _notify_cfg(), "PROJ-1", headless=True)
    assert rc == 0
    settings = json.loads((wt / ".claude" / "settings.local.json").read_text())
    assert "Notification" in settings["hooks"]
    assert "✓ notification wiring: .claude/settings.local.json" in capsys.readouterr().err


def test_start_skips_wiring_when_disabled(tmp_path):
    ctx = full_env(tmp_path)
    wt = tmp_path / "wtree"
    wt.mkdir()
    rc = run_start(ctx, Config(), "PROJ-1", headless=True)
    assert rc == 0
    assert not (wt / ".claude").exists()


def test_dry_run_shows_notify_plan(tmp_path, capsys):
    ctx = full_env(tmp_path)
    rc = run_start(ctx, _notify_cfg(), "PROJ-1", dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "notify:       backend macos; files: .claude/settings.local.json" in out
    rc = run_start(ctx, Config(), "PROJ-1", dry_run=True)
    assert "notify:       disabled" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_notify.py tests/unit/test_start.py -q`
Expected: FAIL — `AttributeError: module 'omc.notify' has no attribute 'wire_worktree'`; start tests fail on missing wiring/plan line.

- [ ] **Step 3: Implement merge + executor** — add to `src/omc/notify.py`:

```python
def _warn(msg: str) -> None:
    print(f"· notify wiring: {msg}", file=sys.stderr, flush=True)


def merge_claude_settings(existing_text: str, ours_text: str) -> str | None:
    """Append our hook groups into an existing settings JSON; None when the
    existing file can't be treated as settings (invalid JSON / wrong shapes).
    Idempotent: events already containing our command are left untouched."""
    try:
        existing = json.loads(existing_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(existing, dict):
        return None
    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return None
    for event, groups in json.loads(ours_text)["hooks"].items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            return None
        ours_cmd = groups[0]["hooks"][0]["command"]
        present = any(
            hook.get("command") == ours_cmd
            for group in entries
            if isinstance(group, dict)
            for hook in group.get("hooks", [])
            if isinstance(hook, dict)
        )
        if not present:
            entries.extend(groups)
    return json.dumps(existing, indent=2) + "\n"


def wire_worktree(provider, worktree: Path) -> list[str]:
    """Materialize the provider's notification wiring in the worktree.
    Returns the relative paths written/updated. Failures warn and skip —
    wiring must never block `omc start`. The merge path is load-bearing:
    wt's copy-ignored hook may have copied the PRIMARY checkout's
    settings.local.json into this worktree before we run."""
    written: list[str] = []
    for rel, content in provider.notification_setup(sink_argv(provider.name)).items():
        target = worktree / rel
        try:
            if target.name == "settings.local.json" and target.exists():
                existing = target.read_text()
                merged = merge_claude_settings(existing, content)
                if merged is None:
                    _warn(f"{rel} is not valid settings JSON — leaving it alone")
                    continue
                if merged != existing:
                    target.write_text(merged)
                    written.append(rel)
            elif target.exists():
                if target.read_text() != content:
                    _warn(f"{rel} exists with foreign content — leaving it alone")
                continue
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                written.append(rel)
        except OSError as exc:
            _warn(f"could not write {rel}: {exc}")
    return written
```

- [ ] **Step 4: Integrate into start.py** — in `src/omc/start.py`:

Add imports: `from pathlib import Path` and `from . import notify`.

In `run_start`, replace the `session_argv = ...` line with:

```python
    notify_argv = notify.sink_argv(name) if cfg.notifications.enabled else None
    session_argv = provider.session_argv(
        session_name=slug, model=model, seed=seed, notify_sink_argv=notify_argv
    )
```

In the `dry_run` branch, before `_print_plan(...)`, compute the plan line and pass it through:

```python
        if cfg.notifications.enabled:
            files = provider.notification_setup(notify.sink_argv(name))
            what = ", ".join(files) or "none (argv only)"
            notify_desc = f"backend {cfg.notifications.backend}; files: {what}"
        else:
            notify_desc = "disabled"
        _print_plan(branch, base, wt_argv, title_seq, session_argv, shell_argv, notify_desc)
```

Extend `_print_plan`:

```python
def _print_plan(branch, base, wt_argv, title_seq, session_argv, shell_argv, notify_desc):
    print("omc start — plan (dry run, no changes made):")
    print(f"  branch:       {branch}")
    print(f"  fetch:        git fetch origin {base}")
    print(f"  worktree cmd: {shlex.join(wt_argv)}")
    print(f"  title seq:    {title_seq!r}")
    print(f"  session argv: {session_argv}")
    print(f"  shell argv:   {shell_argv}")
    print(f"  notify:       {notify_desc}")
```

After the `✓ worktree:` line (both headless and interactive paths reach it), add:

```python
    if cfg.notifications.enabled:
        wired = notify.wire_worktree(provider, Path(path))
        if wired:
            _say(f"✓ notification wiring: {', '.join(wired)}")
```

- [ ] **Step 5: Run tests to verify they pass; whole unit suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
git add src/omc/notify.py src/omc/start.py tests/unit/test_notify.py tests/unit/test_start.py
git commit -m "feat: omc start wires notifications into the worktree — merge-safe, dry-run visible (red->green)"
```

---

### Task 6: E2E (file backend, headless container) + README

**Files:**
- Create: `tests/e2e/test_e2e_notify.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `configure_omc`/`run_in` from `tests/e2e/harness.py`; the `container` fixture from `tests/e2e/conftest.py`; CLI surfaces from Tasks 1–5.

- [ ] **Step 1: Write the E2E test** — create `tests/e2e/test_e2e_notify.py`:

```python
"""File-backend notification E2E: the one backend a headless container can
assert on (macOS rendering gets a manual live check — see the build ledger)."""

import pytest

from .harness import run_in

pytestmark = pytest.mark.e2e


def test_internal_notify_appends_file_line(container):
    rc, out = run_in(container, ["omc", "configure", "--defaults"])
    assert rc == 0, out
    for pair in (
        "notifications.enabled=true",
        "notifications.backend=file:///tmp/omc-notifications.log",
    ):
        rc, out = run_in(container, ["omc", "configure", "--set", pair])
        assert rc == 0, out

    payload = '{"hook_event_name": "Notification", "message": "needs permission"}'
    rc, out = run_in(
        container,
        ["bash", "-c", f"echo '{payload}' | OMC_SLUG=e2e-slug omc internal notify --provider claude"],
    )
    assert rc == 0, out

    rc, out = run_in(container, ["cat", "/tmp/omc-notifications.log"])
    assert rc == 0, out
    cols = out.strip().split("\t")
    assert cols[1:] == ["e2e-slug", "claude", "Notification", "needs permission"]


def test_internal_notify_disabled_is_silent(container):
    rc, out = run_in(container, ["omc", "configure", "--defaults"])
    assert rc == 0, out  # notifications default OFF
    rc, out = run_in(
        container,
        ["bash", "-c", "echo '{}' | omc internal notify --provider claude"],
    )
    assert rc == 0, out
    rc, _ = run_in(container, ["test", "-e", "/tmp/omc-notifications.log"])
    assert rc != 0  # nothing written
```

- [ ] **Step 2: Run the E2E test** (Docker must be running; each test gets a fresh container)

Run: `uv run pytest tests/e2e/test_e2e_notify.py -q`
Expected: PASS. If the container image needs rebuilding, follow `tests/e2e/conftest.py`'s fixture (it builds from `docker/Dockerfile.e2e` automatically).

- [ ] **Step 3: README** — in `README.md`, insert a new `##` section between `## Understanding a codebase` and `## Prerequisites` (README.md:66-70). No Commands-table change — `omc internal` stays undocumented plumbing:

```markdown
## Notifications

Opt in during `omc configure` (or `omc configure --set notifications.enabled=true`)
and every omc-launched session pings you the moment it needs attention — a
question, a permission prompt, a finished turn — instead of idling unseen in
its tab. Delivery is per-harness under the hood (Claude Code hooks, codex's
`notify` program, an OpenCode plugin), all funneling into
`omc internal notify`.

Two backends (`notifications.backend`):

- `macos` (default) — native notification via `osascript`; silently does
  nothing on other platforms.
- `file:///absolute/path.log` — appends one tab-separated line per event
  (`time  slug  provider  event  message`), handy headless or over ssh:
  `tail -f` it in a spare terminal to see which sessions are ready.

Disabling (`--set notifications.enabled=false`) silences everything at once —
already-wired worktrees included.
```

- [ ] **Step 4: Full test suite one last time**

Run: `uv run pytest tests/unit -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS / clean. (E2E already verified in Step 2.)

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_e2e_notify.py README.md
git commit -m "test: notification file-backend E2E + README notifications section (red->green)"
```

---

## Manual verification (post-merge, macOS host)

Not a task — recorded here so the ledger entry has a checklist: enable notifications, `omc start` a ticket with the claude provider, let the session hit a permission prompt, confirm a "omc: <slug>" notification appears; repeat once with the file backend and `tail -f`.
