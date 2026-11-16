## Limits
Timeout: 10m
Quiet timeout: 5m

## Done when
The hook exits: `just build` has run `uvx ruff format --check .`, `uvx ruff check .`
and `uv build`, stopping at the first that fails. Nothing is held.

## Passed when
Exit 0: ruff format check, ruff lint and the package build are all clean. Any non-zero
exit is failed; include the failing output in the stage summary. Unit tests are NOT run
here: that is the `check` stage's job.

## Fixing failures
Read the log: `Would reformat: <file>` is the format check (fix it with
`uvx ruff format <file>`), `<file>:<line>:<col>: <code>` lines are lint findings to fix
in the code, and anything after `uv build` is the package build. Never edit
`.omc/bin/*`: that is the stage itself. A failure to download ruff on a first `uvx`
run is the network, not code.
