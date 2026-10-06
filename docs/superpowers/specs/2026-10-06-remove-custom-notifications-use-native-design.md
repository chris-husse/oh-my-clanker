# Remove omc's custom notifications, drive the native ones

**Date:** 2026-10-06
**Slug:** `remove-custom-notifications-use-native`
**Status:** design, approved in chat; `/omc:design` invoked with the
recommendations adopted and one changed (see §4); hardened (explain +
grug per section, whole-record pass)
**Supersedes:** `2026-07-17-cops-988-add-slack-ping-on-idle-design.md` and
`2026-07-23-fix-broken-duplicate-omc-notifications-design.md`

## 1. Problem

omc ships its own notification layer: a delivery sink behind the hidden
command `omc internal notify --provider <name>`, a macOS `osascript`
backend and a `file://` log backend, per-worktree hook wiring written at
session start, a `notifications` section in the global config, and two
questions in `omc configure`. The user's request, verbatim:

> So we need to get rid of our custom notification. They don't add anything
> and are usually wrong. Focus on the Claude/Codex native nofitications and
> they will be enabled by default, no more config menu for that.

Both harnesses already notify natively by default. Claude Code's
`preferredNotifChannel` defaults to `auto` (a desktop alert in iTerm2,
Ghostty and Kitty; a bell in Terminal.app) and needs no hooks. Codex's
`tui.notifications` defaults to `true`, with `notification_method = auto`
and `notification_condition = unfocused`. Against that baseline omc's layer
only subtracts:

- **Codex.** omc injects `-c notify=["omc","internal","notify",…]` into
  every session argv. That replaces the user's own `notify` program from
  `~/.codex/config.toml` for the omc session, so enabling omc notifications
  silently disables a notifier the user set up themselves.
- **Claude.** omc's own macOS alert was a dead, non-clickable duplicate of
  Claude's and was suppressed at delivery time in July 2026. Since then the
  Claude hooks only feed a tab-separated log file.
- **Both.** A second config surface, two configure questions, a hidden
  command and roughly 440 lines of tests maintain a feature whose manual
  macOS check was never done (SDD ledger, COPS-988 final review).

## 2. Goals and non-goals

Goals:

- Delete the custom notification layer outright: sink, backends, payload
  normalizers, `notifications` config section, configure questions, README
  section, and their tests.
- omc turns each harness's **native** notifications on for the sessions it
  launches, by default, through a per-provider flag the user can turn off.
- Only the omc session is affected. omc never edits `~/.codex/config.toml`
  or `~/.claude/settings.json`.
- An unknown key in a config file never fails loading. A missing key takes
  its default.
- Nothing in the new wiring can fail `omc start` or `omc implement`.

Non-goals:

- No omc-side delivery of any kind, no log file, no fallback for terminals
  without native support.
- No compatibility shim for `omc internal notify`: the command is removed.
- No migration tooling for worktrees wired before this change beyond a
  release note.

## 3. Design

### 3.1 The per-provider flag

`ProviderConfig` gains one boolean, default on:

```python
@dataclass
class ProviderConfig:
    model: str = ""
    docs_model: str = ""
    notifications: bool = True  # the harness's NATIVE notifications, omc sessions only
```

It is set with `omc configure --set llm.providers.codex.notifications=false`.
`set_key` coerces exactly `true` and `false` and rejects any other value,
as `notifications.enabled` did. `NotificationsConfig` leaves the schema,
`GlobalConfig`, the runtime `Config` and the resolver. `validate_backend`
and the `notifications.*` branches in `set_key` and `_hydrate` go with it.

### 3.2 Tolerant config loading

Today `_hydrate` raises on any key it does not know. That strictness was
typo protection: a misspelt key fails loudly instead of silently taking a
default. The cost is that every removed or renamed key turns into a hard
exit 1 for every gated command until the user edits the file by hand, which
is what this change hits with `notifications`.

`_hydrate` stops raising on unknown keys. It prints one warning per load to
stderr in the house style and drops the key:

```
· config: ignoring unknown key(s) ['notifications'] in ~/.omc/config.yaml
```

`_hydrate` is pure (no `ToolContext`), so this is a plain stderr print like
the existing `·` narration lines. It fires once per load; a command loads
the config once or twice, and that repetition needs no de-duplication.

Missing keys already fall back to dataclass defaults; that behaviour is
kept and its test stays. Value validation stays strict: a wrong type, an
invalid provider name or an unsupported docs backend is still a
`ConfigError`, because a default cannot repair a wrong value. The same
warn-and-ignore rule applies to `secrets.yaml`, replacing its "only
`schema_version` and `api_keys`" refusal, with one difference: that file's
loader never echoes a key name, because a hand-edit can transpose a secret
into key position. Its warning therefore reports only a count
(`ignoring 1 unknown key in ~/.omc/secrets.yaml`). `set_key` keeps
rejecting unknown keys: a `--set` argument is something the user just
typed, not a file.

A stale `notifications:` section in an existing `~/.omc/config.yaml` is
therefore harmless and disappears on the next `omc configure` save, which
writes the dataclass back.

### 3.3 The provider contract

Three members of `Provider` change:

- `session_argv(…, notify_sink_argv=None)` becomes
  `session_argv(…, notifications: bool = True)`.
- `notification_setup(sink_argv)` becomes
  `notification_setup(notifications: bool) -> dict[str, str]`. Still pure,
  still "worktree-relative path to file content; `{}` means nothing to
  write".
- `notifies_natively()` is deleted. It existed only to suppress omc's own
  alert.

**Codex** places `-c tui.notifications=true` or `=false` before the
trailing seed positional, next to the existing `-c tui.terminal_title=[]`
override. `-c notify=` is gone, so the user's own `notify` program applies
again. The code-site comment records the verified version (0.158.0) and the
three TUI keys. `notification_setup` keeps returning `{}`.

Only `session.session_plan` consumes `session_argv`, for the interactive
session that `omc start` and `omc implement` open. Every other provider call
(`slug`, the documentation probe, `watch`, the headless runs) is a one-shot
print-mode run with no TUI, so the flag deliberately does not reach
`headless_argv` or `headless_stream_argv`.

**Claude** returns a settings fragment for `.claude/settings.local.json`:
`{"preferredNotifChannel": "auto"}` when on,
`{"preferredNotifChannel": "notifications_disabled"}` when off.
`session_argv` ignores the flag, as it ignored the sink.

Both values are written explicitly in both states. "On" therefore also
overrides a global `notifications_disabled` or `tui.notifications = false`
for the omc session, which is what "omc turns them on" means. The cost is
that a user who chose `iterm2_with_bell` or `terminal_bell` globally, or a
Codex event filter such as `["approval-requested"]`, gets the harness's
`auto` / all-events behaviour in omc sessions instead.

### 3.4 Wiring at session start

`src/omc/notify.py` keeps its name and shrinks to the wiring half.
`wire_worktree(provider, worktree, notifications)` stays the caller-side
function that materialises `notification_setup` into the worktree;
`start.py` and `implement.py` keep calling it after worktree creation, now
unconditionally (the flag decides the content, not whether to call). `omc
start` is the only path that creates a worktree; `omc implement` wires the
worktree it is run in, so a worktree is wired twice over its life and the
merge must stay idempotent, as it is today.

The merge function generalises from "append our hook groups" to "merge our
top-level keys into an existing settings file". It also removes omc's old
hook entries, any hook whose command is exactly
`omc internal notify --provider claude`, from the `Notification` and
`Stop` events. Worktrunk copies the primary checkout's
`.claude/settings.local.json` into new worktrees, and primaries that ran the
old wiring carry those hooks, so the strip is what stops old wiring from
spreading into new worktrees.

The marker-guarded branch for omc-owned non-settings files
(`// generated by omc`) is deleted with its constant: it existed for
providers that wired notifications through a standalone hook file, and no
provider produces one anymore. `wire_worktree` handles exactly one file
shape, a JSON settings file that is merged. The invalid-JSON refusal,
non-UTF-8 tolerance and the warn-and-skip rule all stay. The dry-run plan
line becomes, per provider:

```
  notifications: native, on   (claude: preferredNotifChannel=auto in .claude/settings.local.json)
  notifications: native, off  (codex: -c tui.notifications=false)
```

### 3.5 Configure

The two notification questions leave the global walkthrough. Inside the
existing per-provider loop, right after the model question, one confirm:

```
Enable native notifications for claude?  [Y/n]
```

The default is the current value, so a fresh config answers yes. No backend
question, no free-text prompt. A dismissed prompt (`None` from Esc or
Ctrl-C) keeps the current value, the way the model question treats a blank
answer; the old code coerced it with `bool(enable)`, which silently turned
notifications off.

### 3.6 Removals

- `notify.py`: `run_notify`, `deliver`, `_deliver_macos`, `_deliver_file`,
  `payload_from_claude`, `payload_from_codex`, `sink_argv`, `GENERIC_BODY`,
  `_clean`, `_applescript_str`.
- `internal.py`: the `notify` subcommand, its parser and its usage line.
- `session.py`: the sink decision becomes passing the provider's flag.
- `start.py`, `implement.py`: the `enabled` gates and the backend text.
- `config/schema.py`, `config/store.py`, `config/resolve.py`:
  `NotificationsConfig`, `validate_backend`, their branches.
- `README.md`: the Notifications section is rewritten to a few sentences on
  native notifications, the per-provider flag, and the release note below.
- `src/omc/distribution/AGENTS.md`: "wire notifications" becomes "set the
  harness's native notifications".
- Tests: `tests/e2e/test_e2e_notify.py` is deleted (the file backend is
  gone, and headless containers have no native assertion surface).
  `tests/unit/test_notify.py` keeps only wiring and merge tests, reshaped.
  Notification cases in the config store, providers, session, start,
  implement, internal and configure suites are rewritten for the flag.
- `tests/e2e/artifacts/omc-wiki/notifications.md` is a copy-back artifact
  of the docs E2E and refreshes on its next run. Untouched here.

**Release note.** Worktrees wired before this change carry `Notification`
and `Stop` hooks that call the removed command. Claude treats a hook exit
code of 2 on `Stop` as "block stopping and show stderr to the model", so
those sessions misbehave until the two hook entries are deleted from the
worktree's `.claude/settings.local.json` by hand. New worktrees are cleaned
automatically by §3.4.

### 3.7 Testing and verification

Unit, all pure or fake-context, in the existing patterns:

- Codex argv: `-c tui.notifications=true|false` present, before the seed,
  no `notify=` anywhere.
- Claude fragment: exact JSON for on and off; argv identical either way.
- Merge: sets the channel key, strips only omc's old hook commands and
  leaves foreign hooks, idempotent, refuses invalid JSON, survives
  non-UTF-8.
- Config: flag defaults true; `set_key` coerces `true`/`false` and rejects
  `yes`; an unknown file key warns on stderr and loads; a missing key
  defaults; a wrong value type still errors; `secrets.yaml` follows the
  same rule.
- Start and implement: dry run prints the native line and writes nothing;
  a real run calls the wiring once.
- Internal: `notify` is no longer a subcommand.

E2E, per the project's testing policy ("every external integration keeps
at least one E2E driving the real tool and asserting its on-disk effect"):
the existing start E2E in `tests/e2e/test_e2e_start.py` gains two
assertions. After a real `omc start` on Claude, the new worktree's
`.claude/settings.local.json` contains `preferredNotifChannel: auto` and no
`omc internal notify` hook. For Codex, whose effect is argv-only, the
`omc start --dry-run` output contains `-c tui.notifications=true`. No new
E2E file; the source tree change re-keys the stage snapshots on its own.

The default-on flag has one E2E-visible side effect: every containerised
`omc start` on Claude now writes `.claude/settings.local.json` into the work
repo's worktree, which the old default-off feature never did. The work repo
created by `make_work_repo` has no `.gitignore`, so the file is untracked
there. The harness's repo snapshot hashes tracked files, index, HEAD and
origin refs, so it does not see the file, but any golden or variation
assertion that lists the worktree or inspects `git status` would. The plan
runs the golden stages once to confirm nothing does.

Manual verification, required before the branch is finished: one Claude and
one Codex session on macOS, trigger a permission prompt and a turn end,
confirm the harness's own notification appears, and confirm the user's
Codex Computer Use notifier still fires. This is the live check the
original feature shipped without.

**Audit note (2026-10-06).** Attempted during `/omc:audit`. Headless runs
of the installed CLIs accepted both knobs without warnings; two interactive
sessions built from the branch's own `session_argv` and `wire_worktree`
output were opened for observation. The user reported no visible alert from
either and directed publication. Live confirmation of visible alerts, and
of the Codex Computer Use notifier, is a post-merge follow-up; the branch
publishes with the knobs verified as accepted, not as visibly delivering.

## 4. Decisions taken during brainstorm

Each row was presented as an open question with a recommendation. The user
changed one and let the others stand.

| Question | Decision |
|---|---|
| Flag key name | `llm.providers.<name>.notifications`, boolean, default true. |
| Unknown keys in `secrets.yaml` | Same warn-and-ignore rule as the config files. |
| Explicit value when the flag is on | Yes: `auto` for Claude, `tui.notifications=true` for Codex. On means on for omc sessions, even over a global off. |
| The old `omc internal notify` command | **Changed by the user:** removed outright, no exit-0 stub. Pre-upgrade worktrees are handled by the release note in §3.6. |
| Active vs passive (seed) | omc actively sets the native knob per session; it does not merely stop interfering. |
| Config loading (seed) | Unknown keys warn, never fail; missing keys default. |

## 5. Risks

- **Live pre-upgrade worktrees break on `Stop`** until their hook entries
  are deleted. Accepted by the user in favour of a clean removal; mitigated
  by the release note and the automatic strip for new worktrees.
- **Harness knobs rot.** `preferredNotifChannel` values and the Codex TUI
  keys are external. Each is pinned at its code site with the verified
  version, the convention for every provider quirk in `providers/*.py`.
- **Explicit `auto` / `true` overrides a user's finer choice** for omc
  sessions (§3.3). Turning the flag off restores silence; there is no
  pass-through state by decision.
- **Tolerant loading hides typos.** A misspelt key becomes a warning and a
  default instead of a hard stop. The warning is the signal; value
  validation stays strict.

## Implementation review

**2026-10-06, auditor: Claude (Fable 5.1), `/omc:audit`**

- Important, record requirement unmet. §3.7 "Manual verification" was not
  done during implementation. Audit: Claude Code 2.1.291 ran headless in a
  directory carrying `{"preferredNotifChannel": "auto"}` from
  `src/omc/providers/claude.py:116`; Codex 0.158.0 ran headless with
  `-c tui.notifications=true` from `src/omc/providers/codex.py:43`; neither
  warned. Interactive sessions were opened for the user, who reported no
  visible alert and directed publication. Disposition: §3.7 amended with the
  audit note; live confirmation moved to a post-merge follow-up.
- Minor, record is stale. §3.4 does not mention that `wire_worktree` replaces
  the settings file through a same-directory temporary file
  (`src/omc/notify.py:72`, `_write_settings`), added after independent review
  found a partial-write corruption risk. Additive, no brainstorm decision
  touched. Disposition: noted here, §3.4 unchanged.
- Conforms: §3.1 flag and `set_key` coercion (`src/omc/config/store.py:255`);
  §3.2 one warning per load and count-only secrets warning
  (`src/omc/config/store.py:205`, `:352`); §3.3 provider contract, both
  code-site version pins match the installed CLIs; §3.4 merge, strip of
  exact `omc internal notify --provider claude` hooks, marker branch removed
  (`src/omc/notify.py:22`); §3.5 configure prompt with dismissal preserving
  the current value (`src/omc/configure.py:298`); §3.6 removals including
  `tests/e2e/test_e2e_notify.py` and the `notify` subcommand; §3.7 unit and
  E2E assertions present (`tests/e2e/test_e2e_start.py:30`).
- Rebase fallout, fixed during finish. Main gained `omc review`
  (`src/omc/review.py`) on the old contract after this branch was cut; its
  dry-run and wiring paths and `tests/unit/test_review.py` were mirrored to
  the native flag exactly as `implement.py` (§3.4, §3.6).
- Verify stage, fixed during finish. (1) The §3.7 side effect landed: the
  default-on wiring leaves `.claude/settings.local.json` untracked in the E2E
  work repos, and the suite asserts clean trees after design, implement and
  audit. The fixture repos now ignore that file as a real checkout does
  (`tests/e2e/harness.py:make_work_repo`, `tests/e2e/lifecycle_helpers.py`,
  `tests/e2e/test_e2e_watch.py`). (2) The E2E image copied the host
  worktree's personal `.claude/settings.local.json`, still carrying the two
  removed `omc internal notify` hooks, into `/repo`: the §3.6 release-note
  failure (exit 2 on `Stop`) then emptied every `claude -p` answer in the
  explain tests. `.dockerignore` excludes the file. (3) Built from a
  worktree, `/repo/.git` was a pointer to a host path, so the graph proxy
  refused the tree and explain degraded on `main` too;
  `docker/Dockerfile.e2e` re-roots `/repo` as a standalone repo with
  `origin/main`. (4) The Codex gate's trust prompt was acknowledged once and
  could be missed; `docker/conversation.py` retries it. All pre-existing
  except (1) and (2), which this change exposed.

## Deliberate complexity

- Tolerant config loading (§3.2) changes a store-wide invariant and is
  wider than the notification removal that triggered it (lens:
  `grug:small-refactor`). Waived by the brainstorm decision "Config loading
  (seed)": the user ruled that an excess key must never fail loading, for
  every config file, not only for this key.
- Writing an explicit value when the flag is **on** (§3.3) is the fuller
  version; writing only when off would deliver most of the value, since both
  harnesses default to on (lens: `grug:80-20`). Waived by the brainstorm
  decision "Explicit value when the flag is on": on means on for omc
  sessions even over a global off, and the two states stay symmetric.
- `native_description` in `src/omc/notify.py` restates each provider's knob
  for the dry-run plan line instead of asking the provider (lens:
  `grug:locality`). Waived at review: the two literals mirror one-line knobs
  pinned at their code sites; a provider-side description member would add a
  contract method with a single caller, and deriving the text from
  `notification_setup` / `session_argv` output is more code than the literals.
