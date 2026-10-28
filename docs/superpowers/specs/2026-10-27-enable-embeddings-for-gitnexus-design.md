# Enable embeddings for GitNexus (opt-in, local runtime)

Date: 2026-10-27 · Slug: `enable-embeddings-for-gitnexus` · Status: design record

## 1. Problem

omc's managed GitNexus (fork chris-husse/GitNexus, 1.6.12, under
`~/.omc/dependencies/gitnexus`) indexes every project with `analyze
--skip-agents-md --skip-skills --branch <base>` (`gitnexus.analyze_argv`).
That argv never carries `--embeddings`, so no index omc has ever built holds
a vector. Every `/omc:explain` query — `omc internal gitnexus
query|context|impact|cypher` — is keyword/FTS only, and GitNexus says so on
stderr on every call:

```
GitNexus [query:vector]: This index has no embedding vectors — results are
keyword-only. Enable embeddings in `.gitnexusrc` (auto-sync honors that file)
or run `gitnexus analyze --embeddings`.
```

A conceptual question ("where is the retry budget for stage gates decided")
only hits when its words appear in a symbol name or docstring; semantic
search is what the graph is for. GitNexus can do it locally today: a
one-time runtime install (`@huggingface/transformers` + `onnxruntime-node`,
pinned in its `package.json` `gitnexusEmbeddingStack`) and the default model
`Snowflake/snowflake-arctic-embed-xs` (384 dims, downloaded into the
Hugging Face cache on first use). omc never asks for it.

Why omc cannot just add `--embeddings` to the argv (rejected approaches,
§2): GitNexus's analyze auto-installs a missing runtime into
`~/.gitnexus/embedding-runtime` from inside a captured, silent subprocess
(`_run_analyze` uses `ctx.run`), and when that install fails analyze exits 1
— which `refresh_knowledge` reads as "still stale after analyze" and
escalates into destroy-and-rebuild. A network hiccup would wipe a healthy
index.

## 2. Goals, non-goals, decisions

Goals:

- **G1 — an omc option.** `gitnexus.embeddings`, global config, DEFAULT OFF,
  settable from the `omc configure` menu and `--set
  gitnexus.embeddings=true|false`.
- **G2 — omc owns the runtime.** When the option is on, omc installs the
  embedding runtime under `~/.omc`, narrated, before any analyze asks for
  it; a failed install degrades to keyword-only and never fails `omc
  update` or the refresh that needed it (`omc watch`, `omc internal
  gitnexus refresh`).
- **G3 — the index gains vectors on the normal refresh path.** With the
  option on and the runtime ready, `refresh_knowledge` embeds — new nodes
  on every incremental analyze, and a one-time backfill for an index that
  already exists. `/omc:explain`'s queries become semantic; the
  "keyword-only" warning disappears.
- **G4 — nothing else moves.** Freshness verdicts, the `OMC_KNOWLEDGE`
  contract, the destroy-and-rebuild escalation and the docs/wiki flow are
  untouched; a repo with the option off indexes exactly as today.

Non-goals: an OpenAI-compatible backend, model selection, dimension
settings (all §8 future work); dependency indexes (`dependency.py`'s
`analyze --index-only`, `/omc:explain-dependency`) — they stay keyword-only;
GPU/CUDA runtimes; a per-project switch.

### Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | **Local backend only** — GitNexus's local ONNX runtime with its default model `Snowflake/snowflake-arctic-embed-xs` (384 dims). OpenAI-compatible backend is future work, not built | User: "for now local only" |
| 2 | **Toggle is a global omc config option `gitnexus.embeddings`** — a new `GitNexusConfig` section on `GlobalConfig` and the runtime `Config`, surfaced in the `omc configure` menu as a "GitNexus" section and via `--set gitnexus.embeddings=true\|false` (only those two values, like `notifications`). **DEFAULT FALSE**; a config file without the section hydrates to off | User seed said on by default; user's later ruling overrides it: "the only thing to change is that it should be disabled by default" |
| 3 | **Not `.gitnexusrc`** | User. omc's one configuration surface is `omc configure`; a per-repo dotfile GitNexus reads behind omc's back is a second source of truth |
| 4 | **Runtime and model cache pinned under `~/.omc`**: `~/.omc/dependencies/gitnexus-embeddings/runtime` (`GITNEXUS_EMBEDDING_RUNTIME_DIR`) and `~/.omc/dependencies/gitnexus-embeddings/hf` (`HF_HOME`). `HF_HOME` reaches every omc child; only GitNexus reads it | User accepted. Everything omc manages lives under `~/.omc`; outside the GitNexus clone so `omc update`'s `checkout -f` never touches it |
| 5 | **A failed runtime install never fails `omc update` / start / watch** — narrated `✗`, indexing continues keyword-only | User accepted. Embeddings are an enhancement; the index is the product |
| 6 | **Turning the option off keeps existing vectors** (GitNexus preserves them when `--embeddings` is absent); `omc watch --reset-gitnexus` wipes | User accepted. Off means "stop spending CPU", not "destroy data" |
| 7 | **E2E image pinned to Node ≥ 22.18** | GitNexus `engines` is `^22.18.0 \|\| >=24.11.0`; the prefix-runtime loader needs `module.registerHooks` (Node ≥ 22.15). The image's `node:22-bookworm-slim` tag floats — pin the minor |
| 8 | **Dependency indexes out of scope** | Accepted. `/omc:explain-dependency` reads per-commit checkouts under `~/.omc`; embedding them is a separate decision with its own disk cost |
| 9 | **`GITNEXUS_LBUG_EXTENSION_INSTALL=auto` set explicitly on the embedding analyze/sync path** | Accepted. The VECTOR extension is a one-time download GitNexus performs under its `auto` default; pinning it makes omc's intent survive a user's inherited `never`/`load-only`, and the read paths stay load-only as GitNexus defines them |
| 10 | **Rejected: let GitNexus's analyze auto-install the runtime** | Install hidden inside a captured, silent subprocess; lands in `~/.gitnexus`; a failure exits analyze 1 and drives `refresh_knowledge` into destroy-and-rebuild (§1) |

## 3. Config: `gitnexus.embeddings`

A new dataclass in `src/omc/config/schema.py`:

```python
@dataclass
class GitNexusConfig:
    embeddings: bool = field(
        default=False,
        metadata={"label": "Embeddings",
                  "help": "Generate local vector embeddings for semantic /omc:explain queries."},
    )
```

added as `gitnexus: GitNexusConfig` to `GlobalConfig` (persisted in
`~/.omc/config.yaml`, label "GitNexus") and to the runtime composite
`Config`; `resolve.load_effective` builds `Config(...)` with explicit
keywords today, so it gains `gitnexus=gcfg.gitnexus` (one line).
`ProjectConfig` does not get it (Decision 2: personal, global — a teammate's
laptop should not be committed into embedding CPU time).

- **Hydration**: `_hydrate` already recurses into nested dataclasses, so a
  `config.yaml` written before this change (no `gitnexus:` key) hydrates to
  `GitNexusConfig()` = off. Its generic leaf branch stores the raw YAML
  value, so the bool gets the explicit type check
  `ProviderConfig.notifications` has: a hand-edited `embeddings: "yes"` is a
  `ConfigError`, not a truthy string.
- **`--set gitnexus.embeddings=true|false`**: `store.set_key` gains a
  `GitNexusConfig` branch mirroring the `notifications` rule — exactly
  `true`/`false`, anything else refused with `gitnexus.embeddings expects
  true or false`. `configure._apply_settings` routes it to the global file
  through the existing `else: store.set_key(gcfg, …)` arm; no new routing.
- **Menu**: `_compose_menu` adds root entries by field name (today `"LLM"`
  and `"Documentation"` from the `llm` field); it gains one root entry
  "GitNexus" with the "Embeddings" toggle, saved through the same
  `leaf`/`guarded_commit` path as every other global leaf. Only the global
  file is written. The menu test that pins the root order grows one entry.
- Legacy `config.json` migration is untouched: a legacy file predates the
  option, so `load_legacy`'s explicit `GlobalConfig(llm=…)` loses nothing.

The option is read in exactly one place, `ensure_embeddings` (§5);
`refresh_knowledge` (§6) consumes its answer. Nothing else branches on it.

## 4. Environment: one function instead of one dict

`GITNEXUS_ENV` in `src/omc/toolctx.py` is today a module constant
`{"GITNEXUS_SHARED_STORE": "off"}` merged by `ToolContext.child_env()`;
`session.py` spreads the same dict into interactive launches. It becomes a
small function of the omc home:

```python
def gitnexus_env(home: Path) -> dict[str, str]:
    base = home / "dependencies" / "gitnexus-embeddings"
    return {
        "GITNEXUS_SHARED_STORE": "off",
        "GITNEXUS_EMBEDDING_RUNTIME_DIR": str(base / "runtime"),
        "HF_HOME": str(base / "hf"),
    }
```

`child_env()` merges `gitnexus_env(self.home)`; `session.py` calls it with
`ctx.home`. The three values are unconditional — they only say *where*
GitNexus would look, never *whether* to embed — so a child launched by an
interactive session (`/omc:index` → `gitnexus-index` skill, or a user
typing `gitnexus analyze --embeddings` by hand) finds the same runtime omc
installed instead of silently populating `~/.gitnexus`. The runtime dir
lives beside, not inside, the GitNexus clone, so `update_gitnexus`'s
`checkout -f -B main origin/main` and a future `rm -rf` of the clone never
touch a few hundred MB of model and ONNX binaries.

`HF_HOME` is a Hugging Face convention, not a GitNexus one; among omc's
children only GitNexus's `@huggingface/transformers` reads it (verified: no
Python dependency of omc imports `huggingface_hub`, and the provider CLIs do
not use it). It overrides a user's own inherited `HF_HOME` for every omc
child — git, wt, uv, the provider CLIs, the plugin probe that
`plugin.py` derives from `child_env()` — none of which read it. Setting it
unconditionally is accepted by Decision 4; the alternative (pass the two
paths only on GitNexus argv) would miss the interactive-session children
this section exists for.

`GITNEXUS_LBUG_EXTENSION_INSTALL=auto` (Decision 9) is **not** in this
function: it belongs only to the two write paths that embed (§6), passed as
`extra_env`, so read-path queries keep GitNexus's load-only default.

## 5. Runtime install: `ensure_embeddings`

New in `src/omc/gitnexus.py`, next to `ensure_gitnexus`:

```
ensure_embeddings(ctx, cfg, *, reinstall=False, say=_say) -> bool   # ready?
```

- **Off** (`cfg.gitnexus.embeddings` false): returns False, runs nothing,
  prints nothing. This is the default and therefore the hot path for every
  user who never opts in.
- **Readiness probe**: both `package.json` manifests
  `<runtime>/node_modules/@huggingface/transformers/package.json` and
  `<runtime>/node_modules/onnxruntime-node/package.json` exist. This is the
  same two-package rule GitNexus's own `resolveEmbeddingRuntime` applies
  (probing transformers alone misreads an interrupted install as ready) and
  costs two `stat`s — the no-call hot path once installed.
- **Install**: when not ready, or `reinstall`, run `node <cli> embeddings
  install` under the standard env (so the runtime lands in the §4 dir).
  GitNexus's CPU-mode install is `npm install --prefix <runtime>
  --ignore-scripts` of the two packages pinned in its `package.json`
  `gitnexusEmbeddingStack`, from the user's npm registry — no postinstall,
  no native build; re-running it against a populated prefix moves the
  packages to the current pins, so omc never passes GitNexus's `--force`
  (that flag only concerns a package-sourced leftover tree). The model
  itself downloads on first embedding (§6). The install prints npm's lines
  and may run minutes — and npm may print nothing at all while it downloads
  — so it goes through the supervised runner of §6, not a captured
  `ctx.run`, with liveness read from the runtime dir changing (file count,
  newest mtime, total size) under the `_WIKI_STALL_SECONDS` budget (300 s):
  `→ installing GitNexus embedding runtime…`, `·` lines off a TTY, then
  `✓ GitNexus embedding runtime installed`, `✗ embedding runtime install
  failed (exit N): <tail> — indexing keyword-only` or `✗ embedding runtime
  install stalled — no progress for 300s; killed — indexing keyword-only`.
  The function returns the probe's answer after the attempt; **it never
  raises and never returns a failing exit code** (Decision 5).

Two callers:

| Caller | Mode |
|---|---|
| `update_gitnexus` (from `installer.run_update`), after a successful build/rebuild of the clone | `reinstall=True` — the pins in `gitnexusEmbeddingStack` may have moved with the clone; an up-to-date clone just probes. `run_update` already loads `GlobalConfig` (possibly `None`: unconfigured machine) before this step; it passes that through, and `None` means off |
| `refresh_knowledge`, at the start of its index phase | probe; installs on first opt-in |

`refresh_knowledge` is the one place that spends the runtime, so it is the
place that makes sure it exists — the way `ensure_gitnexus` sits in front
of every command that spends the CLI. `start`, `watch` boot and the
`internal gitnexus ensure` verb do not change: `watch._tick` enters
`refresh_knowledge` only on new commits, a stale verdict or a forced
refresh, so a failing install is retried per refresh event, never per 30 s
tick, and always narrated. Because the default is off, the first install
happens at the first refresh after a user turns the option on. `omc
configure` does **not** install (it is a config editor; it probes models
today only because the value it writes is a model id it must validate).

## 6. Index flow: embed on refresh, backfill once

All changes are inside `refresh_knowledge` and the helpers it owns; the
freshness verdict (`snapshot_freshness`) does not learn about embeddings.
**Missing vectors never make an index stale** — `OMC_KNOWLEDGE` is unchanged.

**Argv.** `analyze_argv(ctx, base, *, embeddings: bool)` appends
`--embeddings` (GitNexus's default 50 000-node cap kept) when asked.
`refresh_knowledge` decides once per run: `embed = ensure_embeddings(ctx,
cfg, say=say)` (§5) — true only when the option is on and the runtime is
ready after any install attempt. Off never emits the flag (G4: the existing
argv tests keep passing byte-for-byte). `_run_analyze(ctx, root, base,
embed)` is the single analyze call site for both the incremental step and
`_destroy_and_rebuild`, so both paths embed or not by the same decision.

**Running it.** An analyze that embeds is not a captured, silent
`ctx.run`: the first CPU pass over this repo takes minutes and downloads the
model. One small helper — `_run_narrated(ctx, argv, *, cwd, say, extra_env,
heartbeat, stall_after)` — wraps `ctx.run_supervised` and narrates each new,
non-pino line GitNexus prints as `· <line>` (line-start ANSI escapes
stripped); it serves the runtime install (§5), the embedding analyze and
the sync below. Liveness is a heartbeat, not bytes: off a TTY `analyze`
prints only its banner, the `Scope:` / `Incremental:` phase lines and the
closing summary — no embedding progress line (verified on the real CLI) —
so a byte-only supervisor would kill a healthy multi-minute pass. The
embedding analyze and `embeddings sync` are therefore supervised on
`<root>/.gitnexus` changing (file count, newest mtime, total size) under
the `_EMBED_STALL_SECONDS` budget (1800 s); the runtime install on its
runtime dir changing under `_WIKI_STALL_SECONDS` (300 s). Each caller
formats its own `✗ <step> stalled — no progress for <budget>s; killed` or
`✗ <step> failed (exit N): <tail>` line from one shared `_failure_reason`
helper, fed the budget it actually passed. No omc progress bar: GitNexus's
structured `GITNEXUS_PROGRESS` lines are a `wiki`-only feature of the fork.
The embedding write paths carry `extra_env={"GITNEXUS_LBUG_EXTENSION_INSTALL":
"auto"}` (Decision 9). The non-embedding analyze stays `ctx.run` as today.

**Offline fallback.** Inside `_run_analyze`: if the embedding analyze exits
non-zero or stalls, retry **once** without `--embeddings` (narrated `✗
analyze with embeddings failed (exit N): <tail> — retrying keyword-only`,
or `✗ analyze with embeddings stalled — no progress for 1800s; killed —
retrying keyword-only`); the retry's result is what the existing "still
stale after analyze → destroy and rebuild" escalation judges. A model
download failing on a plane must not cost the index (§1); the retry costs
one incremental analyze, which is cheap. The fallback is remembered for the
rest of the run: a destroy-and-rebuild after it runs keyword-only (no second
stall budget under the busy lock), and the backfill below is skipped.

**Backfill.** GitNexus's `analyze` short-circuits "Already up to date" when
`lastCommit` matches HEAD and the tree is clean, whatever flags it carries
(verified in the fork's `run-analyze.ts` fast path: it checks the embedding
*checkpoint*, never the embedding count), and the only analyze that revisits
every node is `--force`, a full rebuild. So an existing, fresh index that
turns the option on would gain vectors one commit at a time, forever. After
the index verdict is clean, the gate is: `embed` **and** the index
metadata's `stats.embeddings` (`read_index_meta`) is 0 or absent **and**
`stats.nodes` is positive (an index with nothing to embed would otherwise
sync to 0 vectors on every refresh) **and** no embedding fallback happened
earlier in the same run. When the gate passes, run `node <cli> embeddings
sync <primary>` through `_run_narrated` (`→ embedding the existing index
(one-time)…`). When only the last condition fails, the run narrates
`· skipping the one-time embedding backfill — embedding failed this run;
the next refresh retries` instead: embedding is demonstrably unavailable,
and a sync would hold the busy lock for the whole stall budget for nothing.
`sync` adds the missing embeddings to a healthy index, checkpoints into the
metadata so an interrupted run resumes on the next refresh, writes
`stats.embeddings` on completion and never touches `lastCommit` (so it
cannot move the freshness verdict either way); its failure is narrated `✗`
(`✗ embeddings sync failed (exit N): <tail>`, `✗ embeddings sync stalled —
no progress for 1800s; killed`, or `✗ embeddings sync exited 0 but
stats.embeddings is still 0`), success `✓ index embedded (<n> vectors)`. A
sync counts as work for `refresh_knowledge`'s closing line — `✓ knowledge
is current` must not follow a minutes-long embedding pass. Once
`stats.embeddings` is positive the branch is a single JSON read per
refresh. This is the whole opt-in path for every repo omc already indexes.
The rule is robust to how much the preceding incremental analyze embedded:
if it already covered every node, the count is positive and sync never
runs.

Store routing (verified in the fork's source for the plan to re-verify
against the real CLI): `embeddings sync [path]` has no `--branch`; it
writes the **flat** slot of `<path>/.gitnexus`. omc's placement invariant
(`store_inverted` heal → `analyze --branch <base>` lands flat for the base
owner) means the base index *is* the flat slot, so sync embeds the index
`/omc:explain` reads. The plan's first task runs `embeddings sync` against
a real index and records the outcome as a comment at the call site, the
way `_gitnexus`'s docstring records the `--branch` resolution rule today.

**Turning off** stops both the flag and the sync; vectors stay (Decision 6).
`--reset-gitnexus` → `_destroy` removes `.gitnexus/` wholesale and the
rebuild re-embeds if on.

## 7. Skills, docs

- `skills/gitnexus-explain/SKILL.md` Step 2 gains one sentence: queries are
  semantic when the index carries vectors; GitNexus's "keyword-only" stderr
  line means the user has not enabled `gitnexus.embeddings` (or the runtime
  is missing / the backfill has not run), and the skill relays that as a
  hint, never tries to fix it.
- `README.md` is regenerated by the `regenerate-readme` skill (never
  hand-edited): the Configure section lists `gitnexus.embeddings` (off by
  default, `omc configure` → GitNexus → Embeddings, or `--set
  gitnexus.embeddings=true`), and "How omc is put together" notes the
  `~/.omc/dependencies/gitnexus-embeddings/{runtime,hf}` layout.
- The behavior layer (`src/omc/distribution/AGENTS.md`) is unchanged:
  `/omc:explain` works the same way; it just answers better.

## 8. Future work (nothing built now)

`GitNexusConfig` grows `embeddings_backend: "local" | "openai"` and
`embeddings_model`; the OpenAI key lives in `secrets.yaml`
(`SecretsConfig.api_keys`, the existing 0600 store). `gitnexus_env` is the
one place that would then emit `GITNEXUS_EMBEDDING_URL` / `_MODEL` /
`_API_KEY` / `_DIMS` (the key via `extra_env` on the two write paths, like
the wiki API key — never in `child_env`). Switching model or backend forces
`--drop-embeddings` + re-embed; GitNexus stamps the index with the embedding
identity and refuses a sync under a foreign one, so mixing vector spaces is
already impossible. Dependency indexes could reuse `ensure_embeddings` and
the `--embeddings` flag verbatim.

## 9. Testing (red → green, no skips)

Unit (`tests/unit/`):

- `test_config_store.py`: `GlobalConfig()` has `gitnexus.embeddings is
  False`; a YAML without the section hydrates off; `embeddings: "yes"` is a
  `ConfigError`; `set_key("gitnexus.embeddings", "true"/"false")` sets the
  bool, `"1"`/`"on"`/`"True"` refused; `set_key("gitnexus", …)` is "a
  section, not a settable key".
- `test_configure.py` / `test_configure_menu.py`: `--set
  gitnexus.embeddings=true` writes only the global file; the menu shows a
  "GitNexus" → "Embeddings" toggle whose commit saves only the global file.
- `test_toolctx.py` / `test_session.py`: `child_env()` under a tmp home
  carries `GITNEXUS_EMBEDDING_RUNTIME_DIR` and `HF_HOME` under
  `<home>/dependencies/gitnexus-embeddings/` and still forces
  `GITNEXUS_SHARED_STORE=off`; the interactive session env carries the same.
- `test_gitnexus_update.py` pattern (real git, recording `node`/`npm` stubs
  on a restricted PATH): option off → no `embeddings` call and no output;
  default `Config()` → no call; on + manifests present → no call; on +
  missing → exactly one `node <cli> embeddings install` argv whose env
  carries the §4 runtime dir, its stub output narrated as `·` lines;
  install exit 1 → narrated `✗`, returns False, `update_gitnexus`'s rc
  unaffected; `update_gitnexus` that rebuilt the clone with the option on →
  install runs even when manifests are present; up-to-date clone → probe
  only; `omc update` with the option off → no `embeddings` argv at all.
- `test_gitnexus_refresh.py` (the `_ctx_with_node_stub` fixtures): exact
  analyze argv for on+ready (`… --branch main --embeddings`), on+missing
  with a failing install stub (one `embeddings install`, then today's argv,
  keyword-only narration), off (today's argv, no `embeddings` call, no
  narration); the embedding argv carries `GITNEXUS_LBUG_EXTENSION_INSTALL=
  auto`, the plain one does not; embedding analyze exit 1 → exactly one
  retry without the flag before any `clean`, on both the incremental and
  the rebuild path; retry stub leaves the index stale → the existing
  destroy-and-rebuild path; `stats.embeddings` absent/0 with a clean
  verdict → one `embeddings sync <root>` argv; `stats.embeddings: 7` → no
  sync; sync exit 1 → narrated, verdict unchanged; a stub printing phase
  lines → each distinct line narrated once as `·` off a TTY, pino JSON
  lines not narrated.

Real-tool E2E (`tests/e2e/test_e2e_gitnexus.py`):

- `docker/Dockerfile.e2e`: `FROM node:22.<pinned>-bookworm-slim` (Decision
  7); after the GitNexus pre-bake and before `COPY . /repo`, run `gitnexus
  embeddings install` and one `analyze --embeddings` on a three-file fixture
  repo with `GITNEXUS_EMBEDDING_RUNTIME_DIR`/`HF_HOME` set to the §4 paths
  under `/root/.omc` — this bakes the runtime, the model and the VECTOR
  extension into a cached layer, so tests need no network for them.
- One new test: `omc configure --set gitnexus.embeddings=true`, `omc
  internal gitnexus refresh` on the real repo; assert
  `.gitnexus/{gitnexus,meta}.json` `stats.embeddings > 0` and that `omc
  internal gitnexus query "<concept>"` exits 0 with no `keyword-only` on
  stderr. Artifacts, not transcripts.

## 10. Risks

- **First-index CPU cost**: minutes on a large repo, every node once;
  narrated, GitNexus's default 50k-node cap kept. Opt-in, so nobody pays
  who did not ask.
- **Busy lock held for minutes**: `internal gitnexus refresh` and the
  watch tick hold `omc-watch-busy.lock` for the whole repair, and `omc
  design` waits on it (`wait_until_idle`, one narrated line). The first
  opt-in refresh — install, embedding analyze, backfill — can hold it for
  minutes. Accepted: the lock exists so a design session never snapshots a
  half-written graph, and the backfill writes the graph; it is paid once.
- **Disk**: runtime + ONNX + model ≈ a few hundred MB under `~/.omc`,
  once per machine. Removed only by hand or with the omc home.
- **GitNexus `EMBEDDING_TEXT_VERSION` bump** (currently `v5`) re-embeds
  everything on the next analyze. Accepted; it is GitNexus's correctness
  rule and the cost is the first-index cost again.
- **`embeddings sync` store routing** — verified from source (§6), to be
  confirmed against the real CLI as the plan's first task. If a GitNexus
  upgrade ever routed `sync` away from the flat slot, the backfill would
  embed the wrong store while the incremental flag kept embedding the right
  one; the §9 E2E (`stats.embeddings > 0` on the flat slot's metadata)
  fails loud on that upgrade.
- **Node floor**: a host on Node < 22.15 has a runtime the loader cannot
  reach; GitNexus fails the embedding analyze deterministically → omc's
  one retry indexes keyword-only and narrates. The host gets a `✗`, not a
  broken index.

## Deliberate complexity

- `grug:cut-point` @ §4 — `HF_HOME` and `GITNEXUS_EMBEDDING_RUNTIME_DIR`
  ride `child_env()` to every omc child although only GitNexus reads them.
  Waived under Decision 4: the narrower per-invocation `extra_env` would not
  reach the GitNexus children an interactive session spawns (`/omc:index`,
  a hand-typed `gitnexus analyze --embeddings`), which would then populate
  `~/.gitnexus` behind omc's back.
