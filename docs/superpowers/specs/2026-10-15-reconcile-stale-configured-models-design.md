# Reconcile stale configured models

Date: 2026-10-15 · Branch: `feature/reconcile-stale-configured-models` · Status: design record

Supersedes, for this one step only, two statements of
`2026-10-06-task-type-model-selector-design.md`: §5 "Outside configure — the
launch and the verb — the list is only read" (the launch now also refreshes
and, on a mismatch, writes) and §10 "No persisted resolved ids" (a remap
persists one full id). Everything else in that record stands: configuration
stays `family[:effort]`, `validate_task_model` stays static, and
`omc internal models` stays read-only. It also retires one sentence of the
generated module docs — "`docsllm` never participates in a normal session
launch" — because the launch now runs its probes.

## 1. Problem and goal

The task-type selector made most configured models *families* — `fable`,
`opus`, `sonnet` on Claude, `astra`, `sol` on Codex — resolved against the
provider's current lineup on every call (`taskmodels.resolve_choice`,
`ClaudeProvider.families`, `CodexProvider.families`). A family survives a
vendor shipping a new generation. Four leaves still rot:

- **R1** a full id typed through "Other" for the orchestrator `model` or a
  `tasks.*` leaf — `taskmodels.resolve_choice` passes it through unresolved,
  and `validate_task_model` is static, so a retired id loads fine and fails
  at launch;
- **R2** a family word the vendor drops — `CodexProvider.families()` and
  `ClaudeProvider.families()` are hard-coded, and `taskmodels._family_model`
  raises `ConfigError "sol is unavailable … available families: none"` for a
  family no listed model carries;
- **R3** `docs_model` on the api documentation backend — persisted as a
  full id by `docsllm.resolve_model`, required to be one by
  `registry.docs_llm_for`;
- **R4** `docs_model` as a full id on the cli documentation backend.

The user's request, verbatim:

> At the start of every omc call that uses providers, list the provider's
> models and map configured models to models that exist. Example: someone
> configured Sol-5.5, OpenAI ships an update and only Sol-6.1 exists — this
> must not break omc. Instead omc asks the provider's DEFAULT model "what is
> the closest thing to Sol-5.5" (same for all other configured models), uses
> that, and updates the config. Two steps: (1) query models (cheap); (2) only
> if incompatible, do the LLM fix with config update.

Goal: omc keeps working when a vendor retires a configured model. A config
naming a vanished id or family no longer breaks a launch — the launch prints
one remap line per stale leaf, rewrites the config, and later launches are
clean. When nothing is stale the check costs no model turn and writes
nothing. Every leaf a command uses is checked: the orchestrator `model`, the
six `tasks.*` leaves and `docs_model`; a blank leaf is checked as the
provider default it resolves to (`Provider.default_task_model`,
`Provider.docs_model_default`).

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 0 | Two steps: a **cheap listing** every time, and an **LLM remap on the provider's default model** only on a mismatch, with the config updated | The user's seed, verbatim |
| 1 | Scope: the four user-facing commands `omc design` (alias `start`), `omc implement`, `omc review`, `omc watch`, plus `omc dependency watch`; every configured model leaf those commands use, documentation models included | User: "don't take it so literal" — watch covers docs/wiki runs and the auto-build |
| 2 | Listing source needs **no API key**: Claude reads the Claude CLI's own on-disk model catalog; Codex reads `models_cache.json` (honours `$CODEX_HOME`); the api docs backend, which has a stored key by definition, lists via the existing `GET /v1/models` (`docsllm.api_connection_probe`) | What each provider already exposes; no new credential path |
| 3 | Config write-back is **automatic**, to the file the value came from, narrated on stderr | LLM config lives only in `~/.omc/config.yaml`: `config/resolve.load_effective` takes `llm` from the global file alone |
| 4 | `omc watch` reconciles at the start of **every tick** | The no-op cost is one file read plus `claude auth status`, measured at 0.5 s |
| 5 | A remap writes a **full model id from the listed catalog**, never a family; validation stays static (`validate_task_model` unchanged) | A later retirement of that id is remapped again by the next launch — the same path, nothing new to maintain |
| 6 | Placement: an **explicit reconcile step at command entry** (`cli/__init__.py` right after `_with_provider`) plus once per watch tick — not lazily inside `task_choice` / `orchestrator` / `docs_llm_for`; `omc internal models` stays read-only | A getter that skills call repeatedly must not hide LLM turns and config writes |

**Amendment to Decision 2 (implementation audit, 2026-10-15, user ruling).**
Claude Code (observed on 2.1.286) writes no model catalog under
`ANTHROPIC_API_KEY` auth — `claude auth status` reports `authMethod:
"api_key"` and no `orgId`, and no catalog appears even after a turn. For that
auth mode only, the Claude CLI catalog is the live `GET /v1/models` list
(`docsllm.api_connection_probe`) fetched with the `ANTHROPIC_API_KEY` the CLI
itself runs on, read from `ctx.env`. No new credential is stored or
configured; account auth keeps the no-key on-disk catalog. The models
endpoint carries no effort data, so such catalog entries have **unknown**
efforts: compatibility is judged on family or id alone, and a remapped
`:effort` is validated statically by `validate_task_model` — unknown never
means unsupported, so a configured `opus:max` stays current with no turn and
no write.

Assumptions accepted with the design: an effort-only mismatch (`opus:max`
where the newest Opus lacks `max`) takes the same remap path, no separate
clamp; `--dry-run` detects and reports stale leaves, runs no turn and writes
nothing; `omc dependency watch` reconciles at command entry only (its ticks
scan `~/.omc` and delegate every documentation run to an `omc internal
dependency document` subprocess, which re-reads the config file).

## 3. Where the check runs

`reconcile.reconcile(ctx, cfg, leaves, *, say, dry_run) -> Config` is called
with the leaf groups a command will use and returns the config the command
continues on. The three groups:

| Command | Leaf group | Why |
|---|---|---|
| `design`/`start`, `implement`, `review` | **session**: the effective provider's `model` and six `tasks.*` | the session runs on the orchestrator and its skills dispatch every task type through `omc internal models`, which rereads the file the remap wrote |
| `watch --enable-documentation`, `dependency watch` | **docs**: `docs_model` of the docs provider (`llm.docs.provider or llm.default`, as `registry.docs_llm_for` resolves it) | the only model a wiki run uses |
| `watch --auto-build` | **orchestrator**: `model` of `llm.default` | `watch._auto_build` runs `taskmodels.orchestrator` |

For design/implement/review the call sits in `cli/__init__._dispatch` right
after `_with_provider`, so the `--claude`/`--codex` override is already
applied (Decision 6). `dependency watch` takes no provider override, so its
call sits at command entry and its returned config is unused (the
documentation subprocess re-reads the file). It runs before
`require_tools` and `ensure_plugin`, so a missing provider CLI surfaces as
`cli_connection_probe`'s "claude CLI not found on PATH" (rc 1) instead of
`require_tools`' install hint a few lines later — accepted; both stop the
command.

`omc watch` has no separate entry call: `run_watch` calls `reconcile` at the
start of every tick, inside the busy lock (a remap turn is busy time, and an
`omc design` waiting for watch idle then sees the rewritten file), and the
first tick is the entry. A `watch` with neither flag uses no model, passes no
leaf group and checks nothing — no auth call, no file read.

## 4. Step 1 — listing (cheap, no model turn)

A per-provider **catalog**: a list of `(id, family, efforts)` with the
cheap tier removed before anything else sees it (`haiku`, `luna` — the same
`_BANNED_FAMILIES` the store refuses). One reader per source, each relying
on the fields below and nothing else; an unreadable shape is a `ConfigError`
naming the file, exactly as `taskmodels._model_list` reports an invalid Codex
list today.

| Provider | Source | Fields | Family of an entry |
|---|---|---|---|
| Codex | `models_cache.json`, read by the existing `taskmodels._model_list`; entries with `visibility == "list"` | `slug`, `display_name`, `visibility`, `priority`, `supported_reasoning_levels[].effort` | the family word in `display_name` (`taskmodels._matches_family`) |
| Claude CLI | the model-catalog file under the CLI's config dir: `<configDirectory>/cache/model-catalog/<orgId>-*-cc.json`, both values from `claude auth status` JSON (verified 2.1.300: `loggedIn`, `orgId`, `configDirectory`, …) | `catalog.config.models[].{id, short_name, thinking.effort_options[].id}` | `short_name`, case-insensitive (`Opus` → `opus`) |
| Claude CLI, API-key auth (`authMethod: "api_key"`, no `orgId`) | `GET /v1/models` with `ANTHROPIC_API_KEY` from the environment (Decision 2 amendment) | `data[].id`; efforts unknown | `api_model_family` prefix |
| Claude api docs backend | `GET /v1/models` with the stored key — `docsllm.api_connection_probe`, already the picker's source | `data[].id` | `api_model_family` prefix (`claude-opus-` → `opus`) |

The live Claude catalog (Claude Code 2.1.300) is `{version: 2, fetchedAt,
staleAt, catalog: {surface: "cc", config: {id, models, settings_vocabulary},
state}}`; each model carries `id`, `name`, `short_name`, `section`
(`main` or `overflow`), `thinking.effort_options` and sometimes
`min_claude_code_version`. Both sections count as listed — every entry is a
model the CLI offers. The file name is the `orgId` followed by a further
token and `-cc.json`, so the reader lists the directory and takes the file
whose name starts with `<orgId>-`; several matches (none observed) → the
newest `fetchedAt`. The `state` object is per-user preference history and is
ignored. Taking `configDirectory` from the same JSON means no second
`CLAUDE_CONFIG_DIR` path builder: the CLI already resolved it.

**Compatible** means, per leaf, with `(effective value, applied effort)`
from the existing `taskmodels.probe_key` (a blank leaf becomes the provider
default; a Claude subagent task applies no effort):

- a family needs at least one catalog model in that family;
- a full id must be listed exactly, Claude's `[1m]` long-context suffix
  stripped before comparing (`_MODEL_TOKEN` accepts it, the catalog never
  carries it);
- an applied effort must be in the efforts of the model the value resolves
  to (the family's newest model, or the listed id); when the source lists no
  effort data (the `GET /v1/models` sources) the effort is not checked here.

A blank Codex `docs_model` resolves to `docs_model_default() == ""` — no
model flag, nothing to check.

Two `docs_model` rules sit before the compatibility check, both because
`registry.docs_llm_for` passes the saved value verbatim as `--model`:
`docs_model` takes no effort, so a saved `:effort` is stale and remaps to a
bare listed id; and on the api docs backend a blank or family `docs_model`
is stale (the backend needs a full id) and is remapped to one.

Narration, one line per provider checked: `✓ claude models current
(8 listed)`.

## 5. Step 2 — refresh, remap, write back (only on a mismatch)

1. **Refresh.** One no-model headless turn — `docsllm.cli_model_probe(ctx,
   provider, "")`, the exact call `taskmodels.validate_selection` already
   makes to refresh the Codex list (verified on codex 0.158: a no-model
   `codex exec` rewrites `models_cache.json`) — then the catalog is re-read.
   A leaf that is compatible with the fresh catalog is done: the cache had
   simply not caught up. This step is what keeps a *brand-new* id safe: a
   user who types a model released that morning through "Other" (configure
   proved it live with `claude -p --model <id>`) must not have it rewritten
   because yesterday's cache does not list it yet. `claude -p` refreshes a
   stale Claude catalog (verified, §8); the api backend's list is live and
   skips this step.
2. **Remap.** One headless turn on the provider's **default model** (no
   `--model`/`-m`; on the live Claude account that is the org-enforced
   `claude-sonnet-5-5`) for all stale leaves of that provider together. The
   turn always goes through the provider CLI, for the api docs backend too:
   omc's only network primitive is `ToolContext.http_get`, and the CLI is
   required by every command in scope anyway. The prompt gives each stale
   leaf's name, role (the schema labels: "Review", "Documentation model", …),
   current value and the catalog as ids with family and supported efforts;
   it asks for one JSON object `{"<leaf>": "<id>[:effort]", …}` and nothing
   else. The answer parser scans output lines for that one object, as
   `slug.parse_verdict` scans for its verdict line — `codex exec` stdout is
   not guaranteed to be the bare answer.
3. **Validate** each answer: it is listed in the catalog, its effort (if
   any) is supported by that model, it is not cheap tier, and a task leaf
   passes `validate_task_model` (a docs leaf passes `docsllm.check_model_id`).
   Anything else fails loud: rc 1, config untouched, the message naming the
   leaf, the answer and the offered list. No retry loop — a second turn
   would not make the list longer.
4. **Write back.** `store.load_global` fresh from disk, set only the
   remapped leaves, `store.save_global` — never a dump of the in-memory
   config, so a `watch` that has run for days cannot overwrite a newer
   `omc configure` edit with its own stale copy. The same leaves are set on
   the in-memory `cfg` the command continues with: the `ProviderConfig`
   objects are shared between the loaded config and the `_with_provider`
   copy, which makes reconcile the first and only mutator of that shared
   state (the copy is still never persisted as a `Config`). One line per
   leaf: `→ tasks.review: sol is unavailable; codex (default model) picked
   gpt-6-astra:high; config updated`. The refresh turn narrates
   `→ refreshing codex model list with the default model` and the probe's
   `✓`/`✗` line.

`--dry-run` stops after step 1 and reports every stale leaf as `· tasks.review:
sol is unavailable (would remap)`; a dry run that finds stale leaves then
exits 0 without the rest of the launch preview (with nothing stale the
preview continues as before).

**Rejected alternative — a deterministic same-family bump.** For R1, R3 and
R4 the stale id usually names a family that still exists (`claude-opus-4-1`
→ `opus`), and "the newest listed model of that family" is what
`docsllm.resolve_model` already does for api aliases and `_family_model` for
Codex families. Making that the remap and keeping the LLM for vanished
families would need a family-token extraction from stale ids plus a "newest"
ordering rule per source — Codex `priority`, the Claude catalog's list order
(semantics unverified), the api list's `created_at`. One turn on the default
model replaces three ordering rules, and it is the mechanism the user asked
for (Decision 0). Recorded under Deliberate complexity.

## 6. Failure handling

- Catalog missing after the refresh turn → `ConfigError` "Claude model
  catalog missing under …/cache/model-catalog; open claude once to fetch it"
  (Codex: today's "open codex once to fetch its model list"), rc 1. Decided
  in the design session: fail loud, never a skip (§8). Under Claude API-key
  auth with no `ANTHROPIC_API_KEY` in the environment → `ConfigError`
  "claude API-key auth has no local model catalog; set ANTHROPIC_API_KEY for
  model reconciliation", rc 1.
- `claude auth status` fails or reports not logged in → the existing
  `cli_connection_probe` message, rc 1.
- The refresh or remap turn fails → rc 1 with the probe's detail (already
  redacted and truncated by `cli_model_probe`).
- Invalid remap answer → rc 1, nothing written (§5.3).
- `omc watch`: the first tick is the command's entry, so its failures above
  are rc 1. On every later tick a reconcile failure (`OmcError`) is
  warn-and-skip, per watch's tick doctrine: one `✗ model reconciliation
  failed: …` line, the previous config kept, the rest of the tick run.
- Two omc processes remapping at once (a watch tick and an `omc design`) →
  last writer wins through `save_global`; both values were validated against
  the same catalog. Accepted, no lock.

## 7. Units

- `src/omc/taskmodels.py` gains the catalog readers beside the one it has:
  `catalog(ctx, provider, *, docs_api_key="") -> list[CatalogModel]` (Codex
  through `_model_list`; Claude through the auth-status JSON and the catalog
  file; api through `docsllm.api_connection_probe`) and `compatible(value,
  effort, catalog) -> bool`, reusing `_matches_family` and `probe_key` in
  place. One module keeps owning "what models exist and what a value
  resolves to".
- `src/omc/reconcile.py` (new): `reconcile(ctx, cfg, leaves, *, say, dry_run)
  -> Config`: collect leaves, step 1, on a mismatch refresh → remap →
  validate → write back. Owns the remap prompt and the answer parser.
- `src/omc/docsllm.py`: `auth_status(ctx, provider) -> dict` returning the
  parsed `claude auth status` JSON with `cli_connection_probe`'s messages on
  failure; `cli_connection_probe` keeps its `(ok, detail)` contract (whether
  it is rebased on the new reader is the plan's call — both are a dozen
  lines).
- `src/omc/toolctx.py`: `list_dir(path) -> list[Path]`, the directory
  sibling of `read_text`, so the catalog pick is faked at the same seam as
  every other provider-state read.
- `cli/__init__.py`: one call per command after `_with_provider` (four
  commands); `watch.py`: one call per tick.
- Provider quirks (catalog path and file-name shape, `auth status` keys,
  whether `claude -p` refreshes the catalog, `codex exec` stdout shape) are
  comments at the code site, per the project convention.

## 8. Edge cases and live-verify items

- **`claude -p` refreshes a stale model catalog — verified.** Live on
  Claude Code 2.1.300, 2026-10-15: the catalog was 70.7 min old (5.3 min past
  `staleAt`; `claude` has no `models` subcommand and `staleAt − fetchedAt` ≈
  65 min); after one `claude -p "Reply with exactly OK."` turn it was 0.1 min
  old. §5.1's refresh therefore works for Claude as it does for Codex.
  Still unobserved: a headless turn *creating* an absent catalog (a machine
  that only ever ran `claude -p` — the E2E containers, whose first Claude
  call is `omc configure`'s probe). Observed during implementation: the E2E
  containers authenticate with `ANTHROPIC_API_KEY`, and under API-key auth
  the CLI writes no catalog at all (Decision 2 amendment), so the containers
  take the `GET /v1/models` path and catalog *creation* under account auth
  remains unobserved. Decided in the design session: a catalog still
  absent after the refresh turn fails loud with "open claude once" (§6) —
  never a narrated skip.
- **A catalog entry newer than the installed CLI.** Entries may carry
  `min_claude_code_version` (live: `claude-opus-5-5` → 2.1.280, installed
  2.1.300). Whether the catalog ever lists a model the fetching CLI cannot
  run is unverified; if it does, the remap path excludes such entries using
  `tool_version(ctx, ["claude", "--version"])` once — remap path only, never
  on the no-op path. Not built unless observed.
- **`codex exec` stdout shape** for the remap answer: the parser tolerates
  surrounding lines (§5.2); verified against the real CLI during
  implementation and recorded at the code site.
- **Family word vanished (R2)**: step 1 finds no model of that family; the
  remap writes a full id. The hard-coded `families()` lists and the
  configure pickers are untouched (§10) — the user sees the new lineup by
  retyping the leaf through "Other", exactly as today.
- **Effort-only mismatch**: same remap path; the prompt carries the
  supported efforts, the answer `<id>:<effort>` is validated like any other.
- **Several catalog files** under `model-catalog/` (one per org the user has
  used): the `orgId` from `auth status` picks the current org's file.
- **Old config files** (no `tasks`, blank leaves): every blank resolves to a
  default family that exists today; nothing is written — except a blank
  `docs_model` on the api docs backend, which needs a full id (§4).
- **`--claude` / `--codex`**: `_with_provider` swaps `llm.default` before the
  call, so only that provider's session leaves are checked.

## 9. Testing

Red to green for every item, per the project's testing policy; stubs run on
the restricted PATH with shell builtins (`tests/unit/_stubs.make_stub`,
`stub_env`); every external integration keeps one E2E driving the real tool.

Unit, `tests/unit/test_taskmodels.py` (catalog readers) and a new
`tests/unit/test_reconcile.py` (a `ToolContext.from_env` over `tmp_path` as
the existing tests build it, stub `claude`/`codex` scripts recording argv,
fixture catalogs copied from the real shapes in §4, the api list through a
faked `ctx.http_get` as `test_docsllm.py` does it):

- all leaves current → no headless call, no write, one `✓` line per provider;
- a stale pinned id → one refresh turn then one remap turn (exact argv
  asserted), the reloaded config differs from the fixture in that leaf and
  nothing else;
- the refresh alone fixes it → no remap turn, no write;
- a vanished family; a stale effort; a stale `docs_model` on the cli and on
  the api backend;
- an off-list, cheap-tier, malformed or unsupported-effort answer → rc 1,
  config byte-identical;
- the `orgId` pick among several catalog files; a missing catalog after the
  refresh → the "open claude once" error;
- `--dry-run` → no turn, no write, the `·` report;
- `--claude`/`--codex` → only that provider is listed;
- `test_watch.py`: `reconcile` called once per tick with the leaf groups
  the flags imply, not at all without them;
- `test_cli.py`: each of the four entry commands calls `reconcile` once
  after `_with_provider` (monkeypatched beside `_load_cfg_or_bail` and the
  `run_*` worker, as the dispatch tests already do); `test_toolctx.py`:
  `list_dir`.

E2E (`tests/e2e`, Docker, real CLI and real LLM): one variation per provider
(Codex opt-in like the rest of its suite) that writes a retired full id into
the container's `~/.omc/config.yaml` directly (`omc configure` would refuse
it — its probe runs the model live) — `claude-opus-4-1`, `gpt-5.1-sol` —
runs `omc design --headless`, and asserts the file now holds an id the
container's catalog lists and that the session launched (for Claude under
the containers' API-key auth, the catalog is the same key's live
`GET /v1/models` list, `tests/e2e/claude_catalog.py`). Unit coverage adds
API-key auth: a configured `opus:max` stays current with no turn and no
write, and a stale id remaps keeping a statically valid effort. One extra model
turn per container; the golden fixtures, pinned to `claude-sonnet-5-5` /
`gpt-6-astra` (both listed today), keep their cost.

## 10. Out of scope

- Refreshing the hard-coded `families()` lists or the configure pickers.
- Changing `omc internal models` (read-only, as decided).
- Locking concurrent writers of `~/.omc/config.yaml`.
- Per-task documentation models; `omc configure`'s own probes.
- Reconciling inside a running session (skills call `omc internal models`,
  which reads what the launch left).

## 11. Worktree `.omc` copy (added at audit, user ruling 2026-10-16)

The implementation briefly carried an unrecorded worktree change (omc running
`wt step copy-ignored` itself, failing a cut whose worktree lacked the
primary's `.env`, and `omc design` seeding `.config/wt.toml`). The user
rejected it: `.env` is never a requirement, copying a repo's ignored files is
its wt config's job, and `omc design` does not touch `.config/wt.toml`. It
was reverted. In its place, by the same ruling:

- After `wt switch` returns a worktree path, `worktree.create_worktree` copies
  the **primary checkout's `.omc`** into it — the one directory omc owns and
  that a later version removes, so no repo's wt config should have to name it.
- The copy is non-destructive (`mirror.copy_missing`): only entries the
  worktree lacks are added, so git-tracked `.omc` files and anything the wt
  config copied win; symlinks are recreated, never followed; a primary `.omc`
  that is itself a symlink becomes the same symlink; a `.omc` the two
  checkouts already share resolves to one directory and is left alone
  (`· .omc shared with the primary checkout`).
- No primary or no primary `.omc` → nothing to do. An `OSError` fails the cut
  through the existing cleanup, which removes only what this run created.
- `check-wt-config` no longer treats a config that excludes `.omc/` as
  broken.

## Implementation review

### 2026-10-16 — Claude (Opus 5.5), `/omc:audit`

| # | Severity | Kind | Record section | Code | Disposition |
|---|----------|------|----------------|------|-------------|
| 1 | Important | code deviates (excluded work added) | §10 / Decision 1 | `src/omc/worktree.py`, `wtconfig.py`, `start.py`, `skills/check-wt-config/SKILL.md` (commit f867721) | User ruling: reverted (no omc-run `copy-ignored`, no `.env` requirement, no `ensure_wt_config` in `omc design`); replaced by the non-destructive `.omc` copy, recorded in §11 (`src/omc/worktree.py` `_copy_primary_omc`, `src/omc/mirror.py` `copy_missing`) |
| 2 | Important | record stale, contradicted Decision 2 | §2, §4, §6, §8, §9 | `src/omc/taskmodels.py` `_claude_catalog` / `_api_catalog` | User ruling: record amended (Decision 2 amendment: Claude API-key auth lists via `GET /v1/models` with the CLI's own `ANTHROPIC_API_KEY`) |
| 3 | Important | code deviates | §1, §4 Compatible | `src/omc/taskmodels.py` `compatible`, `src/omc/reconcile.py` `parse_remap_output` / `build_remap_prompt` | Fixed: catalog efforts unknown (`None`) ≠ unsupported; `opus:max` under API-key auth stays current with no turn and no write; remapped efforts validated by `validate_task_model` |
| 4 | Minor | record stale | §4, §5.3, §8 | `src/omc/reconcile.py` `stale_leaves` | Record amended: `docs_model` takes no effort |
| 5 | Minor | record stale | §1, §8 | `src/omc/reconcile.py` `stale_leaves` | Record amended: blank/family api-backend `docs_model` is stale |
| 6 | Minor | record stale | §2, §5 | `src/omc/reconcile.py` `DryRunStale`, `src/omc/cli/__init__.py` | Record amended: stale dry run exits 0 before the launch preview |
| 7 | Minor | record stale | §5 | `src/omc/reconcile.py` narration | Record amended: "is unavailable" wording and refresh line |
| 8 | Minor | record stale | §3 | `src/omc/cli/__init__.py` `dependency watch` | Record amended: called at command entry, no provider override |
| 9 | Minor | code deviates (unrecorded) | — | `tests/e2e/conversation.py` nested Agent task notifications | Listed: E2E harness fix the reconcile E2E runs needed |
| 10 | Minor | tests weaker than §9 | §9 | `tests/unit/test_reconcile.py` | Listed: no reconcile-level two-provider test; remap argv asserted by prefix, not exactly |

README regenerated (`/regenerate-readme`) for the `.omc` copy and model
reconciliation.

`/omc:finish` review stage, fixed in place: a later-tick reconcile failure
killed `omc watch` (`src/omc/watch.py`; now warn-and-skip, §6); the new E2E's
900 s `run_in` budget exceeded the 300 s rule (`tests/e2e/test_e2e_reconcile.py`,
now 240 s); grug:locality — a dead duplicate cheap-tier check in
`reconcile.parse_remap_output` removed. Listed, not gating: a no-op tick on
the api docs backend makes one `GET /v1/models` call (Decision 4's cost
estimate covers cli sources only); re-entering an existing worktree adds new
`.omc/docs` pages beside older ones until `/omc:rebase-main`;
`parse_remap_output` is long (grug:debuggable).

## Deliberate complexity

- §5, `grug:say-no`: the remap is an LLM turn even when the stale id names a
  family that still exists and "newest of that family" would do. Waived by
  Decision 0: the user asked for the provider's default model to pick the
  closest match, and the deterministic alternative needs a "newest" ordering
  rule per catalog source (§5, rejected alternative).
