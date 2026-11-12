# Squash before rebase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/omc:finish` squashes the branch to one commit over the merge-base *before* rebasing, through a deterministic `omc internal rebase-main --squash`, so a conflict with the base is resolved at most once; the LLM-run `squash` skill and `OMC_SQUASH` are retired, and `rebase-main` stops reporting a refused rebase as a fake conflict.

**Architecture:** `watch._rebase_in_progress` moves to `wtconfig.rebase_in_progress` (one copy). `internal._rebase_main` gains a pre-fetch guard (rc 2), splits a refused rebase (rc 1) from a real conflict (rc 3), and with `squash=True` calls a new private `_squash` that folds the tree and recommits over `git merge-base origin/<base> HEAD` before the rebase. Skill text, contract lists and the README follow.

**Tech Stack:** Python 3 (`src/omc/`), real git in pytest, Markdown skills.

**Spec:** `docs/superpowers/specs/2026-11-12-squash-before-rebase-design.md` (the design record; read it in full before any task).

## Global Constraints

- Red → green for every change (`.omc/config/AGENTS.md`): write the test, run it, watch it fail for the stated reason, then implement. Never `pytest.skip` / `skipif`.
- `ToolContext` is the only subprocess boundary: every new git call in `src/` is `ctx.run([ctx.git_bin, ...])` with an argv list; the branch name enters a `-m` argument, never a shell.
- Exit codes: 0 ok, 1 error, 2 refusal, 3 bail. rc 1 and rc 2 print `error: …` on stderr and **no** verdict.
- `OMC_REBASE_MAIN` is a single JSON line; key order on success is `ok, rebased, synced, shared, [squashed], knowledge`. rc 3 and the primary no-op verdict stay byte-for-byte as today.
- stderr narration uses `→` / `·` lines (`_say` in `internal.py`).
- `README.md` is generated: never hand-edit it; change `.claude/skills/regenerate-readme/SKILL.md` and run the `regenerate-readme` skill.
- **Worktree test command (shebang trap):** run pytest as `uv run python -m pytest …`, never bare `uv run pytest` or `just check` (their console-script shebang points at the primary checkout's code). The full unit suite in this worktree is:
  `uv run python -m pytest -m "not e2e and not local_iterm2" -q -n auto`
- **Host baseline:** about 19 unit tests fail on this host on `origin/main` too (`test_fish_hook.py`, `test_worktrunk_real.py`, `test_configure_pty.py`, `test_e2e_image_holders.py`, possibly `test_lifecycle_command.py`). Before Task 1, capture the baseline once:
  `uv run python -m pytest -m "not e2e and not local_iterm2" -q -n auto 2>&1 | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort > /tmp/squash-before-rebase-baseline.txt`
  A task's suite is "green" when its own `FAILED/ERROR` list (same pipeline) equals that file. Do not chase the baseline failures.
- Lint every task's Python before committing: `uvx ruff format --check src tests && uvx ruff check src tests` (line length 100).
- No E2E token exists on this host: do not run `just e2e-tests`. `tests/e2e/test_e2e_finish.py` stays unchanged and valid.
- Each task ends with its worker committing that task's tested changes (end commit messages with the attribution line from the session's system reminder).

---

### Task 1: Move the rebase state-dir helper into `wtconfig`

Complexity: simple

**Files:**
- Modify: `src/omc/wtconfig.py` (add `rebase_in_progress` after `current_branch`, ~line 219)
- Modify: `src/omc/watch.py:47` (import), `src/omc/watch.py:59-70` (delete `_rebase_in_progress`), `src/omc/watch.py:334` (call site)
- Test: `tests/unit/test_wtconfig.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `omc.wtconfig.rebase_in_progress(ctx: ToolContext, root: str) -> bool` — True only when git's `rebase-merge` or `rebase-apply` state dir exists for the checkout at `root`. Tasks 2 and 3 import it in `internal.py` and in `tests/unit/test_internal.py`.

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_wtconfig.py`, add `rebase_in_progress` to the existing `from omc.wtconfig import (...)` block (alphabetical, after `find_design_record`), then append:

```python
def _repo_with_conflicting_branches(tmp_path):
    """Branch `side` and `main` both rewrite f.txt; HEAD is on `side`."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    g("add", ".")
    g("commit", "-qm", "base")
    g("branch", "-M", "main")
    g("checkout", "-q", "-b", "side")
    (repo / "f.txt").write_text("side\n")
    g("commit", "-qam", "side")
    g("checkout", "-q", "main")
    (repo / "f.txt").write_text("main\n")
    g("commit", "-qam", "main")
    g("checkout", "-q", "side")
    return repo, g


def test_rebase_in_progress_tracks_git_state_dirs(tmp_path):
    import omc.watch as watch

    repo, g = _repo_with_conflicting_branches(tmp_path)
    ctx = ToolContext.from_env()
    assert rebase_in_progress(ctx, str(repo)) is False
    assert g("rebase", "main").returncode != 0  # conflict: the rebase pauses
    assert rebase_in_progress(ctx, str(repo)) is True
    assert g("rebase", "--abort").returncode == 0
    assert rebase_in_progress(ctx, str(repo)) is False
    # the am backend's state dir counts too
    apply_dir = repo / g("rev-parse", "--git-path", "rebase-apply").stdout.strip()
    apply_dir.mkdir()
    assert rebase_in_progress(ctx, str(repo)) is True
    # one copy: watch uses the shared helper, its private twin is gone
    assert watch.rebase_in_progress is rebase_in_progress
    assert not hasattr(watch, "_rebase_in_progress")
```

(`ToolContext.from_env()` with the process env, not the file's `_ctx(tmp_path)`: that helper drops `PATH`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/unit/test_wtconfig.py::test_rebase_in_progress_tracks_git_state_dirs -v`
Expected: FAIL — `ImportError: cannot import name 'rebase_in_progress' from 'omc.wtconfig'` (collection error).

- [ ] **Step 3: Move the helper**

In `src/omc/wtconfig.py`, after `current_branch` (`Path` is already imported there):

```python
def rebase_in_progress(ctx: ToolContext, root: str) -> bool:
    """True only when git has an actual rebase checked out. `git rebase
    --autostash` can REFUSE before starting (e.g. an untracked file the replay
    would overwrite): it exits non-zero with HEAD still on the branch and no
    rebase state, so a following `git rebase --abort` would just fail with
    'no rebase in progress'. Detect the real thing via the state dirs git
    itself uses (rebase-merge for the merge backend, rebase-apply for am)."""
    for name in ("rebase-merge", "rebase-apply"):
        cp = ctx.run([ctx.git_bin, "rev-parse", "--git-path", name], cwd=root)
        p = (cp.stdout or "").strip() if cp.returncode == 0 else ""
        if p and (Path(root) / p).exists():
            return True
    return False
```

In `src/omc/watch.py`: delete `_rebase_in_progress` (lines 59-70; keep `_out`, it has other callers), change line 47 to

```python
from .wtconfig import current_branch, ensure_wt_config, primary_root, rebase_in_progress, repo_root
```

and at the former line 334 call `rebase_in_progress(ctx, root)` instead of `_rebase_in_progress(ctx, root)`. Nothing else in `watch.py` changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_wtconfig.py tests/unit/test_watch.py tests/unit/test_watch_mutex.py -q`
Expected: PASS (the watch `--rebase` tick tests, including `test_tick_rebase_pre_start_refusal_quiets_and_leaves_untracked_file`, exercise the moved helper unchanged).

- [ ] **Step 5: Full suite, lint, commit**

Run the full suite (Global Constraints) and compare against the baseline; run ruff.

```bash
git add src/omc/wtconfig.py src/omc/watch.py tests/unit/test_wtconfig.py
git commit -m "refactor: share rebase_in_progress via wtconfig"
```

---

### Task 2: `rebase-main` guard (rc 2) and refused-rebase vs conflict (rc 1 vs rc 3)

Complexity: medium

**Files:**
- Modify: `src/omc/internal.py:31-37` (import), `src/omc/internal.py:148-208` (`_rebase_main`)
- Test: `tests/unit/test_internal.py`

**Interfaces:**
- Consumes: `omc.wtconfig.rebase_in_progress(ctx, root) -> bool` (Task 1).
- Produces (test helpers in `tests/unit/test_internal.py`, reused by Task 3):
  - `_git_out(*args, cwd) -> str` — stripped stdout of a checked git call.
  - `_run_err(args, cwd, tmp_path, capsys) -> tuple[int, dict | None, str, str]` — `(rc, verdict, out, err)`; `_run` keeps its 3-tuple and delegates to it.
  - `_conflicting_branch(primary, tmp_path, versions=("worktree version\n",)) -> Path` — a worktree (`feature/x`) with one commit per entry in `versions`, each rewriting `f.txt`, plus main's own `f.txt` change pushed to origin (the worktree shares `origin/main`, so no fetch is needed to see it).
  - `test_rebase_main_refuses_mid_rebase` parametrized over `extra` (Task 3 adds `["--squash"]`).

- [ ] **Step 1: Write the failing tests**

At the top of `tests/unit/test_internal.py` add:

```python
import pytest

from omc.toolctx import ToolContext
from omc.wtconfig import rebase_in_progress
```

(keep the existing `from omc.internal import run_internal`; ruff's isort orders the block). The local `from omc.toolctx import ToolContext` inside `test_rebase_main_never_runs_gitnexus_index` can stay or be dropped.

Replace the existing `_run` (around line 92) with:

```python
def _git_out(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _run_err(args, cwd, tmp_path, capsys):
    """`_run` plus stderr: the rc 1 / rc 2 paths print `error:` and no verdict."""
    old = os.getcwd()
    os.chdir(cwd)
    try:
        env_home = tmp_path / "omchome"
        os.environ["OMC_HOME"] = str(env_home)
        rc = run_internal(args)
    finally:
        os.chdir(old)
        os.environ.pop("OMC_HOME", None)
    captured = capsys.readouterr()
    verdict_line = next(
        (ln for ln in captured.out.splitlines() if ln.startswith("OMC_REBASE_MAIN ")), None
    )
    verdict = json.loads(verdict_line.split(" ", 1)[1]) if verdict_line else None
    return rc, verdict, captured.out, captured.err


def _run(args, cwd, tmp_path, capsys):
    rc, verdict, out, _ = _run_err(args, cwd, tmp_path, capsys)
    return rc, verdict, out
```

After `test_rebase_main_conflict_bails_rc3_and_leaves_rebase_paused` add:

```python
def _conflicting_branch(primary, tmp_path, versions=("worktree version\n",)):
    """A worktree whose commits each rewrite f.txt, and main's own f.txt change pushed."""
    wt = _add_worktree(primary, tmp_path)
    for i, text in enumerate(versions):
        (wt / "f.txt").write_text(text)
        _git("commit", "-qam", f"branch {i}", cwd=wt)
    (primary / "f.txt").write_text("main version\n")
    _git("commit", "-qam", "main change", cwd=primary)
    _git("push", "-q", "origin", "main", cwd=primary)
    return wt


def test_rebase_main_dirty_tree_without_squash_is_rc1_not_a_fake_conflict(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    # ToolContext.child_env inherits the host's global git config; an autostashing
    # host would otherwise rebase cleanly.
    _git("config", "rebase.autoStash", "false", cwd=wt)
    _advance_main(primary)
    (wt / "f.txt").write_text("unstaged edit\n")
    head = _git_out("rev-parse", "HEAD", cwd=wt)

    rc, verdict, out, err = _run_err(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 1
    assert verdict is None and "OMC_REBASE_MAIN" not in out
    assert "git rebase origin/main failed" in err and "cannot rebase" in err
    assert not rebase_in_progress(ToolContext.from_env(), str(wt))
    assert _git_out("rev-parse", "HEAD", cwd=wt) == head
    assert (wt / "f.txt").read_text() == "unstaged edit\n"


@pytest.mark.parametrize("extra", [[]], ids=["standalone"])
@pytest.mark.parametrize("state", ["rebase", "merge"])
def test_rebase_main_refuses_mid_rebase(tmp_path, capsys, state, extra):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _conflicting_branch(primary, tmp_path)
    paused = subprocess.run(["git", state, "origin/main"], cwd=wt, capture_output=True)
    assert paused.returncode != 0  # conflict: rebase paused, or merge left unmerged paths
    # Any fetch would now fail rc 1, so rc 2 proves the guard runs before the network.
    _git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=wt)
    ctx = ToolContext.from_env()
    head = _git_out("rev-parse", "HEAD", cwd=wt)
    unmerged = _git_out("ls-files", "-u", cwd=wt)
    assert unmerged
    was_rebasing = rebase_in_progress(ctx, str(wt))
    assert was_rebasing is (state == "rebase")

    rc, verdict, _, err = _run_err(
        ["rebase-main", "--base", "main", *extra], wt, tmp_path, capsys
    )

    assert rc == 2 and verdict is None
    assert "a rebase or merge is in progress" in err
    assert _git_out("rev-parse", "HEAD", cwd=wt) == head
    assert _git_out("ls-files", "-u", cwd=wt) == unmerged
    assert rebase_in_progress(ctx, str(wt)) is was_rebasing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_internal.py -q -k "rc1_not_a_fake_conflict or refuses_mid_rebase"`
Expected: FAIL — the dirty-tree test gets `rc == 3` (the `"conflicts": []` misreport); both `refuses_mid_rebase` cases get `rc == 1` (the fetch against `gone.git` failed: no guard runs first).

- [ ] **Step 3: Implement**

In `src/omc/internal.py`, add `rebase_in_progress` to the `from .wtconfig import (...)` block. In `_rebase_main`, between the primary no-op `return 0` and the `git fetch`:

```python
    # Refuse before the fetch: a paused rebase or unmerged paths are the user's to
    # finish, and a squash's `git add -A` would stage conflict markers as resolved.
    unmerged = (ctx.run([ctx.git_bin, "ls-files", "-u"]).stdout or "").strip()
    if rebase_in_progress(ctx, root) or unmerged:
        print(
            "error: a rebase or merge is in progress — finish it (git rebase --continue)"
            " or back out (git rebase --abort) first",
            file=sys.stderr,
        )
        return 2
```

Replace the rebase failure branch with:

```python
    cp = ctx.run([ctx.git_bin, "rebase", f"origin/{base}"])
    if cp.returncode != 0:
        if not rebase_in_progress(ctx, root):
            # git refused before starting (canonical: a dirty tree) — nothing is
            # paused and there are no conflicts to report.
            print(
                f"error: git rebase origin/{base} failed: {(cp.stderr or '').strip()}",
                file=sys.stderr,
            )
            return 1
        conflicts = (
            ctx.run([ctx.git_bin, "diff", "--name-only", "--diff-filter=U"]).stdout or ""
        ).split()
        # The rebase stays PAUSED for the user/skill to resolve — never aborted here.
        _verdict({"ok": False, "conflicts": conflicts, "knowledge": knowledge})
        return 3
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_internal.py -q -k rebase_main`
Expected: PASS, including the unchanged `test_rebase_main_conflict_bails_rc3_and_leaves_rebase_paused` (a real conflict still bails rc 3 with the same verdict) and `test_rebase_main_in_primary_is_noop`.

- [ ] **Step 5: Full suite, lint, commit**

```bash
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "fix: rebase-main refuses mid-rebase and stops reporting refusals as conflicts"
```

---

### Task 3: `rebase-main --squash` — squash over the merge-base, then rebase

Complexity: high

**Files:**
- Modify: `src/omc/internal.py:40` (`_USAGE`), `_rebase_main` (signature, squash step, verdict), new `_squash` above it, `run_internal`'s `rebase-main` branch (~line 422)
- Test: `tests/unit/test_internal.py`

**Interfaces:**
- Consumes: `rebase_in_progress` (Task 1); `_git_out`, `_run_err`, `_conflicting_branch`, the parametrized `test_rebase_main_refuses_mid_rebase` (Task 2); `current_branch(ctx, root) -> str | None` (existing, `wtconfig`).
- Produces:
  - CLI: `omc internal rebase-main [--base BRANCH] [--squash]`.
  - `_rebase_main(ctx: ToolContext, base_arg: str | None, squash: bool) -> int`.
  - `_squash(ctx: ToolContext, root: str, base: str, old: str) -> int | None` — commits folded, or None after printing `error:` (original commits restored on a failed commit).
  - Verdict key `"squashed": <int>`, present only with `--squash`, between `shared` and `knowledge`.
  - stderr lines Task 4's skill text names: `→ folding uncommitted changes into the squash`, `· nothing to squash`, `· pre-squash tip <old> (undo: git reset --hard <old>)`; temp commit subject `wip: squash of <branch>`.

- [ ] **Step 1: Write the failing tests**

Change the Task 2 parametrize line to cover both modes:

```python
@pytest.mark.parametrize("extra", [[], ["--squash"]], ids=["standalone", "squash"])
```

Append to `tests/unit/test_internal.py`:

```python
def _commit_a(wt, *versions):
    """One branch commit per version, each rewriting a.txt (no overlap with main)."""
    for text in versions:
        (wt / "a.txt").write_text(text)
        _git("add", "a.txt", cwd=wt)
        _git("commit", "-qm", f"a {text.strip()}", cwd=wt)


def _resolve_and_continue(wt):
    (wt / "f.txt").write_text("resolved\n")
    _git("add", "f.txt", cwd=wt)
    return subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=wt,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )


def test_rebase_main_squash_hits_a_conflict_once(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _conflicting_branch(primary, tmp_path, versions=("a\n", "b\n", "c\n"))

    rc, verdict, _, _ = _run_err(
        ["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys
    )

    assert rc == 3 and verdict["ok"] is False and verdict["conflicts"] == ["f.txt"]
    cont = _resolve_and_continue(wt)
    assert cont.returncode == 0, cont.stderr  # the one squashed commit was the only stop
    assert not rebase_in_progress(ToolContext.from_env(), str(wt))
    assert _git_out("rev-list", "--count", "origin/main..HEAD", cwd=wt) == "1"
    assert _git_out("log", "-1", "--format=%s", cwd=wt) == "wip: squash of feature/x"


def test_rebase_main_squash_rerun_after_resume_is_idempotent(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _conflicting_branch(primary, tmp_path, versions=("a\n", "b\n"))
    rc, _, _, _ = _run_err(["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys)
    assert rc == 3
    assert _resolve_and_continue(wt).returncode == 0
    tree = _git_out("rev-parse", "HEAD^{tree}", cwd=wt)

    rc, verdict, _, _ = _run_err(
        ["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys
    )

    assert rc == 0 and verdict["ok"] is True and verdict["squashed"] == 1
    assert _git_out("rev-list", "--count", "origin/main..HEAD", cwd=wt) == "1"
    assert _git_out("rev-parse", "HEAD^{tree}", cwd=wt) == tree
    assert _git_out("log", "-1", "--format=%s", cwd=wt) == "wip: squash of feature/x"


def test_rebase_main_squash_does_not_revert_main(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    _commit_a(wt, "a1\n", "a2\n")
    _advance_main(primary)  # main gains f2.txt after the branch was cut
    old = _git_out("rev-parse", "--short", "HEAD", cwd=wt)

    rc, verdict, _, err = _run_err(
        ["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys
    )

    assert rc == 0 and verdict["ok"] is True and verdict["squashed"] == 2
    assert list(verdict) == ["ok", "rebased", "synced", "shared", "squashed", "knowledge"]
    assert verdict["rebased"].startswith(f"{old}..")
    assert _git_out("rev-list", "--count", "origin/main..HEAD", cwd=wt) == "1"
    assert (wt / "f2.txt").exists()  # nothing of main is reverted
    assert _git_out("diff", "--name-only", "origin/main..HEAD", cwd=wt) == "a.txt"
    assert f"pre-squash tip {old} (undo: git reset --hard {old})" in err


def test_rebase_main_squash_folds_dirty_tree(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    _commit_a(wt, "a1\n")
    _advance_main(primary)
    (wt / "f.txt").write_text("unstaged edit\n")
    (wt / "new.txt").write_text("untracked\n")

    rc, verdict, _, err = _run_err(
        ["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys
    )

    assert rc == 0 and verdict["squashed"] == 1
    assert "→ folding uncommitted changes into the squash" in err
    assert _git_out("status", "--porcelain", cwd=wt) == ""
    assert _git_out("rev-list", "--count", "origin/main..HEAD", cwd=wt) == "1"
    changed = set(_git_out("diff", "--name-only", "origin/main..HEAD", cwd=wt).split())
    assert changed == {"a.txt", "f.txt", "new.txt"}


def test_rebase_main_squash_restores_commits_when_hook_rejects(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    _commit_a(wt, "a1\n", "a2\n")
    _advance_main(primary)  # before the hook: the shared config would reject main's commit too
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "commit-msg"
    hook.write_text('#!/bin/sh\necho "hook says no" >&2\nexit 1\n')
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    # Pinned in the repo's own config so a host-wide core.hooksPath cannot shadow it.
    _git("config", "core.hooksPath", str(hooks), cwd=wt)
    head = _git_out("rev-parse", "HEAD", cwd=wt)
    log = _git_out("log", "--format=%s", "-3", cwd=wt)

    rc, verdict, out, err = _run_err(
        ["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys
    )

    assert rc == 1 and verdict is None and "OMC_REBASE_MAIN" not in out
    assert "hook says no" in err
    assert _git_out("rev-parse", "HEAD", cwd=wt) == head
    assert _git_out("log", "--format=%s", "-3", cwd=wt) == log
    assert not rebase_in_progress(ToolContext.from_env(), str(wt))


def test_rebase_main_without_squash_keeps_history(tmp_path, capsys):
    # Guard (green before and after): a standalone refresh never rewrites commits.
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    _commit_a(wt, "a1\n", "a2\n")
    _advance_main(primary)

    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 0 and verdict["ok"] is True and "squashed" not in verdict
    assert _git_out("rev-list", "--count", "origin/main..HEAD", cwd=wt) == "2"


def test_rebase_main_in_primary_is_noop_with_squash(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    (primary / "f.txt").write_text("dirty\n")  # a squash would fold this
    head = _git_out("rev-parse", "HEAD", cwd=primary)

    rc, verdict, _ = _run(
        ["rebase-main", "--base", "main", "--squash"], primary, tmp_path, capsys
    )

    assert rc == 0 and verdict["ok"] is True and "primary" in verdict["note"]
    assert "squashed" not in verdict
    assert _git_out("rev-parse", "HEAD", cwd=primary) == head
    assert _git_out("status", "--porcelain", cwd=primary) == "M f.txt"


def test_rebase_main_squash_nothing_to_squash(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    _advance_main(primary)

    rc, verdict, _, err = _run_err(
        ["rebase-main", "--base", "main", "--squash"], wt, tmp_path, capsys
    )

    assert rc == 0 and verdict["squashed"] == 0
    assert "· nothing to squash" in err
    assert _git_out("rev-list", "--count", "origin/main..HEAD", cwd=wt) == "0"
    assert _git_out("rev-parse", "HEAD", cwd=wt) == _git_out("rev-parse", "origin/main", cwd=wt)
```

(`_git_out` strips, so `git status --porcelain`'s ` M f.txt` compares as `M f.txt`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_internal.py -q -k "squash or refuses_mid_rebase"`
Expected: every new `--squash` test FAILS because argparse rejects the unknown flag (rc 2, `_USAGE` on stderr, no verdict). `refuses_mid_rebase[*-squash]` also gets rc 2 from that usage error, but FAILS on its `"a rebase or merge is in progress"` assertion. `test_rebase_main_without_squash_keeps_history` PASSES (a guard, see its comment).

- [ ] **Step 3: Implement**

In `_USAGE`, replace its first two string pieces

```python
    "usage: omc internal {rebase-main [--base BRANCH] | wt-template | design-record | models"
    " | global-instructions PROVIDER"
```

with

```python
    "usage: omc internal {rebase-main [--base BRANCH] [--squash] | wt-template | design-record"
    " | models | global-instructions PROVIDER"
```

(the concatenated text changes only by ` [--squash]`).

Add above `_rebase_main`:

```python
def _squash(ctx: ToolContext, root: str, base: str, old: str) -> int | None:
    """Fold the branch, uncommitted changes included, into ONE `wip:` commit over
    `merge-base origin/<base> HEAD` — never over origin/<base> itself, which would
    revert every commit the base gained since the cut. Returns the number of
    branch commits folded, or None after printing why; a rejected commit (hooks
    run, no --no-verify) is rolled back to `old`, leaving the branch as found."""
    git = ctx.git_bin
    mb_cp = ctx.run([git, "merge-base", f"origin/{base}", "HEAD"])
    mb = (mb_cp.stdout or "").strip()
    if mb_cp.returncode != 0 or not mb:
        why = (mb_cp.stderr or "").strip()
        print(f"error: no merge-base between origin/{base} and HEAD: {why}", file=sys.stderr)
        return None
    n = int((ctx.run([git, "rev-list", "--count", f"{mb}..HEAD"]).stdout or "").strip() or 0)
    dirty = bool((ctx.run([git, "status", "--porcelain"]).stdout or "").strip())
    if dirty:
        # Untracked files fold too; ignored ones (.omc/docs, .gitnexus) never do.
        _say("→ folding uncommitted changes into the squash")
        add = ctx.run([git, "add", "-A"])
        if add.returncode != 0:
            print(f"error: git add -A failed: {(add.stderr or '').strip()}", file=sys.stderr)
            return None
    if n == 0 and not dirty:
        _say("· nothing to squash")
        return 0
    reset = ctx.run([git, "reset", "--soft", mb])
    if reset.returncode != 0:
        why = (reset.stderr or "").strip()
        print(f"error: git reset --soft {mb} failed: {why}", file=sys.stderr)
        return None
    branch = current_branch(ctx, root) or "HEAD"
    commit = ctx.run([git, "commit", "-m", f"wip: squash of {branch}"])
    if commit.returncode != 0:
        parts = ((commit.stderr or "").strip(), (commit.stdout or "").strip())
        why = "\n".join(p for p in parts if p)
        print(f"error: squash commit failed: {why}", file=sys.stderr)
        # The original commits are back; the index stays staged (at worst the
        # user's own dirty changes, now staged).
        ctx.run([git, "reset", "--soft", old])
        return None
    _say(f"· pre-squash tip {old} (undo: git reset --hard {old})")
    return n
```

Keep lines within 100 columns (split the f-strings if ruff complains).

In `_rebase_main`: signature `def _rebase_main(ctx: ToolContext, base_arg: str | None, squash: bool) -> int:`. The primary no-op and the Task 2 guard come first, unchanged (the primary is never squashed). After `old = …`:

```python
    squashed = None
    if squash:
        squashed = _squash(ctx, root, base, old)
        if squashed is None:
            return 1
```

The success verdict becomes:

```python
    payload = {
        "ok": True,
        "rebased": f"{old}..{new}",
        "synced": result.synced,
        "shared": result.shared,
    }
    if squashed is not None:
        payload["squashed"] = squashed  # only with --squash; finish reports it as N→1
    payload["knowledge"] = knowledge
    _verdict(payload)
    return 0
```

(keep the existing "Deliberately NO gitnexus invocation" comment above it). In `run_internal`'s `rebase-main` branch:

```python
        parser.add_argument("--base", default=None)
        parser.add_argument("--squash", action="store_true")
        ...
        return _rebase_main(ToolContext.from_env(), args.base, args.squash)
```

Nothing in the squash or rebase steps is wrapped in a try/finally that aborts or resets; the only automatic restore is the failed-commit rollback above. Known edge (accepted, spec §4.1 "commit failure → rc 1"): commits that net to an empty diff make `git commit` fail with "nothing to commit", which rolls back and exits rc 1.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_internal.py -q`
Expected: PASS (all `rebase_main` tests, old and new).

- [ ] **Step 5: Full suite, lint, commit**

```bash
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "feat: rebase-main --squash squashes over the merge-base before rebasing"
```

---

### Task 4: Skill text — finish squashes via rebase-main, `squash` skill and `OMC_SQUASH` retired

Complexity: medium

**Files:**
- Modify: `skills/finish/SKILL.md`, `skills/rebase-main/SKILL.md`, `skills/audit/SKILL.md:146`
- Delete: `skills/squash/` (the whole directory)
- Modify: `src/omc/distribution/AGENTS.md:38,82-84`, `.omc/config/AGENTS.md:68-70`, `.omc/skills/explain-context/SKILL.md:27-29`, `.omc/skills/review/SKILL.md:21-23`, `.claude/skills/regenerate-readme/SKILL.md:212-213,237-240`
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Consumes: the CLI surface from Task 3 (`--squash`, `"squashed"`, rc 1 / rc 2 / rc 3, the stderr lines).
- Produces: skill text only. Task 5's README regeneration reads the finish frontmatter and the regenerate-readme recipe edited here.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_plugin_manifests.py`:

1. `INTERNAL_SKILLS` (line ~169): remove `"squash",`.
2. Replace `test_squash_skill_contract` (line ~1039) with:

```python
def test_squash_skill_is_retired():
    # The squash is deterministic Python now (`omc internal rebase-main --squash`).
    assert not (ROOT / "skills" / "squash").exists()
```

3. In `test_finish_skill_contract`, replace the order check and the stage split:

```python
    # rebase-main squashes and rebases, then stages run check -> build -> verify -> review,
    # then push
    order = [text.index("`rebase-main`"), text.index("`check`"), text.index("`build`")]
    order += [text.index("`verify`"), text.index("`review`"), text.index("`create-mr`")]
    assert order == sorted(order), (
        "finish must order rebase-main -> check -> build -> verify -> review -> push"
    )
    stage = text.split("## Step 3", 1)[1].split("## Step 4", 1)[0]
```

(the rest of that test is unchanged).

4. Replace `test_finish_starts_with_rebase_main` with:

```python
def test_finish_starts_with_rebase_main():
    text = (ROOT / "skills" / "finish" / "SKILL.md").read_text()
    step2 = text.split("## Step 2", 1)[1].split("## Step 3", 1)[0]
    assert "`rebase-main`" in step2 and "--squash" in step2
    assert "`squash`" not in text and "OMC_SQUASH" not in text  # the squash skill is gone
    assert '"squashed"' in text  # the N→1 report reads the rebase-main verdict
    assert text.index("`rebase-main`") < text.index("`create-mr`")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert m is not None
    description = m.group(1)
    assert description.index("squash") < description.index("rebase")
```

5. Extend `test_rebase_main_skill_contract`'s needle tuple with
   `"--squash"`, `"squashed"`, `"rc 2"`, `"rc 1"`, `"git reset --hard <old>"`,
   `"A standalone `/omc:rebase-main` always keeps history"` (write that last one as a
   normal Python string containing the backticks; the skill text in Step 3 puts the
   sentence on one line so the needle matches).
6. In `test_machine_contract_listings_include_knowledge_and_models`, add
   `".claude/skills/regenerate-readme/SKILL.md"` to the tuple and inside the loop
   `assert "OMC_SQUASH" not in (ROOT / rel).read_text(), rel`.
7. Add:

```python
def test_publish_route_squashes_before_rebasing():
    for rel in ("src/omc/distribution/AGENTS.md", "skills/audit/SKILL.md"):
        text = " ".join((ROOT / rel).read_text().split())
        assert "rebase, squash" not in text, rel
        assert "squash, rebase" in text, rel
```

8. The anti-stall comment (line ~1778): `finish -> squash/` becomes `finish -> rebase-main/`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py -q`
Expected: FAIL in `test_squash_skill_is_retired`, `test_finish_skill_contract` (`` `squash` `` → order / "## Step 3" split now hits the squash step), `test_finish_starts_with_rebase_main`, `test_rebase_main_skill_contract`, `test_machine_contract_listings_include_knowledge_and_models`, `test_publish_route_squashes_before_rebasing`.

- [ ] **Step 3: Edit the skills and contract lists**

`git rm -r skills/squash`.

`skills/finish/SKILL.md`:
- Frontmatter `description:` becomes: `Finish the current feature branch - squash to one commit, rebase onto the base, run the project's check/build/verify/review stages, describe the commit as the MR and push, then offer to close the worktree, address review comments, or discuss. Use when work on a ticket is done and ready for review.`
- Step 0 list becomes: `gate → anything-to-finish → rebase-main --squash → the four project stages → create-mr (describe + push) → ticket-sync (review phase) → offer follow-ups.`; "nine sub-skills" becomes "eight sub-skills". Keep "`wip: squash of…`" as written (it is not the `` `squash` `` skill reference).
- Replace Step 2 and delete Step 3 with:

```markdown
## Step 2 — squash and rebase onto the fresh base + refresh the snapshot

Invoke the **`rebase-main`** skill **with `--squash`** (`omc internal
rebase-main --squash`) — it folds uncommitted changes (with notice),
squashes the branch to one `wip:` commit over
`merge-base origin/<base> HEAD`, rebases that single commit onto
`origin/<base>`, and re-mirrors `.omc/docs` from the primary checkout.
Only one commit is replayed, so a conflict with the base stops the rebase
at most once. The `.gitnexus/` graph stays primary for `/omc:explain`
queries.

- **rc 3 bail** (conflicts, rebase left paused): hand control to the user
  per that skill — never resolve conflicts silently, never `--abort` on
  their behalf. Once the rebase has completed, re-run the `rebase-main`
  skill with `--squash` (idempotent: the branch is already one commit over
  the base) before continuing.
- **rc 1 or rc 2** (git refused, a hook rejected the squash commit, or a
  rebase or merge is already in progress): surface the stderr message and
  stop.
- Continue to Step 3 only on an `"ok": true` verdict carrying
  `"squashed"`; keep that number for the Step 5 report.
```

- Renumber: `## Step 4 — project stages` → `## Step 3 — project stages: check → build → verify → review`; `## Step 5 — describe and push` → `## Step 4 — describe and push`; `## Step 6 — offer follow-ups` → `## Step 5 — offer follow-ups`. Inside the describe-and-push step, every "Step 6" becomes "Step 5" (three places: "continue to Step 6", "The Step 6 "address review comments" loop", "begins Step 6"). The completion contract's "once Step 6 has run" becomes "once Step 5 has run".
- The follow-ups report line becomes: `Report what happened (squashed N→1 from the verdict's `"squashed"`, rebased onto `<base>`, stage outcomes, pushed, title),`
- Everything else (the stage rules, "leave the branch squashed", `OMC_TICKET` / "not the end of your turn", the three options, the completion contract) stays as written.

`skills/rebase-main/SKILL.md` — write it as:

````markdown
---
name: rebase-main
description: Refresh this worktree onto the latest base branch AND re-mirror .omc/docs from the primary checkout. The .gitnexus graph stays primary. Run anytime; it keeps your commits. /omc:finish runs it first, with --squash.
---

# omc rebase-main

A worktree is a snapshot of main at cut time — code and generated knowledge
docs (`.omc/docs/`). The `.gitnexus/` graph stays in the primary checkout,
where `/omc:explain` queries it. This refreshes code and docs via the
deterministic Python subcommand (an LLM is never trusted with
delete-semantics mirroring):

```sh
omc internal rebase-main
```

(`--base <branch>` overrides the configured base.)

`--squash` is `/omc:finish`'s flag. Before rebasing it folds uncommitted
changes (narrated on stderr) and squashes the branch to one
`wip: squash of <branch>` commit over `git merge-base origin/<base> HEAD`,
so the rebase replays one commit and stops at most once. Project hooks run
on that commit.

A standalone `/omc:rebase-main` always keeps history: never pass `--squash`
unless `/omc:finish` invoked this skill with it.

## Interpreting the outcome

The last line is machine-readable:

- `OMC_REBASE_MAIN {"ok": true, "rebased": "<old>..<new>", "synced": [...],
  "shared": [...]}` — report what moved and whether `.omc/docs` was
  re-mirrored, then continue. `synced` and `shared` never list `.gitnexus`.
  An entry under `shared` was already present via a shared `.omc` (symlinked
  across checkouts) — nothing was copied,
  and that is success, not a warning. With `--squash` the verdict also
  carries `"squashed": <n>` — the branch commits folded (0 when there was
  nothing to squash, or only uncommitted changes were folded) — and `<old>`
  is the pre-squash tip.
- `OMC_REBASE_MAIN {"ok": true, ..., "note": "primary checkout — nothing to
  rebase"}` — you're in the main checkout; `omc watch` owns freshness here.
  The primary is never squashed.
- **rc 3 (bail)** with `{"ok": false, "conflicts": [...]}` — the rebase hit
  conflicts and is LEFT PAUSED. Show the conflicted files and help the user
  resolve them (`git status`, resolve, `git add`, `git rebase --continue` —
  or `git rebase --abort` if they choose to back out; after `--squash`,
  abort lands on the single `wip:` commit, and the stderr line
  `pre-squash tip <old> (undo: git reset --hard <old>)` is the way back to
  the original commits). Never resolve conflicts silently, never abort on
  their behalf. After the rebase completes, re-run it with the same
  arguments so the snapshot mirror still happens (with `--squash` the
  re-run is idempotent).
- **rc 2** (no verdict) — a rebase or merge is already in progress, or
  unmerged paths remain; nothing was fetched or changed. Relay the stderr
  line and stop: the user finishes it (`git rebase --continue`) or backs out
  (`git rebase --abort`) first.
- **rc 1** (no verdict) — the fetch failed, git refused the rebase before
  starting (e.g. uncommitted changes without `--squash`), or a hook
  rejected the squash commit (the original commits were restored). Relay
  git's message from stderr; nothing was left paused.

Every `OMC_REBASE_MAIN` payload carries `"knowledge": {…}` (the primary's
freshness verdict, same schema as `OMC_KNOWLEDGE`). When `knowledge.fresh` is
false, relay its `reasons` and `fix` — the mirrored docs may be stale, and
the primary graph may also be stale.

## When to use

- Anytime the base branch has moved and you want current code and generated
  docs in this worktree. `/omc:explain` queries the primary graph; this
  command does not copy or refresh it.
- `/omc:finish` invokes this as its FIRST working step, with `--squash`.
````

`skills/audit/SKILL.md` Phase 3: "follow its full flow: rebase, squash," → "follow its full flow: squash, rebase,". Leave Phase 2's "`finish`'s squash uses `git add -A`" as is (still true).

`src/omc/distribution/AGENTS.md`:
- "Publish through `/omc:finish`** — rebase, squash, project stage gates" → "— squash, rebase, project stage gates".
- Machine contracts bullet:
  ```
  - **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
    `OMC_REBASE_MAIN` / `OMC_TICKET` / `OMC_KNOWLEDGE` /
    `OMC_DESIGN_RECORD` / `OMC_MODELS` verdicts are
  ```

`.omc/config/AGENTS.md`: "`OMC_SLUG`, `OMC_STAGE`,\n  `OMC_SQUASH`, `OMC_REBASE_MAIN`, …" → drop `` `OMC_SQUASH`, ``.
`.omc/skills/explain-context/SKILL.md`: drop `` `OMC_SQUASH` / `` from the prefix list.
`.omc/skills/review/SKILL.md`: drop `OMC_SQUASH / ` from the list.

`.claude/skills/regenerate-readme/SKILL.md`:
- Lifecycle recipe (line ~212): "Finish rebases/mirrors, squashes with\nMR description, gates before force-with-lease; user opens MR." → "Finish squashes, rebases/mirrors, describes with\nMR description, gates before force-with-lease; user opens MR."
- Architecture sequence (line ~237): the list becomes `OMC_KNOWLEDGE, OMC_DESIGN_RECORD, OMC_STAGE, OMC_REBASE_MAIN, OMC_SLUG, OMC_MODELS (...); include OMC_TICKET from behavior layer.` and after "Distinguish CLI verdicts from skill-emitted contracts." add: "The finish squash count travels in the CLI's rebase-main verdict (`squashed`); no skill emits a squash verdict."

Then grep for leftovers outside historical records:

```bash
grep -rn "OMC_SQUASH\|skills/squash\|\`squash\`" --exclude-dir=.git --exclude-dir=docs --exclude-dir=artifacts --exclude-dir=.superpowers . | grep -v "^./README.md"
```

Expected: no hits (README is Task 5; `docs/superpowers/specs` historical records stay as written).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_agentsmd.py tests/unit/test_e2e_lifecycle_stages.py -q`
Expected: PASS (including the anti-stall `test_conductors_externalize_their_steps_first` and `test_verdict_is_never_the_end_of_the_turn` for finish).

- [ ] **Step 5: Full suite, commit**

```bash
git add -A skills src/omc/distribution/AGENTS.md .omc/config/AGENTS.md .omc/skills/explain-context/SKILL.md .omc/skills/review/SKILL.md .claude/skills/regenerate-readme/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: finish squashes before rebasing via rebase-main --squash; retire the squash skill"
```

---

### Task 5: Regenerate the README

Complexity: medium

**Files:**
- Modify: `README.md` (generated — only via the skill)
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: the finish frontmatter and regenerate-readme recipe from Task 4; `_USAGE` / parser from Task 3.
- Produces: nothing later tasks use.

- [ ] **Step 1: Write the failing guard**

Append to `tests/unit/test_cli.py` (next to `test_readme_documents_optional_write_plan`):

```python
def test_readme_documents_squash_before_rebase():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert "OMC_SQUASH" not in readme
    finish = next(p for p in readme.split("\n\n") if "squash" in p and "--force-with-lease" in p)
    assert finish.index("squash") < finish.index("rebase"), finish
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/unit/test_cli.py::test_readme_documents_squash_before_rebase -v`
Expected: FAIL — `OMC_SQUASH` is still in the contract-name paragraph.

- [ ] **Step 3: Regenerate**

Invoke the `regenerate-readme` skill and follow it in full. In its step 5, run the guards as `uv run --frozen python -m pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'` (worktree shebang trap). Expected diff: the finish paragraph now squashes before rebasing, the `/omc:rebase-main` row may mention `--squash`, and the contract paragraph lists seven names with no squash verdict among the skill-emitted ones ("all eight" → "all seven"). Inspect `git diff README.md`; any change beyond these must trace to a current source fact, not to rewording drift — if the regeneration drifts elsewhere, regenerate the affected section again rather than hand-editing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_cli.py tests/unit/test_plugin_manifests.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, commit**

```bash
git add README.md tests/unit/test_cli.py
git commit -m "docs: regenerate README for squash before rebase"
```

---

## Self-review against the spec

- §4.1 steps 1–7 → Tasks 2 (guard, rc 1 split) and 3 (squash, verdict, rc 1 on commit failure, narration). §4.2 → Task 1. §4.3 → Task 3 (`squashed` only with `--squash`, key order, `rebased` from the pre-squash tip). §4.4 → Task 3 `…rerun_after_resume_is_idempotent`. §4.5–4.8 → Task 4; README → Task 5. §4.9 unchanged items are guarded by the existing watch, primary and rc 3 tests.
- §5 unit tests 1–10 → Task 2 (4, 5) and Task 3 (1, 2, 3, 5-squash, 6, 7, 8, 9, 10). Manifest bullets → Task 4. Watch tests: none monkeypatch the helper; Task 1 runs them.
- Plan additions beyond §5, each a red-first guard for a spec'd change: `test_squash_skill_is_retired`, `test_publish_route_squashes_before_rebasing`, the recipe in the contract-listing test, a merge-state case for the rc 2 guard, and the README guard in Task 5.
