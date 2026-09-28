# iTerm2 branch titles and fish refresh integration

## 1. User-visible behavior and scope

An interactive `omc start` names its terminal tab after the full worktree
branch, including the configured prefix (for example,
`feature/iterm2-tab-title-fish-integration`). The title remains visible while
Claude or Codex runs and when the provider exits back to fish. Provider
session names and `OMC_SLUG` retain the slug; they are separate concepts from
the displayed branch name.

For iTerm2, omc sets an explicit tab title through its Python API. Ordinary
application writes to a session/window title must not replace that explicit
tab title. Other terminals retain the portable OSC behavior; they do not gain
the same override guarantee. Existing Claude and Codex title suppression
remains enabled.

Fish refreshes the desired title at shell initialization, before commands,
when returning to the prompt, and on directory changes. When a branch is
available in the current directory, its full name becomes the desired title.
Outside a repository or on a detached HEAD, fish retains the last valid branch
title, initially the branch selected by `omc start`. There is no restoration
to an unrelated pre-session title when the provider exits.

This feature applies to omc's generated interactive shell integration. It
does not install global fish configuration, modify iTerm2 preferences, or
start a background service. Other shell adapters keep their existing refresh
behavior while receiving the same generic terminal operation and full initial
branch title.

Hardening evidence: `src/omc/start.py:run_start` builds `branch` separately
from `slug` but currently passes the slug to the title, provider session name,
and shell; the graph places these calls in the same startup flow. Therefore
the title change must be selective rather than renaming every slug use.
`src/omc/providers/claude.py:ClaudeProvider.title_env` and
`src/omc/providers/codex.py:CodexProvider.session_argv` already implement the
provider suppression being preserved.

## 2. Generic terminal operation and iTerm2 ownership

The terminal layer owns title setting behind a reusable operation. Shells
describe when that operation runs; they do not know iTerm2 API details.
Terminal selection continues to use the explicit environment supplied by
`ToolContext`. The iTerm2 adapter uses the Python API to resolve the caller's
session and then its containing tab before setting the explicit title.
Branch text is literal data: iTerm2's explicit title is an interpolated string,
so arbitrary branch characters must not be evaluated as title expressions.

The target is determined from the launching session identity, never from
the currently focused window, tab, or session. This must work when another tab
has focus and when the caller is one pane of a split tab. An absent, malformed,
or stale caller identity is a failure to identify the target, not permission
to rename whichever tab happens to be active.

The API operation is bounded: unavailable API service, denied authorization,
missing dependency, missing target, or timeout cannot hang startup or the
prompt. Failures produce a concise diagnostic and retain best-effort OSC
behavior, explicitly explaining that this fallback cannot pin the tab title.
Failures are not reported as successful API title updates. Repeated prompt
events for the same desired title must not produce unbounded retry or warning
spam. No connection, polling loop, daemon, or watcher remains alive between
refreshes.

The timeout covers connection and authorization as well as the title RPC.
The SDK performs blocking authorization work, so an asynchronous RPC deadline
alone is insufficient. Isolating the SDK call in a short-lived worker under
the subprocess boundary gives the parent a deadline independent of SDK
cooperation and confines SDK environment mutation and diagnostics.

Dependency packaging and startup must keep non-iTerm2 platforms usable.
The plan chooses the concrete command/API seam and timeout details; shell
builders remain pure, subprocesses and environment inputs stay behind the
existing `ToolContext` boundary, and new subprocess calls use argv lists.
Dry-run can describe the intended operation but performs no API call. Headless
sessions never rename a terminal or open an API connection.

Hardening evidence: `src/omc/terminals.py:Terminal` currently describes only
escape strings and `Iterm2Terminal` inherits the OSC implementation; a real
effectful operation is needed rather than pretending an API request is another
escape sequence. `src/omc/toolctx.py:ToolContext.run` already supports bounded
child execution. Timeout handling belongs at that boundary, without importing
`subprocess` elsewhere. iTerm2 is not in the dependency graph registry; the
graph cannot establish its semantics. The iTerm2 2.24 SDK and a successful
read-only host connection establish caller-session resolution and tab lookup.
Official API references are
[Tab](https://iterm2.com/python-api/tab.html),
[App](https://iterm2.com/python-api/app.html), and
[Connection](https://iterm2.com/python-api/connection.html).
For future indexed dependency questions, use
`/omc:explain-dependency iterm2 <question>`; no dependency index is created by
this design pass.

## 3. Fish refresh and launch integration

The start path passes the full branch title into the generic terminal
operation before launching the provider. The generated fish integration owns
small, session-local hooks and a cached desired title. Hooks determine the
current branch, compare it with the last attempted/applied desired title, and
invoke the terminal operation only when a refresh is needed. Startup forces
the first attempt even though the initial branch is already known.

Use fish's initialization, pre-command, prompt-return, and directory-change
lifecycle so branch switches and worktree/directory changes are reflected at
the next relevant boundary. The startup command itself runs during fish init,
so it must not depend on an interactive pre-command event to set the first
title. The shell is not expected to discover branch changes made by a running
provider until control returns to an applicable shell boundary.

`fish_title` consistently returns the desired branch title, while the explicit
iTerm2 override provides protection throughout provider execution. Hooks must
preserve the command exit status, avoid recursion, keep normal user fish
startup behavior, and safely quote branch names, paths, and command arguments.
No title helper should prevent the provider or later interactive commands from
running after a title failure. A repeated desired title avoids another API
call; changing desired titles allows another bounded attempt after an earlier
failure.

The initial title and callback invocation remain represented in pure
`Shell.build_invocation` output. The existing effectful shell handoff continues
to materialize init data and execute the shell. Bash, zsh, and sh remain
compatible with the evolved shell/terminal seam. Claude's
`CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` and Codex's session-scoped
`tui.terminal_title=[]` stay intact; no global provider configuration changes.

Hardening evidence: graph impact for
`src/omc/shells/base.py:Shell.build_invocation` identifies both `run_start` and
`Shell.exec_interactive` as direct callers, so all adapters and both callers
must evolve together. `src/omc/shells/fish.py:FishShell.build_invocation`
already emits the initial OSC string before the startup command. Generated
`shell-integration.md` incorrectly says fish needs no initial emission;
source wins, and the new operation must preserve that explicit ordering.

## 4. Validation and acceptance

Every behavior change follows the project red-to-green policy: write and run
the failing requirement test before implementation. Unit coverage establishes
terminal selection, full branch versus slug semantics, pure shell construction,
caller-session targeting, bounded failure behavior, quoting, provider
suppression, and absence of API effects in dry-run/headless paths. Tests must
verify outcomes or exact argv where appropriate, rather than implementation
details that merely mirror the code.

Real fish integration coverage exercises initialization before provider launch,
branch changes, directory changes, return to prompt, duplicate-event
deduplication, detached/outside-repository fallback, command status preservation,
and title-operation failures. Existing shell/provider launch coverage remains
green. Stubs establish orchestration only; they do not establish iTerm2 or
provider behavior.

A real iTerm2 test must target an isolated test tab by its caller identity,
set the explicit title, issue competing application/session title writes, and
observe that the explicit branch title remains. It must cover another tab
having focus and a split-pane caller, without altering unrelated user tabs.
Real Claude and Codex execution must verify the installed suppression behavior
and the branch title during and after their sessions. Missing prerequisites
fail explicitly; selected integration tests must not skip.

Run `/omc:check` during development. Finish runs check, build, verify, and
review in the prescribed order. The project verify stage requires the
token-free Docker smoke suite plus serial live Codex and Claude lifecycle
coverage on the fresh checkout image because this change affects `omc start`
and provider launch. Record exact commands, source/image/model identity and
case results. Real local iTerm2 evidence supplements this matrix: Linux Docker
cannot validate the host application's tab-title API. A failed required stage
blocks publication until resolved; no installation from the feature worktree
is performed to make host tests convenient.

Hardening evidence: graph query results identify
`tests/e2e/test_e2e_interactive.py:test_interactive_exec_emits_title_before_session`
and `test_seeded_session_is_named_after_slug` as existing acceptance seams.
`tests/unit/test_providers.py:test_codex_argv` pins suppression configuration.
The live matrix requirement comes from `.omc/skills/verify/SKILL.md`, not a new
test policy invented for this feature. The combined graph/source review found
no requirement for global shell settings or a long-lived API service.
