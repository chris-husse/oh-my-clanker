# Project post-watch hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `omc watch` runs a project-owned `.omc/hooks/post-watch.sh` after every watch cycle that did real work, warning (never crashing) on failure with a link to a temp log.

**Architecture:** One new helper `_post_watch_hook(ctx, root, outcome)` in `src/omc/watch.py`, called from `run_watch`'s loop when `_tick`'s token is `"synced"` or `"refreshed"`. Convention-based discovery (file exists → runs), no `Config` change, no CLI flag. Spec: `docs/superpowers/specs/2026-07-18-add-post-watch-project-hook-design.md`.

**Tech Stack:** Python 3 (stdlib only: `subprocess`, `tempfile`, `os`), pytest, testcontainers E2E (`tests/e2e/`).

## Global Constraints

- Watch doctrine: hook failures warn and the loop continues — never crash, never change `--once`'s exit code (stays 0 on hook failure).
- Subprocess boundary: everything goes through `ToolContext.run` — array argv, never `shell=True`.
- Narration strings, verbatim: start `→ running project post-watch hook (.omc/hooks/post-watch.sh)`; success `✓ post-watch hook done`; failure `✗ post-watch hook failed (exit N) — log: <path>` (timeout: `(timeout)` in place of `(exit N)`).
- Hook fires ONLY on action tokens `"synced"` / `"refreshed"`; quiet ticks never fire it.
- Hook env gets `OMC_WATCH_OUTCOME` = the token; cwd = primary root; timeout = module constant `_HOOK_TIMEOUT = 600`.
- Log written on EVERY hook run via `tempfile.mkstemp(prefix="omc-post-watch-", suffix=".log")`; path narrated only on failure.
- No new dependencies. TDD: each task commits red→green.
- `--auto-build` (Tasks 5–6): LLM invocation mirrors `slug.fetch_slug`; narration start `→ running project build stage via <provider> (LLM-heavy)`, success `✓ auto-build passed`, failure `✗ auto-build failed (<reason>) — log: <path>`, skip `· no project build stage configured — skipping auto-build`; timeout constant `_BUILD_TIMEOUT = 1800`; log prefix `omc-auto-build-`; runs AFTER the post-watch hook, action ticks only; failures never change exit codes.

---

### Task 1: `_post_watch_hook` happy path + trigger gating (unit, red→green)

**Files:**
- Modify: `src/omc/watch.py`
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `_tick` outcome tokens (`"synced"`, `"refreshed"`, quiet tokens), `run_watch` loop, `ToolContext.run(argv, cwd=, timeout=, extra_env=)`, test helpers `_repo_with_origin`, `_push_remote_commit`, `_ctx_with_node_stub`, `_run_once`, `_run_loop` (all already in `tests/unit/test_watch.py`).
- Produces: `_post_watch_hook(ctx: ToolContext, root: str, outcome: str) -> None` and module constant `_HOOK_TIMEOUT = 600` in `src/omc/watch.py`; test helper `_seed_hook(repo, body)` in `tests/unit/test_watch.py`. Task 2 monkeypatches `_HOOK_TIMEOUT`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_watch.py` (the fake ctx uses REAL bash/git, so hooks genuinely execute):

```python
def _seed_hook(repo, body):
    hooks = repo / ".omc" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "post-watch.sh").write_text(body)


def test_once_runs_post_watch_hook_after_forced_refresh(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, 'echo "$OMC_WATCH_OUTCOME" > hook-ran.txt\n')
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "→ running project post-watch hook (.omc/hooks/post-watch.sh)" in err
    assert "✓ post-watch hook done" in err
    # cwd was the repo root and OMC_WATCH_OUTCOME carried the token
    assert (repo / "hook-ran.txt").read_text().strip() == "refreshed"


def test_hook_sees_synced_outcome(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    _seed_hook(repo, 'echo "$OMC_WATCH_OUTCOME" > hook-ran.txt\n')
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    assert (repo / "hook-ran.txt").read_text().strip() == "synced"


def test_quiet_loop_tick_does_not_run_hook(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "touch hook-ran.txt\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_loop(repo, ctx, ticks=2) == 0  # loop mode: both ticks are quiet up-to-date
    assert not (repo / "hook-ran.txt").exists()
    assert "post-watch" not in capsys.readouterr().err


def test_absent_hook_is_silent(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    assert "post-watch" not in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -k "post_watch_hook or synced_outcome or does_not_run_hook or absent_hook" -v`
Expected: the two positive tests FAIL (no hook narration, no `hook-ran.txt`); the two negative tests may already pass — that's fine, they pin behavior.

- [ ] **Step 3: Implement**

In `src/omc/watch.py`, extend the imports:

```python
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
```

Add below `_out` (module level):

```python
_HOOK_TIMEOUT = 600  # seconds — a stuck project hook must not wedge the loop


def _write_hook_log(output: str) -> str:
    fd, path = tempfile.mkstemp(prefix="omc-post-watch-", suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(output)
    return path


def _post_watch_hook(ctx: ToolContext, root: str, outcome: str) -> None:
    """Project extension point: .omc/hooks/post-watch.sh, fired only after
    ACTION ticks (synced/refreshed). Hooks never break work — failures and
    timeouts warn (with the captured log) and the loop continues."""
    hook = Path(root) / ".omc" / "hooks" / "post-watch.sh"
    if not hook.is_file():
        return
    _say("→ running project post-watch hook (.omc/hooks/post-watch.sh)")
    status: str | None = None
    try:
        cp = ctx.run(
            ["bash", str(hook)],
            cwd=root,
            timeout=_HOOK_TIMEOUT,
            extra_env={"OMC_WATCH_OUTCOME": outcome},
        )
        output = (cp.stdout or "") + (cp.stderr or "")
        if cp.returncode != 0:
            status = f"exit {cp.returncode}"
    except subprocess.TimeoutExpired as exc:
        # POSIX quirk: TimeoutExpired carries the partial output as BYTES
        # even in text mode — decode before logging.
        def _txt(v: object) -> str:
            return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")  # type: ignore[union-attr]

        output = _txt(exc.stdout) + _txt(exc.stderr)
        status = "timeout"
    except OSError as exc:
        output = str(exc)
        status = "failed to start"
    log = _write_hook_log(output)
    if status is None:
        _say("✓ post-watch hook done")
    else:
        _say(f"✗ post-watch hook failed ({status}) — log: {log}")
```

In `run_watch`, wire the call after `_tick` (covers loop AND `--once`):

```python
            last = _tick(
                ctx,
                cfg,
                root,
                enable_documentation=enable_documentation,
                force_refresh=once,
                last=last,
            )
            if last in ("synced", "refreshed"):
                _post_watch_hook(ctx, root, last)
            if once:
                return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch.py -v`
Expected: ALL tests pass (the 16 pre-existing ones prove nothing regressed).

- [ ] **Step 5: Commit**

```bash
git add src/omc/watch.py tests/unit/test_watch.py
git commit -m "feat: omc watch runs project post-watch hook after action ticks (red->green)"
```

---

### Task 2: failure, log-linking, and timeout semantics (unit, red→green)

**Files:**
- Modify: `src/omc/watch.py` (only if Step 3 shows gaps — Task 1's implementation is intended to already satisfy this)
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `_post_watch_hook`, `_HOOK_TIMEOUT` from Task 1; helpers `_seed_hook`, `_run_once`, `_run_loop`, `_repo_with_origin`, `_push_remote_commit`, `_ctx_with_node_stub`.
- Produces: pinned failure contract (narration string with log path; `--once` rc 0; loop survival) that the E2E task re-verifies live.

- [ ] **Step 1: Write the failing tests**

Add `import re` and `from pathlib import Path` to the imports of `tests/unit/test_watch.py`, then append:

```python
def test_hook_failure_links_log_and_keeps_once_rc_zero(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "echo boom-out\necho boom-err >&2\nexit 3\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0  # hook failure never changes --once's exit code
    err = capsys.readouterr().err
    m = re.search(r"✗ post-watch hook failed \(exit 3\) — log: (\S+)", err)
    assert m, f"missing failure narration:\n{err}"
    log = Path(m.group(1))
    assert log.is_file()
    content = log.read_text()
    assert "boom-out" in content and "boom-err" in content  # both streams captured


def test_hook_timeout_is_a_failure_and_loop_survives(tmp_path, capsys, monkeypatch):
    import omc.watch as watch_mod

    monkeypatch.setattr(watch_mod, "_HOOK_TIMEOUT", 1)
    origin, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "sleep 5\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")

    def between(i):
        if i == 1:  # teammate pushes between tick 1 and 2 -> tick 2 syncs -> hook fires
            _push_remote_commit(origin, tmp_path)

    assert _run_loop(repo, ctx, ticks=3, between=between) == 0
    err = capsys.readouterr().err
    assert "✗ post-watch hook failed (timeout)" in err
    # tick 3 still ran after the hook blew up: quiet line reappears post-sync
    assert err.count("up to date") == 2, f"loop did not survive the timeout:\n{err}"
```

- [ ] **Step 2: Run them to verify current state**

Run: `uv run pytest tests/unit/test_watch.py -k "links_log or timeout_is_a_failure" -v`
Expected: PASS if Task 1's implementation is complete (these tests pin the contract); any FAIL identifies a real gap — fix `_post_watch_hook` minimally until green. Either way the tests land.

- [ ] **Step 3: Full suite**

Run: `uv run pytest tests/unit -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_watch.py src/omc/watch.py
git commit -m "test: pin post-watch hook failure/timeout contract (log link, loop survival)"
```

---

### Task 3: E2E — real `omc watch --once` with a real hook (Docker)

**Files:**
- Test: `tests/e2e/test_e2e_watch.py`

**Interfaces:**
- Consumes: `container` fixture (`tests/e2e/conftest.py`), `configure_omc`, `make_work_repo`, `run_in` (`tests/e2e/harness.py`); the shipped implementation from Tasks 1–2 (the Docker image builds omc from the repo).
- Produces: live proof of the full contract (narration, cwd, env var, log file on failure).

- [ ] **Step 1: Write the two E2E tests**

Append to `tests/e2e/test_e2e_watch.py` (add `import re` to its imports):

```python
def _seed_container_hook(container, repo, body):
    script = f"mkdir -p {repo}/.omc/hooks && cat > {repo}/.omc/hooks/post-watch.sh <<'EOF'\n{body}\nEOF"
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_watch_once_runs_project_post_watch_hook_for_real(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_container_hook(container, repo, 'echo "$OMC_WATCH_OUTCOME" > marker.txt')

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "running project post-watch hook (.omc/hooks/post-watch.sh)" in out, out
    assert "post-watch hook done" in out, out
    # hook really ran, in the repo root, with the outcome env var
    rc, marker = run_in(container, ["cat", f"{repo}/marker.txt"])
    assert rc == 0 and marker.strip() == "refreshed", marker


def test_watch_once_failing_hook_links_log_and_exits_zero(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_container_hook(container, repo, "echo boom-e2e >&2\nexit 1")

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out  # hook failure never breaks --once
    m = re.search(r"post-watch hook failed \(exit 1\) — log: (\S+)", out)
    assert m, f"missing failure narration:\n{out}"
    rc, log = run_in(container, ["cat", m.group(1)])
    assert rc == 0 and "boom-e2e" in log, log
```

- [ ] **Step 2: Run the two new E2E tests**

Run: `uv run pytest tests/e2e/test_e2e_watch.py -k "post_watch" -v` (Docker required; image build on first run is slow — allow ~10 min)
Expected: both PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_e2e_watch.py
git commit -m "test: e2e-verify project post-watch hook via real omc watch --once"
```

---

### Task 5: `--auto-build` — build stage via default LLM after action ticks (unit, red→green)

**Files:**
- Modify: `src/omc/watch.py`, `src/omc/cli.py:45-51,111-122`, `src/omc/skills_source.py`, `src/omc/slug.py:72-75`
- Test: `tests/unit/test_watch.py`

**Interfaces:**
- Consumes: `_post_watch_hook` call site and `_write_hook_log` from Task 1; `skill_text` (`src/omc/skills_source.py`); `get_provider` (`src/omc/providers/registry.py`); `Provider.headless_argv`; `slug.build_prompt`'s frontmatter regex `_FRONTMATTER_RE` (`src/omc/slug.py`); test helpers `_repo_with_origin`, `_ctx_with_node_stub`, `_run_once`, `_seed_hook`.
- Produces: `skill_prompt(name: str) -> str` in `skills_source.py`; `_auto_build(ctx: ToolContext, cfg: Config, root: str) -> None`, `_BUILD_TIMEOUT = 1800`, `_parse_stage(output: str) -> dict | None` in `watch.py`; `run_watch(..., auto_build: bool = False)`; `--auto-build` CLI flag; `_write_hook_log(output, prefix)` (generalized). Task 6 (E2E) and Task 4 (docs) rely on all of these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_watch.py` (a PATH stub `claude` binary records its argv and prints a canned transcript — same pattern as the `node` stub):

```python
def _stub_claude(tmp_path, transcript, rc=0):
    """A fake `claude` on the stub PATH: records argv, prints transcript."""
    bindir = tmp_path / "bin"  # same dir _ctx_with_node_stub already put on PATH
    calls = bindir / "claude.calls"
    stub = bindir / "claude"
    stub.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\ncat <<\'TRANSCRIPT\'\n{transcript}\nTRANSCRIPT\nexit {rc}\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return calls


def _seed_build_stage(repo):
    d = repo / ".omc" / "skills" / "build"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# build\nrun make\n")


def _run_once_auto_build(repo, ctx):
    old = os.getcwd()
    os.chdir(repo)
    try:
        return run_watch(ctx, Config(), interval=1, once=True, auto_build=True)
    finally:
        os.chdir(old)


def test_auto_build_passes_on_stage_verdict(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    calls = _stub_claude(
        tmp_path, 'building...\nOMC_STAGE {"stage": "build", "configured": true, "passed": true, "summary": "ok"}'
    )
    assert _run_once_auto_build(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "→ running project build stage via claude (LLM-heavy)" in err
    assert "✓ auto-build passed" in err
    recorded = calls.read_text()
    assert "-p" in recorded  # headless print-mode invocation


def test_auto_build_failure_links_log_and_keeps_rc_zero(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _stub_claude(
        tmp_path, 'OMC_STAGE {"stage": "build", "configured": true, "passed": false, "summary": "make exploded"}'
    )
    assert _run_once_auto_build(repo, ctx) == 0  # failures never change --once's exit code
    err = capsys.readouterr().err
    m = re.search(r"✗ auto-build failed \(make exploded\) — log: (\S+)", err)
    assert m, f"missing failure narration:\n{err}"
    assert "OMC_STAGE" in Path(m.group(1)).read_text()  # full transcript logged


def test_auto_build_no_verdict_is_a_failure(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _stub_claude(tmp_path, "rambling with no verdict line")
    assert _run_once_auto_build(repo, ctx) == 0
    assert "✗ auto-build failed (no verdict)" in capsys.readouterr().err


def test_auto_build_unconfigured_skips_llm_entirely(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)  # no .omc/skills/build
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    calls = _stub_claude(tmp_path, "should never run")
    assert _run_once_auto_build(repo, ctx) == 0
    assert "· no project build stage configured — skipping auto-build" in capsys.readouterr().err
    assert not calls.exists()  # the provider binary was NEVER invoked


def test_no_auto_build_flag_means_no_build(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    calls = _stub_claude(tmp_path, "should never run")
    assert _run_once(repo, ctx) == 0  # plain --once, no auto_build
    assert "auto-build" not in capsys.readouterr().err
    assert not calls.exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_watch.py -k auto_build -v`
Expected: FAIL — `run_watch() got an unexpected keyword argument 'auto_build'`.

- [ ] **Step 3: Implement**

3a. `src/omc/skills_source.py` — add below `skill_text`:

```python
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def skill_prompt(name: str) -> str:
    """Skill body ready to inline into a headless prompt (frontmatter stripped)."""
    return _FRONTMATTER_RE.sub("", skill_text(name))
```

(add `import re` to the module imports)

3b. `src/omc/slug.py` — refactor `build_prompt` onto it (delete slug's own `_FRONTMATTER_RE`):

```python
from .skills_source import skill_prompt


def build_prompt(context: str) -> str:
    return skill_prompt("slug").replace("$ARGUMENTS", context)
```

3c. `src/omc/watch.py` — add imports `import json`, `import re`, `from .providers.registry import get_provider`, `from .skills_source import skill_prompt`. Generalize the log writer (update `_post_watch_hook`'s call to pass `"omc-post-watch-"`):

```python
def _write_hook_log(output: str, prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(output)
    return path
```

Add the auto-build machinery below `_post_watch_hook`:

```python
_BUILD_TIMEOUT = 1800  # seconds — an LLM build stage must not wedge the loop
_STAGE_RE = re.compile(r"^OMC_STAGE (\{.*\})\s*$", re.MULTILINE)


def _parse_stage(output: str) -> dict | None:
    m = None
    for m in _STAGE_RE.finditer(output):
        pass  # last verdict line wins
    if m is None:
        return None
    try:
        v = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def _auto_build(ctx: ToolContext, cfg: Config, root: str) -> None:
    """--auto-build: run the project's build stage via the default LLM after
    an action tick. Same doctrine as the hook: failures warn, never crash.
    The SKILL.md existence pre-check deliberately mirrors the build skill's
    own step 2 — a cost guard so an unconfigured project never spends an
    LLM call per tick to learn 'nothing to do'."""
    if not (Path(root) / ".omc" / "skills" / "build" / "SKILL.md").is_file():
        _say("· no project build stage configured — skipping auto-build")
        return
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    _say(f"→ running project build stage via {name} (LLM-heavy)")
    status: str | None = None
    try:
        cp = ctx.run(
            provider.headless_argv(
                skill_prompt("build"),
                model=model,
                allowed_tools=["Bash", "Read", "Glob", "Grep"],
            ),
            cwd=root,
            timeout=_BUILD_TIMEOUT,
            extra_env=provider.title_env(),
        )
        output = (cp.stdout or "") + (cp.stderr or "")
        verdict = _parse_stage(output)
        if cp.returncode != 0:
            status = f"exit {cp.returncode}"
        elif verdict is None:
            status = "no verdict"
        elif not verdict.get("passed"):
            status = str(verdict.get("summary") or "stage failed")
    except subprocess.TimeoutExpired as exc:
        def _txt(v: object) -> str:
            return v.decode(errors="replace") if isinstance(v, bytes) else (v or "")  # type: ignore[union-attr]

        output = _txt(exc.stdout) + _txt(exc.stderr)
        status = "timeout"
    except OSError as exc:
        output = str(exc)
        status = "failed to start"
    log = _write_hook_log(output, "omc-auto-build-")
    if status is None:
        _say("✓ auto-build passed")
    else:
        _say(f"✗ auto-build failed ({status}) — log: {log}")
```

Thread the flag through `run_watch` (signature + call site):

```python
def run_watch(
    ctx: ToolContext,
    cfg: Config,
    *,
    interval: int = 30,
    once: bool = False,
    enable_documentation: bool = False,
    auto_build: bool = False,
) -> int:
```

```python
            if last in ("synced", "refreshed"):
                _post_watch_hook(ctx, root, last)
                if auto_build:
                    _auto_build(ctx, cfg, root)
```

3d. `src/omc/cli.py` — after the `--enable-documentation` argument (~line 51):

```python
    p_watch.add_argument(
        "--auto-build",
        action="store_true",
        help="After each action tick, run the project's build stage via the default LLM",
    )
```

and in the watch dispatch (~line 117), pass `auto_build=args.auto_build`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit -q`
Expected: all pass (new auto-build tests + all prior tests, including slug tests which cover the `build_prompt` refactor).

- [ ] **Step 5: Commit**

```bash
git add src/omc/watch.py src/omc/cli.py src/omc/skills_source.py src/omc/slug.py tests/unit/test_watch.py
git commit -m "feat: omc watch --auto-build runs the project build stage via the default LLM (red->green)"
```

---

### Task 6: `--auto-build` E2E — token-free (Docker)

**Files:**
- Test: `tests/e2e/test_e2e_watch.py`

**Interfaces:**
- Consumes: `container` fixture, `configure_omc`, `make_work_repo`, `run_in`; `_seed_container_hook` from Task 3; the shipped Task 5 implementation.
- Produces: live proof of the skip path and the full flag path (via a PATH-shim `claude`), without spending LLM tokens.

- [ ] **Step 1: Write the two E2E tests**

Append to `tests/e2e/test_e2e_watch.py`:

```python
def test_watch_auto_build_skips_when_unconfigured(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once", "--auto-build"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "no project build stage configured — skipping auto-build" in out, out


def test_watch_auto_build_runs_stage_via_shim_provider(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    # seed a project build stage + a claude shim that answers with a passing verdict
    seed = (
        f"mkdir -p {repo}/.omc/skills/build /shim && "
        f"printf '# build\\nrun true\\n' > {repo}/.omc/skills/build/SKILL.md && "
        "printf '#!/bin/sh\\necho \"OMC_STAGE {\\\"stage\\\": \\\"build\\\", \\\"configured\\\": true, \\\"passed\\\": true, \\\"summary\\\": \\\"ok\\\"}\"\\n' > /shim/claude && "
        "chmod +x /shim/claude"
    )
    rc, out = run_in(container, ["bash", "-c", seed])
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["env", "PATH=/shim:/usr/local/bin:/usr/bin:/bin", "omc", "watch", "--once", "--auto-build"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, out
    assert "running project build stage via claude" in out, out
    assert "auto-build passed" in out, out
```

- [ ] **Step 2: Run the two new E2E tests**

Run: `uv run pytest tests/e2e/test_e2e_watch.py -k auto_build -v` (Docker required)
Expected: both PASS. NOTE: the shim test's PATH override must still let `omc`, `git`, `bash`, and `node` resolve — if the container installs omc elsewhere than /usr/local/bin, discover the right prefix with `run_in(container, ["bash", "-c", "command -v omc git node"])` and adjust the PATH string; keeping /shim first is the only hard requirement.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_e2e_watch.py
git commit -m "test: e2e-verify omc watch --auto-build (skip path + shim provider path)"
```

---

### Task 4: docs — README + integrate surface inventory

**Files:**
- Modify: `README.md` (watch prose ~line 68, command table row ~line 106)
- Modify: `skills/integrate/SKILL.md` (Phase 1 inventory list; Phase 2 per-surface sections)

**Interfaces:**
- Consumes: the shipped contract from Tasks 1–2 (paths, narration, env var, failure semantics).
- Produces: user-facing documentation only; no code.

- [ ] **Step 1: README prose** — in the long `omc watch` paragraph (~line 68), append after the sentence ending "…so it stays current.":

```
If the project commits a script at `.omc/hooks/post-watch.sh`, watch runs it (via `bash`, from the repo root) after every cycle that did real work — a sync or a forced `--once` refresh, `$OMC_WATCH_OUTCOME` says which (`synced`/`refreshed`) — and a failing or hung hook never stops the loop: watch warns and links the captured output log. `--auto-build` goes one step further: after the hook, watch runs the project's build stage (`.omc/skills/build`) via the default LLM (LLM-heavy, hence the flag; skipped instantly when no build stage exists), again linking the transcript log on failure.
```

- [ ] **Step 2: README command table** — change the `omc watch` row (~line 106) to:

```
| `omc watch` | Keep the main checkout's base branch + knowledge graph fresh (`--once`, `--interval`, `--enable-documentation`, `--auto-build`); runs the project's `.omc/hooks/post-watch.sh` (and with `--auto-build` its build stage) after action ticks |
```

- [ ] **Step 3: integrate inventory** — in `skills/integrate/SKILL.md` Phase 1 inventory list, after the `.omc/skills/…` bullet, add:

```
   - `.omc/hooks/post-watch.sh` — optional CLI-side hook `omc watch` runs
     after action ticks (sync / forced refresh)
```

- [ ] **Step 4: integrate Phase 2 section** — after the `### .omc/skills/explain-context` section, add:

```
### `.omc/hooks/post-watch.sh` (optional)
The CLI-side sibling of the session skills: `omc watch` runs it after every
cycle that did real work (env: `OMC_WATCH_OUTCOME`=`synced`|`refreshed`;
failures warn and link a log, never stop the loop). Investigate whether the
project has post-refresh work — regenerating downstream artifacts, cache
warming, notifying a dashboard. Propose it only when a real use exists;
absence is the correct default.
```

- [ ] **Step 5: Commit**

```bash
git add README.md skills/integrate/SKILL.md
git commit -m "docs: document project post-watch hook (README + integrate inventory)"
```
