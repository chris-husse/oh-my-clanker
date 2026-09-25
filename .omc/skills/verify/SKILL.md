---
name: verify
description: omc's Docker smoke suite, plus live lifecycle coverage for workflow/provider changes.
---

# verify (this repo)

Run the token-free container smoke suite for every verify stage:

```sh
just e2e-tests tests/e2e/test_e2e_smoke.py
```

When the change affects `omc start` or provider launch, start/plan/implement/
finish workflow skills, provider plugin setup, or conversation turn handling,
require passing live evidence for every Codex and Claude lifecycle case on a
fresh checkout image. Run the matrix serially on a trusted local machine or
private runner when that evidence is missing:

```sh
CODEX_AUTH_VOLUME=omc-e2e-codex-auth just lifecycle-tests
```

The account volume needs a prior `just codex-login` (or the documented
separate-home browser fallback); Claude needs `CLAUDE_CODE_OAUTH_TOKEN` or
`ANTHROPIC_API_KEY` in the gitignored `.env`. Do not use a public CI runner or
publish credentials. Use the default checkout image: unset
`OMC_E2E_PREBUILT_IMAGE` and `OMC_E2E_PREBUILT_SOURCE`. These tests make real
model calls and can take over an hour. A selected provider with unavailable
auth fails rather than disappearing from the matrix. Cite exact source/image,
model, command, and case results when using already-recorded evidence; split
runs count only when each case passed against the same workflow/provider source
and any failed or stopped run is disclosed. Changes limited to documentation,
command selection, or evidence filenames do not require repeating successful
model calls. The smoke command must exit 0, and any failing selected lifecycle
case without later passing evidence fails verify. Include failing output in the
stage summary. The smoke suite alone requires Docker and no tokens.
