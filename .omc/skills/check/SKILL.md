---
name: check
description: omc's own check stage - the quick gate (unit tests via just check).
---

# check (this repo)

Run:

```sh
just check
```

Passing means: exit code 0 (the unit test suite all green). Any non-zero
exit or test failure means the stage FAILED; include the failing output in
your summary.
