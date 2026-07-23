# Fix broken/duplicate omc notifications

Date: 2026-07-23 · Task: free-text ("fix the notification issue") · Status:
approved design · Supersedes: the COPS-988 design's (§5) assumption that the
macos backend fires for every provider.

## 1. Problem

Every attention event in an omc-launched Claude Code session produces TWO
macOS notifications:

- Claude Code's own native alert — clickable, focuses the session. Outside
  omc's control, and strictly better than anything omc can post.
- omc's osascript alert (`display notification … with title "omc: <slug>"`,
  from `_deliver_macos` in `src/omc/notify.py`) — non-clickable, carries the
  Script Editor app identity, cannot focus anything. This is the "broken"
  `omc: <slug>` alert; notification-center coalescing between the two
  produces the observed "broken one gets replaced by the real one" effect.

The COPS-988 design (2026-07-17) did not anticipate the harness posting its
own notifications. On Codex and OpenCode the omc alert is NOT noise — those
harnesses have no native desktop notification path (Codex's only channel IS
the `notify` hook omc wires; OpenCode's plugin bus only reaches the user
through omc's sink).

Decision (user): when the harness can notify natively, let it — omc must not
duplicate. Codex/OpenCode keep omc's alerts.

## 2. Approach

**Delivery-time suppression** via a provider capability flag. Chosen over
wiring-time removal (which would kill the `file://` feed for Claude sessions
and require an un-wiring migration through the load-bearing
`settings.local.json` merge logic) and over making omc's alert clickable
(new external dependency that still duplicates what Claude does better).

Why delivery-time wins:

- **Zero migration.** Already-wired worktrees keep invoking
  `omc internal notify`; the sink itself now stays quiet on macOS for
  Claude. Upgrading omc fixes every existing worktree — no user-settings
  edits.
- **The `file://` feed stays complete.** The tail-able "which sessions are
  ready" log still records Claude `Notification`/`Stop` events; the Docker
  E2E assertion surface is untouched.
- **Robust to config changes.** Suppression is decided per delivery, not
  frozen at wiring time, so later `notifications.backend` changes just work.

## 3. Changes

### `src/omc/providers/base.py`

New non-abstract method on `Provider` (pure, like the rest of the contract):

```python
def notifies_natively(self) -> bool:
    """True when this harness posts its own desktop notification for
    attention events — omc's macos backend then stays silent to avoid
    duplicates. File backends always log regardless."""
    return False
```

### `src/omc/providers/claude.py`

Override returning `True`, with the provider-quirk comment at the site (repo
convention): Claude Code posts its own clickable, session-focusing
notification for permission prompts / idle / turn end (observed live,
2026-07-23); omc's osascript ping would duplicate it as a dead
`omc: <slug>` alert.

Codex and OpenCode keep the default `False` — no native channel exists;
that is why their wiring exists at all.

### `src/omc/notify.py`

- `run_notify` resolves the provider object from the registry. Unknown
  provider name → treat as not-native and deliver normally; never raise
  (the exit-0 "notifications never break work" invariant holds).
- `deliver()` receives the flag and applies it to the `macos` branch ONLY:
  natively-notifying provider → skip `_deliver_macos`; `file://` always
  appends.
- Module docstring gains one line stating the native-suppression rule.

Explicitly unchanged: wiring (`wire_worktree`, `notification_setup`,
merge logic), payload normalization, config schema
(`NotificationsConfig`), the sink argv contract, and hook wiring for all
three providers (Claude stays `Notification` + `Stop` — the events now feed
the file backend only, when the backend is macos they are suppressed).

## 4. Not doing (deliberate)

- **No config escape hatch** (e.g. `notifications.force=true` for users
  whose terminal doesn't render Claude's native alerts). YAGNI — decided
  2026-07-23 ("for now let's leave it as is"); the file backend and the
  global kill switch cover the edge. The knob can be added later if it
  bites.
- No un-wiring of existing worktrees (nothing to un-wire under
  delivery-time suppression).
- No changes to Codex/OpenCode behavior.

## 5. Testing

Unit (existing fake-`ToolContext` patterns in the notify tests):

1. `claude` + `macos` backend → NO osascript invocation.
2. `codex` and `opencode` + `macos` → osascript still invoked.
3. `claude` + `file://` → line still appended.
4. Unknown provider string → delivers normally (defensive default).

Existing merge-matrix / payload-normalization / Docker E2E tests are
unaffected and must stay green.

Manual live check (recorded in the build ledger): start a Claude session on
macOS, trigger a permission prompt, confirm exactly one (clickable)
notification arrives; a Codex or OpenCode session still produces the omc
alert.

## 6. Result

- Claude sessions: one proper clickable alert, ever.
- Codex/OpenCode sessions: omc's osascript alert remains (their only
  channel).
- File log: complete feed across all three providers.
