# Fix: Codex launches resolve models from another client's model list

Date: 2026-10-23 · Branch: `feature/fix-codex-implement-stale-model-selection` · Status: design record

Supersedes, for Codex only, Decision 2 of
`2026-10-15-reconcile-stale-configured-models-design.md` ("Codex reads
`models_cache.json` (honours `$CODEX_HOME`)") and that record's §5.1 refresh
step ("one no-model headless turn … then the catalog is re-read") for Codex.
Everything else in it stands — the leaf groups, the remap turn on the default
model, the write-back, the dry run — and Claude's catalog read and refresh
turn are untouched. The old record is not edited; this one names what it
replaces.

## 1. Problem and root cause

`omc implement` on Codex launched `codex -m gpt-6.1-sol` and died on the
server's 400 "The 'gpt-6.1-sol' model is not supported when using Codex with
a ChatGPT account", while reconcile had just printed `✓ codex models current`.
About 1.5 min later the same command launched `-m gpt-6-sol` and worked.

Three Codex clients share one `~/.codex/models_cache.json`: the PATH CLI
(0.158.0, the binary omc launches), a desktop app's bundled CLI (0.159.2) and
a third, unlocated 0.163.0 writer. The server's model list depends on the
client version; each client writes its own list stamped `client_version`.
Codex itself discards a cache whose `client_version` is not its own
(codex log: `models cache: cache version mismatch expected_version="0.158.0"
cached_version=Some("0.159.2")`) and refetches. omc ignores
`client_version` and trusts whatever is in the file. When the desktop app
wrote last:

1. `reconcile.stale_leaves` → `taskmodels.compatible("sol:high", …)` only
   asks whether *some* listed model carries family `sol` → "current", no
   refresh.
2. `session.session_plan` → `taskmodels.orchestrator` → `resolve_choice` →
   `_family_model` picks the lowest-priority visible `sol` model, which in
   the 0.159.2 list is `gpt-6.1-sol` (priority 0) → `codex -m gpt-6.1-sol`
   on 0.158.
3. Codex 0.158 logs "Unknown model gpt-6.1-sol … fallback model metadata";
   the server rejects it.
4. 0.158 saw the version mismatch, refetched and rewrote the cache with its
   own list → the next `omc implement` resolves `sol` → `gpt-6-sol`.

Evidence: Codex rollouts of 2026-10-20 (two `/omc:implement` seeds failing,
each followed ~1.5 min later by a working `gpt-6-sol` session) and of
2026-10-23 17:43:00Z, where the desktop app rewrote the cache at 17:42:59.9 —
0.3 s before omc's launch, so even a "refresh, then launch" sequence races.
Reproduced on the host: running the desktop app's bundled binary
`debug models` rewrote the cache with `gpt-6.1-sol` at priority 0.

The same file read serves every Codex model decision in omc, so the bug
reaches `omc internal models` (worker dispatch inside Codex sessions,
`internal._models`), the start/review/slug/watch launches
(`session.session_plan`, `session.run_headless`, `slug`, `watch._auto_build`),
the configure pickers (`taskmodels.model_options`) and the configure probe
(`taskmodels.validate_selection`).

**What the launched binary offers instead.** `codex debug models` (verified
on 0.158.0, 2026-10-23) prints `{"models": [...]}` to stdout in the same entry
shape as the cache (`slug`, `display_name`, `priority`, `visibility`,
`supported_reasoning_levels`, plus fields omc ignores), takes ~0.4 s and
spends no model turn. Authenticated, it refreshes from the server under
Codex's own rules (300 s TTL, `client_version` mismatch) and rewrites the
cache with *its* list; `--bundled` skips the refresh. With no auth at all
(an empty `CODEX_HOME`) it exits 0 and prints the binary's bundled catalog
(`gpt-6-sol` at priority 2; nothing written).

**Goal.** omc resolves Codex models from the catalog of the `codex` binary it
is about to launch, never from the shared file. The staleness check and the
launched `-m` slug come from the same list, by construction.

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Source = **`codex debug models` on the launched binary**, everywhere omc reads a Codex catalog (option A) | Over B — keep the file, honour `client_version`, refresh on mismatch: still races with other writers (0.3 s observed) and costs a headless turn; and over C — `debug models` with a file fallback: the fallback is the bug |
| 2 | **Drop the Codex refresh turns**: reconcile's `→ refreshing codex model list with the default model` step and configure's refresh in `validate_selection` | The read *is* the refresh now; a turn on top of it is a second, slower way to the same list |
| 3 | Logged-out Codex → **accept the bundled catalog** `debug models` prints | Same binary, so no cross-version slug can appear; an unauthenticated launch fails loudly on auth anyway. Rejected: comparing against `--bundled` to detect "no server list" — a second process and a false alarm whenever the server list equals the bundled one |
| 4 | **Claude untouched** | Claude's catalog is per-org under the CLI's config dir and Claude Code has no `models` subcommand (2026-10-15 record §8); nothing observed is wrong there |
| 5 | **direnv-pinned `codex` out of scope** | `session_plan` may wrap the launch in `direnv exec .`; a project whose direnv puts a different `codex` on PATH could still disagree with omc's read. Assumption: no current project does this |
| 6 | **`omc watch` re-reads per tick**; every other command reads **once per process** | A long-lived process must see the server's list move (0.4 s per tick; network only past Codex's 300 s TTL); a one-shot command gains nothing from a second read |
| 7 | **No fallback to `models_cache.json`, ever** | Any read of the shared file reintroduces the race |

Assumptions accepted with the design: `codex debug models` exists and
answers in the cache's entry shape on every Codex version omc supports (it
is a `debug` subcommand; a future Codex that renames it fails loud, §4.4,
and is a provider-quirk change, not a design change); the configure menu
may run the command (0.4 s) before it shows the Codex pickers.

## 3. Goals, non-goals, anti-goals

Goals:

- **G1 — one source.** Every Codex catalog read in omc goes through one
  function that runs `codex debug models` and parses its stdout with today's
  `_ListedModel` validation. Callers: `resolve_choice`/`_family_model`
  (session, headless, slug, watch auto-build, `omc internal models`),
  `_codex_catalog` (reconcile), `model_options` (configure pickers),
  `validate_selection` (configure probe).
- **G2 — check and launch agree.** `✓ codex models current` and the launched
  `-m` slug come from the same catalog; within one process, from the same
  read.
- **G3 — no Codex refresh turns.** Reconcile's `→ refreshing codex model list
  with the default model` step is gone for Codex; so are configure's refresh
  in `validate_selection` (its `refresh=` parameter) and
  `configure._probe_task_models`'s `refreshed` bookkeeping. A leaf still
  stale after the read goes straight to the existing remap turn. The
  refresh step existed to protect a brand-new id typed through "Other"
  from a remap because yesterday's cache did not list it yet (2026-10-15
  record §5.1); `codex debug models` refreshes from the server past
  Codex's 300 s TTL, so the list a leaf is checked against is at most five
  minutes old — the read does the fence's job.
- **G4 — reproducing test first.** A planted cache from "another client"
  with `gpt-6.1-sol` at priority 0 and a stub `codex` answering `debug
  models` with `gpt-6-sol` → the session argv carries `-m gpt-6-sol` and
  reconcile reports current without a remap. Fails on main today.

Non-goals: Claude (Decision 4); detecting or warning about several Codex
installs; a direnv-pinned `codex` (Decision 5); the remap prompt, its
parser and the Codex launch argv, which change only in the slug they carry.

Anti-goals: a silent fallback to the file (Decision 7) — a non-zero exit,
timeout or invalid JSON from `codex debug models` is a `ConfigError` naming
the command and its output; and any new model turn — resolution stays
sub-second and non-LLM.

## 4. Mechanism

### 4.1 One reader

`taskmodels` gains one function, say `_codex_models(ctx, *, refresh) ->
list[_ListedModel]` (the `refresh` argument is §4.2's memo rule), that runs `ctx.run_bounded(["codex", "debug", "models"], timeout=…)` and
parses stdout with the validation `_model_list` applies to the file today
(the `_ListedModel` fields; anything else is a `ConfigError`). The
provider-quirk comment at the call site records the observed 0.158 facts from
§1: stdout shape, ~0.4 s, refresh under Codex's own TTL and
`client_version` rules, bundled catalog when logged out. `CODEX_HOME` keeps
working with no code of omc's own: `codex` honours it, and `ctx.run_bounded`
passes `ctx.env` through `child_env()`.

Deleted: `_model_list`'s file read, `CodexProvider.model_list_path` (only
`taskmodels` uses it; `providers/base.py` never declared it), and
`CatalogMissing` for Codex — the binary always prints a catalog, so the
"open codex once to fetch its model list" path has no trigger left. Claude's
`CatalogMissing` stays; `reconcile`'s `except taskmodels.CatalogMissing`
keeps serving it.

### 4.2 Once per process, fresh per reconcile

The read is memoised for the process, with one rule that lives on the reader
as one argument — `_codex_models(ctx, *, refresh)`: **`catalog(ctx, "codex")`
— reconcile's entry — reads with `refresh=True`, which always runs the
command and replaces the memo; `resolve_choice` and `model_options` read with
`refresh=False`, which reuses the memo when it is set and runs the command
otherwise.** A failed read is memoised as its error for the same process:
`model_options` is called once per task picker, and seven pickers must not
each pay a subprocess (or a timeout) for one broken `codex`. The
consequences:

- `omc design`/`implement`/`review`: reconcile at command entry reads once;
  `session_plan`/`run_headless`/`slug` resolve the orchestrator from that
  same list. One call, and G2 holds by construction — the check and the
  launch never see two lists.
- `omc watch`: every tick's reconcile re-reads (Decision 6); the tick's
  `_auto_build` resolves against that tick's list.
- `omc internal models`: no reconcile; the first `task_choice` reads, the
  other six reuse. One call for seven tasks. Each invocation is a fresh
  process, so a skill dispatching several workers spends 0.4 s per dispatch
  (network only past Codex's TTL) — acceptable, and still cheaper than the
  file read plus the refresh turn it replaces.
- `omc configure`: the pickers read once for all seven task menus;
  `validate_selection` reuses that list for a family leaf — the picker just
  listed it from the same binary — and keeps probing a full id live as it
  does today.

Where the memo lives is the builder's call within two constraints. It must
not make `toolctx.py` know about Codex (a provider-agnostic per-context
scratch dict is acceptable; a `codex_models` field is not). And it must be
scoped to the context object, not to the process or to `id(ctx)`:
`ToolContext` is a non-frozen `eq=True` dataclass, hence unhashable, so a
`WeakKeyDictionary` cannot key on it, a module dict keyed by `id(ctx)`
aliases across garbage-collected contexts in one pytest process, and a
module global leaks one test's catalog into the next. No in-process memo
exists in `src/omc` today; this is the first, and the tests in §6 that
build several contexts per process are what keep it honest.

### 4.3 Reconcile and configure

For Codex, the read is the refresh. `reconcile` keeps its shape — collect
leaves, list, `stale_leaves`, dry run, remap, write back — and its refresh
loop (`refresh = {leaf.provider for leaf in stale if leaf.source == "cli"}`)
simply no longer includes Codex: a Codex leaf still stale after the read
goes straight to the remap turn. Claude keeps the refresh step. The
`✓ … models current` narration and PR #67's first-tick/after-failure rule
in watch are unchanged.

`validate_selection` loses its `refresh=` parameter and the
`→ refreshing Codex model list with the default model` probe;
`configure._probe_task_models` loses `refreshed` and the family-leaf check
that set it. A Codex family leaf resolves against the memoised list with
`validate_effort=True` exactly as today; a full id is still probed live.

`model_options` keeps its families-only degradation: a `codex` binary
missing from PATH, or a `debug models` failure, is reported once through
`warn` (`· <error>; Codex task pickers show families only`) and the menu
shows families without efforts, as it does today for an unreadable file.
The `HOME`/`CODEX_HOME` guard that protected the file read is replaced by
the same degradation — an isolated menu test with no `codex` on PATH gets
families only.

### 4.4 Failure handling

- `codex` not on PATH (`FileNotFoundError` from `run_bounded`) → the
  existing install-hint `ConfigError` (`CodexProvider.install_hint()`), as
  `require_tools` reports it — except in `model_options`, which degrades
  (§4.3).
- Non-zero exit, timeout, empty or invalid JSON → `ConfigError` naming the
  command and the output tail (`codex debug models failed (exit N): …`,
  `invalid Codex model list from codex debug models: …`). Never the file.
- `omc watch`: the first tick is the command's entry and fails loud; a
  later-tick failure is warn-and-skip under reconcile's existing
  `OmcError` handling (2026-10-15 record §6). That tick's memo holds the
  failure (§4.2), so its `_auto_build` resolution fails too and is warned by
  the existing auto-build failure handling; the next tick's reconcile
  re-reads.
- Logged out: the bundled catalog is accepted (Decision 3). A family the
  bundled catalog lacks fails as "unavailable" with the listed families, as
  today; the launch itself then fails on auth before any model is used.

## 5. Record amendments and documentation

- This record supersedes Decision 2 and §5.1 of
  `2026-10-15-reconcile-stale-configured-models-design.md` for Codex (see
  the preamble). The old record is not edited.
- `README.md` is generated: its model-reconciliation paragraph says a model
  is remapped only "after one list refresh"; for Codex the read is the
  refresh. `/regenerate-readme` is an implementation step, as it was for
  the 2026-10-15 record.
- The provider-quirk comments at `validate_selection` ("a no-model exec …
  refreshes the same models_cache.json") and `reconcile` ("Both CLIs refresh
  their picker cache during a no-model headless turn") go with the code
  they annotate; the Claude half of the latter stays.

## 6. Testing

Red to green per the project's testing policy: stubs run on the restricted
PATH with shell builtins or absolute paths; every external integration keeps
one E2E on the real tool.

**Fixtures.** `make_stub` prints one fixed stdout for every argv, so it
cannot serve both the catalog and a recorded launch. A new argv-branching
helper in `tests/unit/_stubs.py` (`case "$1 $2" in "debug models") /bin/cat
<<'EOF' … ;; *) <today's behaviour> ;; esac`) replaces
`seed_codex_model_cache` at its 13 call sites (`test_start`,
`test_implement`, `test_review`, `test_watch`, `test_configure`,
`test_configure_menu`) and the direct cache writers in
`tests/unit/test_taskmodels.py` (`_cache`, ~10 tests) and
`tests/unit/test_reconcile.py` (`_codex_catalog`; `_cli_stub` is already an
argv-branching script and gains the `debug models` branch while its
`refreshed` hook goes). The cache's entry shape stays the fixture's shape —
the parser is the same. Two more surfaces the file read never touched:

- six duck-typed context fakes implement only `read_text`/`run`
  (`CaptureCtx` in `test_taskmodels.py` ×3, `FakeCtx` in `test_session.py`
  ×2 and `test_start.py`); each grows a `run_bounded` that answers `debug
  models`, or becomes a real `ToolContext` over a stub PATH. The memo must
  work with whichever the builder picks.
- `tests/unit/test_configure_pty.py` runs the real menu with the host PATH
  inherited; once `model_options` spawns a process, the PTY child env puts
  a stub `codex` dir first on PATH, so `just check` never executes a
  developer's real `codex` (testing policy: no real tools or network in
  the check tier).

**New unit tests.**

- The G4 reproducer (`test_taskmodels.py` or `test_reconcile.py`): a planted
  `~/.codex/models_cache.json` with `gpt-6.1-sol` at priority 0 and
  `client_version: "0.159.2"`, a stub `codex` whose `debug models` lists
  `gpt-6-sol` → `session_plan`'s argv carries `-m gpt-6-sol`; `reconcile`
  on `sol:high` prints `✓ codex models current` with no remap turn and no
  write. Red on main (main reads the file → `gpt-6.1-sol`).
- `debug models` exits non-zero / prints non-JSON → `ConfigError` naming
  the command; a planted valid cache beside it proves there is no fallback.
- The memo rule at the reader's seam: `omc internal models` resolving seven
  tasks → the stub's argv log holds exactly one `debug models` line; two
  `reconcile` calls on one context → two lines (Decision 6, the property
  watch's per-tick call relies on — `test_watch`'s autouse fixture replaces
  `reconcile`, so it is unobservable there and `test_watch` keeps asserting
  only "reconcile once per tick"); a `reconcile` followed by `session_plan`
  → one line (G2). A failed read → one line and one warning across seven
  `model_options` calls.
- Reconcile with a stale Codex leaf → no `exec` refresh turn in the argv
  log, the remap turn is the first `exec`; the `→ refreshing codex …` line
  is absent. The Claude stale path keeps its refresh assertion.
- `configure --set llm.providers.codex.tasks.simple=sol` → no `exec` turn at
  all (today: one refresh exec); a full id still probes once.
- `model_options` with no `codex` on PATH → families only, warned once
  (replaces the unreadable-file tests).
- `test_watch.py`:
  `test_auto_build_model_resolution_failure_does_not_stop_watch_loop`
  (today: missing/malformed cache, asserts `models_cache.json` in the
  error) becomes the `debug models` failure case and asserts the command
  is named; the loop still continues.
- `test_providers.py`: `test_codex_model_list_path_uses_supplied_environment_only`
  is deleted with the method.

**E2E** (Docker, real Codex, opt-in as the rest of the Codex suite):
`tests/e2e/test_e2e_task_models.py::test_codex_family_uses_real_model_list`
derives its expected slug from the container's `codex debug models` output
instead of reading `models_cache.json` after a no-model exec, then asserts
`omc internal models` resolves `sol` to that slug;
`tests/e2e/test_e2e_reconcile.py`'s Codex catalog helper does the same, so
`test_codex_design_recovers_retired_orchestrator` keeps asserting the saved
id is one the binary lists. Both drive the real binary and assert on
artifacts (the verdict line, the config file). Quirk the parser must
respect: Codex 0.156.1 warns on **stderr** when `CODEX_HOME` is under
`/tmp` (the E2E layout, see `codex_auth.require_codex_ready`); the reader
parses stdout only, which `run_bounded` already separates.

## 7. Left to the builder, out of scope

Left to the builder: the timeout — the command is a non-LLM CLI read like
`claude auth status`, so `docsllm.AUTH_STATUS_TIMEOUT`'s 30 s class fits
better than `HEADLESS_PROBE_TIMEOUT`'s 120 s (0.4 s locally, a few seconds
when it refreshes from the server; either constant is acceptable); whether
to run it with `cwd=ctx.home` as `cli_model_probe` does; the memo's location
within §4.2's constraints; the exact error wording; which test file hosts
the G4 reproducer.

Size: the product change is one reader with one keyword and one memo in
`taskmodels.py`, minus the file read, `model_list_path`, the Codex
`CatalogMissing` and two refresh turns — small. The test sweep (§6) is the
bulk of the diff and is forced by the deletion, not a bundled refactor; the
plan can sequence it reader-first (add the reader and its tests, switch the
callers, then delete the file read and sweep the fixtures) so the tree is
green between steps.

Out of scope: Claude's catalog and refresh (Decision 4); detecting several
Codex installs; direnv-pinned binaries (Decision 5); the remap prompt and
parser; `omc internal models` staying read-only and `omc configure`'s other
probes (2026-10-15 record §10).

## Implementation review

### 2026-10-23 — Claude (Opus 5.5)

- **Important — code deviates from the record (unrecorded scope).** §7 Out
  of scope; `scripts/e2e.sh:17-113`, `scripts/e2e_lock.py:1-47`,
  `tests/unit/test_e2e_image_holders.py`. Two commits added cross-process
  flock leases around E2E image pruning, a harness repair made during the
  implementation's verify gate; the record does not cover them, and they
  conflicted with the base's own E2E pruning rewrite. Disposition: dropped
  from this branch at the user's direction and preserved on a local
  `backup/e2e-prune-leases` branch for separate work.
- **Important — code deviates from the record (E2E weakened).** §6 E2E,
  G3, Decision 4; `tests/e2e/test_e2e_reconcile.py:180`, `:186-187`. The
  Codex catalog-helper swap also deleted, for both providers, the
  `→ model: … is unavailable;`, `✓ <provider> models current` and
  no-`config updated`/no-`→ refreshing ` assertions; §6 only swaps the
  helper and Decision 4 leaves Claude untouched. Disposition: fixed, the
  three assertions restored; the Codex gate's
  `test_codex_design_recovers_retired_orchestrator` and the parallel suite
  pass with them.
- **Minor — code deviates from the record (dead branch).** §4.4;
  `src/omc/taskmodels.py:121-124` and
  `test_debug_models_undecodable_output_names_command`.
  `ToolContext.run_bounded` decodes with `errors="replace"`, so the
  `except UnicodeError` branch cannot fire. Listed, not gating.

## Deliberate complexity

None.
