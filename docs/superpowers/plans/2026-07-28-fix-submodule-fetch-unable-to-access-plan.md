# Submodule-Proof Base-Branch Fetches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** omc's `git fetch origin <base>` sites stop recursing into submodules (which fail with `fatal: unable to access` on repos like hummingbird-bridge), and the watch loop reports fetch failures by their cause, credential-redacted.

**Architecture:** Per-site `--recurse-submodules=no` on the three project-repo fetch invocations (watch tick, `rebase-main`, `sync_base`), a module-private `_fetch_error()` in `watch.py` that extracts the `fatal:`/`error:` cause from git stderr and redacts credentials, and `--ignore-submodules=all` on the watch's dirty gate so a synced gitlink bump can't wedge the loop. Spec: `docs/superpowers/specs/2026-07-28-fix-submodule-fetch-unable-to-access-design.md`.

**Tech Stack:** Python 3.12, pytest (real git repos in `tmp_path`), git ≥ 2.38 semantics (`protocol.file.allow`).

## Global Constraints

- Run `uv sync` once in this worktree before the first test run (a copied `.venv` runs PRIMARY-checkout code otherwise).
- Run tests with `uv run pytest <path> -v`.
- Ruff line length 100 (`uv run ruff check src tests` and `uv run ruff format src tests` must stay clean).
- Do NOT touch `src/omc/gitnexus.py`'s own fetch (line ~157) or `src/omc/dependency.py`'s clone — deliberately out of scope per spec.
- No new watch outcome tokens; `OMC_REBASE_MAIN` verdict JSON shape unchanged.
- Test fixtures that add local-path submodules MUST pass `-c protocol.file.allow=always` to `git submodule add` (git ≥ 2.38 blocks file-protocol submodule clones).
- Fixtures MUST rewire the submodule's origin URL to a nonexistent path to make recursive fetch fail (on-demand *fetches* of an already-cloned submodule succeed over file protocol even on modern git — verified on 2.54).
- Never use the cheap/fast model tier for any task.

---

### Task 1: `_fetch_error()` — cause extraction + redaction for fetch stderr

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/watch.py` (new module-private helper near `_say`, ~line 37; extend the existing `from .gitnexus import …` line)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `redact_userinfo(url: str) -> str` from `src/omc/gitnexus.py:40` (plain `re.sub`, safe on multi-line text).
- Produces: `_fetch_error(stderr: str) -> str` in `src/omc/watch.py` — Task 2 calls it in the fetch-failed message.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_watch.py`:

```python
def test_fetch_error_keeps_cause_drops_progress():
    from omc.watch import _fetch_error

    stderr = (
        "From kakarot.chorse.space:gcx/backend/hummingbird-bridge\n"
        " * branch                main       -> FETCH_HEAD\n"
        "Fetching submodule git/event-schemas\n"
        "fatal: unable to access 'https://kakarot.chorse.space/gcx/git/event-schemas.git/':"
        " The requested URL returned error: 403\n"
    )
    out = _fetch_error(stderr)
    assert out.startswith("fatal: unable to access")
    assert "Fetching submodule" not in out and "FETCH_HEAD" not in out


def test_fetch_error_redacts_embedded_credentials():
    from omc.watch import _fetch_error

    out = _fetch_error("fatal: unable to access 'https://oauth2:t0ken@host/x.git/': 403\n")
    assert "t0ken" not in out
    assert "[REDACTED]" in out


def test_fetch_error_caps_length():
    from omc.watch import _fetch_error

    assert len(_fetch_error("fatal: " + "x" * 1000)) <= 300


def test_fetch_error_falls_back_to_last_line():
    from omc.watch import _fetch_error

    assert _fetch_error("something odd\nlast line\n") == "last line"


def test_fetch_error_empty_stderr():
    from omc.watch import _fetch_error

    assert _fetch_error("") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -k fetch_error -v`
Expected: FAIL — `ImportError: cannot import name '_fetch_error'`

- [ ] **Step 3: Implement `_fetch_error`**

In `src/omc/watch.py`, extend the gitnexus import:

```python
from .gitnexus import ANALYZE_ARGS, ensure_gitnexus, gitnexus_argv, redact_userinfo
```

Add below `_say` (~line 40):

```python
def _fetch_error(stderr: str) -> str:
    """Compress git-fetch stderr to its cause. Git narrates progress first
    ('From …', 'Fetching submodule …') and puts the fatal:/error: lines LAST —
    a head-slice shows only noise and cuts the cause mid-URL."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    causes = [ln for ln in lines if ln.startswith(("fatal:", "error:"))]
    text = " ".join(causes) if causes else (lines[-1] if lines else "")
    return redact_userinfo(text)[:300]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch.py -k fetch_error -v`
Expected: 5 PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "Add _fetch_error: cause-first, credential-redacted fetch stderr"
```

---

### Task 2: Watch tick — no-recurse fetch, `_fetch_error` message, submodule-proof dirty gate

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/watch.py:303-305` (fetch) and `src/omc/watch.py:362-366` (dirty gate)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `_fetch_error(stderr: str) -> str` from Task 1; existing test helpers `_repo_with_origin(tmp_path)`, `_git(*args, cwd)`, `_ctx_with_node_stub(tmp_path, home)` in `tests/unit/test_watch.py`.
- Produces: test helpers `_repo_with_submodule(tmp_path) -> (origin, repo, sub_src)`, `_push_remote_gitlink_bump(origin, sub_src, tmp_path, marker: str)`, `_break_submodule_origin(repo, tmp_path)` — Tasks 3 and 4 replicate the same pattern in their own test files (test modules don't import from each other here; each file re-defines what it needs).

- [ ] **Step 1: Write the failing tests (fixture + two tests)**

Append to `tests/unit/test_watch.py`:

```python
def _repo_with_submodule(tmp_path):
    """Origin + watching clone with an initialized submodule. Callers break
    the submodule origin afterwards so any recursive fetch must fail."""
    sub_src = tmp_path / "subsrc"
    sub_src.mkdir()
    subprocess.run(["git", "init", "-q", str(sub_src)], check=True)
    _git("config", "user.email", "t@t", cwd=sub_src)
    _git("config", "user.name", "t", cwd=sub_src)
    (sub_src / "s.txt").write_text("one\n")
    _git("add", ".", cwd=sub_src)
    _git("commit", "-qm", "sub c1", cwd=sub_src)

    origin, repo = _repo_with_origin(tmp_path)
    # git >= 2.38 blocks file-protocol submodule CLONES unless allowed
    _git("-c", "protocol.file.allow=always", "submodule", "add", str(sub_src), "sub", cwd=repo)
    _git("commit", "-qm", "add submodule", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    return origin, repo, sub_src


def _push_remote_gitlink_bump(origin, sub_src, tmp_path, marker):
    """Advance origin/main with a commit that moves the submodule pointer —
    plumbing only, so the pushing clone never initializes the submodule."""
    (sub_src / "s.txt").write_text(f"{marker}\n")
    _git("add", ".", cwd=sub_src)
    _git("commit", "-qm", f"sub {marker}", cwd=sub_src)
    new_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sub_src, check=True, capture_output=True, text=True
    ).stdout.strip()
    other = tmp_path / f"other-{marker}"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    _git("update-index", "--add", "--cacheinfo", f"160000,{new_sha},sub", cwd=other)
    _git("commit", "-qm", f"bump submodule {marker}", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)


def _break_submodule_origin(repo, tmp_path):
    """Any fetch inside the submodule now fails: unreachable origin URL."""
    _git("remote", "set-url", "origin", str(tmp_path / "nonexistent"), cwd=repo / "sub")


def test_tick_syncs_gitlink_bump_without_fetching_submodules(tmp_path, capsys):
    from omc.watch import _tick

    origin, repo, sub_src = _repo_with_submodule(tmp_path)
    _push_remote_gitlink_bump(origin, sub_src, tmp_path, "two")
    _break_submodule_origin(repo, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    token = _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False)
    err = capsys.readouterr().err
    assert token == "synced", err
    assert "fetch failed" not in err


def test_tick_stale_submodule_pointer_does_not_wedge_dirty_gate(tmp_path, capsys):
    from omc.watch import _tick

    origin, repo, sub_src = _repo_with_submodule(tmp_path)
    _push_remote_gitlink_bump(origin, sub_src, tmp_path, "two")
    _break_submodule_origin(repo, tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert (
        _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False)
        == "synced"
    )
    # first sync moved the gitlink; the submodule working dir is now stale
    # (' M sub') — a second remote bump must STILL sync, not skip as dirty
    _push_remote_gitlink_bump(origin, sub_src, tmp_path, "three")
    token = _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False)
    err = capsys.readouterr().err
    assert token == "synced", err
    assert "dirty" not in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -k "gitlink or wedge" -v`
Expected: both FAIL — token is `"fetch-failed"` (the recursive submodule fetch hits the nonexistent URL). If the first test unexpectedly PASSES, stop: the fixture is not reproducing the bug — check `git -C <repo> config fetch.recurseSubmodules` and the rewired URL before proceeding.

- [ ] **Step 3: Implement — fetch flag, message, dirty gate**

In `src/omc/watch.py`, replace lines 303-305:

```python
    cp = ctx.run([ctx.git_bin, "fetch", "origin", base], cwd=root)
    if cp.returncode != 0:
        return quiet("fetch-failed", f"✗ fetch failed: {(cp.stderr or '').strip()[:200]}")
```

with:

```python
    # fetch.recurseSubmodules defaults to on-demand: a fetched gitlink bump
    # makes git fetch the submodules too, whose URLs may need credentials this
    # loop doesn't have (observed: SSH superproject, https submodules). The
    # loop only needs origin/<base> refs — never recurse.
    cp = ctx.run(
        [ctx.git_bin, "fetch", "--recurse-submodules=no", "origin", base], cwd=root
    )
    if cp.returncode != 0:
        return quiet("fetch-failed", f"✗ fetch failed: {_fetch_error(cp.stderr or '')}")
```

Replace the dirty gate (lines 362-366):

```python
    # -uno: only TRACKED modifications endanger an ff-merge (untracked files —
    # e.g. the wt.toml starter ensure_wt_config just seeded — must not block a
    # sync; a genuinely colliding untracked file makes the merge itself refuse).
    if _out(ctx, [ctx.git_bin, "status", "--porcelain", "-uno"], root):
```

with:

```python
    # -uno: only TRACKED modifications endanger an ff-merge (untracked files —
    # e.g. the wt.toml starter ensure_wt_config just seeded — must not block a
    # sync; a genuinely colliding untracked file makes the merge itself refuse).
    # --ignore-submodules=all: a synced gitlink bump leaves the submodule dir
    # stale (' M sub') forever; ff-merge succeeds regardless (git never touches
    # submodule content), so staleness must not wedge the loop in dirty-skip.
    if _out(
        ctx,
        [ctx.git_bin, "status", "--porcelain", "-uno", "--ignore-submodules=all"],
        root,
    ):
```

- [ ] **Step 4: Run the new tests, then the whole watch suite**

Run: `uv run pytest tests/unit/test_watch.py -v`
Expected: all PASS (pre-existing tests prove no regression — same fetch behavior on submodule-free repos).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "Watch: never recurse submodules on fetch; submodule-proof dirty gate"
```

---

### Task 3: `rebase-main` — no-recurse fetch + redacted error

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/internal.py:57-61` (`_rebase_main`'s fetch; extend the existing `from .gitnexus import …` line)
- Test: `tests/unit/test_internal.py`

**Interfaces:**
- Consumes: `redact_userinfo` from `src/omc/gitnexus.py:40`; existing test helpers in `tests/unit/test_internal.py` (`_setup_primary_with_origin`, `_add_worktree`, `_advance_main`, `_git`, `_run`).
- Produces: nothing consumed by later tasks. `OMC_REBASE_MAIN` verdict JSON shape MUST stay exactly `{"ok", "rebased", "synced", "note"|"conflicts"}` as today.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_internal.py`, reusing that file's helpers. The submodule lives in the PRIMARY clone (worktrees share `.git/modules`, so the fetched submodule repo — with its broken URL — is visible from the worktree; the failing-red run in Step 2 empirically confirms on-demand recursion triggers from the worktree):

```python
def test_rebase_main_ignores_submodules_on_fetch(tmp_path, capsys):
    origin, primary = _setup_primary_with_origin(tmp_path)
    # primary gains an initialized submodule (file-protocol clone needs allow)
    sub_src = tmp_path / "subsrc"
    sub_src.mkdir()
    subprocess.run(["git", "init", "-q", str(sub_src)], check=True)
    _git("config", "user.email", "t@t", cwd=sub_src)
    _git("config", "user.name", "t", cwd=sub_src)
    (sub_src / "s.txt").write_text("one\n")
    _git("add", ".", cwd=sub_src)
    _git("commit", "-qm", "sub c1", cwd=sub_src)
    _git(
        "-c", "protocol.file.allow=always", "submodule", "add", str(sub_src), "sub", cwd=primary
    )
    _git("commit", "-qm", "add submodule", cwd=primary)
    _git("push", "-q", "origin", "main", cwd=primary)

    wt = _add_worktree(primary, tmp_path)

    # remote main moves the gitlink (plumbing, no submodule init needed)
    (sub_src / "s.txt").write_text("two\n")
    _git("add", ".", cwd=sub_src)
    _git("commit", "-qm", "sub c2", cwd=sub_src)
    new_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sub_src, check=True, capture_output=True, text=True
    ).stdout.strip()
    other = tmp_path / "other-sub"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    _git("update-index", "--add", "--cacheinfo", f"160000,{new_sha},sub", cwd=other)
    _git("commit", "-qm", "bump submodule", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)

    # any submodule fetch now fails (shared .git/modules carries this URL)
    _git("remote", "set-url", "origin", str(tmp_path / "nonexistent"), cwd=primary / "sub")

    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 0, verdict
    assert verdict["ok"] is True
    assert verdict["rebased"]  # the gitlink-bump commit arrived under our work
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_internal.py::test_rebase_main_ignores_submodules_on_fetch -v`
Expected: FAIL — rc 1, stderr `error: git fetch origin main failed: … unable to access …`. If it PASSES before the implementation change, on-demand recursion does not trigger from a linked worktree on this git version — then replace the fixture assertion approach: drop this test file's fixture and instead assert the argv directly with a recording `ToolContext` stub (record `ctx.run` argv lists, assert `"--recurse-submodules=no" in argv` for the fetch call), and note the substitution in the commit message.

- [ ] **Step 3: Implement**

In `src/omc/internal.py`, extend the gitnexus import (line 17):

```python
from .gitnexus import ensure_gitnexus, gitnexus_argv, gitnexus_cli, redact_userinfo
```

Replace lines 57-62:

```python
    cp = ctx.run([ctx.git_bin, "fetch", "origin", base])
    if cp.returncode != 0:
        print(
            f"error: git fetch origin {base} failed: {(cp.stderr or '').strip()}", file=sys.stderr
        )
        return 1
```

with:

```python
    # Never recurse into submodules: rebase-main needs only origin/<base>
    # refs (see the watch tick's fetch for the full rationale).
    cp = ctx.run([ctx.git_bin, "fetch", "--recurse-submodules=no", "origin", base])
    if cp.returncode != 0:
        print(
            f"error: git fetch origin {base} failed:"
            f" {redact_userinfo((cp.stderr or '').strip())}",
            file=sys.stderr,
        )
        return 1
```

- [ ] **Step 4: Run the test and the whole internal suite**

Run: `uv run pytest tests/unit/test_internal.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "rebase-main: never recurse submodules on fetch; redact fetch errors"
```

---

### Task 4: `sync_base` — no-recurse fetch + redacted warning

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/worktree.py:28-42` (`sync_base`; new import)
- Test: `tests/unit/test_worktree.py`

**Interfaces:**
- Consumes: `redact_userinfo` from `src/omc/gitnexus.py:40`; existing helpers in `tests/unit/test_worktree.py` (`make_recording_stub`, `stub_env` from `tests/unit/_stubs.py`). NOTE: this test file's convention is a RECORDING GIT STUB, not real git — `test_sync_base_fetches` asserts the exact argv (`calls.read_text().strip() == "fetch origin main"`). Assert at the argv level here (the spec sanctions argv-level assertions; real-git submodule behavior is already covered end-to-end by Tasks 2 and 3).
- Produces: nothing consumed by later tasks. `sync_base(ctx, base) -> bool` signature unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_worktree.py`, change the expectation in the EXISTING `test_sync_base_fetches` (it pins the exact fetch argv and must move with the implementation):

```python
def test_sync_base_fetches(tmp_path):
    bindir = tmp_path / "bin"
    calls = make_recording_stub(bindir, "git")
    ctx = ToolContext.from_env(stub_env(bindir))
    assert sync_base(ctx, "main") is True
    assert calls.read_text().strip() == "fetch --recurse-submodules=no origin main"
```

Append a redaction test (the stub prints to stdout; `sync_base` reports `cp.stderr or cp.stdout`, so stdout works — keep the fake URL free of quote characters, the stub script single-quotes its echo):

```python
def test_sync_base_failure_redacts_credentials(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_recording_stub(
        bindir,
        "git",
        rc=1,
        stdout="fatal: unable to access https://oauth2:t0ken@host/x.git/ 403",
    )
    ctx = ToolContext.from_env(stub_env(bindir))
    assert sync_base(ctx, "main") is False
    err = capsys.readouterr().err
    assert "t0ken" not in err
    assert "[REDACTED]" in err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_worktree.py -k sync_base -v`
Expected: `test_sync_base_fetches` FAILS (argv is still `fetch origin main`), `test_sync_base_failure_redacts_credentials` FAILS (`t0ken` present, no `[REDACTED]`), `test_sync_base_failure_is_nonfatal` PASSES.

- [ ] **Step 3: Implement**

In `src/omc/worktree.py`, add the import:

```python
from .gitnexus import redact_userinfo
```

Replace in `sync_base` (lines ~33-41):

```python
    try:
        cp = ctx.run([ctx.git_bin, "fetch", "origin", base])
    except OSError as exc:
        print(f"warning: could not fetch origin/{base}: {exc}", file=sys.stderr)
        return False
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        print(f"warning: 'git fetch origin {base}' failed: {detail}", file=sys.stderr)
        return False
```

with:

```python
    try:
        # Never recurse into submodules: sync_base needs only origin/<base>
        # refs (see the watch tick's fetch for the full rationale).
        cp = ctx.run([ctx.git_bin, "fetch", "--recurse-submodules=no", "origin", base])
    except OSError as exc:
        print(f"warning: could not fetch origin/{base}: {exc}", file=sys.stderr)
        return False
    if cp.returncode != 0:
        detail = redact_userinfo((cp.stderr or cp.stdout or "").strip())
        print(f"warning: 'git fetch origin {base}' failed: {detail}", file=sys.stderr)
        return False
```

- [ ] **Step 4: Run the worktree suite, then the full unit suite**

Run: `uv run pytest tests/unit/test_worktree.py -v && uv run pytest tests/unit -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/omc/worktree.py tests/unit/test_worktree.py
git commit -m "sync_base: never recurse submodules on fetch; redact fetch warnings"
```
