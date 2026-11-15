# GitNexus index hygiene in the review stage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent finishing any branch in an omc-managed repo keeps committed generated, build/output and vendored directories out of the GitNexus graph by maintaining a repo-root `.gitnexusignore`. A short inline step in omc's review stage tells it to.

**Architecture:** Skill text only (D1, §3 non-goals). Add one paragraph to `skills/review/SKILL.md` as a step-2 continuation paragraph, after step 2's Missing/Present bullets and before `3. **Grug lens.**`. Reroute the two passing branches of step 2 to it; the paragraph then hands on to step 3. A red-first wording guard in `tests/unit/test_plugin_manifests.py` pins it.

**Tech Stack:** Markdown skill, pytest wording guard.

**Spec:** `docs/superpowers/specs/2026-11-15-investigate-gitnexus-index-node-bloat-design.md` (the design record; read it in full before the task).

## Global Constraints

- Red → green (`.omc/config/AGENTS.md`): write the test, run it, watch it fail for the stated reason, then change the skill. Never `pytest.skip` / `skipif`.
- Only `skills/review/SKILL.md` and `tests/unit/test_plugin_manifests.py` change. No change to `src/omc/`, the GitNexus fork, `skills/finish/SKILL.md`, `skills/human-review/mr-feedback-rules.md`, `src/omc/distribution/AGENTS.md` or `/omc:integrate` (D1, D2, D5, §3).
- The step numbers, the `3. **Grug lens.**` heading, the `OMC_STAGE` line and the meaning of `"passed"` stay unchanged (§4, D4). The existing guards `test_review_proxy_runs_grug`, `test_review_proxy_dispatches_grug_judgment_on_review_choice`, `test_review_proxy_runs_human_review`, `test_stage_proxy_contract` and `test_audit_and_project_review_dispatch_review_judgment_even_without_findings` (whose slice runs from `2. Look for` to `3. **Grug lens.**`, so it now contains the new paragraph) must pass **unedited**.
- The new paragraph must not contain any string that existing guards locate by first occurrence: `3. **Grug lens.**`, `- **Grug unavailable.`, `Fix first, Review`, `**waive**`, `batched`, `grug diff`, `grug skill unavailable`, `human-review skill unavailable`, `human-review: invalid rules`, `"passed": false`. It must also avoid the banned conflict phrases in `test_rebase_conflicts_are_resolved_not_handed_back`.
- `README.md` is generated; never hand-edit it. The README guards do not read `skills/review/SKILL.md` (§6). Run `/regenerate-readme` only if one of them fails.
- **Worktree test command (shebang trap):** run pytest as `uv run python -m pytest …`, never bare `uv run pytest` or `just check`.
- **Host baseline:** `just check` has about 247 host-environment failures on `main` here (e.g. 4 in `tests/unit/test_review.py`, which tests the `omc review` CLI and not this skill). Verify against the target files below; do not chase baseline failures.
- Lint the touched test: `uvx ruff format --check tests && uvx ruff check tests` (line length 100).
- No E2E is added (§5); no E2E token exists on this host.
- The worker commits test and skill together and ends the commit message with the attribution line from the session's system reminder.

## Review Focus

- **The Missing branch skipping the step.** Both passing branches of step 2 currently say "Continue to step 3", so an agent following them literally would jump past a paragraph placed after step 2. The two routes are reworded to point at the hygiene paragraph, and the paragraph ends with "Then continue to step 3." Pinned in Task 1: the Missing and passed slices contain `index hygiene`.
- **A failed project stage.** §4 says hygiene is skipped with the lenses, so the failed bullet must still go straight to step 4. Pinned in Task 1: the failed slice names `step 4` and not `index hygiene`.
- **A new, untracked `.gitnexusignore` dropped by finish.** finish amends with `git add -u`, which ignores untracked files. Pinned in Task 1: needle `` `git add .gitnexusignore` ``. Pressure test: review is finish's last stage, nothing touches the index between it and the amend, and create-mr's `git commit --amend` also commits a staged file. A standalone `/omc:review`'s staged file is folded in by the next squash's `git add -A` (`src/omc/internal.py:_squash`).
- **Over-ignoring hand-written code** (§7). Pinned in Task 1: needles `*directory*` and `stay indexed`.
- **The step gating publication** (D4). Pinned in Task 1: needle `never fails the stage`. The `OMC_STAGE` shape is guarded by the existing tests, which stay unedited.

---

### Task 1: The index hygiene step in the review skill, guarded red-first

Complexity: simple

**Files:**
- Modify: `skills/review/SKILL.md:22-40` (step 2: Missing bullet line 24, passed bullet line 40, new paragraph after line 40 and before `3. **Grug lens.**` at line 41)
- Test: `tests/unit/test_plugin_manifests.py` (new test directly after `test_review_proxy_runs_human_review`, which ends ~line 380)

**Interfaces:**
- Consumes: `_flat(text: str) -> str` (module-level in the same test file, ~line 1667; it is called at run time, so its later position in the file is fine).
- Produces: skill text only.

- [x] **Step 1: Write the failing test**

```python
def test_review_proxy_keeps_gitnexusignore_covering_generated_code():
    steps = (ROOT / "skills" / "review" / "SKILL.md").read_text().split("## Steps", 1)[1]
    heading = "**GitNexus index hygiene.**"
    assert heading in steps, "review has no GitNexus index hygiene step"
    hygiene = _flat(steps.split(heading, 1)[1].split("3. **Grug lens.**", 1)[0])
    for needle in (
        "`.gitnexusignore`",
        "`git add .gitnexusignore`",
        "never fails the stage",
        "*directory*",
        "stay indexed",
        "inline",
        "summary",
    ):
        assert needle in hygiene, f"index hygiene step missing {needle!r}"
    # after the project stage, before the grug lens (and so before its unavailable exits)
    assert (
        steps.index("Project stage **passed**")
        < steps.index(heading)
        < steps.index("3. **Grug lens.**")
    )
    # both passing branches route through it; a failed project stage skips it with the lenses
    missing = _flat(steps.split("**Missing**", 1)[1].split("**Present**", 1)[0])
    passed = _flat(steps.split("Project stage **passed**", 1)[1].split(heading, 1)[0])
    failed = _flat(
        steps.split("Project stage **failed**", 1)[1].split("Project stage **passed**", 1)[0]
    )
    assert "index hygiene" in missing and "index hygiene" in passed
    assert "index hygiene" not in failed and "step 4" in failed
```

- [x] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py::test_review_proxy_keeps_gitnexusignore_covering_generated_code -q`
Expected: FAIL with `AssertionError: review has no GitNexus index hygiene step`

- [x] **Step 3: Edit `skills/review/SKILL.md` step 2**

- Missing bullet (line 24): `Continue to step 3.` → `Continue to the index hygiene paragraph below.`
- Project stage **passed** bullet (line 40): `continue to step 3.` → `continue to the index hygiene paragraph below.`
- Leave the failed bullet (lines 38-39) unchanged.
- Insert this paragraph after line 40, with a blank line before it and 3-space indentation so it belongs to item 2 and not to the Present bullet. Wrap it like its neighbours. The facts the test pins are fixed; the prose may be tightened:

```markdown
   **GitNexus index hygiene.** Reached from either passing branch above;
   run it inline in the main session, no worker. omc indexes this repo with
   GitNexus, the code knowledge graph behind `/omc:explain`. Committed
   generated code, build/output and vendored files bloat it. If the branch
   diff adds or changes tracked files of that kind, make sure the repo-root
   `.gitnexusignore` (gitignore syntax) covers their *directory* (e.g.
   `src/**/generated/`; a `linguist-generated` line in `.gitattributes` is a
   strong hint), then `git add .gitnexusignore`. Hand-written source, tests
   and docs stay indexed. This never fails the stage; name any added
   patterns in the summary. Then continue to step 3.
```

- [x] **Step 4: Run the guard file and the README guards; verify they pass**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py -q`
Expected: `102 passed` (101 on the base plus the new test; no existing guard edited)

Run: `uv run python -m pytest tests/unit/test_cli.py -q -k readme`
Expected: `13 passed`

Run: `uvx ruff format --check tests && uvx ruff check tests`
Expected: clean

- [x] **Step 5: Commit**

```bash
git add skills/review/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Review stage keeps .gitnexusignore covering committed generated code"
```
