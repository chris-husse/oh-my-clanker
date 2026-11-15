---
name: verify
description: omc's own verify stage - the parallel Docker E2E suite (golden lifecycle path, then every variation and smoke case in parallel); Codex gate only when Codex code changed.
---

# verify (this repo)

Run the parallel E2E suite:

```sh
just e2e-tests
```

It builds the image once from this checkout, runs the golden lifecycle
stages sequentially (snapshotting each stage), then everything else with
`-n auto`: variations forked from the snapshots, smoke, marketplace. Every
test has a 300 s ceiling. Exit 0 passes; include failing output in the
stage summary. Docker is required; Claude needs `CLAUDE_CODE_OAUTH_TOKEN` or
`ANTHROPIC_API_KEY` in the gitignored `.env` (never a public runner, never
published credentials).

When the change touches the Codex provider (`src/omc/providers/codex.py`),
the Codex conversation driver or plugin payload (`tests/e2e/conversation.py`,
`tests/e2e/codex_plugin_payload.py`, `tests/e2e/codex_auth.py`,
`docker/conversation.py`, the codex half of `docker/setup-plugins.sh`), also
run the Codex gate, serially on the account volume (needs a prior
`just codex-login`):

```sh
just codex-gate
```

A selected Codex gate with unavailable auth fails rather than disappearing.

## Running a subset

To rerun only some pieces, pass pytest node ids:

```sh
just e2e-tests <pytest node ids…>
just e2e-tests tests/e2e/variations/test_from_recorded.py::test_write_plan_commits_plan_only
```

The golden path always runs in full first: its stages chain, and a fix
changes the source id, so the stage snapshots are rebuilt. Then only the named
non-golden tests run. A golden node id reruns the golden path alone. Use node
ids, never `-k`: a keyword filter deselects the golden stages, so the
variations find no snapshots. A failed Codex-gate piece reruns on its own with
`just codex-gate tests/e2e/<file>::<test>`.

The old monolithic lifecycle run (`just lifecycle-full`, the `expensive`
tier) is evidence-only and never a gate. Changes limited to documentation
do not require repeating successful model calls; cite the last green run.
