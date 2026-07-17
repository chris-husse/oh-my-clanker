# `omc watch`, faithful worktrees, `/omc:rebase-main` — the snapshot model

Approved 2026-07-17 across four design rounds. The worktree knowledge model:

- The PRIMARY root's `.gitnexus/` + `.omc/docs/` are kept current by `omc watch`.
- Cutting a worktree SNAPSHOTS main: worktrunk's `copy-ignored` step copies all
  gitignored files — `.env`, caches, AND the graph/docs — so the worktree's
  knowledge matches the code it was cut from. NO excludes.
- A worktree refreshes explicitly via `/omc:rebase-main` (also finish's first
  working step): rebase onto fresh base + re-mirror the snapshot.
- `gitnexus-explain` therefore prefers the LOCAL `.gitnexus/` snapshot when
  present, falling back to the primary root.

## `omc watch [--interval SECONDS] [--once] [--enable-documentation]`

Foreground polling loop (default 300s; Ctrl-C stops; `--once` = single tick
for external schedulers — omc never creates daemons/launchd/cron itself).

Gate: config; cwd inside the PRIMARY worktree; GitNexus CLI present (else
"run /omc:index once first — it installs GitNexus"); ensure_wt_config.

Per tick, narrated in start's `→`/`✓` style:
1. `git fetch origin <base>`. Not ON `<base>` → warn, skip (never yank the
   checkout). Dirty or diverged → warn, skip (never destructive).
2. Behind and clean → `git merge --ff-only origin/<base>`, report the range.
3. New commits → refresh the index DIRECTLY (`node <cli> analyze
   --skip-agents-md --skip-skills`) — zero LLM cost; with
   `--enable-documentation` also `node <cli> wiki --provider <configured>
   [--model …]` + wiki→`.omc/docs/gitnexus/docs` sync (LLM-heavy, hence the
   flag). These are the same operations the /omc:index and /omc:document
   skills prescribe — watch is the automation interface, skills the
   in-session interface.
4. Nothing new → `· up to date` — in LOOP mode that's the whole tick, but
   `--once` is the "refresh now" button: index (and docs when enabled) run
   unconditionally. Tick failures narrate and skip, never crash the loop.

## wt configuration (`ensure_wt_config`, called by start + watch)

- No `.config/wt.toml` → write omc's starter (with notice):
  pre-start submodule/direnv line + `[post-start] copy-ignored = "wt step
  copy-ignored"`, NO excludes. Never overwrite or edit an existing file.
- Existing file → deterministic sniff only (TOML parse: is a copy-ignored
  step wired?). Suspicious → one stderr pointer to `/omc:check-wt-config`.
- `/omc:check-wt-config` (user-facing skill, the LLM judgment step): reads
  the project's wt.toml, gets the canonical template via `omc internal
  wt-template`, and reports real insights — does it copy what matters, do
  excludes break the snapshot model, do hooks look dangerous/slow, is a
  submodule pre-start missing. Suggests edits; never edits the file.

## `/omc:rebase-main` + the revived `omc internal` namespace

`omc internal …`: hidden from --help (intercepted before argparse),
machine-readable stdout, chicken-style skill↔CLI contract.

`omc internal rebase-main [--base <b>]`:
1. Resolve primary root vs cwd worktree; in the primary → "nothing to
   rebase" (watch owns the primary), rc 0.
2. `git fetch origin <base>`; `git rebase origin/<base>`. Conflict → rc 3
   (bail), conflicted files listed, rebase left paused — never aborted or
   force-resolved in Python.
3. Mirror `.gitnexus/` and `.omc/docs/` from the primary root into the
   worktree with delete-extraneous semantics — implemented as a unit-tested
   Python shutil mirror (rsync --delete semantics, no shell, no rsync
   dependency). Sources absent → noted, skipped.
4. If needed (verify at impl): register the copied index (`node <cli> index`)
   so gitnexus commands work from the worktree.
5. Last line: `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>",
   "synced": [".gitnexus", ".omc/docs"]}` / `{"ok": false, "conflicts": […]}`.

`/omc:rebase-main` (user-facing skill): wraps the subcommand conversationally;
on bail (rc 3) guides conflict resolution. `/omc:finish` invokes it as the
FIRST working step (its inline rebase step is replaced); ordering asserted:
rebase-main < squash < build < verify < review < create-mr.

`omc internal wt-template`: prints the starter template (single source =
Python constant).

## Testing

Unit: watch tick matrix against stubs (behind→sync+index argv, off-branch/
dirty/diverged→skip, --enable-documentation gates wiki, --once); wtconfig
(create-if-absent, never-overwrite, sniff verdicts); the shutil mirror
(delete-extraneous, nested dirs, refuses paths outside its two targets);
internal rebase-main (ff path verdict line, conflict→rc 3 + paused, primary→
no-op); cli wiring incl. hidden internal interception; manifest/contract for
the two new skills; finish ordering update; explain local-first needle.

E2E (claude where LLM needed, fail-loud):
- watch --once: push a commit to the work repo's origin → tick syncs and the
  index refreshes (no tokens — direct CLI).
- rebase-main: worktree cut behind main → subcommand rebases + re-mirrors;
  snapshot equality asserted mechanically.
- **explain-the-tool judge**: `/omc:explain "explain the omc tool: what
  happens end to end when I run omc start, and which modules are involved?"`
  on /repo — judge requires real architecture (probe→slug→worktree→handoff),
  real modules cited (start.py, slug.py, providers/…), no generic essay.
- **documentation artifact**: `tests/e2e/artifacts/omc-wiki/` is a COMMITTED
  permanent artifact. The docs test mounts it into the container as the
  primary root's `.gitnexus/wiki`, runs /omc:document (incremental update —
  gitnexus wiki regenerates only stale modules; verify at impl, surface if it
  can't), syncs the refreshed wiki back OUT to the artifact dir (the diff is
  reviewable in git), then an LLM judge reads a sample and verifies the docs
  are about omc and deep enough that the system demonstrably works (real
  module names, real flows, not boilerplate). First full generation is seeded
  once, locally, and committed.
