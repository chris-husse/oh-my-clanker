---
name: review
description: Run the project's review stage if one is configured (.omc/skills/review in the repo omc is invoked from); nothing to do otherwise. Proxy - the project defines what "review" means.
---

# omc review (project-stage proxy)

There is nothing omc-specific to do here: this skill runs the PROJECT's
review stage, if the project defines one.

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory.
2. Look for `<project-root>/.omc/skills/review/SKILL.md`.
   - **Missing** → report "no project `review` stage configured — nothing to
     do" and end (that is a PASS, not a failure).
   - **Present** → read it and follow its instructions, running commands from
     the project root. The project skill decides what passing means; take its
     instructions at face value and judge the outcome honestly.
3. **Always** end with exactly one machine-readable line (plain text, no
   backticks or code fences around it):

   `OMC_STAGE {"stage": "review", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

   Unconfigured → `"configured": false, "passed": true`. A configured stage
   that failed → `"passed": false` with the failure in `summary`.
