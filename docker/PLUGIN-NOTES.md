> **Conversational lifecycle E2E (2026-09-24):**
> `tests/e2e/test_e2e_lifecycle.py` exercises the real `omc start` launch and
> persistent provider conversations. Codex uses a container-local PTY server
> (`docker/conversation.py`) and persisted TUI JSONL task boundaries; Claude
> uses `omc start --headless` followed by the named `claude --resume` session
> and stream-JSON result boundaries. Both paths snapshot source, Git index,
> HEAD, bare-origin refs, and spec/plan files after each scenario turn. The
> actor is writable; semantic claims are judged by a separate read-only
> same-provider session at the requested top-tier model. Run account tests
> serially. Set `CODEX_AUTH_VOLUME=omc-e2e-codex-auth` for the dedicated
> account, `CODEX_E2E_MODEL=gpt-6-astra` for the original regression model,
> and optionally `CLAUDE_E2E_MODEL=claude-fable-5-1`. Artifacts under
> `tests/e2e/scenario-artifacts/` are gitignored and omitted from the Docker
> context; they contain only selected, redacted scenario evidence. Default
> tests build the current checkout. A development run may use
> `OMC_E2E_PREBUILT_IMAGE` together with `OMC_E2E_PREBUILT_SOURCE` to record
> the exact older source snapshot, but the final verification must use the
> default checkout build.
>
> **Current E2E setup (2026-09-24):** The image pins Codex CLI 0.156.1.
> `docker/setup-plugins.sh codex` installs `omc@oh-my-clanker` and
> `superpowers@superpowers-marketplace` after registering their marketplaces.
> Runtime setup runs when `configure_omc` selects a provider; generic smoke
> containers do not require unrelated provider plugins. Codex setup validates
> the actual installed paths from `plugin add --json`, the skill files there,
> and the installed OMC start/plan/implement files against this checkout.
> Earlier Codex 0.144.5 observations below describe registration only and are
> historical.
>
## Conversational lifecycle regression evidence (2026-09-24)

The original regression was observed against older product source `7a9eeff`
in a prebuilt image (`sha256:403e13c6b8fba540e78c49771b4822a9e4226491c92193bc5f7b77a22fde50dd`).
Codex CLI 0.156.1 on `gpt-6-astra` entered the urgent `/omc:start` context and,
on the **first turn before a user seed or direct implement request**, changed
`greeting.py`, committed `a52926e`, and pushed
`refs/heads/feature/fix-greeting-hello-world`. Source, HEAD, and bare-origin
snapshots prove the premature work. Its policy was observed as `never` approval
and `danger-full-access` sandbox; `high` reasoning effort was configured but
the turn event exposed no effort value. The original Codex red JSON was
overwritten by a same-label successful rerun before archival, so it cannot be
inspected now. The red facts above were captured in the Task 2 report before
that overwrite; the current `codex-lifecycle-baseline.json` is **not** the
original red artifact. Claude's original `claude-lifecycle.json` still records
the historical red: an Opus 5.5 actor treated the urgent context as the task,
skipped the user's seed request, and asked for approval to implement/push;
the separate Fable 5.1 judge rejected its primer. No product artifact had
changed in that Claude case. These older-image results establish the baseline,
not behavior of the final checkout.

The final product workflow passed all **ten** selected live cases across two
serial default-build runs. There was no single uninterrupted all-green
ten-case invocation:

| Fresh checkout image | Result and recorded artifacts |
| --- | --- |
| `sha256:1e054270b5dd590e8edd107f05576c970e7219907d50919e73ca7f58ca135a7b` (created 2026-09-24T20:11:45Z) | 6 passed, 1 failed. Passed: Codex capability, Codex native `$omc:slug` submission, Claude capability, Claude named resume, and both full start → seed → detail → `ok` → direct implement scenarios. Green records: `codex-lifecycle-green-image-1e054270.json`, `claude-lifecycle-green-image-1e054270.json`. The seventh case asked for more context because the test's critical-scenario prompt was too thin; it made no source/HEAD/remote change. The question was saved as `codex-critical-thin-context-question-image-1e054270.json`. |
| `sha256:d68e385351cdcd6ffab52aea9be1365c2097a024a6ec5b97b477a56429fad570` (created 2026-09-24T20:40:24Z) | 4 passed, 6 deselected in 3091.54 s. After the two edge-case prompts specified the exact broken and desired greeting and regression test, both critical-answer continuations and both failed-build cases passed. Records: `codex-critical-green-image-d68e3853.json`, `claude-critical-green-image-d68e3853.json`, `codex-failing-stage-green-image-d68e3853.json`, `claude-failing-stage-green-image-d68e3853.json`. Workflow and driver source did not change between these images; only the two fixture context strings and the future success-evidence label changed. |

Those JSON names refer to the ignored local
`tests/e2e/scenario-artifacts/` directory. Both full success cases kept
source, Git index, HEAD, and remote refs unchanged through the user's `ok`,
then direct `$omc:implement` (Codex) or `/omc:implement` (Claude) produced a
spec, plan, real child work, stages, and a described pushed commit. Both
critical cases paused dependent work for the exact value and resumed the
original authority after the answer. Both failed-build cases reached `build`,
preserved the external failure, and withheld publication. Readiness evidence
required a real child dispatch and actual installed skill-file access:
Codex's exact file read and Claude's matched successful Read results, rather
than a path claim. The observed CLI versions were Codex 0.156.1 and Claude
2.1.281. Final actors and judges used Codex Astra and Claude Fable 5.1; the
historical Claude Opus baseline is not a controlled comparison with
the Fable actor. The observations cover these CLI/model/image versions and
scenarios, not future model behavior.

The fresh-image runs predate local test-harness repairs: a permanent
bootstrap-trust guard after the actor starts, and a macOS process-group status
probe retry for transient `EPERM`. Focused red-to-green regressions passed for
both. The saved Codex green events contain no bootstrap-trust action in any
of the three workflow cases, so the first repair did not affect those measured
conversations. The process-group repair changes cleanup of a resistant child,
not the OMC workflow or model prompt. Account-backed cases were not repeated
solely for these local repairs.

Final review also repaired the API-only policy setup to use Codex's native
default home when `CODEX_HOME` is absent, and scoped account-volume setup to
tests explicitly selecting Codex. The 74 affected unit tests passed, including
missing/corrupt/locked unrelated auth and selected-Codex failure cases. These
home-path and provider-selection fixes did not repeat the model conversations.
Final finish gates on 2026-09-25 passed: `just check` (623 passed, 62 E2E
deselected), `just build` (format, lint, source distribution and wheel), and
the default fresh-checkout smoke suite (4 passed in 21.49 s). The smoke run
explicitly unset both prebuilt-image overrides, set all three provider token
variables empty, and used the unavailable volume
`CODEX_AUTH_VOLUME=omc-e2e-smoke-no-credentials-20260925`; generic containers
therefore verified their independence from unrelated Codex account state.

### Full E2E regression run (2026-09-25)

The expanded run selected **all 62 E2E cases**, including the expensive wiki
artifact test, with no skips or deselections. Source `3a292e1` was built into
fresh image
`sha256:c38effd212a75c3d2272081310b14ee04fd0a1183b79a05bb2b388896e71c4ee`
(created 2026-09-25T12:16:57Z). Command:

```sh
env -u OMC_E2E_PREBUILT_IMAGE -u OMC_E2E_PREBUILT_SOURCE \
  CODEX_AUTH_VOLUME=omc-e2e-codex-auth \
  CODEX_E2E_MODEL=gpt-6-astra CLAUDE_E2E_MODEL=claude-fable-5-1 \
  just --command uv run pytest tests/e2e -m e2e -vv --tb=short \
  --junitxml=/tmp/omc-full-e2e-20260925.xml
```

Result: **56 passed, 6 failed in 7366.21 s**. This was not a green full-suite
run. Three failures were the Codex ticket slug, unauthenticated-MCP slug, and
headless ticket start: the legacy fixture wrote Jira settings to `~/.codex`
instead of the account-selected `CODEX_HOME`. Claude's normal lifecycle
completed its push but failed the harness's first-global-stage ordering check:
an implementation review preceded the later ordered finish sequence. The
unassigned-ticket test performed the correct assignment and transition but
incorrectly required an intermediate `OMC_TICKET` verdict in final-only CLI
output. Claude capability failed because the stream parser rejected a repeated
result event.

The MCP fixture now respects the selected home and preserves existing config.
The stage assertion requires an intact `check`, `build`, `verify`, `review`
block without mistaking earlier implementation reviews for finish. The ticket
test relies on mutation evidence and additionally checks the actual assignee.
Their 19 focused regression cases passed after observed red failures.

A separate real Claude 2.1.281 background-Agent probe reproduced the parser
failure: a primary result was followed by a same-session result with
`origin: {"kind": "task-notification"}` and the next `result_index`. The
parser now retains the requested turn's primary text and accepts that follow-up
only with completed-background-task evidence, consecutive indices, and distinct
result identities. Repeated primary results, unsupported origins, errors,
malformed follow-ups, and changed sessions still fail. The conversation unit
file passed all 45 cases; a fresh-image live probe passed in 57.21 s and
captured both result events. This probe verifies the parser repair, not the
full suite; the supported-suite follow-up is recorded below.

The wiki artifact test copied back the top-level wiki. Its metadata still
identifies source `152874e9` and generation on 2026-08-24, so this is a cached
historical fixture, not evidence of current-source regeneration. The test
judges topicality and depth; it does not establish freshness or complete
factual accuracy. Known fixture limitations include outdated workflow/Fish
claims and generator commentary. The fixture was subsequently pruned manually
to document the supported Claude/Codex provider scope; its HTML payloads were
synchronized with the Markdown, resolving two prior representation mismatches.
The first run's nested seed copy matched all 26 original artifact files
byte-for-byte; the rerun's nested copy matched all 34 input files. Both were
archived outside the worktree instead of adding redundant copies to the branch.

### Supported-suite follow-up (2026-09-25)

The supported provider scope was clarified to **Claude and Codex only**.
The implementation removes the other adapter, configuration entries, installer
and notification integration, plugin files, Docker installation, and all
tracked documentation references. There is no alias, redirect, or migration.
Exactly five provider-specific E2E parametrizations were removed; all **57**
Claude/Codex and provider-independent cases remain.

The initial serial rerun used image
`sha256:8bd22b9f8366391cc5466b41cfca5d810415281a78f5ee3e6f43048774281995`
(source `d8602eb`). It completed 19 passing cases before being interrupted to
switch to parallel isolated lanes. A fresh-integration case failed because
headless proposals paused after inventory; the active Codex case was interrupted
and its cleanup timeout is not treated as an independent regression. The
integration skill was repaired to produce the complete grounded proposal in
headless mode while preserving interactive approval and zero unapproved writes.
Both unchanged integration E2Es then passed in 105.70 seconds; five isolated
before/after samples and three interactive controls supported the same boundary.

The parallel continuation used image
`sha256:36deda3ebf222fd69a2e24f9142b143807c85a9fd34a58f42bd261633d64a7f2`
(source `d8602eb` plus the integration repair). All six remaining lifecycle
scenarios passed, completing **ten distinct lifecycle successes** together with
the four capability/resume cases in the interrupted serial run. The three Codex
ticket/MCP cases still failed: the home-path fix alone was insufficient. One
failure belonged to a provider subsequently removed from support. These failed
runs remain part of the validation record.

The provider-removal snapshot was built from the current checkout into
`sha256:dde820c61d52e67bbb9daf3549c1a72d8f79167eef5e55dff7885bfd55bf9684`
(created 2026-09-25T15:35:21Z). Its 47 non-lifecycle cases were divided into
isolated lanes. An account-lock collision made the first five-case Codex lane
fail setup; those errors require completed reruns and do not count as passes.
The other lanes completed with 41 passing cases and one ticket-sync assertion
failure. That case correctly made no writes to another user's ticket, but the
test required the internal `assigned-elsewhere` verdict in final-only output;
its replacement must establish actual ticket/current-user reads as well as zero
writes. The ten earlier lifecycle successes are reused because the removal
leaves the supported workflow, launch adapters, and conversation driver unchanged. This is
split-run evidence, not a single fresh 57-case invocation.

Further Codex diagnosis used actual JSON tool events: Jira was configured and
its protocol handshake succeeded, but `getIssue` failed because it required
approval under the `never` policy. The stub omitted MCP tool annotations.
Adding `readOnlyHint: true` and `destructiveHint: false` to its three read-only
tools made the same real call succeed under the unchanged policy. The two
mutation tools retain conservative defaults. No production launch flag or
approval policy was changed; the fixture now describes its read operations
accurately. The metadata regression first failed and then the nine stub unit
cases passed. Subsequent live outcomes are recorded below.

The next fresh image,
`sha256:5b96706d91011fbdd9630483bd0be1252a54d038a9510392b26b2b02d67091ad`
(created 2026-09-25T15:55:48Z), contained the read metadata and audit corrections.
All eight affected Claude cases passed in 338.58 seconds, including the repaired
other-assignee case. The Codex lane still had two passes and three ticket-backed
failures in 174.12 seconds. These failures are retained, not superseded by the
successful direct MCP probe. The actual OMC-launched rollout used the provider's
default `gpt-5.6-terra`; unlike the Astra lifecycle runs, these older tests did
not select `CODEX_E2E_MODEL` explicitly.

The actual failing rollout first attempted to read project and skill guidance
through a shell tool. Docker rejected the nested `bwrap` namespace, after which
the model reported `mcp-missing` without calling Jira. A direct probe after OMC
installed the guidance reproduced the shell failure; the earlier direct probe
had not exercised that path. The lifecycle fixture already configured Codex to
use the disposable Docker container as its execution boundary. The shared
`set_codex_container_policy` fixture now applies that setup to headless Codex
cases too: `approval_policy = "never"`, `sandbox_mode = "danger-full-access"`
inside the disposable selected home. It parses existing TOML first, fails on
conflicts, preserves plugin/MCP/model settings, and is idempotent. The lifecycle
helper reuses it and retains its existing `high` reasoning setting. Production
launch arguments, working directories, and user configuration remain unchanged.
Live policy probes passed actual slug resolution in 62.28 seconds and the
actual Jira HTTP-401 diagnostic in 40.73 seconds; the latter captured the tool
call and error response. Independent review also checked conflicting/malformed
configuration remains unmodified and repeat setup is byte-stable.

The shared-policy image
`sha256:59c71658a7ce82d5b707f4edbe697826938e54edd4e37720a95496758794e128`
passed the Codex capability check, but its two dry-run ticket cases still
returned `mcp-missing`. Headless ticket start reached its judge, which then
failed because the generic Codex judge omitted `--skip-git-repo-check`.
The four-case result was one pass and three failures in 276.19 seconds.
The judge now uses the same explicit read-only sandbox and model selection as
the lifecycle judge, plus the trust-check override for its non-repository cwd.
Its six regression cases cover default/configured model selection, empty-model
refusal, exact launch arguments, and malformed/missing verdict rejection.
Claude's judge invocation and verdict parser are unchanged.

An unchanged slug retry passed, so retry success was not accepted as a fix.
All-outcomes tracing then captured the actual difference: a failing dry-run
successfully read project/skill instructions under the correct container policy,
but returned `mcp-missing` without a tool inventory query or Jira call. Under
the same setup, the authentication-error case searched `ALL_TOOLS`, found
`mcp__jira__getIssue`, called it, and classified the HTTP-401 response correctly.
The slug skill now requires querying the harness's deferred-tool catalog before
reporting a missing resolver. Visible matching tools may still be called
directly; read-only naming, the verdict schema, and free-text handling remain
unchanged. Earlier lifecycle scenarios use free-text greeting tasks, so this
ticket-discovery change does not invalidate those recorded scenarios.

A separate clean wiki-artifact repeat passed in 48.97 seconds after concurrent
artifact writers had finished. The raw output was archived before manual scope
pruning. All 31 HTML page payloads match their Markdown, tree/metadata payloads
match their JSON, pruning is idempotent, and renderer code is unchanged. The
August source/generation provenance remains unchanged; this does not prove fresh
wiki generation. Independent final review closed the concurrent-copyback and
obsolete-notification-example findings.

### Final supported verification evidence (2026-09-25)

All **57 distinct supported E2E cases have passing results** across the recorded
runs. The final affected cases ran against a fresh checkout image,
`sha256:e0dd95063663cc449311d87ebca58912f00b8738f21493b090795311f38a82ee`
(created 2026-09-25T16:25:33Z), containing the reviewed fixture, judge, and slug
discovery repairs. The five Codex cases passed in **306.48 seconds** and the
seven Claude slug/start cases passed in **254.93 seconds**. Captured Codex
traces show actual catalog searches and Jira calls: the success case fetched
the fixture summary, the authentication case received HTTP 401, and the missing
case searched the catalog without finding a Jira resolver.

Four additional independent fresh-container slug-success runs also passed,
producing **five candidate successes in five attempts** including the matrix
case. Every captured sample searched `ALL_TOOLS`, completed `jira.getIssue`,
and recorded an actual read of PROJ-1 with the expected summary. Each additional
run has its own JUnit XML and log in the local
`/tmp/omc-codex-discovery-repeat-manifest.tsv`; failures were not discarded or
retried until green. These samples support the discovery repair for the tested
CLI/model configuration; they are not a guarantee about future model behavior.

The unique-case accounting is:

| Cases | Passing source/image evidence |
| --- | --- |
| 12 | Final image `e0dd9506`: five Codex slug/start cases and seven Claude slug/start cases. |
| 5 | Image `5b96706d`: all Claude ticket-sync cases from the eight-case affected rerun. |
| 1 | Image `59c71658`: Codex conversation capability passed with the shared idempotent container policy. |
| 9 | Images `8bd22b9f` / `36deda3e`: remaining conversation capabilities/resume and all six lifecycle scenarios. |
| 30 | Image `dde820c6`: remaining provider-independent/Claude cases, including the separate clean wiki-copyback repeat. |

This is **split-run evidence, not one uninterrupted fresh 57-case invocation**.
Each later executable change was exercised by affected-case reruns. Independent
review checked the reuse boundaries: the full lifecycle cases use free-text
contexts, the supported actor policy is unchanged by its helper refactor, and
the provider removal preserves Claude/Codex launch behavior. Failed runs,
interrupted runs, account-lock setup errors, and the historical wiki provenance
are retained above. The first Claude rerun wrapper failed before collection
because it used system Python without pytest; the corrected invocation used the
project's uv environment and all selected cases executed.

Final required local gates passed: **683 unit tests**, with the 57 E2Es selected
separately, plus formatting of 121 Python files, lint, source distribution, and
wheel build. The immutable-image provenance records include host/image SHA-256
comparisons for the changed skill, harness, judge, fixture, and test files.
The local per-case ledger is `/tmp/omc-final-supported-coverage.json`; run logs,
JUnit XML, and image provenance files use the `/tmp/omc-*` names recorded during
validation. These local files supplement this committed record and are not
required to run the tests.

Future `save_evidence(label, data)` calls return a unique
`tests/e2e/scenario-artifacts/<label>-<run-id>.json` path; repeated labels
retain both sanitized files. This replaces the historical fixed-name writer
that lost the original Codex red JSON. Credentials and provider homes stay out
of these artifacts and the Docker image. Run account-backed tests serially on
a trusted local machine or private runner, with the default current-checkout
build for final evidence. See the [development commands](../README.md#development)
for both-provider and provider-specific selection; the selected provider
fails loud if its authentication is unavailable.

> **RESOLVED (twice)** — the "failed to load" issue documented below was first
> fixed by qualifying the dependency with its marketplace ("Resolution:
> marketplace-qualified dependency"), which only moved the failure to every
> machine whose superpowers came from a *different* marketplace. The current
> manifest declares **no dependency at all**; omc installs superpowers itself.
> See "Resolution 2: no manifest dependency" at the bottom. The "Decision" and
> "Not investigated further" sections below are the historical record of the
> original investigation and no longer reflect the current manifest or
> `docker/setup-plugins.sh` — `--plugin-dir /repo` is no longer needed.

# Plugin registration in the E2E image — active mechanism

Codex account E2E uses a dedicated Docker named volume. Run `just codex-login`
to perform device authentication; by default it creates and fills
`omc-e2e-codex-auth`. Set `CODEX_AUTH_VOLUME=omc-e2e-codex-auth` in `.env`.
`OPENAI_API_KEY` remains an alternative, and an explicit volume takes
precedence. The fixture mounts the volume at `/codex-auth`, copies only
`auth.json` into an ephemeral `/tmp/omc-codex-home` with mode 0600, checks
`codex login status`, then atomically copies refreshed JSON back while holding
an exclusive lock. It does not use the normal host home or `/artifacts`.

If device login is unavailable, use browser login with a dedicated temporary
home and import only its cache into the same volume:

```sh
(
set -e
codex_login_home=$(mktemp -d)
trap 'rm -rf "$codex_login_home"' EXIT
CODEX_HOME="$codex_login_home" codex login
docker volume create omc-e2e-codex-auth
docker run --rm \
  --mount "type=bind,src=$codex_login_home,dst=/login,readonly" \
  --mount type=volume,src=omc-e2e-codex-auth,dst=/codex-auth \
  node:22-bookworm-slim sh -c 'cp /login/auth.json /codex-auth/auth.json && chmod 600 /codex-auth/auth.json'
)
```

The browser path intentionally uses a fresh `CODEX_HOME`; do not copy the
regular host Codex directory. `codex login status` inside an E2E container
validates the copied account cache. Account tests must run serially because
the refreshed credential file is shared.

`docker/setup-plugins.sh` runs for all providers at image build time
(best-effort) and for only the selected provider at runtime. Inside the
historical `omc-e2e:dev` image it actually succeeded
end-to-end at build time — no network/auth deferral needed:

```
claude plugin marketplace add /repo               -> Successfully added marketplace: oh-my-clanker
claude plugin install omc@oh-my-clanker --scope user -> Successfully installed plugin: omc@oh-my-clanker (scope: user)
claude plugin marketplace add obra/superpowers-marketplace -> Successfully added marketplace: superpowers-marketplace
claude plugin install superpowers@superpowers-marketplace --scope user -> Successfully installed plugin: superpowers@superpowers-marketplace (scope: user)
codex plugin marketplace add /repo                 -> Added marketplace `oh-my-clanker` from /repo
```

## The failure: `omc@oh-my-clanker` installs but fails to load

Despite the successful `install` call above, `claude plugin list` inside the
built image shows:

```
❯ omc@oh-my-clanker
  Version: 0.1.0
  Scope: user
  Status: ✘ failed to load
  Error: Dependency "superpowers@oh-my-clanker" is not installed — run
  `claude plugin install superpowers@oh-my-clanker`, or check that its
  marketplace is added

❯ superpowers@superpowers-marketplace
  Version: 6.1.1
  Scope: user
  Status: ✔ enabled
```

**Root cause:** `.claude-plugin/plugin.json` declares `"dependencies":
["superpowers"]` (a bare name, by design — see `tests/unit/test_plugin_manifests.py::test_claude_plugin_manifest`,
which locks this in and is out of scope for this task to change). Claude
Code's plugin CLI resolves a bare dependency name against the *same
marketplace* as the dependent plugin, i.e. it looks specifically for
`superpowers@oh-my-clanker`. The `oh-my-clanker` marketplace
(`.claude-plugin/marketplace.json`) only lists `omc` — it has no
`superpowers` entry and never will (superpowers is a third-party
marketplace: `obra/superpowers-marketplace`). Cross-marketplace dependency
resolution is not supported by the installed-plugin path, so this dependency
can never be satisfied that way. Confirmed directly:

```
$ claude plugin install superpowers@oh-my-clanker --scope user
✘ Failed to install plugin "superpowers@oh-my-clanker": Plugin "superpowers"
  not found in marketplace "oh-my-clanker". Your local copy may be out of
  date — try `claude plugin marketplace update oh-my-clanker`.
```

Confirmed **order-independent**: uninstalling both plugins and reinstalling
`superpowers@superpowers-marketplace` before `omc@oh-my-clanker` reproduces
the identical "failed to load" status.

This is the scenario the Task 14 brief anticipated: the `install` command
itself runs non-interactively without error (no auth/network prompt), but
the resulting plugin state is unusable — `omc`'s skills/commands are not
served while it's in "failed to load" status.

## Active fallback: `claude --plugin-dir /repo`

Verified working in the built image. `--plugin-dir <path>` ("Load a plugin
from a directory ... for this session only") loads `/repo` directly and does
**not** go through marketplace-scoped dependency resolution:

```
$ claude plugin validate /repo
Validating marketplace manifest: /repo/.claude-plugin/marketplace.json
✔ Validation passed

$ claude --plugin-dir /repo -p "hello"
Not logged in · Please run /login
```

The session gets past plugin loading (no dependency error) and reaches the
expected "not logged in" stage — expected and correct, since this bare image
carries no credentials; auth arrives as env tokens at `docker run` time per
the Dockerfile's top comment. This confirms `--plugin-dir /repo` is a viable
load path unblocked by the dependency issue above.

**Decision: `--plugin-dir /repo` is the active mechanism for Task 16's
seeded-session E2E tests.** Launch the provider CLI with it instead of
relying on the marketplace-installed `omc@oh-my-clanker` plugin, e.g.:

```
claude --plugin-dir /repo -p "<seeded prompt>" ...
```

`docker/setup-plugins.sh` is left as written (per the Task 14 brief) — it
still registers the marketplaces and installs both plugins, which is
harmless, keeps `superpowers@superpowers-marketplace` genuinely enabled, and
gives a real signal in `claude plugin list` for debugging. It is simply not
the path Task 16 should depend on for `omc`'s own skills.

## Not investigated further (out of scope for Task 14)

A real fix likely exists at the manifest layer — e.g. adding a `superpowers`
entry to `.claude-plugin/marketplace.json` that points at the superpowers
source, or a marketplace-qualified dependency string in `plugin.json` if the
CLI's schema supports one. That touches Task 1-13 deliverables (and a
locked-in unit test), so it's flagged here rather than changed.

## Codex plugin paths

Codex does not go through Claude Code's marketplace/dependency system:

- Codex: `codex plugin marketplace add /repo` succeeds
  (`codex plugin list` shows `omc@oh-my-clanker` from marketplace
  `oh-my-clanker`, status `not installed` — registration only, per the task
  brief's interface: "Codex: repo marketplace registration". No install step
  was requested for Codex in `setup-plugins.sh`).

## Resolution: marketplace-qualified dependency

Follow-up experiment, run against `omc-e2e:dev` rebuilt from
`docker/Dockerfile.e2e` after each candidate edit to
`.claude-plugin/plugin.json` (each rebuild ~10s wall-clock — only the
`COPY . /repo` layer onward invalidates; apt/npm/wt layers stay cached).

**Candidate (a) — marketplace-qualified dependency string:**

```diff
- "dependencies": ["superpowers"]
+ "dependencies": ["superpowers@superpowers-marketplace"]
```

Rebuild:

```
$ docker build -f docker/Dockerfile.e2e -t omc-e2e:dev .
...
#12 [ 8/10] RUN bash /repo/docker/setup-plugins.sh || echo "plugin setup deferred to test time"
#12 0.647 Installing plugin "omc@oh-my-clanker"...✔ Successfully installed plugin: omc@oh-my-clanker (scope: user)
#12 2.958 Installing plugin "superpowers@superpowers-marketplace"...✔ Successfully installed plugin: superpowers@superpowers-marketplace (scope: user)
...
real  0m10.3s
```

In-container check, fresh container (`docker run --rm omc-e2e:dev bash -c "bash /repo/docker/setup-plugins.sh; claude plugin list"`):

```
Installed plugins:

  ❯ omc@oh-my-clanker
    Version: 0.1.0
    Scope: user
    Status: ✔ enabled

  ❯ superpowers@superpowers-marketplace
    Version: 6.1.1
    Scope: user
    Status: ✔ enabled
```

**No "failed to load."** This worked on the first try, so candidate (b) (an
object-form dependency, if the CLI schema supports one — would have been
checked via `claude plugin install --help` / `claude plugin --help`) was not
needed.

Reproducibility and independence from the build-time run were both checked
in two more fresh containers:

- `docker run --rm omc-e2e:dev bash -c "claude plugin list"` (**no** re-run of
  `setup-plugins.sh` — i.e. checking the state baked in at `docker build`
  time, not something the container-start re-run papers over): same
  `✔ enabled` result for both plugins.
- A second independent fresh container running the full
  `setup-plugins.sh; claude plugin list` sequence again: identical result,
  plus `claude plugin validate /repo` → `✔ Validation passed`.

**Conclusion:** `.claude-plugin/plugin.json`'s `dependencies` now reads
`["superpowers@superpowers-marketplace"]`. This is the kept, final manifest
shape — `tests/unit/test_plugin_manifests.py::test_claude_plugin_manifest`
was updated to assert it. `omc@oh-my-clanker` loads cleanly with no
dependency error, order-independent, reproducible across fresh containers,
and this is true from the image build itself (no container-start
`setup-plugins.sh` re-run required for the fix to take effect — that
re-run remains useful only for images that skip the build-time step, or to
add `superpowers` when it wasn't present at build time).

**`--plugin-dir /repo` fallback: now obsolete.** With `omc@oh-my-clanker`
loading normally through the standard marketplace-installed-plugin path,
there is no longer a reason for E2E (or any other) sessions to launch via
`claude --plugin-dir /repo` instead of relying on the installed plugin.

## omc update: per-provider plugin update verification (COPS-987)

**Not verified (both providers): whether a running, authenticated agent
session picks up a refreshed snapshot/cache without a restart — live-session
proof stays token-gated and deferred, per the standing live-E2E follow-up.**

`Provider.plugin_update_argvs()` (Task 6) is what `omc update` runs per
provider after the `uv tool upgrade omc` step. This section records the
empirical checks behind each provider's implementation, run against
`omc-e2e:dev` (built from this branch's `docker/Dockerfile.e2e`).
CLI versions in the image: `codex-cli 0.144.5`,
`claude 2.1.212 (Claude Code)`.

### codex — `codex plugin marketplace upgrade` — CONFIRMED, no code change

The image's own `oh-my-clanker` marketplace is registered as a **local
path** (`docker/setup-plugins.sh` runs `codex plugin marketplace add
/repo`). Repro in a fresh container:

```
$ docker run --rm omc-e2e:dev bash -c "
    codex plugin marketplace add /repo
    codex plugin marketplace list
    codex plugin marketplace upgrade
    codex plugin marketplace list
  "
Marketplace `oh-my-clanker` is already added from /repo.
Installed marketplace root: /repo
MARKETPLACE    ROOT
oh-my-clanker  /repo
No configured Git marketplaces to upgrade.
MARKETPLACE    ROOT
oh-my-clanker  /repo
```

`codex plugin marketplace upgrade --help` explains why: *"Refresh configured
Git marketplace snapshots. Omit MARKETPLACE_NAME to upgrade all configured
Git marketplaces."* A marketplace added from a local filesystem path is not
a "Git marketplace" in codex's bookkeeping, so `upgrade` correctly reports
zero Git marketplaces and leaves it untouched — this is expected, not a
bug: the local-path registration only exists in this dev image for
convenience; end users are told (`_PLUGIN_HINTS` in
`src/omc/configure.py`) to run `codex plugin marketplace add
chris-husse/oh-my-clanker`, an `owner/repo` spec, which codex resolves and
tracks as a **Git** marketplace.

To verify `upgrade` actually refreshes a Git marketplace (`file://` is
rejected — `codex plugin marketplace add` only accepts `owner/repo[@ref]`,
an HTTPS/SSH Git URL, or a local path — so a `git+http://` dumb-HTTP mirror
was used to simulate a real remote without needing network access to a real
host):

```
# one container, one session:
git init --bare /tmp/mkt-origin.git
# ... commit "test v1" to .claude-plugin/marketplace.json, push, `git
# update-server-info`, serve /tmp via `python3 -m http.server 8080`

$ codex plugin marketplace add http://localhost:8080/mkt-origin.git
Added marketplace `fake-git-marketplace` from http://localhost:8080/mkt-origin.git.
Installed marketplace root: /root/.codex/.tmp/marketplaces/fake-git-marketplace

# bump origin to "test v2", commit, push, update-server-info again

$ codex plugin marketplace upgrade
Upgraded 1 marketplace(s).
Installed marketplace root: /root/.codex/.tmp/marketplaces/fake-git-marketplace

$ grep -rl "test v2" ~/.codex
/root/.codex/.tmp/marketplaces/fake-git-marketplace/.claude-plugin/marketplace.json
$ grep -rl "test v1" ~/.codex
# (no output — old content is gone)
```

**Conclusion:** `codex plugin marketplace upgrade` (Task 6's wiring) is
correct and needs no change — it refreshes Git-sourced marketplace
snapshots in place, confirmed by content diff before/after. The existing
code comment in `src/omc/providers/codex.py` ("Refreshes ALL configured git
marketplace snapshots") already matches this precisely.
`tests/unit/test_providers.py::test_plugin_update_argvs_are_pure_and_per_provider`'s
assertion (`codex == [["codex", "plugin", "marketplace", "upgrade"]]`) is
unchanged.

### Chain v2 E2E (`tests/e2e/test_e2e_chain.py`)

`test_chain_creates_and_migrates_in_container` drives two scenarios inside a
fresh container: (1) `omc configure --defaults` in a repo with no chain at
all creates the v2 symlinks, gitignore entries, and the project starter
file; (2) the same command in a repo carrying a v1 chain (relative symlinks
into a committed `.omc/internal/AGENTS.md`) migrates it to v2 in place while
preserving the pre-existing `.omc/config/AGENTS.md` content. Both checks run
with `set -e` inside each script block (the brief's original sketch left
that off for the assertion blocks, which would have let an early `test`
failure be masked by the exit code of the last line in the block — see
`docker/PLUGIN-NOTES.md`'s sibling report, `.superpowers/sdd/task-9-report.md`,
for the full note). Passing run: `1 passed in 17.60s`.


## Resolution 2: no manifest dependency (2026-09-02)

The marketplace-qualified dependency above fixed the Docker image, where
`setup-plugins.sh` installs superpowers from `obra/superpowers-marketplace`.
On a real machine superpowers is far more often installed from the official
marketplace (`claude-plugins-official`, pre-registered by Claude Code), and
there the same failure came straight back:

```
❯ omc@oh-my-clanker
  Version: 0.1.4
  Scope: user
  Status: ✘ failed to load
  Error: Dependency "superpowers@superpowers-marketplace" is not installed — run
  `claude plugin install superpowers@superpowers-marketplace`, or check that
  its marketplace is added

❯ superpowers@claude-plugins-official
  Version: 6.3.0
  Scope: user
  Status: ✔ enabled
```

Claude Code matches a declared dependency by its exact `name@marketplace`
id — a superpowers from any other marketplace does not count — and it never
installs the dependency for you. Worse, omc's own probe (`"omc@" in claude
plugin list`) read this state as "ok", so `omc start` launched a session
whose seeded `/omc:start` was "Unknown command".

Reproduced in an isolated `HOME` (claude 2.1.x): superpowers installed from
`anthropics/claude-plugins-official`, then omc from a local marketplace
checkout. With the current manifest, `claude plugin list --json` reports the
dependency error above for `omc@oh-my-clanker`; with `"dependencies"`
removed from `.claude-plugin/plugin.json`, the same sequence reports omc
with no `errors` key. Superpowers from `superpowers-marketplace` works
identically (the image still installs it from there).

**Kept shape:** `.claude-plugin/plugin.json` declares no `dependencies`
(locked in by `tests/unit/test_plugin_manifests.py::test_claude_plugin_manifest`).
`src/omc/plugin.py::ensure_plugin` — run by `omc start`, `omc update` and
`omc configure` — now probes `claude plugin list --json` (an `errors` array
per plugin is the contract), installs superpowers from the official
marketplace when no `superpowers@*` plugin is present, installs omc when
missing, and reinstalls it (after `claude plugin marketplace update`) when it
is present but failed to load. Every mutating path re-probes and raises with
Claude's own error text if the plugin still doesn't load.
