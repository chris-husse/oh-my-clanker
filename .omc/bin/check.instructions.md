## Limits
Timeout: 10m
Quiet timeout: 5m

## Done when
The hook exits: `just check` has run the unit suite
(`uv run pytest -m "not e2e and not local_iterm2" -q -n auto`). Nothing is held.

## Passed when
Exit 0: the whole unit suite is green. Any non-zero exit or test failure is failed;
include the failing output in the stage summary. This is the quick gate: no LLM, no
Docker, and network only for `tests/unit/test_installed_wheel.py` on a cold uv cache.

## Fixing failures
Read the end of the log: pytest's short summary lists each `FAILED <node id>`, and the
traceback above it shows why. Fix the code under test, red first as
`.omc/config/AGENTS.md` requires; never skip a test or add a skip guard. Never edit
`.omc/bin/*`: that is the stage itself. A `test_installed_wheel.py` failure that is a
uv download error on a cold cache is the network, not code.
