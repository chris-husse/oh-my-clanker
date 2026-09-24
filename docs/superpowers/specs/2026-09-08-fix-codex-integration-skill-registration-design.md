# Codex plugin registration (+ provider plugin seam)

Date: 2026-09-08
Slug: fix-codex-integration-skill-registration
Status: approved design, ready for planning

## Problem

`omc start` launches a codex session seeded with `/omc:start`, but nothing
ever registers omc's plugin with codex. The session opens on "Unknown
command" — the exact failure `src/omc/plugin.py`'s module docstring calls
"the worst possible first-run".

Observed on a real machine (codex-cli 0.153.4) before any change:

```
$ codex plugin marketplace list
MARKETPLACE     ROOT
openai-curated  /Users/<user>/.codex/.tmp/plugins      # no oh-my-clanker

$ codex plugin list --json | jq '.installed[].name'    # no omc, no superpowers
```

The manifest layer is already complete: `.codex-plugin/plugin.json` ships in
the repo, declares `"skills": "./skills/"` (asserted by
`tests/unit/test_plugin_manifests.py::test_codex_plugin_manifest`), and its
version is locked to `pyproject.toml` plus both `.claude-plugin` manifests by
`test_all_version_strings_agree`, kept in step by `scripts/stamp_version.py`.
Nothing registers it.

The proximate cause is a single early return:

```python
# src/omc/plugin.py:124
if provider != "claude":
    return "unverified (no scriptable check for this provider yet)"
```

with a second provider-name branch at `src/omc/installer.py:83`
(`if name == "claude":`). Both exist because the `Provider` ABC exposes
`plugin_update_argvs()` but no *install* and no *probe* counterpart, so the
knowledge had nowhere to live except as a special case in `plugin.py`.

This was a deliberate v1 decision, not an oversight —
`.superpowers/sdd/progress.md:33` records "plugin SELF-HEAL (ensure_plugin:
... codex/opencode unverified-skip)". It was taken when codex had no
scriptable install path. **That premise has expired**: codex 0.153.4 ships
`codex plugin add`, `codex plugin remove`, and `--json` output on both list
commands.

The branches also violate a convention this project already documents. From
`.omc/docs/gitnexus/docs/ai-provider-adapters.md`:

> No caller ever branches on provider name — every integration point goes
> through `get_provider` and then calls interface methods.

## Goals

1. `omc start`, `omc configure` and `omc update` install, repair and refresh
   the omc plugin **and** superpowers for codex, with no manual step.
2. Delete both provider-name branches; plugin capability becomes real
   `Provider` members, restoring the documented convention.
3. Preserve `ensure_plugin`'s hard rule: every mutating path re-probes and
   raises with the harness's own error text — a broken plugin must never be
   reported as "ok".
4. Record the verified codex CLI contract, superseding the stale 0.144.5-era
   notes in `docker/PLUGIN-NOTES.md`.

## Non-goals

- opencode plugin automation. No verified scriptable path exists; it keeps
  returning its `"unverified …"` status.
- Whether a *running* codex session picks up a newly installed plugin without
  a restart. Token-gated, already flagged unverified for both providers in
  `docker/PLUGIN-NOTES.md`; stays a documented follow-up.
- The claude cache version-pin bug (a machine observed serving `omc/0.1.0`
  while the repo is at 0.1.7). Pre-existing and separate; this design only
  explains the mechanism (see "Version pinning and `omc update`").
- `src/omc/skills_source.py`. Its wheel-asset/dev-fallback lookup serves
  *headless prompt inlining*, an unrelated mechanism that merely shares the
  `skills/` directory with harness plugin loading.

## Decisions (resolved with the user)

1. **Delivery route: marketplace only.** No `~/.codex/skills/`, no
   `~/.agents/skills/`, no direct-copy fallback — even though both roots
   exist and the predecessor tool populated them.
2. **Fix it properly.** Push plugin capability onto the `Provider` ABC and
   delete the name branches, rather than adding a codex branch beside the
   claude one.
3. **superpowers is in scope for codex**, registered by omc, not left to a
   README pointer.
4. **superpowers comes from `obra/superpowers-marketplace`**, added silently
   the same way the claude flow adds Anthropic's official marketplace. The
   official codex catalog entry is admin-blocked — see "superpowers under
   codex".
5. **`omc update` forces a fresh copy for local sources only** — see
   "Version pinning and `omc update`".
6. **A conflicting marketplace registration is auto-removed**, narrated —
   see "Error handling".
7. **`.codex-plugin/plugin.json` gains `hooks: {}` and `longDescription`
   only** — not the asset-dependent interface fields superpowers ships
   (Component change 7).

## The codex contract (verified 2026-09-08, codex-cli 0.153.4)

Established empirically this session, partly in an isolated `CODEX_HOME`.
These facts supersede the codex claims in `docker/PLUGIN-NOTES.md`.

| # | Fact | Consequence |
|---|---|---|
| C1 | `codex plugin list --json` → `{"installed":[…],"available":[…]}`. Entries: `pluginId` (= `name@marketplaceName`), `name`, `marketplaceName`, `version`, `installed`, `enabled`, `source`, `marketplaceSource`, `installPolicy`, `authPolicy` | A real machine-readable probe exists; no table scraping |
| C2 | **No `errors` field** | Codex has no load-error channel. `installed`/`enabled` are the whole health signal; claude's `_problems()` has no analogue and the "failed to load → uninstall + reinstall" repair collapses to a plain re-add |
| C3 | The JSON **omits** path/git marketplaces until the plugin is installed. `available` was `[]` throughout, even with dozens of uninstalled remote plugins | The probe confirms "installed & enabled" but cannot distinguish "registered, not installed" from "nothing registered". Acceptable: the repair is idempotent, so it simply re-runs both commands |
| C4 | `codex plugin marketplace list --json` → `{"marketplaces":[{name, root, marketplaceSource:{sourceType, source}}]}` | The registered source *is* readable — required for C7 |
| C5 | Codex reads **`.claude-plugin/marketplace.json`** (it prints that path as the marketplace root), for omc's checkout and obra's marketplace alike. No `.codex-plugin/marketplace.json` exists or is needed | No new marketplace manifest to author |
| C6 | `codex plugin marketplace add` accepts a local path **and** `owner/repo`. Re-adding the *same* source → exit 0, "already added" | Idempotent; safe to run unconditionally |
| C7 | Re-adding a same-named marketplace from a **different** source → **exit 1**: `marketplace 'oh-my-clanker' is already added from a different source; remove it before adding this source` | A live bug generator: `marketplace_source(env)` varies with how omc was installed, so moving from a local checkout to a GitHub install hard-fails. Must be handled explicitly |
| C8 | `codex plugin add omc@oh-my-clanker` → exit 0; repeating it → exit 0, same message | Idempotent; safe to run unconditionally |
| C9 | Verbs are `add` / `remove`, **not** `install` / `uninstall` | Diverges from claude; belongs in the codex adapter |
| C10 | `marketplace remove` succeeds even with the plugin installed, and the plugin then disappears from `installed` — no zombie state | Remove-then-re-add is a safe repair |
| C11 | Failing commands exit 1 with the message on **stderr** | Existing `ctx.run` + returncode handling works unchanged |
| C12 | Install root is version-pinned: `~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/`, a full repo copy | See "Version pinning and `omc update`" |
| C13 | `CODEX_HOME` fully isolates config and cache | Clean sandbox for E2E and future probing |

## Design overview

The ABC's invariant (`src/omc/providers/base.py`) is that argv builders are
pure — no I/O, no subprocess calls, no filesystem writes. A probe needs I/O,
so the provider must never *run* anything. The split:

**Providers supply argv, a pure parser, and a pure planner. `plugin.py`
executes.**

Three new dataclasses, in `src/omc/providers/base.py` alongside the ABC:

```python
@dataclass(frozen=True)
class PluginEntry:
    id: str                      # "omc@oh-my-clanker"
    enabled: bool
    problems: tuple[str, ...]    # harness-reported load errors; always () on codex

@dataclass(frozen=True)
class PluginFacts:
    """What one probe round learned about a harness's plugin state."""
    omc: PluginEntry | None
    superpowers: PluginEntry | None
    marketplace_source: str | None   # registered source for omc's marketplace; None = unknown

@dataclass(frozen=True)
class RepairStep:
    argv: list[str]
    label: str      # narration for _say(), e.g. "installing superpowers"
    fatal: bool     # False = best-effort self-heal; failure tolerated
```

Three new `Provider` members replace `plugin_update_argvs()`:

| Member | Signature | Contract |
|---|---|---|
| `plugin_probe_argvs()` | `-> list[list[str]]` | Commands whose stdout carries plugin state. `[]` = no scriptable probe → the `"unverified …"` status. Pure |
| `parse_plugin_facts(stdouts)` | `list[str] -> PluginFacts` | One stdout per probe argv, same order. Pure |
| `plugin_repair_argvs(facts, *, source, update)` | `-> list[RepairStep]` | Ordered plan; `[]` = nothing to do. Pure |

`RepairStep.fatal` replaces an implicit rule. Today `installer.py:184`
encodes "only the final command decides" as an index comparison
(`i == len(argvs) - 1`). Codex needs a genuinely non-fatal *first* step
(`marketplace remove`), so per-step policy becomes explicit.

`plugin_update_argvs()` is **deleted** — `update=True` is now an input to the
planner, which is strictly more expressive.

## Component changes

### 1. `src/omc/providers/base.py`

Add the three dataclasses and the three abstract members; remove
`plugin_update_argvs`. Extend the class docstring to state that plugin
members follow the same purity rule: the provider describes *what to run and
what the output means*, never runs it.

### 2. `src/omc/providers/claude.py`

Behaviour unchanged, relocated from `plugin.py`.

- `plugin_probe_argvs()` → `[["claude", "plugin", "list", "--json"]]`.
- `parse_plugin_facts()` reads the existing array-of-`{id, enabled, errors}`
  contract; matches `omc@*` / `superpowers@*` by name prefix so a plugin from
  *any* marketplace counts (today's `_find` semantics, which exist because a
  marketplace-qualified dependency broke loading — see `PLUGIN-NOTES.md`
  "Resolution 2"). `marketplace_source` is `None`: claude exposes no source
  probe, and re-adding is harmless.
- `plugin_repair_argvs()` reproduces today's logic exactly: superpowers from
  `claude-plugins-official` when absent; install omc when absent; on load
  errors `marketplace add` → `marketplace update` → `uninstall` → `install`;
  on `update` the existing update sequence.

All 15 existing `test_plugin.py` cases must pass **unmodified** — that is the
regression guard for this refactor.

### 3. `src/omc/providers/codex.py`

New capability.

```python
def plugin_probe_argvs(self):
    return [
        ["codex", "plugin", "marketplace", "list", "--json"],
        ["codex", "plugin", "list", "--json"],
    ]
```

`parse_plugin_facts(stdouts)`:
- from the marketplace JSON (C4), the `oh-my-clanker` entry's
  `marketplaceSource.source` → `PluginFacts.marketplace_source`;
- from the plugin JSON (C1), `installed` entries matched by `omc@*` /
  `superpowers@*` prefix — **any marketplace counts**, mirroring claude, so a
  superpowers the user already installed from the curated catalog satisfies
  the check and is left alone;
- `problems` is always `()` (C2).

`plugin_repair_argvs(facts, *, source, update)`:

Let `conflict = facts.marketplace_source is not None and facts.marketplace_source != source`.

| Condition | Step | Fatal |
|---|---|---|
| `conflict` | `codex plugin marketplace remove oh-my-clanker` | no |
| always | `codex plugin marketplace add <source>` | yes |
| omc absent, or `enabled is False`, **or `conflict`** | `codex plugin add omc@oh-my-clanker` | yes |
| superpowers absent | `codex plugin marketplace add obra/superpowers-marketplace` | no |
| superpowers absent | `codex plugin add superpowers@superpowers-marketplace` | yes |
| `update` and source is a local path | `codex plugin remove omc@oh-my-clanker` | no |
| `update` | `codex plugin marketplace upgrade` | no |
| `update` | `codex plugin add omc@oh-my-clanker` | yes |

The `or conflict` clause is load-bearing. `facts` is captured *before* the
plan runs, and removing a marketplace also removes its installed plugins
(C10) — so an omc that *was* installed from the stale marketplace reads as
present in `facts` while being gone by the time the plan reaches the install
row. Without the clause, repairing a conflict would leave codex with no omc
plugin at all, and the mandatory re-probe would then correctly fail the
command. Hence the dedicated test case below.

Per the `explain-context` convention — "provider quirks are documented as
comments at the exact code site that depends on them" — replace the now-false
comment "codex has no scriptable per-marketplace add" with the verified
facts: the version-pinned copy (C12), the different-source failure (C7), and
the absent `errors` channel (C2).

### 4. `src/omc/providers/opencode.py`

`plugin_probe_argvs()` → `[]`. Behaviour unchanged: it keeps returning the
`"unverified …"` string that `test_non_claude_provider_unverified` pins and
that `configure.py:143`'s display contract
(`mark = "·" if status.startswith("unverified")`) depends on.

### 5. `src/omc/plugin.py`

`ensure_plugin` becomes provider-agnostic; `_list_plugins`, `_find`,
`_problems` and `_install` move into the claude adapter or become pure
helpers on it. `marketplace_source(env)` and the `PLUGIN_REF` /
`MARKETPLACE_NAME` / `SUPERPOWERS_REF` constants stay (constants move to the
adapters that own them). The `provider != "claude"` branch is deleted.

### 6. `src/omc/installer.py`

`run_update`'s `if name == "claude":` branch collapses to a single
`ensure_plugin(ctx, name, update=True)` for every configured provider,
keeping the existing best-effort `✗ … — continuing` policy. The
`plugin_update_argvs` fallback loop is removed.

### 7. `.codex-plugin/plugin.json`

Add `"hooks": {}` and a `longDescription`, matching what superpowers 6.3.0
ships. The asset-dependent interface fields (icons, screenshots, brandColor)
are deliberately skipped.

### 8. `docker/setup-plugins.sh`

Currently registers the codex marketplace and deliberately never installs.
Add the install steps:

```bash
codex plugin add omc@oh-my-clanker 2>/dev/null || true
codex plugin marketplace add obra/superpowers-marketplace 2>/dev/null || true
codex plugin add superpowers@superpowers-marketplace 2>/dev/null || true
```

## Data flow

```
argvs = provider.plugin_probe_argvs()
if not argvs:
    return "unverified (no scriptable check for this provider yet)"

stdouts = [ctx.run(a) for a in argvs]        # non-zero → OmcError with the harness's stderr
facts   = provider.parse_plugin_facts(stdouts)

if check_only:                               # the --dry-run path; never mutates
    return describe(facts)

plan = provider.plugin_repair_argvs(
    facts, source=marketplace_source(ctx.env), update=update
)
if not plan:
    return "ok"

for step in plan:
    _say(step.label)
    cp = ctx.run(step.argv)
    if cp.returncode and step.fatal:
        raise OmcError(f"`{' '.join(step.argv)}` failed: {stderr}\n  fix manually: …")

facts = provider.parse_plugin_facts([ctx.run(a).stdout for a in argvs])   # re-probe
if facts.omc is None or not facts.omc.enabled or facts.omc.problems:
    raise OmcError(<harness's own text>)
return <action summary>
```

The three callers keep their current, differing failure policies untouched:

| Caller | Call | Policy |
|---|---|---|
| `src/omc/start.py:79` | `ensure_plugin(ctx, name, check_only=dry_run)` | raises — blocks the session |
| `src/omc/configure.py:142` | `ensure_plugin(ctx, name)` per configured provider | best-effort; prints `✗`, never fails the run |
| `src/omc/installer.py:86` | `ensure_plugin(ctx, name, update=True)` | best-effort, continues |

## superpowers under codex

omc's start skill hands off to superpowers, so registering it is required.
Two sources exist and evidence forces the choice:

- `superpowers@openai-curated-remote` (6.3.0) is in codex's **official**
  curated catalog, but installing it returns **exit 1**:
  `remote plugin plugins~Plugin_… is disabled by admin`. That is org policy
  on a managed machine, so it will fail for colleagues too. It cannot be the
  primary route.
- `obra/superpowers-marketplace` works end to end: `marketplace add` → exit 0
  (git marketplace), then `codex plugin add superpowers@superpowers-marketplace`
  → exit 0, `installed: true`, `enabled: true`, 14 skills present under the
  install root.

So codex uses obra, which is also what `docker/setup-plugins.sh` already does
for claude in the E2E image. Superpowers from *any* marketplace satisfies the
check, so a user who has it from the curated catalog is left alone.

Note the deliberate asymmetry: claude installs
`superpowers@claude-plugins-official`, codex installs
`superpowers@superpowers-marketplace`. The source is a per-provider fact and
lives in each adapter.

Superpowers 6.3.0 ships its own `.codex-plugin/plugin.json` with
`"skills": "./skills/"` — direct precedent for omc's manifest. (The 5.0.6
copy in a claude cache predates codex support, which arrived in 6.x.)

## Version pinning and `omc update`

Codex copies the whole repo into
`~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/` (C12). Because the
path is keyed by version, an unchanged version means codex keeps serving the
old snapshot. This is the same trap that has been observed leaving a claude
cache at `omc/0.1.0` while the repo was at 0.1.7.

Released upgrades are safe: `scripts/stamp_version.py` bumps
`.codex-plugin/plugin.json` every release, and codex resolved 0.1.7 correctly
on a fresh install. Two rough edges remain:

- **Git marketplace**: `marketplace upgrade` refreshes the snapshot, then
  `plugin add` re-installs. Covered by the plan's `update` steps.
- **Local-checkout source (dogfooding)**: edits to `skills/` are invisible
  until the version changes, because the install is a *copy*, not a link.
  **Decision:** on `update`, when `source` is a local path, precede the re-add
  with a non-fatal `plugin remove` so the copy is refreshed even at an
  unchanged version. This is exactly the trap that has already bitten this
  project, and it costs one extra command.

## Error handling

- **Probe failure** (non-zero exit, or unparseable JSON) → `OmcError`
  carrying the command and the harness's stderr, matching
  `_list_plugins`'s existing behaviour.
- **Fatal step failure** → `OmcError` with the command, the harness's stderr,
  and a `fix manually:` line.
- **Non-fatal step failure** → narrated, execution continues. Covers
  `marketplace remove` (may legitimately not exist), `marketplace upgrade`
  (reports "No configured Git marketplaces to upgrade" for local
  registrations — expected, not a bug), and obra's `marketplace add`.
- **Still broken after repair** → `OmcError`. The rule holds: a broken plugin
  is never reported as "ok".
- **Marketplace source conflict** (C7) → auto-removed and re-added, narrated
  (e.g. `the oh-my-clanker marketplace points at <old> — re-registering from
  <new>…`). It is omc's own marketplace name and removal is reversible
  (C10). omc mutates codex state only through the CLI, never by editing
  `~/.codex/config.toml`, which also holds the user's MCP servers.

## Testing

**Unit** (`tests/unit/`), using the existing `_stubs.make_stub` PATH-stub
pattern:

- `test_plugin.py` — all 15 claude cases pass **unmodified** (refactor
  regression guard). New codex cases: fresh install; already healthy (no-op →
  `"ok"`); plugin present but `enabled: false`; **different-source
  marketplace conflict → remove-then-add**; **conflict while omc reads as
  installed → the plugin is still re-added** (the `or conflict` clause);
  superpowers absent → both steps emitted; superpowers present from another
  marketplace → untouched;
  `check_only` never mutates; re-probe-still-broken → `OmcError`; `update`
  with a local source → `plugin remove` precedes the re-add.
  `test_non_claude_provider_unverified` already asserts against `"opencode"`,
  so it passes unmodified and keeps pinning the `"unverified"` sentinel — but
  it stops standing in for codex, whose behaviour the new cases now cover.
- `test_providers.py` — `test_plugin_update_argvs_are_pure_and_per_provider`
  is **replaced**: the member it asserts on is gone. New assertions cover
  `plugin_probe_argvs()` shapes, `parse_plugin_facts()` against captured real
  JSON fixtures (from §"The codex contract"), and `plugin_repair_argvs()`
  plans for each facts permutation. Purity is asserted as today.
- `test_plugin_manifests.py` — unchanged except for the `.codex-plugin`
  additions from Component change 7; still guards the four-way version
  agreement.

**E2E / Docker** — `docker/setup-plugins.sh` gains the codex install steps
(Component change 8), and a new E2E asserts codex-side registration via
`codex plugin list --json`. This is genuinely cheap and **not** token-gated:
the image already carries codex-cli, and plugin commands need no auth. It
closes the gap where nothing asserted codex's plugin state.

**Docs** — deliverables, not afterthoughts:

- `docker/PLUGIN-NOTES.md`: new dated section recording the C1–C13 table; the
  existing codex content is stale at 0.144.5.
- `README.md:28` and `configure.py`'s `_PLUGIN_HINTS`: drop the manual
  "then install `omc` from `/plugins`" instruction — omc does it now.
- `.superpowers/sdd/progress.md`: ledger line closing the
  "codex/opencode unverified-skip" item for codex.

## Out of scope

opencode plugin automation; live-session pickup-without-restart (token-gated,
already a tracked follow-up); the pre-existing claude cache version-pin bug;
`skills_source.py`'s headless-inlining path.
