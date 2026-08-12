---
name: build
description: omc's own build stage - build the world (format check, lint, package build). No tests.
---

# build (this repo)

Run:

```sh
just build
```

Passing means: exit code 0 (ruff format --check, ruff check, and `uv build`
all clean). Any non-zero exit means the stage FAILED; include the failing
output in your summary. Unit tests are NOT run here — that is the `check`
stage's job.
