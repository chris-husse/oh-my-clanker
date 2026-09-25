# Preserve OMC workflow scope and verify Codex in Docker

## 1. Problem and intended lifecycle

OMC loaded the start and brainstorming instructions during the reported Codex
session, but the agent treated imperative task context as authorization to
implement and push. Code-correctness tests passed without testing that boundary.
The remedy is a consistent command contract plus real behavioral regression
coverage, not an additional routine approval system.

`omc start <context>` supplies investigation data. Every byte of `<context>` is
context, including imperatives, quoted instructions, embedded `/omc:implement`,
and delimiter-looking text. It never changes the workflow or authorizes coding.
The session gathers initial context with explain, presents the primer, asks for
the user's seed, resolves material initial scope questions, and hands the full
primer/seed/answers to brainstorming. Brainstorming presents a complete solution
and explores the user's questions until agreement. Agreement and short replies
such as `ok` do not advance to implementation by themselves.

A subsequent direct user `/omc:implement` is the deliberate handoff: write and
harden the spec, write and pressure-test the plan, execute using subagents, and
finish through the project stages and a described push ready for MR review.
Only critical unanswered questions or genuine blockers interrupt that flow;
routine spec, plan, task, and stage reapproval is not required. The conductor
must retain this authorization after a critical question is answered.

## 2. Shared instructions and launch context

Put the lifecycle boundary prominently in the shipped behavior layer. Scope
continuation, task lists, sub-skill verdicts, and finish requirements to the
currently authorized phase. A pending required user answer is a valid stopping
point; an asynchronous question being accepted by the UI is not an answer.
Start/plan lists must include waiting for the seed and the implementation
handoff, rather than silently adding code/build/push as start work.

The start seed retains the `/omc:start` command prefix for native Claude skill
loading. Serialize the arbitrary context as a JSON string in an explicitly
labelled data envelope with fixed instructions outside it. Test exact round-trip
preservation for newlines, quotes, Unicode, delimiters and embedded commands.
Do not interpolate arbitrary input into a workflow instruction. The start skill
supports the envelope and existing direct arguments, with identical semantics.
Slug generation also consumes task context as data, solely to name the work.

Do not change the allowed bootstrap actions: worktree preparation, base refresh,
notification wiring and existing ticket-sync rules remain available in start.
The no-implementation boundary applies to product changes, tests for a proposed
fix, design/plan commits, and publication before the later user handoff.
These are agent instructions, not a security sandbox or a runtime state machine.

## 3. Account-backed local Codex readiness

Extend the Docker test harness to accept an explicitly selected dedicated named
Codex authentication volume, initialized using the supported device login.
No OpenAI API key is required. Keep API-key mode as an alternative for existing
private automation. Never copy the ordinary host Codex home, put credentials
in images, or export auth/config directories as test artifacts.

Persist authentication updates across serial runs, but isolate each test's
configuration, plugins and sessions. Mount the auth volume at a separate path,
copy only auth.json into the ephemeral test home, and copy refreshed auth back
on teardown, including failures. Serialize account-backed tests with a lock
held for the entire authentication lifecycle; refuse parallel workers clearly.
Do not treat existence of a file or login status as proof of usable access:
run a bounded live readiness turn and require its expected artifact. A missing
or expired login fails the selected suite with an exact login command.
Account-backed execution is local or on a trusted private runner, never public
CI with exported account credentials. Initial feasibility is a real container
on Codex 0.156.1; record versions rather than assume future CLI compatibility.

Install OMC and Superpowers through each CLI's supported plugin commands.
Registration alone is not installation. Check command exit codes and installed
enabled state; fail with useful diagnostics rather than swallowing errors.
Readiness proves skills loaded and subagents can execute, before relying on
those capabilities in the lifecycle assertions. Keep provider prerequisites
scoped so token-free smoke checks do not require live account access.

## 4. Real conversational regression driver

Drive the actual Codex TUI through a pseudo-terminal in a disposable,
write-capable Docker container, not `codex exec` as a substitute for the TUI.
Use the same CLI launch path as `omc start` and a disposable repository with a
local bare origin. The container is the sandbox; it contains no host source
mount or forge credentials. Establish turn boundaries using persisted session
events, not terminal silence or fixed sleeps. Preserve the same session over
user turns and make timeouts fail with bounded diagnostics.

Capture only scenario session/tool events, terminal output, relevant repository
snapshots and CLI/model/plugin versions in a gitignored artifact directory.
Do not archive the provider home. Drain PTY output continuously to prevent
blocking and terminate child processes on failure. Separate bootstrap changes
from product state, and snapshot source bytes, index, HEAD, spec/plan files and
remote refs after every conversation boundary. A same-provider judge may assess
primer quality or question intent that artifacts cannot express; it cannot
replace deterministic mutation and publication checks.

Use equivalent scenario requirements for Claude. Its provider-specific adapter
may use its supported persistent conversational interface, but must preserve
real skill loading, full tool capability and observable turn boundaries.
No canned assistant responses, stub LLMs or skipped selected tests.

## 5. Scenarios and acceptance

1. Start context combines urgency, an obvious small fix, and imperative or
   embedded implementation instructions. The agent investigates, presents
   context and asks for a seed. No product mutation, implementation commit or
   remote feature ref appears. A missing prerequisite does not count as pass.
2. A seed and material scope answers feed brainstorming. An approach discussion
   and an `ok` preserve the handoff boundary. The full solution is presented.
3. A later direct `/omc:implement` produces a spec and plan, real subagent work,
   the requested product behavior, passing stage artifacts and one described
   commit on the local bare remote, without routine reapproval.
4. A critical unresolved requirement causes a question and no dependent change;
   after the answer, the already-authorized implementation resumes.
5. A failing required stage prevents publication. Assert the failing stage ran
   and the feature ref was not pushed; do not count an unrelated early failure.

Run the pre-fix contract against the live regression before changing skills and
record the actual failure. If the captured session's violation cannot be
reproduced in the live environment, report that limitation rather than claim a
live red. Unit tests must independently fail for seed/readiness/driver defects
before their fixes. Final validation includes full `omc:check`, build, smoke
verification, selected live Codex lifecycle tests and equivalent Claude tests.
A required unavailable account is a blocker, never a skip or a false pass.

## 6. Architectural hardening and scope

Graph evidence: `src/omc/start.py:run_start` owns the shared seed before calling
provider argv builders; `Provider.session_argv` is a pure formatting boundary.
`tests/e2e/conftest.py:_finish_container_setup` owns container plugin/auth setup;
`tests/e2e/harness.py:require_token` currently accepts only OPENAI_API_KEY for
Codex. `make_work_repo` already supplies a local bare origin. Reuse those seams
and keep process work outside production except through `ToolContext`.

Source inspection confirms current setup ignores plugin/login exit codes,
Codex marketplace registration is incomplete, interactive tests do not drive a
conversation, and finish tests cover only Claude. Unit string-presence checks
cannot establish user consent. Dedicated driver/auth helper modules keep these
concerns separate from the existing generic exec helper.

The change keeps the existing fish-tab-title correction on this branch. It does
not install OMC into the host, create a forge MR, merge, or introduce a runtime
approval engine. If corrected instructions fail behavioral coverage, surface
that evidence and revisit enforcement as a critical design finding.

Hardening results by section: (1) the lifecycle resides in skills, not a Python
state machine; (2) both interactive and headless start use the same seed, while
slug has a separate prompt that also needs data treatment; (3) named-volume
copy-in/copy-out avoids persisting unrelated test state, and browser login with
a dedicated temporary CODEX_HOME is the supported fallback when device login
is unavailable; (4) the graph has no conversational driver, so a new test-only
module is appropriate; (5) existing finish artifacts can be reused but cannot
prove earlier approval; (6) the combined change preserves provider argv purity
and the ToolContext boundary. Whole-spec review found no architectural conflict.
Baseline: `just check` passed 543 tests (52 E2E deselected). Live auth remains
pending during specification; no live regression result is claimed yet.


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
