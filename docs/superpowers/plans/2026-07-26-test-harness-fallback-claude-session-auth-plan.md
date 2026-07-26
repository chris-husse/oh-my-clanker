# Actionable E2E Auth Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an invalid or absent provider credential fail where it happens, carrying its own remediation, instead of detonating later inside an unrelated test with `/login` advice that is meaningless in a container.

**Architecture:** One pure function, `detect_auth_failure(output)`, added to `tests/e2e/harness.py` and called from `run_in` — the single funnel through which every container exec passes, the LLM judge included. It matches verified auth-failure signatures in command output and converts them into a `pytest.fail` carrying the fix. Costs nothing when auth works: no extra container, no extra LLM call, no new fixture, and no change to `run_in`'s `(rc, out)` signature, so all ~30 existing call sites are untouched.

**Tech Stack:** Python 3, pytest, testcontainers, ruff (line-length 100), `just` task runner.

**Spec:** `docs/superpowers/specs/2026-07-26-test-harness-fallback-claude-session-auth-design.md` (commit `d0a3797`)

---

## Background the implementer needs

The branch was opened to build a "Claude session/auth fallback" so the E2E suite could run without an `ANTHROPIC_API_KEY`. **That work was cancelled: live verification proved it unnecessary.** A `claude setup-token` OAuth token in `.env` already runs the entire `claude` tier with no API key — proven by `test_slug_free_text_description_needs_no_tracker[claude]` passing in 37.98s with only `CLAUDE_CODE_OAUTH_TOKEN` set. Do not implement any fallback, keychain reader, or credential mount.

Three container probes produced the signatures this plan depends on:

| Auth state | CLI output | rc |
| --- | --- | --- |
| no token | `Not logged in · Please run /login` | 1 |
| bogus `CLAUDE_CODE_OAUTH_TOKEN` | `Failed to authenticate. API Error: 401 OAuth access token is invalid.` | 1 |
| valid token | normal output | 0 |

**The trap that shapes the whole design:** the stub Jira MCP has an `auth-error` mode that deliberately emits `Authentication failed (HTTP 401): OAuth token expired or revoked. Re-authenticate this MCP server and retry.` (`docker/stub-jira-mcp/server.py:47-50`). The test `test_slug_mcp_unauthenticated` needs that run to proceed normally to omc's own `mcp-unauthenticated` verdict. The two signatures above do not collide with it — but broadening either to `OAuth`, `401`, or `Authentication failed` **will** break that test. Match full signature strings, never fragments.

## File Structure

- **`tests/e2e/harness.py`** (modify) — owns the token model and the container exec helper. Gains `_AUTH_FAILURES` (the signature→remediation table) and `detect_auth_failure`, and `run_in` gains the check. Natural home: it already owns `_TOKEN_GUIDANCE` and `require_token`, so all credential guidance stays in one file.
- **`tests/unit/test_e2e_auth_detect.py`** (create) — fast-tier unit tests for the pure detector. Lives under `tests/unit/` because it needs no Docker; verified that `import tests.e2e.harness` pulls in no Docker dependency.
- **`tests/e2e/conftest.py`** (modify) — fixture-level dedup of the duplicated credential-forwarding and codex-login blocks.
- **`env.example`** (modify) — documentation correction.

---

### Task 1: The pure detector and its tests

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/harness.py:21-25` (add below `_TOKEN_GUIDANCE`)
- Test: `tests/unit/test_e2e_auth_detect.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_e2e_auth_detect.py`:

```python
"""The e2e auth-failure detector is pure text matching — no Docker needed, so it
lives in the fast tier."""

from tests.e2e.harness import detect_auth_failure

# Captured verbatim from live container runs against omc-e2e:test.
NOT_LOGGED_IN = "Not logged in · Please run /login"
INVALID_TOKEN = "Failed to authenticate. API Error: 401 OAuth access token is invalid."

# The stub Jira MCP's auth-error mode emits this ON PURPOSE and
# test_slug_mcp_unauthenticated depends on it reaching omc's own verdict.
STUB_MCP_AUTH_ERROR = (
    "Authentication failed (HTTP 401): OAuth token expired or revoked. "
    "Re-authenticate this MCP server and retry."
)


def test_detects_missing_credentials():
    remediation = detect_auth_failure(NOT_LOGGED_IN)
    assert remediation is not None
    assert "setup-token" in remediation


def test_detects_invalid_token():
    remediation = detect_auth_failure(INVALID_TOKEN)
    assert remediation is not None
    assert "CLAUDE_CODE_OAUTH_TOKEN" in remediation


def test_ignores_stub_mcp_auth_error():
    """The regression guard: a broader matcher would fire here and break
    test_slug_mcp_unauthenticated."""
    assert detect_auth_failure(STUB_MCP_AUTH_ERROR) is None


def test_ignores_omc_verdicts():
    assert detect_auth_failure("omc: mcp-unauthenticated") is None
    assert detect_auth_failure("omc: context-insufficient") is None


def test_clean_output_is_none():
    assert detect_auth_failure("branch: proj-1-add-rate-limiting\n") is None
    assert detect_auth_failure("") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_e2e_auth_detect.py -q`
Expected: FAIL — `ImportError: cannot import name 'detect_auth_failure' from 'tests.e2e.harness'`

- [ ] **Step 3: Write the implementation**

In `tests/e2e/harness.py`, immediately after the `_TOKEN_GUIDANCE` dict (line 25) and before `PROVIDERS`, add:

```python
# Auth failures surface INSIDE the container, printed by a provider CLI that omc
# spawns itself — run_in's argv is usually ["omc", ...], so the failing provider
# cannot be read off the command. These signatures are provider-specific strings,
# so a match identifies the provider on its own; no provider argument is needed.
#
# VERIFIED strings only, captured from live container runs (the first is also
# recorded in docker/PLUGIN-NOTES.md). codex and opencode have no entries because
# their auth-failure output has never been observed here — do not guess one, and
# never broaden these to a fragment like "OAuth", "401", or "Authentication
# failed": the stub Jira MCP's auth-error mode emits "Authentication failed (HTTP
# 401): OAuth token expired or revoked" on purpose, and a loose matcher would fire
# on it and break test_slug_mcp_unauthenticated.
_AUTH_FAILURES = (
    (
        "Not logged in",
        "claude has no credentials in the container — "
        f"{_TOKEN_GUIDANCE['claude']} (start from `cp env.example .env`).",
    ),
    (
        "OAuth access token is invalid",
        "claude rejected CLAUDE_CODE_OAUTH_TOKEN as expired or malformed — "
        "re-run `claude setup-token` and replace it in .env.",
    ),
)


def detect_auth_failure(output: str) -> str | None:
    """Map a container CLI's auth-failure output to its remediation.

    Pure: text in, guidance or None out. run_in calls this on every exec so a bad
    credential fails at the point of use with the fix attached, instead of
    surfacing as an unrelated assertion three tests later.
    """
    for signature, remediation in _AUTH_FAILURES:
        if signature in output:
            return remediation
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_e2e_auth_detect.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Run the full fast gate**

Run: `just build`
Expected: lint + format check + all unit tests pass. If ruff flags line length, keep lines ≤ 100 chars.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/harness.py tests/unit/test_e2e_auth_detect.py
git commit -m "Detect provider auth failures from container output"
```

---

### Task 2: Wire the detector into `run_in`

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/harness.py:41-49` (`run_in`)

`run_in` is the single funnel for every container command in the suite, including the LLM judge (`tests/e2e/judge.py:33`). Verified: no test invokes a provider CLI by any other route; the only direct `exec_run` calls are fixture setup in `conftest.py`.

- [ ] **Step 1: Replace the body of `run_in`**

The current implementation is:

```python
def run_in(container, argv, *, env=None, cwd=None, timeout=600):
    """Exec argv in the container; returns (rc, combined-output)."""
    cmd = shlex.join(argv)
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    wrapped = ["timeout", str(timeout), "bash", "-lc", cmd]
    envs = {k: v for k, v in (env or {}).items()}
    result = container.get_wrapped_container().exec_run(wrapped, environment=envs or None)
    return result.exit_code, result.output.decode(errors="replace")
```

Replace it with:

```python
def run_in(container, argv, *, env=None, cwd=None, timeout=600):
    """Exec argv in the container; returns (rc, combined-output).

    A provider auth failure anywhere in that output fails the test right here,
    with the remediation — otherwise it resurfaces later as a confusing
    assertion about missing text, blamed on whichever test ran first.
    """
    cmd = shlex.join(argv)
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    wrapped = ["timeout", str(timeout), "bash", "-lc", cmd]
    envs = {k: v for k, v in (env or {}).items()}
    result = container.get_wrapped_container().exec_run(wrapped, environment=envs or None)
    output = result.output.decode(errors="replace")
    remediation = detect_auth_failure(output)
    if remediation is not None:
        pytest.fail(f"provider auth failed in container: {remediation}\n\n{output}")
    return result.exit_code, output
```

The `(rc, out)` return shape is unchanged — do not alter any caller.

- [ ] **Step 2: Verify nothing else changed shape**

Run: `git diff --stat tests/e2e/harness.py`
Expected: only `tests/e2e/harness.py` modified. No test file appears in the diff.

- [ ] **Step 3: Run the fast gate**

Run: `just build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/harness.py
git commit -m "Fail e2e runs at the point a provider auth error appears"
```

---

### Task 3: Dedup the fixture credential setup

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/conftest.py:69-117`

`container` (lines 69-89) and `container_with_artifacts` (lines 92-117) carry byte-identical credential-forwarding loops and codex-login blocks. Fold both into helpers so a third fixture cannot drift, and so any future per-provider auth step has one home.

- [ ] **Step 1: Add the helpers**

Insert directly above the `e2e_image` fixture (before line 51):

```python
def _forward_tokens(c):
    """Forward whatever provider credentials the host has into the container.
    Absent vars are simply not forwarded — require_token does the gating."""
    for var in ALL_TOKEN_VARS:
        if os.environ.get(var):
            c = c.with_env(var, os.environ[var])
    return c


def _finish_container_setup(c):
    """Post-start steps every container needs, in order."""
    # finish plugin registration (needs network; baked layer may have been offline)
    c.get_wrapped_container().exec_run(["bash", "/repo/docker/setup-plugins.sh"])
    # codex >=0.144 doesn't use a bare OPENAI_API_KEY env — it needs an explicit
    # stdin login that writes ~/.codex/auth.json (real users run this themselves).
    if os.environ.get("OPENAI_API_KEY"):
        c.get_wrapped_container().exec_run(
            ["bash", "-c", "printenv OPENAI_API_KEY | codex login --with-api-key"]
        )
```

- [ ] **Step 2: Rewrite both fixtures to use them**

Replace the `container` fixture body:

```python
@pytest.fixture
def container(e2e_image):
    from testcontainers.core.container import DockerContainer

    c = _forward_tokens(DockerContainer(e2e_image).with_command("sleep infinity"))
    try:
        c.start()
        _finish_container_setup(c)
        yield c
    finally:
        c.stop()
```

Replace the `container_with_artifacts` fixture body, keeping its docstring:

```python
@pytest.fixture
def container_with_artifacts(e2e_image):
    """A container with tests/e2e/artifacts mounted rw at /artifacts — the
    permanent-E2E-artifact channel (committed wiki docs sync back out)."""
    from testcontainers.core.container import DockerContainer

    artifacts = REPO_ROOT / "tests" / "e2e" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    c = _forward_tokens(
        DockerContainer(e2e_image)
        .with_command("sleep infinity")
        .with_volume_mapping(str(artifacts), "/artifacts", "rw")
    )
    try:
        c.start()
        _finish_container_setup(c)
        yield c
    finally:
        c.stop()
```

The codex-quirk comment moves with the code it explains — it must end up inside `_finish_container_setup`, not left behind.

- [ ] **Step 3: Confirm no duplicated block remains**

Run: `grep -c "codex login --with-api-key" tests/e2e/conftest.py`
Expected: `1`

- [ ] **Step 4: Run the fast gate**

Run: `just build`
Expected: PASS (this file is import-checked by lint even though its fixtures need Docker).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py
git commit -m "Fold duplicated container credential setup into helpers"
```

---

### Task 4: Correct `env.example`

**Model:** standard coding tier

**Files:**
- Modify: `env.example`

The file currently tells a reader that `CLAUDE_CODE_OAUTH_TOKEN` is "Optional if ANTHROPIC_API_KEY (below) is set" — backwards for the common case, and actively unhelpful to someone with no API key, which is exactly the situation that opened this branch.

- [ ] **Step 1: Replace the Claude and Anthropic stanzas**

Replace:

```
# Claude Code (live E2E for the `claude` provider + its judge calls).
# Get one: run `claude setup-token` in a terminal and paste the result here.
# Optional if ANTHROPIC_API_KEY (below) is set — claude accepts that too.
CLAUDE_CODE_OAUTH_TOKEN=
```

with:

```
# Claude Code (live E2E for the `claude` provider + its judge calls).
# Get one: run `claude setup-token` in a terminal and paste the result here.
# This token ALONE runs the whole `claude` tier — no API key needed (verified).
CLAUDE_CODE_OAUTH_TOKEN=
```

Replace:

```
# Anthropic API key: used by the `opencode` provider AND as claude auth fallback.
# Get one: https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=
```

with:

```
# Anthropic API key: REQUIRED by the `opencode` provider; also accepted by
# `claude` as an alternative to the token above. A Claude subscription token
# cannot drive opencode or codex — those need real API keys.
# Get one: https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=
```

- [ ] **Step 2: Commit**

```bash
git add env.example
git commit -m "Document that a setup-token alone runs the claude e2e tier"
```

---

### Task 5: Live verification

**Model:** top tier

**Files:** none — verification only.

- [ ] **Step 1: Fast gate**

Run: `just build`
Expected: PASS.

- [ ] **Step 2: Live E2E proof**

Run: `just e2e-tests 'tests/e2e/test_e2e_slug_matrix.py::test_slug_free_text_description_needs_no_tracker[claude]'`
Expected: `1 passed`.

`just` word-splits recipe arguments, so a `-k "a and b"` expression breaks — pass the single quoted node id exactly as shown. The `omc-e2e:test` image is already built and cached locally. This worktree's `.env` has a valid `CLAUDE_CODE_OAUTH_TOKEN`; `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are empty, so codex- and opencode-parametrized tests will fail their presence gate by design — run only claude-parametrized node ids.

- [ ] **Step 3: Confirm the MCP path is not broken by the detector**

This is the test the false-positive trap would break.

Run: `just e2e-tests 'tests/e2e/test_e2e_slug_matrix.py::test_slug_mcp_unauthenticated[claude]'`
Expected: `1 passed` — the stub's `Authentication failed (HTTP 401): OAuth token expired or revoked` output must flow through to omc's `mcp-unauthenticated` verdict without the detector firing.

- [ ] **Step 4: Report results**

Report the actual command output for all three runs. Do not claim success without it.

---

## Self-Review

**Spec coverage:** Finding/premise → Background section. Pure `detect_auth_failure` (no provider arg) → Task 1. Verified-signatures-only, claude-only, narrow-match constraint → Task 1 code comment + Task 1 negative tests + Task 5 Step 3. `run_in` wiring, unchanged `(rc, out)` → Task 2. `require_token` untouched → no task modifies it (intentional). Fixture dedup → Task 3. `env.example` → Task 4. Unit tests incl. stub-MCP negative → Task 1. No bogus-token E2E → deliberately absent. All spec sections have a task.

**Placeholder scan:** No TBD/TODO; every code step carries complete code; every command carries expected output.

**Type consistency:** `detect_auth_failure(output: str) -> str | None` is defined in Task 1 and called with that exact signature in Task 2 and the Task 1 tests. `_AUTH_FAILURES` is a tuple of `(signature, remediation)` pairs, iterated as such. `_forward_tokens(c) -> c` returns the container (builder chaining); `_finish_container_setup(c) -> None` does not — both used accordingly in Task 3.
