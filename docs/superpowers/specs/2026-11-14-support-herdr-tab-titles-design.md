# herdr sidebar follows an omc session: worktree folder and branch at launch and at every Claude start

Date: 2026-11-14 · Branch: `feature/support-herdr-tab-titles` · Slug: `support-herdr-tab-titles`

## 1. Problem

User request: "Support HERDR so that we properly update the tab titles with
the folder and then the branch name below like it would do when terminating
the claude session and re-initiating it. this is akin to the pyhton API we
used for iTerm2".

herdr (0.9.5) shows each workspace in its sidebar as two rows: the folder
name, and below it the Git branch. It derives both from the pane's working
directory, which is the last *accepted* OSC 7 report from the pane, or, when
no report was ever accepted, the working directory of the pane's own shell
process.

`omc design`, `omc implement` and `omc review` replace themselves with a
nested interactive shell that runs `cd <worktree>` and then the provider
(`src/omc/session.py:session_plan` → `src/omc/shells/{fish,zsh,bash}.py`
`build_invocation`; the exec is `Shell.exec_interactive` in
`src/omc/shells/base.py`). The pane's own shell stays in the directory omc
was launched from, and nothing in the nested shell produces a report herdr
accepts: zsh and bash emit none, and fish emits OSC 7 with the machine's
hostname (`file://hetzner-nbg1/…`), which herdr rejects (it accepts only an
empty host or `localhost`). So for the whole session the sidebar keeps
showing the launch folder and the launch folder's branch. Observed live: the
sidebar says `oh-my-clanker` while Claude works in
`oh-my-clanker.feature-support-herdr-tab-titles`.

The outcome wanted: when an omc session launches inside herdr, the sidebar
shows the worktree folder with its branch below, immediately. This is the
herdr counterpart of the iTerm2 tab-title integration.

Added by the user when the design was hardened: "you need to also make
sure that just entering a claude session again will update it, at least if
the claude session was once belonging to omc design". The case behind it:
the omc-launched shell has exited, the user later changes into the worktree
from the pane's own shell and starts or resumes Claude there. No omc launch
shell is involved, so the launch report alone cannot cover it.

Facts this record relies on, verified against herdr v0.9.5 source and live
in a private `herdr --session` server:

- A host-less report, `ESC ] 7 ; file:///<abs path> BEL`, relabels the
  workspace to that folder at once; `herdr pane get` then returns it as
  `cwd` and `herdr workspace get` returns the folder name as `label`.
- herdr percent-decodes the path and ignores a report whose path is not an
  absolute existing directory.
- An accepted report is sticky. herdr 0.9.5 has no way to clear it: once a
  pane has reported, herdr stops following the shell process's directory in
  that pane until the next accepted report (verified: after a report, a
  plain `cd` no longer moves the label; before any report it does).
- Inside herdr the pane environment has `TERM_PROGRAM=herdr` and
  `HERDR_ENV=1`. Inside tmux running in herdr, `TERM_PROGRAM` is `tmux`
  while `HERDR_ENV` is still inherited.
- A process Claude Code spawns (its Bash tool) has no controlling terminal:
  opening `/dev/tty` fails with `ENXIO`. Its ancestor `claude` does have the
  pane's terminal (`ps -o ppid=,tty=` shows `pts/7`), and a report written
  to `/dev/pts/7` relabelled the live workspace and set the pane cwd.

## 2. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | omc's launch shell emits a host-less OSC 7 report for the worktree; herdr derives folder and branch itself. No herdr binary or socket call at runtime. | `herdr workspace rename`, the shape of the iTerm2 API worker (sets only the folder row; the branch row stays the launch folder's branch; the custom name is sticky and herdr has no command to clear it). `herdr workspace report-metadata` custom tokens (the user must edit herdr's sidebar row config; TTL and sequence bookkeeping). |
| D2 | The report is a `file://` URI with an empty host and a percent-encoded path. | The host-ful form fish emits (herdr ignores it). A bare path after `7;` (herdr 0.9.5 accepts it, verified live, but it is a leniency in herdr's parser rather than the OSC 7 form; the URI is what every OSC 7 consumer defines). |
| D3 | All three entry points (`omc design`, `omc implement`, `omc review`) and all three shell builders (fish, zsh, bash) report at launch, right after `cd <worktree>` and before the provider starts. | Fish only (zsh and bash launches would stay mislabelled for the whole session). |
| D4 | Following later `cd`s is fish only: inside the omc-launched fish each `PWD` change re-reports. | A `chpwd` hook in zsh and a `PROMPT_COMMAND` step in bash (both need percent-encoding written in POSIX shell; nobody asked for it). |
| D5 | When the omc-launched shell exits it reports the directory omc was launched from (fish `fish_exit` event, zsh `zshexit` hook, bash `EXIT` trap). | No exit report (the sticky report would leave the sidebar on the worktree after the session is gone, while the pane's shell is back in the launch folder). |
| D6 | Everything is guarded at run time by `test "$TERM_PROGRAM" = herdr`, evaluated by the launched shell. | Detecting herdr in Python, or a new `Terminal` subclass in `src/omc/terminals.py` (the builders take no environment today, so `--dry-run` prints the same shell text in every terminal, and `_ITERM2_HOOK_PREDICATE` already sets the pattern of a predicate the shell evaluates; `Terminal` and `detect_terminal` serve only the title operation, and this is not a title). Guarding on `HERDR_ENV` (still set inside tmux, where the report would go to tmux instead of herdr). |
| D7 | In the launch path only the three shell builders gain behaviour, and they stay pure. fish percent-encodes at run time with `string escape --style=url`. zsh and bash embed a URI computed in Python by a small percent-encoding helper that does not import `urllib`. The builders gain an `origin_cwd` argument; `session_plan` fills it and `start.py`, `implement.py` and `review.py` forward it to `exec_interactive` with the plan's other fields. | `urllib.parse.quote` (`.omc/config/AGENTS.md`: nothing outside `ToolContext` imports `urllib`). Capturing the origin inside the launched shell (`exec_interactive` has already changed directory before the shell starts, so the shell cannot see it). |
| D8 | No opt-out environment variable. | An `OMC_HERDR_DISABLE`-style switch (no one needs one yet; the guard already makes the code inert outside herdr). |
| D9 | The E2E asserts herdr's own answers, `workspace get` label and `pane get` cwd, and does not read the sidebar screen. | Screen-scraping the sidebar for the branch row (the branch row is herdr's derivation from the same cwd; a screen read tests herdr's renderer). |
| D10 | This record carries a ready-to-file upstream herdr issue note (§8); the user files it. | omc filing it, or omc working around fish's host-ful OSC 7 in ordinary shells. |
| D11 | A Claude Code `SessionStart` hook, shipped in omc's plugin (`hooks/hooks.json` at the plugin root), runs a new quiet command `omc internal herdr-cwd`, which writes the same host-less report for the session's working directory to Claude's terminal. It fires on startup, resume, clear and compact. The launch-shell report (D1–D7) stays: it covers Codex, fires before the provider starts, and does the exit restore. | Relying on the launch report alone (does not cover Claude started or resumed from the pane's own shell, the user's added requirement). A hook written into each worktree's `.claude/settings.json` (omc removed its settings-file hooks on 2026-10-06 and old worktrees kept calling a command that no longer existed; a plugin hook updates with the plugin). The herdr socket API (D1). |
| D12 | The hook reports for every Claude session inside herdr, not only omc worktrees. The user confirmed this scope knowing its cost (D14). | Reporting only when `OMC_SLUG` is set or the directory is an omc worktree (the user's case is exactly a session started outside the omc launch shell, where no omc marker is in the environment; worktree detection is extra mechanism for no benefit). |
| D13 | Claude only. Codex gets no hook and keeps the launch-shell report. | A Codex counterpart (not asked for). |
| D14 | The residual limitation is accepted by the user, for every herdr pane that ran an omc session or any Claude session: after a report, plain `cd` in the pane's own shell does not move the sidebar until the next Claude start or omc launch (§7). The reason: the sidebar is right whenever Claude is running, which is the user's case, and narrowing the hook costs more mechanism. It is documented and §8 asks upstream for the fix. | Dropping D1 for herdr's socket API to avoid sticky reports. |

D8, D9 and D10 are the brainstorm's recommended defaults, taken when the
user invoked `/omc:design` without overriding them. Within D7, the source of
`origin_cwd` (the `PWD` entry of `ctx.env`) and its carrier (a `SessionPlan`
field, §4.3) are the Design worker's calls. D11 to D14 are the user's
answers to the hardening pass: D14 answers the two questions it raised
about the sticky report (after an omc session, and after any Claude
session), D11 is the mechanism the user approved for the added requirement
in §1, and D12 and D13 are the scope the user chose for it.

## 3. Goals and non-goals

**Goals.**

- Inside herdr, an interactive `omc design`, `omc implement` or `omc review`
  session shows the worktree folder and its branch in the sidebar from the
  moment the provider starts, in fish, zsh and bash.
- In fish, a `cd` typed in the omc-launched shell after the provider exits
  moves the sidebar with it.
- When the omc-launched shell exits, the sidebar returns to the folder omc
  was launched from.
- Inside herdr, starting or resuming a Claude session (`claude`,
  `claude --resume`, `claude -c`) puts the sidebar on the directory that
  session runs in, whether or not omc launched it.
- Outside herdr, and inside tmux within herdr, not one byte more is written
  to the terminal than today.
- The hook never delays, blocks or fails a Claude session start, and never
  adds text to it.
- `--dry-run` output stays independent of the terminal it runs in.

**Non-goals.**

- Ordinary herdr shells that omc did not launch. fish's host-ful OSC 7 stays
  ignored by herdr there; that is an upstream fix (§8).
- Renaming herdr workspaces or tabs, or any use of herdr's socket API or
  binary by omc at run time.
- A Codex hook (D13), and making the sidebar follow plain `cd` in the
  pane's own shell after a report (D14).
- Reproducing the exact "terminate and re-initiate" flow from the request.
  The fix does not depend on it.
- The POSIX `sh` fallback shell (`ShShell` in `src/omc/shells/registry.py`):
  it never changes directory itself and stays as it is.
- Headless runs (`--headless`): there is no launch shell and no pane to
  relabel.
- Changing what fish itself emits, herdr tab labels, and provisioning
  anything into the user's shell configuration.

## 4. Design

### 4.1 The report and its guard

The report is the byte sequence `ESC ] 7 ; file://<encoded path> BEL`, where
`<encoded path>` is the absolute path with every byte percent-encoded except
the RFC 3986 unreserved characters (`A–Z a–z 0–9 - . _ ~`) and `/`. The host
is empty, so the URI always starts `file:///`.

Every piece of shell text this record adds sits inside one conditional that
the launched shell evaluates: `test "$TERM_PROGRAM" = herdr` (fish) or
`[ "${TERM_PROGRAM-}" = herdr ]` (zsh, bash; the empty default keeps the
test valid when the user's own rc, sourced first, turns on `set -u` /
`setopt nounset` and `TERM_PROGRAM` is unset). Python never reads
`TERM_PROGRAM`. That is the same arrangement as `_ITERM2_HOOK_PREDICATE` in
`src/omc/shells/fish.py`: the builder emits the predicate, the shell decides.

### 4.2 The three shell builders

Each builder gains one guarded block, placed after its `cd <worktree>` and
before the provider command. The block is independent of the existing title
code: in fish it follows the iTerm2 `if … else … end` (and the plain
no-`title_argv` branch) rather than living inside either arm.

| Shell | At launch | On later `cd` | On shell exit |
|---|---|---|---|
| fish (`-C` string) | reports `$PWD`, encoded at run time by `string escape --style=url -- $PWD` | a function registered `--on-variable PWD` reports again | a function registered `--on-event fish_exit` reports the origin, encoded the same way |
| zsh (generated `.zshrc`) | prints the worktree URI embedded by the builder | nothing (D4) | a `zshexit` hook prints the embedded origin URI |
| bash (generated `rc.bash`) | prints the worktree URI embedded by the builder | nothing (D4) | an `EXIT` trap prints the embedded origin URI |

Requirements the plan must keep:

- The launch report comes after `cd` and before the provider command in the
  generated text, for every shell. The prompt hooks fire only after the
  provider exits, which is why the existing title code also emits once up
  front.
- The exit hooks are added, not substituted: the generated rc files source
  the user's own `.zshrc` / `.bashrc` first, so a `zshexit` hook or `EXIT`
  trap the user already has must still run.
- New fish function names take the `__omc_herdr_` prefix, apart from the
  `__omc_title_*` set that the packaged hook erases on activation.
- Embedded strings go through `shlex.quote`, like every other value the
  builders embed.

The percent-encoding helper is a small pure function in a new module,
`src/omc/herdr.py`, which is where the hook command of §4.5 also lives. It
encodes the path's filesystem bytes, so a non-UTF-8 path cannot raise. The
zsh and bash builders and the hook command call the same function.

### 4.3 Where the origin comes from

`session_plan` reads the origin once, as `ctx.env.get("PWD")`, the same way
it already reads `ctx.env` to pick the terminal and the shell. It passes the
value to `build_invocation` and returns it as a new `SessionPlan` field,
`origin_cwd: str | None`. The three entry points forward `plan.origin_cwd`
to `Shell.exec_interactive` exactly as they forward `plan.title_seq` and
`plan.title_argv` today; `exec_interactive` hands it to `build_invocation`
when it rebuilds the invocation with the real worktree path (in `start.py`
the plan is built before the worktree exists, with the placeholder
`<worktree>`). `Shell.build_invocation` and `Shell.exec_interactive` take it
as `origin_cwd: str | None = None`.

`ctx.env` is the environment snapshot `ToolContext` already carries, so no
module gains a new environment or filesystem read, and unit tests set the
origin by constructing the context. `PWD` is the directory the user's shell
was in when it ran omc, which is the directory the pane's shell is still in
when the launched shell exits.

With `origin_cwd=None` (no `PWD` in the environment) the builders emit the
launch report and no exit hook. `ShShell` accepts the argument and ignores
it.

The report is emitted even when the origin and the worktree are the same
directory, as they are when `omc implement` or `omc review` is typed inside
the worktree. One rule for all three entry points; and a pane frozen on an
earlier report (§7) is corrected by it.

### 4.4 The Claude `SessionStart` hook

omc's plugin gains `hooks/hooks.json` at the plugin root (the repository
root, beside `skills/`; `.claude-plugin/plugin.json` names no hook path, so
Claude Code picks the file up by convention). It registers one
`SessionStart` command hook with no matcher, so it runs on startup, resume,
clear and compact, and gives it a short timeout.

The hook's command line is
`[ "${TERM_PROGRAM-}" = herdr ] && omc internal herdr-cwd >/dev/null 2>&1 || true`.
Its shell test means `omc` starts only inside herdr (not outside herdr, and
not inside tmux within herdr); Claude Code runs hook commands with bash.
The line is written so that it cannot fail or print whatever the `omc` on
`PATH` is: a missing
`omc`, or an older CLI that does not know the verb and answers with its
usage text and exit 2, ends as a silent success. That is the lesson of the
settings-file hooks removed on 2026-10-06, and it matters here because
Claude Code adds a `SessionStart` hook's stdout to the session's context and
shows a failing hook to the user.

`.codex-plugin/plugin.json` declares only `"skills": "./skills/"`, and omc's
own Codex payload check (`src/omc/plugin.py:_codex_skill_files`) compares
the `skills/` tree only, so nothing in Codex's manifest or omc's installer
refers to `hooks/`. `codex plugin add` copies the whole plugin tree,
`hooks/` included, into its cache (observed, `docker/PLUGIN-NOTES.md`); no
Codex run was observed firing it (§7). The hook file is not packaged into the wheel:
the plugin is installed from the marketplace source, not from wheel assets.

### 4.5 `omc internal herdr-cwd`

A new verb in `src/omc/internal.py`'s dispatcher; the work is in
`src/omc/herdr.py`. In order:

1. Return at once unless `ctx.env` has `TERM_PROGRAM` equal to `herdr`.
   (The hook's command line already makes the same test in the shell,
   §4.4; this check stays for direct calls.)
2. Read the hook's JSON from stdin and take its `cwd`. Anything else
   (no input, not JSON, no `cwd`, a relative path) ends the command.
3. Find the terminal: starting at its own parent, ask
   `ps -o ppid= -o tty= -p <pid>` for each ancestor through
   `ctx.run_bounded` (two separate `-o` options, because BSD/macOS `ps`
   reads everything after the first `=` as a header, so `ppid=,tty=` would
   be one column there), until one has a terminal (the first whose tty
   column is not `?` / `??`), or the walk reaches pid 1, passes 32
   ancestors, or `ps` fails. The tty name must look like a terminal device name before
   it is joined to `/dev/`; anything else ends the command. Verified on this
   host: from a process Claude spawned, the first ancestor is `claude` on
   the pane's terminal.
4. Write `ESC ] 7 ; file://<encoded cwd> BEL` to that device, using the
   helper of §4.2.

It prints nothing on stdout or stderr in any of these outcomes and returns
0 for all of them: outside herdr there is nothing to do, and inside herdr
it is a best-effort attempt whose only caller is a hook that must not see a
failure. This sits inside `.omc/config/AGENTS.md`'s exit codes rather than
against them: code 1 is an error reported to someone, code 3 tells a calling
skill to fall back to its own judgment, and this command has neither a
reader nor a fallback. Wrong arguments still return the dispatcher's usage
and exit 2, like every other verb.

The command reports whatever directory the session runs in. It does not
check for an omc worktree (D12) and has no exit counterpart: when Claude
ends, the report stays (§7).

Assumption, not verified live: a `SessionStart` hook process has the same
ancestry as the Bash tool (so the walk finds `claude`'s terminal) and
inherits `TERM_PROGRAM`. The token-gated E2E in §5 is what checks it.

### 4.6 Unchanged

The OSC 0 title sequence and `title_argv` helper call (herdr shows OSC 0 as
the pane's terminal title); `src/omc/terminals.py`, `iterm2_title.py`,
`title.py`, `terminal_title.py`; the packaged fish hook
`src/omc/assets/omc-title.fish` and its provisioning; herdr's numbered tab
labels and window title; `run_headless`; the existing `SessionPlan` fields;
both plugin manifests; the worktree notification wiring in `notify.py`.

## 5. Tests (red first, per `.omc/config/AGENTS.md`)

Every test below is written and seen failing before the implementation.
None is skipped: a missing prerequisite is a `pytest.fail` naming the
command that satisfies it.

**Unit (`just check`).**

- `tests/unit/test_shells.py`: for fish, zsh and bash, the generated text
  contains the guarded block; the launch report sits after `cd` and before
  the provider command; the exit hook is present when `origin_cwd` is given
  and absent when it is `None`; fish registers the `PWD` handler; `ShShell`
  output is byte-identical with and without `origin_cwd`. Exact-text
  assertions on the block, in the style of the existing fish tests.
- Encoding helper: a space, a literal `%`, a non-ASCII character
  (`/home/a b/x%y/ü` → `/home/a%20b/x%25y/%C3%BC`, the same result fish's
  `string escape --style=url` gives), unreserved characters and `/` kept.
- `tests/unit/test_session.py`: `ctx.env["PWD"]` becomes
  `SessionPlan.origin_cwd` and reaches the builder. The three entry-point
  tests that already capture the `exec_interactive` keyword arguments
  (`test_start.py`, `test_implement.py`, `test_review.py`) also assert
  `origin_cwd`.
- A real fish run of the built command, on the existing `fish_session`
  fixture in `tests/unit/test_fish_title.py` (it already executes the built
  `fish -i -C …` argv with `TERM_PROGRAM` scrubbed, extra typed commands and
  captured stdout; no PTY driver is needed because the report is written to
  stdout): with `TERM_PROGRAM=herdr` the output contains the host-less
  worktree report at start, a second one after a `cd`, and the origin report
  at exit, each with a path that needs encoding; with `TERM_PROGRAM` unset or
  set to `tmux` it contains no host-less report (`ESC ] 7 ; file:///`).
  fish's own host-ful OSC 7 may be in the output either way and is not what
  the test looks for.

- `omc internal herdr-cwd`, driven through `run_internal` with hook JSON on
  stdin, a stub `ps` on the restricted `PATH` (the `make_stub` pattern of
  `tests/unit/_stubs.py`), and the device directory pointed at a temporary
  path: the exact report bytes land in the
  fake terminal file for a `cwd` that needs encoding; nothing is written and
  nothing is printed when `TERM_PROGRAM` is not `herdr`; malformed stdin, a
  missing `cwd`, no ancestor with a terminal and a device that cannot be opened each
  end silently with exit 0 (one parametrized test); extra arguments return
  exit 2.
- `hooks/hooks.json` parses, registers exactly one `SessionStart` command
  hook with a timeout, and its command names `omc internal herdr-cwd`
  (`tests/unit/test_plugin_manifests.py`).

**E2E (Docker, token-free).** One new test module, no provider and no LLM
token:

- `docker/Dockerfile.e2e` gains the herdr v0.9.5 release binary
  (`herdr-linux-x86_64` / `herdr-linux-aarch64`, selected by `TARGETARCH`
  and checked against pinned sha256 values, the way the worktrunk binary is
  installed) and the `fish` and `zsh` packages, which the image lacks today,
  plus `procps` if the image has no `ps`.
- The test starts a private headless `herdr --session <name>` server in the
  container, creates a workspace whose pane starts in a launch repository,
  and in that pane runs omc's real launch path
  (`Shell.exec_interactive` of the installed omc, once per shell) with a
  harmless placeholder command where the provider would be.
- It asserts on herdr's own state: `herdr workspace get` label equals the
  worktree folder name and `herdr pane get` cwd equals the worktree path;
  then, after the launched shell is told to exit, both equal the launch
  folder.

- Hook command, same module: in a pane whose shell stays in one directory,
  run `omc internal herdr-cwd` as a `setsid` child (no controlling terminal,
  like a process Claude spawns) with hook JSON naming another directory;
  assert `herdr pane get` cwd and `herdr workspace get` label follow it.

**E2E (Docker, token-gated).** One test with real Claude and the installed
plugin, in the tier that already requires a token (`require_token`): in a
herdr pane whose shell stays in one directory, start a short Claude print
run from another directory, and assert `herdr pane get` cwd equals that
other directory. This is the test that proves the plugin hook fires and
finds the terminal. This host has no token, so it runs wherever the
token-gated tier runs.

This keeps the AGENTS.md rule that an external integration has at least one
E2E driving the real tool and asserting its effect. The E2E enters at
`Shell.exec_interactive`, below the three entry points, because those need a
provider and a token; their forwarding of `origin_cwd` is covered by the unit
tests above. Real zsh runs only here (the unit tier has no zsh requirement).

## 6. Documentation

- `docs/shell-integration.md` gains a herdr paragraph: what updates (folder
  and branch rows of the sidebar), when (at launch, on `cd` in fish, on
  shell exit), that a session started inside tmux within herdr is left
  alone, that ordinary fish shells are not followed by herdr because of the
  hostname in fish's own report, that every Claude session started or
  resumed inside herdr reports its directory through the plugin hook
  (Claude only), and the two limits described in §7 (plain `cd` after a
  report, and which pane names the workspace).
- `README.md` has one sentence linking macOS fish/iTerm2 branch titles to
  that document. Its source is the recipe line in
  `.claude/skills/regenerate-readme/SKILL.md` ("One sentence links macOS
  fish/iTerm2 branch titles to `docs/shell-integration.md`"); that line gains
  herdr and `/regenerate-readme` rewrites the README. Never by hand.
  `src/omc/internal.py` is one of the recipe's sources, so the new verb
  goes through the same regeneration.
- `docker/PLUGIN-NOTES.md`, the record of how plugin loading behaves per
  harness, gains what Claude Code and Codex each did with the new `hooks/`
  directory, as observed in the E2E image.
- `tests/unit/test_cli.py` reads `docs/shell-integration.md` and the README
  guards report drift; both stay green.

## 7. Risks and side effects

- **After a report, the pane stops following plain `cd` (accepted, D14).**
  Before a pane's first accepted report, herdr follows the shell process's
  directory. Every report is sticky, and herdr 0.9.5 cannot withdraw one.
  So once an omc session or any Claude session has run in a pane, a plain
  `cd` in that pane's own shell no longer moves the sidebar; it moves again
  at the next Claude start or omc launch there. The exit report (D5) and
  the hook (D11) keep the sidebar right at the moments that matter: during
  a session, when the omc-launched shell exits, and whenever Claude starts.
  The scope chosen in D12 carries this to panes that never ran omc: a pane
  where plain `claude` ran used to follow `cd` afterwards and now waits for
  the next Claude start. The user accepted both cases (D14), because the
  sidebar is right whenever Claude is running and narrowing the hook costs
  more mechanism. The documentation states both; §8 asks upstream for the
  two changes that remove it.
- **The hook runs on every Claude session start inside and outside herdr.**
  Outside herdr, and inside tmux within herdr, it costs one bash `[` test:
  the command line's `TERM_PROGRAM` check ends it before `omc` starts.
  Inside herdr it costs one `omc` process start (0.23 s wall measured on
  this host for an `omc internal` verb). The hook's timeout bounds the worst
  case.
- **CLI and plugin can be at different versions.** `omc update` moves both,
  but a plugin newer than the CLI calls a verb the CLI does not have. The
  hook's command line absorbs that (§4.4).
- **The hook's process ancestry is assumed (§4.5).** If a `SessionStart`
  hook has no ancestor with the pane's terminal, the command does nothing
  and the sidebar stays as the launch report left it. macOS prints `??` for
  "no terminal" and names devices `ttys003`; the walk handles both, verified
  only on Linux.
- **Codex loading the new directory.** Nothing in `.codex-plugin/plugin.json`
  points at `hooks/`. If a Codex version loaded it by convention anyway, the
  command would report Codex's directory too, which is harmless; the
  existing Codex plugin E2E shows whether loading still succeeds.
- **Only the workspace's first pane names the workspace.** herdr takes the
  workspace's folder row from the root pane of its first tab. A session
  launched in any other pane changes that pane's working directory in herdr
  but not the workspace rows, and a workspace the user renamed keeps its
  custom name. Both are herdr's rules and are left alone. The E2E launches
  in the root pane.
- **zsh and bash do not follow `cd` inside the launched shell (D4).** After
  the provider exits, moving around in the nested zsh or bash leaves the
  sidebar on the worktree until the shell exits.
- **A shell that dies without running its exit hook** (killed with
  `SIGKILL`) leaves the sidebar on the worktree. Closing the pane removes
  the pane with it; otherwise the next accepted report corrects it.
- **Claude inside tmux within herdr.** `TERM_PROGRAM` is `tmux` there, so
  the hook's command line stops at its `TERM_PROGRAM` test before `omc`
  starts, like the launch block.
- **`TERM_PROGRAM=herdr` where the terminal is not herdr.** tmux 3.2 and
  later replace `TERM_PROGRAM` with `tmux` (verified), which is what makes
  the guard inert there. GNU screen and older tmux leave the inherited
  value, so inside them the launched shell writes the report to the
  multiplexer. Multiplexers do not forward OSC 7 to the outer terminal, so
  herdr's sidebar is expected to stay as it is today; this case was not
  verified live and gets no extra guard.
- **The herdr release binary in the E2E image** must run on
  `node:22-bookworm-slim` for both architectures. If it does not, the plan
  stops and reports rather than weakening the test.
- **Dry-run text changes.** The fish `shell argv` printed by `--dry-run`
  grows by the guarded block and now contains the launch directory. It still
  does not vary with the terminal.

## 8. Upstream herdr issue note (ready to file; the user files it)

> **OSC 7 cwd reports: accept the local hostname, and allow a report to be
> withdrawn**
>
> herdr 0.9.5, Linux.
>
> 1. `parse_file_uri_cwd` (`src/pane/osc.rs`) accepts `file://` URIs only
>    when the host is empty or `localhost`. fish, and the OSC 7 snippets
>    commonly used for zsh and bash, send the machine's hostname
>    (`file://myhost/home/me/repo`), so herdr drops every report from an
>    ordinary fish shell and the sidebar follows only the pane's shell
>    process. Proposal: also accept a host equal to the local hostname
>    (case-insensitive, with and without the domain part).
> 2. An accepted report is sticky: `Pane::cwd` prefers `reported_cwd`
>    forever, so once a nested shell has reported and exited, the pane no
>    longer follows the outer shell's directory. Proposal: treat an OSC 7
>    with an empty payload as "withdraw the report", returning the pane to
>    process-cwd tracking.
>
> Repro for 2: in a pane, `cd /tmp` (label follows), then
> `printf '\033]7;file:///usr\007'` (label `usr`), then `cd /tmp` (label
> stays `usr`).

## 9. Plan

Implementation plans born from this record give each task a `Complexity:`
line (`simple` | `medium` | `high`).

## Implementation review

### 2026-11-14 — auditor: Claude (Opus 5.5 orchestrator, Review worker opus)

Code conforms to D1–D14 and every §3 goal and non-goal. All findings were
"record is stale"; each was dispositioned by amending this record (no
amendment contradicts a Decisions row). No product code changed in the
audit.

1. Important — record is stale — §4.4, §4.5 step 1, §7 (hook cost, tmux) —
   `hooks/hooks.json:4`: the hook command checks `TERM_PROGRAM` in the shell
   before starting `omc`. Disposition: record amended (§4.4 command line,
   §4.5 step 1 note, §7 cost and tmux bullets).
2. Minor — record is stale — §4.1 — `src/omc/shells/bash.py:20`,
   `src/omc/shells/zsh.py:17`: nounset-safe guard `[ "${TERM_PROGRAM-}" = herdr ]`.
   Disposition: record amended.
3. Minor — record is stale — §4.5 step 3 — `src/omc/herdr.py:36`:
   `ps -o ppid= -o tty=` (portable to BSD/macOS ps). Disposition: record amended.
4. Minor — record is stale — §4.5 step 3 — `src/omc/herdr.py:28,35`: the
   walk is capped at 32 ancestors. Disposition: record amended.
5. Minor — record is stale — §4.4 — `docker/PLUGIN-NOTES.md`: `codex plugin add`
   copies `hooks/` into its cache (not observed firing). Disposition: record
   amended.
6. Minor — code deviates from the record (test detail) — §5 —
   `tests/unit/test_herdr.py:108`: the "unwritable device" case tests a
   missing device (same `OSError` path). Disposition: record amended to "a
   device that cannot be opened".

Not run on this host (no LLM token, Linux only): the token-gated real-Claude
hook E2E and real macOS `ps`/tty behaviour.

## Deliberate complexity

- `grug:80-20` on D2/D7 (two percent-encoders, fish's `string escape` and a
  Python helper, where a bare path after `7;` needs neither): waived, D2. The
  bare form works only through a leniency in herdr 0.9.5's parser, which also
  trims whitespace and surrounding double quotes from it; the `file://` URI
  is the OSC 7 form. The cost kept is one small pure function and one fish
  builtin call.
- `grug:locality` on §4.3 (one optional string, `origin_cwd`, crosses the
  four builders, `session.py` and the three entry points): waived, D7. The
  shorter route, `exec_interactive` reading the environment itself, would
  give the dry-run plan and the real exec two different sources for the same
  value and put an environment read in the shell layer. The field travels
  the path `title_seq` and `title_argv` already travel.
- `grug:80-20` on D11 (two mechanisms emit the same report, the launch
  block in three shells and the Claude hook, and both fire for a Claude
  session omc launched): waived, D11. The hook alone would lose Codex and
  the exit restore; the launch block alone does not cover Claude started
  from the pane's own shell.
- `grug:say-no` on D12 (the hook acts on every Claude session in herdr,
  wider than the stated need of sessions that once belonged to omc):
  waived, D12. Narrowing it needs a way to recognise such a session where
  no omc marker is in the environment, which is more mechanism than the
  unconditional report.
- `human-review:structure/find-the-existing-mechanism` on
  `src/omc/herdr.py` (hand-rolled percent-encoder beside
  `urllib.parse.quote`): waived, D7. `.omc/config/AGENTS.md` forbids
  importing `urllib` outside `ToolContext`, so the stdlib encoder is not
  available to this module; the helper is one byte-set and one join,
  pinned by `test_percent_encode_*` against fish's own output.
