---
name: verify
description: Run the project's verify stage if one is configured (.omc/skills/verify in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "verify" means. Intended semantics: full E2E verification - an agent runs it only within finish; the user or the project may run it at any time.
---

# omc verify (project-stage proxy)

There is nothing omc-specific to do here: this skill runs the PROJECT's
verify stage, if the project defines one.

Intended semantics: verify is the heavyweight full-E2E tier. An agent runs
it only within `finish` (before review, and last before the push); the user
or the project may run it at any time. The routine dev-loop gate is
`check`'s job.

## User Input

```text
$ARGUMENTS
```

The whole input selects the mode:

- `rerun only: <pieces>` → rerun only those pieces (the failed ones from an
  earlier run): follow the project skill's "Running a subset" section with
  them. Without that section, run the full stage and say so; the stage-gate
  rule's repair loop asks for no targeted rerun then, so this fallback only
  serves a direct call. `rerun only:` with no pieces runs the full stage.
- Empty, or anything not starting with `rerun only:` → run the full stage.
  Any text is context the caller attached.

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory.
2. Look for `<project-root>/.omc/skills/verify/SKILL.md`.
   - **Missing** → report "no project `verify` stage configured — nothing to
     do" and end (that is a PASS, not a failure).
   - **Present** → read it and follow its instructions, running commands from
     the project root, for the mode the input selected. The project skill
     decides what passing means; take its instructions at face value and
     judge the outcome honestly.
3. **Always** end with exactly one machine-readable line (plain text, no
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
