# Documentation LLM backend: validated CLI or API key

**Date:** 2026-10-01
**Status:** approved (brainstorm converged; hardened section by section with
/omc:explain — findings folded in below)
**Branch:** `feature/configure-claude-api-key-1password`
**Supersedes in part:** `2026-07-23-per-user-docs-model-config-design.md`
(the docs model stays per provider and gains a backend and a dialog);
extends `2026-07-23-make-omc-configure-project-local-design.md` with a
third, personal, secret-bearing file.

## 1. Problem and goal

GitNexus documentation generation — the project wiki
(`gitnexus._run_wiki`, reached from `omc watch --enable-documentation` and
`omc internal gitnexus refresh --enable-documentation`) and the dependency
wiki (`dependency.run_document`) — always runs `gitnexus wiki --provider
<llm.default>`. For `claude` that is GitNexus's *local CLI* provider: one
`claude -p` process per wiki page through the user's Claude Code login. It
works, but it is slow and clunky. The documentation model is a hidden
per-provider leaf (`llm.providers.<name>.docs_model`, `--set` only) whose
default is the CLI alias `sonnet`.

Goal: let the user choose, in `omc configure`, how documentation is
generated — through the provider **CLI** (today's path, unchanged, still the
default) or through the provider's **HTTP API** with a stored API key — and
which **model**, with the choices fetched from the chosen source and every
choice proven by a real call before it is saved. The key is pasted once,
stored by omc, and displayed masked afterwards.

Explicitly **not** a goal (user decisions): any 1Password integration
(reference resolution at configure time or run time), a cheaper or stronger
default model, routing the API key into interactive sessions, slug
resolution, auto-build or finish stages, a native Anthropic provider inside
GitNexus, or an API backend for codex.

## 2. Configuration

### 2.1 Personal config `~/.omc/config.yaml` (`GlobalConfig`)

`LLMConfig` gains a `docs: DocsConfig` section; `ProviderConfig.docs_model`
stays exactly where it is.

```yaml
schema_version: 1
llm:
  default: claude
  docs:
    provider: ""      # blank = llm.default
    backend: cli      # cli | api   (default cli — today's behaviour)
  providers:
    claude:
      model: ""
      docs_model: ""  # cli: alias or full id, blank = sonnet
                      # api: a full model id, written by configure (§3.2)
notifications: { enabled: false, backend: macos }
```

User-facing dotted keys: `llm.docs.provider`, `llm.docs.backend`,
`llm.providers.<name>.docs_model`.

- `llm.docs.provider` — which configured provider documents. Blank follows
  `llm.default`. A non-blank value is validated against `provider_names()`;
  blank is explicitly allowed (today's `_validate_provider` rejects `""`, so
  this needs a blank-tolerant variant).
- `llm.docs.backend` — closed set `cli` | `api`, default `cli`. `api` is
  refused for a provider whose adapter has no API base URL (§5): `codex:
  API documentation backend not supported yet, use cli`.
- `docs_model` keeps its meaning and its default (`sonnet` on claude, "" on
  codex). The default family does **not** change with the backend. On the
  CLI backend the value is passed through as today (the CLI resolves
  aliases). On the API backend configure always writes a **full model id**
  (§3.2); the run-time resolver (§4) does no alias mapping.

**Where validation runs** (hardening finding — `_hydrate` validates per
class, so a `DocsConfig`-level check cannot see `llm.default`):

- Load path: in `_hydrate`'s `cls is LLMConfig` block, after construction —
  type checks on `docs.provider`/`docs.backend` (`isinstance(str)`, like
  notifications), closed set for `backend`, provider validation, and the
  backend/provider consistency check on `docs.provider or default`.
- Set path: `set_key` gets an explicit `LLMConfig` branch for `docs.*`
  (like the existing `providers` branch) with the per-leaf checks (type,
  closed set, provider name). The cross-field consistency check is
  deliberately **not** run at set time: `--set` pairs apply in arbitrary
  order, and `--set llm.docs.backend=api --set llm.default=claude` must be
  accepted when the on-disk default is codex. Every `--set` run ends in
  `save_global`, where the check runs — same `error: …` rc 1 to the user.
- Save path: `_validate_llm_providers` (called by `save_global`) performs
  the same consistency check. Without this, `--set llm.docs.backend=api
  --set llm.default=codex` (blank docs provider) would pass every set-time
  check and write a file every later `omc` command refuses to load.

Everything else about strictness is unchanged: unknown keys are rejected on
load; omitted fields take dataclass defaults, so every existing
`config.yaml` without a `docs:` section loads unchanged and is rewritten
with `docs:` present on its next save (expected, not a migration).
`schema.py` keeps concrete dataclass field types and no
`from __future__ import annotations` — `_hydrate` relies on
`is_dataclass(f.type)`.

### 2.2 Secrets `<home>/secrets.yaml` (`SecretsConfig`) — new, mode 0600

`home` is `ctx.home` (`OMC_HOME` honoured, tests use temp homes).

```yaml
schema_version: 1
api_keys:
  claude: "<key>"
```

- Separate from `config.yaml` on purpose: `config.yaml` is printed, pasted
  into issues and read by skills; the secrets file never needs to be.
- `SecretsConfig(schema_version: int = 1, api_keys: dict[str, str] =
  field(default_factory=dict, repr=False))`. `repr=False` so a traceback,
  `print(cfg)` or a pytest assertion diff never shows the key. The composite
  `Config` is never passed to `asdict()`.
- Hydration gets its own branch (plain leaves are stored untyped today, so a
  hand-edited `claude: 0x1F` would load as `int`): `api_keys` must be a
  mapping; its keys are validated against `provider_names()` (consistent
  with `llm.providers`); values must be strings satisfying the key rules.
- Key value rules (shared by load and set paths): non-empty, printable
  ASCII, no whitespace or control characters. A value starting with `op://`
  is refused with `paste the key itself — omc does not resolve 1Password
  references`. No vendor-prefix gate (`sk-ant-…` prefixes have changed
  before and would rot). PyYAML `safe_dump` round-trips such strings safely
  and does not fold them (verified; no `width=` or quoting workaround).
- `save_secrets(home, cfg)` is its own writer — `_save_yaml` is non-atomic
  and umask-mode and stays as is for the other files: `mkdir(parents=True,
  exist_ok=True)` of `home` first (a first-ever `--set …api_key=` may run
  before any global save), `tempfile.mkstemp(dir=home)`, explicit
  `os.fchmod(fd, 0o600)`, write, `os.replace`, unlink-on-failure (the
  `awscreds._store` pattern). The inode swap is why a pre-existing 0644
  `secrets.yaml` ends 0600 without touching the old file. Unlike
  `awscreds._prepare_dir`, the **parent directory is not chmodded** —
  `~/.omc` hosts the managed GitNexus clone and manifests.
- `load_secrets(home)`: missing file → empty `SecretsConfig`, never an
  error; unknown keys / wrong types → `ConfigError` naming the file.
- `omc configure --defaults` resets `config.yaml` only and never touches
  `secrets.yaml` (same non-clobber stance as the committed project file).
  `Config` is also the legacy `config.json` hydration class, so
  `load_legacy` **rejects** a `secrets` key explicitly (`ConfigError`) rather
  than hydrating and silently dropping it; migration never writes
  `secrets.yaml`. `Config.secrets` is
  `field(default_factory=SecretsConfig, repr=False)` in addition to
  `api_keys`'s own `repr=False`.
- `omc uninstall` removes `ctx.home`, taking the key with it — except when
  `OMC_HOME` resolves to `/` or `$HOME`, where `run_uninstall` already
  refuses data removal and says so; the key then stays on disk.

### 2.3 Runtime view

`resolve.load_effective(ctx)` composes `Config(llm, notifications,
worktree, secrets)`; the secrets part comes from `load_secrets(ctx.home)`.
Consumers never open the secrets file themselves. `dependency.run_document`
switches from `store.load_global` to `resolve.load_effective` so both wiki
call sites see one composed view; this adds a `git rev-parse` in the
caller's cwd for the (unused) worktree part, which is harmless, and keeps
today's "omc is not configured" message on `None`. `notify.py`,
`installer.py` and `configure.py` keep `load_global`; `probe.require_tools`
and `notify.notify` are typed on the dataclasses and need no change.
Consequence of the same strict stance as `config.yaml`: a malformed
`secrets.yaml` blocks every gated command (`start`, `watch`, `internal
gitnexus refresh`) with `error: …` rc 1. Known downgrade trait, as with
every schema addition: an older omc rejects `llm.docs` as an unknown key.

## 3. `omc configure`

### 3.1 Routing of `--set`

| key | file |
|---|---|
| `worktree.*` | `<repo>/.omc/config.yaml` (unchanged) |
| `llm.providers.<name>.api_key` | `<home>/secrets.yaml` (new) |
| everything else | `<home>/config.yaml` (unchanged) |

`llm.providers.<name>.api_key` is a routing key, not a `ProviderConfig`
field: `set_key` on `GlobalConfig` continues to reject it, and `configure`
peels off exactly the four-segment shape `llm.providers.<name>.api_key`
before dispatch, as it peels off `worktree.*`, tracking a third
`write_secrets` flag; a malformed `llm.providers.api_key` falls through to
`set_key`'s existing "unknown config key" `ConfigError`. On a write it prints `Updated
<home>/secrets.yaml (mode 0600)` on stdout like the existing `Updated …`
lines. The README says plainly that a key passed via `--set` lands in shell
history and is visible in `ps` for the process lifetime, and points at the
interactive prompt.

### 3.2 Validation before any write (unconditional)

Configure **never saves a documentation backend, key or model it has not
just proven to work**. There is no `--no-validate`.

**Trigger** (hardening decision): probes run on the interactive path
always, and on the `--set` path when one of the *documentation keys*
changed relative to the loaded config — `llm.docs.provider`,
`llm.docs.backend`, the docs provider's `docs_model`, or the docs
provider's `api_key`. The docs provider is `docs.provider or default` evaluated on the
**post-apply** config; "changed" compares that provider's value against the
loaded config's value for the same provider name (an entry that did not
exist loads as `""`). `--defaults` **alone** and `--set` of other keys run
no probe (`--defaults --set …api_key=` does). Worked examples: `--set
llm.default=codex` alone → no probe; `--set llm.default=codex --set
llm.providers.codex.docs_model=x` → probe (codex's docs_model changed).
Named gap, accepted: `--set llm.default=codex` with a blank
`llm.docs.provider` changes the *effective* docs provider without a probe,
exactly as today; `--defaults` writes `cli`/blank unprobed, exactly as
today. Making `llm.default` a trigger would run a provider CLI probe in
every E2E container (`harness.configure_omc`) and in existing unit tests.

**Order on the `--set` path:** apply all pairs → consistency checks
(§2.1) → refuse `backend=api` with no key for the docs provider (stored or
set in this run): `ConfigError` (rc 1, not a `Refusal`),
"llm.providers.<name>.api_key is required for the api backend" → probes →
write. A failing probe raises an
`OmcError` subclass (rc 1) whose **message is built redacted**, because
`cli.main` prints it verbatim as `error: …`; nothing is written.

**Which probes run:**

- The docs provider's `api_key` changed → the API connection probe with that
  key, regardless of the backend (otherwise a key set while the backend is
  still `cli` would be saved unproven).
- Backend, provider or model changed → the selected backend's connection
  probe and model probe.
- Interactive path: both probes of the selected backend, always — a kept
  key is re-probed too (a GET is cheap and the user is present).

Two probes per backend live in a new module `src/omc/docsllm.py`. All
external I/O goes through `ToolContext`; `docsllm` itself does no
subprocess or network calls.

**API backend** (claude):

1. *Connection probe* — `ctx.http_get(<base>models?limit=1000, headers)`
   with `x-api-key: <key>` and `anthropic-version: 2023-06-01`. 2xx proves
   the key and yields the model list (the list endpoint is paginated; the
   maximum `limit` is requested and `has_more` is tolerated — the list is
   for the picker and family resolution, not the proof). 401/403 → `key
   rejected by api.anthropic.com (HTTP <status>)`. Other non-2xx → the
   status plus Anthropic's error `message` field, redacted. A personal key
   that spans several workspaces fails here with Anthropic's own message
   about the `anthropic-workspace-id` header — the correct outcome, since
   GitNexus cannot send that header either.
2. *Model resolution* — the dialog's choices are the fetched ids filtered to
   `claude-*`, Haiku ids dropped (tier policy), newest first by
   `created_at`, plus `Other (type a model id)…`. A **family alias**
   (`fable`, `opus`, `sonnet` — from `docs_model`, the dialog default, or
   `--set`) resolves to the newest fetched id whose prefix is the adapter's
   `api_model_family(alias)` (§5). The resolved **full id is what gets
   written to `docs_model`**, so run time is pure pass-through and nothing
   rots when Anthropic ships a new model: the next configure simply
   resolves anew. Default `sonnet` → newest `claude-sonnet-*`.
3. *Model probe* — `ctx.http_get(<base>models/<id>)` must return 2xx. Always
   the per-id GET, never list membership (pagination-proof).

All authenticated GETs, no token spend (user decision: a GET is the proof;
no paid message).

**`ToolContext.http_get(url, *, headers, timeout) -> (status, body)`** is
new and the only HTTP primitive in omc (hardening decision: omc had zero
in-process network I/O; keeping it on `ToolContext` preserves "the single
external boundary", builds the proxy handler from `ctx.env` rather than
`os.environ`, and gives tests one monkeypatch seam like `run`/`run_bounded`).
Implemented with the standard library (`urllib.request`), no new dependency.
It never raises for HTTP status codes; transport errors surface as
`(0, <raw transport text>)` — `ToolContext` never knows the key, so
`docsllm`, the single redactor owner (§3.4), redacts that text before it
becomes a `detail`. The probes take `(key, model, …)` directly, not a
`Config`, so configure (which holds a `GlobalConfig` plus a separate
`SecretsConfig`) never fabricates a composite. The base URL is the constant
`ClaudeProvider.api_base_url()` = `https://api.anthropic.com/v1/`, joined
without producing `//` — there is no test-time URL override; unit tests
fake `http_get`, the E2E hits the real endpoint.

**CLI backend** (any provider):

1. *Connection probe* — `claude auth status` via `ctx.run_bounded`, parsed
   as JSON, must report `loggedIn: true` (verified on claude 2.1.286: a
   JSON object with `loggedIn`, `authMethod`, `apiProvider`). Not logged
   in → `claude is not logged in — run claude auth login` (lowercase "not
logged in" on purpose: the E2E harness fails any output containing the
literal `Not logged in`; keep the casing). Codex has no
   equivalent; its connection probe is folded into the model probe (a
   not-logged-in codex surfaces as the headless call's non-zero exit).
2. *Model probe* — the CLI has no model-listing command, so the choices are
   the adapter's aliases (claude: `fable`, `opus`, `sonnet`) plus a
   free-typed id. The choice is proven by one headless call,
   `provider.headless_argv("Reply with exactly OK.", model=<choice>)`,
   through **`ctx.run_bounded(timeout=120)`** (kills the whole process
   group on expiry; `claude -p` spawns MCP grandchildren that plain `run`
   would orphan); exit 0 and non-empty stdout required. Costs one short
   turn on the user's login and leaves one persisted `-p` session in
   Claude's history. It runs under `ctx.child_env()`: if the user has
   `ANTHROPIC_API_KEY` exported in their shell, Claude Code bills that key
   for the probe — the user's existing setup, not omc's doing.

Both probes mirror `tool_version`'s shape — never raise from the subprocess
or HTTP layer, return `(ok, detail)`; `FileNotFoundError`, `TimeoutError`,
`OSError` become details.

**Narration (stderr, both paths):** `→ checking the key against
api.anthropic.com` then `✓ key accepted (<n> models)` or `✗ key rejected by
api.anthropic.com (HTTP 401)`; `→ validating <model> via <backend>` then
`✓ <model> works` or `✗ <reason>`.

### 3.3 Interactive flow (PTY-driven, `pragma: no cover` like today)

`omc configure` with no flags on a non-TTY stdin stays a `Refusal` (rc 2),
as today — the flow below is reached only on a TTY. `_walkthrough_global`
gains `ctx` and the secrets object; the probe calls
inside it are the same `docsllm` functions unit-tested on the `--set` path.
Inserted between the per-provider session-model picks and the notifications
question:

1. **Documentation provider** — only asked when more than one provider is
   selected; otherwise follows the single one.
2. **Documentation backend** — `CLI (uses your <provider> login)` |
   `API key`. `API key` is not offered for a provider without an API base
   URL.
3. **API key** (only for `API key`) — hidden input (`questionary.password`,
   present in the pinned questionary). The current value is shown as
   `******` + its last four characters (shorter than eight characters: all
   asterisks). Blank keeps it, typing replaces it. Then the connection probe
   runs; on failure the HTTP status is shown, never the key, and the prompt
   repeats. Escape: `None` (Ctrl-C/Esc) or a blank answer after a failed
   probe aborts configure with nothing written — today's `.ask() or
   default` idiom must not silently keep a failed value.
4. **Documentation model** — API: the fetched ids (§3.2 step 2), default the
   resolved current `docs_model` or the newest `claude-sonnet-*`; CLI: the
   aliases plus `Other…`, default `docs_model` or `sonnet`. Then the model
   probe.
5. Only after both probes pass are `config.yaml` and (if a key was entered)
   `secrets.yaml` written, then the existing migration/chain/plugin steps
   run.

### 3.4 What is displayed, what is never displayed

Masked key only, ever. The key never appears in argv, stdout, stderr,
exception text or test output. One redactor — exact-substring replacement
of the key with asterisks, sufficient given the key rules — is applied to
every sink that can echo external text: probe `detail` strings, `OmcError`
messages, `http_get` transport errors, and the existing 400-character wiki
failure tails in `gitnexus.py` and `dependency.py`. **Redact before
truncating** (precedent: `tests/unit/test_dependency.py` on `_redact`).
The existing helpers `dependency._redact` and `gitnexus.redact_userinfo`
strip URL userinfo only and stay as they are.

## 4. Documentation run resolution — one helper, two call sites

`providers/registry.docs_llm_for(cfg) -> DocsRun` **replaces**
`docs_model_for` (graph: exactly two callers, `gitnexus._run_wiki` and
`dependency.run_document`; `tests/unit/test_docs_model.py` is the test file
it replaces). `DocsRun` is a frozen dataclass (house style, like
`Freshness`) carrying: provider name, backend, the wiki argv fragment, child
env additions, a display label, and `redact(text) -> str` (exact-key
replacement; no-op on `cli`). The provider is `cfg.llm.docs.provider or
cfg.llm.default`.

| backend | argv fragment | child env | label |
|---|---|---|---|
| `cli` | `wiki --provider <name> [--model <docs_model>]` — byte-identical to today | — | `<name> cli (<docs_model>)` |
| `api` | `wiki --provider custom --base-url https://api.anthropic.com/v1/ --model <docs_model> --reasoning-model` | `GITNEXUS_API_KEY=<key>` | `<name> api (<docs_model>)` |

On `api`, `docs_model` is already a full id (§3.2); the resolver does no
mapping. Two states are `ConfigError`s at resolution time (reachable only
by hand-editing files; configure never produces them): `api` with no stored
key for the docs provider, and `api` with a blank or alias `docs_model`.
Both messages say `run omc configure`. **Call sites catch them**: `_run_wiki`
narrates `✗ documentation: <message>` and returns `False` like its stall
path (it deliberately returns `True` on a mere wiki failure — the
recomputed verdict decides — and that stays); `omc watch`'s loop has no
`OmcError` guard and must not crash. `run_document` prints `error: …` and
returns 1 as it does today. `probe.require_tools` is unchanged: it probes
only `llm.default`'s CLI, so a docs provider that differs from it on the
`cli` backend is gated by configure's probe; a CLI missing at run time
surfaces as a wiki failure, never a crash.

Both call sites pass `run.extra_env` through the `extra_env` parameter
`run_supervised` already has, so the key reaches exactly one `node` child.
`_run_wiki`'s narration becomes `→ regenerating documentation via claude api
(claude-sonnet-5-5)` / `via claude cli (sonnet)` (dropping `(LLM-heavy)`);
`run_document` prints `· via <label>` to stderr so the per-job log shows
the backend. Both failure tails go through `run.redact` before `[:400]`.

**Why this works with GitNexus 1.6.8 (chris-husse fork) without any
GitNexus change** — verified in the fork's source at commit `8de99dc` (the
E2E image pin; `wiki.ts` and `llm-client.ts` are byte-identical to the
indexed dependency commit `452623e`): `generator.ts:invokeLLM`
routes `claude`/`codex`/`opencode`/`cursor` to local CLI clients and every
other provider, `custom` included, to `llm-client.ts:callLLM`: `POST
<base>/chat/completions` (trailing slash stripped first), `system` + `user`
messages, `max_completion_tokens`, `temperature: 0` for non-`o1/o3` models,
`Authorization: Bearer <key>` — all listed as supported by Anthropic's
OpenAI-compatible endpoint (docs read 2026-10-01). `resolveLLMConfig` takes
the key from `GITNEXUS_API_KEY` ahead of `OPENAI_API_KEY` and any saved
key. `wiki.ts:applyCliConfigOverrides` persists `provider`, `baseUrl`,
`model` to `~/.gitnexus/config.json` and `apiKey` **only when the
`--api-key` flag is passed** — the env key is never written; it also skips
the save when nothing changed, so repeated same-backend runs do not
rewrite the file, and each `cli`↔`api` switch rewrites it once. omc always
passes `--provider`, so GitNexus's non-TTY "No LLM API key found" gate is
bypassed on both backends. In `api` mode GitNexus spawns no LLM CLI (only
`git`; `gh` gist publishing is skipped non-TTY without `--gist`), so the
key reaches no sibling LLM process. GitNexus's error text is `LLM API error
(<status>): <body[:500]>` and never interpolates the key; the redactor is
defence in depth. The `--api-key` flag was rejected: visible in `ps` for
the whole run and persisted to disk by GitNexus.

**Accepted consequences, stated precisely:**

- After an `api` run `~/.gitnexus/config.json` holds `provider: custom`
  with no key, so a bare `gitnexus wiki` typed by hand drops into
  GitNexus's interactive setup wizard on a TTY (it ignores env there on
  purpose) and exits 1 non-interactively unless `GITNEXUS_API_KEY` is
  exported. Previously the saved `provider: claude` made hand runs just
  work. If the user completes the wizard, GitNexus saves that key into its
  own config; omc's env key still wins at run time.
- Switching backend or model does **not** regenerate existing pages:
  GitNexus decides `up-to-date`/incremental on `fromCommit` only and omc's
  `Freshness` ignores `meta.json.model`. The change takes effect as the
  index moves; pages can be mixed-model — same as today's `docs_model`.
- Rate limits: `omc dependency watch` runs up to 8 document jobs, each
  GitNexus at `--concurrency 3`, on one key. GitNexus backs off on 429 and
  retries with `Retry-After`; an exhausted run fails loud and is retried
  next pass. Not a broken key.
- Anthropic documents the compatibility layer as intended for testing and
  comparison, "intended to remain fully functional" but not their
  production priority. Live-verified 2026-10-01: the endpoint returns 400
  `temperature is deprecated for this model` for `claude-sonnet-5-5`, so
  the `api` fragment always carries `--reasoning-model` (GitNexus then
  omits `temperature`; `isReasoningModel: true` is persisted to
  `~/.gitnexus/config.json`, harmless). The E2E (§7) pins the live
  behaviour.

**Run-time failure: no fallback.** A failing API run surfaces GitNexus's
redacted error and the freshness verdict stays stale. `omc watch` records
`knowledge-stale:<codes>` and retries when the codes change, when new
commits land, or on `omc watch --once` (no retry hammer); `omc dependency
watch` retries the dependency on its next pass — exactly today's behaviour
for a failing CLI run. A broken key is loud, never quietly slow.

## 5. Provider adapter

`Provider` (base) gains two pure, defaulted methods — the same growth
pattern as `docs_model_default`, `notification_setup`, `notifies_natively`:

- `api_base_url() -> str` — `""` means "no API backend" (base default);
  `ClaudeProvider` returns `https://api.anthropic.com/v1/` **with** the
  trailing slash (GitNexus strips it; `http_get` callers join without
  `//`).
- `api_model_family(alias) -> str` — `ClaudeProvider` maps `fable` →
  `claude-fable-`, `opus` → `claude-opus-`, `sonnet` → `claude-sonnet-`;
  anything else → `""` (meaning "not a family alias, treat as a full id").
  Base default: `""`.

**`codex.py` is not modified**: the base defaults already yield the §2.1
refusal and the "not an alias" behaviour. Adapters stay pure (argv/env/URL
builders; no I/O) — `docsllm.py` and the registry do the calls. Haiku is
never a family (tier policy). There is no static alias→full-id table
anywhere; the live models list is the only source of full ids (§3.2).

## 6. Behaviour layer and docs

- `skills/gitnexus-document/SKILL.md`: "Python owns the LLM choice — the
  configured documentation provider, backend and model — never the session
  model" (dropping "omc's configured default", since the docs provider can
  now differ from `llm.default`).
- README: the backend/model/key options in the configure section; the
  `--set` keys (`llm.docs.backend`, `llm.docs.provider`,
  `llm.providers.claude.docs_model`, `llm.providers.claude.api_key`); the
  secrets file and its mode; the shell-history and `ps` caveat; that
  configure validates with real calls and refuses to save anything that
  failed; the compatibility-layer caveat and why a failing API run has no
  fallback; the hand-run consequence; that `fable` can pass the probe yet
  fail generation for zero-data-retention organisations. Fix the stale
  README line that calls the docs default `claude-sonnet-5` (code and spec:
  the alias `sonnet`; on the API backend the newest `claude-sonnet-*`).
- `.omc/config/AGENTS.md`, architectural invariants: widen "the only
  subprocess/env boundary … nothing else imports subprocess" to "the only
  subprocess/env/**network** boundary … nothing else imports `subprocess`
  or `urllib`".
- The generated wiki (`.omc/docs/gitnexus/docs/`) regenerates after merge
  via `/omc:document`; the two 2026-07-23 specs stay as history.

## 7. Testing

Red before green; no skips; stub ≠ tested; exact argv over "was called";
timeouts are simulated by monkeypatching `run_bounded` to raise
`TimeoutError` (the `test_installer.py` pattern), never by a sleeping stub.

- **store**: secrets round trip; file mode 0600 after save, also when a
  0644 file pre-existed; missing file → empty; unknown key / non-mapping /
  non-string / unknown provider rejected; key rules (empty, whitespace,
  control char, `op://`) rejected on load and set; `docs.backend` closed
  set, blank-`docs.provider` allowed, `api`-for-codex refused on load and save
  (per-leaf checks on set; the ordering case above); `GlobalConfig` round trip with
  `docs` and without it (defaults). `repr(Config)` never contains the key.
- **registry/adapter** (`tests/unit/test_providers.py`, exact-value style):
  `api_base_url` values and the URL join, `api_model_family` map and `""`
  fallthrough, `docs_llm_for` for both backends (exact argv, exact env,
  label, `redact`), `api` without key / with alias model → `ConfigError`.
  `tests/unit/test_docs_model.py` is migrated to `docs_llm_for`.
- **docsllm**: fake `ctx.http_get` for 200 with a model list (filtering
  `claude-*`, Haiku dropped, newest first; family resolution to the newest
  prefix match; `has_more` tolerated), 401, 403, non-2xx with a message,
  `models/<id>` 2xx/404, transport error; `make_claude_stub` gains an
  additive, default-permissive `auth status` branch (logged in by default,
  kwarg to flip) and an explicit `-p` branch (`headless_reply`,
  `headless_rc`), leaving its generic fallthrough untouched so
  `test_start`/`test_plugin`/`test_installer`/`test_start_mutex` keep
  passing; every failure detail asserted to not contain the key.
- **configure `--set`**: `api_key` lands in `secrets.yaml` and never in
  `config.yaml`; probes run before any write; nothing written when a probe
  fails (both files absent/unchanged); `api_key` alone triggers the key
  probe with backend still `cli`; `backend=api` without key refused before
  any probe (rc 1); unrelated `--set`, `--set llm.default=…` and `--defaults` run
  no probe (the stub records no `auth status` / `-p` call); output contains
  the masked form and never the key; exit code 1 and the redacted
  `error:` line on failure.
- **call sites** (`_run_wiki`, `run_document`): exact argv per backend; the
  `node` stub echoes `$GITNEXUS_API_KEY` so the key is proven present only
  in the child env on `api` and absent on `cli`; key absent from all
  captured stdout/stderr including the redacted failure tail; the
  resolution-time `ConfigError` is caught (watch tick survives,
  `run_document` returns 1).
- **E2E harness**: a `require_env("ANTHROPIC_API_KEY", guidance)` helper
  (or a `var=` parameter on `require_token`) with its own unit test in the
  `test_e2e_codex_auth.py` style, because `require_token("claude")` is
  satisfied by `CLAUDE_CODE_OAUTH_TOKEN` alone.
- **E2E (one, Docker, real tools, marked `e2e` like its template
  `test_document_generates_wiki_docs`)**: requires `ANTHROPIC_API_KEY` in
  `.env` — an OAuth token alone makes the test `pytest.fail` naming the
  variable (never skip). The configure step runs as one `bash -c` script
  so the key is expanded inside the container and never appears in pytest
  argv or output (`run_in` quotes argv via `shlex.join`):
  `omc configure --set llm.docs.backend=api --set
  llm.providers.claude.api_key="$ANTHROPIC_API_KEY"` with real validation.
  Then `omc internal gitnexus refresh --enable-documentation` on the
  two-module toy repo (the same `_run_wiki` path, without a second LLM in
  the loop). Assert: `*.md` under `.omc/docs/gitnexus/docs`; the
  container's `~/.gitnexus/config.json` has `provider: custom`, `baseUrl:
  https://api.anthropic.com/v1/`, `model` = a `claude-sonnet-*` id and
  `isReasoningModel: true` and **no** `apiKey`; the key is absent from `~/.omc/config.yaml` and present
  in `~/.omc/secrets.yaml` with mode `600`. The manual-dispatch E2E
  workflow already wires `secrets.ANTHROPIC_API_KEY`; it must be populated
  before the next dispatch or this test turns it red by design.

## 8. Out of scope / follow-ups

- API key for `claude -p` children (slug, seeded session, auto-build,
  finish stages): never injected. Claude Code honours `ANTHROPIC_API_KEY`
  and would likely bill the API instead of the subscription — unverified,
  hence excluded.
- Native Anthropic provider in GitNexus; codex API backend; 1Password in
  any form; run-time secret resolution; a changed default model family;
  forcing a wiki regeneration on backend/model switch.
