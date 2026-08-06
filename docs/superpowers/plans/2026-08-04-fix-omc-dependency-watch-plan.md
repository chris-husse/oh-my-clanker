# Fix omc dependency watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `omc dependency watch` from retrying a corrupt dependency store forever: name signal deaths in failure messages, auto-heal a broken `.gitnexus` once (wipe + reindex via the existing ensure path), park the entry as `broken` on repeat, and stop leaking per-attempt temp logs.

**Architecture:** A shared child-failure describer lands in `src/omc/gitnexus.py` and is adopted by every gitnexus-child failure message. `run_document` gains a signal-death branch that heals once / parks on repeat, driven by two new optional manifest fields (`heals: int`, `broken: str`) whose lifecycle is: document success clears both; the automatic reindex preserves `heals` (that makes the one-heal cap bind); a manual cached-hit ensure of a parked entry clears both (re-arm). The watch loop only learns to *skip* broken entries and report them honestly — all mutations stay in worker verbs per doctrine.

**Tech Stack:** Python 3.12, pytest, uv, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-04-fix-omc-dependency-watch-design.md`

## Global Constraints

- Watch doctrine: warn-and-skip, never crash the loop (`watch.py _chain_tick` doctrine, cited throughout `depwatch.py`).
- Workers report, callers render: `OMC_PROGRESS` / `OMC_DEPENDENCY` single-line JSON contracts are unchanged; errors are plain stderr lines + exit codes.
- All manifest writes go through `update_manifest`'s flock.
- Stream-detail caps stay at 400 chars per stream; dependency-side messages apply `_redact`.
- No schema version bump; new manifest fields are optional and read with `.get`.
- **Before the first test run in this worktree:** `uv sync --reinstall` (a copied `.venv` runs the primary checkout's code otherwise).
- Verification for every task: `uv run pytest tests/unit -q` must pass; final task also runs `uv run ruff check src tests` and `uv run ruff format --check src tests`.
- Model tiers below follow AGENTS.md "Model selection" (tier names, never model ids).

---

### Task 1: Child-failure describer in gitnexus.py

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/gitnexus.py` (add `import signal` up top; two new functions near `redact_userinfo`)
- Test: `tests/unit/test_gitnexus_store.py` (append)

**Interfaces:**
- Produces: `child_death(rc: int | None) -> str` — `"killed by SIGSEGV (signal 11)"` for `rc=-11`, `"killed by signal 99"` for unnamed negatives, `"exit 3"` otherwise.
- Produces: `describe_child_failure(cp, redact=lambda s: s) -> str` — `child_death` plus labeled, redacted, 400-char-capped streams: `"killed by SIGSEGV (signal 11) — stdout: GitNexus Wiki Generator"`; streams-empty → just the death; both streams → `"… — stderr: …; stdout: …"` (stderr first).
- Consumes: nothing new. `cp` is any object with `.returncode`, `.stdout`, `.stderr` (a `subprocess.CompletedProcess`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gitnexus_store.py`:

```python
from types import SimpleNamespace

from omc.gitnexus import child_death, describe_child_failure


def test_child_death_names_signals_and_exits():
    assert child_death(-11) == "killed by SIGSEGV (signal 11)"
    assert child_death(-9) == "killed by SIGKILL (signal 9)"
    assert child_death(3) == "exit 3"
    assert child_death(0) == "exit 0"
    assert child_death(-99) == "killed by signal 99"  # no such Signals member


def test_describe_failure_labels_both_streams_redacted():
    cp = SimpleNamespace(returncode=1, stdout="  banner\n", stderr="tok@host boom")
    desc = describe_child_failure(cp, redact=lambda s: s.replace("tok@", "[REDACTED]@"))
    assert desc.startswith("exit 1 — ")
    assert "stderr: [REDACTED]@host boom" in desc
    assert "stdout: banner" in desc
    assert desc.index("stderr:") < desc.index("stdout:")


def test_describe_failure_signal_death_banner_only():
    cp = SimpleNamespace(returncode=-11, stdout="\n  GitNexus Wiki Generator\n", stderr="")
    assert (
        describe_child_failure(cp)
        == "killed by SIGSEGV (signal 11) — stdout: GitNexus Wiki Generator"
    )


def test_describe_failure_silent_child_and_caps():
    assert describe_child_failure(SimpleNamespace(returncode=-9, stdout="", stderr=None)) == (
        "killed by SIGKILL (signal 9)"
    )
    long = describe_child_failure(SimpleNamespace(returncode=2, stdout="x" * 500, stderr=""))
    assert long == f"exit 2 — stdout: {'x' * 400}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_gitnexus_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'child_death'`

- [ ] **Step 3: Implement**

In `src/omc/gitnexus.py`, add `import signal` to the imports, then below `redact_userinfo`:

```python
def child_death(rc: int | None) -> str:
    """How a child ended: 'killed by SIGSEGV (signal 11)' or 'exit 3'."""
    if rc is not None and rc < 0:
        try:
            return f"killed by {signal.Signals(-rc).name} (signal {-rc})"
        except ValueError:
            return f"killed by signal {-rc}"
    return f"exit {rc}"


def describe_child_failure(cp, redact=lambda s: s) -> str:
    """Diagnosis of a failed gitnexus child: how it died + what it said.

    Signal deaths are named — a corrupt lbug store SIGSEGVs node before any
    error line, so the signal IS the evidence. Both streams are shown:
    `stderr or stdout` used to swallow whichever stream carried the real
    error whenever the other held banner noise.
    """
    streams = [
        (label, redact((text or "").strip())[:400])
        for label, text in (("stderr", cp.stderr), ("stdout", cp.stdout))
    ]
    detail = "; ".join(f"{label}: {text}" for label, text in streams if text)
    death = child_death(cp.returncode)
    return f"{death} — {detail}" if detail else death
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_gitnexus_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/omc/gitnexus.py tests/unit/test_gitnexus_store.py
git commit -m "Add child-failure describer: name signal deaths, show both streams"
```

---

### Task 2: Adopt the describer at dependency.py's analyze and wiki failure sites

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/dependency.py` (the `run_ensure` analyze-failure block around line 352, the `run_document` wiki-failure block around line 458, and the import from `.gitnexus`)
- Test: `tests/unit/test_dependency.py` (extend `test_document_wiki_nonzero_rc_keeps_documented_false`; extend the analyze-failure redaction test)

**Interfaces:**
- Consumes: `describe_child_failure(cp, redact)` from Task 1 (import alongside the existing `from .gitnexus import gitnexus_argv, …`).
- Produces: failure message formats `error: gitnexus analyze failed: <desc>` and `error: gitnexus wiki failed: <desc>` where `<desc>` is the describer's output. Task 3 keeps the wiki-failure gate exactly where this task leaves it.

- [ ] **Step 1: Extend the failing tests**

In `tests/unit/test_dependency.py`, replace `test_document_wiki_nonzero_rc_keeps_documented_false` with:

```python
def test_document_wiki_nonzero_rc_keeps_documented_false(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)  # wiki dir exists...
    # ...but the wiki step exits non-zero: the rc != 0 half of the gate.
    node = tmp_path / "bin" / "node"
    node.write_text(
        f'#!/bin/sh\necho "$@" >> "{nodecalls}"\npwd >> "{nodecalls}"\n'
        "echo banner\necho boom >&2\nexit 1\n"
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    assert run_document(ctx, "github.com/foo/bar") == 1
    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["documented"] is False
    err = capsys.readouterr().err
    # the describer names the exit and keeps BOTH streams
    assert "gitnexus wiki failed: exit 1" in err
    assert "stderr: boom" in err and "stdout: banner" in err
```

- [ ] **Step 2: Run to verify the new assertions fail**

Run: `uv run pytest tests/unit/test_dependency.py::test_document_wiki_nonzero_rc_keeps_documented_false -q`
Expected: FAIL on the `"gitnexus wiki failed: exit 1"` assertion (current message has no exit code)

- [ ] **Step 3: Adopt the describer**

In `src/omc/dependency.py`, extend the `.gitnexus` import with `describe_child_failure`, then:

Analyze site (in `run_ensure`, currently):
```python
    if cp.returncode != 0:
        detail = _redact((cp.stderr or cp.stdout or "").strip())[:400]
        print(f"error: gitnexus analyze failed: {detail}", file=sys.stderr)
        return 1
```
becomes:
```python
    if cp.returncode != 0:
        detail = describe_child_failure(cp, _redact)
        print(f"error: gitnexus analyze failed: {detail}", file=sys.stderr)
        return 1
```

Wiki site (in `run_document`, currently):
```python
    if cp.returncode != 0 or not wiki.is_dir():
        print(
            f"error: gitnexus wiki failed: {_redact((cp.stderr or cp.stdout or '').strip())[:400]}",
            file=sys.stderr,
        )
        return 1
```
becomes:
```python
    if cp.returncode != 0 or not wiki.is_dir():
        print(
            f"error: gitnexus wiki failed: {describe_child_failure(cp, _redact)}",
            file=sys.stderr,
        )
        return 1
```

- [ ] **Step 4: Run the dependency test file**

Run: `uv run pytest tests/unit/test_dependency.py -q`
Expected: PASS (the existing analyze-redaction test still passes — the describer applies `_redact` to each stream)

- [ ] **Step 5: Commit**

```bash
git add src/omc/dependency.py tests/unit/test_dependency.py
git commit -m "Name exit/signal and show both streams in dependency gitnexus failures"
```

---

### Task 3: Heal-then-park in run_document; counter lifecycle in run_ensure

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/dependency.py` (`run_document` signal-death branch + success cleanup; `run_ensure` cached-hit re-arm + `_record` broken-clear; new module-level `_heal_or_park`; import `child_death` from `.gitnexus`, `shutil` already imported)
- Test: `tests/unit/test_dependency.py`

**Interfaces:**
- Consumes: `child_death`, `describe_child_failure` (Task 1); existing `update_manifest`, `_redact`, `_verdict`.
- Produces: manifest per-commit fields `heals: int` and `broken: str` with this lifecycle (Tasks 4's watch behavior relies on `broken` exactly as written here):
  - document signal death with `heals` absent/0 → checkout's `.gitnexus` removed, entry `indexed=False, heals=1`, `broken` absent, exit 1.
  - document signal death with `heals` ≥ 1 → `broken="gitnexus wiki <death> after reindex — likely a GitNexus/lbug bug"`, no wipe, exit 1.
  - document success → `documented=True`, `heals`/`broken` removed.
  - `run_ensure` full-index `_record` → removes `broken`, PRESERVES `heals`.
  - `run_ensure` cached-hit on an entry with `broken` set → removes `broken` and `heals` (manual re-arm), then the normal cached verdict.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dependency.py`:

```python
def _segv_node(tmp_path, nodecalls):
    """node stub that logs its argv then dies by SIGSEGV — the corrupt-store
    signature (run_supervised reports it as returncode -11)."""
    node = tmp_path / "bin" / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{nodecalls}"\nkill -SEGV $$\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)


def _entry(ctx):
    return load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]


def _patch_entry(ctx, **fields):
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"]["commits"][H].update(fields)
    save_manifest(ctx.home, m)


def test_document_signal_death_heals_once(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    dest = _seed_indexed(ctx)
    _segv_node(tmp_path, nodecalls)
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    err = capsys.readouterr().err
    assert "SIGSEGV" in err and "wiped" in err
    entry = _entry(ctx)
    assert entry["indexed"] is False and entry["heals"] == 1
    assert "broken" not in entry
    assert not (dest / ".gitnexus").exists()  # derived cache gone
    assert (dest / ".git").exists()  # checkout untouched


def test_document_signal_death_after_heal_parks(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    dest = _seed_indexed(ctx)
    _patch_entry(ctx, heals=1)
    _segv_node(tmp_path, nodecalls)
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    err = capsys.readouterr().err
    assert "omc update" in err
    assert f"ensure --git https://github.com/foo/bar.git --commit {H}" in err
    entry = _entry(ctx)
    assert "SIGSEGV" in entry["broken"] and "after reindex" in entry["broken"]
    assert entry["indexed"] is True  # park never un-indexes
    assert (dest / ".gitnexus").exists()  # no second wipe


def test_document_success_clears_heal_state(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    _patch_entry(ctx, heals=1)
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    entry = _entry(ctx)
    assert entry["documented"] is True
    assert "heals" not in entry and "broken" not in entry


def test_ensure_cached_hit_rearms_parked_entry(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    _patch_entry(ctx, heals=1, broken="gitnexus wiki killed by SIGSEGV after reindex")
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 0
    assert _verdict(capsys)["cached"] is True
    entry = _entry(ctx)
    assert "broken" not in entry and "heals" not in entry  # manual re-arm


def test_ensure_reindex_preserves_heals_clears_broken(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    _patch_entry(ctx, indexed=False, heals=1, broken="stale")
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 0
    entry = _entry(ctx)
    assert entry["indexed"] is True
    assert entry["heals"] == 1  # preserved: the park cap must bind next time
    assert "broken" not in entry
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_dependency.py -q -k "signal_death or clears_heal or rearms or preserves_heals"`
Expected: FAIL — `KeyError: 'heals'` / assertion errors (no heal logic yet)

- [ ] **Step 3: Implement the lifecycle**

In `src/omc/dependency.py`:

(a) New module-level function (below `run_document`'s helpers, above `run_document`):

```python
def _heal_or_park(ctx: ToolContext, key: str, commit: str, entry: dict, cp) -> int:
    """Signal death opening the dependency store — the corrupt-store
    signature. Heal once: wipe the derived .gitnexus and flip the entry
    un-indexed so the watch's ensure rebuilds it. A second signal death
    after that rebuild means reindexing does not fix this store: park the
    entry as broken so the watch stops retrying. Manifest first, then the
    wipe: a failed wipe leaves indexed:false + a corrupt store, which the
    rebuilt analyze overwrites anyway."""
    desc = describe_child_failure(cp, _redact)
    death = child_death(cp.returncode)
    outcome: dict[str, str] = {}

    def _mutate(m: dict) -> None:
        c = m["dependencies"][key]["commits"][commit]
        if int(c.get("heals") or 0) < 1:
            c.update(indexed=False, heals=int(c.get("heals") or 0) + 1)
            c.pop("broken", None)
            outcome["action"] = "healed"
        else:
            c["broken"] = f"gitnexus wiki {death} after reindex — likely a GitNexus/lbug bug"
            outcome["action"] = "parked"

    update_manifest(ctx.home, _mutate)
    if outcome["action"] == "healed":
        shutil.rmtree(Path(entry["checkout"]) / ".gitnexus", ignore_errors=True)
        print(
            f"error: gitnexus wiki failed ({desc}) — store looks corrupt; "
            "wiped .gitnexus and flagged for reindex (the watch will retry)",
            file=sys.stderr,
        )
    else:
        print(
            f"error: gitnexus wiki failed ({desc}) after a reindex — likely a "
            f"GitNexus/lbug bug; parking {_redact(key)}@{commit[:7]}. After "
            "upgrading GitNexus (`omc update`), retry with "
            f"`omc internal dependency ensure --git {target} --commit {commit}`",
            file=sys.stderr,
        )
    return 1
```

(b) In `run_document`, between the `stalled` branch and the wiki-failure gate:

```python
    if stalled:
        ...existing...
        return 1
    if cp.returncode is not None and cp.returncode < 0:
        return _heal_or_park(ctx, key, commit, entry, cp)
    if cp.returncode != 0 or not wiki.is_dir():
        ...Task 2's message...
```

(c) `run_document` success path — replace
`update_manifest(ctx.home, lambda m: m["dependencies"][key]["commits"][commit].update(documented=True))` with:

```python
    def _complete(m: dict) -> None:
        c = m["dependencies"][key]["commits"][commit]
        c.update(documented=True)
        c.pop("heals", None)
        c.pop("broken", None)

    update_manifest(ctx.home, _complete)
```

(d) `run_ensure` cached-hit path — before its `_verdict(...)`:

```python
    if entry and entry.get("indexed") and (dest / ".git").exists():
        if entry.get("broken"):
            # Manual re-arm: an explicit ensure of a parked entry grants one
            # fresh heal cycle. The watch never ensures parked entries.
            def _rearm(m: dict) -> None:
                c = m["dependencies"][ref.key]["commits"][commit]
                c.pop("broken", None)
                c.pop("heals", None)

            update_manifest(ctx.home, _rearm)
        _verdict(...)  # unchanged
```

(e) `run_ensure._record` — inside `_record`, after the `c.update({...})`, add:

```python
        c.pop("broken", None)  # a fresh index invalidates the park; heals stays
```

(f) Extend the `.gitnexus` import with `child_death`.

- [ ] **Step 4: Run the dependency tests**

Run: `uv run pytest tests/unit/test_dependency.py -q`
Expected: PASS, including all pre-existing tests

- [ ] **Step 5: Commit**

```bash
git add src/omc/dependency.py tests/unit/test_dependency.py
git commit -m "Heal a corrupt dependency store once, park it on repeat"
```

---

### Task 4: Park semantics in the watch loop and status surfaces

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/depwatch.py` (`_tick` skip, `_manifest_status` 4-tuple, `_pass` announcements, `run_dependency_watch` idle line, `run_dependency_list` DOCUMENTED cell)
- Test: `tests/unit/test_depwatch.py`

**Interfaces:**
- Consumes: manifest field `broken: str` exactly as Task 3 writes it (truthy string on parked entries).
- Produces: `_manifest_status(home) -> tuple[int, int, int, int]` — `(dependencies, commits, remaining, broken)`; remaining excludes broken entries.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_depwatch.py` (extend `_seed_manifest` with a `broken` kwarg first):

```python
def _seed_broken(home, key="github.com/foo/bar"):
    from omc.dependency import load_manifest, save_manifest

    m = load_manifest(home)
    m["dependencies"][key] = {
        "url": f"https://{key}.git",
        "commits": {
            H: {
                "checkout": str(home / "dependencies" / Path(key) / H),
                "indexed": True,
                "documented": False,
                "broken": "gitnexus wiki killed by SIGSEGV (signal 11) after reindex",
                "created": "2026-08-04T00:00:00+00:00",
            }
        },
    }
    save_manifest(home, m)


def test_tick_never_touches_broken_entries(tmp_path, capsys):
    ctx, calls = _ctx(tmp_path)
    _seed_broken(ctx.home)
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()  # neither ensure nor document spawned
    err = capsys.readouterr().err
    assert "1 broken item(s) parked (see omc dependency list)" in err


def test_tick_skips_broken_even_when_unindexed(tmp_path, capsys):
    # broken wins over indexed:false — a parked entry is never re-ensured.
    from omc.dependency import load_manifest, save_manifest

    ctx, calls = _ctx(tmp_path)
    _seed_broken(ctx.home)
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"]["commits"][H]["indexed"] = False
    save_manifest(ctx.home, m)
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()


def test_pass_reports_pending_and_broken_separately(tmp_path, capsys):
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True, documented=False)  # pending, stub no-op
    _seed_broken(ctx.home, key="github.com/baz/qux")
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    assert "1 item(s) still pending" in err
    assert "1 broken (see omc dependency list)" in err
    assert "Finished documenting" not in err


def test_finished_never_claimed_with_broken_parked(tmp_path, capsys):
    ctx, calls = _stateful_ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=False, documented=False)  # stub flips to done
    _seed_broken(ctx.home, key="github.com/baz/qux")
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    assert "Finished documenting" not in err
    assert "1 item(s) broken (see omc dependency list); not retrying" in err


def test_dependency_list_marks_broken(tmp_path, capsys):
    from omc.depwatch import run_dependency_list

    ctx, _ = _ctx(tmp_path)
    _seed_broken(ctx.home)
    assert run_dependency_list(ctx.home) == 0
    assert "broken" in capsys.readouterr().out
```

Add `from pathlib import Path` to the test file's imports if missing.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_depwatch.py -q -k "broken"`
Expected: FAIL — document/ensure calls ARE spawned for broken entries; announcements missing

- [ ] **Step 3: Implement**

In `src/omc/depwatch.py`:

(a) `_tick`, inside the commits loop — first line:

```python
    for key, dep in sorted(deps.items()):
        for commit, entry in dep.get("commits", {}).items():
            if entry.get("broken"):
                continue  # parked: a human re-ensure re-arms it, never the loop
            if not entry.get("indexed"):
                ...
```

(b) `_manifest_status` returns a 4-tuple; broken entries leave "remaining":

```python
def _manifest_status(home: Path) -> tuple[int, int, int, int]:
    """(dependencies, commits, remaining, broken). Remaining counts manifest
    commit entries not yet indexed AND documented — excluding parked ones,
    which are reported separately — plus disk checkouts the manifest doesn't
    know (a failed adoption must not read as completion)."""
    try:
        deps = load_manifest(home).get("dependencies", {})
    except OmcError:
        return 0, 0, 0, 0
    commits = [e for dep in deps.values() for e in dep.get("commits", {}).values()]
    broken = sum(1 for e in commits if e.get("broken"))
    remaining = sum(
        1
        for e in commits
        if not e.get("broken") and not (e.get("indexed") and e.get("documented"))
    )
    known = {e.get("checkout") for e in commits}
    remaining += sum(1 for checkout in _scan_disk(home) if str(checkout) not in known)
    return len(deps), len(commits), remaining, broken
```

(c) `_pass` announcement block:

```python
    if total:
        ndeps, ncommits, remaining, broken = _manifest_status(ctx.home)
        if remaining == 0 and broken == 0:
            dep_word = "dependency" if ndeps == 1 else "dependencies"
            commit_word = "commit" if ncommits == 1 else "commits"
            _say(
                "✓ Finished documenting all dependencies! "
                f"({ndeps} {dep_word}, {ncommits} {commit_word})"
            )
        elif remaining == 0:
            _say(
                f"· pass complete — {broken} item(s) broken "
                "(see omc dependency list); not retrying"
            )
        else:
            retry = "re-run to retry" if once else "retrying next tick"
            note = f", {broken} broken (see omc dependency list)" if broken else ""
            _say(
                f"· pass complete — {remaining} item(s) still pending "
                f"(see ✗ lines above){note}; {retry}"
            )
```

(d) `run_dependency_watch` idle transition:

```python
            if actions == 0:
                if not last_idle:
                    _, _, _, broken = _manifest_status(ctx.home)
                    if broken:
                        _say(
                            f"· idle — {broken} broken item(s) parked "
                            "(see omc dependency list); waiting for work"
                        )
                    else:
                        _say("· all dependencies reconciled — waiting for work")
                last_idle = True
```

(e) `run_dependency_list` DOCUMENTED cell:

```python
            "broken" if entry.get("broken") else ("✓" if entry.get("documented") else "✗"),
```

- [ ] **Step 4: Run the depwatch tests**

Run: `uv run pytest tests/unit/test_depwatch.py -q`
Expected: PASS, including all pre-existing announcement tests (wording without broken entries is unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/omc/depwatch.py tests/unit/test_depwatch.py
git commit -m "Watch skips parked entries and reports broken counts honestly"
```

---

### Task 5: Temp-log hygiene — delete on success

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/depwatch.py` (`_DocumentJob.final_line`, `_document_batch._document`)
- Test: `tests/unit/test_depwatch.py` (rework `test_document_job_logs_output_and_parses_progress`; extend `test_document_job_failure_names_exit_and_log`)

**Interfaces:**
- Consumes: nothing new.
- Produces: success `✓ done <ref>` (no log path, file deleted); failure keeps `✗ failed (exit N) <ref> — log: <path>` with the file intact.

- [ ] **Step 1: Rework the tests**

Replace `test_document_job_logs_output_and_parses_progress` with (success case — log gone) and extend the failure test (teeing assertions move here, the log must persist):

```python
def test_document_job_success_deletes_log(tmp_path, capsys, monkeypatch):
    import tempfile

    logdir = tmp_path / "joblogs"
    logdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(logdir))
    ctx, calls = _ctx(tmp_path)
    omc = tmp_path / "bin" / "omc"
    omc.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\n'
        "echo 'OMC_PROGRESS {\"percent\": 42}'\n"
        "echo 'OMC_PROGRESS not-json'\n"  # malformed: must be ignored, not crash
        "exit 0\n"
    )
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    done_line = next(ln for ln in err.splitlines() if ln.startswith("✓ done"))
    assert "log:" not in done_line
    assert list(logdir.iterdir()) == []  # the successful job's log was removed
    assert "\x1b[" not in err  # non-TTY: no ANSI bar bytes


def test_document_job_failure_names_exit_and_log(tmp_path, capsys, monkeypatch):
    import tempfile

    logdir = tmp_path / "joblogs"
    logdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(logdir))
    ctx, calls = _ctx(tmp_path)
    omc = tmp_path / "bin" / "omc"
    omc.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\n'
        "echo 'OMC_PROGRESS {\"percent\": 42}'\necho NARRATION >&2\nexit 3\n"
    )
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    assert "✗ failed (exit 3) github.com/foo/bar@" in err
    assert "log: " in err
    log_path = err.split("log: ", 1)[1].split()[0].rstrip(")")
    logged = open(log_path).read()  # teed output survives for diagnosis
    assert 'OMC_PROGRESS {"percent": 42}' in logged and "NARRATION" in logged
```

- [ ] **Step 2: Run to verify the success-case test fails**

Run: `uv run pytest tests/unit/test_depwatch.py -q -k "document_job"`
Expected: FAIL — ✓ line still carries `log:` and the file persists

- [ ] **Step 3: Implement**

In `src/omc/depwatch.py`:

`_DocumentJob.final_line`:
```python
    def final_line(self) -> str:
        if self.rc == 0:
            return f"✓ done {self.ref}"
        return f"✗ failed (exit {self.rc}) {self.ref} — log: {self.log_path}"
```

`_document_batch._document`, in the `finally` block after `job.log.close()`:
```python
        finally:
            try:
                job.log.close()
            except OSError:
                pass
            if job.rc == 0:
                # success: the log carried live progress only — drop it. A
                # failure's log is the diagnosis artifact and is kept.
                with contextlib.suppress(OSError):
                    os.unlink(job.log_path)
```

- [ ] **Step 4: Run the depwatch tests**

Run: `uv run pytest tests/unit/test_depwatch.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/omc/depwatch.py tests/unit/test_depwatch.py
git commit -m "Delete a document job's temp log on success, keep it on failure"
```

---

### Task 6: Adopt the describer at watch.py's four project-store sites; full verification

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/watch.py:248,252,272,294` (message-only; import `describe_child_failure` from `.gitnexus`)
- Test: none new — no existing test pins these message strings; the describer is unit-tested in Task 1. Full suite + lint close the task.

**Interfaces:**
- Consumes: `describe_child_failure(cp)` (no redactor — same as the current messages, which don't redact project-store output).
- Produces: nothing consumed later.

- [ ] **Step 1: Replace the four format expressions**

Each `{(cp.stderr or cp.stdout or '').strip()[:400]}` becomes `{describe_child_failure(cp)}`:

```python
        _say(f"✗ clean did not remove the index: {describe_child_failure(cp)}")
        _say(f"✗ full analyze failed: {describe_child_failure(cp)}")
            _say(f"✗ analyze failed: {describe_child_failure(cp)}")
        _say(f"✗ wiki failed: {describe_child_failure(cp)}")
```

(at their respective sites; add the import alongside watch.py's existing `.gitnexus` imports)

- [ ] **Step 2: Run the watch tests**

Run: `uv run pytest tests/unit/test_watch.py -q`
Expected: PASS (no test pins the old wording)

- [ ] **Step 3: Full verification**

Run: `uv run pytest tests/unit -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass / clean

- [ ] **Step 4: Commit**

```bash
git add src/omc/watch.py
git commit -m "Adopt the child-failure describer at watch.py's gitnexus sites"
```
