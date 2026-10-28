# Fix: Claude model list never refreshes when nonessential traffic is disabled

Date: 2026-10-27 · Branch: `feature/fix-claude-model-list-detection` · Status: design record

Amends, for Claude only, §6 and §8 of
`2026-10-15-reconcile-stale-configured-models-design.md`: the "open claude
once to fetch it" hint is retired, catalog creation under account auth is now
observed, and the Claude refresh turn's trigger changes from "a leaf is
stale" to "at command start, the catalog file is missing or past its own
`staleAt`". Everything
else in that record stands — the leaf groups, the compatibility check, the
remap turn on the default model, the write-back, the dry run, the API-key
path. Codex is untouched: its `codex debug models` read is already live
(`2026-10-23-fix-codex-implement-stale-model-selection-design.md`). The old
record gets a short amendment note pointing here (§5).

## 1. Problem and root cause

omc's Claude model reconciliation (`taskmodels._claude_catalog`) reads Claude
Code's served model catalog at
`<configDirectory>/cache/model-catalog/<orgId>-*-cc.json`, with `orgId` and
`configDirectory` from `claude auth status`. When the user's
`~/.claude/settings.json` `env` block sets
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, Claude Code classifies the served
catalog as nonessential traffic, never fetches it and never writes the file
(debug log: `[Bootstrap] Skipped: Nonessential traffic disabled`,
`[servedCatalog] off (essential_traffic)`). On a machine that fetched the
catalog before the setting went on, the file is frozen forever.

omc's refresh turn in `reconcile.reconcile` — `docsllm.cli_model_probe(ctx,
"claude", "")`, a no-model `claude -p "Reply with exactly OK."` — inherits
the same user settings, so it cannot produce the file either, and the run
fails with `CatalogMissing`: "Claude model catalog missing under …; open
claude once to fetch it". That hint cannot succeed: opening claude once is
exactly what the setting stops from working.

The 2026-10-15 record (§6, §8) decided "fail loud with *open claude once*,
never a skip" while noting that catalog *creation* under account auth had
never been observed. This is the first observation, and it shows the hint
is wrong. The Claude Code version (2.1.296 vs 2.1.312) was a red herring.

A second layer was observed by the user: the same setting also freezes
Claude's GrowthBook feature-flag cache, so the first turn with traffic
allowed still logs `[servedCatalog] off (flag)`, and only a second turn
writes the file.

### Live verification

Claude Code 2.1.312, 2026-10-28 UTC. The user settings file sets the
variable to `"1"`. Each row is one `claude -p "Reply with exactly OK."
--output-format text --debug-file …` turn from an EMPTY cwd, judged by the
`[servedCatalog]` debug line.

| Variant | Override tried | Result |
|---|---|---|
| A | process env `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=0` | off (essential_traffic) |
| I | process env `=false` | off |
| K | process env `=` (empty string) | off |
| F | `--settings '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":"0"}}'` | off |
| G | `--settings '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'` | **ON** — "primary: using served rows (12 rows via cache over the org route)", Bootstrap fetch ok |
| E | `--setting-sources project,local` (user source dropped) | ON |
| J | project `.claude/settings.json` in cwd sets `"0"` | off |

Earlier runs from cwd=`$HOME` were inconclusive because there the "project"
settings file is `~/.claude/settings.json` itself. omc's probe runs with
cwd=`ctx.home` (`~/.omc`), so this does not affect omc.

Conclusions: (1) Claude Code applies the settings `env` block *over* the
process env, so no environment change from omc's side can win; (2) the
variable counts as set whenever it is NON-EMPTY, so only an empty string
disables it; (3) variant G is the mechanism — it keeps every other user
setting (plugins, permissions, proxy env, `apiKeyHelper`) intact. Variant E
also works but drops the whole user settings file and could break auth
elsewhere → rejected. The two-turn flag-cache layer was NOT re-verified (it
would require deleting the user's catalog and flag cache); it is designed
for through the two-turn bound (Decision 3).

**Goal.** On a machine whose user settings disable nonessential traffic,
`omc design`/`implement`/`review`/`watch` refresh the Claude model list
themselves when it is missing or stale, and the remap then runs against a
current list. On a machine without the setting nothing observable changes
except that omc stops spending a refresh turn on a list that is fresh.

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 0 | Remedy = **refresh the catalog from omc with nonessential traffic enabled on the refresh turn** | User's seed: "the model list needs to be updated on each omc design etc. command using a quick call with the env variable turned off". Rejected: detect-and-explain only (leaves the user to fix by hand and never refreshes a frozen catalog); fall back to the configured family with a warning (reverses the 2026-10-15 "never a skip" and hides the problem) |
| 1 | Mechanism = **`--settings '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'` on the Claude refresh turn only** (variant G) | The only override verified to win (§1 table). Rejected: any process-env form (A, I, K — verified not to work); `--setting-sources` (E — drops the user settings file) |
| 2 | Trigger = **catalog freshness at command initialization**: the refresh turn runs when the on-disk catalog is MISSING or past its own `staleAt` (epoch ms; live file shows `staleAt − fetchedAt` ≈ 62 min), and only at the start of a command — the entry of `omc design`/`implement`/`review`/`dependency watch` and the FIRST tick of `omc watch`. Later watch ticks spend no refresh turn. The old Claude trigger "a leaf is stale → refresh turn" is dropped | Claude uses its cache when fresh — variant G logged "via cache, fetched 576 s ago" — so an unconditional turn (per launch, per `omc watch` tick) changes nothing and costs a model call; a leaf-triggered turn on a fresh catalog is the same wasted call. User ruling on the watch question: "Who said to run this in every tick? Only during the initialization of these commands." The leaf trigger existed as the 2026-10-15 §5.1 fence for a brand-new id; §6 weighs what dropping it costs. Codex untouched: its read is already live (2026-10-23 record) |
| 3 | Bound = **at most two refresh turns per command start**, re-reading after each, stopping as soon as the catalog is present and not past `staleAt` | Two matches the flag-cache layer the user observed (first turn thaws the flag cache, second writes the file). No open-ended loop; with Decision 2 the bound is per process, not per tick |
| 4 | Reader shape = **a freshness query beside `catalog()`** in `taskmodels` (say `catalog_fresh()`), so every current `catalog()` caller stays untouched; a missing or malformed `staleAt` counts as stale, never as an error. Wall clock through a new **`ToolContext.now_ms()`** | `ToolContext` is the only runtime boundary; tests fake the clock at the same seam as every other provider-state read. A catalog without `staleAt` is one Claude did not stamp, and one extra turn is the cheap side of that doubt |
| 5 | Still **missing** after two turns → `ConfigError`, rc 1; message names the directory, says two refresh turns were tried (that clause only when turns ran — not on a later watch tick), and points at the `[servedCatalog]` lines in `~/.claude/debug/latest`. Still **past `staleAt` but present** after two turns → continue with the cached list and narrate one `·` line | Fail-loud for the missing case is kept from 2026-10-15 (never a silent skip); the "open claude once" hint is retired because it cannot succeed. A present-but-old list is still a list of real models — stopping a launch over its age would be a regression against a machine where Claude simply has not refreshed yet |
| 6 | **API-key auth path unchanged** (no file; live `GET /v1/models`). **`omc watch`** keeps first-tick-fails-loud / later-ticks-warn; later ticks reconcile against the on-disk list as it is, with no turn | Nothing observed is wrong there. A running watch spends at most two turns in its whole life (its first tick); the per-tick cost stays the 2026-10-15 Decision 4 file read plus `claude auth status` |
| 7 | **Correct the README recipe sentence and regenerate**: the one sentence in `.claude/skills/regenerate-readme/SKILL.md` ("Claude keeps one no-model list refresh before remap") is rewritten to the new behaviour and `/regenerate-readme` is run during implementation so `README.md` follows; the 2026-10-15 record §6/§8 get a short amendment note pointing here | User ruling (hardening question 1): the README is generated, a wrong sentence in the recipe propagates forever; the 2026-10-23 record regenerated for its analogous trigger change. The old record's hint and its unobserved-creation note are now wrong and should say so |

Assumptions accepted with the design: `--settings` with an inline JSON
object is accepted by every Claude Code version omc supports (verified
2.1.312; it is a documented flag); a future Claude Code that honours the
process env would make the override redundant, not harmful; the flag-cache
layer never needs more than one extra turn.

## 3. Goals, non-goals, anti-goals

Goals:

- **G1 — omc can refresh the Claude catalog on its own.** Under user
  settings that disable nonessential traffic, the refresh turn carries the
  variant-G override and the file appears (or is rewritten) without the
  user touching their settings.
- **G2 — the turn is spent only when it can change something, and only at
  command start.** Missing or past `staleAt` at initialization → turn;
  fresh → no turn, even when a leaf remaps; a later watch tick → never a
  turn.
- **G3 — bounded.** Two turns per command start, then a decision: fail
  loud (missing) or continue with the cached list (old).
- **G4 — reproducing test first.** A Claude stub that writes the catalog
  only when it receives the override makes today's reconcile fail with the
  "open claude once" error and the fixed one succeed with the override on
  its argv log (§7).

Non-goals: Codex; the API-key path; the remap prompt, parser and write-back;
`omc configure`'s probes (`taskmodels.validate_selection` probes a chosen
model live and reads no catalog); a dedicated "nonessential traffic is on"
diagnostic.

Anti-goals: changing or dropping any other user setting (`--setting-sources`
is rejected, Decision 1); an unconditional turn per launch, or any refresh
turn on a later watch tick (Decision 2); a silent skip when the catalog is
missing at command start (Decision 5).

## 4. Mechanism

### 4.1 Provider seam

`Provider.headless_argv` gains one keyword, `served_catalog: bool = False`.
`ClaudeProvider.headless_argv` appends `--settings` with the JSON object
`{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}` when it is true.
The verified quirk is the comment at that site: the settings `env` block
overrides the process env; the variable counts as set whenever it is
non-empty, so only `""` disables it; `--setting-sources` would also work
but drops the user settings file; verified on 2.1.312, 2026-10-28.
`CodexProvider.headless_argv` accepts and ignores the keyword — the base
class declares it, so both signatures carry it.

`docsllm.cli_model_probe` gains the same keyword and passes it through to
`headless_argv`. No other caller changes: `session.run_headless`, `slug`,
`validate_selection` and the remap turn keep calling without it, so their
argv is byte-identical to today. The `--settings` argument is appended after
the existing flags; the claude argv rule "prompt right after `-p`,
`--allowed-tools` last" still holds because the refresh turn passes no
allowed tools, and the builder keeps `--allowed-tools` last regardless.

Argv lists only, as everywhere in omc: the JSON is one argv element, never
shell-quoted.

### 4.2 Freshness

Today `_claude_catalog` reads `fetchedAt` only to pick the newest file when
several match the org prefix. The same read now also notes the chosen file's
`staleAt` (Claude Code writes it as epoch milliseconds beside `fetchedAt`;
live: `staleAt − fetchedAt` ≈ 62 min) and answers one more question beside
the list: "is this catalog past `staleAt` now?". `taskmodels` exposes that
answer through a freshness query beside `catalog()` — the name and whether
it is a second function or a richer return the Claude reader hands back
and `catalog()` unwraps is the plan's call — under one constraint: **one
read**. `_claude_catalog` already spends a `claude auth status` subprocess
and a directory listing per call; reconcile must get the list and its
freshness from the same read, not pay a second auth status to ask the
file's age. Every current `catalog()` caller stays untouched (Decision 4).

A missing, non-integer or boolean `staleAt` answers *stale*, never raises:
the catalog is still readable and remappable, the reader just cannot vouch
for its age, and the cost of being wrong is one extra turn. The existing
`fetchedAt` validation is unchanged.

The wall clock comes from a new `ToolContext.now_ms() -> int`
(`time.time() * 1000`), the time sibling of `read_text`/`list_dir`. Nothing
else in `taskmodels` or `reconcile` reads the clock. For the API-key path
(which leaves `_claude_catalog` before any file is touched) and for Codex
there is no file and no freshness to ask (Decision 6).

### 4.3 Reconcile flow (Claude CLI source only)

In `reconcile.reconcile`, the Claude CLI bucket is handled before the
compatibility check instead of between it and the remap:

1. Read the catalog as today (`CatalogMissing` caught → `None`).
2. `needs_refresh` = this reconcile is a command's initialization, and the
   catalog is missing or the freshness answer (§4.2) says past `staleAt`.
3. If so: one refresh turn — `cli_model_probe(ctx, "claude", "",
   served_catalog=True)` — narrated `→ refreshing claude model list
   (nonessential traffic enabled)` with the probe's `✓`/`✗` line, then
   re-read. If still missing or still past `staleAt`: one more turn and
   re-read. Never a third.
4. The existing `stale_leaves` check, dry-run report, remap turn, validation
   and write-back run unchanged against whatever list step 3 left. A
   catalog still **missing** here — after the two turns, or on a later
   watch tick where step 3 does not run — raises the §4.4 `ConfigError`
   before step 4 (a dry run reports it instead, below): the remap turn is
   never spent against an empty list. Today that cannot happen only
   because the post-refresh re-read raises; the new flow makes it an
   explicit check.

**Initialization only.** The refresh step belongs to a command's start: the
entry calls of `omc design`/`implement`/`review` (`cli/__init__.py`, after
`_with_provider`), `omc dependency watch`'s entry call, and the first tick
of `omc watch`. A later watch tick skips step 3 entirely and runs step 4
against the on-disk list as it is — stale, fresh or missing — spending no
turn. How reconcile learns it is the first call is the plan's choice;
`watch.py` already derives `report_current` from `first_tick`, and the
initialization signal is the same kind of caller flag (the one-shot
commands always pass it). It cannot ride `report_current` itself, which
watch also sets after a failed tick. `stale_leaves`' "no catalog → every
leaf stale" branch stays only for the dry run's report.

The refresh block replaces today's `refresh = {… if leaf.source == "cli"
and leaf.provider == "claude"}` loop and its "Claude refreshes its per-org
picker catalog during a no-model turn" comment; the new comment at the
call site says why the override is there and why the bound is two.
Consequence of Decision 2 worth stating: a stale leaf on a *fresh* catalog
goes straight to the remap turn — one model turn instead of today's two.

`--dry-run` keeps its contract from the 2026-10-15 record ("runs no turn and
writes nothing"), so it skips step 3. Today a missing catalog makes every
leaf "stale" (`stale_leaves` treats a `None` catalog as stale) and the dry
run prints `· <leaf>: … is unavailable (would remap)` for each — a guess,
now that the real run would refresh first. Instead: catalog **missing** →
one line `· claude catalog missing under <dir>; would refresh`, then
`DryRunStale` as today (exit 0, no per-leaf verdicts — there is no list to
judge against); catalog **past `staleAt`** → one line `· claude catalog is
N min past its refresh time; would refresh`, then the per-leaf verdicts
against the cached list exactly as today. With a fresh catalog the dry run
is unchanged.

### 4.4 Failure handling

- Catalog still **missing** after two turns → `ConfigError`, rc 1:
  "Claude model catalog missing under `<dir>`/cache/model-catalog after two
  refresh turns; check the `[servedCatalog]` lines in
  `~/.claude/debug/latest`". Config untouched. The "open claude once" hint
  is gone from omc. On a later watch tick, where no turn runs, the same
  error names the directory without the "after two refresh turns" clause
  (the list is refreshed at the next command start).
- Catalog **present but still past `staleAt`** after two turns → one `·`
  line, "claude catalog is N min past its refresh time; continuing with the
  cached list", then the normal check and remap against that list. A
  cached list is a list of real models; its age is narrated, not fatal.
- A refresh turn **fails** (non-zero exit, timeout, no output) →
  `ProbeFailed` through `docsllm._step`, rc 1 with the probe's redacted
  detail — exactly today's behaviour for the refresh turn.
- **API-key auth**: `_claude_catalog` takes the `GET /v1/models` path before
  any file is read; the freshness query never runs and no refresh turn is
  spent, so the override is never sent under API-key auth.
- **`omc watch`**: the first tick is the command's entry — it runs the
  refresh step and fails loud like the one-shot commands (missing after two
  turns → the `ConfigError` above; a failed turn → `ProbeFailed`). Later
  ticks spend no refresh turn (Decision 2): they reconcile against the
  on-disk list as it is. A list past `staleAt` on a later tick is used
  silently — the freshness `·` line is narrated only when the refresh
  step ran, so a 30 s loop never repeats it; a catalog *missing* on a
  later tick (deleted under a running watch) is the existing
  warn-and-skip — `✗ model reconciliation failed: …`, previous config
  kept, tick continues — and the next command start refreshes it. Watch's
  cost is therefore bounded to two turns per process, at its first tick;
  the per-tick cost stays one file read plus `claude auth status`.
- **`claude auth status`** failures and the "missing orgId or
  configDirectory" case are unchanged.

### 4.5 Cost

One or two `Reply with exactly OK.` turns on the default model, only at a
command's start and only when the list is missing or past `staleAt`. On a
machine without the setting Claude refreshes the file during normal
interactive use, so omc almost never pays the turn; on a machine with the
setting omc pays one refresh (usually one turn, two on a cold flag cache)
per command start whose list is older than ~62 min. `omc watch` pays at
most once, at its first tick. Today's cost — a refresh turn on every
launch with a stale leaf, fresh catalog or not — goes away.

## 5. Record amendments and documentation

- `2026-10-15-reconcile-stale-configured-models-design.md` §6 and §8 get a
  short amendment note: catalog creation under account auth is now
  observed (this record §1); the "open claude once" hint is retired; the
  Claude refresh trigger is catalog freshness, not leaf staleness. The
  note points at this record; the old text is not rewritten.
- `README.md` is generated: its recipe
  `.claude/skills/regenerate-readme/SKILL.md` (the model-reconciliation
  paragraph, "Claude keeps one no-model list refresh before remap") is
  corrected to the new behaviour — at a command's start, a Claude model
  list that is missing or past its refresh time is refreshed first (one or
  two quick turns with nonessential traffic enabled), then a still-stale
  value is remapped — and `/regenerate-readme` is run during
  implementation so `README.md` follows (Decision 7, as the 2026-10-23
  record did for its trigger change). README.md is never hand-edited.
- Provider-quirk comments: the verified `--settings` facts at
  `ClaudeProvider.headless_argv` (§4.1); the two-turn rationale at the
  reconcile call site (§4.3); the `staleAt` field beside the existing
  `fetchedAt` comment in `_claude_catalog` (§4.2).

## 6. Edge cases and live-verify items

- **Variant G keeps the user's other settings** — verified (plugins,
  permissions, proxy env and `apiKeyHelper` all loaded on the G run).
- **A fresh catalog, a stale leaf** → no refresh turn, straight to remap
  (Decision 2). A brand-new model typed through "Other" that the fresh
  catalog does not list yet would be remapped; the 2026-10-15 §5.1 fence
  against that case rested on the leaf-triggered turn. Accepted: Claude's
  own `staleAt` is ~62 min, so the window is at most an hour after a model
  ships, and `omc configure` already proved the id live before saving it.
- **Several catalog files** for the org prefix: the newest `fetchedAt` is
  chosen as today; its `staleAt` is the one judged.
- **Clock skew**: `staleAt` is Claude's own stamp from the same machine's
  clock; omc compares with the same clock. A machine whose clock jumps
  backwards sees a "fresh" catalog for longer, never an error.
- **Flag cache needs a second turn** (observed by the user, not
  re-verified): the bound covers it. If a future Claude needs three, the
  user sees the `·` line (present case) or the two-turns error (missing
  case) and the bound is one constant.
- **`--dry-run`** on a missing or stale catalog: no turn (§4.3).
- **Catalog goes stale under a running watch** (the usual case after ~62
  min with the setting on): later ticks keep reconciling against the old
  list with no turn (Decision 2); the next `omc design`/`implement`/
  `review` start, or a watch restart, refreshes it. A model retired inside
  that window is caught by that next command start, which is where the
  launch happens anyway.
- **`cwd=ctx.home`** for the probe stays; it is what keeps the project
  settings source from being `~/.claude/settings.json` (§1, variant J
  note).
- **Still to verify live during implementation**: one real `omc design
  --dry-run`/`omc design` on the user's machine with the setting on,
  catalog deleted, confirming the file appears within two turns and the
  `[servedCatalog]` debug line reads `ON` on the writing turn.

## 7. Testing

Red to green per the project's testing policy: stubs run on the restricted
PATH; argv asserted exactly; every external integration keeps one E2E on
the real tool.

**Fixture changes in `tests/unit/test_reconcile.py`.** `_claude_catalog`
(the fixture) gains a `staleAt`, and the Claude tests run under a faked
`ToolContext.now_ms` so that the fixture is *fresh* by default. `_cli_stub`
gains a parameter that makes the Claude branch write the catalog file when
— and only when — its argv carries the `--settings` override (optionally
on the N-th such turn). Existing Claude stale-leaf tests that asserted
"refresh + remap = two headless turns" against a fresh fixture now assert
one remap turn and no `→ refreshing` line (Decision 2); the API-key tests
are unchanged.

**New and rewritten unit tests.**

- The G4 reproducer (rewrites
  `test_missing_claude_catalog_after_refresh_has_path_and_open_hint`): no
  catalog on disk, stub writes it only on an override turn → today fails
  with the "open claude once" error; after the fix reconcile succeeds, the
  argv log shows exactly one headless turn and it carries
  `--settings {"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}`,
  config unchanged (the leaf was current).
- Two-turn test: stub writes only on its second override turn → exactly
  two headless turns, then success.
- Bound test: stub never writes → exactly two turns, `ConfigError` whose
  message names the directory, "two refresh turns" and
  `~/.claude/debug/latest`; config.yaml bytes unchanged.
- Freshness under a faked `now_ms`: `staleAt` in the past → one turn
  (stub rewrites with a future `staleAt`); `staleAt` in the future → no
  turn even when a leaf remaps; `staleAt` missing or malformed → treated
  as stale (one turn, no error); present-but-still-old after two turns →
  the `·` line and the remap proceeds against the cached list.
- Dry run with a missing or stale catalog → no turn, no write, `·`
  report.
- Initialization only, at the reconcile level: a reconcile called without
  the initialization signal, with a stale or missing catalog, spends zero
  headless turns (argv log holds only `auth status`) and prints no
  freshness line; missing without the signal raises the §4.4
  `ConfigError` with no remap turn; with the signal the first-tick cases
  above apply. At the watch level, `tests/unit/test_watch.py` already
  replaces `reconcile` with keyword-exact fakes (`:94`, `:184`, `:210`),
  which grow the new keyword and assert it is passed on the first tick
  only; the later-tick failure test keeps asserting warn-and-skip. The
  one-shot entry commands always pass the signal (`tests/unit/test_cli.py`'s
  dispatch tests, which already fake `reconcile` per command).
- `tests/unit/test_providers.py`: exact Claude argv with and without
  `served_catalog`; Codex argv unchanged with it set; `--allowed-tools`
  still last when both are passed.
- `tests/unit/test_taskmodels.py`: the reader-level freshness answer
  beside the existing Claude reader tests (`staleAt` in the past / future
  / absent / malformed, under a faked `now_ms`; the newest-`fetchedAt`
  file's `staleAt` is the one judged);
  `test_claude_catalog_missing_has_open_once_hint` loses the hint
  assertion (the `CatalogMissing` message keeps naming the directory).
- `tests/unit/test_toolctx.py`: `now_ms` returns an int near
  `time.time()*1000` (only if the builder finds it worth a test; the
  faked seam is what matters).
- `tests/unit/test_docsllm.py`: `cli_model_probe(..., served_catalog=True)`
  forwards the keyword (one argv assertion).

**E2E.** The containers authenticate with `ANTHROPIC_API_KEY`, so
account-auth catalog creation stays unobservable there (2026-10-15 record
§8); the live matrix in §1 is the real-tool evidence and is recorded here.
`tests/e2e/test_e2e_reconcile.py` must stay green: under API-key auth the
freshness query never runs (§4.4), so what it proves is that the provider
seam and reconcile reordering left the ordinary argv and the API-key remap
path intact. The proof that `--settings` parses and wins on the real CLI
is the §1 matrix. No new E2E.

## 8. Affected files

`src/omc/providers/base.py`, `src/omc/providers/claude.py`,
`src/omc/providers/codex.py` (signature only), `src/omc/docsllm.py`,
`src/omc/taskmodels.py`, `src/omc/reconcile.py`, `src/omc/toolctx.py`
(`now_ms`), `tests/unit/test_reconcile.py`, `tests/unit/test_taskmodels.py`,
`tests/unit/test_providers.py`, `tests/unit/test_docsllm.py`,
`tests/unit/test_toolctx.py` (if a `now_ms` test is warranted),
`tests/unit/test_watch.py` and/or `tests/unit/test_cli.py` (initialization
signal), `.claude/skills/regenerate-readme/SKILL.md` (one sentence) and
`README.md` (regenerated, never hand-edited),
`docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md`
(amendment note). `src/omc/watch.py` and `src/omc/cli/__init__.py` change
only if the initialization signal needs a new argument at their existing
`reconcile` calls.

Left to the plan: the freshness query's exact shape (a bool beside
`catalog()`, or a small struct the Claude reader returns and `catalog()`
unwraps); the exact error and `·` wording; where the two-turn constant
lives.

## Deliberate complexity

- §4.2, `grug:factor-late`: `ToolContext.now_ms()` is a new seam with one
  caller; `time.time()` inside `taskmodels`, monkeypatched in tests, would
  do. Waived by Decision 4: `ToolContext` is the one runtime boundary tests
  fake, and the freshness tests fake the clock where they fake the file.
- §7, `grug:test-level`: the one new real-tool integration point — the
  `--settings` override on the refresh turn — has no automated E2E; the
  containers run API-key auth and never reach the freshness path. Waived
  by the brainstorm's testing decision: account-auth catalog creation is
  unobservable in containers (2026-10-15 record §8), the §1 live matrix on
  Claude Code 2.1.312 is the real-tool evidence, and §6 carries the manual
  live-verify item for the implementation.
