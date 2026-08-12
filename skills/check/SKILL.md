---
name: check
description: Run the project's check stage if one is configured (.omc/skills/check in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "check" means. Intended semantics: quick validation while working - build only what the unit tests need, then run them.
---

# omc check (project-stage proxy)

There is nothing omc-specific to do here: this skill runs the PROJECT's
check stage, if the project defines one.

Intended semantics: check is the fast "am I on the right track" gate —
build only what the unit tests need, then run them. Call it constantly
during development; the world-build belongs to `build` and full E2E to
`verify` (major milestones only).

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory.
2. Look for `<project-root>/.omc/skills/check/SKILL.md`.
   - **Missing** → report "no project `check` stage configured — nothing to
     do" and end (that is a PASS, not a failure).
   - **Present** → read it and follow its instructions, running commands from
     the project root. The project skill decides what passing means; take its
     instructions at face value and judge the outcome honestly.
3. **Always** end with exactly one machine-readable line (plain text, no
   backticks or code fences around it):

   `OMC_STAGE {"stage": "check", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

   Unconfigured → `"configured": false, "passed": true`. A configured stage
   that failed → `"passed": false` with the failure in `summary`.
