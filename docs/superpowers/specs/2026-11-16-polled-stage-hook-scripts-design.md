# Stage hooks omc runs detached and polls: `.omc/bin/{check,build,verify}`

Date: 2026-11-16 · Branch: `feature/polled-stage-hook-scripts` · Slug: `polled-stage-hook-scripts`

Supersedes the stage convention of `2026-07-17-omc-stages-design.md`
(`.omc/skills/<stage>/SKILL.md`) for `check`, `build` and `verify`. The
`review` stage stays a project skill. The fix-forward rule of
`2026-11-15-run-verify-only-before-push-design.md` is kept and adjusted
(section 5.6).

## 1. Problem

A project's `check`, `build` and `verify` stages are prose today:
`.omc/skills/<stage>/SKILL.md`, which the `/omc:<stage>` proxy reads and an
agent then carries out with its shell tool. Three things are wrong with that.

- **The ten-minute ceiling.** Claude's shell tool ends a foreground command
  after ten minutes at most. A verify stage that runs longer (this repo's
  Docker E2E suite, apuntalo's gates) cannot be run as one command. Each
  project works around it on its own; apuntalo built a detach-and-poll tool
  (`~/Projects/apuntalo.co/docs/tools/gates.md`) and leaves the polling loop
  to the agent.
- **The agent judges what a machine can tell.** Whether the run has ended and
  whether it passed are read out of prose and command output by an LLM, per
  project, per run.
- **Nothing limits parallel runs.** Two sessions in one worktree can run the
  same expensive stage side by side, and nothing identifies a project or a
  worktree to count against.

The user's request, verbatim: "Look at how apuntalo does the new /verify poll
process. We need to hoist this into OMC. /build, /check and /verify all should
kick off a hook executable in `.omc/bin/build|check|verify.sh` that runs the
actual step and potentially fails. It will provide a log location in /tmp
where they can write their output and an initial instruction section where
they inform the OMC agent how to interpret the results. We need a clear signal
for termination and a clear signal for how to check if it has passed
successfully or needs fixing. So this way we can build our existing process
around this and allow them to be polled for more than the 10 minutes allowed
by claude today. Plus going forward we can limit parallel executions per
project as well."

## 2. Goals, non-goals, anti-goals

**Goals.**

- A stage is an executable, `.omc/bin/<stage>`. omc starts it detached,
  owns its log, tells mechanically when it has ended and whether it passed,
  and kills it.
- One polling loop, written once in omc and shared by the three proxy skills.
  The agent repeats one command; it decides nothing about time.
- A run may take any length of time. omc sets no overall limit; the project
  states its limits in the hook's instructions and omc enforces them.
- At most one stage run per worktree. A project ID and a worktree ID exist so
  that a later change can count runs per project.
- A failed verify can keep its system up so that `/omc:investigate local` can
  look at it.
- A stage run needs a running `omc watch` for its project and never outlives
  it, or its worktree (A1).

**Non-goals.**

- A limit on parallel runs per project. The IDs prepare it; a follow-up
  builds it.
- Understanding single test failures. The project provides that through
  `/omc:investigate local` and its `investigation-context` skill (U4).
- Migrating apuntalo. It migrates with `/omc:integrate` like any other repo.
- The `review` stage. It needs an LLM and stays `.omc/skills/review`.
- Changing the `OMC_STAGE` line. Its four fields stay as they are.

**Anti-goals.**

- A fallback to the old stage skills. A repo without a hook has no such stage
  (U3).
- Instructions that carry anything beyond the template (U2).
- A background service. A supervisor lives for one hook run and nothing
  restarts or schedules it.
- A time judgment by the agent, or a time limit invented by omc (U12).

## 3. Constraints

From `.omc/config/AGENTS.md` and the code as it is:

- `ToolContext` (`src/omc/toolctx.py`) is the only subprocess boundary, argv
  lists only. Its four run methods (`run`, `run_bounded`, `run_supervised`,
  `stream`) all wait for the child. A detached spawn is new and belongs
  there.
- "Long-running behavior is foreground-only: omc never creates daemons" is an
  invariant today. This design amends it (section 5.4) and does not stretch
  it silently.
- Exit codes: 0 ok, 1 error, 2 refusal, 3 bail. Machine contracts are single
  JSON lines.
- The stage-gate rule (`src/omc/distribution/AGENTS.md`) forbids a fix that
  edits the stage's own definition. A hook or its instructions that break the
  contract can therefore not be repaired by the fix loop; they go to the user.
- worktrunk copies every gitignored file into a new worktree
  (`src/omc/wtconfig.py`, `WT_TEMPLATE`), `.cache/` included. Anything omc
  keeps under `.cache/` must tell a copy from its own.
- `omc watch` is one foreground process per primary checkout. It holds an
  instance lock in the shared git directory for as long as it lives
  (`src/omc/watchlock.py`), and the kernel releases that lock when the
  process dies. This design adds one passive lock to watch (6.4, the
  fallback named in section 8) and changes nothing else in what it does.
- Claude's shell tool allows 600 s per command. Codex runs the same commands
  and its shell timeout has to cover them.
- Red before green for every change; tests drive real scripts, not stubs of
  omc's own runner.

## 4. Approaches considered

- **A (chosen): an omc supervisor process, detached.** `omc internal stage
  start` spawns a second omc process in its own session. That process runs
  the hook as its child, so it gets the exit code, owns the log file, checks
  the limits, and records the outcome in a status file. Polling and killing
  are omc commands that read that file. The supervisor also ends its own run
  when the project's `omc watch` or its worktree is gone (A1).
- **B: omc detaches the hook itself, without a supervisor.** Rejected: the
  process that started the hook exits, so nothing reaps it and its exit code
  is lost. The verdict would have to be guessed from the log, and nobody
  would be there to enforce a limit or a hold.
- **C: the harness's own background commands** (Claude's background Bash and
  Monitor). Rejected: Claude only, so Codex has no equivalent path; omc could
  not enforce one run per worktree, could not kill a run from another
  session, and would not own the log.
- **D: each project detaches itself**, as apuntalo does with
  `just <stage> --detach`. Rejected by the user (U1): every project would
  rebuild the same detach, poll and kill code, and omc could not build a
  per-project limit on top of it.

- **E: `omc watch` hosts the runs** as its own children, so that they end
  with it by construction. Rejected by the user ("We aren't moving them into
  `omc watch`"): watch would gain a second job and a second thread beside its
  tick loop, which blocks for minutes during a knowledge refresh. A1 gets the
  same guarantee from a check inside the run.

## 5. Decisions taken during brainstorm

| # | Decision | Source |
|---|---|---|
| U1 | omc owns detachment, polling and killing. The hook runs the actual work in the foreground, as it is. | user |
| U2 | `<hook> --show-instructions` (that one parameter) prints the agent instructions. They follow a fixed template; anything beyond it stops the stage and is flagged. | user |
| U3 | No hook, no stage. No fallback to the old skills. Existing repos migrate with `/omc:integrate`. | user |
| U4 | omc does not interpret single test failures; `/omc:investigate local` and `investigation-context` do. | user |
| U5 | Only a verify run is held, and only when it failed, so that `/omc:investigate local` can debug the live system. | user |
| U6 | A UUID in `.omc/project.id`; each worktree has `.cache/worktree.id`. One stage process per worktree; a new one kills the old one. A per-project limit comes later. | user |
| U7 | The instructions carry what the project's stage skills say today, adapted: when the run is done, how to tell success, how to drive the fix loop. | user |
| U8 | omc defines the polling loop exactly; the agent does not build it. | user |
| U9 | `project.id` and `worktree.id` are created by whichever omc command reads them first, not by integrate. | user |
| U10 | `--show-instructions` is static or a thin script: no network, no expensive logic. Warning over 1 s, CRITICAL STOP at 10 s. Over 20 KB in total is a CRITICAL STOP; about 5 KB per section. | user |
| U11 | The instruction template is placed in `.omc/bin/`. | user |
| U12 | omc's poll periods are fixed. omc sets no overall time limit; a timeout and a no-log-progress timeout are part of the instructions. | user |
| U13 | `.cache/.gitignore` containing `*`; the run ends by exit or by result file; a stage may set its own fix-cycle bound, at most 10 (where it is written: A3; since the rebase onto main it only lowers omc's default; amended at audit after rebase onto main); an unmigrated repo is red, not unconfigured; `watch --auto-build` runs `.omc/bin/build` with no LLM; this repo migrates its own stages; the daemon invariant is amended. | user ("rest ok") |
| U14 | Stage hooks are extensionless executables `.omc/bin/<stage>`, run directly so their shebang decides the interpreter ("…as this allows us to replace them more neatly with whatever the project wants…"). | user (at audit) |
| A1 | A stage run refuses to start when no `omc watch` is running for its project ID, and ends itself when that watch stops ("they will refuse to boot if `omc watch` isn't running for the same project.id and they will automatically all terminate if `omc watch` for that project.id terminates"; "the processes just check if `omc watch` is running for that project.id"). It also ends when its worktree is closed. Watch does nothing new beyond holding a passive lock that runs probe (the fallback of section 8; amended at audit). A held verify has no maximum lifetime. | user |
| A2 | A CRITICAL stop, or any question, never kills a held verify. | user |
| A3 | The fix-cycle bound is a third line under `## Limits`, `Fix cycles: <n>`, which omc validates (1 to 10; `none` or absent is omc's default, now main's ten-with-progress bound, so a stage's `Fix cycles` only lowers it; amended at audit after rebase onto main). | user |
| A4 | The template file is refreshed by every stage command in a linked worktree, "because most people will never run integrate again"; never in the primary checkout, where a rewritten tracked file would stop `omc watch`'s sync (amended at audit, user-confirmed). | user |
| A5 | Neither `/omc:finish` nor `/omc:audit` kills a run. "The respective /build, /check, /verify skills control the lifecycle." | user |
| C1 | `project.id` is read and created at the primary checkout only (`wtconfig.primary_root`). A worktree's copy is never read. Otherwise worktrees cut from a main without an ID would each mint their own. | orchestrator call |
| C2 | omc ships the template and writes it to `.omc/bin/INSTRUCTIONS.template.md` when it is missing or outdated, in a linked worktree (A4). The conventional hook prints a static `.omc/bin/<stage>.instructions.md` for `--show-instructions`; this repo's hooks do that. | orchestrator call |
| C3 | The validated instructions are saved beside the log and `start` returns their path; the agent reads the file. A single JSON line stays short, and the path in every answer is how the instructions are found again after a compaction. | design call (replaces the brainstorm's `wait --instructions`) |
| C4 | A broken stage definition (template violation, slow or oversized instructions, hook not executable, unmigrated repo) is reported as `OMC_STAGE` with `"passed": false` and a summary starting `CRITICAL STOP:`. The stage-gate rule sends such a verdict to the user at once, without a fix cycle. | design call (follows from U2 and the stage-definition guard) |

## 6. Design

### 6.1 Identity

Two IDs, each created by the first omc command that needs it and returned as
it is afterwards (U9).

- **Project.** `<primary>/.omc/project.id`, one UUID on one line. Always the
  primary checkout's file (C1). It is meant to be committed, so that every
  clone agrees on it; omc creates it and does not commit it by itself. Until
  it is committed it is one ID per machine, which is all the planned
  per-project limit needs. No omc command refuses over the untracked file
  (`omc watch` reads `git status -uno`); a `finish` run in the primary
  checkout folds it into the commit, as its squash does with any untracked
  file. A missing file adopts the ID already committed upstream when there is
  one. When another clone commits its own ID after this one was made, git
  refuses to sync over the untracked file: `omc watch` then removes it, the
  committed ID wins, and the sync (ff-merge or `--rebase`) is retried once.
- **Worktree.** `<worktree>/.cache/worktree.id`, JSON
  `{"id": "<uuid>", "path": "<absolute worktree path>"}`. When `path` is not
  this worktree's path the file was copied in by worktrunk, and omc replaces
  it with a new ID. The primary checkout is a worktree in this sense and has
  its own.
- omc writes `.cache/.gitignore` containing `*` whenever it is missing,
  including in a `.cache/` that existed before, so nothing under it shows up
  in `git status`.

Both IDs are used now: they name the log directory (6.4), they are passed to
the hook, the project ID is what a run asks "is a watch running for" (6.4),
and the worktree ID is what marks a copied status file as foreign.
Resolving them is one function with one caller today, the stage runner.

### 6.2 The hook

`.omc/bin/<stage>`, for stage `check`, `build` or `verify`. The name is
fixed and has no extension; the file may be any executable (U14). omc runs it
directly by its path, so the kernel picks the interpreter from its first
line: a shebang (`#!/bin/sh`, `#!/usr/bin/env python3`, …), or the file is a
binary. A hook that cannot start (no shebang, a missing interpreter) is the
CRITICAL STOP `could not start: <strerror>` (6.3).

| State of the repo | Outcome |
|---|---|
| hook present and executable | the stage exists |
| no hook, no `.omc/skills/<stage>/SKILL.md` | the stage does not exist: `OMC_STAGE` with `"configured": false, "passed": true`, as today |
| no hook, but `.omc/skills/<stage>/SKILL.md` exists | unmigrated: red, `CRITICAL STOP: migrate this stage with /omc:integrate` |
| hook present, not executable | broken: red, `CRITICAL STOP:` naming the file |
| no hook, but `.omc/bin/<stage>.sh` exists (with or without the old skill) | unmigrated: red, `CRITICAL STOP: rename .omc/bin/<stage>.sh to .omc/bin/<stage> (stage hooks have no extension)`; the `.sh` file is never run (U14) |

**Two ways to call it.**

- `<hook> --show-instructions`, with exactly that one argument: print the
  instructions (6.3) on stdout and exit 0. Static text or a thin script. No
  network, no build, no test discovery (U10).
- `<hook> [args…]`: run the stage. Working directory is the worktree root.
  omc sends stdout and stderr to the log. The environment adds:

  | Variable | Value |
  |---|---|
  | `OMC_STAGE_NAME` | `check`, `build` or `verify` |
  | `OMC_PROJECT_ID`, `OMC_WORKTREE_ID` | the two IDs |
  | `OMC_STAGE_LOG` | the log file omc writes the hook's output to |
  | `OMC_STAGE_DIR` | a directory beside the log for anything else the hook wants to keep |
  | `OMC_STAGE_RESULT` | a file path, absent at the start |

  Everything else in the environment is what the process that called `start`
  had (the agent's shell, or `omc watch`), as with a command the agent runs
  itself today.

**When the run is done and what its verdict is.** This is the whole contract:

1. The hook exits. Exit code 0 is passed, anything else is failed.
2. The hook writes the single word `passed` or `failed` to
   `$OMC_STAGE_RESULT`. The run is done with that verdict, whether or not the
   hook is still alive. omc then kills the hook's process group at once, with
   one exception: the stage is `verify` and the verdict is `failed`. That run
   is **held**: omc leaves it running until it is killed (U5), so that
   whatever it keeps up can be inspected.

A hook that only ever exits needs nothing but its exit code. The result file
exists for a hook that wants to keep a system up after a failure. When the
result file exists it decides, also over the exit code of a hook that wrote
it and then exited; it is read once. A result file that is empty while the
hook is alive is no verdict yet (`echo failed > f` creates the file before it
writes), so the hook writes the word in one write. A file still empty after
the hook exited, or one holding anything other than the two words, is
`failed`, and the reason quotes it. A verify hook that wrote `failed` and
then exited has nothing left to hold: the run is `failed`.

**Stage hooks and project hooks.** `.omc/bin/<stage>` stage hooks and
#83's `.omc/hooks/<name>/SKILL.md` project hooks coexist and are different
things: the first are the stages, the second answer one yes/no question each;
the layer's fix guard names both (`.omc/bin/<stage>` and its instructions,
`.omc/skills/review/`, `.omc/hooks/<name>/`). (Added after rebase onto main.)

### 6.3 The instructions

Markdown with exactly these second-level headings, in this order, nothing
before the first one and no other second-level heading. Each section is at
most 5 KB, the whole output at most 20 KB.

```markdown
## Limits
Timeout: 45m
Quiet timeout: 5m
Fix cycles: 4

## Done when
## Passed when
## Fixing failures
## Rerunning a subset
```

| Section | Reader | Content |
|---|---|---|
| `## Limits` | omc | `Timeout: <duration or none>` and `Quiet timeout: <duration or none>`, both required, durations such as `90s`, `45m`, `2h`; then optionally `Fix cycles: <1 to 10, or none>`. Nothing else. The only section omc reads as data. |
| `## Done when` | the agent | In the project's terms, what makes the run end (the hook exits; or it writes the result file), and, for a verify that can be held, what the held system offers. |
| `## Passed when` | the agent | In the project's terms, what a pass means and what it covers. |
| `## Fixing failures` | the agent that drives the fix loop | How to read the log, and what the fix agent must never fix (flaky infrastructure, the stage itself). |
| `## Rerunning a subset` | the agent | Optional, the only heading that may be missing. The arguments that rerun only named pieces. Replaces the verify skill's "Running a subset". |

The instructions describe the mechanical contract of 6.2; they cannot change
it. The verdict always comes from the exit code or the result file.

**Limits are the project's, enforced by omc (U12).** Over `Timeout` the run is
killed and is red: `timed out after 45m`. With no growth of the log for
`Quiet timeout` the run is killed and is red:
`no log progress for 5m at: <last log line>`. `none` switches a limit off.
omc has no limit of its own. A held run is done, so neither limit applies
to it, and a hold has no maximum lifetime (A1).

`Fix cycles` is how many fix-and-rerun cycles the stage-gate rule may spend
while this stage is red (6.6). Absent or `none` keeps omc's default, the
layer's up to ten cycles per conductor run, each needing progress or a new
idea (A3). A stage's value, at most 10, only lowers it for that stage.

**Validation, at every `start`.** omc runs `--show-instructions` and checks
the result before it runs anything:

| Finding | Outcome |
|---|---|
| answered in over 1 s | a warning in the answer; the run goes on |
| not answered in 10 s | killed; CRITICAL STOP |
| non-zero exit, or empty output | CRITICAL STOP |
| over 20 KB, or a section over 5 KB | CRITICAL STOP naming the size |
| text before the first heading, an unknown or repeated heading, wrong order, a required heading missing | CRITICAL STOP naming the heading |
| `## Limits` with a required line missing, an unknown line, a duration that does not parse, or `Fix cycles` outside 1 to 10 | CRITICAL STOP naming the line |

A CRITICAL STOP names the excess and goes to the user (C4): the fix loop may
not edit the stage's own definition, and an agent that "repairs" the
instructions to get past the check is exactly the brittle flow U2 rules out.

**The template file (U11, C2).** omc carries the template, with one paragraph
under each heading saying what belongs there. Under `## Limits` the example
lines come first and the paragraph starts `Delete this paragraph:`.
`## Limits` is data, so the template copied verbatim is refused at that
paragraph, and `/omc:integrate` says to delete it. The `## Done when`
paragraph says the hook writes the result word in one write.
`omc internal stage template` prints the template (the precedent is
`omc internal wt-template`). A stage command (`start`, `wait`, `kill`) writes
it to `.omc/bin/INSTRUCTIONS.template.md` when `.omc/bin/` exists and the
file is missing or differs, in a linked worktree only. The primary checkout
is never rewritten: `watch --auto-build` runs there, and a modified tracked
file would make watch's tree dirty and stop its sync. The primary's copy
changes when a worktree's refresh reaches main. Validation uses omc's own
copy, never the file. The conventional hook keeps its text in
`.omc/bin/<stage>.instructions.md` and prints it:

```sh
#!/bin/sh
if [ "$#" -eq 1 ] && [ "$1" = "--show-instructions" ]; then
    exec cat "$(dirname "$0")/check.instructions.md"
fi
exec just check "$@"
```

### 6.4 The runner: `omc internal stage`

Three commands for the agent and one for the author. Each of the three prints
exactly one line, `OMC_STAGE_RUN {…}`, a new machine contract, whenever it
answers. An error (exit 1) prints `error: …` on stderr and no `OMC_STAGE_RUN`
line, as house errors do. `OMC_STAGE_RUN` joins the contract lists in
`.omc/config/AGENTS.md`, `src/omc/distribution/AGENTS.md` and
`.omc/skills/explain-context/SKILL.md`. With it, beside #83's `OMC_GATE` and
`OMC_HOOK`, the layer names ten sacred contracts; the README guard derives
that count from the layer's list instead of pinning a number (amended after
rebase onto main).

| Command | Does |
|---|---|
| `start <stage> [-- args…]` | Resolves the IDs. Checks the hook (6.2) and the instructions (6.3). Checks that an `omc watch` is running for this project ID (A1). Kills this worktree's live run, whatever its stage (U6). Removes stage logs older than 7 days, every project's under the per-user stage root (an ID watch gave up for a committed one starts no runs that would prune its own). Spawns the supervisor and returns once the supervisor holds the lock and has written the status file. |
| `wait <stage>` | Blocks until the run is no longer `running`, or for 540 s, whichever is first. The run is the one it found first: when a newer `start` replaces it during the wait, `wait` answers that run's final record at once (`killed`, `superseded`), never the newer run's progress. |
| `kill <stage>` | Ends this worktree's run of that stage: SIGTERM to the hook's process group, 30 s grace, SIGKILL. Records `killed`. With no live run of that stage (the live run is of another stage, or there is none) it answers the last recorded state of that stage and changes nothing; with no record of that stage at all it is an error (exit 1). |
| `template` | Prints the instruction template. |

`OMC_STAGE_RUN` fields: `stage`, `state`, `run` (an ID for this run), `log`,
`instructions` (path of the validated instructions, saved beside the log,
C3), `elapsed` and `quiet` in seconds, `tail` (the log lines new since the
last `wait`, the last 40 of them at most), `reason` (one sentence for every
state that is not `running`, `passed` or `unconfigured`), `fix_cycles` (the
stage's own validated bound; 0 when there is no run, as in a `refused` or
`unconfigured` answer, or when the instructions give none), `warnings`.

| `state` | Meaning |
|---|---|
| `running` | the hook is alive and no verdict exists yet |
| `passed` | exit 0, or `passed` in the result file |
| `failed` | non-zero exit, or `failed` in the result file of a `check` or `build` run |
| `held` | `failed` in the result file of a `verify` run; the hook is still up |
| `timed-out` | killed at `Timeout` |
| `quiet-timed-out` | killed at `Quiet timeout` |
| `killed` | ended before it had a verdict or while held; `reason` says by what: `kill`, `superseded` (a newer `start`), `watch-gone`, `worktree-gone` |
| `died` | the status file says `running` or `held` and nobody holds the lock: the supervisor was killed from outside or the machine restarted |
| `refused` | `start` only: the stage definition is broken (6.2, 6.3), or no `omc watch` is running for this project; `reason` starts with `CRITICAL STOP:` |
| `unconfigured` | `start` only: the stage does not exist |

Exit codes follow the house rule: 0 when the command answered, whatever the
state; 2 with `refused`; 1, with no `OMC_STAGE_RUN` line, for an error such
as `wait` or `kill` with no run of that stage in this worktree; a usage error
is 2 with no line.

**The supervisor.** A second omc process, started by a new
`ToolContext.spawn_detached` (argv only, its own session, no inherited
terminal). It is bound to one hook run:

- It takes a flock on `.cache/omc/stage.lock` and holds it until it exits.
  The kernel drops a flock with its process, so "is the run alive" is "is the
  lock held"; a recycled PID can never be mistaken for the run. This is the
  mechanism `src/omc/watchlock.py` already uses for `omc watch`.
- It runs the hook in a process group of its own, with stdout and stderr
  appended to
  `/tmp/omc-stages-<uid>/<project-id>/<worktree-id>/<stage>-<time>-<run>.log`
  (per user, since `/tmp` is shared; `OMC_STAGE_ROOT` overrides the root).
  The run's `.d` stage directory, `.instructions.md` and `.result` path sit
  beside it under the same name. The hook's process is started through
  `ToolContext` like every other.
- About once a second it looks at three things: has the hook exited, does the
  result file exist, has the log grown. From these it applies 6.2 and the
  limits of 6.3.
- In the same pass it checks two more things, for as long as it lives, a hold
  included (A1). Is an `omc watch` still running for its project ID? Does its
  worktree's path still exist? When either answer is no, it ends its own run
  the way `kill` does and records `killed` with the reason `watch-gone` or
  `worktree-gone`. The second covers `wt remove`, omc's own closing of a
  worktree and a plain `rm`; the status file is gone with the worktree then,
  and the log in `/tmp` stays.
- It is the only writer of `.cache/omc/run.json`, rewritten atomically
  (write a temporary file, rename): PID, process group, stage, start time,
  `HEAD` and whether the tree was dirty at the start, state, exit code,
  verdict, worktree ID. A status file whose worktree ID is not this
  worktree's was copied in with `.cache/` and is treated as no run at all.
- It ends when the hook ends or is killed. For a held verify it stays, doing
  nothing but waiting for the kill. Nothing restarts it and nothing schedules
  it.

`kill` signals the supervisor, which ends the hook's group and records
`killed`. When the supervisor is already gone (`died`), `kill` and `start`
end what is left of the recorded process group themselves, but only while
its leader is still the process the supervisor started. On Linux that means
its `/proc` start time equals the recorded `leader_start`; without `/proc`,
the recorded boot time must match this boot. A group whose leader has
exited, or whose number may since name another group, is left alone. No
module outside
`ToolContext` sends a signal today; the two new ones (to the supervisor, to a
process group) are `ToolContext` methods as well, so the boundary stays where
it is.

Two starts at the same moment: both kill the old run, both spawn, one
supervisor gets the lock. The other exits and its `start` answers with an
error (exit 1) naming the run that won.

**Watch is a precondition, not a host (A1).** `omc watch` gains no duty: it
does not start, own, signal or end a run, and it does not know that runs
exist. Its one addition is passive. Right after `acquire_instance` it takes a
second lock, `omc-watch-alive.lock`, exclusive and blocking, beside the
instance lock in the shared git directory, and holds it until it exits
(`--once`: for one tick, auto-build included; `--clear-mutex` unlinks it as
it does the instance lock). The run is the only actor. `start` refuses with
`CRITICAL STOP: no \`omc watch\` running for this project`, and the agent
never starts watch itself: watch is a foreground process in the user's
terminal in the primary checkout, and starting it is the user's act. The
supervisor repeats the same check and ends itself. A run tells that a watch
is running by taking that lock shared and non-blocking: refused means a watch
holds it. The instance lock cannot serve, because watch takes it non-blocking
and a probe would make a starting watch bail. The lock lives in the primary
checkout's git directory, so "a watch for this project ID" means a watch in
the primary checkout that holds the ID's file (C1).

**The invariant, amended.** `.omc/config/AGENTS.md` today: "Long-running
behavior is foreground-only: omc never creates daemons, LaunchAgents, or cron
entries." It gains one sentence: "The one exception is a stage run: a
detached supervisor bound to one hook run; it ends when the hook ends or is
killed, is never restarted or scheduled, and never outlives a running
`omc watch` for its project, or its worktree."

**`wait` is a poll period, not a limit.** 540 s keeps each `wait` command
inside the 600 s a shell command may take. A run that is still going after
540 s answers `running` and the agent calls `wait` again. Nothing counts the
calls.

### 6.5 The loop

Written once and used word for word by `skills/check`, `skills/build` and
`skills/verify` (U8). A skill cannot include another file, and a proxy has to
work in a session whose global layer is missing, so the text is in all three;
a unit test holds the three copies identical apart from the stage name. The
proxies stop reading `.omc/skills/<stage>/`.

1. `omc internal stage start <stage>` (for a subset run,
   `start <stage> -- <args>` with the arguments the "Rerunning a subset"
   section gives for the named pieces; the verify proxy reads that section
   from `.omc/bin/verify --show-instructions` before `start`, since no run
   exists yet).
   - `unconfigured` → `OMC_STAGE` with `"configured": false, "passed": true`.
   - `refused` → `OMC_STAGE` with `"passed": false` and the `reason` as the
     summary (a broken stage definition, or no `omc watch` running). End.
   - any other state → say one line naming the run
     (`started <stage> run <run>`), read the file at `instructions` once, and
     go to 2 (`wait` answers a finished run at once).
   - No `OMC_STAGE_RUN` line (any exit, in step 1 or 2) → `OMC_STAGE` with
     `"passed": false` and the `error:` or usage line as the summary. End.
2. `omc internal stage wait <stage>`, with the shell command's timeout set to
   600 s. By `state`:
   - `running` → say one progress line (elapsed time and the last line of
     `tail`) and repeat step 2. Nothing else: no sleep, no kill, no judgment
     about how long it has been.
   - `passed` → `OMC_STAGE` with `"passed": true`.
   - `failed`, `held` → `OMC_STAGE` with `"passed": false`; the summary says
     what failed as far as `tail` and "Fixing failures" tell, gives the `log`
     and `instructions` paths, and says `held` when the run is held.
   - `timed-out`, `quiet-timed-out`, `killed` (with its reason, `watch-gone`
     and `worktree-gone` included), `died` → `OMC_STAGE` with
     `"passed": false`; the summary is the `reason` and the `log` and
     `instructions` paths. For
     `watch-gone` the summary starts with `CRITICAL STOP:`, like the refusal
     at `start`: no code fix brings watch back, so no fix cycle is spent on
     it (6.6).

Every `"passed": false` summary built from an answer says `fix cycles: <n>`
whenever `fix_cycles` is not 0 (0: no run, or the stage sets no `Fix cycles`
of its own), whatever the state: a stage's own bound, `Fix cycles: 1` and
`Fix cycles: 10` alike, has to reach the stage-gate rule (A3).

Every answer carries `run`, and the proxy compares it before it reads
`state`. A `wait` answer whose `run` is not the one
`start` returned means another session started a stage in this worktree and
ended this one: the verdict is `"passed": false`, "superseded by another
run", and the other run's outcome is never reported as this one's.

After a compaction the agent calls `wait` again; every answer carries the
state and the `instructions` path.

**The proxies own the lifecycle of a run (A5).** A run is ended by the next
`start` in its worktree, which every proxy issues, and a held verify also by
`/omc:verify kill`, a new input of the verify proxy that calls
`omc internal stage kill verify` and reports the answer. No conductor calls
`kill`.

`/omc:verify rerun only: <pieces>` keeps its meaning. Without a "Rerunning a
subset" section the proxy runs the full stage and says so, as it does today
without "Running a subset".

Codex runs the same two commands. Its shell timeout has to cover 600 s; one
Codex stage run that outlasts a single `wait` proves it
(`tests/e2e/test_e2e_stage_poll.py`). That test runs for over 540 s, above
the 300 s ceiling of every gated E2E test. It therefore lives in the
`expensive` tier with its own timeout and recipe, `just codex-stage-poll`: an
evidence run outside every gate, not part of `just codex-gate`.

### 6.6 The fix loop

The stage-gate rule in `src/omc/distribution/AGENTS.md` keeps its shape:
bounded fix-forward, one shared counter, the verify clause, the CRITICAL
question at the bound. Four changes.

- **The brief.** The fix subagent gets the stage's summary, the log path and
  the stage's "Fixing failures" section. For a held verify the brief adds
  that the system is up and that `/omc:investigate local` inspects it. The
  hold lasts as long as the fix agent starts no stage: the `/omc:build` that
  follows the fix (the stage-gate order is now fix → build → check) is a
  `start` and ends it. The brief says so, so that the agent looks first and
  builds afterwards.
- **The guard names the new files.** "Never the stage's own definition" now
  means `.omc/bin/<stage>`, its instructions and whatever script or
  command it runs.
- **CRITICAL STOP verdicts (C4).** A verdict whose summary starts with
  `CRITICAL STOP:` is the CRITICAL question at once. No fix is dispatched
  and no cycle is spent.
- **The bound (A3).** omc's default is the layer's: up to ten cycles per
  conductor run, each needing progress or a new idea. While a stage is red
  whose `## Limits` give `Fix cycles: <n>`, that lower bound applies; the red
  verdict's summary carries it. The counter stays one counter per conductor
  run. Example: check gives 3 and verify gives nothing. Three fixes went into
  a red verify; check then turns red. The counter is at 3, check's bound is
  3, so that is the CRITICAL question.

**What ends a held verify, and what does not.** No conductor kills a run:
not `/omc:finish`, not `/omc:audit` (A5). A CRITICAL question, or any other
question to the user, leaves a held verify up (A2); the user may want to look
at exactly that system before answering. A hold ends in four ways: the next
`start` in the worktree, `/omc:verify kill`, the project's `omc watch`
stopping, the worktree being closed. It has no maximum lifetime (A1).

The verify clause's "Running a subset" wording becomes "Rerunning a subset"
in the stage's instructions; this design changes nothing else in it. After
the rebase onto main the clause follows #83: after a fix, `/omc:build` →
`/omc:check` → the targeted rerun, and a green targeted rerun goes straight
to the full verify; the "`/omc:build` once" step is gone.

### 6.7 Migration and the other consumers

- **`/omc:integrate`.** The inventory lists `.omc/bin/{check,build,verify}`
  and their instructions in place of the three stage skills. Phase 2 designs
  each stage as a hook plus an instructions file, from the template. For a
  repo that still has `.omc/skills/<stage>/SKILL.md`, it converts that skill
  (commands into the hook, the prose into the four sections) and deletes the
  old skill once the user approves the pair. Its wrap-up says that stages
  need a running `omc watch`. In interactive mode only, after the user
  approves a pair, it validates the pair. For check and build it runs a real
  `start` and `wait`, once the user confirms that `omc watch` runs. For verify
  it checks only the `--show-instructions` output against the template,
  because a `start` would launch the full run, which it offers to the user
  instead. Its existing trigger for repos
  from before the check/build split (a build stage that runs unit tests and
  no check stage) stays, reading hooks and old skills alike.
- **`omc watch --auto-build`** (`src/omc/watch.py`, `_auto_build`). It asks
  an LLM to run the build stage today for one reason: the stage is prose and
  only an agent can carry it out. With a hook that reason is gone. Watch
  calls the same start, wait and kill operations in its own process, shows
  the log as it grows, and kills the run on Ctrl-C. Its progress bar and the
  cargo progress variables it sets today stay: the bar reads the log, and
  the hook inherits watch's environment (6.2). No LLM call, no model
  choice, no `OMC_STAGE` parsing (`_STAGE_RE` and `_parse_stage` go).
  Watch's per-tick model reconcile goes with it: the Orchestrator group of
  `--auto-build` was its last user, so watch reconciles no model. No hook:
  one line, skipped, as today. Watch runs in the primary checkout,
  which is a worktree like any other: its build and an agent's stage run
  there end each other (U6). The precondition of A1 holds by construction:
  watch is the caller, and it waits for its build before it exits, `--once`
  included.
- **This repo.** `.omc/bin/check` wraps `just check`, `build` wraps
  `just build`, `verify` wraps `just e2e-tests` and keeps the rule that
  the Codex gate also runs when Codex code changed. Each has an
  `.instructions.md` carrying what its stage skill says today. None of them
  holds anything; they end by exit code. `.omc/bin/verify.instructions.md`
  no longer carries "Changes limited to documentation do not require
  repeating successful model calls; cite the last green run.":
  `.omc/hooks/needs_verify` makes that call now (#83 D11; amended after
  rebase onto main). `.omc/skills/{check,build,verify}` stay until the
  installed omc reads hooks, and are removed in a follow-up. This branch's
  own gates run through the installed proxies, which read them, and a
  present hook wins over them (6.2), so they are inert under this design.
  `.omc/skills/review` and `explain-context` stay.
  `.omc/config/AGENTS.md` and `explain-context` are updated; `README.md`
  follows `/regenerate-readme`'s recipe, which gains a stage-hook slot; the
  affected sections are updated in place to that recipe.
- **Other skill text.** Of the conductors only `skills/finish` names the
  stage skills by path; that wording follows. No conductor gains a kill
  (A5). `implement`, `rebase-main` and `review` call the proxies
  by name and need no change. No order of stages changes: #83's sequence
  stands (audit runs build → check → review → verify; finish runs
  build → check, skipped on a verified tree through
  `omc internal gate verify`, and the MR gate), and finish names the stage
  hooks as `.omc/bin/<stage>` (amended after rebase onto main).

### 6.8 Unchanged

- The `OMC_STAGE` line and everything that parses it in skills and tests.
- The order of stages as #83 set it (audit: build → check → review →
  verify; finish: build → check and the MR gate), the tree record
  (`omc internal gate record`) and the skip rule for a verified tree
  (`omc internal gate verify`) (amended after rebase onto main).
- `.omc/skills/review`, `investigation-context`, `explain-context`.
- `omc watch`'s instance and busy locks and its tick, apart from the alive
  lock it now holds (6.4) and the model reconcile that leaves with
  auto-build's model choice (6.7). A run only asks whether watch is running.
  The stage lock is per worktree and lives in that worktree's
  `.cache/`, not in the shared git directory.

## 7. Tests (red first, per `.omc/config/AGENTS.md`)

Unit tests run the real runner against real, tiny hook scripts in a temporary
git repository. No stub stands in for the supervisor. The hook scripts follow
the house rule for scripts on a restricted PATH: shell builtins and absolute
paths (`/bin/sleep`). "A watch is running" is a helper process that holds the
watch lock, as `tests/unit/_mutexproc.py` does for the lock tests today; the
real `omc watch` needs GitNexus and a provider CLI and is driven in E2E.

- No watch running → `start` answers `refused` with the CRITICAL STOP
  sentence, and no hook process was started.
- The helper releases the watch lock during a run → `killed`, reason
  `watch-gone`, process group gone. The same for a held verify.
- The worktree directory is removed during a run → the hook's process group
  is gone and the supervisor has exited.
- `Fix cycles: 4` → `fix_cycles` is 4 (`Limits.fix_cycles` 4). Absent or
  `none` → `Limits.fix_cycles` is `None` and the answer's `fix_cycles` is 0.
  `Fix cycles: 11` or `0` → `refused` naming the line.

- Only `.omc/bin/<stage>.sh`, alone or beside the old skill → unmigrated,
  refused with the rename sentence; the `.sh` file is never run (U14).
- A hook that exits 0 → `passed`. A hook that exits 1 → `failed`.
- A verify hook that writes `failed` and sleeps → `held`, still alive; `kill`
  → `killed`, process group gone.
- A check hook that writes `failed` and sleeps → `failed`, killed at once. A
  verify hook that writes `passed` and sleeps → `passed`, killed at once.
- A silent hook with `Quiet timeout: 2s` → `quiet-timed-out`, the reason
  quoting the last log line. A chatty hook with `Timeout: 2s` → `timed-out`.
- Instructions with an extra heading, a missing heading, text before the
  first heading, an unknown line under `## Limits`, a section over 5 KB, a total
  over 20 KB → `refused`, each naming its excess. Instructions that take over
  10 s → `refused`; over 1 s → a warning and a run.
- A second `start` while a run is alive → the first is `killed`, the second
  runs, and a `wait` answer carries the second run's `run`.
- A hook that writes `failed` and exits 0 → `failed`: the result file wins.
- A hook sees a variable set in the environment of the `start` call.
- A `.cache/` copied from another worktree: `worktree.id` is regenerated and
  the copied `run.json` is not answered from.
- `project.id` from a worktree lands in the primary checkout.
- A supervisor killed with SIGKILL → `wait` answers `died`.
- No hook → `unconfigured`. Old stage skill without a hook → `refused` with
  the migrate sentence. Hook without the executable bit → `refused`.
- `watch --auto-build` runs a real `.omc/bin/build` and reports its exit code; no
  provider is asked for a model.
- Text assertions in `tests/unit/test_plugin_manifests.py`: the three proxies
  share the loop and no longer read `.omc/skills/<stage>`; the layer names
  `OMC_STAGE_RUN`, the CRITICAL STOP sentence, the bound from `Fix cycles`;
  finish and audit contain no `stage kill`; the verify proxy has the `kill`
  input; integrate names the hooks and the watch prerequisite.

E2E: every fixture that creates a stage skill today creates a hook and its
instructions instead (`tests/e2e/test_e2e_finish.py`,
`test_e2e_lifecycle_full.py`, `lifecycle_helpers.py`, `test_e2e_watch.py`,
`test_e2e_integrate.py`). Every such test now also needs a watch running for
the primary checkout in its container. A real `omc watch` boots GitNexus and
fetches on its first tick, in every container, under a 300 s ceiling per
test. So the golden lifecycle path and the new held-verify test run the real
`omc watch`, started by the harness before the agent and stopped afterwards;
the other containers run the lock-holding helper the unit tests use.
Variations forked from a golden snapshot run the helper too, because the
golden path's real watch does not survive the snapshot. A golden snapshot is
refused while any `run.json` of the repo or its worktrees says `running` or
`held`: variations forked from it would record a process group the image
does not have. One new E2E drives a held verify
through a real agent: the stage reports red and held, the hook is still
alive after the agent has stopped, and it is gone once that `omc watch` is
stopped. The `expensive` tier gains the Codex stage run that outlasts one
`wait` (6.5), run by `just codex-stage-poll` and part of no gate.

After the rebase onto main, `tests/e2e/harness.add_stage` writes a stage
hook (hook, instructions, watch stand-in) rather than a stage skill; #83's
`tests/e2e/test_e2e_hooks.py` drives its verify and check stages through it,
and the lifecycle fixture also ships this repo's `.omc/hooks/needs_verify`
hook verbatim.

## 8. Risks and what is left to the builder

**Risks.**

- **Stages depend on a process that has another job (A1).** `omc watch`
  keeps the knowledge graph fresh; a stage has no use for the graph. A user
  who never ran watch now has red stages until it runs, and watch has
  prerequisites of its own (GitNexus, `wt`, a provider CLI). The refusal
  names the command. Every E2E test with a configured stage needs a watch or
  its stand-in (section 7).
- **A held run outlives its session**, but no longer its watch or its
  worktree. Until one of those ends it holds whatever the hook keeps up.
- **Stopping watch ends every run of the project**, a long verify included.
  That is the decision (A1); the `reason` says `watch-gone`, so the red is
  not mistaken for a failure of the code.
- **A supervisor killed from outside leaves the hook's group running.** The
  next `start` or `kill` ends the recorded group. A process group number from
  before a restart of the machine may belong to anyone; the builder guards
  that case (apuntalo compares the run's start with the boot time).
- **The template file is tracked and omc rewrites it.** After an `omc update`
  that changes the template, the next stage run in a worktree changes a
  tracked file, and `finish` amends it into the feature commit. It is a
  one-file, documentation-only change, and it happens once per template
  version. The primary checkout is never rewritten (6.3), so a repo whose
  stages only ever run in the primary keeps an old template until a worktree
  runs a stage.
- **Every existing repo is red until it migrates** (U3, U13). The message
  names `/omc:integrate`.
- **A host that cleans `/tmp`** can remove a log before omc's 7-day prune
  does.
- **Layer and skills install separately.** A session with the new proxies and
  an old layer treats a `CRITICAL STOP:` verdict as an ordinary red stage and
  spends fix cycles on it; the stage-definition guard still stops it from
  editing the hook.

**Left to the builder.**

- How a process tells that an `omc watch` is running for a project ID. The
  instance lock of `src/omc/watchlock.py` is in the primary's shared git
  directory, which the project ID's primary checkout leads to. Three facts
  about it: an advisory lock is probed by taking and releasing it, and watch
  takes it without waiting (`acquire_instance`), so a probe must not make a
  starting watch bail; `--clear-mutex` unlinks the lock file while an older
  watch may still hold the old one; `omc watch --once` holds it only for one
  tick. If the probe cannot be made safe on that lock, a second lock file
  that watch holds beside it is the smallest change to watch.

- The fields of `run.json` beyond those named, and where `wait` keeps its
  position in the log (not in `run.json`, which has one writer).
- How "the log lines new since the last `wait`" are counted and cut to 40.
- How the 7-day prune finds a project's log directories.
- The signature of `ToolContext.spawn_detached`, of the call that runs the
  hook with its output in a file, and of the two signal calls. How omc starts
  itself as the supervisor (`sys.executable -m omc …`, as
  `src/omc/terminal_title.py` does).
- Where the Codex test of 6.5 lives, given the 300 s ceiling (the
  `expensive` tier, or a ceiling of its own).
- Whether the 10 s cap is a parameter, so that its unit test does not take
  10 s of the fast gate.
- How `watch --auto-build` shows progress from the log (its progress bar
  reads lines today).
- This repo's limits. Measure `just check`, `just build` and `just e2e-tests`
  and set `Timeout` and `Quiet timeout` from the measurements; `none` until
  measured.
- How `.omc/bin/verify` decides that Codex code changed, and how a subset argument
  selects a Codex-gate piece.

## Implementation review

Auditor: claude (opus), 2026-11-16.

1. Important; record is stale; §6.3, §8, §5 A4; `src/omc/stagerun.py:196-202`
   (`_refresh_template`, primary checkout skipped); record amended (R1); user
   accepted narrowing of A4.
2. Important; record is stale; §3, §6.4, §6.7, §6.8, §5 A1;
   `src/omc/watchlock.py:78-117` (alive lock), `src/omc/watch.py` `run_watch`;
   record amended (R2).
3. Important; record is stale; §6.7 "This repo"; `src/omc/stagedef.py:74-81`
   (hook wins over the kept stage skills); record amended (R3).
4. Important; record is stale; §6.5, §7; `tests/e2e/test_e2e_stage_poll.py:35`
   (`expensive`), `justfile:56` (`codex-stage-poll`); record amended (R4).
5. Minor; record is stale; §6.4 supervisor; `src/omc/stagerun.py:62`;
   record amended (R5).
6. Minor; record is stale; §6.4 died recovery; `src/omc/stagerun.py:332-354`;
   record amended (R6).
7. Minor; record is stale; §6.4 `wait` row; `src/omc/stagerun.py:504-541`;
   record amended (R7).
8. Minor; record is stale; §6.4 intro, `kill` row, exit codes;
   `src/omc/internal.py:494-529`, `src/omc/stagerun.py:476-480`; record
   amended (R8).
9. Minor; code deviates from the record (trivial); §6.4 fields;
   `src/omc/stagerun.py:125-127` (`_blank`); record amended (R9).
10. Minor; record is stale; §6.3 "The template file";
    `src/omc/stagedef.py:234-262`; record amended (R10).
11. Minor; record is stale; §6.2 contract; `src/omc/stagerun.py:807-822`;
    record amended (R11).
12. Minor; record is stale; §6.5 steps 1-2; `skills/verify/SKILL.md:41-82`
    (and `skills/{check,build}/SKILL.md`, "The loop"); record amended (R12).
13. Minor; record is stale; §6.1; `src/omc/stageid.py:66-73`; record amended
    (R13).
14. Minor; record is stale (unrecorded addition); §6.7 `/omc:integrate`;
    `skills/integrate/SKILL.md:150` ("Validating a pair"); record amended
    (R14).
15. Minor; record is stale; §7 E2E; `tests/e2e/stages.py:47-87`,
    `tests/e2e/conftest.py:207-211`; record amended (R15).
16. Minor; record is stale (process only); §6.7 "This repo"; `README.md`,
    `.claude/skills/regenerate-readme/SKILL.md`; record amended (R16).

- M1, the fix-cycle default is the layer's ten-with-progress bound and a
  stage's `Fix cycles` only lowers it (§5 U13 and A3, §6.3, §6.4, §6.5, §6.6,
  §7; `src/omc/stagedef.py:32-35`, `src/omc/stagerun.py:106,155`): record
  amended after rebase onto main (M1).
- M2, the held-verify brief follows the fix → build → check order (§6.6):
  record amended after rebase onto main (M2).
- M3, #83's stage sequence, tree record and verify clause (§6.6, §6.7, §6.8):
  record amended after rebase onto main (M3).
- M4, ten sacred contracts and stage hooks beside project hooks (§6.2, §6.4):
  record amended after rebase onto main (M4).
- M5, `.omc/bin/verify.instructions.md` drops the documentation-only sentence
  to `.omc/hooks/needs_verify` (§6.7): record amended after rebase onto main
  (M5).
- M6, the e2e harness's `add_stage` writes a stage hook (§7): record amended
  after rebase onto main (M6).

Verdict: conforms; no code fix required.

2026-11-16: user requirement U14 applied at audit (rename, unmigrated-`.sh` refusal)

## Deliberate complexity

- `grug:simple-concurrency` @ §6.4: a detached process, a lock and a status
  file where every omc command is foreground today. Waived per U1: omc owns
  detachment, polling and killing; section 4 says why no approach without a
  supervisor keeps the exit code.
- `grug:cut-point` @ §6.4: a stage run depends on `omc watch`, a process
  with another job (knowledge freshness) and prerequisites of its own
  (GitNexus, `wt`, a provider CLI), through watch's lock. Waived per A1: the
  user made a running watch the precondition of every stage run and its end
  the end of every run.
- `grug:say-no` @ §6.3 (`## Done when`, `## Passed when`): omc decides done
  and passed from the exit code and the result file, so the two sections
  change no outcome. Waived per U7 and U2: the instructions state when the
  run is done and how success is told, in the project's terms, for the agent
  that writes the summary and briefs the fix.
- `grug:say-no` @ §6.3: omc rewrites the tracked template file in `.omc/bin/`
  although validation never reads it. Waived per U11, C2 and A4: the template
  sits beside the hooks its authors write, and is kept current by stage
  commands in linked worktrees "because most people will never run integrate again".
- `human-review:tests/tests-do-not-shape-production-code` @ src/omc/stagerun.py (`touch_s` in the run spec): the supervisor is a separate process, so a test-process monkeypatch cannot reach its touch interval; only the spec can carry it. Waived at finish review.
