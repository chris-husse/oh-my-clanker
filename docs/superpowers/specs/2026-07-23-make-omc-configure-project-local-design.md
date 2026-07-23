# Design: project-local omc config (`.omc/config.yaml`)

**Date:** 2026-07-23
**Status:** approved
**Branch:** `feature/make-omc-configure-project-local`

## Problem & goal

omc's configuration is a single global file, `~/.omc/config.json`. Worktree
naming (`branch_prefix`, `base_branch`) is project truth, not user preference —
it belongs in the repo, shared with the team. The split:

- **Global, YAML** — `~/.omc/config.yaml` (`$OMC_HOME` honored): `llm` +
  `notifications`. Personal settings stay personal.
- **Project, YAML, committed** — `<repo>/.omc/config.yaml`: `worktree`,
  extensible for future project keys (slug rules, stages, …).
- Legacy `~/.omc/config.json` migrates via `omc configure`, which then deletes
  the JSON (its content lives in the YAML afterwards; no `.bak`).
- Parser: PyYAML, `safe_load`/`safe_dump` only.

## Decisions (brainstorm forks, all resolved)

1. **Scope**: only the `worktree` section moves project-local. No per-project
   LLM overrides.
2. **Committed**: `.omc/config.yaml` is committed — every worktree carries it
   by virtue of being in the branch. Matches the committed `.omc/skills/*`
   precedent. (`_ensure_gitignore` only manages root AGENTS.md/CLAUDE.md
   symlink entries; it never touches this file.)
3. **Migration**: via `omc configure` only. Readers never write
   (no migrate-on-read side effects); a legacy-only state is "unconfigured"
   with a migrate hint.
4. **Dependency**: PyYAML (`pyyaml>=6,<7`). Vetted: 6.0.2 current, actively
   maintained, ubiquitous, no unpatched critical/high CVEs. Accepted
   trade-off: rewriting a hand-commented file drops comments.

## Approach

**Split persistence, composed runtime view.** Two on-disk schemas; a resolver
composes them into the existing three-section shape (`cfg.llm`,
`cfg.worktree`, `cfg.notifications`) at the dispatch boundary. Consumers
(`start.py`, `watch.py`, `notify.py`, `probe.py`, `plugin.py`, `slug.py`)
mix all three sections freely today and do not change.

Rejected alternatives: threading `ProjectConfig` through every consumer
signature (churn without behavioral gain); a layered overlay where the project
file may override any key (contradicts the clean split, makes strict
validation fuzzy).

## On-disk layout

`~/.omc/config.yaml` (global):

```yaml
schema_version: 1
llm:
  default: claude
  providers:
    claude:
      model: ""
notifications:
  enabled: false
  backend: macos
```

`<repo>/.omc/config.yaml` (project, committed):

```yaml
schema_version: 1
worktree:
  branch_prefix: feature/
  base_branch: main
```

Each file carries its own `schema_version`, both starting at 1 (new filenames,
fresh versioning). The committed `.omc/config/` *directory* (AGENTS.md chain)
sits alongside without collision.

## Schema & store changes

`src/omc/config/schema.py`:

- `Config` splits into `GlobalConfig` (`schema_version`, `llm`,
  `notifications`) and `ProjectConfig` (`schema_version`, `worktree`).
- The composed runtime shape survives as `EffectiveConfig` — the same
  three-section attribute surface consumers already use; never persisted.

`src/omc/config/store.py` (`_hydrate`, `set_key`, `validate_backend` are
schema-generic and stay as-is):

- `global_config_path(home) -> home / "config.yaml"`;
  `project_config_path(root) -> root / ".omc" / "config.yaml"`.
- `load_global(home)` / `save_global(home, cfg)` and `load_project(root)` /
  `save_project(root, cfg)` — YAML via `yaml.safe_load` /
  `yaml.safe_dump(sort_keys=False)`. Contract unchanged: missing file →
  `None`; unparseable / non-mapping / unknown keys → `ConfigError` naming the
  file.
- `load_legacy(home)` — reads the old combined-v1 `config.json` into
  `(GlobalConfig, ProjectConfig)`; used only by `omc configure` migration.

## Resolution layer

New `load_effective(ctx) -> EffectiveConfig | None`, replacing today's
`_load_cfg_or_bail` load:

- Global part: `load_global(ctx.home)`. Missing → `None` (command bails,
  exit 2, as today). When a legacy `config.json` exists, the bail message adds:
  "found legacy ~/.omc/config.json — `omc configure` will migrate it".
- Project part: `load_project(repo_root(ctx))`. Missing file or outside a
  repo → dataclass defaults (`feature/`, `main`) — an un-integrated repo keeps
  working exactly as an unconfigured project does today.

Per-caller behavior (parity everywhere):

| Caller | Today | After |
|---|---|---|
| `cli.py` `start`/`watch` gate | global JSON or exit 2 | `load_effective`; global required, project defaulted |
| `internal.py` rebase-main / gitnexus proxy | `cfg.worktree.base_branch if cfg else "main"` | `load_project(repo_root) or defaults` — same fallback |
| `notify.py` | global; broken/missing → exit 0 | `load_global`; identical tolerance |
| `installer.py` update | global providers; skip if none | `load_global`; identical |

**Behavior-layer consumers (hardening finding).** Three skills reference the
config file by path and must be updated in the same change:

- `skills/finish/SKILL.md` — reads `worktree.base_branch` from
  `~/.omc/config.json`; becomes the project `.omc/config.yaml` (committed, so
  present in the worktree the skill runs in), keeping its existing "otherwise
  the repo's default branch" fallback.
- `skills/gitnexus-document/SKILL.md` — reads `llm.default` from
  `~/.omc/config.json`; path becomes `~/.omc/config.yaml`.
- `skills/integrate/SKILL.md` — mentions reading the current `llm.default`
  from `~/.omc/config.json`; path becomes `~/.omc/config.yaml`.

## `omc configure` flow

- **Interactive, inside a repo**: LLM + notifications questions (global), then
  worktree questions (project). Saves both files, prints both paths. Seeding
  order for current values: existing YAML file(s) → else legacy JSON → else
  defaults.
- **Interactive, outside a repo**: global questions only; prints that project
  settings are configured per-repo.
- **`--set KEY=VALUE`**: routed by top-level key — `worktree.*` → project file
  (`Refusal` outside a repo), everything else → global. `set_key` mechanics
  unchanged.
- **`--defaults`**: resets the **global** file. The project file is written
  only if absent — it is committed team truth; "reset my omc" must not clobber
  it.
- **Migration**: when configure successfully writes the global YAML and a
  legacy `~/.omc/config.json` exists, it has seeded from it (both sections)
  and then deletes the JSON, printing what happened.
- Non-configure commands never write config.

## Error handling

Strictness preserved wholesale: unknown keys, wrong types, invalid
`notifications.backend` raise `ConfigError` naming the offending file. New
cases: YAML parse error → `ConfigError("invalid YAML in <path>: …")`;
top-level not a mapping → same shape as today's "expected an object". A
legacy-only state is unconfigured for every reader — never silently readable.

## Docs & tests

- Unit: `test_config_store.py` reworked for YAML + split (round-trip, strict
  rejects, legacy loader); `test_configure.py` gains routing, out-of-repo,
  `--defaults` non-clobber, and migration cases; new `load_effective` tests.
  Unit fixtures planting `config.json` move to the new layout
  (`test_installer.py:75`, `test_notify.py:265`, `test_config_store.py`).
  The only e2e `config.json` (`tests/e2e/conftest.py:33`) is **Docker's**
  auth config — untouched (hardening finding).
- Docs: README table row (`writes ~/.omc/config.json` → the two YAML paths);
  the `configure` help string at `cli.py:26` literally says "writes
  ~/.omc/config.json" and is updated. The v1 design spec stays untouched
  (historical; code wins). Generated wiki regenerates post-merge via
  `/omc:document`.
- Dogfooding: this repo gains its own committed `.omc/config.yaml`
  (`branch_prefix: feature/`, `base_branch: main`).

## Out of scope

Per-project LLM overrides; layered overrides; new project keys beyond
`worktree` (schema is ready for them); any change to `ctx.home`'s other roles
(managed dependencies dir, uninstall deletion).
