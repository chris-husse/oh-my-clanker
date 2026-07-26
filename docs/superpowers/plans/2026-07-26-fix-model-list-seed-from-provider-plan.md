# Fix Model List: Seed From Provider Aliases — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded version-pinned model IDs in `ClaudeProvider` with CLI aliases (`fable`, `opus`, `sonnet`) so the `omc configure` picker is self-updating.

**Architecture:** Two methods in `ClaudeProvider` change their return values from version-pinned IDs to CLI aliases. Tests across four files update their assertions to match.

**Tech Stack:** Python, pytest, uv

---

### Task 1: Update `ClaudeProvider.models()` and `docs_model_default()`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/providers/claude.py:12-16`

- [ ] **Step 1: Change `models()` return value**

In `src/omc/providers/claude.py`, change line 13 from:

```python
return ["claude-fable-5", "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]
```

to:

```python
return ["fable", "opus", "sonnet"]
```

- [ ] **Step 2: Change `docs_model_default()` return value**

In `src/omc/providers/claude.py`, change line 16 from:

```python
return "claude-sonnet-5"  # standard coding tier — the docs floor
```

to:

```python
return "sonnet"
```

- [ ] **Step 3: Run tests to see what breaks**

Run: `uv run pytest tests/unit/test_providers.py tests/unit/test_docs_model.py tests/unit/test_watch.py tests/unit/test_dependency.py -v --tb=short`

Expected: failures in `test_docs_model.py`, `test_watch.py`, and `test_dependency.py` where assertions still reference `"claude-sonnet-5"`. `test_providers.py` should pass (no model-list-specific assertions).

- [ ] **Step 4: Commit the production change**

```bash
git add src/omc/providers/claude.py
git commit -m "fix: use CLI aliases in ClaudeProvider model list

Replace version-pinned IDs with fable/opus/sonnet aliases that the
claude CLI resolves to the latest model in each family."
```

### Task 2: Update test assertions

**Model:** standard coding tier

**Files:**
- Modify: `tests/unit/test_docs_model.py:19,26,28`
- Modify: `tests/unit/test_watch.py:122`
- Modify: `tests/unit/test_dependency.py:465`

- [ ] **Step 1: Fix `test_docs_model.py`**

Change three assertions from `"claude-sonnet-5"` to `"sonnet"`:

Line 19:
```python
assert get_provider("claude").docs_model_default() == "sonnet"
```

Line 26:
```python
assert docs_model_for(_cfg(), "claude") == "sonnet"
```

Line 28:
```python
assert docs_model_for(_cfg(model="claude-fable-5"), "claude") == "sonnet"
```

- [ ] **Step 2: Fix `test_watch.py`**

Change line 122 from:
```python
assert "--model claude-sonnet-5" in recorded
```

to:
```python
assert "--model sonnet" in recorded
```

- [ ] **Step 3: Fix `test_dependency.py`**

Change line 465 from:
```python
assert "--model claude-sonnet-5" in log
```

to:
```python
assert "--model sonnet" in log
```

- [ ] **Step 4: Run the full affected test suite**

Run: `uv run pytest tests/unit/test_providers.py tests/unit/test_docs_model.py tests/unit/test_watch.py tests/unit/test_dependency.py -v --tb=short`

Expected: all pass.

- [ ] **Step 5: Run the full build gate**

Run: `just build`

Expected: lint + format + all unit tests pass.

- [ ] **Step 6: Commit the test updates**

```bash
git add tests/unit/test_docs_model.py tests/unit/test_watch.py tests/unit/test_dependency.py
git commit -m "test: update assertions for CLI alias model defaults"
```
