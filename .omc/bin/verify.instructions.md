## Limits
Timeout: none
Quiet timeout: none

## Done when
The hook exits. With no arguments it runs `just e2e-tests`, then, when Codex code
changed, the Codex gate (`just codex-gate`), and exits after both have run; the log
line `codex code changed: running the Codex gate` marks the second. Codex code changed
when a file in `git diff --name-only origin/main...HEAD` or `git diff --name-only HEAD`
is the Codex provider (`src/omc/providers/codex.py`), the Codex conversation driver or
plugin payload (`tests/e2e/conversation.py`, `tests/e2e/codex_plugin_payload.py`,
`tests/e2e/codex_auth.py`, `docker/conversation.py`), or `docker/setup-plugins.sh`
(the whole file: its codex half is not detectable by name). Nothing is held: the run
ends when the hook exits, and its exit code is the verdict.

## Passed when
Exit 0: the parallel E2E suite is green and, when it ran, so is the Codex gate. Any
non-zero exit is failed; include the failing output in the stage summary.
`just e2e-tests` builds the image once from this checkout, runs the golden lifecycle
stages sequentially (snapshotting each stage), then everything else with `-n auto`:
variations forked from the snapshots, smoke, marketplace. Every test has a 300 s
ceiling. The Codex gate runs serially on the account volume. The old monolithic
lifecycle run (`just lifecycle-full`, the `expensive` tier) is evidence-only and never
a gate.

## Fixing failures
Read the end of the log: pytest's short summary lists each `FAILED <node id>`, and the
output above it shows why; the `codex code changed` line separates the Codex gate's
output from the suite's. These are infrastructure, not code, and go to the user rather
than to a fix: a missing token (Claude needs `CLAUDE_CODE_OAUTH_TOKEN` or
`ANTHROPIC_API_KEY` in the gitignored `.env`, from `cp env.example .env`; never a public
runner, never published credentials), missing or stopped Docker, and a missing
`just codex-login` (a selected Codex gate with unavailable auth fails rather than
disappearing). Never edit `.omc/bin/*`: that is the stage itself. Never skip a test.

## Rerunning a subset
Pass pytest node ids as the arguments, `-- <node ids…>`, for example
`-- tests/e2e/variations/test_from_recorded.py::test_write_plan_commits_plan_only`;
the hook runs `just e2e-tests <node ids…>`. The golden path always runs in full first:
its stages chain, and a fix changes the source id, so the stage snapshots are rebuilt.
Then only the named non-golden tests run. A golden node id reruns the golden path
alone. Use node ids, never `-k`: a keyword filter deselects the golden stages, so the
variations find no snapshots. A failed Codex-gate piece reruns on its own with
`-- codex-gate tests/e2e/<file>::<test>` (the hook runs
`just codex-gate tests/e2e/<file>::<test>`).
