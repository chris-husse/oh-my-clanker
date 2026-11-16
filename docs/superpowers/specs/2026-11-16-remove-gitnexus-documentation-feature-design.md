# Remove generated documentation: omc's knowledge is the GitNexus graph only

Date: 2026-11-16 · Status: design · Branch: `feature/remove-gitnexus-documentation-feature` · Slug: `remove-gitnexus-documentation-feature`

Removes what these records introduced: `2026-07-17-omc-gitnexus-design.md`
(the `/omc:document` half), `2026-07-22-gitnexus-dependency-index-explain-design.md`
(the dependency docs half), `2026-07-23-per-user-docs-model-config-design.md`,
`2026-10-01-configure-claude-api-key-1password-design.md` (the API
documentation backend and `secrets.yaml`), and the wiki half of
`2026-09-27-stale-knowledge-snapshot-self-heal-design.md`. Those records stay
in the tree as history and are not edited.

## 1. Problem

omc builds two kinds of knowledge about a project: the GitNexus graph (index,
optional embeddings, queried by `/omc:explain` and `/omc:explain-dependency`)
and LLM-written documentation generated from that graph by `gitnexus wiki`
(the "wiki", mirrored to `.omc/docs/gitnexus/docs/`). User request: "Remove
the GitNexus documentation feature entirely from this process."

The documentation half is opt-in (`omc watch --enable-documentation`,
`/omc:document`, `omc dependency watch`), costs real LLM money per module, and
runs for tens of minutes. For that it carries a large surface:

- a wiki engine and its supervision (`src/omc/gitnexus.py:_run_wiki`,
  `src/omc/wikirun.py`), three `wiki-*` freshness reasons, and a docs mirror
  that every exit of `refresh_knowledge` has to keep consistent;
- a second engine for dependencies (`dependency.run_document`, the parallel
  `_document_batch` in `src/omc/depwatch.py`, an `OMC_PROGRESS` line protocol);
- its own configuration: `llm.docs.provider`, `llm.docs.backend`,
  `docs_model` per provider, API keys in `~/.omc/secrets.yaml`, a configure
  menu, configure-time probes, and a `docs` group in model reconciliation;
- a worktree mirror: `omc internal rebase-main` re-mirrors `.omc/docs/` from
  the primary and reports it in `OMC_REBASE_MAIN`;
- wording in the behavior layer and in a dozen skills that tells every agent
  about generated docs.

It also makes the freshness verdict wrong for the common case. Every reader
of the verdict (`omc design`, `omc internal gitnexus status`, the
`knowledge` field of `OMC_REBASE_MAIN`, the `OMC_KNOWLEDGE` line behind
`/omc:explain`) calls `snapshot_freshness` with `documentation=True`, its
default. A project with a current index and no generated docs is therefore
reported stale (`wiki-missing`, "no generated docs yet") with the fix
`omc watch --once --enable-documentation`, although the user never asked for
docs.

## 2. Decisions taken during brainstorm

| # | Decision | Answer | Why |
|---|----------|--------|-----|
| D1 | Does dependency documentation go too? | Yes: the dependency `document` verb, the documenting half of `omc dependency watch`, and the `documented` / `docs` fields | "Entirely". A dependency wiki is the same engine and the same cost; keeping it would keep `wikirun.py`, the docs LLM config and the `docs` reconcile group alive for one caller |
| D2 | Does the docs-LLM configuration go? | Yes, all of it: `llm.docs.*`, `docs_model`, `secrets.yaml` API keys, the docs-only parts of `docsllm.py`, the configure "Documentation" menu, the reconcile `docs` group | With no documentation run there is nothing left to configure, probe or reconcile |
| D3 | Does the `.omc/docs/` worktree mirror go? | Yes: the snapshot parts of `mirror.py`, the mirror step of `omc internal rebase-main`, and the behavior-layer wording | The mirror exists only to carry generated docs into worktrees |
| D4 | What happens to files and config keys already on disk? | omc leaves them alone. `.gitnexus/wiki/`, `.omc/docs/`, `~/.omc/gitnexus/…/docs`, `~/.omc/secrets.yaml` are not deleted by any upgrade or cleanup code. Exception (amended at audit, a pre-existing behavior): `gitnexus clean --force`, run by `omc watch --reset-gitnexus` and by a heal that escalates to destroy-and-rebuild, removes the whole `.gitnexus/` store, so a leftover `.gitnexus/wiki/` goes with it; the README upgrade note says so. Stale config keys fall to the existing "ignoring unknown key(s)" notice of `config/store.py:_hydrate` | omc never deletes user files on an upgrade; the notice already exists for exactly this case |
| D5 | `--enable-documentation` after the change | Removed. argparse rejects it; no deprecated no-op | A silent no-op would let a script believe it still refreshes docs |
| D6 | `/omc:document` after the change | Deleted, with `gitnexus-document`. No placeholder skill | A placeholder is one more skill every session lists |
| D7 | `secrets.yaml` once nothing reads it | No code. One upgrade note in the README | Decision D4: omc does not clean up; the user is told the file may be deleted |
| D8 | The surviving half of `docsllm.py` | Renamed to `modelprobe.py` | What survives probes session and task models; "docs" in the name would be false |
| D9 | `OMC_REBASE_MAIN` | Drops the `synced` and `shared` keys | They report the mirror; with no mirror they would always be empty lists |

## 3. Goals, non-goals, anti-goals

Goals:

- After the change no code path, CLI flag, config key, skill, verdict field
  or agent-facing text generates, mirrors, validates or points at generated
  documentation. `.omc/docs/` survives only as a path to ignore: in
  `.gitignore` and in the skip lists of the two review lenses.
- Every graph feature works as before: indexing, the incremental refresh and
  its self-heal, embeddings, the freshness verdict for the index, `/omc:index`,
  `/omc:explain`, `/omc:explain-dependency`, dependency indexing.
- The freshness verdict judges the index only, so an indexed project is fresh
  when its index is.

Non-goals:

- Any change to the GitNexus fork. Its `wiki` command goes unused; omc still
  needs the fork for `analyze --branch`. The fork pin and the comments in
  `docker/Dockerfile.e2e` that describe what the pinned merge carries stay.
- Editing historical design records and plans under `docs/superpowers/`.
- Replacing generated docs with anything. Hand-written project docs reach
  `/omc:explain` through the project's `explain-context` skill, as today.

Anti-goals:

- omc never deletes a user's leftover files (D4; see D4 for the `clean --force` exception).
- No regression in indexing, embeddings, index freshness, or session and task
  model reconciliation.
- The probe code that model reconciliation and the task-model pickers share
  with the docs backend must not be lost by deleting `docsllm.py` wholesale
  (§5.7).

## 4. Approaches considered

- **A. Full removal (chosen).** Delete the engines, the config, the mirror,
  the skills and the wording.
- **B. Hide it.** Remove the skills and the flag, keep the engine dormant.
  Rejected: the request says "entirely", and dormant code still has to be
  read, tested and kept consistent with every refresh exit.
- **C. Remove the project wiki, keep dependency docs.** Rejected by D1.

## 5. Design

The change is a deletion. Each subsection names what goes, what stays, and
the one place where the remaining code changes shape. File and symbol names
were checked against the tree at `5e5b6c0`.

### 5.1 Skills

`skills/document/` and `skills/gitnexus-document/` are deleted. The plugin
manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`) do not
list skills, so they do not change; the skill inventory in
`tests/unit/test_plugin_manifests.py` and its two guards for these skills
(`test_gitnexus_document_contract`, the `document` half of
`test_index_and_document_delegate`) do.

### 5.2 CLI and watch

`--enable-documentation` is removed from `omc watch`
(`src/omc/cli/__init__.py`) and from `omc internal gitnexus refresh`
(`src/omc/internal.py`, parser and usage line). argparse then refuses the
flag: `omc watch` exits 2 with argparse's own message, the internal verb
prints its usage and returns 2 as it does for any unknown argument.

`src/omc/watch.py` loses the `enable_documentation` parameter through
`run_watch`, the tick functions and `_refresh_index`, the "documentation
enabled" banner text, and the `docs` entry of the per-tick model groups.
A watch without `--auto-build` therefore reconciles no model at all.
`--reset-gitnexus` force-clears the GitNexus store (`.gitnexus/`) and rebuilds the index; its help text says so and no longer mentions wiki or docs mirror.

`omc dependency watch` no longer calls `reconcile(ctx, cfg, {"docs"}, …)`
before its loop: indexing a dependency uses no LLM. The config load in front
of that call existed only to feed it and goes with it, so the command runs
like `omc dependency list` already does, without reading `config.yaml`. The
`omc internal dependency ensure` children it spawns never read the config.

### 5.3 The wiki engine and the freshness verdict (`src/omc/gitnexus.py`)

Deleted: `_run_wiki`, `read_wiki_meta`, `WIKI_META_REL`, `_clear_mirror`, the
mirror bookkeeping in `refresh_knowledge` (`mirror_cleared`, the
restore-on-exit and "docs mirror cleared" lines in `finish`), the
`documentation` parameter of `refresh_knowledge` and `snapshot_freshness`,
the three reasons `wiki-missing`, `wiki-unknown`, `wiki-behind`, and
`Freshness.wiki_codes`. `Freshness.index_codes` existed only to separate the
two families; its callers read `codes()`.

`refresh_knowledge` becomes: optional reset, embeddings readiness, verdict,
incremental analyze with the existing escalation to destroy-and-rebuild,
the one-time embedding backfill, verdict. `_destroy_and_rebuild` returns
whether it healed, nothing else. The verdict's `fix` is always
`omc watch --once` when anything is stale.

The JSON shape of the verdict (`fresh`, `basis`, `reasons`, `fix`, `run_in`)
does not change; the set of reason codes shrinks to the index ones. This
fixes the false stale reported in §1: an indexed project with no wiki is
fresh.

`src/omc/start.py:render_knowledge_alert` drops its index-before-wiki
ordering and prints the reasons in verdict order.

### 5.4 `src/omc/wikirun.py`

The module is deleted with `PageCountTracker`, `GitNexusProgress`,
`wiki_failure_excerpt` and `PROGRESS_PREFIX`. Two constants in it are not
about the wiki at all: `_WIKI_STALL_SECONDS` (300 s without progress marks a
wedged child) and `_WIKI_POLL_SECONDS` are the default stall budget and poll
interval of `gitnexus._run_narrated` and the budget of `ensure_embeddings`'s
runtime install. They move into `gitnexus.py` under names that do not say
"wiki", next to `_EMBED_STALL_SECONDS`, with the comment reduced to what is
still true. `ToolContext.run_supervised` stays; `_run_narrated` uses it.

### 5.5 Dependency documentation

`src/omc/dependency.py` loses `run_document`, `docs_dir`, the `OMC_PROGRESS`
emitter and its config load; `omc internal dependency` accepts `ensure` and
`list` only, and `document` gets the usage line and rc 2 like any unknown
verb. The `OMC_DEPENDENCY` verdict of `ensure` loses `docs` and `documented`.
`ensure` stops writing those two keys into new manifest entries; an old
manifest that has them loads unchanged, because every reader uses
`entry.get(...)` and nobody asks for them any more. Nothing is written under
`<home>/gitnexus/` after the change; `<home>/dependencies/` (checkouts, their
indexes, the GitNexus CLI itself) is untouched. The manifest lock stays:
`ensure` and the watch still write concurrently.

`src/omc/depwatch.py` loses `_document_batch`, `_DocumentJob`,
`_DOCUMENT_JOBS` and the `document` branch of `_tick`. A commit is done when
it is indexed: `_manifest_status` counts entries that are not indexed, and
the pass announces "Finished indexing all dependencies". `omc dependency
list` drops its DOCUMENTED column. `MultiBarThread` in
`src/omc/cli/progress_bar.py` has no caller left and goes with its tests;
`BarThread` stays (the watch build bar).

`skills/explain-dependency/SKILL.md` drops the `documented` status, the
"also read the generated docs" step, the `documented` column of its status
table and the "run `omc dependency watch` to backfill" hint for undocumented
entries. Its description says graph only.

### 5.6 Documentation LLM configuration

Deleted from the schema (`src/omc/config/schema.py`): `DocsConfig` and
`LLMConfig.docs`, `ProviderConfig.docs_model`, `SecretsConfig` and
`Config.secrets`. Deleted from the store (`src/omc/config/store.py`):
`secrets_path`, `load_secrets`, `save_secrets`, `set_api_key`,
`validate_api_key`, `_validate_docs_leaf`, `DOCS_BACKENDS`, the docs
cross-check in `validate_llm`, the `docs` branch of `set_key`.
`resolve.load_effective` stops composing secrets.

Deleted from `src/omc/configure.py`: the "Documentation" menu, the
`docs_model` picker, the API-key leaf and its masked tag, `_docs_provider`,
`_docs_changed`, `_probe_docs`, the `llm.providers.<name>.api_key` routing of
`--set`, and the secrets write flag. Deleted from `src/omc/reconcile.py`: the
`docs` group, the `docs_model` leaf, the `api` catalog source and its key
lookup. Deleted from the providers: `registry.docs_llm_for`, `DocsRun`,
`GITNEXUS_API_KEY_ENV`, and `Provider.docs_model_default`.
`taskmodels.catalog` loses its `docs_api_key` parameter.

What an existing installation sees:

- `config.yaml` is written with every field (`asdict`), so **every**
  configured user has `llm.docs` and `llm.providers.<name>.docs_model` on
  disk, not only users who generated docs. After the upgrade each config load
  prints one stderr line, `· config: ignoring unknown key(s) ['llm.docs',
  'llm.providers.claude.docs_model'] in <path>`, and continues. The line
  stops the first time omc rewrites the file: any `omc configure` save, or a
  model reconciliation that remaps a leaf. This is D4 as decided; the README
  upgrade note names the line and the one command that clears it.
- `omc configure --set llm.docs.backend=api`,
  `--set llm.providers.claude.docs_model=…` and
  `--set llm.providers.claude.api_key=…` are refused with the store's
  existing `unknown config key` error.
- `~/.omc/secrets.yaml` is neither read nor written. It stays on disk (D7).
- A legacy `~/.omc/config.json` goes through the same `_hydrate`, so its docs
  keys get the same notice during migration. The explicit refusal of a
  `secrets` key in that file goes with `Config.secrets`; the key is then one
  more ignored unknown.

### 5.7 `docsllm.py` becomes `modelprobe.py`

`src/omc/docsllm.py` holds two things: the documentation backend's
validation, and the probes that model reconciliation and the task-model
pickers use. The second half survives under the new name. What stays is
decided by today's non-docs callers:

| Stays | Used by |
|---|---|
| `auth_status` (with `_auth_status_result`), `AUTH_STATUS_TIMEOUT` | `taskmodels.claude_catalog`, `taskmodels._codex_models` |
| `cli_model_probe`, `HEADLESS_PROBE_PROMPT`, `HEADLESS_PROBE_TIMEOUT` | `taskmodels` (model validation), `reconcile` (catalog refresh turn, remap turn) |
| `_step`, made public as `step` | `taskmodels`, `reconcile` |
| `ProbeFailed` | `reconcile` |
| `api_connection_probe`, `ModelInfo`, `redact`, and the private helpers it needs (`_base`, `_host`, `_models_url`, `_headers`, `_error_message`, `MASK`, `ANTHROPIC_VERSION`, `MODELS_PAGE_LIMIT`) | `taskmodels._api_catalog` |

The last row is why the HTTP probe cannot go with the docs backend. Claude
Code with `ANTHROPIC_API_KEY` auth writes no local model catalog, so
`taskmodels.claude_catalog` lists models from the key's `/v1/models`
endpoint. The key comes from the environment (`ctx.env`), not from
`secrets.yaml`. For the same reason `Provider.api_base_url` and
`Provider.api_model_family` stay on the providers; only their docstrings stop
describing a documentation backend.

Deleted as docs-only: `DocsSelection`, `validate_selection`,
`validate_key_only`, `resolve_model`, `model_choices`, `_newest_first`,
`api_model_probe`, `check_model_id` with `_MODEL_ID`, `cli_connection_probe`,
`mask_key`.

`tests/unit/test_docsllm.py` follows the module: the cases for surviving
symbols move to `tests/unit/test_modelprobe.py` unchanged in what they
assert, the rest are deleted. The module rename is mechanical; the import
sites are `taskmodels.py`, `reconcile.py`, `configure.py` and the tests that
monkeypatch `omc.docsllm.*`. The plan makes the rename its own step after
the deletions, with `just check` green before and after, so the tree is
never half renamed.

### 5.8 The `.omc/docs/` mirror and `rebase-main`

`src/omc/mirror.py` keeps `copy_missing`, which `src/omc/worktree.py` uses to
copy the primary's `.omc/` into a new worktree (the shared-`.omc` alias check
for that copy lives in `worktree.py` and is not touched). Everything else
goes:
`SNAPSHOT_DIRS`, `mirror_snapshot`, `SnapshotResult`, `mirror_dir`,
`_sync_tree`, `_remove_entry`, `DOCS_MIRROR_REL`, `clear_docs_mirror`. The
module docstring is rewritten for what is left.

`omc internal rebase-main` mirrors nothing. `OMC_REBASE_MAIN` keeps `ok`,
`rebased`, `squashed` (with `--squash`), `conflicts` and `knowledge`, and
loses `synced` and `shared` on every path that prints them (D9). Nothing in
`src/omc/` parses those two keys; the consumers are
`skills/rebase-main/SKILL.md` and `skills/finish/SKILL.md`, which lose the
"re-mirror `.omc/docs`" wording and the `synced` / `shared` explanations.
`/omc:rebase-main` is then what its name says: fetch, optional squash,
rebase, verdict.

The worktree starter (`wtconfig.WT_TEMPLATE`, and this repo's
`.config/wt.toml` which is a copy of it) drops the comment line "Refresh a
worktree's .omc/docs later with /omc:rebase-main". The copy rule itself does
not change. A leftover `.omc/docs/` in a primary checkout is still copied
into new worktrees by `copy_missing` and by `wt step copy-ignored`; that is
harmless, nothing reads it. `.gitignore` keeps its `.omc/docs/` line so a
leftover directory does not show up as untracked.

### 5.9 Agent-facing text and README

The behavior layer (`src/omc/distribution/AGENTS.md`, propagated by
`omc update`): the worktree bullet says worktrees are snapshots of main's
code, the `.gitnexus/` graph stays in the primary checkout where
`/omc:explain` queries it, and `/omc:rebase-main` refreshes a worktree; the
sentences about `.omc/docs/` and its "deterministic mirror" go. "Ask the
graph, not grep" says the graph, without "and docs".

Skills, beyond 5.1, 5.5 and 5.8: `gitnexus-explain` (description, the
"also read the generated docs" paragraph, and Step 3's "doc-page excerpts"),
`explain` (Step 2's "doc excerpts" promise), `plan` (the `.omc/docs/gitnexus/docs/`
standing pointer), `index` (the `/omc:document` cadence), `integrate` (the
`.omc/docs/` inventory line), `investigate` (the "generated docs" mention and
the no-index fallback that reads `.omc/docs/gitnexus/docs/`), `check-wt-config`
(the "including the `.omc/docs` knowledge snapshot" clause). The two review
lenses `grug` and `human-review` keep `.omc/docs/` in their diff skip lists:
a repo that once committed generated docs should still not have them
reviewed line by line. Only grug's label "the knowledge mirrors" is reworded.

Also: `docker/PLUGIN-NOTES.md` where it describes current behavior (dated
evidence entries are history and stay), the `expensive` marker description in
`pyproject.toml` and the `expensive-e2e-tests` comment in the `justfile`
(both say "documentation generation"; the tier itself stays for the
lifecycle cases).

`README.md` is generated. The recipe `.claude/skills/regenerate-readme/SKILL.md`
loses its documentation facts (docs model defaults, `secrets.yaml`,
`--enable-documentation`, the docs half of the knowledge flowchart, the
`document` skill row, dependency docs backfill) and gains one upgrade note,
then `/regenerate-readme` is run. The note: documentation generation was
removed; `.gitnexus/wiki/`, `.omc/docs/` and `~/.omc/secrets.yaml` are no
longer used and may be deleted by hand; a one-time `ignoring unknown key(s)`
line for `llm.docs` / `docs_model` disappears after the next `omc configure`
save. The README guards in `tests/unit/test_cli.py` that pin documentation
facts (`test_readme_documents_every_watch_option`,
`test_readme_documents_gitnexus_storage_contract`) are changed first.

## 6. Tests (red first, per `.omc/config/AGENTS.md`)

A removal still gets its failing test first. Each of these is written and
seen failing against today's code:

- `omc watch --enable-documentation` exits 2; `omc internal gitnexus refresh
  --enable-documentation` returns 2 with the usage line.
- `omc internal dependency document --git …` returns 2.
- A current index with no `.gitnexus/wiki/meta.json` gives a verdict with
  `fresh: true` and no reason (today: `wiki-missing`).
- A `config.yaml` carrying `llm.docs` and `docs_model` loads, with the
  ignoring notice on stderr; a config with an invalid `llm.docs.backend`
  value loads too (today a `ConfigError`).
  `omc configure --set llm.docs.backend=api` fails with `unknown config key`.
- Shipped agent text carries no "generated docs", "doc excerpts" or "doc-page"
  wording, and rebase-main, finish and the behavior layer no longer say "mirror"
  (amended at audit).
- `omc internal rebase-main` from a worktree prints a verdict without
  `synced` and `shared`, and a `.omc/docs/` present in the primary is not
  copied into the worktree by it.
- `omc dependency watch --once` runs with no `config.yaml` and reconciles no
  model (today: refused as not configured).
- The dependency `ensure` verdict has no `docs` and no `documented`; a
  manifest entry that is indexed and not documented counts as done in
  `depwatch`, and the pass says "Finished indexing all dependencies".
- The skill inventory has no `document` and no `gitnexus-document`; a text
  guard asserts the behavior layer and the shipped skills no longer contain
  `/omc:document`, `--enable-documentation` or `.omc/docs/gitnexus/docs`,
  and that `.omc/docs` appears in no skill other than the two skip lists.

Kept green through the split: the `ANTHROPIC_API_KEY` catalog and
remap tests in `tests/unit/test_reconcile.py`, the other reconcile and
`test_taskmodels.py` probe tests, and the moved
probe cases in `test_modelprobe.py`.

Deleted with the code they test: `tests/unit/test_wikirun.py`,
`tests/unit/test_docs_model.py`; the documentation cases in `test_mirror`,
`test_gitnexus_refresh`, `test_gitnexus_freshness`, `test_dependency`,
`test_depwatch`, `test_reconcile`, `test_configure*`, `test_config_store`,
`test_resolve`, `test_internal`, `test_start`, `test_watch`, `test_cli`,
`test_progress_bar`; E2E `tests/e2e/test_e2e_docs_artifact.py` with
`tests/e2e/artifacts/` and the `container_with_artifacts` fixture, the two
`test_document_*` cases in `test_e2e_gitnexus.py`, and
`test_watch_once_regroups_a_stale_pinned_wiki` in `test_e2e_watch.py`. The
judged rubric in `test_e2e_dependency.py` stops asking for a `documented`
status.

No new E2E. The real-tool E2Es for what remains already exist and stay:
`test_index_then_explain_on_real_repo`, the embeddings refresh, the watch
sync and heal cases, `test_dependency_ensure_then_query`. Gate while
building: `just check`. `/omc:finish` runs the full stages.

## 7. Risks and side effects

- **A notice for every upgraded user.** See 5.6: the unknown-key line appears
  on each command until the config is rewritten once. Accepted under D4; the
  alternative, a migration that rewrites `config.yaml` on load, is code D4
  and D7 chose not to have.
- **Scripts that pass `--enable-documentation` break loudly** (rc 2). That is
  D5's intent.
- **A deleted probe turns out to be shared.** The split in 5.7 is the one
  place where deleting too much breaks a kept feature (model reconciliation
  under API-key auth). The table there is the contract; the kept tests guard
  it.
- **Leftover docs go stale silently.** A `.omc/docs/gitnexus/docs/` tree left
  on disk describes an older codebase. No skill points at it after 5.9, so an
  agent finds it only by browsing; the README note tells the owner to delete
  it.
- **Installed skills lag the CLI.** Between upgrading the CLI and
  `omc update` refreshing the plugin, an old `/omc:document` skill calls
  `omc internal gitnexus refresh --enable-documentation` and gets rc 2 with
  the usage line. That is a loud, correct failure; `omc update` updates both.
- **Less for `/omc:explain` to read.** Module pages sometimes carried
  architectural "why". After the change that comes from the graph and from
  the project's own docs via `explain-context`.

## 8. Left to the plan

- Task order. Suggested: skills and agent-facing text, CLI flags, wiki engine
  and freshness, dependency docs, configuration with the `docsllm` split,
  mirror and `rebase-main`, README last.
- The exact rename sites, dead imports, docstrings and comments that still
  say "wiki" or "docs" in kept code (`toolctx.run_supervised`, `watch.py`,
  `dependency.py` headers, `tests/unit/_stubs.py`).
- Which test files are trimmed and which are deleted outright.
- Whether `Provider.models()` has a caller left after the `docs_model` picker
  goes; if not it goes too.
- Every plan task carries `Complexity: simple | medium | high`: the
  configuration removal with the `docsllm` split is high, text-only tasks are
  simple. The cheap/fast tier is never used.

## 9. Prerequisite added at implementation: a portable unit suite

Amended 2026-11-16, user decision. At `b7ac5c6` the host's `just check` was red before
any change (248 failed + 5 errors on NixOS). Tests hardcode FHS binary paths
(`/bin/cat`, `/bin/bash`, `/usr/bin/git`, ...) that the host lacks, so `/omc:check`
could gate nothing here. Asked whether to gate in a Debian container, the user
chose instead to **fix portability first**. The plan's Task 0 resolves binaries through one shared
test helper (`shutil.which`, failing loud when a tool is missing), and the
`.omc/config/AGENTS.md` stub rule changes from "absolute paths (`/bin/cat`, …)" to
"absolute paths resolved through the shared helper" (`/bin/sh` and `/usr/bin/env`
excepted). Assertions don't change, except the two Worktrunk wording assertions below.

Amended at audit. Getting the host gate green also needed three product fixes,
each red-tested first. `plugin._codex_resolve` reports symlink loops on Python
3.13+ (a strict ELOOP probe, because non-strict `Path.resolve()` no longer
raises there). `configure._run_menu` maps Linux pty EIO to the "lost its
terminal" refusal (rc 2). The fish title hook calls `mkdir` through PATH.
The real-Worktrunk tests require wt >= 0.79.0 and fail loud with the install
command when wt is older (user direction: a minimum version, not an exact pin).
They accept both the v0.79 and the v0.82 pre-start hook wording, so those two
assertions were widened. Also: `tests/conftest.py` sets the NixOS shell
once-markers, `tests/unit/test_stub_portability.py` guards against new FHS
literals, and `scripts/e2e.sh` uses `xargs -r`. direnv was installed on the
host (user instruction).
Missing host prerequisites (e.g. `direnv`) still fail loud, by policy.

Added at finish. Under `CLAUDE_CODE_OAUTH_TOKEN` auth, Claude Code 2.1.325
reports `authMethod: "oauth_token"` with no `orgId` and writes no local model
catalog, so `taskmodels.claude_catalog` failed and the verify gate could not
pass. omc now lists models over HTTP `/v1/models` with the token sent as
`Authorization: Bearer` (live-verified HTTP 200). The E2E catalog helper does
the same. Header-validation errors are redacted: `modelprobe.redact` also masks
the repr-escaped forms of the credential, and `claude_catalog` strips
surrounding whitespace from the env credential.

## Implementation review

### 2026-11-16 — auditor: Claude (Opus 5.5), `/omc:audit` over `origin/main...HEAD`

Conforms in substance: §5.1–§5.8 and D1–D9 are implemented as decided, the exclusions held, and every §6 test exists.

1. Minor · code deviates from the record · §5.9 upgrade note / D4 · `README.md:55`, `.claude/skills/regenerate-readme/SKILL.md:96-98`, `tests/unit/test_cli.py:509,517`. The note said "an automatic heal" clears the store; only a destroy-and-rebuild heal does. **Fixed** in 77f1703: wording narrowed, guard needles updated, README regenerated.
2. Important · record is stale · D4, §3 anti-goal, §5.2 · `src/omc/cli/__init__.py:137`. `gitnexus clean --force` (reset, escalated heal) has always removed the whole `.gitnexus/` store, including a leftover wiki. **Record amended**: the D4 exception for this pre-existing behavior; §5.2 reset wording. D4's decision of no new cleanup or migration code is unchanged.
3. Important · record is stale · §9 · `src/omc/plugin.py:172-187`, `src/omc/configure.py:564-570`, `src/omc/assets/omc-title.fish:65-69`, `tests/unit/test_worktrunk_real.py`, `tests/conftest.py`, `scripts/e2e.sh:134`. Task 0 shipped three red-tested product fixes, a wt minimum-version gate and two widened wording assertions that §9 did not describe. **Record amended** (§9).
4. Minor · code deviates from the record · §5.9 · `skills/grug/SKILL.md:47-48`. The label called the live `.gitnexus/` a leftover. **Fixed** in 77f1703.
5. Minor · record is stale · §5.9, §6 · `skills/explain/SKILL.md:31-34`, `skills/gitnexus-explain/SKILL.md`. The doc-excerpt removals and the wider text guard were not listed. **Record amended**.

## Deliberate complexity

- `grug:small-refactor` @ §5.7: a module rename (`docsllm.py` to
  `modelprobe.py`, `_step` to `step`) rides along with the removal. Waived:
  decision D8; a module named for a feature that no longer exists would
  mislead every later reader.
- `grug:small-refactor` @ §9 (Task 0 portability sweep and product fixes;
  files `src/omc/plugin.py`, `src/omc/configure.py`,
  `src/omc/assets/omc-title.fish`, `tests/unit/_stubs.py`,
  `tests/unit/test_stub_portability.py`, `tests/unit/test_worktrunk_real.py`,
  `tests/conftest.py`, `scripts/e2e.sh`): waived, user decision §9; the host
  gate was red before any change, so nothing could be gated without it.
- `grug:small-refactor` @ `src/omc/taskmodels.py` (the oauth_token
  model-catalog path): waived; it is the repair that made the verify gate pass
  under the documented `CLAUDE_CODE_OAUTH_TOKEN` auth (§9, added at finish).
