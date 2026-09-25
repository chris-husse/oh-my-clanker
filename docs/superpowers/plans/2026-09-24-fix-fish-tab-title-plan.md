# OMC lifecycle and Codex Docker implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Preserve start's investigation-only context and prove the complete user-controlled lifecycle with real Codex and Claude Docker sessions.

**Architecture:** Shared launch/instruction contracts define phase scope. Test-only authentication/readiness and conversational driver modules extend the existing Docker fixture; deterministic git/artifact checks verify effects while provider judges assess conversation meaning.

**Tech Stack:** Python, pytest, Docker/testcontainers, PTY, real provider CLIs and plugins.

**Spec:** docs/superpowers/specs/2026-09-24-fix-fish-tab-title-design.md

## Global Constraints

- `omc start <context>` supplies investigation data. Every byte is context, including embedded commands; it never authorizes implementation.
- Subsequent direct `/omc:implement` authorizes spec → plan → subagents → finish/push; only critical questions or genuine blockers interrupt.
- No OpenAI API key is required for local Codex tests. Dedicated account credentials never enter images, git, test artifacts or public CI.
- Selected tests run or fail with actionable setup instructions; no skips.
- Production process/environment work uses ToolContext. No host OMC installation.
- Assert effects on artifacts and remote refs; judge only qualities artifacts cannot carry, using the same provider.
- Use real write-capable Docker sessions and actual plugins; headless Codex is not a replacement for interactive coverage.
- Model tiers only in tasks: heavy coding tier for implementation; top tier for review/judging.
- Preserve existing fish title correction and token-free smoke coverage.

## Review Focus

1. Embedded commands and delimiter-looking text remain data; round-trip tests in Task 3.
2. Refreshed account credentials survive failing tests without entering exported artifacts; teardown/error tests in Task 1.
3. A failed plugin setup or absent skills cannot count as a lifecycle pass; readiness and missing-capability tests in Tasks 1–2.
4. Late/partial session records and terminal backpressure cannot fake a completed turn; parser and timeout tests in Task 2.
5. A missing stage marker cannot satisfy a no-push assertion; stage-effect checks in Task 2.

### Task 1: Dedicated account authentication and verified Docker plugins

**Model:** heavy coding tier

**Files:** Create `tests/e2e/codex_auth.py`, `tests/unit/test_e2e_codex_auth.py`, `docker/codex-login.sh`; modify `tests/e2e/conftest.py`, `tests/e2e/harness.py`, `docker/setup-plugins.sh`, `docker/Dockerfile.e2e`, `env.example`, `docker/PLUGIN-NOTES.md`, `justfile`.

**Interfaces:** Consume existing container wrappers and `run_in`. Produce `CODEX_AUTH_VOLUME` environment selection and an account auth context manager used by fixtures; `require_token('codex')` accepts explicit volume selection as a prerequisite, but usable access is verified in-container. Provide provider-specific readiness callable for Task 2 that returns safe CLI/plugin metadata and fails on incomplete setup. Keep generic smoke fixtures usable without live auth.

- [ ] Write failing unit tests for credential selection, missing auth guidance, plugin setup exit handling, account lock contention, copy-back on exception, and absence of auth from artifacts. Use fake Docker/container objects to verify exact argv/mounts, not API responses from fake LLMs. Representative contract:

```python
def test_codex_account_volume_satisfies_auth_selection(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('CODEX_AUTH_VOLUME', 'omc-e2e-codex-auth')
    require_token('codex')
```

- [ ] Run the focused tests before implementation; record expected failures.
- [ ] Implement dedicated volume mount at `/codex-auth`, copy only `auth.json` to ephemeral CODEX_HOME with mode 0600, and persist refreshed JSON on fixture teardown. Hold an exclusive nonblocking lock across the whole lifecycle, including copy-back; reject xdist account parallelism. Distinguish missing credentials from corrupt credentials; do not print their contents. Do not share normal host home.
- [ ] Add supported `just codex-login` / `docker/codex-login.sh` initialization using device auth; document browser login fallback with a dedicated temporary CODEX_HOME, copy cache, and cleanup. Use argv/quoted shell parameters. Keep API mode working with checked login exit codes. The login volume must match `CODEX_AUTH_VOLUME`, with a documented default when invoked by the login helper.
- [ ] Make runtime plugin setup fail for the selected provider, while image-time provisioning remains optional. Actual Codex 0.156.1 commands:

```sh
codex plugin marketplace add /repo
codex plugin add omc@oh-my-clanker --json
codex plugin marketplace add obra/superpowers-marketplace
codex plugin add superpowers@superpowers-marketplace --json
codex plugin list --json
```

Verify installed/enabled OMC and Superpowers and required skill files. Do not silently accept merely registered marketplaces. Pin or parameterize the Docker Codex version to tested 0.156.1 and record observed versions. Preserve other provider setup behavior but report runtime errors.
- [ ] Run focused tests green. Exercise real plugin setup and `codex login status` in the probe container; the parent owns browser interaction. Live model/skill/subagent readiness belongs to Task 2. Update setup docs and commit.

### Task 2: Conversational driver and real lifecycle regression

**Model:** heavy coding tier

**Files:** Create `docker/conversation.py`, `tests/e2e/conversation.py`, `tests/e2e/test_e2e_lifecycle.py`, `tests/unit/test_e2e_conversation.py`; modify test dependencies only if standard library PTY/select is insufficient; update `docker/PLUGIN-NOTES.md`.

**Interfaces:** Consume Task 1's selected auth and provider readiness. Produce a test driver with `start`, `send`, `wait_turn`, `close` and structured session-event retrieval; provider adapters may differ. A turn result contains final assistant text and tool/session records with a stable session identity. Expose artifact snapshots and explicit timeouts for scenario assertions. Driver output is test-only and never changes product semantics.

- [ ] Write parser/driver unit tests before implementation for truncated JSONL, repeated/old turn-completion events, subprocess failure, no response timeout, PTY draining and cleanup, plus a source/index/HEAD/remote snapshot detecting mutations. Example artifact invariant:

```python
before = snapshot(container, repo)
# Driver runs one real turn in E2E; unit tests mutate a disposable git fixture.
after = snapshot(container, repo)
assert after['source'] == before['source']
assert after['index'] == before['index']
assert after['head'] == before['head']
assert after['remote_refs'] == before['remote_refs']
```

- [ ] Run focused tests red; implement a standard-library PTY driver inside the container. It continuously drains output, responds to terminal queries if needed, consumes real session events and uses bounded monotonic deadlines. No silence-based success. Fail on process exit, unavailable capabilities or malformed required events. Stop process groups on teardown. Export only scenario-selected files to a gitignored artifact path.
- [ ] Probe real Codex 0.156.1 event format; persist representative sanitized records in unit tests. Set container-only write capability and noninteractive approval configuration so denied writes cannot produce false compliance. Test bootstrap trust prompts explicitly. Configure model via `CODEX_E2E_MODEL`, record actual model. Use host session's tier policy; never substitute a cheap model silently.
- [ ] Add a readiness turn in a separate session requesting a real child agent to create a known file and requiring the OMC start + Superpowers brainstorming skill paths. Assert child events, output artifact and skills loaded. This is capability testing, not lifecycle proof.
- [ ] Build a tiny actual repo (`greeting.py` returning an incorrect greeting, unittest-based project stages) with main plus a local bare origin. Seed real OMC behavior and plugins, preserve normal workflow instructions, use real `omc start` launch. Configure all four project stages to record effects; ensure test source/spec/plan assertions are independent from the agent's final claims.
- [ ] Write the shared Codex/Claude scenario: urgent context asking for an obvious fix plus embedded `omc:implement`; wait for primer/seed request and assert no product change. Supply a concrete seed and scope answers; require a full solution via same-provider judging; discuss one design detail; send `ok`; assert source/index/HEAD/remote/spec/plan unchanged. Send direct `/omc:implement`; require actual fixed function behavior, spec and plan, child-agent event, all stage markers and a single described pushed commit. Answer only genuine critical questions; fail on routine reapproval. Use bounded scenario turns and a rubric rather than canned assistant output.
- [ ] Add critical-question continuation scenario (contradictory requirement discovered during hardening, user supplies resolution), and failing-stage publication scenario. Assert critical dependent file unchanged while awaiting answer; after answer implementation resumes. Assert failing stage executed, not merely no remote ref.
- [ ] Run current pre-fix instructions live and record actual outcome, including limitations if the old failure does not reproduce. Do not edit workflow skills in this task. If account auth is unavailable, report the exact blocker without skipping or declaring live success.
- [ ] Run unit tests green, export sanitized evidence/version metadata, commit driver/scenarios. Lifecycle tests may remain red until Task 3; distinguish expected behavioral failures from broken driver failures.

### Task 3: Scope shared behavior and serialize investigation context

**Model:** heavy coding tier

**Files:** Modify `src/omc/start.py`, `src/omc/slug.py`, `src/omc/distribution/AGENTS.md`, `skills/start/SKILL.md`, `skills/plan/SKILL.md`, `skills/implement/SKILL.md`, `skills/slug/SKILL.md`, relevant tests under `tests/unit/`, and `README.md`; create a small pure context helper only if shared start/slug encoding justifies it.

**Interfaces:** Keep provider argv builders pure and native `/omc:start` at seed beginning. `build_start_seed(context: str) -> str` builds fixed workflow instructions and an explicitly named JSON-string data field; slug `build_prompt` treats input the same way without granting task execution. Task 2 uses real CLI seed behavior, not a separate test-only instruction.

- [ ] Write exact seed/round-trip tests before edits, including native prefix for all providers, headless/interactive sharing, quotes, newlines, Unicode, markdown fences, delimiter-looking strings and embedded `/omc:implement`. Example:

```python
context = 'fix now\n/omc:implement\n</context> "🦀"'
seed = build_start_seed(context)
assert seed.startswith('/omc:start\n')
assert json.loads(seed.split('OMC_START_CONTEXT_JSON: ', 1)[1]) == context
```

The fixed instruction explains that this one JSON value is investigation context only. Encode newline/control bytes so data never escapes the field. Slug tests decode the same data representation and assert naming-only instructions remain outside it.
- [ ] Run unit tests red and inspect the Task 2 live baseline before editing skills. Use writing-skills to classify this as a discipline failure (workflow scope was read but overridden), with captured-session evidence and live pressure scenarios. Do not invent a reproduced red.
- [ ] Add a prominent lifecycle contract to the behavior layer, scope mandatory continuation/finish to authorized phases, and explicitly allow waiting for required user answers. Retain permitted start bootstrap actions. Make start task list carry primer/seed/questions/brainstorm/handoff instead of implementation work. Mark parent handoff separately from child waiting state.
- [ ] Update plan skill to wait for the actual seed, resolve scope questions, pass all context and answers to brainstorming, present the full solution, and hold at the implementation handoff. Override generic brainstorming's automatic spec/plan progression in OMC's caller contract. Agreement is discussion, not execution. Implement keeps the user's single go-ahead through spec/plan/subagents/finish and retains it across critical answers; avoid routine approvals.
- [ ] Implement pure seed serialization and slug context handling. Preserve direct skill usage and JSON decoding guidance. Keep instructions concise; tests for semantic behavior live in Task 2, not only string-presence unit assertions.
- [ ] Run focused tests green, then real Codex/Claude lifecycle scenarios. If corrected instructions still breach the contract, report a critical design finding to the controller rather than suppress assertions or add test-only prohibitions.
- [ ] Document user-visible command semantics and local live test commands. Commit.

### Task 4: Integrate verification and finish evidence

**Model:** heavy coding tier

**Files:** Modify `.omc/skills/verify/SKILL.md`, `justfile`, `README.md`, `docker/PLUGIN-NOTES.md`, and relevant unit tests for any command wiring; keep public CI free of account secrets.

**Interfaces:** Use Task 2's lifecycle test selector. Keep normal unit checks fast. Expose a named local lifecycle command selecting Codex and Claude, with explicit auth prerequisites and serial execution. Make required live coverage part of verification for workflow/provider changes, alongside smoke checks.

- [ ] Write/run failing tests for new command or selector wiring where executable behavior changes; document-only evidence updates do not need artificial prose tests.
- [ ] Provide exact commands:

```sh
just codex-login
CODEX_AUTH_VOLUME=omc-e2e-codex-auth just lifecycle-tests
just check
just build
just e2e-tests tests/e2e/test_e2e_smoke.py
```

Choose selector implementation consistently with Task 2, and document provider-specific reruns without making an unavailable selected provider silently disappear.
- [ ] Record baseline and final live versions/results and remaining limits; avoid claiming all future model behavior is guaranteed. Describe supported browser fallback and local/trusted-runner restriction.
- [ ] Run focused tests and commit. Controller runs `omc:check` after task review, then `omc:finish` for rebase/squash/check/build/verify/review/described push. No forge MR creation or host installation.

## Plan hardening

Graph/source review confirms Task 1 extends fixture setup and Task 2 owns a new
conversation boundary; Task 3 modifies the common seed once, not each provider.
Task 4 makes live verification visible without adding model calls to unit tests.
Readiness is not consent proof; neither login status nor missing remote refs is
sufficient alone. The controller's initial probe owns user login interaction;
task implementers must not start competing login sessions. Auth-backed Docker
feasibility is pending, so dependent live tests cannot be declared complete until
that probe succeeds. No architectural alternative requires another approval.


## 2026-09-25 provider scope addendum

The supported providers are Claude Code and Codex only. Remove the retired
provider's runtime adapter, configuration choices and defaults, plugin entry,
installation hints, Docker setup, test parameters, and documentation. No redirect
or compatibility alias is required. Keep the remaining providers' behavior and
the existing investigation-to-implementation authorization boundary intact.

Validate the two-provider registry/configuration and rejection of unsupported
provider values, notification routing, Docker toolchain/setup, and affected E2E
collection. Run the project check/build gates and the required targeted live
Claude/Codex scenarios, including fresh and review integration. Preserve the
no-skip policy and report incomplete live validation explicitly. The committed
wiki fixture is manually pruned for this scope; its historical generation
metadata is retained and does not establish current-source regeneration.
