---
name: verify
description: Run the project's verify stage if one is configured (.omc/bin/verify in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "verify" means. Intended semantics: full E2E verification - an agent runs it after `/omc:audit`'s review and in the MR gate; the user or the project may run it at any time.
---

# omc verify (project-stage proxy)

There is nothing omc-specific to do here: this skill runs the PROJECT's
verify stage, if the project defines one.

Intended semantics: verify is the heavyweight full-E2E tier. An agent
starts it in two places only: `/omc:audit`'s fixed verify after its review,
and the MR gate in `create-mr` when the gate says run; the user or the
project may run it at any time. The routine dev-loop gate is `check`'s job.

## User Input

```text
$ARGUMENTS
```

The whole input selects the mode:

- `rerun only: <pieces>` → rerun only those pieces (the failed ones from an
  earlier run): first run `.omc/bin/verify --show-instructions` from the
  project root (`git rev-parse --show-toplevel`) and read its
  `## Rerunning a subset` section. With that section, start with
  ` -- <args>` for those pieces. Without that section, run the full stage and
  say so; the stage-gate rule's repair loop asks for no targeted rerun then,
  so this fallback only serves a direct call. `rerun only:` with no pieces
  runs the full stage.
- `kill` → run `omc internal stage kill verify` and report its `state` and
  `reason`. End with `OMC_STAGE` `"passed": false`, summary
  `verify run <run> <state>: <reason>`, plus `fix cycles: <n>` as the loop
  says. With no `OMC_STAGE_RUN` line (any exit) → `"passed": false` with the
  `error:` or usage line as the summary. This is how a user ends a held
  verify; no conductor calls it (A5).
- Empty, or anything else → run the full stage. Any text is context the
  caller attached.

## The loop

Run each command from the project root (`git rev-parse --show-toplevel`). Each
prints one `OMC_STAGE_RUN {…}` line; act on its `state` and `run`, nothing
else. Every outcome other than `unconfigured` is `"configured": true`. When an
answer's `warnings` is non-empty, mention them once in a progress line and
append them to the final summary. Every `"passed": false` summary built from
an answer also says `fix cycles: <n>` when its `fix_cycles` is not 0 (0: no
run, or the stage sets no `Fix cycles` of its own), whatever the `state`.

1. `omc internal stage start verify` — a subset run appends ` -- <args>`, the
   arguments the instructions' `## Rerunning a subset` section gives for the
   named pieces.
   - `unconfigured` → no project `verify` stage configured — nothing to do:
     `"configured": false, "passed": true`. End.
   - `refused` → `"passed": false` with the `reason` as the summary. End.
   - no `OMC_STAGE_RUN` line (any exit) → `"passed": false` with the `error:`
     or usage line as the summary. End.
   - any other state → say one line naming the run
     (`started verify run <run>`), read the file at `instructions`, and go to
     2 (`wait` answers a finished run at once, with its `tail`).
2. `omc internal stage wait verify`, with the shell command's timeout set to
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

After a compaction, call `omc internal stage wait verify` again: every answer
carries the `state` and the `instructions` path, and the `run` that `start`
returned is in your `started verify run <run>` line or your last progress line.

**Always** end with exactly one machine-readable line (plain text, no
backticks or code fences around it):

`OMC_STAGE {"stage": "verify", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

Unconfigured → `"configured": false, "passed": true`. A configured stage
that failed → `"passed": false` with the failure in `summary`; the summary
names the failing pieces when the output identifies them. It is what the
stage-gate rule hands the fix subagent, and the one line of a long run
that survives a compacted context, so it is where the conductor finds
what to pass back as `rerun only`. A subset run's verdict describes the
subset: its `passed` and `summary` cover the pieces it ran, not the full
stage.
