# Ordinary fish branch titles in iTerm2 (second attempt)

Date: 2026-09-30. Status: authorized design for `omc:implement`.
Supersedes the 2026-09-29 design. Its implementation is preserved on
`backup/ordinary-fish-pre-squash` and reused only where §8 says so.

## 1. Outcome and scope

Any interactive fish shell in iTerm2 pins its tab title to the full, literal
Git branch name. Typing plain `codex`, `codex resume <id>`, or `claude` keeps
that title while the provider writes its own activity titles and after it
exits. `omc start` uses the same hook. The prompt never waits on Python.

Rules of the restart, in priority order:

1. Zero foreground cost on an unchanged branch; no visible pause on change.
2. Product code a reviewer can hold in one sitting: roughly 500 lines.
3. A native test tier that launches its own iTerm2 instance and never
   attaches to, reads from, or writes to the user's running app, targeting a
   few hundred lines with no SDK monkeypatch. The dropped harness reached
   3,500 lines; growth past about 600 is a stop-and-redesign signal.

Out of scope: background Git watching, focus tracking, provider wrappers or
aliases, global iTerm2 or provider configuration changes, daemons, OSC
fallback on the hook path, tab-wide ownership arbitration beyond
last-writer-wins, and restoring a parent shell's title after a nested shell.

Throughout this document `<omc home>` means `$OMC_HOME` when set, else
`$HOME/.omc`, exactly as `ToolContext.from_env` resolves it. The fish hook
derives it the same way.

## 2. The fish hook

One packaged file, `src/omc/assets/omc-title.fish`, is provisioned into
`$XDG_CONFIG_HOME/fish/conf.d/omc-title.fish` (default `~/.config/fish/conf.d/`).
Its first line starts with the ownership prefix
`# omc-managed fish title integration` followed by a version suffix.

**Activation.** Return immediately unless all hold: `status is-interactive`;
`ITERM_SESSION_ID` matches `^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$`;
`TERM_PROGRAM` is `iTerm.app` or `LC_TERMINAL` is `iTerm2` (the same predicate
as `Iterm2Terminal.detect`); neither `TMUX` nor `STY` is set, because every
shell inside a multiplexer inherits one tab's session id. Then erase every
function matching `__omc_title_*` (enumerated with `functions -a`, not a fixed
list, so a stale earlier copy leaves nothing behind), reset the hook's
shell-global *state* variables (last request, per-worktree branches, hint and
marker bookkeeping) but never the configuration variable `__omc_title_helper`,
and register handlers for `fish_prompt`, `fish_preexec`, and
`--on-variable PWD`. Never define or replace `fish_title`; never touch
`config.fish`. The hook is written to fish 3.7 syntax, the version Ubuntu's
package ships for CI; `$last_pid`, `path`, and `string` all exist there.

**Refresh.** The refresh function runs in the foreground and does only cheap
work:

1. If `$OMC_FISH_TITLE_DISABLE` is `1`, return.
2. If `<omc home>/title-failed/<session uuid>` exists (builtin `test -e`; the
   uuid is `ITERM_SESSION_ID` with any `w<n>t<n>p<n>:` prefix stripped) and
   its `path mtime` is newer than the last marker this shell acted on, print
   its first line to stderr once per shell, remember the mtime, erase the
   shell's cache so the next decision re-dispatches, and continue. A shell
   therefore re-dispatches once per failure; the helper absorbs the cooldown,
   so the retry lands once the cooldown expires.
3. Run `command git symbolic-ref --quiet HEAD 2>/dev/null` and decide:

| Git result | Desired outcome |
| --- | --- |
| Exit 0, `refs/heads/<name>` | pin `<name>`; remember it as the last branch of this worktree |
| Exit 1 (detached HEAD) | pin the last branch remembered for this worktree; if none, release |
| Exit 128 (outside a repository) | release |
| Any other exit | no change |

   The worktree key is `git rev-parse --absolute-git-dir`, run when the
   pinned branch or the working directory's repository changes, and in the
   detached case. Branch and tag names never collide because the full symbolic
   ref is used and `refs/heads/` is stripped literally.
4. If the desired outcome equals this shell's last requested outcome, return.
5. Record the outcome as the shell's last request and dispatch it (below).

**Dispatch is fire-and-forget; the helper serializes.** fish cannot serialize
this itself: `--on-process-exit` never fires for a disowned job, and a
non-disowned job makes `exit` refuse once while a helper is in flight. So on
every outcome change the hook writes the desired outcome, `set <branch>` or
`release`, as one line to `<omc home>/title-request/<session uuid>-<fish pid>`
using only the `echo` builtin and a redirection, after creating
`<omc home>/title-request` with `/bin/mkdir -p` the first time, then launches
`$__omc_title_helper title apply <that path>` with `>/dev/null 2>&1 &; disown`.
The helper (§3) takes a per-session lock, re-reads the request after acquiring
it, and applies the current content, so concurrent helpers from one shell
converge on the latest request and a stale one is never applied. Two panes in
one tab can still race each other; that is the last-writer-wins model of §4.

`$__omc_title_helper` is a list variable. When it is unset or empty at refresh
time the hook resolves `command -s omc` and uses that, so a PATH fixed later
in the session is picked up; an `omc start` preset of `<python> -m omc` (§6)
is never overridden. If neither resolves, print one hint per shell and
return.

The first prompt performs the first refresh; PWD events before it are
ignored so user `config.fish` PATH changes take effect first. Handlers run in
fish's event scope; the user's `$status` and `$pipestatus` are untouched, and
a real-shell test proves it: `true | false | true` followed by a prompt
handler that launches a background job leaves `$pipestatus` at `0 1 0`.

## 3. The title helper

`omc title set -- <title>`, `omc title release`, and `omc title apply
<request file>` are quiet public subcommands. `set` and `release` are the
manual, foreground-friendly forms; `apply` is what the hook dispatches. In `cli._run` the banner-exempt tuple gains `title` and
`shell-integration`; `_dispatch` gets a lazy-import `title` branch that never
loads configuration, like `aws-credential-process`. A three-line
`src/omc/__main__.py` makes `<python> -m omc` equivalent to `omc`.

Contract: no stdout ever; nothing on success; one omc-authored stderr line on
failure. Exit 0 on success; 2 (`Refusal`, through `cli.main`'s single
`OmcError` boundary) for bad input: control characters (Unicode category
`Cc`), an empty title, a missing or malformed `ITERM_SESSION_ID`, or an
unreadable request file; 1 for a failed or timed-out API write. A refusal
writes no marker; the hook's activation predicate already rejects a malformed
session id, so ordinary shells never reach it.

`apply` reads one line from the request file, `set <title>` or `release`,
under an exclusive `fcntl.flock` on `<omc home>/title-lock/<session uuid>`.
It re-reads the file after acquiring the lock, compares it with the sibling
`<request file>.applied`, exits 0 immediately when they match (an earlier
helper already applied this request), otherwise performs the request exactly
as `set` or `release` would and, on success, writes `.applied` atomically.
The lock is held across the SDK worker so two helpers never interleave; the
worker deadline bounds the hold time. The control-character rule moves into one
shared `terminal_title.validate_title` used by both `run_title` and the new
helper; `run_title`'s pre-existing return of 1 is left alone.

The session id check moves out of `Iterm2Terminal.set_title` into a module
level `terminals.iterm2_session_id(env) -> str | None` (UUID round trip,
optional `w<n>t<n>p<n>:` prefix) that both callers use. The helper never calls
`detect_terminal` and never emits an OSC sequence; it targets only the
caller's tab, never the focused one.

Both subcommands run the existing `iterm2_title` worker through
`ToolContext.run_bounded` with the existing five-second deadline, so a hung
SDK or a blocked AppleScript cookie request is killed with its process group.
The worker argv becomes `<python> -m omc.iterm2_title --session-id <id>
(--release | -- <title>)`. `set` keeps today's mechanism: write
`user.omc_title` on the tab, then `Tab.async_set_title(r"\(user.omc_title)")`.
`release` first calls `Tab.async_set_title("")`, which the SDK documents as
restoring the live default title, then clears `user.omc_title`. The worker's
own stderr is never surfaced; `iterm2_title.main` swallows SDK errors because
they may contain authorization secrets. No test covers that branch today; a
new unit test drives `main` with a failing fake SDK and asserts exit 1 with
exactly the fixed stderr line.

**Failure handling.** On any failure the helper writes
`<omc home>/title-failed/<session uuid>` atomically (temp sibling and
`os.replace`, as `dependency.py` and `awscreds.py` do), creating the
directory if needed. The file holds one omc-authored diagnostic line
(`timed out after 5s`, `worker exit N`) and a human-readable timestamp; the cooldown is judged from the file's mtime, never
by parsing. While the marker is younger than sixty seconds, `apply` releases the lock,
waits out the remaining cooldown, then re-reads the latest request and applies
it; `set` and `release` always run the worker immediately and remove the
marker on success. Besides bounding background work this
bounds the AppleScript cookie request, and any macOS automation prompt it
raises, to one per minute per session while the API is unauthorized. Success
removes the caller's own marker. Keying by session means a shell with a stale
session id throttles only itself; leftover marker, request, lock, and
`.applied` files for closed sessions are inert bytes and `omc uninstall`
removes them with the rest of `<omc home>`.
`omc title set -- <branch>` run by hand in the foreground shows the same
diagnostic directly and is the documented manual retry.

## 4. Ownership

Last writer wins, per tab. Within one shell the latest request always wins
because the helper serializes and re-reads (§2, §3). Across shells there is
no arbitration: two
panes on different branches in one tab show whichever pane changed branch
most recently, a `release` from any pane unpins the whole tab, and other
panes do not re-pin until their own branch changes.

Each shell remembers only what it last requested. That cache is per shell,
not per tab: after a nested shell (plain `fish`, `su`, or an `omc start`
session launched from a hook-enabled fish) pins or releases in the same tab
and exits, the parent's next prompt is a no-op and the tab keeps the child's
title until the parent's branch changes, it `cd`s into a different
repository, or the user runs `omc title set -- <branch>`. This is accepted
and documented (§9). It is not a regression: the merged feature never
restored a parent title either.

A manual iTerm2 title (Edit Tab Title) survives only until the next `set` or
`release` from any shell in that tab. Focus alone never changes the title.
Provider OSC 0/1/2 writes remain overridden while the API pin is active, as
`tests/local/test_iterm2_title.py:_test_title` established; after `release`
they move the title again.

There is no `fish_exit` handler: without tab-wide ownership state nothing can
be released safely, and a closing tab needs none.

## 5. Provisioning, opt-out, update, removal

Reused from the backup's `src/omc/fish_integration.py` with the changes
noted:

- `omc shell-integration fish enable|disable|status|reconcile`, quiet and
  configuration-independent. `status` prints one JSON line.
- The packaged asset is located through the same packaged-asset convention
  `skills_source.py` uses (`importlib.resources.files("omc") / "assets"`), and
  copied atomically: temp sibling in `conf.d`, then `replace`. A file is
  *owned* when its first line starts with the ownership prefix, any version
  suffix, so a header bump never orphans an earlier copy. Install refuses a
  symlink at the target or an existing unowned file by raising `Refusal`
  (exit 2), leaving the file untouched and naming the path; symlinked parent
  directories are written through, as dotfiles setups expect. `disable`
  always records the opt-out; a removal it cannot perform over an unowned
  file is a note, never a refusal. Repeat installs are byte-idempotent.
- The hook path resolves `$XDG_CONFIG_HOME` only when absolute, else
  `$HOME/.config`, falling back to `Path.home()` as `ToolContext.from_env` does.
- Persistent opt-out is `<omc home>/integrations/fish-title.disabled`;
  `disable` removes the hook and creates it, `enable` clears it, `reconcile`
  honors it. Because `omc uninstall` deletes `<omc home>`, the opt-out does
  not survive an uninstall and reinstall.
- On macOS, `omc install` and `omc update` run the freshly installed on-disk
  CLI as `shell-integration fish reconcile` right after uv succeeds and before
  `load_global`, `require_tools`, GitNexus, and the plugin loop. The
  executable is `<uv tool dir --bin>/omc`, resolved through
  `ToolContext.uv_argv` so `UV_TOOL_BIN_DIR` is honored, with
  `<uv tool dir>/omc/bin/omc` as fallback when the bin link is missing. The
  installer prints `→ provisioning fish integration` on stderr first, per the
  CLI narration convention, then runs it under `ToolContext.run_bounded` with
  a thirty-second deadline; its stderr is relayed verbatim. The running old process never imports the new
  package's fish modules. **A fish failure never aborts the update**: it is
  recorded, the remaining gates run, and one final line reports the two
  outcomes distinctly (`✓ omc <v> installed · ✗ fish integration: <reason>`).
  When every later gate succeeds and the fish step failed, the command exits
  1; the reconcile's own exit code and stderr were already relayed verbatim.
  `<v>` is read from the fresh CLI's `--version` with a five-second deadline
  and omitted when unavailable. When `require_tools` aborts the update after
  this step, the fish outcome has been narrated but the final line is not
  printed. Non-macOS skips the step entirely.
- `omc uninstall` removes the conf.d file only when it is owned. An unowned
  file or a symlink is left in place with one stderr note and uninstall
  continues to data removal and `uv tool uninstall`; an unrelated file must
  never block uninstall. It then notes that running shells keep the loaded
  hook until they exit. Removal runs on every platform but only for an owned
  file.
- The first upgrade from a release predating the hook cannot run this
  post-step because the old updater is still executing; the README tells the
  user to run `omc shell-integration fish reconcile` once. Wiring the same
  idempotent reconcile into `omc configure` is a possible follow-up, not part
  of this change.

## 6. `omc start` on fish

`FishShell.build_invocation` keeps its signature and stays a pure builder: it
never reads the environment. When `title_argv` is set, the generated `-C`
command becomes one fish `if`/`else`. Fish evaluates the condition at startup
using the §2 activation predicate plus `OMC_FISH_TITLE_DISABLE` not being `1`.
The `else` branch is today's inline title code, byte for byte, so non-iTerm
terminals keep the helper's OSC path, a per-session disable leaves `omc start`
behaving exactly as today, and `--dry-run` output is identical in every
terminal. bash, zsh, and sh are unchanged.

The `if` branch does, in order: `set -g __omc_title_helper <python> -m omc`
(PATH-independent, the same convention as `terminal_title_argv`; the hook's
reset step preserves this variable), `source <packaged asset path>`, `cd
<worktree>`, `__omc_title_refresh`, then the provider startup command. The
asset path is `importlib.resources.files("omc") / "assets" / "omc-title.fish"`
resolved by the builder without reading the environment, so the builder stays
pure. Sourcing the packaged asset rather than the conf.d copy is deliberate:
`omc start` sessions title their tab even when the hook was never provisioned
(first legacy upgrade) or was disabled with `shell-integration fish disable`.

The conf.d copy is already loaded before `-C` runs (`fish -i` order:
`conf.d`, `config.fish`, `-C`), so this is a second sourcing on every
provisioned machine. It is safe because of the §2 erase-and-reset step. The
explicit refresh is required because a `-C` command emits neither
`fish_preexec` nor `fish_prompt`; it counts as the shell's first refresh, so
the `cd`'s PWD event before it is ignored and the first prompt after the
provider exits is a no-op on an unchanged branch. `title_argv` is unused in
the `if` branch but still passed and still printed by `--dry-run`.

The helper runs in the background, so the provider may start before the pin
lands. That is acceptable: `run_start` already suppresses provider title
writes (`CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`, Codex `tui.terminal_title=[]`)
and the API pin overrides any OSC write regardless of order. A helper failure
during startup surfaces through the §3 marker at the next prompt.

## 7. Testing

Every tier fails loud with exact setup guidance on a missing prerequisite;
nothing skips. Every module under `tests/local/` carries
`pytestmark = pytest.mark.local_iterm2`, enforced by a `tests/local/conftest.py`
collection hook that fails otherwise, because `just check` collects `tests/`
and runs on Linux CI. After this change nothing under `just iterm2-tests`
connects to the user's iTerm2; the existing `tests/local/test_iterm2_title.py`
cases migrate onto the private-instance fixture.

**Unit (`just check`).** Provisioning lifecycle, XDG fallback, collision and
symlink refusal, prefix-based ownership across a header bump,
disable/enable/reconcile; installer post-step ordering, non-aborting failure,
distinct reporting, uninstall continuing past an unowned file; CLI banner
exemption, unconfigured operation, exit codes 0/1/2; `iterm2_session_id` and
`validate_title`; fake-SDK worker for `set` and `release` including the
empty-string reset ordering; marker cooldown by mtime. Real fish over pipes,
adapting main's `fish_session` fixture (it is pipe-based, not a PTY): the
fixture scrubs `ITERM_SESSION_ID`, `TERM_PROGRAM`, `LC_TERMINAL`, `TMUX`, and
`STY` from the inherited environment and sets them per case, so `just check`
run inside iTerm2 never touches the developer's tab; the hook is copied into
`$XDG_CONFIG_HOME/fish/conf.d`; PATH is restricted to one stub directory
holding an executable named `omc` that appends its argv and the request
file's content to a log using only shell builtins, plus a `git` symlink to
the real git resolved with `shutil.which` beforehand, because the hook runs
`command git`; `OMC_HOME` points at a temp dir. Because the helper is
disowned and the pipe-based session ends with `exit`, each case waits inside
fish for the expected number of log lines with a bounded builtin loop before
exiting; Python then asserts the log. Cases: branch → detached →
outside → branch yields exactly `set`, nothing, `release`, `set`; detached
start with no known branch releases; two rapid branch changes leave the request file holding the second branch
and the stub sees `apply` twice with the same path; a unit test of `apply`
with a fake worker proves that two helpers on one request file perform one
write and that a request rewritten under a held lock is applied by the
holder, not the newcomer; user `fish_title` and
`config.fish` untouched; re-sourcing leaves one handler per event; a shell
without the iTerm predicate, under `TMUX`, or with `OMC_FISH_TITLE_DISABLE=1`
records nothing; `$pipestatus` after `true | false | true` is `0 1 0` at the
next prompt; the marker yields one hint and one retry; a missing `omc` yields
one hint. A `test_shells.py` case asserts the generated `-C` body has exactly
one `if`/`else`/`end`, the ordering `helper var < source < cd < refresh <
startup` inside the `if`, and the inline helper call before startup inside
the `else`; the generated-start fish case asserts exactly one `set` despite
double sourcing.

**Installed artifact (`just check`, Linux and macOS).** The module lives
under `tests/unit/` (`test_installed_wheel.py`), because everything under
`tests/local/` carries the `local_iterm2` marker that `just check` deselects.
uv and fish are required; absence is a `pytest.fail` naming the install
command. The test builds the wheel once per session, `uv tool install
--reinstall`s it with `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `OMC_HOME`, `HOME`,
and all `XDG_*` pointed at disposable directories, asserting every one of them
is under pytest's tmp before invoking uv, inherits the host's uv cache, runs its `shell-integration fish reconcile`, and drives an ordinary
`fish -i` under a PTY (`pty.fork`, prompts counted through a private
`fish_prompt` function) whose first prompt calls the stub `omc`. It proves the
wheel contains `omc/assets/omc-title.fish` next to the force-included
`omc/assets/skills`. This is the one sanctioned `uv tool install` outside a
user decision; `.omc/config/AGENTS.md`'s rule gains a sentence naming the
exception and its disposable-directory condition.

**Native (`just iterm2-tests`, macOS only).** A session-scoped fixture of a
few hundred lines owns a private iTerm2 instance. Task 1 of the plan is the
probe that turns each step below into a verified precondition before any
product code is written.

1. Copy `/Applications/iTerm.app` to `/private/tmp/omc-<hex>/iTerm.app`. The
   copy is required, not a fallback: AppleScript cannot disambiguate two
   instances of the same bundle path, and the SDK's cookie request targets by
   path through `IT2_APP_PATH`. The copy keeps the bundle id and signature,
   so existing Automation consent carries over. LaunchServices registers the
   copy while it exists; teardown deletes it.
2. The private HOME is `/private/tmp/omc-<hex>/home`, kept short because the
   socket path must fit the 104-byte `sockaddr_un` limit. Its
   `.config/fish/conf.d` receives the hook and its `.omc` is `OMC_HOME`.
3. Launch with `open -n -g -j -a <copy> --env HOME=<home> --env
   CFFIXED_USER_HOME=<home> --env XDG_CONFIG_HOME=<home>/.config --env OMC_HOME=<home>/.omc --env
   PATH=<checkout>/.venv/bin:/usr/bin:/bin --env IT2_SUITE=<suite> --env
   IT2_APP_PATH=<copy> --args -suite <suite> -EnableAPIServer YES
   -OpenNoWindowsAtStartup YES -openNewWindowAtStartup NO -runJobsInServers NO
   -PromptOnQuit NO -moveToApplicationsFolderAlertSuppress YES
   -SUEnableAutomaticChecks NO -SUHasLaunchedBefore YES`, plus `-New Bookmarks`
   with one custom-command profile running `fish -i` (not a login shell) and
   `-Default Bookmark Guid` for it; `open` runs with an environment of only
   `PATH`, so nothing from the runner (including `.env` loaded by `just`)
   reaches the app. Find
   the PID with
   `pgrep -f -- '-suite <suite>'`; exactly one process whose executable lives
   inside the copy. The probe verifies the argument-domain flags took effect
   by observing the socket, the absence of windows, and, after the first
   session has run, no daemonized `iTermServer` under the private Application
   Support directory (iTerm2 copies and daemonizes it there; it re-parents to
   launchd, so never match by parent PID).
4. Wait for `<home>/Library/Application Support/<suite>/private/socket`, then
   connect with the stock SDK inside a scoped environment override
   (`HOME`, `IT2_SUITE`, `IT2_APP_PATH`) applied only around
   `Connection.async_create` and restored afterwards, never process-wide. The
   SDK resolves the socket in the connecting process's `HOME` and silently
   falls back to TCP `localhost:1912` when the path is missing, so the fixture
   asserts the private socket exists first and treats a TCP fallback as a
   failure. Shells inside the private tabs inherit the app's environment, so
   omc's helper reaches the private instance with no test-only code paths in
   the product.
5. Create one window with `Window.async_create` (the instance has none) and
   tabs with `async_create_tab(command="<fish> -i", select=False)`; the user's
   login shell is not fish on every host. A probe tab runs `omc
   print-install-path` and the fixture asserts the path is inside this
   checkout's `src/omc`. Never activate or select anything in the private
   instance; never read or write `com.googlecode.iterm2` (iTerm2 itself writes
   two harmless keys there on launch, which the probe records, not repairs).
6. Teardown force-closes recorded tabs, sends SIGTERM to the PID with a
   bounded wait then SIGKILL, kills any remaining process whose argv contains
   `-suite <suite>` or whose environment carries the private HOME, deletes the
   `<suite>` and `<suite>.private` preference domains (macOS writes them to the
   real `~/Library/Preferences` regardless of `HOME`) and unlinks their plist
   files under the real `~/Library/Preferences` (`defaults delete` leaves the
   files, which keeps the domain listed), removes
   `/private/tmp/omc-<hex>`, and reports every leftover path, PID, or domain
   as a failure note. The session fixture owns only identity (PID, suite,
   socket, copy, home); each test connects under its own `asyncio.run` with
   `invalidate_app`, as today.

Cases: caller-tab targeting with a split pane and competing OSC 0/1/2 writes
from a foreground command; the branch transition sequence with proof that
after `release` a subsequent OSC write moves the tab title, showing the
override is gone rather than replaced; an ordinary fish prompt with the hook,
then plain `codex`, a real `codex resume`, and `claude`, asserting the branch
during provider activity and after exit. Codex reuses `_codex_account`
unchanged with `CODEX_HOME` inside the private HOME. Claude cannot reuse the
merged tier's real-HOME profile because `~/.claude.json` is HOME-relative: the
private HOME receives a generated `.claude.json` (onboarding complete, trust
for the disposable repo). The probe checks whether `claude auth status` inside
the private HOME still sees the first-party Keychain login; if not, the tab
exports `CLAUDE_CODE_OAUTH_TOKEN` read from the existing Keychain item into a
0600 file that is unlinked on teardown, as the backup did. The token never
appears in argv, logs, or evidence files. `_native_claude_repo`'s login
preflight remains the fail-loud gate.

## 8. Reuse map

From `backup/ordinary-fish-pre-squash`: `fish_integration.py` and its tests
(with the §5 changes); the installer post-step, uninstall change, and their
tests (made non-aborting); the CLI `shell-integration` wiring and banner
exemption; the hook skeleton (activation guard, handler replacement, PATH
hint); the exit-code discipline of `discover_git`, ported into fish; the
wheel-loads-hook test shape and its PTY prompt driver; the private-HOME
facts recorded by the dropped harness (sockaddr_un length, preference-domain
residue, `runJobsInServers`, Claude onboarding state).
Dropped: `title_guard.py`, `title_reconcile.py`, `title_state.py`,
`reconcile_title`, the `/tmp` lock, the private iTerm harness and its unit
tests, the Swift launcher, `Dockerfile.portable`, the `omc-title-guard`
console script, the `iterm2`/`websockets` dev pins, and the README fixture
provenance.

## 9. Documentation

README: replace the merged feature's fish paragraph with the hook behavior,
the automatic macOS provisioning, the one-time `reconcile` after a legacy
upgrade, `enable|disable|status|reconcile`, `OMC_FISH_TITLE_DISABLE=1` (which
also makes `omc start` fall back to today's behavior), the failure hint and
`omc title set -- <branch>` as the manual retry, the last-writer-wins and
nested-shell limitations, that `disable` affects ordinary shells only, and the
native tier's prerequisites in one short paragraph.
