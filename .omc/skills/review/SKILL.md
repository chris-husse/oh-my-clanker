---
name: review
description: omc's own review stage - review the branch diff for defects before it ships.
---

# review (this repo)

Review the current branch's diff against the base:

```sh
git fetch origin main && git diff origin/main...HEAD
```

Judge it as a careful reviewer, focused on this repo's load-bearing rules:

- **ToolContext stays the only subprocess/env boundary** — no `subprocess`
  imports or `~/.omc` reads outside `src/omc/toolctx.py`.
- **Tests run or fail — never skip** — no `pytest.skip`/`skipif` anywhere.
- Argv lists only, never `shell=True`; user-controlled strings go through
  `shlex.quote`.
- Skills keep their machine contracts intact (OMC_SLUG / OMC_STAGE /
  OMC_SQUASH lines; internal skills marked "not meant for direct invocation").
- No secrets in code, commits, or displayed URLs (redact userinfo).

Report findings as Critical / Important / Minor with `file:line` citations.
The stage PASSES when there are no Critical or Important findings; Minor
findings are listed in the summary but do not fail the stage.
