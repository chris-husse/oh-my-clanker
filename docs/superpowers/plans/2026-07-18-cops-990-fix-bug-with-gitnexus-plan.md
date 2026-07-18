# COPS-990 gitnexus --repo fix + install guardrail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `omc internal gitnexus` scope `--repo` by the primary root's *path* (not directory basename) so GitNexus's resolver always finds the repo, and add an AGENTS.md rule forbidding agents from running `omc install` on their own initiative.

**Architecture:** One-token change in the gitnexus proxy (`src/omc/internal.py:_gitnexus`) — GitNexus's `resolveRepo` matches by name OR canonicalized path, and the path is collision-free while remote-URL-derived registry names need not match the checkout's basename. The guardrail is prose in `.omc/config/AGENTS.md`, the project-instructions layer of omc's AGENTS.md control chain.

**Tech Stack:** Python 3 (uv project), pytest, just.

**Spec:** `docs/superpowers/specs/2026-07-18-cops-990-fix-bug-with-gitnexus-design.md`

## Global Constraints

- Red → green: run the changed test and watch it FAIL before touching implementation; never commit a test that hasn't been seen failing.
- No `pytest.skip` in any form.
- `just build` (ruff + unit tests) must pass before each commit.
- Commit test + implementation together.
- Work happens in this worktree (`feature/cops-990-fix-bug-with-gitnexus`); do not touch the primary checkout.

---

### Task 1: `_gitnexus` passes the primary root's path as `--repo`

**Files:**
- Modify: `tests/unit/test_internal.py:194` (one assertion + its comment)
- Modify: `src/omc/internal.py:84-108` (`_gitnexus`: argv line + docstring sentence)

**Interfaces:**
- Consumes: existing test fixture `_gitnexus_env` (unchanged) — returns `(repo, wt, calls, env)` where `repo` is the primary checkout `Path` and `calls` records the node stub's argv + cwd.
- Produces: `omc internal gitnexus <verb> …` now invokes the GitNexus CLI with `--repo <absolute primary path>` instead of `--repo <basename>`. No signature changes; later tasks rely on nothing from this one.

- [ ] **Step 1: Flip the assertion to the absolute path (the failing test)**

In `tests/unit/test_internal.py`, inside `test_gitnexus_proxy_injects_scoping_and_runs_from_primary`, replace:

```python
    assert "--repo primary" in logged  # basename of the primary root
```

with:

```python
    assert f"--repo {repo}" in logged  # absolute PATH of the primary root
```

(Rationale in the spec: GitNexus registers repos under remote-URL-derived
names — e.g. `gcx-backend-hummingbird-bridge` for a checkout at
`…/hummingbird-chicken` — so a basename `--repo` crashes with
`Repository "…" not found`. GitNexus's resolver also matches canonicalized
paths, which are collision-free.)

- [ ] **Step 2: Run the test and verify it FAILS**

Run: `uv run pytest tests/unit/test_internal.py::test_gitnexus_proxy_injects_scoping_and_runs_from_primary -v`
Expected: FAIL with `AssertionError` on the `f"--repo {repo}"` assertion (the log still contains `--repo primary`, the basename).

- [ ] **Step 3: Fix `_gitnexus`**

In `src/omc/internal.py`, in `_gitnexus`, replace:

```python
    argv = gitnexus_argv(ctx, *rest, "--repo", Path(primary).name, "--branch", base)
```

with:

```python
    argv = gitnexus_argv(ctx, *rest, "--repo", primary, "--branch", base)
```

(`primary` is already a `str` — `primary_root` returns `str | None`; the
`Path` import stays, `_rebase_main` uses it.)

Then extend the docstring: after the sentence ending `…always pin --repo
(registry may hold several repos) and --branch (the configured base).`,
insert this sentence:

```
    --repo is the primary root's PATH, not its basename: GitNexus registers
    repos under remote-URL-derived names that need not match the directory
    name, and its resolver also matches canonicalized paths.
```

- [ ] **Step 4: Run the test and verify it PASSES**

Run: `uv run pytest tests/unit/test_internal.py -v`
Expected: all tests in the file PASS (the flipped one plus its neighbors, which share the fixture).

- [ ] **Step 5: Run the build gate**

Run: `just build`
Expected: ruff clean, full unit suite green, exit 0.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_internal.py src/omc/internal.py
git commit -m "fix: gitnexus proxy scopes --repo by primary path, not basename (red->green)"
```

---

### Task 2: worktree-install guardrail in `.omc/config/AGENTS.md`

**Files:**
- Modify: `.omc/config/AGENTS.md` (append one bullet to the `## Architectural invariants` section)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: prose only — no runtime surface, no test (per spec: the enforcement point is the agent reading the file).

- [ ] **Step 1: Append the guardrail bullet**

In `.omc/config/AGENTS.md`, in the `## Architectural invariants` section, immediately after this existing final bullet:

```markdown
- Long-running behavior is foreground-only: omc never creates daemons,
  LaunchAgents, or cron entries.
```

append:

```markdown
- **Never run `omc install` / `uv tool install` on your own initiative.**
  `omc install <path>` re-roots every future `omc update` at that path — an
  install pointed at a feature worktree has silently pinned the host omc to
  a stale branch multiple times. Installing is a USER decision, made from
  the primary checkout on `main`; if a task seems to require reinstalling
  omc, stop and tell the user the exact command instead of running it.
```

- [ ] **Step 2: Run the build gate**

Run: `just build`
Expected: exit 0 (unchanged code; guards against accidental edits).

- [ ] **Step 3: Commit**

```bash
git add .omc/config/AGENTS.md
git commit -m "docs: AGENTS.md invariant - agents never run omc install on their own"
```
