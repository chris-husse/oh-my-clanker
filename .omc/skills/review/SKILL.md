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
  OMC_SQUASH / OMC_REBASE_MAIN / OMC_KNOWLEDGE / OMC_DESIGN_RECORD /
  OMC_MODELS / OMC_WORKSPACE lines; internal skills marked
  "not meant for direct invocation").
- No secrets in code, commits, or displayed URLs (redact userinfo).
- **No slow tests.** The 5-minute ceiling itself is mechanical
  (`pytest-timeout`, 300 s per test body); this rule catches what would get
  around it. An Important finding: a `@pytest.mark.timeout` above 300 outside
  the `expensive` tier, any single wait budget above 300 s, or a measured
  runtime above about 240 s (a test that lives at the ceiling is a test about
  to flake). The `expensive` tier is exempt and no stage gates on it.
- **No serial-only tests.** A test that cannot run under `-n auto` (module
  or session state shared across tests, locks that fail instead of wait,
  guards on `PYTEST_XDIST_WORKER`, fixed host ports or paths, inherited host
  config) is an Important finding. Serialization is expressed only through
  `xdist_group`.

Report findings as Critical / Important / Minor with `file:line` citations.
The stage PASSES when there are no Critical or Important findings; Minor
findings are listed in the summary but do not fail the stage.
