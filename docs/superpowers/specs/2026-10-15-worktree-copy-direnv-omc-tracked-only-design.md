# A failed worktree cut fails `omc design`; worktrees copy every gitignored file except `.gitnexus/` and `target/`; the session starts under direnv

**Date:** 2026-10-15
**Slug:** `worktree-copy-direnv-omc-tracked-only`
**Status:** design, hardened with `/omc:explain` and the grug lens
**Predecessors:** `2026-10-07-fix-gitnexus-stale-index-clean-loop-design.md` (the
flat `.gitnexus/` store stays primary-only; this record stops copying it),
`2026-10-02` lifecycle split (the `omc design` / `omc implement` / `omc review`
launchers whose session plan §7 changes)

## 0. Understanding

### What the user asked for (verbatim)

> hotfixes. (1) I need the direnv to be copied into worktrunks so kache is
> enabled there from the get go. (2) ensure that .omc is always copied
> (ideally symlinked) into the worktrunks as well (3) ensure that no build
> directory like `target` etc are copied. Overall only git committed files
> should be comitted. give me a rundown of what else is currently copied and
> why we need it

Answers given during the brainstorm, in the user's words where they matter:

- Target projects: `~/Projects/hummingbird-wt` and omc itself.
- `.omc` in the worktree is a symlink when the primary's is a symlink, a
  directory copy when it is a directory ("Not difficult is it?").
- "Get rid of .gitnexus and target. the rest can stay as is." Every other
  gitignored file keeps being copied: `.env`, `.secrets/`, `kgrpc_modules/`,
  the `AGENTS.md`/`CLAUDE.md` links, `.omc/docs`.
- HARD: "Make sure that if the worktrunk cutting fails, the entire operation
  fails. Why are you continuing if the worktrunk cutting failed?"
- The session must see the `.envrc` environment ("yes make sure they are
  loaded").

### What was found while investigating (the actual bugs)

Three of the four symptoms the user saw have one cause, and it is not the
copy rules.

1. **omc hides a failed cut.** `src/omc/worktree.py:create_worktree` runs
   `wt switch --create <branch> --base <base> --no-cd --yes --format=json`,
   DISCARDS the error (`path, _ = _switch(...)`) and retries
   `wt switch <branch> --no-cd --yes --format=json`. Worktrunk v0.79 aborts
   the cut when a `pre-start` hook fails but leaves the worktree and the
   branch behind (observed in a scratch repo: `pre-start = "exit 7"` →
   `wt` exits 7, `git worktree list` and `git branch` both show the new
   entry; the retry then exits 0 with `{"action":"existing",...}`). So omc
   launched the session in a half-built worktree.
   `tests/unit/test_worktree.py:test_create_worktree_retries_without_create`
   encodes the bug as intended behaviour. The caller is
   `src/omc/start.py:run_start` (~line 188): `if path is None: raise
   OmcError`, then notification wiring, title, session exec.
2. **This very worktree is half-built.** The primary's worktrunk command log
   (`<primary>/.git/wt/logs/commands.jsonl`) shows `pre-start project …
   exit:1` at 2026-10-15T14:39:05Z: the starter's `direnv allow .` exits 1
   with "`.envrc file not found`" when the repo has no `.envrc` (reproduced).
   The `[post-start]` copy never ran, so this worktree has no `.env`, no
   `.omc/docs`, no `AGENTS.md`/`CLAUDE.md` links.
3. **hummingbird-wt's cut at 14:45 today failed the same way.** Its
   `pre-start` exited 128 at `git submodule update --init --recursive`
   (kakarot.chorse.space unreachable); `direnv allow .`, `wt step
   copy-ignored` and the two fix-up scripts after it never ran → no `.omc`,
   no direnv allow, no `kgrpc_modules`. omc continued anyway (bug 1).
4. **`.gitnexus/` is copied but never read in a worktree.**
   `src/omc/internal.py:_gitnexus` always runs from the PRIMARY root with
   `--repo <primary>`; `src/omc/gitnexus.py:snapshot_freshness` reads the
   primary. The copy (803 M in hummingbird) is dead weight that
   `src/omc/mirror.py:mirror_snapshot` then re-syncs on every
   `/omc:rebase-main`.
5. **The session starts before direnv exports.** `src/omc/session.py:
   session_plan` → `src/omc/shells/fish.py:build_invocation` produces
   `fish -i -C "…; cd <wt>; <provider argv>"`. direnv's fish hook fires on
   `fish_prompt`, which comes AFTER the `-C` command finishes, so the
   provider (and every `cargo` it runs) never sees `RUSTC_WRAPPER=kache`.
   Same for zsh/bash (`src/omc/shells/zsh.py`, `bash.py`): the startup
   command runs from the rc file before the first prompt.

### What is currently copied, and why (the requested rundown)

Worktrunk's `wt step copy-ignored` copies every gitignored path from the
primary except its built-in VCS/tool-state exclusions; a project narrows it
with `[step.copy-ignored] exclude`. In hummingbird-wt (gitignored, measured):

| Path | Size | Why a worktree wants it |
|---|---|---|
| `.omc` → `~/Projects/chicken-data/hummingbird-omc` (symlink) | link | omc's project config, skills, docs; shared across checkouts by design |
| `.secrets/` | 56 K | UAT override secrets; the stack does not start without them |
| `crates/kraken-cops-v1-proto/kgrpc_modules/` | 1.4 M | kgrpc download cache; re-download needs GitLab credentials per `build.rs` |
| `AGENTS.md`, `CLAUDE.md` (links) | links | agent instructions resolved through `.omc` |
| `.env` | small | local env (also in omc: E2E provider tokens) |
| `docker/mariadb/certs/*.pem|key` | small | generated at stack-up; copying saves a regeneration, harmless |
| test generated data, `.idea/` | small | convenience; harmless |
| `.gitnexus/` | 803 M | **never read in a worktree** (finding 4) — stop copying |
| `target/` | 12 G | build cache; the reason for the two fix-up scripts (COPS-992/1043) — stop copying |

The user's ruling: drop `.gitnexus/` and `target/`, keep the rest.

### Scope split

This omc branch carries omc changes only (§1, §2, §3, §7). The hummingbird
half (§4) is a separate commit in a hummingbird worktree, made by hand or by
a worker in that repo; the implementation plan names it as a task in another
repository so it is not forgotten. Existing worktrees keep whatever
`.gitnexus/` copy they have (§6).

## 1. A failed cut fails `omc design`

### Behaviour

`worktree.create_worktree(ctx, branch, base)` decides create-vs-re-enter
BEFORE calling `wt`, calls `wt` exactly once, and never retries:

1. `git show-ref --verify --quiet refs/heads/<branch>` (through `ctx.run`).
   Exit 0 → the branch exists → `wt switch <branch> --no-cd --yes
   --format=json` (re-enter; `wt` creates the worktree for an existing
   branch when none exists yet). Otherwise → `wt switch --create <branch>
   --base origin/<base> --no-cd --yes --format=json` (today's argv).
2. ANY non-zero `wt` exit, or unparseable/pathless JSON, raises `OmcError`
   naming the branch, the `wt` exit code, the tail of `wt`'s stderr (which
   carries the failing hook's own output and worktrunk's `✗ pre-start
   command failed: exit status: N`), and the pointer `wt config state logs`
   (the command log). `create_worktree` returns a path or raises; the
   `str | None` return and the "wt switch failed:" print go away.
3. `run_start` catches nothing: the `OmcError` propagates, `omc design`
   exits 1. No notification wiring, no terminal title, no session, no
   headless run. The `if path is None` check in `run_start` is deleted
   (unreachable).

### Decision 1(a): remove what THIS run created, nothing else

`wt switch` can leave two things behind when a hook fails: the worktree it
registered, and (with `--create`) the branch. Before calling `wt`, omc
records which of the two already exist — the branch ref (step 1 above,
needed anyway) and whether `git worktree list --porcelain` already names
`refs/heads/<branch>`. After a failed `wt`, it removes exactly what appeared
since, then raises:

- the worktree, when none was listed before: `git worktree remove --force
  <path>` with the path git now lists for the branch;
- the branch, when the ref did not exist before: `git branch -D <branch>`.

Each step runs only when its target exists now (a `--create` can fail before
either exists, for example on a bad `--base`). A cleanup failure is
narrated (`✗ could not remove …`) and the original `OmcError` still raises.
A worktree or branch that pre-existed this run is never touched: it may hold
the user's work. (The brainstorm phrased the rule as "the worktree and branch
it just created, only when the branch did not pre-exist"; hardening widened
the worktree half to the pre-existing-branch case — `wt switch <branch>`
with no worktree yet also creates one and runs the hooks — because the rule
"what this run created" is the one that makes a rerun cut fresh in every
case. Decision 1's intent is unchanged.)

Why git and not `wt remove`: `wt remove` runs its own hooks, defaults to
background removal and may prompt; two `git` commands through `ctx.run` are
deterministic, need no new stub, and act only on the path git itself lists.

Why the branch-exists probe instead of parsing `wt`'s error: `wt switch
--create` on an existing branch and `wt switch --create` with a failing
hook both exit non-zero; the probe is the only way to know which one would
have happened, and it makes `wt`'s argv predictable for the exact-argv tests
(§5). The `worktree list` probe costs one more read-only `git` call and is
the same command the cleanup needs afterwards.

### Dry run

`run_start`'s `--dry-run` prints the `--create` argv today without knowing
whether the branch exists. It gains the same probe so the printed `worktree
cmd:` is the one the real run would issue (the probe is read-only).

### What this does NOT change

`sync_base` (best-effort fetch, loud on failure) is untouched. `omc
implement` and `omc review` never cut worktrees (they resolve the record
from the current checkout) and are untouched here; §7 touches their launch.

## 2. Starter `wt.toml`: blocking copy, direnv guard, two excludes

`src/omc/wtconfig.py:WT_TEMPLATE` and omc's own `.config/wt.toml` (a copy of
the starter) become:

```toml
# Worktrunk project config, seeded by omc — faithful worktrees out of the box.
# Every gitignored file (.env, caches, .omc) is reflink-copied into new
# worktrees EXCEPT .gitnexus/ (omc queries the primary's graph) and target/
# (build cache; a fresh build is cheaper than fixing up copied artifacts).
# Refresh a worktree's .omc/docs later with /omc:rebase-main.
# Docs: https://worktrunk.dev

# Blocking setup, in order: submodules, copy, direnv. Any failure aborts the cut.
pre-start = "{ ! test -f .gitmodules || git submodule update --init --recursive; } && wt step copy-ignored && { ! test -f .envrc || ! command -v direnv >/dev/null || direnv allow .; }"

[step.copy-ignored]
exclude = [".gitnexus/", "target/"]
```

- **`! test -f .envrc ||` guard** — REQUIRED by §1: without it the starter
  fails every cut in a repo without `.envrc` (bug 2 in §0). Observed: the
  guarded line exits 0 in a repo with no `.envrc`.
- **Copy before allow** (amended at implementation review, from the plan's
  final-review ruling). The copy runs before the guarded `direnv allow .`
  because a project may gitignore its `.envrc`: only after `wt step
  copy-ignored` is it present in the new worktree for the guard to see and
  for direnv to allow, and §7's session needs that environment. A tracked
  `.envrc` (hummingbird, §4) is present either way, so §4 keeps its order.
  Proven by the real-tool test
  `test_real_wt_allows_copied_ignored_envrc_before_provider`.
- **Decision 2(a): the copy moves into the blocking `pre-start`** and the
  `[post-start]` table goes away. A background `post-start` cannot fail the
  cut and races the session (the provider may start reading `.omc` before
  the copy lands). The copy was background for one reason: with `target/`
  (12 G in hummingbird) and `.gitnexus/` (803 M) in the copy set a blocking
  hook would have stalled every cut for minutes. Both leave the set in this
  record, so what remains (dotfiles, secrets, a 1.4 M download cache, `.omc`)
  copies in well under a second and the reason is gone with the exclude.
  Worktrunk runs `pre-start` with the new worktree as cwd, so `wt step
  copy-ignored` with no flags copies primary → new worktree (observed:
  "Copied 4 files" into the new worktree). The background runner process
  worktrunk spawned for `[post-start]` (`wt hook run-pipeline` in the command
  log) disappears with it.
- **`exclude = [".gitnexus/", "target/"]`** — Decision 5. Observed in the
  scratch repo: `.gitnexus/meta.json` and `target/debug/app` absent in the
  worktree; `.env`, `.secrets/k`, the `AGENTS.md` symlink present.
- **`.omc` needs no code (Decision 6).** Observed: a symlinked `.omc` arrives
  as a symlink with the same absolute target; a directory `.omc` arrives as a
  directory with its files. Proven again by the real-tool test in §5.
- `_has_copy_ignored(text)` accepts a `copy-ignored` step in `pre-start` OR
  `[post-start]`, each either a string or a table of named steps (worktrunk
  allows both shapes). Today it inspects only `[post-start]`, which already
  flags hummingbird (copy in `pre-start`) as "suspicious" wrongly.
- `ensure_wt_config` still creates-if-absent and NEVER edits an existing
  file (unchanged contract, `tests/unit/test_wtconfig.py`). Projects that
  already have a `wt.toml` (omc itself, hummingbird) change theirs by hand;
  omc's own is part of this branch, hummingbird's is §4.
- `wtconfig.py`'s module docstring ("The starter has NO excludes") and
  `skills/check-wt-config/SKILL.md` are updated: the copy step may live in
  either hook table; excluding `.omc/docs/` breaks the snapshot model;
  excluding `.gitnexus/` and `target/` is the expected shape; and its
  "missing → `omc start`/`omc watch` will seed the starter" line names only
  `omc watch` (`ensure_wt_config`'s single caller is `watch.run_watch`;
  `omc design` never seeds it). The three needles
  `test_plugin_manifests.py:531` pins stay in the text.

## 3. `.gitnexus/` leaves the worktree snapshot

Why it was ever there: the snapshot model predates the proxy. Worktrees
once ran `gitnexus index` on their own copy so `/omc:explain` worked inside
them; that registration was removed on 2026-08-03 (comment in
`src/omc/internal.py:_rebase_main`) and `omc internal gitnexus` has pinned
`--repo <primary>` since. Every `.gitnexus` reader in `src/omc/gitnexus.py`
takes a root that its callers bind to the primary (`start.run_start`,
`internal._rebase_main`, `internal._gitnexus`, `watch._tick`). Nothing
worktree-side has read the copy since; this record removes a superseded
mechanism, not a working one.

- `src/omc/mirror.py:SNAPSHOT_DIRS` → `(".omc/docs",)`. `mirror_snapshot`,
  `mirror_dir`, `_sync_tree` are unchanged; the `OMC_REBASE_MAIN` verdict
  shape is unchanged (`synced`/`shared` simply never list `.gitnexus`).
  `mirror.py`'s docstring drops `.gitnexus/`.
- Text that calls `.gitnexus` a worktree snapshot is corrected to "knowledge
  lives in the primary; worktrees carry `.omc/docs`":
  `src/omc/distribution/AGENTS.md:27` (the behaviour layer, reaches users via
  `omc update`), `skills/rebase-main/SKILL.md` (frontmatter description +
  body), `skills/finish/SKILL.md:46`, `skills/check-wt-config/SKILL.md`,
  `src/omc/wtconfig.py` docstring and template comment.
- `README.md` is generated: regenerate via `/regenerate-readme` after the
  source edits (its "Worktrunk's `copy-ignored` copies … `.gitnexus/`" line
  and the `.gitignore` row must follow).
- Nothing deletes old `.gitnexus/` copies from existing worktrees (§6);
  `/omc:rebase-main` simply stops touching them.

## 4. hummingbird-wt `.config/wt.toml` (separate commit, other repo)

In `~/Projects/hummingbird-wt` (tracked file, last changed 159ce90c1):

```toml
pre-start = "git submodule update --init --recursive && { ! command -v direnv >/dev/null || direnv allow .; } && wt step copy-ignored"

[step.copy-ignored]
exclude = [".gitnexus/", "target/"]
```

- Order unchanged (submodules, direnv, copy). hummingbird tracks `.envrc`
  (kache setup), so no `.envrc` guard is needed there; it keeps its
  unconditional `git submodule update` because the submodule is required.
- Drop `python3 scripts/wt-sanitize-copied-cargo-target.py && python3
  scripts/wt-mirror-mtimes.py` from `pre-start`, the `target/*/build/*/out/
  build/` exclude, and the COPS-992/COPS-1043 comments that explain them.
- **Decision 3(a): delete `scripts/wt-sanitize-copied-cargo-target.py` and
  `scripts/wt-mirror-mtimes.py` in the same commit.** They are referenced
  only by `wt.toml` and historical specs/plans; once `target/` is not copied
  there is nothing for them to sanitize.
- With §1 in place, a failing `git submodule update` (GitLab unreachable)
  fails the cut loudly instead of producing a worktree without `.omc`
  (Decision 7). That is the intended behaviour: the user cuts again when the
  network is back, or cuts by hand with `--no-hooks` knowing what is missing.
- The first `cargo build` in a new hummingbird worktree is a cold build
  (kache makes it cheaper, which is why direnv must load — §7). Accepted by
  the user with Decision 5.

## 5. Testing (red first, per `.omc/config/AGENTS.md`)

### Unit (`just check`, stubs on a restricted PATH)

`tests/unit/test_worktree.py`:

- Replace `test_create_worktree_retries_without_create`: `git` stub answers
  `show-ref` with exit 1 (branch absent), `wt` stub exits 1 with stderr
  `✗ pre-start command failed: exit status: 7` → `OmcError` whose message
  contains the branch, "exit 1" and that stderr; `wt` was called EXACTLY once
  with the exact `--create` argv; `git` recorded `worktree remove --force
  <path>` and `branch -D <branch>` (the stub's `worktree list --porcelain`
  lists the branch).
- Branch exists: `show-ref` exit 0 → exactly one `wt switch <branch> --no-cd
  --yes --format=json`, path returned.
- Branch and worktree both pre-exist and `wt` fails → `OmcError`; NO
  `worktree remove`, NO `branch -D` in the git stub's calls.
- Branch pre-exists, no worktree listed before, `wt` fails and the stub
  now lists one → `worktree remove --force <path>` recorded, NO `branch -D`.
- Create fails before anything exists: `worktree list` empty, `show-ref`
  still 1 after → no remove/delete calls, `OmcError` still raised.
- `test_create_worktree_both_fail` becomes "wt not found (OSError) →
  OmcError".

`tests/unit/test_start.py`: `omc design` end-to-end with stubs where `wt`
fails → exit 1 (`OmcError` propagates through the CLI), and
`notify.wire_worktree`, `exec_interactive`, `_run_headless` were never
called (monkeypatched recorders). Dry-run prints the re-enter argv when the
git stub says the branch exists. `_make_git_stub` there answers every verb
but `rev-parse` with exit 0, so it gains a `show-ref) exit 1` arm (and an
empty `worktree) :` arm) — otherwise every design test silently flips to
the re-enter argv.

`tests/unit/test_wtconfig.py`: `WT_TEMPLATE` parses; `pre-start` contains
`wt step copy-ignored` before the direnv guard (§2, copy before allow); `step.copy-ignored.exclude ==
[".gitnexus/", "target/"]`; no `post-start` key; `ensure_wt_config` returns
"ok" for a copy step in `pre-start` (string), in `[pre-start]` (table) and
in `[post-start]`; "suspicious" when absent. The repo's own `.config/wt.toml`
equals `WT_TEMPLATE` (keeps the two in lock-step).

`tests/unit/test_mirror.py` / `test_internal.py`: `SNAPSHOT_DIRS ==
(".omc/docs",)`; a primary with `.gitnexus/` and `.omc/docs/` mirrors only
`.omc/docs` and the verdict's `synced` is `[".omc/docs"]`. The assertions
that pin `.gitnexus` in `synced` today (`test_mirror.py:42,56,208`,
`test_internal.py:366-372,402+`) flip to "not synced, worktree copy left
alone". Skill-text needles stay green: `test_plugin_manifests.py:525`
(rebase-main: `omc internal rebase-main`, `OMC_REBASE_MAIN`, `rc 3`,
`conflict`, `knowledge`), `:531` (check-wt-config: `omc internal
wt-template`, `.config/wt.toml`, `never edit`), `:537` (finish ordering).

`tests/unit/test_session.py` / `test_shells.py`: §7's exact-argv tests.

### Real-tool (no stubs; a missing tool is `pytest.fail` naming the install
command — never a skip)

In a throwaway git repo seeded with the §2 template, driving the REAL `wt`
(`tests/conftest.py` already makes git hermetic; these tests also point
`HOME` and `XDG_CONFIG_HOME` at tmp so the developer's
`~/.config/worktrunk/config.toml` — custom worktree paths, hooks — cannot
leak in; `--yes` answers worktrunk's hook-approval gate, observed). The
missing-tool check follows `tests/unit/test_installed_wheel.py:_tool`
(`shutil.which` → `pytest.fail` naming the install command):

- `pre-start = "exit 7"` → `wt switch --create` exits non-zero; and through
  `create_worktree` → `OmcError` and the worktree and branch are gone
  afterwards (the real `git` cleanup).
- Primary with symlinked `.omc` → worktree `.omc` is a symlink to the same
  target; primary with directory `.omc` → directory with the same files.
- Primary with `.gitnexus/x`, `target/x`, `.env` → worktree has `.env`, not
  the other two.
- Repo without `.envrc` → the starter's `pre-start` exits 0 (bug 2 stays
  dead).
- §7: a worktree with an allowed `.envrc` exporting a marker; the launched
  command (a stub provider that prints its env) sees the marker; a blocked
  `.envrc` makes the launch exit non-zero with direnv's "is blocked" line.

**Tier constraint the plan must honour.** `.github/workflows/ci.yml` runs
`just check` on `ubuntu-latest` with only `fish`, `uv` and `just`
installed: no `wt`, no `direnv`. Under the no-skip rule, real-tool tests in
the unit tier fail CI until the workflow installs both (`wt`: the prebuilt
musl release tarball recipe already in `docker/Dockerfile.e2e`; `direnv`:
`apt-get install direnv`). The alternative is the Docker E2E tier
(`tests/e2e`, image has `wt`; `direnv` would be added to
`docker/Dockerfile.e2e`). Recommendation: unit tier plus the two CI install
steps; these tests take seconds and `just check` is the gate run after every
change. The plan settles it and includes the CI/Dockerfile edit as a task.

### E2E that already covers the real path

`tests/e2e/test_e2e_start.py` cuts a worktree with real `wt` via `omc
design`; with §2 its `pre-start` now runs `wt step copy-ignored` in the
container (no `direnv` there → guard skips). Its idempotent re-run assertion
exercises the branch-exists path. No new Docker test is required for §1–§3.

## 6. Out of scope

- Changing what else is copied (user: "the rest can stay as is").
- Rewriting existing projects' `wt.toml` through omc (`ensure_wt_config`
  never edits; `/omc:check-wt-config` advises).
- Cleaning old `.gitnexus/` copies out of existing worktrees.
- hummingbird's cold `target/` on first build (accepted with Decision 5).
- A `--no-hooks` escape hatch in omc (the user cuts by hand if they want a
  worktree despite a failing hook).

## 7. The session runs under `direnv exec .`

### Behaviour (Decision 4(a))

When `direnv` is on PATH, the provider's startup argv is prefixed with
`direnv exec .`: `["direnv", "exec", ".", *provider_argv]`. The shell has
already `cd`'d into the worktree (fish: `cd <wt>` precedes the startup
command in `-C`; zsh/bash: in the rc file), and `run_headless` runs with
`cwd=<worktree>`, so `.` is the worktree on every path. `direnv exec`
searches upward for `.envrc` like the hook does; with none it runs the
command unchanged (observed, exit 0); allowed → environment loaded
(observed, marker visible); blocked → `direnv: error … is blocked. Run
`direnv allow`…`, exit 1 — the launch fails loudly with direnv's own fix
line, which is the right outcome: a worktree whose `.envrc` is not allowed
is exactly the half-built state §1 exists to surface.

Applies to `omc design` (interactive and `--headless`), `omc implement`,
`omc review` and `run_headless`: all four build their argv through
`session_plan` / `run_headless` in `src/omc/session.py`, so the prefix lives
in one place. After the provider exits the user lands in the normal
interactive shell where direnv's own hook takes over.

### Where the PATH fact comes from

`session_plan` is pure (no I/O; `cwd` is a placeholder at design time). The
fact "direnv is on PATH" is resolved ONCE by the launcher through the
`ToolContext` boundary — `src/omc/probe.py` gains `has_direnv(ctx) -> bool`
using `toolctx.tool_version(ctx, ["direnv", "version"])` (a real run, like
every other probe; `direnv version` prints e.g. `2.38.2`) — and passed in as
a keyword `direnv: bool = False` to `session_plan` and `run_headless`.
`run_headless` keeps its five positional parameters (a test contract). A
`False` default keeps every existing `session_plan`/`run_headless` test
byte-identical. `--dry-run` resolves the same fact so the printed
`session argv` is the one the real launch uses.

### Tests

- Red-first real-launch test (§5, real `direnv`): a throwaway worktree with
  an allowed `.envrc` exporting `OMC_TEST_MARK`; `run_headless(..., direnv=
  True)` with a stub provider that prints its environment → the marker is in
  stdout. Fails on today's launch (no prefix). Blocked `.envrc` → non-zero
  exit and direnv's "is blocked" message on stderr.
- Unit exact-argv: `session_plan(..., direnv=True).session_argv[:3] ==
  ["direnv", "exec", "."]` and the provider argv follows unchanged;
  `direnv=False` → today's argv. The fish invocation's `-C` string ends with
  `direnv exec . claude …`. `has_direnv` with a stub `direnv` on PATH →
  True; empty PATH → False.

## 8. Risks

- **A failing hook now blocks work that used to "succeed".** Intended
  (Decision 7). The error names the hook, exit code and log pointer, so the
  fix is one command away. Hooks that need the network (hummingbird's
  submodule update) fail offline; that is reality surfacing, not a
  regression in omc.
- **Cleanup removes the wrong thing.** Bounded by construction (§1): only
  what this run created — the worktree when git listed none for the branch
  before the call (the path git lists for that branch now), the branch when
  its ref did not exist before. A worktree or branch that pre-existed this
  run is never touched. Accepted residual: a concurrent process that creates
  the same branch or worktree between omc's probe and a failed `wt` call is
  indistinguishable from this run's own and is removed; omc is a
  single-user launcher and the window is one `wt` call.
- **Projects with an old starter `wt.toml`.** Their copy stays in
  `[post-start]` (works, background) and their `direnv allow .` still fails
  the cut without `.envrc` — but omc now reports that failure instead of
  hiding it, and `/omc:check-wt-config` says what to change.
  `ensure_wt_config` keeps "never edit" (D-table).
- **direnv noise.** `direnv exec` prints `direnv: loading …` / `direnv:
  export +X` on stderr before the TUI starts; harmless, and the TUI clears
  the screen.
- **A `.envrc` that changes the shell for the provider.** It is the user's
  own `.envrc`, which they already run every cargo command under; the
  provider seeing the same environment is the point.
- **`direnv exec .` searches upward.** A repo without `.envrc` under a parent
  directory that has one gets that parent's environment — the same thing
  the user's interactive shell does in that directory, and only if the user
  already allowed it (otherwise their own shell would fail there too).

## 9. Decisions taken during brainstorm

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Half-built worktree after a failed cut | omc removes the worktree and branch it just created — only when the branch did not pre-exist | Otherwise a rerun re-enters the broken worktree silently through the branch-exists path |
| 2 | Where the starter copies | Blocking `pre-start`, `[post-start]` removed | A background copy cannot fail the cut and races the session |
| 3 | hummingbird's two `target/` fix-up scripts | Deleted in the same hummingbird commit | Nothing runs them once `target/` is not copied |
| 4 | Session environment | `direnv exec .` prefix on the provider argv when direnv is on PATH | direnv's shell hook fires only at the prompt; the agent starts before it |
| 5 | Copy set | Everything gitignored except `.gitnexus/` and `target/` | User's explicit ruling ("the rest can stay as is") |
| 6 | `.omc` in worktrees | Worktrunk's native copy: symlink stays a symlink, directory is copied | Already the behaviour (observed); proven by a real-tool test, no code |
| 7 | Hook failure (incl. submodule/network) | Fatal to the cut, `omc design` exits 1 | User's hard requirement; fail loud, no silent continue |

## 10. Hardening notes

Each section went through `/omc:explain` (GitNexus graph of `main` at
`da87c41`, current; Spectrum source unavailable: non-Kraken origin) and the
grug lens, then the whole record once more. What changed:

1. **§1 (grug `cut-point`, Important → fixed).** The cleanup guard was keyed
   on "did the branch pre-exist"; `wt switch <branch>` for a branch with no
   worktree also creates one and runs the hooks, so a hook failure there
   left a half-built worktree that the next run re-entered silently. Rule
   rewritten to "remove exactly what this run created", both facts probed
   before the call. Decision 1's intent unchanged.
2. **§1 (explain).** `tests/unit/test_start.py:_make_git_stub` answers every
   verb but `rev-parse` with exit 0; without a `show-ref) exit 1` arm the
   new probe flips every design unit test to the re-enter argv. Recorded in
   §5.
3. **§2 (grug `fence`, Important → fixed).** Stated why the copy was
   background (the 12 G `target/`), so nobody re-backgrounds it later.
4. **§2 (explain).** `ensure_wt_config` is called only from
   `watch.run_watch`, never from `omc design` (graph: one caller). The
   check-wt-config skill's "`omc start`/`omc watch` will seed" line is wrong
   on the `omc start` half; corrected with the other §2 text edits.
5. **§3 (grug `fence`, Important → fixed).** Stated where the `.gitnexus`
   snapshot came from (pre-proxy design, registration removed 2026-08-03).
6. **§3 (explain).** Every `.gitnexus` reader in `gitnexus.py` is bound to
   the primary by its callers (`start.run_start`, `internal._rebase_main`,
   `internal._gitnexus`, `watch._tick`); `snapshot_freshness` has six
   callers, all primary-rooted. Confirms "never read in a worktree".
7. **§4 (explain, hummingbird checkout).** Its own `wt.toml` comment says the
   old sub-path exclude was best-effort because worktrunk copies `target/`
   as a directory entry; the whole-directory exclude was observed to work.
   The scripts are referenced only by `wt.toml`, themselves and four
   historical docs.
8. **§5 (explain).** `ci.yml` installs only fish/uv/just: the tier
   constraint and the two install steps are recorded; real-`wt` tests must
   isolate `HOME`/`XDG_CONFIG_HOME`; `test_internal.py` pins and the three
   skill-needle tests listed.
9. **§7 (explain).** All four shells route the startup line through
   `shells/base.py:joined_startup`, so the prefix needs no shell change;
   `test_session`'s `FakeCtx` has only `run`, which is why the direnv fact is
   passed in rather than probed inside `run_headless`. Parent-directory
   `.envrc` behaviour added to §8.
10. **Whole record (grug `spec`).** 0 Important, 1 Minor (the §3 prose
    fact lives in seven files; the plan edits them in one task).

Observed, not assumed (scratch repo, worktrunk v0.79.0, direnv 2.38.2, this
machine): starter `pre-start` exits 0 without `.envrc`; "Copied 4 files" into
the new worktree with `.gitnexus/` and `target/` absent and `.env`,
`.secrets/`, the `AGENTS.md` symlink present; symlinked `.omc` → symlink
with the same absolute target; directory `.omc` → directory with its files;
`pre-start = "exit 7"` → `wt` exit 7, worktree and branch both left behind,
`wt switch <branch>` afterwards → `{"action":"existing"}` exit 0; `direnv
exec .` no `.envrc` → exit 0 unchanged, blocked → exit 1 "is blocked. Run
`direnv allow`", allowed → marker exported.

## Implementation review

### 2026-10-15 — Claude (Opus 5.5), `/omc:audit`

1. **Important · code deviates from the record · §1 Decision 1(a) ·
   `src/omc/worktree.py:60-95`.** Cleanup after a failed `wt` was gated on an
   extra "ownership evidence" check (worktrunk's `◎ Running pre-start … hook @
   <path>` stderr line string-equal to git's listed path). Worktrunk prints
   `~/…` for worktrees under `$HOME`, so cleanup never ran there and the next
   `omc design` re-entered the half-built worktree. **Fixed:** the gate is
   removed; cleanup follows §1's before/after probe rule. Red first: the
   real-`wt` test `test_create_worktree_cleans_real_failed_cut[under-home]`
   failed, then passed. The two unit tests that pinned the gate are gone. The
   race residual the gate covered is now stated in §8.
2. **Important · record is stale · §2 template, §5 `test_wtconfig` bullet ·
   `src/omc/wtconfig.py:35-36`, `.config/wt.toml`.** The implementation copies
   before the guarded `direnv allow .` (plan's final-review ruling), so a
   gitignored `.envrc` exists when approval runs. **Amended:** the §2 template
   and order now read submodules, copy, direnv, with a "copy before allow"
   bullet; the §5 bullet now says the copy runs before the guard. This
   contradicts no row of §9 (Decision 2 fixes where the copy runs, not the
   order).
3. **Minor · record is stale · §8 "Cleanup removes the wrong thing".** It
   said a pre-existing branch never triggers cleanup, which contradicts §1's
   hardened rule. **Amended** to "only what this run created". The §9
   Decision 1 row is left as the brainstorm phrased it; §1 already records
   the widening.
4. **Minor · code deviates (complexity) · §2 `_has_copy_ignored` ·
   `src/omc/wtconfig.py:93-200`.** It is about 110 lines of shell lexing
   (heredoc delimiters, here-strings, command-start keywords) whose only job
   is deciding whether to print a `/omc:check-wt-config` hint. Listed, not
   gated; simplifying it is a follow-up.
5. **Minor · code deviates (cosmetic) · §2 skill text ·
   `skills/check-wt-config/SKILL.md:41`.** `**never edit` was lowercased at
   sentence start. **Fixed:** restored to `**Never edit`.

Gate repairs during the post-fix gate (verify was red twice for reasons
outside this record; both repairs are test-harness only):

- `tests/e2e/test_e2e_explain_sources.py:146`: the echo-evidence assertion
  now also accepts the finding's claim text ("flipping a coin"), because
  models paraphrase the cited finding.
- `tests/e2e/conversation.py`: `parse_claude_stream` now accepts a foreground
  Agent the CLI later moves to the background (`task_started
  is_backgrounded:false` → `task_updated patch.is_backgrounded:true`), and
  still rejects an unmatched notification. Covered by new red-first tests in
  `tests/unit/test_e2e_conversation.py`.

Post-fix gate: check 1582 passed; build clean; verify golden 5 passed,
parallel 59 passed.

Finish-stage review repairs (same day):

- Project review, Important: `tests/unit/test_worktrunk_real.py` inherited
  the host's `DIRENV_*` state, so under a shell where direnv had loaded an
  `.envrc`, `direnv exec .` reverted the stub `PATH` and the host's real
  provider CLI ran. **Fixed:** the fixture env drops every `DIRENV_*` and
  `OMC_*` key. Red first: 4 failed under `direnv exec`, then 10 passed.
- Grug, Important, `grug:too-complex-for-grug` at
  `src/omc/wtconfig.py:93-192` (finding 4 above). **Fixed** without changing
  behaviour: a token regex plus `shlex` dequoting replaces the
  character-by-character state machine (100 → 67 lines). The
  `test_wtconfig.py` assertions are unchanged, and a 1M-input differential
  fuzz against the old helpers found no mismatches on valid shell.
- Grug, Minor, `src/omc/worktree.py:76-104`: a successful failed-cut cleanup
  prints nothing. Listed, not gated.
- Grug re-run: the simplified lexing is still Important; the user ruled
  to waive it (see Deliberate complexity).
- Project review re-run, Important: the same fixture still inherited host
  `WORKTRUNK_*` config overrides, so a host `WORKTRUNK_WORKTREE_PATH` made
  parallel tests collide. **Fixed** in a user-authorized third gate cycle:
  the fixture also filters `WORKTRUNK_*` and `GIT_*` (its hermetic
  git config is re-set explicitly after the filter). Minor, listed: the `src/omc/internal.py:226` docstring "local
  snapshot" wording; the pre-existing `skipif` at
  `tests/unit/test_awscreds.py:398` on main.

## Deliberate complexity

- `grug:too-complex-for-grug` @ `src/omc/wtconfig.py:93-159` (waived at
  `/omc:finish` review, 2026-10-15, user ruling): the copy-ignored check
  must not misjudge copy text inside heredocs, echo strings or comments;
  `tests/unit/test_wtconfig.py` pins those cases.
