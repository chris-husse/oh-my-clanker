# `omc watch --rebase` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--rebase` flag to `omc watch` that syncs even dirty or diverged checkouts via `git rebase --autostash origin/<base>` instead of warn-and-skip.

**Architecture:** One new branch inside `_tick`'s per-tick ladder (`src/omc/watch.py`): when the flag is set and the checkout is behind, rebase with autostash instead of consulting the dirty/diverged guards and ff-merging. Mid-rebase conflicts abort-and-restore; a zero-exit rebase is checked for unmerged paths (`git ls-files -u`) because an autostash-pop conflict also exits 0. The flag is CLI-only (no config schema change), threaded `cli.py → run_watch → _tick` exactly like `--auto-build`.

**Tech Stack:** Python 3 stdlib (argparse, subprocess via `ToolContext.run`), pytest with real-git fixtures.

**Spec:** `docs/superpowers/specs/2026-07-19-watch-ignore-dirty-flag-design.md` — read it before starting any task.

## Global Constraints

- Without `--rebase`, behavior stays bit-for-bit today's: dirty → skip, diverged → skip, clean → ff-merge. `test_tick_refuses_dirty_tree` must keep passing UNMODIFIED.
- Off-branch stays untouchable in all modes — the rebase path sits AFTER the off-branch/fetch/behind checks.
- Quiet-token convention: the new repeatable outcomes (`conflicted`, `rebase-failed`, `autostash-conflict`) narrate only when the outcome changed since the last tick (use the existing `quiet()` closure).
- Tick failures never crash the loop — every failure path returns a token.
- Narration style: `→`/`✓`/`✗`/`·` prefixes via `_say()`, matching existing lines.
- Run tests with `uv run pytest tests/unit/test_watch.py -q` (and `tests/unit/test_cli.py` where touched). Full suite before each commit: `uv run pytest tests/unit -q`.
- Model-tier policy: each task carries a `Model:` line naming a tier (top / heavy coding / standard coding); tiers resolve at dispatch time; the cheap/fast tier is never used.

---

### Task 1: Rebase path in `_tick`

**Model:** heavy coding tier (single file but git-edge-case dense: abort restoration, zero-exit autostash conflict detection).

**Files:**
- Modify: `src/omc/watch.py` (`_tick`, lines ~218–277; module docstring lines 1–8)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: existing helpers `_out(ctx, argv, cwd)`, `_say(msg)`, `_refresh_index(ctx, cfg, root, enable_documentation)`, the `quiet(token, msg)` closure inside `_tick`; test fixtures `_git`, `_repo_with_origin`, `_push_remote_commit`, `_ctx_with_node_stub`.
- Produces: `_tick(..., rebase: bool = False)` keyword — Task 2 threads it from `run_watch`. Outcome tokens: `"synced"` (success), `"conflicted"`, `"rebase-failed"`, `"autostash-conflict"`. Test helper `_push_remote_edit(origin, tmp_path)` (remote edit of `f.txt`, used for conflict tests).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_watch.py`, after `_push_remote_commit` (helper) and after `test_tick_refuses_dirty_tree` (tests):

```python
def _push_remote_edit(origin, tmp_path):
    """Advance origin/main with an edit to f.txt (conflicts with local f.txt changes)."""
    other = tmp_path / "other-edit"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / "f.txt").write_text("remote edit\n")
    _git("add", ".", cwd=other)
    _git("commit", "-qm", "remote edit", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)
```

```python
def _tick_rebase(ctx, repo, last=None):
    from omc.watch import _tick

    return _tick(
        ctx,
        Config(),
        str(repo),
        enable_documentation=False,
        force_refresh=False,
        last=last,
        rebase=True,
    )


def test_tick_rebase_syncs_dirty_tree(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)  # remote adds new.txt — no overlap with f.txt
    (repo / "f.txt").write_text("uncommitted edit\n")
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "synced"
    assert "rebased main" in capsys.readouterr().err
    assert (repo / "new.txt").exists()  # sync actually happened
    assert (repo / "f.txt").read_text() == "uncommitted edit\n"  # autostash restored the dirt
    assert "analyze" in calls.read_text()  # action tick -> index refresh


def test_tick_rebase_replays_local_commits(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    (repo / "local.txt").write_text("local\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "local work", cwd=repo)  # diverged: ahead 1, behind 1
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "synced"
    assert (repo / "new.txt").exists() and (repo / "local.txt").exists()
    subjects = subprocess.run(
        ["git", "log", "--format=%s", "-2"], cwd=repo, capture_output=True, text=True
    ).stdout.splitlines()
    assert subjects == ["local work", "remote change"]  # replayed ON TOP of origin/main


def test_tick_rebase_conflict_aborts_and_restores(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_edit(origin, tmp_path)  # remote edits f.txt
    (repo / "f.txt").write_text("conflicting local\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "local conflicting", cwd=repo)
    (repo / "g.txt").write_text("dirt\n")
    _git("add", "g.txt", cwd=repo)  # tracked dirt on top — must survive the abort
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert _tick_rebase(ctx, repo) == "rebase-failed"
    assert "aborted, checkout restored" in capsys.readouterr().err
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert head_after == head_before  # abort restored HEAD
    assert (repo / "g.txt").read_text() == "dirt\n"  # and the autostashed dirt
    assert not calls.exists()  # no index refresh on failure


def test_tick_rebase_autostash_conflict_warns_and_skips_refresh(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_edit(origin, tmp_path)  # remote edits f.txt
    (repo / "f.txt").write_text("dirty conflicting edit\n")  # UNCOMMITTED same-file edit
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "autostash-conflict"
    assert "safe in git stash" in capsys.readouterr().err
    unmerged = subprocess.run(
        ["git", "ls-files", "-u"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert unmerged  # tree left with conflict markers to resolve
    stashes = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "autostash" in stashes  # changes parked in the stash too
    assert not calls.exists()  # NOT an action tick — no index refresh


def test_tick_rebase_conflicted_tree_skips_quietly(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_edit(origin, tmp_path)
    (repo / "f.txt").write_text("dirty conflicting edit\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "autostash-conflict"
    _push_remote_commit(origin, tmp_path)  # behind again, tree still conflicted
    capsys.readouterr()
    assert _tick_rebase(ctx, repo, last="autostash-conflict") == "conflicted"
    assert "unmerged paths" in capsys.readouterr().err  # token changed -> narrates once
    assert _tick_rebase(ctx, repo, last="conflicted") == "conflicted"
    assert capsys.readouterr().err == ""  # same token -> silent (quiet convention)


def test_tick_rebase_clean_tree_still_syncs(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "synced"
    assert "rebased main" in capsys.readouterr().err
    assert (repo / "new.txt").exists()  # rebase fast-forwards a clean checkout
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -q -k rebase`
Expected: all 6 FAIL with `TypeError: _tick() got an unexpected keyword argument 'rebase'`

- [ ] **Step 3: Implement the rebase path in `_tick`**

In `src/omc/watch.py`, add `rebase: bool = False` to `_tick`'s keyword-only params and extend its docstring:

```python
def _tick(
    ctx: ToolContext,
    cfg: Config,
    root: str,
    *,
    enable_documentation: bool,
    force_refresh: bool,
    last: str | None = None,
    rebase: bool = False,
) -> str:
    """One tick; returns an outcome token. Repeatable QUIET outcomes (up to
    date, off-branch, dirty, diverged, fetch-fail, conflicted, rebase-failed,
    autostash-conflict) narrate only when the outcome CHANGED since the last
    tick — a 30s loop must not spam identical lines. Action outcomes (sync,
    refresh) always narrate. With rebase=True the dirty/diverged skips are
    replaced by `git rebase --autostash` (the user's explicit opt-in)."""
```

Then insert the rebase branch immediately AFTER the `behind in ("", "0")` block and BEFORE the `ahead` check (a clean, non-diverged checkout rebases to the identical fast-forward result, so this one path covers dirty, diverged, and clean alike):

```python
    if rebase:
        if _out(ctx, [ctx.git_bin, "ls-files", "-u"], root):
            return quiet("conflicted", "· unmerged paths in the tree — resolve them, skipping sync")
        old = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
        cp = ctx.run([ctx.git_bin, "rebase", "--autostash", f"origin/{base}"], cwd=root)
        if cp.returncode != 0:
            abort = ctx.run([ctx.git_bin, "rebase", "--abort"], cwd=root)
            if abort.returncode != 0:
                _say(f"✗ rebase --abort also failed: {(abort.stderr or '').strip()[:200]}")
            return quiet(
                "rebase-failed",
                f"✗ rebase onto origin/{base} failed — aborted, checkout restored; resolve manually",
            )
        # A conflicting autostash pop still exits 0: git rebases HEAD, leaves the
        # tree with conflict markers AND keeps the changes in stash@{0}. The exit
        # code cannot distinguish this from success — unmerged paths can.
        if _out(ctx, [ctx.git_bin, "ls-files", "-u"], root):
            return quiet(
                "autostash-conflict",
                "✗ rebased, but restoring your uncommitted changes conflicted — "
                "resolve the markers; your changes are also safe in git stash",
            )
        new = _out(ctx, [ctx.git_bin, "rev-parse", "--short", "HEAD"], root)
        _say(f"✓ rebased {base}: {old}..{new} ({behind} commits)")
        _refresh_index(ctx, cfg, root, enable_documentation)
        return "synced"
```

The existing ahead/dirty/ff-merge code below stays byte-for-byte unchanged.

Also update the module docstring (lines 6–7) from:

```
LLM-generated wiki. Never destructive: off-branch, dirty, or diverged
checkouts are warned about and left alone.
```

to:

```
LLM-generated wiki. Never destructive by default: off-branch, dirty, or
diverged checkouts are warned about and left alone — --rebase is the explicit
opt-in past the dirty/diverged skips (autostash rebase; conflicts abort and
restore); off-branch checkouts are never touched in any mode.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch.py -q`
Expected: ALL pass — the 6 new tests AND every pre-existing test (especially `test_tick_refuses_dirty_tree` and `test_tick_syncs_and_reindexes`, which pin the no-flag behavior).

- [ ] **Step 5: Commit**

```bash
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "feat: rebase path in watch tick (--rebase core)"
```

---

### Task 2: CLI plumbing for `--rebase`

**Model:** standard coding tier.

**Files:**
- Modify: `src/omc/watch.py` (`run_watch`, lines ~280–335)
- Modify: `src/omc/cli.py` (parser lines ~45–59, dispatch lines ~116–129)
- Test: `tests/unit/test_watch.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `_tick(..., rebase=...)` from Task 1; existing `_run_once` test helper.
- Produces: `run_watch(ctx, cfg, *, interval=30, once=False, enable_documentation=False, auto_build=False, rebase=False)`; argparse flag `--rebase` → `args.rebase`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_watch.py`, extend `_run_once` with a passthrough kwarg:

```python
def _run_once(repo, ctx, *, enable_documentation=False, rebase=False):
    old = os.getcwd()
    os.chdir(repo)
    try:
        return run_watch(
            ctx,
            Config(),
            interval=1,
            once=True,
            enable_documentation=enable_documentation,
            rebase=rebase,
        )
    finally:
        os.chdir(old)
```

and add:

```python
def test_watch_rebase_flag_threads_through(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    (repo / "f.txt").write_text("uncommitted edit\n")
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx, rebase=True) == 0
    assert "rebased main" in capsys.readouterr().err
    assert (repo / "new.txt").exists()
    assert (repo / "f.txt").read_text() == "uncommitted edit\n"
```

In `tests/unit/test_cli.py`, next to `test_watch_default_interval_is_30s`:

```python
def test_watch_rebase_flag_default_off():
    from omc.cli import build_parser

    assert build_parser().parse_args(["watch"]).rebase is False
    assert build_parser().parse_args(["watch", "--rebase"]).rebase is True
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py::test_watch_rebase_flag_threads_through tests/unit/test_cli.py::test_watch_rebase_flag_default_off -q`
Expected: FAIL — `run_watch() got an unexpected keyword argument 'rebase'` and `AttributeError: ... 'rebase'`.

- [ ] **Step 3: Thread the flag**

`src/omc/watch.py`, `run_watch` signature gains the kwarg and forwards it:

```python
def run_watch(
    ctx: ToolContext,
    cfg: Config,
    *,
    interval: int = 30,
    once: bool = False,
    enable_documentation: bool = False,
    auto_build: bool = False,
    rebase: bool = False,
) -> int:
```

and in the loop body:

```python
            last = _tick(
                ctx,
                cfg,
                root,
                enable_documentation=enable_documentation,
                force_refresh=once,
                last=last,
                rebase=rebase,
            )
```

`src/omc/cli.py`, after the `--auto-build` argument:

```python
    p_watch.add_argument(
        "--rebase",
        action="store_true",
        help="Sync via 'git rebase --autostash' — syncs even dirty or diverged "
        "checkouts (opt-out of warn-and-skip)",
    )
```

and in `_dispatch`'s watch branch:

```python
        return run_watch(
            ctx,
            cfg,
            interval=args.interval,
            once=args.once,
            enable_documentation=args.enable_documentation,
            auto_build=args.auto_build,
            rebase=args.rebase,
        )
```

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/omc/watch.py src/omc/cli.py tests/unit/test_watch.py tests/unit/test_cli.py
git commit -m "feat: add --rebase flag to omc watch"
```

---

### Task 3: README documentation

**Model:** standard coding tier.

**Files:**
- Modify: `README.md` (watch prose paragraph ~line 68; command table row ~line 106)

**Interfaces:**
- Consumes: the shipped behavior from Tasks 1–2 (flag name `--rebase`, autostash semantics, conflict handling).
- Produces: user-facing docs only; nothing downstream.

- [ ] **Step 1: Update the command table row**

In the `omc watch` row (~line 106), extend the flag list `(--once, --interval, --enable-documentation, --auto-build)` to include `--rebase`:

```
| `omc watch` | Keep the main checkout's base branch + knowledge graph fresh (`--once`, `--interval`, `--enable-documentation`, `--auto-build`, `--rebase`); runs the project's `.omc/hooks/post-watch.sh` (and with `--auto-build` its build stage) after action ticks |
```

- [ ] **Step 2: Update the watch prose paragraph**

In the paragraph at ~line 68, after the sentence describing the ff-sync behavior ("…it ff-syncs the base branch as commits land, refreshing the index directly (no LLM cost) — add `--enable-documentation` to also regenerate the docs (LLM-heavy, so it's opt-in)."), insert:

```
By default watch is never destructive — a dirty or diverged checkout is warned about and left alone. `--rebase` opts out: watch then syncs via `git rebase --autostash origin/<base>`, stashing uncommitted edits around the rebase and replaying local commits on top. A conflicting rebase is aborted and the checkout restored; if restoring the stashed edits conflicts, watch warns loudly — the tree keeps the conflict markers and the edits stay safe in `git stash` — and skips further syncs until the conflict is resolved.
```

- [ ] **Step 3: Verify rendering and consistency**

Run: `grep -n "rebase" README.md`
Expected: both edits present; flag name spelled `--rebase` everywhere; no stale `--ignore-dirty` anywhere (`grep -n "ignore-dirty" README.md` → no hits).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document omc watch --rebase"
```
