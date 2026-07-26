# Install GitNexus from the Python CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GitNexus installation a Python-owned critical prerequisite of `omc update`, `omc watch`, and `omc start`, and self-heal the claude plugin marketplace registration in `omc update`.

**Architecture:** Factor the existing two-step npm build out of `update_gitnexus` into reusable helpers, add an idempotent `ensure_gitnexus` (silent no-op when healthy), expose it as `omc internal gitnexus ensure`, and wire the uniform prerequisite gate (`require_tools` + `ensure_gitnexus`) into the three commands. The `gitnexus-ensure` skill becomes a thin wrapper over the internal verb. Separately, `omc update` prepends a best-effort `claude plugin marketplace add <source>` so a missing marketplace registration no longer errors.

**Tech Stack:** Python 3, `argparse`, `subprocess` via `ToolContext`, pytest (network-free tests using recording shell stubs + local bare git repos, mirroring `tests/unit/test_gitnexus_update.py`).

---

## Reference: settled decisions (from the spec)

- GitNexus install failure is **fatal** for `omc start` / `omc watch`.
- `ensure_gitnexus` is **silent** when GitNexus is already healthy.
- `omc update` runs the full `require_tools` probe, **before** the GitNexus install (require_tools-first ordering, confirmed with the user).
- Marketplace fix ships on this branch.
- Approved source is unchanged: `https://github.com/chris-husse/GitNexus.git`.

## File map

- Modify `src/omc/gitnexus.py` — add `_clone_if_missing`, `_build`, `ensure_gitnexus`; convert `update_gitnexus` to install-or-update. (Task 1)
- Modify `src/omc/internal.py` — new `ensure` verb + `_USAGE`; update stale hint. (Task 2, Task 7)
- Modify `src/omc/watch.py` — prerequisite gate replaces the hard error. (Task 3)
- Modify `src/omc/start.py` — `ensure_gitnexus` after `require_tools`, skipped on `--dry-run`. (Task 4)
- Modify `src/omc/installer.py` — config load up front, `require_tools`, pass `marketplace_source`, best-effort marketplace steps. (Task 5, Task 6)
- Modify `src/omc/providers/{base,claude,codex,opencode}.py` — `plugin_update_argvs(self, marketplace_source=None)`. (Task 6)
- Modify `src/omc/dependency.py` — stale hint. (Task 7)
- Rewrite `skills/gitnexus-ensure/SKILL.md` — thin wrapper. (Task 7)
- Tests: `tests/unit/test_gitnexus_update.py`, `test_internal.py`, `test_watch.py`, `test_start.py`, `test_installer.py`, `test_providers.py`.

---

## Task 1: `ensure_gitnexus` + shared helpers in `gitnexus.py`

**Model:** heavy coding tier (refactor of a load-bearing module with an approved-source guarantee and multiple call sites).

**Files:**
- Modify: `src/omc/gitnexus.py`
- Test: `tests/unit/test_gitnexus_update.py`

Context: today `update_gitnexus` (`src/omc/gitnexus.py:72`) prints `GitNexus not installed — /omc:index installs it on first use; skipping.` and returns 0 when `<root>/.git` is absent. The two-step build already lives at `src/omc/gitnexus.py:122-139`. We extract two helpers and add `ensure_gitnexus`, and make `update_gitnexus` clone-then-update instead of skip.

- [ ] **Step 1: Write failing tests for `ensure_gitnexus`**

Add to `tests/unit/test_gitnexus_update.py` (the `_make_ctx` / `_seed_clone` / `_advance_origin` helpers already exist in this file; reuse them). Import `ensure_gitnexus` alongside `update_gitnexus`:

```python
from omc.gitnexus import ensure_gitnexus, update_gitnexus
```

```python
def test_ensure_noops_silently_when_healthy(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, dest = _seed_clone(tmp_path, home)
    # _seed_clone wrote a fake built CLI; the node stub reports 9.9.9, so the
    # CLI is "healthy" — ensure must not clone or build, and must stay silent.
    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 0
    recorded = calls.read_text() if calls.exists() else ""
    assert "npm" not in recorded  # no build on the healthy path
    assert capsys.readouterr().err == ""  # silent when healthy


def test_ensure_installs_when_missing(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin = tmp_path / "gitnexus-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    (seed / "gitnexus-shared").mkdir()
    (seed / "gitnexus-shared" / "package.json").write_text("{}")
    (seed / "gitnexus").mkdir()
    (seed / "gitnexus" / "package.json").write_text("{}")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c1", cwd=seed)
    _git("branch", "-M", "main", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    # NO managed clone at home/dependencies/gitnexus yet — ensure must create it.
    dest = home / "dependencies" / "gitnexus"
    assert not dest.exists()
    # The node stub always reports 9.9.9, so post-build verify passes once the
    # build "creates" dist/cli/index.js. The npm stub is a no-op, so seed the
    # CLI the way _seed_clone does, to stand in for `npm run build` output.
    # ensure clones first; then we let the build stub run. To make the post-
    # build --version succeed, the CLI file must exist after clone: create it
    # here by having the origin seed carry it.
    (seed / "gitnexus" / "dist" / "cli").mkdir(parents=True)
    (seed / "gitnexus" / "dist" / "cli" / "index.js").write_text("// built")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "add cli", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)

    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 0
    assert (dest / ".git").exists()  # cloned
    recorded = calls.read_text()
    npm = [ln for ln in recorded.splitlines() if ln.startswith("npm")]
    assert "install" in npm[0] and "gitnexus-shared" in npm[0]
    assert npm[1].startswith("npm ci")
    assert "run build" in npm[2]
    assert "installed" in capsys.readouterr().err.lower()


def test_ensure_refuses_wrong_origin(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _seed_clone(tmp_path, home)
    # A present clone whose origin differs from approved, but make the CLI look
    # BROKEN so ensure reaches the clone/verify path: point node at a bad rc.
    dest = home / "dependencies" / "gitnexus"
    (dest / "gitnexus" / "dist" / "cli" / "index.js").unlink()  # CLI missing -> unhealthy
    assert ensure_gitnexus(ctx, approved_origin="https://example.com/other.git") == 1
    assert "refusing" in capsys.readouterr().err
    assert "npm" not in (calls.read_text() if calls.exists() else "")  # never built


def test_ensure_build_failure_is_nonzero(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home, npm_rc=1)
    origin, _, dest = _seed_clone(tmp_path, home)
    (dest / "gitnexus" / "dist" / "cli" / "index.js").unlink()  # unhealthy -> must build
    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 1
    assert "failed" in capsys.readouterr().err
```

Also add a test that `update_gitnexus` now installs a missing clone instead of skipping, and **replace** the existing `test_skips_when_not_installed` (which asserts the old skip behavior):

```python
def test_update_installs_when_missing(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin = tmp_path / "gitnexus-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    for pkg in ("gitnexus-shared", "gitnexus"):
        (seed / pkg).mkdir()
        (seed / pkg / "package.json").write_text("{}")
    (seed / "gitnexus" / "dist" / "cli").mkdir(parents=True)
    (seed / "gitnexus" / "dist" / "cli" / "index.js").write_text("// built")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c1", cwd=seed)
    _git("branch", "-M", "main", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    dest = home / "dependencies" / "gitnexus"
    assert not dest.exists()

    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    assert (dest / ".git").exists()  # installed, not skipped
    assert "npm" in calls.read_text()  # built
```

Delete `test_skips_when_not_installed` (its asserted behavior is being removed).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_gitnexus_update.py -v`
Expected: the new `test_ensure_*` and `test_update_installs_when_missing` FAIL with `ImportError: cannot import name 'ensure_gitnexus'` (and, once the import exists, assertion failures).

- [ ] **Step 3: Refactor `gitnexus.py` — extract helpers, add `ensure_gitnexus`, convert `update_gitnexus`**

In `src/omc/gitnexus.py`, add two private helpers (place them above `update_gitnexus`). `_clone_if_missing` factors out the clone/origin-verify; `_build` factors out the existing two-step build loop verbatim:

```python
def _clone_if_missing(ctx: ToolContext, root: Path, approved_origin: str) -> int:
    """Ensure <root> is a clone of the approved origin. Clone when absent;
    refuse (never re-point) when an existing clone has a different origin."""
    git = ctx.git_bin
    if not (root / ".git").exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        cp = ctx.run([git, "clone", approved_origin, str(root)])
        if cp.returncode != 0:
            print(
                f"error: GitNexus clone failed: {(cp.stderr or '').strip()[:400]}",
                file=sys.stderr,
            )
            return 1
        return 0
    cp = ctx.run([git, "-C", str(root), "remote", "get-url", "origin"])
    origin = (cp.stdout or "").strip()
    if cp.returncode != 0 or origin != approved_origin:
        shown = redact_userinfo(origin) or "<unknown>"
        print(
            f"error: {root} origin is {shown!r}, not the approved GitNexus source — "
            "refusing to update",
            file=sys.stderr,
        )
        return 1
    return 0


def _build(ctx: ToolContext, root: Path) -> int:
    """Two-step npm build; order matters (gitnexus-shared is a plain sibling
    package compiled by the main build with its own node_modules)."""
    for argv, cwd in (
        (["npm", "install", "--no-audit", "--no-fund"], root / "gitnexus-shared"),
        (["npm", "ci"], root / "gitnexus"),
        (["npm", "run", "build"], root / "gitnexus"),
    ):
        cp = _run_tool(ctx, argv, cwd=str(cwd))
        if cp is None:
            print(f"error: {argv[0]} not found on PATH", file=sys.stderr)
            return 1
        if cp.returncode != 0:
            print(
                f"error: {' '.join(argv)} in {cwd.name}/ failed:\n"
                f"{(cp.stderr or cp.stdout or '').strip()[:800]}",
                file=sys.stderr,
            )
            return 1
    return 0
```

Add `ensure_gitnexus` (heal/install, silent when healthy, no forced update):

```python
def ensure_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int:
    """Install/heal the managed GitNexus clone. Silent no-op when the CLI is
    already healthy (the common path start/watch hit every run). Does NOT
    fetch/ff main — updating a healthy install is `omc update`'s job."""
    if _cli_version(ctx) is not None:
        return 0  # healthy: silent no-op
    root = gitnexus_root(ctx)
    rc = _clone_if_missing(ctx, root, approved_origin)
    if rc:
        return rc
    rc = _build(ctx, root)
    if rc:
        return rc
    ver = _cli_version(ctx)
    if ver is None:
        print(
            "error: GitNexus built but the CLI won't report --version — not claiming success",
            file=sys.stderr,
        )
        return 1
    print(f"✓ GitNexus installed ({ver})", file=sys.stderr)
    return 0
```

Rewrite `update_gitnexus` so a missing clone installs (via `_clone_if_missing`) instead of skipping, and the build reuses `_build`. Replace the body from the current early-return block (`src/omc/gitnexus.py:78-85`) and the inline build loop (`122-139`):

```python
def update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int:
    """Deterministic install-or-update of the managed GitNexus clone
    (`omc update`). Installs when missing, else forces main and rebuilds."""
    root = gitnexus_root(ctx)
    rc = _clone_if_missing(ctx, root, approved_origin)
    if rc:
        return rc
    git = ctx.git_bin
    old = _cli_version(ctx)
    cp = ctx.run([git, "-C", str(root), "fetch", "origin", "--prune"])
    if cp.returncode != 0:
        print(f"error: GitNexus fetch failed: {(cp.stderr or '').strip()[:400]}", file=sys.stderr)
        return 1
    head = ctx.run([git, "-C", str(root), "rev-parse", "HEAD"])
    remote = ctx.run([git, "-C", str(root), "rev-parse", "origin/main"])
    if (
        head.returncode == 0
        and remote.returncode == 0
        and head.stdout.strip() == remote.stdout.strip()
        and old is not None
    ):
        print(f"✓ GitNexus up to date{f' ({old})' if old else ''}", file=sys.stderr)
        return 0
    print("→ updating GitNexus…", file=sys.stderr)
    for argv in (
        [git, "-C", str(root), "checkout", "main"],
        [git, "-C", str(root), "merge", "--ff-only", "origin/main"],
    ):
        cp = ctx.run(argv)
        if cp.returncode != 0:
            print(
                f"error: GitNexus {' '.join(argv[3:])} failed: {(cp.stderr or '').strip()[:400]}",
                file=sys.stderr,
            )
            return 1
    rc = _build(ctx, root)
    if rc:
        return rc
    new = _cli_version(ctx)
    if new is None:
        print(
            "error: GitNexus built but the CLI won't report --version — not claiming success",
            file=sys.stderr,
        )
        return 1
    print(
        f"✓ GitNexus updated{f': {old} → {new}' if old and old != new else f' ({new})'}",
        file=sys.stderr,
    )
    return 0
```

Note the added `and old is not None` on the up-to-date short-circuit: a freshly-cloned-but-not-yet-built tree is at `origin/main` yet has no CLI, so it must fall through to the build rather than falsely report "up to date".

- [ ] **Step 4: Run the gitnexus tests to verify they pass**

Run: `uv run pytest tests/unit/test_gitnexus_update.py -v`
Expected: PASS (all existing tests that were kept, plus the new ones). `test_up_to_date_short_circuits`, `test_moved_pulls_builds_and_verifies`, `test_refuses_wrong_origin`, `test_credential_redaction`, `test_missing_node_is_clean_failure` still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/omc/gitnexus.py tests/unit/test_gitnexus_update.py
git commit -m "feat(gitnexus): ensure_gitnexus installs/heals; update installs when missing"
```

---

## Task 2: `omc internal gitnexus ensure` verb

**Model:** standard coding tier.

**Files:**
- Modify: `src/omc/internal.py` (`_gitnexus`, `_USAGE`)
- Test: `tests/unit/test_internal.py`

Context: `_gitnexus` (`src/omc/internal.py:84`) parses `--git`, checks the verb against `_GITNEXUS_VERBS`, then guards on the CLI being present (`src/omc/internal.py:110`). `ensure` installs, so it must be handled *before* that guard and needs no repo scoping.

- [ ] **Step 1: Write the failing test**

Look at `tests/unit/test_internal.py` for the existing `_gitnexus` invocation pattern (how it builds a ctx and calls `run_internal(["gitnexus", ...])`). Add:

```python
def test_gitnexus_ensure_calls_ensure_gitnexus(monkeypatch, tmp_path):
    import omc.internal as internal

    seen = []
    monkeypatch.setattr(internal, "ensure_gitnexus", lambda ctx: seen.append(True) or 0)
    # ensure runs with no repo and no CLI present — must not hit the
    # CLI-present guard or primary-root resolution.
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert internal.run_internal(["gitnexus", "ensure"]) == 0
    assert seen == [True]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_internal.py::test_gitnexus_ensure_calls_ensure_gitnexus -v`
Expected: FAIL — `ensure` falls through to the usage error (returns 2) because it isn't a known verb.

- [ ] **Step 3: Implement the `ensure` interception**

In `src/omc/internal.py`, import `ensure_gitnexus`:

```python
from .gitnexus import ensure_gitnexus, gitnexus_argv, gitnexus_cli
```

At the **top** of `_gitnexus`, before the `--git` parsing block (`src/omc/internal.py:101`):

```python
def _gitnexus(ctx: ToolContext, rest: list[str]) -> int:
    if rest == ["ensure"]:
        return ensure_gitnexus(ctx)
    ...
```

Add `ensure` to `_USAGE` (`src/omc/internal.py:26`):

```python
    " | gitnexus [--git REF] <ensure|query|context|impact|cypher> [args…]"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_internal.py -v`
Expected: PASS (new test + existing internal tests).

- [ ] **Step 5: Commit**

```bash
git add src/omc/internal.py tests/unit/test_internal.py
git commit -m "feat(internal): add 'gitnexus ensure' verb (installs before the CLI-present guard)"
```

---

## Task 3: Prerequisite gate in `omc watch`

**Model:** standard coding tier.

**Files:**
- Modify: `src/omc/watch.py` (`run_watch`, `src/omc/watch.py:399-405`)
- Test: `tests/unit/test_watch.py`

Context: `run_watch` currently hard-errors when the CLI is missing (`src/omc/watch.py:399`), before `ensure_wt_config` and mutex acquisition (`406-407`). Replace that with `require_tools` + `ensure_gitnexus`, placed before the mutex so a failed prerequisite leaves no lock. Every `run_watch` test now goes through `require_tools`, so the shared env helper must stub `wt` and the provider CLI.

- [ ] **Step 1: Update the shared watch env helper and rewrite the missing-CLI test**

In `tests/unit/test_watch.py`, extend `_ctx_with_node_stub` (`tests/unit/test_watch.py:60`) to also stub `wt` and `claude` (both used by `require_tools`; `Config()`'s default provider is claude):

```python
def _ctx_with_node_stub(tmp_path, home):
    """Real git on PATH + recording `node`/`wt`/`claude` stubs + a fake built CLI."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\necho ok\nexit 0\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    for name in ("wt", "claude"):
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} 1.0"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake built CLI")
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return ToolContext.from_env(env), calls
```

Replace `test_watch_requires_gitnexus_cli` (`tests/unit/test_watch.py:324`) — watch now *installs* GitNexus when missing rather than erroring. Test that `run_watch` calls `ensure_gitnexus` (monkeypatched to avoid a real clone) and that a failure aborts before the loop:

```python
def test_watch_installs_gitnexus_when_missing(tmp_path, capsys, monkeypatch):
    import omc.watch as watch_mod

    _, repo = _repo_with_origin(tmp_path)
    # Stub git/wt/claude for require_tools; no gitnexus CLI on disk.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("wt", "claude"):
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} 1.0"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(tmp_path / "home"),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    ctx = ToolContext.from_env(env)
    calls = []
    monkeypatch.setattr(watch_mod, "ensure_gitnexus", lambda c: calls.append(True) or 0)
    # ensure returns 0 but writes no real CLI; make the loop's own gitnexus use
    # a no-op by running --once against an up-to-date repo (no analyze needed).
    old = os.getcwd()
    os.chdir(repo)
    try:
        rc = run_watch(ctx, Config(), interval=1, once=True, enable_documentation=False)
    finally:
        os.chdir(old)
    assert calls == [True]  # ensure_gitnexus was invoked
    assert rc == 0


def test_watch_aborts_when_gitnexus_install_fails(tmp_path, capsys, monkeypatch):
    import omc.watch as watch_mod

    _, repo = _repo_with_origin(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("wt", "claude"):
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} 1.0"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(tmp_path / "home"),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    ctx = ToolContext.from_env(env)
    monkeypatch.setattr(watch_mod, "ensure_gitnexus", lambda c: 1)  # install fails
    from omc.watchlock import instance_lock_path  # existing helper; see watchlock.py

    old = os.getcwd()
    os.chdir(repo)
    try:
        rc = run_watch(ctx, Config(), interval=1, once=True, enable_documentation=False)
    finally:
        os.chdir(old)
    assert rc == 1  # aborted on the failed prerequisite
```

Note: the second test asserts abort-on-failure. If `watchlock` exposes no `instance_lock_path`, drop that import — the `rc == 1` assertion is the contract; a no-lock-left assertion can reuse whatever `tests/unit/test_watch_mutex.py` uses to inspect the lock dir.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_watch.py -v`
Expected: the two new tests FAIL (`ensure_gitnexus` not imported in `watch.py`); some existing tests may error until Step 3 wires `require_tools`.

- [ ] **Step 3: Wire the gate into `run_watch`**

In `src/omc/watch.py`, add imports:

```python
from .gitnexus import ANALYZE_ARGS, ensure_gitnexus, gitnexus_argv, gitnexus_cli
from .probe import require_tools
```

Replace the missing-CLI block (`src/omc/watch.py:399-405`) with the gate, keeping it before `ensure_wt_config`/mutex:

```python
    require_tools(ctx, cfg)  # git/wt/provider — raises OmcError on a miss
    rc = ensure_gitnexus(ctx)
    if rc:
        return rc
    ensure_wt_config(ctx, root)
```

(`gitnexus_cli` may now be unused in `watch.py`; keep the import only if `_refresh_index` still references it — it does not directly, so remove `gitnexus_cli` from the import if the linter flags it.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_watch.py -v`
Expected: PASS (all existing tests now that `_ctx_with_node_stub` stubs wt/claude, plus the two new tests).

- [ ] **Step 5: Commit**

```bash
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "feat(watch): install GitNexus + probe tools as prerequisites, before the mutex"
```

---

## Task 4: Prerequisite in `omc start`

**Model:** standard coding tier.

**Files:**
- Modify: `src/omc/start.py` (`run_start`, after `src/omc/start.py:72`)
- Test: `tests/unit/test_start.py`

Context: `run_start` already calls `require_tools(ctx, cfg)` (`src/omc/start.py:72`); we add `ensure_gitnexus` right after, skipped on `--dry-run` (dry-run makes no changes). `full_env` (`tests/unit/test_start.py:30`) stubs git/wt/claude but has no `node`/CLI, so `ensure_gitnexus` would try to build — monkeypatch it in start tests to a no-op.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_start.py`:

```python
def test_start_ensures_gitnexus(tmp_path, capsys, monkeypatch):
    import omc.start as start_mod

    seen = []
    monkeypatch.setattr(start_mod, "ensure_gitnexus", lambda ctx: seen.append(True) or 0)
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()
    rc = run_start(ctx, Config(), "PROJ-1", headless=True)
    assert rc == 0
    assert seen == [True]  # ensure ran on the real path


def test_start_dry_run_skips_gitnexus_ensure(tmp_path, monkeypatch):
    import omc.start as start_mod

    seen = []
    monkeypatch.setattr(start_mod, "ensure_gitnexus", lambda ctx: seen.append(True) or 0)
    ctx = full_env(tmp_path)
    rc = run_start(ctx, Config(), "PROJ-1", dry_run=True)
    assert rc == 0
    assert seen == []  # dry-run makes no changes


def test_start_aborts_when_gitnexus_install_fails(tmp_path, monkeypatch):
    import omc.start as start_mod
    from omc.errors import OmcError

    monkeypatch.setattr(start_mod, "ensure_gitnexus", lambda ctx: 1)
    ctx = full_env(tmp_path)
    with pytest.raises(OmcError):
        run_start(ctx, Config(), "PROJ-1", headless=True)
```

The third test asserts a non-zero `ensure_gitnexus` aborts start. Decide the mechanism in Step 3 (raise `OmcError` for a uniform failure surface); if you instead return the code, change the test to `assert run_start(...) == 1`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_start.py -v`
Expected: the three new tests FAIL (`ensure_gitnexus` not imported/called in `start.py`).

- [ ] **Step 3: Wire `ensure_gitnexus` into `run_start`**

In `src/omc/start.py`, import:

```python
from .gitnexus import ensure_gitnexus
```

After `require_tools(ctx, cfg)` (`src/omc/start.py:72`) and the plugin-ensure line, add the GitNexus prerequisite, guarded off the dry-run path. Place it before slug generation:

```python
    if not dry_run:
        rc = ensure_gitnexus(ctx)
        if rc:
            raise OmcError("GitNexus is required but could not be installed")
```

`OmcError` is already imported in `start.py`. Using `raise` matches how `require_tools` fails and how `main()` renders errors; the healthy path stays silent (Task 1).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_start.py -v`
Expected: PASS (new tests + existing start tests unchanged — `full_env` already covers `require_tools`).

- [ ] **Step 5: Commit**

```bash
git add src/omc/start.py tests/unit/test_start.py
git commit -m "feat(start): ensure GitNexus as a boot prerequisite (skipped on --dry-run)"
```

---

## Task 5: `require_tools` + install-or-update in `omc update`

**Model:** heavy coding tier (reorders `run_update`, interacts with several existing installer tests, and changes real-world update semantics).

**Files:**
- Modify: `src/omc/installer.py` (`run_update`, `src/omc/installer.py:57-96`)
- Test: `tests/unit/test_installer.py`

Context: `run_update` loads config only after the gitnexus step (`src/omc/installer.py:68`). Reorder so config loads up front; when present, run `require_tools(ctx, cfg)` (fatal) before `update_gitnexus`; when absent, skip `require_tools` but still update GitNexus. `require_tools` probes the **default** provider only (`src/omc/probe.py:44`), so the existing installer tests must stub `wt` and make the default provider's `--version` succeed.

- [ ] **Step 1: Update installer test infrastructure and add new tests**

In `tests/unit/test_installer.py`, the `_stub` helper (`tests/unit/test_installer.py:87`) makes a stub that exits `rc` for **all** args. `require_tools` calls `claude --version`; the failure-isolation test uses `claude_rc=1`, which would fail that probe and abort update before the loop. Make the claude stub `--version`-aware and add a `wt` stub in `_update_ctx`:

```python
def _stub(bindir, name, rc=0):
    calls = bindir / f"{name}.calls"
    exe = bindir / name
    # --version always succeeds (require_tools probe); other subcommands use rc.
    exe.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\n'
        f'case "$1" in --version) exit 0 ;; *) exit {rc} ;; esac\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return calls


def _update_ctx(tmp_path, *, claude_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv_calls = _stub(bindir, "uv")
    claude_calls = _stub(bindir, "claude", rc=claude_rc)
    codex_calls = _stub(bindir, "codex")
    _stub(bindir, "wt")  # require_tools probes git/wt/provider
    _stub(bindir, "git")  # deterministic --version for the probe
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    cfg = GlobalConfig()
    cfg.llm.providers = {"claude": ProviderConfig(), "codex": ProviderConfig()}
    store.save_global(ctx.home, cfg)
    return ctx, uv_calls, claude_calls, codex_calls
```

Add a test that a missing tool aborts update (config present), and update `test_update_isolates_unknown_provider` (which stubs only uv+codex but keeps default provider claude) to also stub claude/wt/git so `require_tools` passes:

```python
def test_update_aborts_when_required_tool_missing(tmp_path, capsys):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    _stub(bindir, "claude")  # default provider present…
    _stub(bindir, "git")
    # …but NO wt stub → require_tools raises.
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    cfg = GlobalConfig()
    cfg.llm.providers = {"claude": ProviderConfig()}
    store.save_global(ctx.home, cfg)
    monkeypatch_gitnexus_noop(tmp_path)  # see below
    from omc.errors import OmcError
    import pytest

    with pytest.raises(OmcError) as exc:
        run_update(ctx)
    assert "wt" in str(exc.value)
```

Because `require_tools` runs **before** `update_gitnexus`, and the existing tests don't want a real clone, monkeypatch `omc.gitnexus.update_gitnexus` to a no-op in the `_update_ctx`-based tests that currently rely on it running harmlessly (it currently prints the "not installed → skip" line and returns 0; after Task 1 it would attempt a clone). Add a fixture/helper:

```python
import pytest

@pytest.fixture(autouse=True)
def _no_real_gitnexus(monkeypatch):
    # Installer tests exercise require_tools + the plugin loop, not the real
    # clone/build. Keep update_gitnexus a no-op success here.
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 0)
```

(Then the `monkeypatch_gitnexus_noop` reference above is unnecessary — the autouse fixture covers every test in the module. Keep the two existing `test_run_update_*` monkeypatch tests, which set their own `update_gitnexus` return; a per-test `monkeypatch.setattr` overrides the autouse one.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_installer.py -v`
Expected: `test_update_aborts_when_required_tool_missing` FAILs (require_tools not yet wired); some existing tests may need the reordering from Step 3.

- [ ] **Step 3: Reorder `run_update`**

In `src/omc/installer.py`, add the import:

```python
from .probe import require_tools
```

Rewrite the head of `run_update` (`src/omc/installer.py:57-70`) so config loads first, `require_tools` gates when configured, then GitNexus, then the plugin loop:

```python
def run_update(ctx: ToolContext) -> int:
    print("Updating omc via uv…", file=sys.stderr)
    rc = _uv(ctx, "tool", "upgrade", "omc")
    if rc != 0:
        return rc
    cfg = store.load_global(ctx.home)
    if cfg is not None:
        require_tools(ctx, cfg)  # git/wt/provider — raises OmcError on a miss
    # Managed dependencies (GitNexus). Module-attribute import so tests can
    # monkeypatch omc.gitnexus.update_gitnexus; a failure here fails the command.
    from . import gitnexus

    dep_rc = gitnexus.update_gitnexus(ctx)
    if cfg is None:
        print("· no config — skipping plugin updates (run `omc configure`)", file=sys.stderr)
        return dep_rc
    ...  # plugin loop unchanged for now (Task 6 adds marketplace_source)
```

`require_tools`'s annotation is `Config`; `store.load_global` returns `GlobalConfig`. Both expose `llm.default`, which is all `require_tools` reads. Widen the annotation to accept either — change `src/omc/probe.py:38` signature to `def require_tools(ctx: ToolContext, cfg: Config | GlobalConfig) -> None:` and import `GlobalConfig` there (`from .config.schema import Config, GlobalConfig`).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_installer.py -v`
Expected: PASS. `test_update_without_config_skips_plugins` still passes (no config → require_tools skipped). `test_update_isolates_provider_failures` passes (claude `--version` now succeeds; the plugin subcommands still fail → `✗` narrated).

- [ ] **Step 5: Commit**

```bash
git add src/omc/installer.py src/omc/probe.py tests/unit/test_installer.py
git commit -m "feat(update): require git/wt/provider, then install-or-update GitNexus"
```

---

## Task 6: Marketplace self-heal in `omc update`

**Model:** standard coding tier.

**Files:**
- Modify: `src/omc/providers/base.py:95`, `src/omc/providers/claude.py:108`, `src/omc/providers/codex.py:47`, `src/omc/providers/opencode.py:58`
- Modify: `src/omc/installer.py` (plugin loop)
- Test: `tests/unit/test_providers.py`, `tests/unit/test_installer.py`

Context: `omc update`'s claude sequence (`src/omc/providers/claude.py:108`) runs `marketplace update` + `plugin update` with no `marketplace add`, so a missing marketplace registration errors. `ensure_plugin` self-heals with `marketplace add <source>` (`src/omc/plugin.py:62`); `marketplace_source(env)` (`src/omc/plugin.py:25`) computes the source. Change the provider contract to accept the source; claude prepends the add; `run_update` treats the marketplace add/update as best-effort with `plugin update` as the pass/fail signal.

- [ ] **Step 1: Write failing provider + installer tests**

In `tests/unit/test_providers.py`, add (find the existing `plugin_update_argvs` assertions and mirror them):

```python
def test_claude_plugin_update_prepends_marketplace_add():
    from omc.providers.claude import ClaudeProvider

    argvs = ClaudeProvider().plugin_update_argvs("chris-husse/oh-my-clanker")
    assert argvs[0] == ["claude", "plugin", "marketplace", "add", "chris-husse/oh-my-clanker"]
    assert ["claude", "plugin", "marketplace", "update", "oh-my-clanker"] in argvs
    assert ["claude", "plugin", "update", "omc@oh-my-clanker"] in argvs


def test_claude_plugin_update_without_source_omits_add():
    from omc.providers.claude import ClaudeProvider

    argvs = ClaudeProvider().plugin_update_argvs()
    assert not any("add" in a for a in argvs)  # no source → no marketplace add


def test_codex_ignores_marketplace_source():
    from omc.providers.codex import CodexProvider

    assert CodexProvider().plugin_update_argvs("anything") == [
        ["codex", "plugin", "marketplace", "upgrade"]
    ]
```

In `tests/unit/test_installer.py`, assert `run_update` registers the marketplace and stays green on a benign re-add. Reuse the `_update_ctx` helper (claude stub echoes args, exits 0 for all here):

```python
def test_update_registers_marketplace_before_updating(tmp_path):
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(tmp_path)
    assert run_update(ctx) == 0
    recorded = claude_calls.read_text()
    assert "plugin marketplace add" in recorded  # self-heal registration
    assert "plugin marketplace update oh-my-clanker" in recorded
    assert "plugin update omc@oh-my-clanker" in recorded
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_providers.py tests/unit/test_installer.py -v`
Expected: the new provider tests FAIL (`plugin_update_argvs` takes no arg yet); the installer test FAILs (no `marketplace add`).

- [ ] **Step 3: Change the provider contract + claude implementation**

`src/omc/providers/base.py:95`:

```python
    @abstractmethod
    def plugin_update_argvs(self, marketplace_source: str | None = None) -> list[list[str]]:
        """Commands that update this provider's installed omc plugin, in order.

        ``marketplace_source`` (owner/repo or a local path) lets a provider
        self-heal a missing marketplace registration; providers that don't need
        it ignore the argument. [] means no scriptable update path is known yet.
        Builders stay pure (no I/O)."""
```

`src/omc/providers/claude.py:108`:

```python
    def plugin_update_argvs(self, marketplace_source: str | None = None):
        # Self-heal the marketplace registration first (best-effort — a re-add
        # of an existing marketplace is benign), then snapshot + update. Claude
        # docs: "restart required to apply" — running sessions keep the old plugin.
        argvs = []
        if marketplace_source:
            argvs.append(["claude", "plugin", "marketplace", "add", marketplace_source])
        argvs += [
            ["claude", "plugin", "marketplace", "update", "oh-my-clanker"],
            ["claude", "plugin", "update", "omc@oh-my-clanker"],
        ]
        return argvs
```

`src/omc/providers/codex.py:47` and `src/omc/providers/opencode.py:58`: add the parameter, ignore it:

```python
    def plugin_update_argvs(self, marketplace_source: str | None = None):
```

- [ ] **Step 4: Pass the source and make marketplace steps best-effort in `run_update`**

In `src/omc/installer.py`, import `marketplace_source`:

```python
from .plugin import marketplace_source
```

In the plugin loop (`src/omc/installer.py:72-95`), pass the source and treat every argv except the final one as best-effort — a non-zero `marketplace add`/`update` is logged quietly and does not break the sequence or mark failure; only the last argv (`plugin update`) decides the `✓`/`✗`:

```python
    source = marketplace_source(ctx.env)
    for name in cfg.llm.providers:
        try:
            argvs = get_provider(name).plugin_update_argvs(source)
        except OmcError as exc:
            print(f"✗ {name}: {exc} — continuing", file=sys.stderr)
            continue
        if not argvs:
            print(f"· {name}: no scriptable plugin update yet — update it in-app", file=sys.stderr)
            continue
        ok = True
        for i, argv in enumerate(argvs):
            is_last = i == len(argvs) - 1
            try:
                cp = ctx.run(argv)
            except OSError as exc:
                print(f"✗ {name}: {argv[0]} not runnable ({exc}) — continuing", file=sys.stderr)
                ok = False
                break
            if cp.returncode != 0 and is_last:
                detail = (cp.stderr or cp.stdout or "").strip()[:200]
                print(f"✗ {name}: {' '.join(argv)} failed: {detail} — continuing", file=sys.stderr)
                ok = False
                break
            # non-last (marketplace add/update) failures are benign self-heal
            # steps — never abort the sequence or mark failure.
        if ok:
            print(f"✓ {name}: plugin updated", file=sys.stderr)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/unit/test_providers.py tests/unit/test_installer.py -v`
Expected: PASS. `test_update_isolates_provider_failures` (claude_rc=1) still narrates `✗` — the final `plugin update` fails; the benign earlier steps don't mask it.

- [ ] **Step 6: Commit**

```bash
git add src/omc/providers/ src/omc/installer.py tests/unit/test_providers.py tests/unit/test_installer.py
git commit -m "fix(update): self-heal claude marketplace registration before updating the plugin"
```

---

## Task 7: Skill wrapper + stale hints

**Model:** standard coding tier.

**Files:**
- Rewrite: `skills/gitnexus-ensure/SKILL.md`
- Modify: `src/omc/internal.py` (hint at `src/omc/internal.py:112`)
- Modify: `src/omc/dependency.py` (hint at `src/omc/dependency.py:301`)
- Test: `tests/unit/test_dependency.py` (only if it asserts the hint text)

Context: with Python owning install, the "run /omc:index once" hints are stale; `watch.py`'s copy was removed in Task 3. The `gitnexus-ensure` skill becomes a thin wrapper over `omc internal gitnexus ensure`; the index/explain/document skills keep their "Step 1 — run gitnexus-ensure" line unchanged (now bottoming out in Python).

- [ ] **Step 1: Rewrite the skill**

Replace `skills/gitnexus-ensure/SKILL.md` body (keep the frontmatter `name`/`description`) with:

```markdown
# omc gitnexus-ensure (internal)

GitNexus is omc's managed code-knowledge-graph dependency, installed and built
by the Python CLI. It runs as `node <CLI>` where:

    CLI = ~/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js

(`~/.omc` is `$OMC_HOME` when that env var is set.)

## Ensure it

Run:

    omc internal gitnexus ensure

This installs GitNexus when missing (approved-source clone from
`https://github.com/chris-husse/GitNexus.git` + the two-step npm build) and is a
silent no-op when the CLI is already healthy. It refuses any existing clone
whose origin is not the approved source. Report what it prints; on a non-zero
exit, surface its output and stop — never claim success on a broken build.
```

- [ ] **Step 2: Update the stale hints**

`src/omc/internal.py:112` (query-proxy CLI-missing branch):

```python
        print(
            "error: GitNexus is not installed — run `omc update` (or `omc start`/`omc watch`) "
            "to install it",
            file=sys.stderr,
        )
```

`src/omc/dependency.py:301` (`run_ensure` CLI-missing branch): same wording.

- [ ] **Step 3: Run the affected tests**

Run: `uv run pytest tests/unit/test_internal.py tests/unit/test_dependency.py -v`
Expected: PASS. If any test asserts the old "/omc:index once" string, update that assertion to the new wording.

- [ ] **Step 4: Commit**

```bash
git add skills/gitnexus-ensure/SKILL.md src/omc/internal.py src/omc/dependency.py tests/unit/
git commit -m "docs(gitnexus): skill wraps 'internal gitnexus ensure'; refresh install hints"
```

---

## Task 8: Full verification pass

**Model:** top tier (final review/verification).

**Files:** none (verification only).

- [ ] **Step 1: Run the whole unit suite**

Run: `uv run pytest tests/unit -q`
Expected: all green. Investigate any failure against the task that owns the file.

- [ ] **Step 2: Lint + type-check**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Run: `uv run mypy src` (if the repo runs mypy in CI — check `pyproject.toml`/CI config first; skip if not configured).
Expected: clean. Fix any unused-import warnings (e.g. `gitnexus_cli` in `watch.py`).

- [ ] **Step 3: Grep for leftover stale references**

Run: `git grep -n "installs it on first use\|/omc:index once"` 
Expected: no hits in `src/` (only in older spec/plan docs, which are historical and left alone).

- [ ] **Step 4: Sanity-check the E2E prebake is untouched**

Run: `git diff --name-only origin/main -- docker/`
Expected: empty — `docker/Dockerfile.e2e` still prebakes the clone+build, so `ensure_gitnexus` no-ops in E2E (no image change needed).

- [ ] **Step 5: Commit any verification fixes**

```bash
git add -A
git commit -m "chore: verification pass — lint/type/test green"
```

---

## Self-review notes

- **Spec coverage:** ensure_gitnexus (Task 1); internal verb (Task 2); watch gate (Task 3); start gate (Task 4); update require_tools + install-or-update (Task 5); marketplace self-heal (Task 6); skill wrapper + hints (Task 7); E2E untouched (Task 8 Step 4).
- **Type consistency:** `ensure_gitnexus(ctx, *, approved_origin=...)` and `_clone_if_missing(ctx, root, approved_origin)` / `_build(ctx, root)` signatures are consistent across Tasks 1–4. `plugin_update_argvs(self, marketplace_source=None)` is uniform across base/claude/codex/opencode (Task 6). `require_tools(ctx, cfg: Config | GlobalConfig)` widened in Task 5.
- **Ordering interaction (Task 5):** `require_tools` runs before `update_gitnexus`; a missing `wt` aborts update before GitNexus installs — this is the user-confirmed require_tools-first behavior. The autouse `_no_real_gitnexus` fixture keeps installer tests network-free.
```
