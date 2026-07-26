# E2E auth failures carry their remediation — no session fallback needed

Approved 2026-07-26. The branch opened to build a Claude session/auth fallback
into the E2E harness. Verification disproved the need. What it exposed instead
is that the harness gates on token *presence* and lets token *invalidity*
detonate later, in an unrelated test, with advice that means nothing in a
container. That is what this change fixes.

## Finding: the fallback is not needed

The branch premise was that driving the `claude` tier requires an
`ANTHROPIC_API_KEY`, and that a machine without one needs the host's
interactive Claude session smuggled into the container. Both halves are false.

`tests/e2e/test_e2e_slug_matrix.py::test_slug_free_text_description_needs_no_tracker[claude]`
**passed in 37.98s with only `CLAUDE_CODE_OAUTH_TOKEN` set** — `ANTHROPIC_API_KEY`
and `OPENAI_API_KEY` both empty. A `claude setup-token` token, pasted into
`.env`, runs the whole `claude` tier with no API key at all. The support was
already there: `TOKEN_ENV["claude"]` has listed `CLAUDE_CODE_OAUTH_TOKEN` first
since v1 (`tests/e2e/harness.py:16`), both fixtures forward it
(`tests/e2e/conftest.py:73-76`, `100-107`), and `env.example` already documented
the mint step.

Three in-container probes against the real `omc-e2e:test` image (macOS host,
claude CLI v2.1.220) established the behaviour:

| Auth state | CLI output | rc |
| --- | --- | --- |
| no token | `Not logged in · Please run /login` | 1 |
| bogus `CLAUDE_CODE_OAUTH_TOKEN` | `Failed to authenticate. API Error: 401 OAuth access token is invalid.` | 1 |
| valid token | normal output | 0 |

The middle row carries the load. The message *changes* when the variable is
set, which proves the CLI reads `CLAUDE_CODE_OAUTH_TOKEN` from the environment
— there is no codex-style "bare env var is not enough" quirk here, so no
in-container login step is needed (contrast `conftest.py:82-86`). It also shows
invalidity is reported without consuming LLM tokens.

One adjacent risk was checked and ruled out: `claude --bare` documents that
"Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper … OAuth and
keychain are never read", which would have killed the OAuth path. omc never
uses it — it invokes `claude -p <prompt> --output-format text`
(`src/omc/providers/claude.py:22`).

Recorded because it is the branch's most useful output: **no keychain
extraction, no credential mounting, no in-container login.** `cp env.example
.env` plus `claude setup-token` is the supported path, and it works.

## Problem

`require_token(provider)` (`tests/e2e/harness.py:32-38`) asks only whether the
variable is non-empty:

```python
if not any(os.environ.get(v) for v in varnames):
    pytest.fail(...)
```

A token that is expired, truncated, or accidentally quoted in `.env` passes
that gate. The run then proceeds to build the image, start a container, and
execute a test — which fails somewhere inside omc's output with `Not logged in
· Please run /login`. Three things are wrong with that failure: `/login` is an
interactive command that cannot be run in a test container; the failure is
attributed to whichever test happened to run first rather than to the
credential; and it arrives minutes in, after the image build and container
start.

The e2e tier's stated contract is not merely that missing auth fails — it is
that it fails *with the remediation*. That is asserted in the module docstring
("a missing token FAILS the test with guidance — it never skips",
`harness.py:1-3`), in `env.example`, and in the v1 design
(`docs/superpowers/specs/2026-07-17-omc-v1-design.md:227-230`). Presence-only
gating honours the letter and breaks the promise: the tier is fully covered for
an *absent* credential and completely uncovered for an *invalid* one.

## Approaches considered

**(A) Host-side validity preflight.** Validate the token from the host before
tests run. Rejected: re-implements the OAuth introspection the CLI already
performs, hardcodes both an endpoint and a token format that upstream is free
to change, and adds an HTTP dependency to a test tier that deliberately has
none.

**(B) Session-scoped in-container preflight.** One container plus one small
`claude -p` per pytest session, result cached in a session fixture. Works, and
fails ~40s earlier than the chosen design. Rejected on cost: it pays a
container start and a live LLM call on every run — including the overwhelming
majority where auth is fine — to catch a rare condition.

**(C) Detect at the choke point — chosen.** `run_in` (`harness.py:41-49`) is
the single funnel through which every container exec passes, the judge included
(`judge.py:33`). Matching known auth-failure signatures in its output converts
them into an immediate, actionable failure. It costs nothing when auth works —
no extra container, no extra LLM call, no new fixture — and it covers all ~30
`require_token` call sites across 10 test files without editing any of them.

## Decision

Add one pure function to `harness.py` and call it from `run_in`.

```python
def detect_auth_failure(output: str) -> str | None
```

Text in, remediation string or `None` out. No I/O, no Docker, no environment
access — which makes it unit-testable in the fast tier (`just test`) with no
container, and that is where its regression coverage lives.

**The function takes no `provider` argument.** This differs from the shape
sketched during brainstorming, for a reason found while specifying: `run_in`
cannot supply one. Its `argv` is typically `["omc", "start", …]` — the failing
`claude` process is spawned by omc *inside* the container, so `argv[0]` is
`omc`, not the provider. The provider is instead implied by the match, since
the signatures are claude-specific strings. Keying the map by signature rather
than by provider also keeps `run_in`'s call site free of state it has no
business knowing.

`run_in` calls it on the combined output before returning. On a hit it raises
via `pytest.fail` with the remediation. `run_in`'s `(rc, out)` return signature
is unchanged, so no caller is affected.

**Signature map — verified strings only, claude-only.**

| Signature | Meaning | Remediation |
| --- | --- | --- |
| `Not logged in` | no credential reached the CLI | `.env` missing or empty — `cp env.example .env`, then `claude setup-token` |
| `OAuth access token is invalid` | token present but expired or malformed | re-run `claude setup-token`, replace `CLAUDE_CODE_OAUTH_TOKEN` in `.env` |

Both strings were captured from live container runs, not guessed. The first is
independently corroborated by `docker/PLUGIN-NOTES.md:83`, which recorded the
same `Not logged in · Please run /login` output during earlier container work.
No `codex` or `opencode` signatures are included: their auth-failure text has
not been observed, and shipping unverified matchers is how false positives get
into a test gate. The map is structured so entries can be added when their
output is actually seen, with a comment saying exactly that — matching the
project convention of documenting provider quirks at the code site that
depends on them.

**The signatures must stay narrow, and this is not a style preference.** The
stub Jira MCP in `auth-error` mode deliberately emits `Authentication failed
(HTTP 401): OAuth token expired or revoked. Re-authenticate this MCP server and
retry.` (`docker/stub-jira-mcp/server.py:47-50`), and
`test_slug_mcp_unauthenticated` requires that run to proceed to omc's own
`mcp-unauthenticated` verdict. The two exact signatures above do not collide
with it — `Not logged in` does not appear, and the stub says "OAuth token
expired or revoked" rather than "OAuth access token is invalid". Broadening a
matcher to `OAuth`, `401`, `Authentication failed`, or similar would make the
detector fire on a fixture that is working as designed and break that test.
Match the full signature strings; never a generic fragment.

Remediation text reuses the existing `_TOKEN_GUIDANCE` entries
(`harness.py:21-25`) so guidance lives in one place and cannot drift between
the presence gate and the validity detector.

**Failing hard is safe.** Tests that deliberately assert non-zero exits assert
on omc's own vocabulary — `mcp-unauthenticated`, `mcp-missing`,
`context-insufficient` (`test_e2e_slug_matrix.py:26-38`) — which is disjoint
from the CLI's auth errors. And if auth is genuinely broken, those tests cannot
legitimately pass anyway; failing them with the real cause strictly improves
the signal over an assertion error about a missing substring.

## Mechanism

`require_token` is untouched. It remains the cheap presence gate that fails
before any container work when a variable is plainly absent; `detect_auth_failure`
covers the case it structurally cannot see. The two are complementary, and
sharing `_TOKEN_GUIDANCE` keeps them consistent.

**Fixture dedup.** `conftest.py:73-86` and `100-114` are the same
credential-forwarding and codex-login block, duplicated across the `container`
and `container_with_artifacts` fixtures — identical loops over `ALL_TOKEN_VARS`
followed by the same `codex login --with-api-key` stdin step. Fold into one
helper. This is the file the change already touches, it is where any future
per-provider auth step would belong, and a third copy is how the drift starts.
The codex-quirk comment moves with the code it explains.

**Docs.** `env.example` frames `CLAUDE_CODE_OAUTH_TOKEN` as "Optional if
ANTHROPIC_API_KEY (below) is set", which is backwards for the common case and
actively unhelpful to someone without an API key — the exact situation that
opened this branch. Restate it as verified fact: a `claude setup-token` token
alone runs the entire `claude` tier, no API key required. The boundary stays
explicit — `opencode` requires `ANTHROPIC_API_KEY` and `codex` requires
`OPENAI_API_KEY`; a Claude OAuth token cannot serve either, since neither is
the Claude Code CLI.

## Testing

Unit tests in the fast tier, no container:

- each captured signature maps to its remediation;
- **negatives**, the point of the exercise:
  - the stub Jira MCP's `auth-error` text — `Authentication failed (HTTP 401):
    OAuth token expired or revoked. Re-authenticate this MCP server and retry.`
    — must **not** match. This is the regression guard for the collision
    described in the Decision; it is the one negative case that would actually
    break a live test if the matcher were loosened.
  - omc's own verdicts `mcp-unauthenticated` and `context-insufficient` must
    not match;
- clean output returns `None`.

The already-green live E2E stands as the proof that the OAuth path works. No
bogus-token E2E is added: it would spend an image build and container start to
re-verify what the unit test covers deterministically in milliseconds.

## Out of scope

- **Keychain / session extraction.** Rejected by the user as black magic, and
  made unnecessary by the finding above.
- **A validity preflight.** YAGNI given the choke-point detector.
- **The worktree `.env` timing artifact.** This worktree's `.env` was empty
  because it was cut before the token was added to the primary checkout's
  `.env` — a timing artifact, not a defect. `.config/wt.toml` already
  reflink-copies gitignored files via `wt step copy-ignored`.
- **Renaming the branch**, whose name now describes a thing that is not being
  built.

## Constraints preserved

- No `pytest.skip` anywhere in the e2e tier; auth problems fail with guidance.
- `require_token(provider)`'s signature is stable — ~30 call sites across 10
  files are untouched.
- Secrets never enter the image: `.env` is gitignored and dockerignored;
  credentials arrive only as runtime env (`docker/Dockerfile.e2e:1-3`).
- Provider quirks stay documented as comments at the code site that depends on
  them.
