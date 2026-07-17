---
name: verify
description: omc's own verify stage - the Docker smoke suite (container harness, no LLM tokens needed).
---

# verify (this repo)

Run:

```sh
just e2e-tests tests/e2e/test_e2e_smoke.py
```

Passing means: exit code 0 (all smoke tests green — container toolchain,
configure gate, install re-root, wt wiring). Requires Docker; the first run
builds the E2E image and can take minutes — that is normal, not a hang. Any
failing test means the stage FAILED; include the failing output in your
summary.

(The full live matrix — `just e2e-tests` — needs provider tokens in `.env`
and real money; it is NOT part of this stage.)
