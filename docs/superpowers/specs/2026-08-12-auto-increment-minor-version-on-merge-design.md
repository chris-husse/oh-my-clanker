# GitHub auto-stamps the patch version on every merge to main

Date: 2026-08-12 · Status: approved design · Branch:
feature/auto-increment-minor-version-on-merge

## 1. Problem

OMC's version lives in **four** committed files, all hand-set to `0.1.0`:

- `pyproject.toml` (the package version; `omc.__version__` derives from it
  via `importlib.metadata.version("omc")`).
- `.claude-plugin/plugin.json` (the Claude plugin).
- `.claude-plugin/marketplace.json` (the `omc` entry in the marketplace
  listing).
- `.codex-plugin/plugin.json` (the Codex plugin).

`omc update` runs `uv tool upgrade omc` and then each provider's
`plugin_update_argvs` (`src/omc/installer.py:run_update`). For Claude that is
`claude plugin marketplace update oh-my-clanker` followed by `claude plugin
update omc@oh-my-clanker` (`src/omc/providers/claude.py:plugin_update_argvs`).
Claude decides "is there a newer plugin?" by reading the `version` field from
the **committed** `.claude-plugin/plugin.json` / `marketplace.json` on `main`
(it git-pulls the marketplace repo and reads the file). Because that string
never changes on a merge, code lands on `main` but the installed Claude plugin
never refreshes for users.

The version carried by any individual PR is meaningless: concurrent worktrees
are all cut from the same base and all carry the same `0.1.0`. Only GitHub, at
merge time, can assign a unique, monotonically increasing number that is
immune to what the PR happened to contain.

## 2. Goal

- The human owns `MAJOR.MINOR` by hand (e.g. `0.1`, later `0.2`).
- GitHub **exclusively** owns the third number and sets it to
  `github.run_number` on every merge to `main`, ignoring whatever the PR
  carried.
- `main` therefore always carries a strictly-increasing version
  (`0.1.7 → 0.1.8 → …`; after a hand minor bump, `0.2.9 → …`), so `omc update`
  → `claude plugin update` reliably refreshes the plugin.

## 3. Mechanism

A new workflow triggers on **push to `main`** — which is what a merged PR
produces. On each run it:

1. Reads the current version from `pyproject.toml`, keeps `MAJOR.MINOR`,
   discards the old patch.
2. Composes `new = MAJOR.MINOR.${{ github.run_number }}`.
3. Stamps `new` into all four version files.
4. Commits and pushes back to `main` with `[skip ci]` in the commit message.

**Why `github.run_number`.** It is a monotonic counter GitHub maintains
entirely on its own — zero repo state, and unaffected by any PR's version
string. It does not increment for skipped runs, and it does not change on a
re-run. It never resets on a hand minor bump, but that is harmless: the
number only ever increases, so every stamped version is strictly newer than
the last, which is exactly what `claude plugin update`'s "is this newer?"
check wants.

**Loop guard.** The stamp commit carries `[skip ci]`, so GitHub skips the run
entirely: the commit does not re-trigger the workflow and does not consume a
`run_number`. This is the single mechanism that prevents an infinite
merge → stamp → merge loop.

**Concurrency.** A `concurrency:` group serializes overlapping merge runs so
two runs cannot race on the push. As a backstop, the workflow does a
`git pull --rebase` before pushing.

**Coexistence with `ci.yml`.** The existing `ci.yml` build already triggers on
`push: { branches: [main] }`. On a normal merge both `build` and
`version-stamp` run — independently, no interaction. The `[skip ci]` stamp
commit is skipped by *both* workflows (the marker suppresses all workflow runs
for that push), so it neither rebuilds nor re-stamps. Verified: no test (unit
or e2e) asserts an exact version number — the smoke test matches
`omc \S+ … from /repo`, so stamping never breaks CI.

## 4. Components

1. **`.github/workflows/version-stamp.yml`** (new)
   - `on: push: { branches: [main] }`.
   - `permissions: contents: write`.
   - A `concurrency` group keyed to the workflow + `main` so runs serialize.
   - Checks out `main`, runs the stamp script with `${{ github.run_number }}`,
     commits, `git pull --rebase`, pushes with `[skip ci]` in the message.
   - Uses the default `GITHUB_TOKEN`.
2. **`scripts/stamp_version.py`** (new)
   - Pure and unit-testable: takes the patch number as an argument, reads
     `MAJOR.MINOR` from `pyproject.toml`, and writes `MAJOR.MINOR.PATCH` into
     all four files.
   - Surgical edits: a targeted regex for the TOML `version` line, a JSON
     round-trip for the three `.json` files — nothing else in any file moves.
   - `pyproject.toml` is the single source of truth for `MAJOR.MINOR`. The
     script reads it there and **overwrites** the other three files to match,
     so a forgotten manifest edit self-heals.
3. **The four version files** — the stamp destinations. Bumping all four keeps
   `omc version`, the Claude plugin, the marketplace listing, and the Codex
   plugin in lockstep.

## 5. Conventions & how the human bumps MAJOR.MINOR

To bump the minor (or major), edit the `version` in `pyproject.toml` in a
normal PR (e.g. to `0.2.0`). On merge, the script reads `0.2` and stamps
`0.2.<run_number>`. Only `pyproject.toml` needs the hand edit — the script
overwrites the manifests to match, so they cannot drift. The patch you type
in that hand edit is irrelevant; GitHub replaces it.

## 6. Permissions & branch protection

The workflow pushes back to `main` using the default `GITHUB_TOKEN` with
`contents: write`. `main` currently has **no branch protection** (verified:
`gh api repos/chris-husse/oh-my-clanker/branches/main/protection` returns
404), so the bot can push directly.

**Note for the future:** if `main` is ever protected (required PR / required
status checks), this workflow will no longer be able to push directly and
will need an explicit bypass — a GitHub App token or a protection allowance
for the actor. This is a known, documented breakage point, not a silent one.

## 7. Edge cases

- **Concurrent merges** — the `concurrency` group serializes stamp runs; each
  run checks out the latest `main` and (with rebase-before-push) pushes
  cleanly.
- **Hand minor bump** — you set `pyproject.toml` to `0.2.0` in a PR; on merge
  the script reads `0.2` and stamps `0.2.<run_number>`. The patch does not
  reset to 0 (run_number keeps climbing) — harmless, still strictly
  increasing.
- **Feature-branch rebases** — feature branches never touch the patch line, so
  the per-merge stamp commits rebase in conflict-free. Only a deliberate
  minor-bump PR touches the version line, and that is intentional.
- **This feature merging** — once `version-stamp.yml` is on `main`, the merge
  that lands it is the first push to `main` that triggers it: run #1 stamps
  `0.1.1`.

## 8. Testing

- **Unit** (`tests/unit/test_stamp_version.py`, new): `MAJOR.MINOR` extraction
  from a `0.1.47`-style version; all four files rewritten to `X.Y.N`; the
  three JSON files stay valid and structurally unchanged except for `version`;
  idempotence (running twice with the same patch is a no-op).
- **Version-sync test** (new, small): assert the four files agree on the
  version string. No such guard exists today, and since we now write all four
  programmatically this locks it in. It complements the existing
  `tests/unit/test_plugin_manifests.py` (which checks manifest **structure**
  but not version).
- **No E2E** — the stamp only rewrites strings; unit coverage plus the
  workflow's own run on merge is sufficient.

## 9. Out of scope

- Conventional-commit-driven major/minor/patch decisions (the human owns
  `MAJOR.MINOR`).
- Changing build-provenance stamping (`hatch_build.py` / `src/omc/_buildinfo.py`)
  — that is the orthogonal `branch@commit` display, unrelated to semver.
- Publishing to PyPI, or changing which ref the Claude marketplace tracks.
