---
name: build
description: omc's own build stage - the fast gate (format check, lint, unit tests).
---

# build (this repo)

Run:

```sh
just build
```

Passing means: exit code 0 (ruff format --check, ruff check, and the unit
test suite all clean). Any non-zero exit or test failure means the stage
FAILED; include the failing output in your summary.
