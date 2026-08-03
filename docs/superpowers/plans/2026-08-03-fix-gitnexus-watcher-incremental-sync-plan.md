# Fix GitNexus Watcher Incremental Sync (Flat-Store Inversion Heal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `omc watch` detects when the GitNexus default store belongs to a branch other than the configured base (the flat-store inversion) and heals it: destroy the index including the generated docs, rebuild on the base branch.

**Architecture:** Two pure detection helpers in `src/omc/gitnexus.py` read the flat store's `meta.json`; a guarded docs-mirror deleter joins `src/omc/mirror.py`; `_refresh_index` in `src/omc/watch.py` checks inversion before its incremental analyze and, when inverted, runs a heal (`clean --force` → full analyze → docs-mirror handling) verified by post-conditions on the flat meta, never by exit codes (GitNexus `clean` exits 0 on failure). Every failure warns and skips per watch doctrine — the loop never crashes.

**Tech Stack:** Python 3 (stdlib only — json, shutil, pathlib), pytest, existing Docker e2e harness.

**Spec:** `docs/superpowers/specs/2026-08-03-fix-gitnexus-watcher-incremental-sync-design.md` — read it before starting any task.

## Global Constraints

- Watch doctrine: tick actions warn and skip on failure; they NEVER raise out of the loop.
- Never claim success on unverified state: post-conditions on `.gitnexus/meta.json` decide, not exit codes.
- The only destructive operations allowed: `gitnexus clean --force` (via `ctx.run`, cwd = primary root) and deleting `<root>/.omc/docs/gitnexus/docs` (gitignored). Nothing else is removed.
- No new dependencies. All subprocess calls go through `ToolContext.run` (`ctx.run`).
- Narration lines go through watch's `_say` (stderr). Exact strings are specified per task — the e2e asserts them.
- Every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Detection helpers `flat_store_branch` / `store_inverted`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/gitnexus.py` (add `import json`; add two functions after `gitnexus_root`, around line 38)
- Test: `tests/unit/test_gitnexus_store.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 3 imports these from `omc.gitnexus`):
  - `flat_store_branch(root: Path) -> str | None`
  - `store_inverted(root: Path, base: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_gitnexus_store.py`:

```python
"""flat_store_branch / store_inverted — flat-store inversion detection."""

import json
from pathlib import Path

from omc.gitnexus import flat_store_branch, store_inverted


def _seed_meta(root: Path, payload) -> None:
    d = root / ".gitnexus"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(payload, encoding="utf-8")


def test_absent_store_is_none_and_not_inverted(tmp_path):
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_unparseable_meta_is_none_and_not_inverted(tmp_path):
    _seed_meta(tmp_path, "{not json")
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_missing_or_empty_branch_stamp_is_none(tmp_path):
    _seed_meta(tmp_path, json.dumps({"lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) is None
    _seed_meta(tmp_path, json.dumps({"branch": "", "lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_owner_equals_base_is_not_inverted(tmp_path):
    _seed_meta(tmp_path, json.dumps({"branch": "main", "lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) == "main"
    assert store_inverted(tmp_path, "main") is False


def test_owner_differs_from_base_is_inverted(tmp_path):
    _seed_meta(tmp_path, json.dumps({"branch": "feature/omc-v1", "lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) == "feature/omc-v1"
    assert store_inverted(tmp_path, "main") is True


def test_non_dict_meta_is_none(tmp_path):
    _seed_meta(tmp_path, json.dumps(["not", "a", "dict"]))
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_gitnexus_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'flat_store_branch'`

- [ ] **Step 3: Implement the helpers**

In `src/omc/gitnexus.py`, add `import json` to the imports, then after `gitnexus_root`:

```python
def flat_store_branch(root: Path) -> str | None:
    """Branch stamped in the flat (default) GitNexus store, or None.

    None covers: store absent, meta unreadable/unparseable, branch field
    missing/empty/non-string. All of those are states GitNexus's adoption
    rule resolves on the next analyze — only a non-empty FOREIGN stamp
    (see store_inverted) needs the heal.
    """
    meta = Path(root) / ".gitnexus" / "meta.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    branch = data.get("branch") if isinstance(data, dict) else None
    return branch if isinstance(branch, str) and branch else None


def store_inverted(root: Path, base: str) -> bool:
    """True when the flat store belongs to a branch other than ``base``.

    GitNexus keys its default store to the branch a repo was FIRST indexed
    on; analyzes on any other branch land in .gitnexus/branches/<slug>/,
    which the MCP server, staleness hints, and `wiki` never read. Inverted
    means: incremental refreshes are being delivered where nothing looks.
    """
    owner = flat_store_branch(root)
    return owner is not None and owner != base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_gitnexus_store.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full unit suite (no regressions)**

Run: `uv run pytest tests/unit -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/omc/gitnexus.py tests/unit/test_gitnexus_store.py
git commit -m "Detect the GitNexus flat-store inversion

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Guarded docs-mirror deletion in mirror.py

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/mirror.py`
- Test: `tests/unit/test_mirror.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 3 imports these from `omc.mirror`):
  - `DOCS_MIRROR_REL: Path` — value `Path(".omc/docs/gitnexus/docs")`
  - `clear_docs_mirror(root: Path) -> bool` — True when a mirror existed and was removed

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mirror.py`:

```python
def test_clear_docs_mirror_removes_and_reports(tmp_path):
    from omc.mirror import DOCS_MIRROR_REL, clear_docs_mirror

    target = tmp_path / DOCS_MIRROR_REL
    target.mkdir(parents=True)
    (target / "page.md").write_text("stale")
    assert clear_docs_mirror(tmp_path) is True
    assert not target.exists()
    # parent dirs (.omc/docs/gitnexus) are left alone
    assert target.parent.is_dir()


def test_clear_docs_mirror_absent_is_noop(tmp_path):
    from omc.mirror import clear_docs_mirror

    assert clear_docs_mirror(tmp_path) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mirror.py -v`
Expected: the two new tests FAIL with `ImportError: cannot import name 'DOCS_MIRROR_REL'`

- [ ] **Step 3: Implement**

In `src/omc/mirror.py`, after `SNAPSHOT_DIRS`:

```python
# Where watch mirrors the generated wiki inside a root. Fixed relative path —
# clear_docs_mirror deletes ONLY this, by construction.
DOCS_MIRROR_REL = Path(".omc/docs/gitnexus/docs")


def clear_docs_mirror(root: Path) -> bool:
    """Delete the generated-docs mirror under ``root``; True when removed.

    Used by the watch heal: docs generated from an inverted (frozen) graph
    cite deleted files as current — stale docs are worse than absent docs.
    """
    target = Path(root) / DOCS_MIRROR_REL
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mirror.py -v`
Expected: all pass (existing 4 + new 2)

- [ ] **Step 5: Commit**

```bash
git add src/omc/mirror.py tests/unit/test_mirror.py
git commit -m "Add guarded deletion of the generated-docs mirror

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Heal orchestration in watch

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/watch.py` (`_refresh_index`, new `_heal_store`; imports)
- Test: `tests/unit/test_watch.py` (append)

**Interfaces:**
- Consumes:
  - `flat_store_branch(root: Path) -> str | None`, `store_inverted(root: Path, base: str) -> bool` from `omc.gitnexus` (Task 1)
  - `DOCS_MIRROR_REL: Path`, `clear_docs_mirror(root: Path) -> bool` from `omc.mirror` (Task 2)
- Produces: no new public surface — `_refresh_index(ctx, cfg, root, enable_documentation)` keeps its signature; behavior changes only on inverted stores.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_watch.py`, and add `import json` to the module's top import block (next to `import os`) — not mid-file (ruff E402). The existing `_ctx_with_node_stub` node stub only records argv; the heal verifies post-conditions on `.gitnexus/meta.json`, so these tests need a stub that SIMULATES GitNexus side effects (clean removes the store, analyze writes a stamped meta). `ctx.run` uses `cwd=root`, so relative paths in the stub hit the repo.

```python
def _seed_inverted_store(repo, owner="feature/omc-v1"):
    d = repo / ".gitnexus"
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"branch": owner, "lastCommit": "old"}))


def _seed_docs_mirror(repo):
    docs = repo / ".omc" / "docs" / "gitnexus" / "docs"
    docs.mkdir(parents=True)
    (docs / "stale.md").write_text("cites deleted files")
    return docs


def _ctx_with_healing_node_stub(tmp_path, home, *, clean_removes=True, analyze_stamps="main"):
    """Like _ctx_with_node_stub, but `node` simulates GitNexus side effects:
    clean removes .gitnexus, analyze writes a meta stamped `analyze_stamps`.
    Knobs simulate the failure modes (clean that silently fails, analyze
    that stamps the wrong branch)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "node.calls"
    clean_cmd = "rm -rf .gitnexus" if clean_removes else ":"
    node = bindir / "node"
    node.write_text(
        '#!/bin/sh\n'
        f'echo "$@" >> "{calls}"\n'
        'case "$*" in\n'
        f'  *" clean --force") {clean_cmd} ;;\n'
        '  *" analyze --skip-agents-md --skip-skills") mkdir -p .gitnexus; '
        f"printf '{{\"branch\":\"{analyze_stamps}\",\"lastCommit\":\"new\"}}' > .gitnexus/meta.json ;;\n"
        'esac\n'
        'echo ok\nexit 0\n'
    )
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


def test_refresh_heals_inverted_store(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_inverted_store(repo)
    docs = _seed_docs_mirror(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "owned by 'feature/omc-v1', not 'main' — destroying and rebuilding" in err
    assert "✓ index rebuilt for main" in err
    assert "docs mirror cleared — run omc watch --once --enable-documentation" in err
    recorded = calls.read_text()
    assert "clean --force" in recorded
    assert "analyze --skip-agents-md --skip-skills" in recorded
    # clean ran BEFORE analyze
    assert recorded.index("clean --force") < recorded.index("analyze")
    # post-conditions actually hold
    assert json.loads((repo / ".gitnexus" / "meta.json").read_text())["branch"] == "main"
    assert not docs.exists()
    # healed path must not ALSO run the incremental analyze (exactly one analyze)
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1


def test_refresh_healthy_store_stays_incremental(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_inverted_store(repo, owner="main")  # owner == base: NOT inverted
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    recorded = calls.read_text()
    assert "clean --force" not in recorded
    assert "analyze --skip-agents-md --skip-skills" in recorded
    assert "destroying and rebuilding" not in capsys.readouterr().err


def test_heal_clean_failure_warns_and_skips(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_inverted_store(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home", clean_removes=False)
    assert _run_once(repo, ctx) == 0  # warn-and-skip: exit code stays 0
    err = capsys.readouterr().err
    assert "✗ clean did not remove the index" in err
    recorded = calls.read_text()
    assert "clean --force" in recorded
    assert "analyze" not in recorded  # aborted before rebuild


def test_heal_wrong_stamp_never_claims_success(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_inverted_store(repo)
    ctx, _ = _ctx_with_healing_node_stub(
        tmp_path, tmp_path / "home", analyze_stamps="feature/omc-v1"
    )
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "✗ rebuilt index is not owned by 'main' — not claiming success" in err
    assert "✓ index rebuilt" not in err


def test_heal_with_documentation_regenerates_wiki(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_inverted_store(repo)
    _seed_docs_mirror(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx, enable_documentation=True) == 0
    recorded = calls.read_text()
    assert "clean --force" in recorded
    assert "wiki --provider claude" in recorded
    err = capsys.readouterr().err
    assert "docs mirror cleared — run omc watch" not in err  # hint only when docs are OFF
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -k "heal or healthy_store" -v`
Expected: 5 FAIL (no heal path exists; inverted store currently takes the incremental branch)

- [ ] **Step 3: Implement `_heal_store` and wire `_refresh_index`**

In `src/omc/watch.py`:

Imports — extend the existing lines:

```python
from .gitnexus import ANALYZE_ARGS, ensure_gitnexus, flat_store_branch, gitnexus_argv, store_inverted
from .mirror import DOCS_MIRROR_REL, clear_docs_mirror, mirror_dir
```

New function directly above `_refresh_index`:

```python
def _heal_store(ctx: ToolContext, root: str, base: str) -> bool:
    """Destroy and rebuild the GitNexus index when the flat (default) store
    belongs to a branch other than `base` — the flat-store inversion: GitNexus
    keys the default store to the FIRST-indexed branch, so incremental analyze
    lands in a side store the MCP server, staleness hints, and `wiki` never
    read. `gitnexus clean` exits 0 even when deletion fails, so every step is
    judged by its POST-CONDITION on the flat meta, never by exit code. The
    stale docs mirror is deleted unconditionally: docs generated from the
    frozen graph cite deleted files as current."""
    owner = flat_store_branch(Path(root))
    _say(f"✗ GitNexus index is owned by {owner!r}, not {base!r} — destroying and rebuilding")
    if clear_docs_mirror(Path(root)):
        _say("· stale docs mirror deleted")
    cp = ctx.run(gitnexus_argv(ctx, "clean", "--force"), cwd=root)
    if flat_store_branch(Path(root)) is not None:
        _say(f"✗ clean did not remove the index: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return False
    cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=root)
    if cp.returncode != 0:
        _say(f"✗ full analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return False
    if flat_store_branch(Path(root)) != base:
        _say(f"✗ rebuilt index is not owned by {base!r} — not claiming success")
        return False
    _say(f"✓ index rebuilt for {base}")
    return True
```

Rewrite `_refresh_index` (keep signature; the wiki tail is today's code with the docs path swapped to `DOCS_MIRROR_REL`):

```python
def _refresh_index(ctx: ToolContext, cfg: Config, root: str, enable_documentation: bool) -> None:
    base = cfg.worktree.base_branch
    healed = False
    if store_inverted(Path(root), base):
        if not _heal_store(ctx, root, base):
            return  # warned inside; warn-and-skip, next tick retries
        healed = True
    else:
        _say("→ refreshing GitNexus index (incremental)")
        cp = ctx.run(gitnexus_argv(ctx, *ANALYZE_ARGS), cwd=root)
        if cp.returncode != 0:
            _say(f"✗ analyze failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
            return
        _say("✓ index refreshed")
    if not enable_documentation:
        if healed:
            _say("· docs mirror cleared — run omc watch --once --enable-documentation to regenerate")
        return
    name = cfg.llm.default
    wiki_args = ["wiki", "--provider", name]
    # Docs model, never the session model: wiki is bulk grounded summarization
    # and a thinking-heavy session model turns it into an hours-long silent run.
    docs_model = docs_model_for(cfg, name)
    if docs_model:
        wiki_args += ["--model", docs_model]
    _say(f"→ regenerating documentation via {name} (LLM-heavy)")
    cp = ctx.run(gitnexus_argv(ctx, *wiki_args), cwd=root)
    if cp.returncode != 0:
        _say(f"✗ wiki failed: {(cp.stderr or cp.stdout or '').strip()[:400]}")
        return
    wiki = Path(root) / ".gitnexus" / "wiki"
    if wiki.is_dir():
        mirror_dir(wiki, Path(root) / DOCS_MIRROR_REL)
        _say("✓ documentation refreshed → .omc/docs/gitnexus/docs")
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/unit/test_watch.py -k "heal or healthy_store" -v`
Expected: 5 passed

- [ ] **Step 5: Run the full watch suite + whole unit suite (no regressions — the healthy path must be byte-identical in behavior)**

Run: `uv run pytest tests/unit/test_watch.py -q && uv run pytest tests/unit -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "Heal the GitNexus flat-store inversion from omc watch

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: E2E — real heal in the container

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/test_e2e_watch.py` (append)

**Interfaces:**
- Consumes: `make_work_repo`, `run_in` from `tests/e2e/harness.py`; the pre-baked GitNexus CLI at `/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js`; narration strings from Task 3.
- Produces: nothing downstream.

- [ ] **Step 1: Write the e2e test**

Append to `tests/e2e/test_e2e_watch.py` (module docstring says it: real git + real GitNexus, no tokens — do NOT pass `--enable-documentation`):

```python
_CLI = "/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js"


def test_watch_once_heals_feature_branch_owned_index(container):
    repo = make_work_repo(container, path="/work/heal-repo")
    # Arrange the inversion: FIRST index runs on a feature branch, so the
    # flat store is stamped with it; then the branch dies.
    rc, out = run_in(container, ["git", "switch", "-qc", "feature/first"], cwd=repo)
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, f"seed analyze failed:\n{out[:1500]}"
    rc, out = run_in(
        container,
        ["bash", "-c", f"grep -o '\"branch\"[^,]*' {repo}/.gitnexus/meta.json"],
    )
    assert "feature/first" in out, f"seed did not stamp the flat store:\n{out}"
    # stale docs mirror that the heal must clear
    rc, _ = run_in(
        container,
        ["bash", "-c", f"mkdir -p {repo}/.omc/docs/gitnexus/docs && echo stale > {repo}/.omc/docs/gitnexus/docs/x.md"],
    )
    assert rc == 0
    rc, out = run_in(container, ["git", "switch", "-q", "main"], cwd=repo)
    assert rc == 0, out
    rc, out = run_in(container, ["git", "branch", "-qD", "feature/first"], cwd=repo)
    assert rc == 0, out

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, f"watch --once failed:\n{out[:2000]}"
    assert "destroying and rebuilding" in out, out
    assert "index rebuilt for main" in out, out
    assert "docs mirror cleared" in out, out

    # flat store now belongs to main at HEAD; no shadow branch store; registry advanced
    check = (
        "import json, subprocess, sys\n"
        f"meta = json.load(open('{repo}/.gitnexus/meta.json'))\n"
        f"head = subprocess.run(['git', '-C', '{repo}', 'rev-parse', 'HEAD'],"
        " capture_output=True, text=True).stdout.strip()\n"
        "assert meta['branch'] == 'main', meta['branch']\n"
        "assert meta['lastCommit'] == head, (meta['lastCommit'], head)\n"
        "reg = json.load(open('/root/.gitnexus/registry.json'))\n"
        "entries = reg if isinstance(reg, list) else reg.get('repos', [])\n"
        f"entry = [e for e in entries if e.get('path') == '{repo}']\n"
        "assert entry and entry[0]['lastCommit'] == head, entry\n"
        "print('HEAL-OK')\n"
    )
    rc, out = run_in(container, ["python3", "-c", check])
    assert rc == 0 and "HEAL-OK" in out, out
    rc, _ = run_in(container, ["bash", "-c", f"ls {repo}/.gitnexus/branches 2>/dev/null | grep ."])
    assert rc != 0, "shadow branch store survived the heal"
    rc, _ = run_in(container, ["test", "-e", f"{repo}/.omc/docs/gitnexus/docs/x.md"])
    assert rc != 0, "stale docs mirror survived the heal"
```

Note for the implementer: the registry file location/shape assertion (`/root/.gitnexus/registry.json`, list vs `{"repos": [...]}`) must be verified against the real file in the container on first run — adjust the `entries =` line to the actual shape, keeping the `lastCommit == head` assertion intact.

- [ ] **Step 2: Run the e2e test**

Run: `uv run pytest tests/e2e/test_e2e_watch.py::test_watch_once_heals_feature_branch_owned_index -v`
Expected: PASS (requires Docker; the suite builds/uses the e2e image per `tests/e2e/conftest.py`)

- [ ] **Step 3: Run the full watch e2e file (no regressions)**

Run: `uv run pytest tests/e2e/test_e2e_watch.py -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_e2e_watch.py
git commit -m "E2E: watch --once heals a feature-branch-owned GitNexus index

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
