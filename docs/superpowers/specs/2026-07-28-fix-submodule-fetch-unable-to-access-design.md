# Design: submodule-proof omc's base-branch fetches

- **Date:** 2026-07-28
- **Slug:** `fix-submodule-fetch-unable-to-access`
- **Ticket:** none (free-text report)
- **Status:** approved

## Problem

On repos with git submodules (observed: hummingbird-bridge, submodules
`git/event-schemas` and `git/ktls-rs`), omc's watch loop dies every tick with
`✗ fetch failed: ... fatal: unable to access '`. Git's default
`fetch.recurseSubmodules=on-demand` makes `git fetch origin <base>` recurse
into submodules whenever fetched superproject commits move a gitlink; the
submodule URLs are not reachable with the watch process's credentials, so the
whole fetch exits non-zero — even though the superproject refs omc actually
needs arrived fine.

Two secondary defects compound it:

1. The watch reports the *first* 200 chars of stderr (`watch.py`), which keeps
   git's progress noise (`From …`, `Fetching submodule …`) and cuts the
   `fatal:` line mid-URL.
2. None of the fetch sites redact credentials that git can embed in reported
   URLs (`https://oauth2:token@host`).

omc never needs submodule content: change detection is `rev-list` over
`origin/<base>`, and rebase moves gitlink pointers without submodule network
access.

## Approaches considered

- **A — per-site `--recurse-submodules=no` flag (chosen).** Add the flag to
  each project-repo fetch invocation with a quirk comment at the code site
  (repo convention). Three small diffs; each site keeps its own, genuinely
  different error handling.
- **B — central `fetch_base()` helper.** Rejected: the sites share only the
  argv, not error handling; indirection for a one-flag win, and it fights the
  "document quirks at the dependent site" convention.
- **C — config/env (`-c fetch.recurseSubmodules=false` via ToolContext).**
  Rejected: silently changes git behavior for every subprocess omc spawns,
  including project hooks and stage skills that are not omc's to configure.

## Design

### 1. Fetch sites gain `--recurse-submodules=no`

All three fetches of the user's project repo become
`git fetch --recurse-submodules=no origin <base>`:

- `src/omc/watch.py` `_tick` — the reported bug.
- `src/omc/internal.py` `rebase-main` — hits the identical failure on the next
  `/omc:rebase-main` / `/omc:finish` in such a repo; hard-fails today.
- `src/omc/worktree.py` `sync_base` — best-effort, but currently yields a
  stale worktree cut on these repos.

The watch site carries the full rationale comment (on-demand recursion +
unreachable submodule credentials; omc only needs `origin/<base>` refs); the
other two get a one-liner pointing at the same reason.

**Deliberately untouched:** `src/omc/gitnexus.py` (fetch of omc's own GitNexus
install clone — approved-source repo, no submodules; decided: no flag, YAGNI)
and `src/omc/dependency.py` (`git clone --no-checkout` — clone does not
recurse submodules by default). Rebase itself does not fetch and needs no
change.

### 2. Fetch-failure message: tail, not head — and redacted

Replace the head-slice in `watch.py` with a module-private helper
`_fetch_error(stderr) -> str` that:

- keeps only lines starting with `fatal:` or `error:` (fallback: last
  non-empty line) — git puts the cause at the end of stderr;
- caps the result at 300 chars (decided over keeping 200);
- passes the result through the existing `redact_userinfo`
  (`src/omc/gitnexus.py`) so embedded credentials never reach the terminal.
  Verified: it is a plain `re.sub` over the whole string, so multi-line
  stderr is fine. `watch.py` and `internal.py` already import from
  `.gitnexus`; only `worktree.py` gains a new import.

The outcome-token protocol is unchanged — still `fetch-failed`, still
quiet-on-repeat. The two other touched sites also wrap their reported stderr
in `redact_userinfo` (no truncation change there — they already print in
full; `internal.py`'s text feeds the `OMC_REBASE_MAIN` failure verdict, whose
shape stays identical).

### 3. Error handling / loop behavior

The loop already survives fetch failures (quiet token, retry next tick); the
flag makes this failure stop happening, the message fix makes any remaining
failure diagnosable. No new outcome tokens.

**One follow-on change (empirically verified during plan pressure-testing):**
once a gitlink-moving sync lands, the submodule working dir is stale and
`git status --porcelain -uno` reports ` M sub` forever — the non-rebase
watch would sync one gitlink bump and then wedge at the dirty gate
("dirty — skipping sync") on every later tick. The dirty gate therefore
gains `--ignore-submodules=all`. Verified safe: both `merge --ff-only` and
`git rebase` succeed with a stale gitlink present (git never touches
submodule content), so submodule staleness cannot endanger the sync the
gate protects. Rebase mode (`--autostash`) already absorbs the staleness
and needs no change; `rebase-main`'s plain rebase also succeeds unchanged.

### 4. Testing

Real-git fixtures in `tmp_path`, matching `tests/unit/test_watch.py` style:

- **Submodule fixture:** remote superproject + local submodule repo; the
  watching clone has the submodule initialized; the submodule's origin URL is
  then rewired to a nonexistent path and the remote's `main` advances with a
  gitlink bump. Without the fix, `git fetch` exits non-zero (reproduces the
  bug); with the fix, `_tick` returns `synced`/`refreshed`.
- Same fixture reused to assert `omc internal rebase-main` succeeds (verdict
  `ok: true`) and `sync_base` returns `True` with no warning.
- **`_fetch_error` unit tests:** multi-line git stderr → only the `fatal:`
  line survives; embedded `user:token@` is redacted; over-long input is
  capped; input without `fatal:`/`error:` lines falls back to the tail.
- **File-protocol quirk (hardening finding):** git ≥ 2.38 defaults
  `protocol.file.allow=user`, which blocks submodule clone/fetch over
  local-path remotes. The fixture must pass `-c protocol.file.allow=always`
  on `git submodule add` / `submodule update --init`. (Empirically checked
  on git 2.54: the restriction blocks submodule *clones* only — on-demand
  *fetches* of an already-cloned submodule still succeed over file protocol
  — so rewiring the submodule's origin URL to a nonexistent path is REQUIRED
  to make the fixture's recursive fetch fail.)
- Fallback if on-demand recursion proves unreproducible on some git version:
  assert at the argv level via a recording `ToolContext`. Real-git is the
  default.

### 5. Scope boundaries

No changes to `wt` worktree creation, dependency indexing, or the depwatch
loop. Implementation plan lives at
`docs/superpowers/plans/2026-07-28-fix-submodule-fetch-unable-to-access-plan.md`;
its tasks carry `Model:` lines per the behavior layer's model-tier policy
(cheap/fast tier never used).
