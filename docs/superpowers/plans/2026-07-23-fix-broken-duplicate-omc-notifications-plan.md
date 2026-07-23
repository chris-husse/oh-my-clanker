# Fix Broken/Duplicate omc Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suppress omc's osascript macOS notification for providers whose harness already posts its own clickable desktop alert (Claude Code), so each attention event produces exactly one notification.

**Architecture:** Delivery-time suppression via a provider capability flag. `Provider.notifies_natively()` (new, default `False`) is overridden to `True` by `ClaudeProvider`; `deliver()` in `src/omc/notify.py` consults it and skips ONLY the `macos` backend — the `file://` backend always logs. No wiring, config-schema, or migration changes.

**Tech Stack:** Python 3 (uv project), pytest unit tests under `tests/unit/`.

**Spec:** `docs/superpowers/specs/2026-07-23-fix-broken-duplicate-omc-notifications-design.md`

## Global Constraints

- Invariant: notifications never break work — `run_notify` always exits 0; `deliver` never raises.
- Providers stay pure (no I/O) — the flag is a plain method returning a constant.
- The `file://` backend behavior is untouched (Docker E2E assertion surface + tail feed).
- No config knob (decided 2026-07-23: "for now let's leave it as is").
- No changes to `wire_worktree`, `notification_setup`, merge logic, payload normalization, or the sink argv contract.
- Provider quirks are documented as comments at the exact code site (repo convention).
- Run tests with `uv run pytest <path> -v` from the worktree root.

---

### Task 1: `Provider.notifies_natively()` capability flag

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/providers/base.py` (after `notification_setup`, ~line 51)
- Modify: `src/omc/providers/claude.py` (after `notification_setup`, ~line 93)
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Provider.notifies_natively(self) -> bool` — default `False`; `ClaudeProvider` returns `True`. Task 2 calls this via `get_provider(name).notifies_natively()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_providers.py` (it already imports `get_provider` from `omc.providers.registry`):

```python
def test_notifies_natively_flags():
    # claude: the harness posts its own clickable desktop notification;
    # codex/opencode: no native channel — omc's alert is their only one.
    assert get_provider("claude").notifies_natively() is True
    assert get_provider("codex").notifies_natively() is False
    assert get_provider("opencode").notifies_natively() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_providers.py::test_notifies_natively_flags -v`
Expected: FAIL with `AttributeError: 'ClaudeProvider' object has no attribute 'notifies_natively'`

- [ ] **Step 3: Write minimal implementation**

In `src/omc/providers/base.py`, add after the `notification_setup` method (keep it non-abstract, like `notification_setup`):

```python
    def notifies_natively(self) -> bool:
        """True when this harness posts its own desktop notification for
        attention events — omc's macos backend then stays silent to avoid
        duplicates. File backends always log regardless."""
        return False
```

In `src/omc/providers/claude.py`, add after the `notification_setup` method:

```python
    def notifies_natively(self):
        # Claude Code posts its own clickable, session-focusing notification
        # for permission prompts / idle / turn end (observed live 2026-07-23);
        # omc's osascript ping would duplicate it as a dead "omc: <slug>"
        # alert, so the macos backend suppresses itself for claude.
        return True
```

Codex and OpenCode deliberately keep the default `False` — no native channel exists (that is why their hook wiring exists at all). Do not touch those files.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers.py -v`
Expected: all PASS (including the new `test_notifies_natively_flags`)

- [ ] **Step 5: Commit**

```bash
git add src/omc/providers/base.py src/omc/providers/claude.py tests/unit/test_providers.py
git commit -m "feat: Provider.notifies_natively() capability flag (claude: True)"
```

---

### Task 2: `deliver()` skips the macos backend for natively-notifying providers

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/notify.py` (module docstring lines 1–10, imports ~line 23, `deliver` lines 38–47, new helper below `deliver`)
- Test: `tests/unit/test_notify.py`

**Interfaces:**
- Consumes: `get_provider(name).notifies_natively()` from Task 1; `OmcError` from `omc.errors`.
- Produces: no new public surface — `deliver()` keeps its exact signature `deliver(cfg, *, ctx, provider: str, event: str, body: str, cwd: str) -> None`; new module-private `_notifies_natively(provider_name: str) -> bool`.

- [ ] **Step 1: Update existing macos-backend tests that assume claude delivers via osascript**

After this task, `provider="claude"` + `macos` backend is suppressed, so the four existing macos tests must exercise a NON-native provider instead. In `tests/unit/test_notify.py`, change `provider="claude"` to `provider="codex"` in exactly these tests (each has one or two `notify.deliver(...)` calls; change every one inside them):

- `test_macos_backend_runs_osascript_on_darwin_only` (lines ~137 and ~147)
- `test_macos_backend_oserror_is_swallowed` (line ~161)
- `test_macos_backend_timeout_is_swallowed` (line ~178)
- `test_macos_backend_cleans_control_chars` (line ~194)

Everything else in those tests (FakeCtx, assertions, platform monkeypatching) stays identical — the osascript argv does not encode the provider, so the expected strings are unchanged. Do NOT touch the file-backend tests: `provider="claude"` + `file://` must keep logging (that is part of the design).

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_notify.py`:

```python
def test_macos_backend_suppressed_for_natively_notifying_provider(tmp_path, monkeypatch):
    calls = []

    class FakeCtx:
        env = {"OMC_SLUG": "s-1"}

        def run(self, argv, **kwargs):
            calls.append(list(argv))

    cfg = Config()
    cfg.notifications.enabled = True  # backend stays "macos"
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg, ctx=FakeCtx(), provider="claude", event="e", body="b", cwd=str(tmp_path)
    )
    assert calls == []  # Claude Code posts its own alert — omc stays silent


def test_macos_backend_unknown_provider_still_delivers(tmp_path, monkeypatch):
    # deliver() is argparse-guarded in production, but stays defensive:
    # an unknown name means "not known to notify natively" — deliver.
    calls = []

    class FakeCtx:
        env = {"OMC_SLUG": "s-1"}

        def run(self, argv, **kwargs):
            calls.append(list(argv))

    cfg = Config()
    cfg.notifications.enabled = True
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg, ctx=FakeCtx(), provider="mystery", event="e", body="b", cwd=str(tmp_path)
    )
    assert len(calls) == 1 and calls[0][0] == "osascript"


def test_macos_backend_delivers_for_opencode(tmp_path, monkeypatch):
    # opencode has no native desktop channel — omc's alert must keep firing.
    calls = []

    class FakeCtx:
        env = {"OMC_SLUG": "s-1"}

        def run(self, argv, **kwargs):
            calls.append(list(argv))

    cfg = Config()
    cfg.notifications.enabled = True
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg, ctx=FakeCtx(), provider="opencode", event="e", body="b", cwd=str(tmp_path)
    )
    assert len(calls) == 1 and calls[0][0] == "osascript"


def test_file_backend_still_logs_natively_notifying_provider(tmp_path):
    # Suppression is macos-only: the tail feed / E2E surface keeps claude lines.
    log = tmp_path / "n.log"
    notify.deliver(
        _file_cfg(log),
        ctx=_ctx(tmp_path, OMC_SLUG="s-1"),
        provider="claude",
        event="Stop",
        body="turn complete",
        cwd=str(tmp_path),
    )
    assert log.read_text().split("\t")[2:] == ["claude", "Stop", "turn complete\n"]
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `uv run pytest tests/unit/test_notify.py -v`
Expected: `test_macos_backend_suppressed_for_natively_notifying_provider` FAILS (osascript IS called — suppression not implemented); the other two new tests and all updated existing tests PASS.

- [ ] **Step 4: Implement suppression in `src/omc/notify.py`**

Extend the imports (the file already has `from .errors import ConfigError`):

```python
from .errors import ConfigError, OmcError
from .providers.registry import get_provider
```

(`providers/*` never imports `notify` — no import cycle; `internal.py` already imports the registry at module level.)

Change `deliver`'s macos branch (lines 43–45) from:

```python
    backend = cfg.notifications.backend
    if backend == "macos":
        _deliver_macos(ctx, slug, body)
```

to:

```python
    backend = cfg.notifications.backend
    if backend == "macos":
        if not _notifies_natively(provider):
            _deliver_macos(ctx, slug, body)
```

Add the helper directly below `deliver`:

```python
def _notifies_natively(provider_name: str) -> bool:
    """macos-backend suppression check: a harness that posts its own clickable
    desktop notification (claude) makes omc's osascript ping a duplicate.
    Unknown names (production is argparse-guarded; tests call deliver directly)
    count as not-native — deliver rather than drop."""
    try:
        return get_provider(provider_name).notifies_natively()
    except OmcError:
        return False
```

Update the module docstring: after the sentence describing dispatch on `notifications.backend` (line 7–8), add one sentence:

```
Providers whose harness posts its own desktop notification (claude) skip
the macos backend — file backends always log.
```

- [ ] **Step 5: Run the full unit suite**

Run: `uv run pytest tests/unit/ -v`
Expected: all PASS (notify, providers, internal, start — nothing else consumes `deliver`).

- [ ] **Step 6: Commit**

```bash
git add src/omc/notify.py tests/unit/test_notify.py
git commit -m "fix: suppress omc macos notification when the harness notifies natively"
```

---

## Post-implementation note (human, at /omc:finish time)

Manual live check for the build ledger (`.superpowers/sdd/progress.md`), per spec §5: on macOS, start a Claude session and trigger a permission prompt — exactly one (clickable) notification must arrive; a Codex or OpenCode session must still produce the omc `osascript` alert.
