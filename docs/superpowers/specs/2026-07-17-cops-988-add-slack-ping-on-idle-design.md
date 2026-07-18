# Idle notifications for omc sessions (COPS-988)

Date: 2026-07-17 · Ticket: COPS-988 "Add Slack ping on idle" (sub-task of
COPS-855) · Status: approved design

## 1. Problem & decision history

An omc-launched LLM session that asks a question, wants tool permission, or
finishes a turn sits invisible in its tab until the user happens to look.
The ticket asked for a Slack ping "via the local endpoint" — investigation
established that no local Slack endpoint exists (the desktop app exposes
none; Slack's programmatic paths are the remote Incoming Webhook / Web API,
both requiring secret credentials) and that Claude Code's own Slack
integrations (Claude Tag, "Claude Code in Slack") cannot observe local CLI
sessions. **Slack delivery was therefore dropped** in favor of:

- **macOS native notifications** (default backend) — `osascript` ships with
  the OS: no secrets, no setup, no new dependency.
- **A file backend** — appends formatted lines to a log file, giving
  headless environments (Docker E2E) a real assertion surface and users a
  `tail -f`-able "which sessions are ready" feed (e.g. over ssh).

Detection is inherently per-harness; delivery is shared. Only trigger
wiring differs per provider.

## 2. Architecture

Four pieces:

1. **`src/omc/notify.py` (new)** — the whole feature lives here:
   - *Delivery*: normalize a harness payload, dispatch to the configured
     backend (§5).
   - *Wiring*: functions `run_start` calls after worktree creation to
     materialize each provider's hook config (§4). Providers only
     *describe* wiring; this module + `start.py` execute it.
2. **`omc internal notify --provider <name>` (new)** — the command every
   harness hook invokes. Thin entry point in `internal.py` beside
   `rebase-main`: read payload, deliver, exit 0 (exit 2 only for
   missing/unknown `--provider`). No `OMC_*` verdict line — nothing parses
   its output.
3. **`Provider.notification_setup(sink_argv) -> dict[str, str]`** — new
   method on the pure provider contract (`providers/base.py`), default
   implementation returns `{}`. Returns a description only: worktree-relative
   file paths → content to write. No I/O in providers — callers own
   execution, exactly like `session_argv`. Session-argv additions are NOT
   part of this return value: flag placement is provider-specific (Codex's
   seed is a trailing positional, so a naïve append would land flags after
   it), so `session_argv()` instead gains an optional
   `notify_sink_argv: list[str] | None = None` parameter and each provider
   places it correctly itself.
4. **Config** (`config/schema.py`, strict as ever):

   ```python
   @dataclass
   class NotificationsConfig:
       enabled: bool = False          # opt-in
       backend: str = "macos"         # "macos" | "file://<abs path>"
   ```

   `omc configure` gains one yes/no (enable) and, when enabled, a backend
   prompt defaulting to `macos`; `--set notifications.enabled=true` and
   `--set notifications.backend=file:///tmp/omc-notifications.log` work
   non-interactively. Missing key in existing configs → defaults
   (schema_version stays 1).

   Two store changes this drags in (today `set_key` assigns raw strings and
   `_hydrate` never validates values): `set_key` must coerce
   `notifications.enabled` explicitly (`true`/`false` → bool; anything else
   → `ConfigError` — a stored string `"true"` would otherwise be truthy
   even when the user meant false), and the backend value (`macos` or
   `file://` + absolute path) is validated by one shared check applied on
   BOTH paths — `_hydrate` at load and `set_key` at write — consistent with
   the schema's reject-unknown posture.

## 3. Notification content

- **Title**: `omc: <slug>` — slug from `$OMC_SLUG` (exported to every
  omc-launched session), falling back to the worktree basename of the
  payload's `cwd`/current dir.
- **Body**: the harness's short status message ("Claude Code needs your
  permission…", "turn complete", "session idle").
- **Excluded by design**: transcript content (Claude's
  `last_assistant_message` is ignored), ticket text, anything
  user-generated beyond the harness's own status strings. Nothing
  sensitive reaches the notification center or log file.

## 4. Per-provider wiring

| Provider | Mechanism | Events |
|---|---|---|
| Claude Code | write/merge `.claude/settings.local.json` in the worktree: `Notification` (unfiltered) + `Stop` hooks → `omc internal notify --provider claude` | permission prompts, idle-waiting, agent-needs-input, elicitation, turn end |
| Codex | no file; `session_argv(notify_sink_argv=…)` places `-c notify=["omc","internal","notify","--provider","codex"]` before the trailing seed positional | `agent-turn-complete` only — all Codex's `notify` exposes today |
| OpenCode | write generated `.opencode/plugin/omc-notify.js` (self-contained, no npm deps) | `session.idle`, `permission.asked`, `session.error` |

Payload shapes differ and are normalized in `notify.py`: Claude sends JSON
on stdin (`message`, `hook_event_name`); Codex passes one JSON argument;
the OpenCode plugin passes `--message <text>` (and `--event <name>`)
explicitly. Provider quirks get comments at the exact code site, per repo
convention (the Codex `-c` flag syntax is re-verified against the
installed CLI during implementation).

The merge logic is load-bearing, not defensive: omc's own `wt.toml`
template copies every gitignored file into new worktrees
(`copy-ignored`, `wtconfig.py:WT_TEMPLATE`), so a
`.claude/settings.local.json` living in the primary checkout arrives in
the fresh worktree BEFORE omc wires anything.

Accepted consequences:

- The Claude/OpenCode wiring is a worktree file, so *manually* started
  sessions in that worktree also notify (harmless; arguably a feature).
  Codex notifies only for omc-launched sessions — omc never edits
  `~/.codex/config.toml`.
- On Claude, a turn end produces a `Stop` ping and possibly a later
  `idle_prompt` reminder — chosen deliberately ("all attention events +
  turn end").

## 5. Backends

Dispatch on `notifications.backend`:

- **`macos`** (default): `osascript -e 'display notification <body> with
  title <title>'` via `ToolContext.run`. The strings are escaped as
  AppleScript string literals (quote/backslash escaping) — payload text is
  data, never code. Non-Darwin platform or `osascript` failure → silent
  no-op, still exit 0.
- **`file://<abs path>`**: append one line, `O_APPEND` single-write (atomic
  enough for concurrent sessions):

  ```
  2026-07-17T14:03:22+02:00  cops-988-add-slack-ping-on-idle  claude  permission_prompt  Claude Code needs your permission to use Bash
  ```

  Fields: ISO-8601 local timestamp, slug, provider, event, body — tab
  separated, body last so free text can't break the columns. Control
  characters and newlines in the body are escaped (log-injection
  hygiene). Works on any platform; this is also the Docker E2E surface.
  Parent directory must exist; append failure → stderr note, exit 0.

## 6. Enablement & error handling

Invariant: **notifications never break work**.

- `notifications.enabled=false` (default) → zero side effects: no files,
  no argv extras — and `omc internal notify` is a silent no-op (exit 0),
  so disabling the config is a true global kill switch even for worktrees
  wired while it was enabled.
- Wiring runs in `run_start` after worktree creation, before launch;
  `--dry-run` prints the planned wiring with the rest of the plan.
- `.claude/settings.local.json` merge: parse existing JSON, append our
  hook entries; idempotent (already wired → skip); unparseable existing
  file → leave it alone, warn on stderr, skip Claude wiring. Same
  leave-alone rule if `.opencode/plugin/omc-notify.js` exists with foreign
  content.
- Any wiring failure → one stderr warning, launch continues.
- `omc internal notify`: malformed/absent payload degrades to the generic
  body "needs your attention"; always exit 0.

## 7. Testing

Unit (existing patterns — fake `ToolContext`, tmp dirs):

- Config: round-trip + rejection of unknown backend schemes on both load
  and `set_key` paths; `set_key` bool coercion for `notifications.enabled`
  (including rejection of non-bool text); defaults on configs written
  before this feature.
- Providers: `notification_setup()` purity — exact file map, no filesystem
  access; `session_argv(notify_sink_argv=…)` ordering per provider (Codex:
  flags before the seed positional; None → argv unchanged).
- Merge matrix for `.claude/settings.local.json`: fresh file / existing
  other hooks / already wired / corrupt JSON.
- Payload normalization ×3 (stdin JSON, argv JSON, flags), including
  malformed payloads.
- Backends: macos → exact escaped osascript argv on Darwin, no-op
  elsewhere, exit 0 on failure; file → formatted line, escaping of control
  chars, append to existing content, missing parent dir handling.

E2E (Docker, per-test container, existing harness): configure
`backend=file:///…`, invoke `omc internal notify --provider claude` with a
synthetic stdin payload, assert the formatted line (headless-safe — this is
what the file backend exists for). macOS notification rendering itself gets
one manual live check recorded in the build ledger.

## 8. Out of scope (deliberate)

- Slack / any remote delivery (needs credentialed remote endpoints —
  possible follow-up ticket; the backend seam is where it would plug in).
- Linux/Windows native sinks (`notify-send` etc. — the backend seam makes
  them later drop-ins; the file backend already works there).
- Focus-awareness (suppress pings while the terminal is focused),
  debouncing, per-event filtering, sounds, richer log formats.
