---
name: build
description: Run the project's build stage if one is configured (.omc/bin/build in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "build" means. Intended semantics: build the world - everything compiles and packages, NO tests.
---

# omc build (project-stage proxy)

There is nothing omc-specific to do here: this skill runs the PROJECT's
build stage, if the project defines one.

Intended semantics: build is the world-build — everything compiles and
packages, with NO tests. Unit-test validation belongs to `check`; full
E2E belongs to `verify`.

## The loop

Run each command from the project root (`git rev-parse --show-toplevel`). Each
prints one `OMC_STAGE_RUN {…}` line; act on its `state` and `run`, nothing
else. Every outcome other than `unconfigured` is `"configured": true`. When an
answer's `warnings` is non-empty, mention them once in a progress line and
append them to the final summary. Every `"passed": false` summary built from
an answer also says `fix cycles: <n>` when its `fix_cycles` is not 0 (0: no
run, or the stage sets no `Fix cycles` of its own), whatever the `state`.

1. `omc internal stage start build` — a subset run appends ` -- <args>`, the
   arguments the instructions' `## Rerunning a subset` section gives for the
   named pieces.
   - `unconfigured` → no project `build` stage configured — nothing to do:
     `"configured": false, "passed": true`. End.
   - `refused` → `"passed": false` with the `reason` as the summary. End.
   - no `OMC_STAGE_RUN` line (any exit) → `"passed": false` with the `error:`
     or usage line as the summary. End.
   - any other state → say one line naming the run
     (`started build run <run>`), read the file at `instructions`, and go to
     2 (`wait` answers a finished run at once, with its `tail`).
2. `omc internal stage wait build`, with the shell command's timeout set to
   600 s (600000 ms). No `OMC_STAGE_RUN` line (any exit) → as in step 1.
   Otherwise, first compare `run` with the one `start` returned. If it
   differs → `"passed": false`, summary "superseded by another run" (the other
   run's outcome is never reported as this one's). End. Otherwise, by `state`:
   - `running` → say one progress line (`run`, `elapsed` and the last line of
     `tail`) and repeat step 2. Nothing else: no sleep, no kill, no judgment
     about how long it has been.
   - `passed` → `"passed": true`.
   - `failed`, `held` → `"passed": false`; read the file at `instructions`
     first if you have not read it yet. The summary says what failed as far
     as `tail` and the instructions' `## Fixing failures` tell, gives the
     `log` and `instructions` paths and says `held` when the run is held.
   - `timed-out`, `quiet-timed-out`, `killed`, `died` → `"passed": false`;
     the summary is the `reason` and the `log` and `instructions` paths. A
     `reason` starting `watch-gone` makes the summary start with
     `CRITICAL STOP:`.

After a compaction, call `omc internal stage wait build` again: every answer
carries the `state` and the `instructions` path, and the `run` that `start`
returned is in your `started build run <run>` line or your last progress line.

**Always** end with exactly one machine-readable line (plain text, no
backticks or code fences around it):

`OMC_STAGE {"stage": "build", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

Unconfigured → `"configured": false, "passed": true`. A configured stage
that failed → `"passed": false` with the failure in `summary`.
