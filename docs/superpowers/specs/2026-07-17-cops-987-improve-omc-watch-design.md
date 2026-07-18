# The omc update story: distribution chain, one-stop update, gitnexus proxy

Approved 2026-07-17 (COPS-987, sub-task of COPS-855). The behavior layer
(`.omc/internal/AGENTS.md`) is stamped from a constant inside the CLI and only
`omc configure` re-stamps it — updating omc does not update the rules agents
read in managed repos. This design makes `uv tool upgrade omc` propagate the
behavior layer everywhere instantly, makes `omc update` the whole update loop
(CLI + plugins per provider), and fixes the diagnosed graph-staleness defect
by proxying GitNexus invocations through omc's own CLI.

Ticket premise correction, recorded for honesty: the ticket claimed
`ensure_wt_config` runs in start AND watch; in the current code it runs only
in `watch.run_watch` (`src/omc/watch.py:146`). Start gets chain-ensure wiring
for the first time in this design.

## 1. `distribution/AGENTS.md` — the behavior layer becomes a shipped file

The `INTERNAL_AGENTS_MD` text moves out of `src/omc/agentsmd.py` into a real
file, committed in the omc repo and shipped as package data:

- Source of truth: `src/omc/distribution/AGENTS.md`. Living inside the
  package directory, hatchling includes it automatically (`packages =
  ["src/omc"]`) — no force-include needed, unlike the repo-root `skills/`
  tree. Every install mode carries it; git, directory, and PyPI installs all
  materialize it inside the uv tool venv.
- Resolved at runtime via `importlib.resources` on the installed `omc`
  package — never a repo-relative path. Precedent:
  `skills_source.skill_text` already resolves wheel assets exactly this way
  (`resources.files("omc") / …`); uv tool venvs are real directories, so the
  resolved path is a valid symlink target.
- `uv tool upgrade omc` replacing the venv IS the propagation mechanism:
  every managed repo's root symlink serves the new content the moment the
  upgrade lands. No re-stamping, no per-repo step.
- omc's own repo dogfoods the same mechanism: its root `AGENTS.md`/`CLAUDE.md`
  are machine-local symlinks into the installed distribution file like any
  other managed repo (the repo-relative `src/omc/distribution/AGENTS.md` stays
  the *editable source*, the installed copy stays the *served target* — no
  self-breakage when hacking on omc itself).

The file's content is today's `INTERNAL_AGENTS_MD` verbatim, with the header
line reworded (it no longer claims "omc configure regenerates it"; it says the
file ships with the omc install and updates with it).

## 2. `omc print-install-path`

New CLI subcommand. Prints exactly ONE line to stdout — the absolute path of
the installed omc package directory (the directory containing
`distribution/AGENTS.md`) — and nothing else: no banner, no decoration, no
trailing commentary. Shell-composable by contract:

```
OMC_PATH=$(omc print-install-path)
```

The chain's symlink target is `<print-install-path>/distribution/AGENTS.md`.
The existing banner already goes to stderr (stdout stays clean for `$()`),
and `version` already has a banner exemption in `cli.main` —
`print-install-path` joins that exemption so the command is quiet on both
streams.

## 3. Chain v2 — `ensure_agents_chain` rework

Root `AGENTS.md`/`CLAUDE.md` become machine-local, GITIGNORED, absolute
symlinks to the installed `distribution/AGENTS.md`:

- **Gitignore management**: omc ensures `.gitignore` contains entries for
  `/AGENTS.md` and `/CLAUDE.md` (root-anchored). This is a committed edit,
  made once, idempotent thereafter. No gitignore-editing helper exists in
  the codebase today — this is a new, small, append-only function (never
  reorders or rewrites user content).
- **`.omc/config/AGENTS.md` unchanged**: committed, project-owned, seeded
  once, never touched again. The distribution file still ends by deferring
  to it.
- **`.omc/internal/AGENTS.md` retired**, with automatic migration: when
  ensure finds the v1 chain (root symlinks pointing at `.omc/internal/
  AGENTS.md` plus the stamped internal file), it replaces those symlinks with
  v2 targets, deletes the internal file (and the `.omc/internal/` dir if
  empty), and adds the gitignore entries. Only omc's OWN artifacts are
  migrated; foreign regular files or unknown symlinks still report "blocked"
  with migration steps and nothing is touched.
- **Collateral text updates** (found by sweep): `skills/integrate/SKILL.md`
  inventories `.omc/internal/AGENTS.md` and must describe the v2 chain;
  `PROJECT_STARTER` (the seeded `.omc/config/AGENTS.md` text) references the
  internal layer and gets reworded. `tests/unit/test_agentsmd.py` rewrites
  wholesale with the v2 semantics.
- **Callers**:
  - `omc configure` — as today (`configure._ensure_repo_chain`).
  - `omc start` — NEW: ensures the chain before seeding the session, so any
    machine that starts work gets working symlinks automatically. Blocked
    chain warns-but-proceeds (start's job is starting work, not arguing).
  - `omc watch` — repairs per tick when the target dangles (install moved,
    python-version path change inside the uv venv). Quiet-token narrated
    like every other repeatable tick outcome; warn-but-proceed, never blocks
    the loop.

## 4. `omc update` — the whole update loop in one command

`installer.run_update` grows from a bare `uv tool upgrade omc` into:

1. **Upgrade the CLI**: `uv tool upgrade omc`, reporting old→new version
   (unchanged versions reported as "already current").
2. **Update plugins for every configured provider** via a new method on the
   `Provider` ABC (`src/omc/providers/base.py`):
   - claude: `claude plugin marketplace update <marketplace>` +
     `claude plugin update omc@oh-my-clanker` (both commands verified to
     exist; "restart required to apply" is claude's documented semantic —
     running sessions keep the old plugin, new sessions get the new one).
   - codex: `codex plugin marketplace upgrade` (command exists; must be
     Docker-verified before trusting, findings recorded in
     `docker/PLUGIN-NOTES.md` per repo convention).
   - opencode: mechanism unknown (git-ref plugin entry in `opencode.json`,
     cache managed by opencode itself) — investigate empirically, implement
     best-effort, record findings in PLUGIN-NOTES.md.
3. **Failure isolation**: each provider's update failure warns and continues
   — one broken harness never aborts the CLI upgrade or the other providers.

The behavior layer needs no step of its own: symlinks already point at the
upgraded files. `omc watch --auto-update-self` (the ticket's item 4) is
explicitly DROPPED by user decision — no self-updating watcher; `omc update`
is the one entry point.

## 5. `omc internal gitnexus` — proxy instead of prose

New internal subcommand following the existing `omc internal` contract
(`src/omc/internal.py`: pre-argparse intercept, machine stdout, exit codes
0 ok / 2 usage):

```
omc internal gitnexus <query|context|impact|cypher> [args…]
```

- Resolves the GitNexus CLI path (`gitnexus.gitnexus_cli`), erroring with the
  install hint when absent.
- Resolves the PRIMARY worktree root (never the worktree you're standing in)
  and runs the query from there.
- ALWAYS injects `--repo <primary-root basename>` and `--branch <configured
  base branch>` (from config). Deterministic; callers cannot get scoping
  wrong because they never see it.
- Everything else passes through verbatim; GitNexus's JSON goes straight to
  stdout.

Why: this session diagnosed the stale-default-store defect — GitNexus writes
incremental analyze results to `.gitnexus/branches/<branch>/`, but unscoped
queries read the top-level default store, which is keyed to the branch the
repo was FIRST indexed on. In this repo that branch (`feature/omc-v1` @
`1fd58d6`) is deleted, so unscoped queries answer from a permanently frozen
index while `omc watch` truthfully reports "✓ index refreshed". Deterministic
scoping (always primary root + configured base branch) sidesteps the defect
for good.

Consequences:

- `skills/gitnexus-explain/SKILL.md` shrinks to "run `omc internal gitnexus
  …`" — the interim `--repo`-mandatory/`--branch`-fallback prose fix (an
  uncommitted working-tree edit from this session) is superseded and gets
  rewritten rather than committed.
- `watch._refresh_index` routes its analyze invocation through the same
  Python helper, so watch and skills cannot drift apart.
- The explain path's local-snapshot preference disappears (worktree
  `.gitnexus/` snapshots remain — worktrees still copy them — but queries
  are always answered by the primary root's graph at the base branch).
- Implementation must verify the no-branch-store-yet edge case (a repo whose
  first index happened on the base branch may have only the default store —
  does `--branch <base>` resolve or error?) and document the resolution as a
  comment at the injection site.

## 6. Testing

Red→green TDD throughout (test first, watch it fail, make it pass).

Unit (`tests/unit/`):
- Chain v2: fresh create, v1→v2 migration (symlinks replaced, internal file
  deleted, gitignore entries added), blocked foreign files untouched,
  gitignore idempotence (no duplicate entries on re-run).
- `print-install-path`: single line, no banner, path contains
  `distribution/AGENTS.md`'s parent.
- `omc update`: provider fan-out order, per-provider failure isolation
  (claude fails → codex still runs), version reporting.
- Watch tick: dangling-symlink repair narrates via quiet-token (repairs
  narrate once per state change, healthy chain is silent).
- `internal gitnexus` proxy: flag injection, primary-root resolution from
  inside a worktree, missing-CLI error, passthrough fidelity.
- SKILL.md contract tests (`test_plugin_manifests.py` style): the
  gitnexus-explain skill invokes the proxy, not raw `node`.

Docker E2E (`tests/e2e/`, credential-free):
- Chain create + v1→v2 migrate in a real repo inside the container.
- codex plugin update mechanics (`codex plugin marketplace upgrade` actually
  refreshes a snapshot); opencode investigation results as executable checks
  where possible.
- Live-session E2E (updated plugin's skills actually load in a driven
  session) stays token-gated and deferred, per the standing follow-up.

## Out of scope

- `omc watch --auto-update-self` (dropped by decision).
- GitNexus upstream changes (the proxy sidesteps the store-resolution defect;
  upstream fix may happen independently in the GitNexus repo).
- PyPI publishing of omc.
- Codex API credentials for live E2E (owner will supply if/when available).
