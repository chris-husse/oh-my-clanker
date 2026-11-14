# Rebase conflicts resolved by the agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent-run rebase that stops on a conflict (`/omc:finish` and `/omc:audit` through it, standalone `/omc:rebase-main`, `/omc:start` Step 3) is resolved, continued and validated by the agent, with no prompt about the conflict.

**Architecture:** Skill text only (D1). One rule lives in `skills/rebase-main/SKILL.md` (rc 3 and rc 2); finish points at it, audit inherits it through finish, start invokes the `rebase-main` skill when its inline rebase pauses. A unit test pins the wording; the README is regenerated; the squash-before-rebase record gets a dated supersede note.

**Tech Stack:** Markdown skills, pytest wording guards (`tests/unit/test_plugin_manifests.py`, `tests/unit/test_cli.py`).

**Spec:** `docs/superpowers/specs/2026-11-14-auto-resolve-rebase-conflicts-design.md` (the design record; read it in full before any task).

## Global Constraints

- Red → green for every change (`.omc/config/AGENTS.md`): write the test, run it, watch it fail for the stated reason, then change the text. Never `pytest.skip` / `skipif`.
- No change to `src/omc/internal.py`, `src/omc/watch.py`, the rc 1/2/3 codes, the `OMC_REBASE_MAIN` verdict or `src/omc/distribution/AGENTS.md` (D1, §4.7). `tests/unit/test_internal.py` stays untouched.
- `omc watch --rebase` keeps abort-on-conflict; README's watch paragraph ("A conflicting rebase is aborted to restore the checkout.") comes out unchanged.
- Anti-goals, never written into any skill as an allowed move: `-X ours` / `-X theirs`, whole-file `git checkout --ours/--theirs`, `git rebase --skip`, `git rebase --abort` on the agent's own initiative, a "continue / you do it / give up?" prompt, conflict markers left in a resolved file.
- `README.md` is generated: never hand-edit it; change `.claude/skills/regenerate-readme/SKILL.md` or its sources and run the `regenerate-readme` skill.
- **Worktree test command (shebang trap):** run pytest as `uv run python -m pytest …`, never bare `uv run pytest` or `just check` (their console-script shebang points at the primary checkout's code). Full unit suite in this worktree:
  `uv run python -m pytest -m "not e2e and not local_iterm2" -q -n auto`
- **Host baseline:** about 247 unit tests fail on this host on `main` too (host environment). Before Task 1, capture the baseline once:
  `uv run python -m pytest -m "not e2e and not local_iterm2" -q -n auto 2>&1 | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort > /tmp/auto-resolve-baseline.txt`
  A task's suite is "green" when its own `FAILED/ERROR` list (same pipeline) equals that file. Do not chase baseline failures.
- Lint touched Python: `uvx ruff format --check tests && uvx ruff check tests` (line length 100).
- No E2E token exists on this host: do not run `just e2e-tests`; no E2E is added (§5).
- Each task ends with its worker committing that task's tested changes (end commit messages with the attribution line from the session's system reminder).

## Review Focus

- **The base log read during a paused rebase.** `git merge-base origin/<base> HEAD` returns `origin/<base>` itself while a rebase is paused (HEAD sits on the new base), so `git log -p <that>..origin/<base>` is empty and the agent sees no base side. The rule must compute the merge-base against `REBASE_HEAD` (verified with real git). Pinned in Task 1: needle `git merge-base origin/<base> REBASE_HEAD`.
- **Start's base vs the configured base.** Start determines `<base>` from `git remote show origin`; `omc internal rebase-main` without `--base` uses `worktree.base_branch`. Re-running with a different base would rebase onto the wrong branch. Pinned in Task 1: start Step 3 invokes the skill with `--base <base>`.
- **Squash-mode way back.** The re-run after a squash-mode resolution prints a second `pre-squash tip` line naming the already-rebased tip; only the first run's line is the way back. Pinned in Task 1: rebase-main needles `git reset --hard <old>`, `folded uncommitted changes stay in <wip>` and `not the way back`.
- **A dirty tree at start still stops** (D6) — start's split must not route a git refusal (nothing paused) to the skill. Pinned in Task 1: start Step 3 still says a dirty tree stops.
- **The watch abort path in the README** (D2) — a regeneration that rewrites "conflicts are resolved" across the README would wrongly cover `omc watch --rebase`. Pinned in Task 2: the watch sentence is asserted verbatim.

---

### Task 1: The resolution rule in `rebase-main`, and finish / audit / start follow it

Complexity: high

**Files:**
- Modify: `skills/rebase-main/SKILL.md` (rc 3 and rc 2 bullets of "Interpreting the outcome", ~lines 50-72)
- Modify: `skills/finish/SKILL.md` (Step 2 bullets, lines 54-61; optionally the "stops the rebase at most once" sentence, line 50-51)
- Modify: `skills/audit/SKILL.md:150` (Phase 3)
- Modify: `skills/start/SKILL.md:45` (Step 0.5 task-list item 3), `:113-115` (Step 3) and `:131` (closing line)
- Modify: `docs/superpowers/specs/2026-11-12-squash-before-rebase-design.md` (one dated note after the §3 Non-goals list, ~line 70)
- Test: `tests/unit/test_plugin_manifests.py` (new test after `test_finish_starts_with_rebase_main`, ~line 1663; edit `test_rebase_main_skill_contract`, line 1599)

**Interfaces:**
- Consumes: nothing.
- Produces: skill text only. Task 2's README regeneration reads the start, finish and rebase-main skill text written here.

- [x] **Step 1: Write the failing tests**

In `test_rebase_main_skill_contract`, replace the needle line

```python
        "git merge --abort",  # rc 2 also covers a resolved-but-unconcluded merge
```

with

```python
        "git merge --continue",  # rc 2 also covers a resolved-but-unconcluded merge
```

and add the needle `"not the way back"` (the re-run's tip line is not the undo; §4.1 step 5). Leave every other needle as is.

Append after `test_finish_starts_with_rebase_main`:

```python
def _flat(text: str) -> str:
    return " ".join(text.split())


def test_rebase_conflicts_are_resolved_not_handed_back():
    # D9: the agent resolves an agent-run rebase's conflicts; it never asks whether to
    # continue, hand over or give up. Banned phrases catch the old handoff verbatim.
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        flat = _flat(path.read_text()).lower()
        for phrase in (
            "never resolve conflicts silently",
            "hand control to the user",
            "conflicts → stop",
            "conflict handoff",
        ):
            assert phrase not in flat, f"{path.parent.name} still hands conflicts back: {phrase!r}"

    # rebase-main owns the one rule (D3).
    rebase = _flat((ROOT / "skills" / "rebase-main" / "SKILL.md").read_text())
    for needle in (
        "not a question",
        "REBASE_HEAD",
        # merge-base against HEAD is origin/<base> itself while the rebase is paused
        "git merge-base origin/<base> REBASE_HEAD",
        "GIT_EDITOR=true git rebase --continue",
        "/omc:check",  # D7: validate a resolution made outside finish
    ):
        assert needle in rebase, f"rebase-main rule missing {needle!r}"
    assert "never `git rebase --abort`" in rebase.lower()
    assert "or `git rebase --abort` if they choose" not in rebase

    # finish follows the rule for rc 3 and rc 2; rc 1 still stops.
    finish = (ROOT / "skills" / "finish" / "SKILL.md").read_text()
    step2 = _flat(finish.split("## Step 2", 1)[1].split("## Step 3", 1)[0])
    assert "resolve it per the `rebase-main` skill's rule" in step2
    assert "rc 1" in step2 and "surface the stderr message and stop" in step2

    # audit inherits finish's resolution.
    audit = _flat((ROOT / "skills" / "audit" / "SKILL.md").read_text())
    assert "conflict resolution apply unchanged" in audit

    # start: conflicts go through the rebase-main skill; a dirty tree still stops (D6).
    start = (ROOT / "skills" / "start" / "SKILL.md").read_text()
    step3 = _flat(start.split("## Step 3", 1)[1].split("## Step 4", 1)[0])
    assert "invoke the `rebase-main` skill with `--base <base>`" in step3
    assert "dirty tree → stop" in step3.lower()
    assert "never brainstorm on a stale base" in step3.lower()
```

(If a `_flat` helper already exists in the file by then, reuse it instead of adding one.)

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py -k "rebase_main_skill_contract or rebase_conflicts_are_resolved" -v`
Expected: both FAIL — the contract test on `'not the way back'` (the swapped `git merge --continue` needle already passes: today's rc 2 text names it as a user option, and the swap only stops pinning the `--abort` option), the new test on the first banned phrase (`audit` still says `conflict handoff`).

- [x] **Step 3: Rewrite the rc 3 and rc 2 bullets of `skills/rebase-main/SKILL.md`**

The rc 3 bullet keeps its first sentence (rebase hit conflicts, LEFT PAUSED), then says "you resolve it — this is not a question" and carries the §4.1 block as a numbered list:

1. For each conflicted path (the verdict's `conflicts`; `git status` when there is no verdict) read both sides before editing: the base's side, `git log -p $(git merge-base origin/<base> REBASE_HEAD)..origin/<base> -- <file>` (write it so the literal `git merge-base origin/<base> REBASE_HEAD` appears — against `HEAD` the merge-base is `origin/<base>` itself while the rebase is paused), and the branch's side, `git show REBASE_HEAD -- <file>`.
2. Write the merge that keeps both intents; carry the branch's change onto a renamed / moved / reshaped base; never drop either side.
3. No conflict markers in the resolved paths, `git diff --name-only --diff-filter=U` empty, `git add` them, `GIT_EDITOR=true git rebase --continue`.
4. Stopped again → repeat from 1. Rebase complete → re-run `omc internal rebase-main` with the same arguments (mirror still happens; with `--squash` the re-run is idempotent).
5. Report in a few lines what conflicted and how each path was resolved; flag merges where the two intents were hard to reconcile. In squash mode carry the first run's stderr `pre-squash tip <old> (undo: git reset --hard <old>)` line, and its `folded uncommitted changes stay in <wip>` clause when present, as the way back; say the re-run's line names the already-rebased tip and is not the way back. A report, not a question; the flow continues.
6. Never `git rebase --abort`, never `git rebase --skip`, no `-X ours` / `-X theirs` or whole-file `--ours` / `--theirs`, never ask whether to continue, hand over or give up.

After the list: when something was resolved and the skill was not invoked by `/omc:finish` with `--squash`, run `/omc:check` before returning (a clean rebase adds no check). A red check falls under the behavior layer's stage-gate rule, its fix-forward confined to repairing the merge — the conflicted paths and what the merge broke — never new feature work. The only stop after a conflict is that rule's existing CRITICAL question.

The rc 2 bullet keeps its first two sentences (what is in progress; the primary's detached-HEAD `note` parenthesis) and replaces "Relay the stderr line and stop: …" with: finish whatever `git status` shows by the same rule — both intents, no markers, then that operation's own `--continue` (`git rebase --continue` / `git merge --continue`; a cherry-pick or revert takes its own `--continue`) — re-run the command, and report it like any other resolution (including the `/omc:check` above). No per-operation procedure.

The rc 1 bullet is unchanged. Delete the old "help the user resolve them … or `git rebase --abort` if they choose to back out … Never resolve conflicts silently, never abort on their behalf" text; its pre-squash-tip content now lives in step 5.

- [x] **Step 4: Point finish, audit and start at the rule**

`skills/finish/SKILL.md` Step 2:
- rc 3 bullet: "(conflicts, rebase left paused): resolve it per the `rebase-main` skill's rule — this is not a question — then re-run the `rebase-main` skill with `--squash` (idempotent: …) before continuing." Keep the idempotent clause.
- Split "rc 1 or rc 2": **rc 1** (the fetch failed, git refused the rebase, the squash failed — e.g. a hook rejected the squash commit): surface the stderr message and stop. **rc 2** (a rebase, merge, cherry-pick or revert already in progress): finish it per the `rebase-main` skill's rc 2, then re-run with `--squash`.
- The count-gate bullet and the `note` bullet stay byte-for-byte (`test_finish_starts_with_rebase_main` pins them).
- Name no stage (`` `check` ``, `` `build` ``…) in Step 2: `test_finish_skill_contract` orders the first occurrences of `` `rebase-main` `` → `` `check` `` → … → `` `create-mr` ``; Step 3's stages already validate the resolved tree (§4.2).
- Optional, for the README (Task 2): extend "so a conflict with the base stops the rebase at most once" with "— and this session resolves that stop itself (rc 3 below)".

`skills/audit/SKILL.md:150`: "failing-stage behavior and conflict handoff apply unchanged" → "failing-stage behavior and conflict resolution apply unchanged".

`skills/start/SKILL.md` Step 3, item 2's "not an ancestor" sub-bullet becomes:
- `git rebase origin/<base>`. **Dirty tree → stop** and surface; never force it (git refused before starting; nothing is paused).
- **Conflicts** (the rebase paused) → invoke the `rebase-main` skill with `--base <base>`: it finds the paused rebase (rc 2), resolves it by its rule, re-runs, runs `/omc:check` and reports; then continue to Step 4.
- Keep "Never brainstorm on a stale base."

Step 0.5's externalized item 3 gains the branch so it survives in the task list: "Base freshness gate — fetch + rebase onto `origin/<base>`; a conflict goes through the `rebase-main` skill (Step 3)". (The `rebase-main` invocation sits in Step 3, after Step 0.5's "externalize the flow", as `test_conductors_externalize_their_steps_first` requires.)

No copy of the rule and no check clause of start's own (D3). Closing line 131: "it never designs or writes code itself — except merging the branch's existing commits onto the base, which is part of the freshness gate."

- [x] **Step 5: Add the supersede note to the squash-before-rebase record**

In `docs/superpowers/specs/2026-11-12-squash-before-rebase-design.md`, directly after the §3 **Non-goals (D4).** list (before **Anti-goals.**), add one dated note:

```markdown
> **2026-11-14 — superseded in part:** the non-goal "resolving conflicts on the
> user's behalf" (and "automatic conflict resolution" in D4 and §7) is superseded
> by `docs/superpowers/specs/2026-11-14-auto-resolve-rebase-conflicts-design.md`:
> the agent now resolves an agent-run rebase's conflicts. The rest of this record
> stands as written.
```

Nothing else in that record changes.

- [x] **Step 6: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_plugin_manifests.py -q`
Expected: PASS — the new test, the edited `test_rebase_main_skill_contract`, and unchanged `test_finish_starts_with_rebase_main`, `test_start_skill_contract`, `test_audit_skill_contract`.

Also run the other pins on these four skills: `uv run python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_agentsmd.py -q -k "finish or start or audit or conductors or publish_route or rebase"` — expected PASS (`test_finish_skill_contract`, `test_conductors_externalize_their_steps_first`, `test_publish_route_squashes_before_rebasing`, `test_ticket_sync_names_finish_follow_ups_step` are among them).

Then re-read the four diffs once for the anti-goals: no skill now names `--skip`, `-X ours/theirs` or `--abort` as an allowed move.

- [x] **Step 7: Full suite, lint, commit**

Run the full suite (Global Constraints) and compare against the baseline; run ruff on `tests`.

```bash
git add skills/rebase-main/SKILL.md skills/finish/SKILL.md skills/audit/SKILL.md skills/start/SKILL.md docs/superpowers/specs/2026-11-12-squash-before-rebase-design.md tests/unit/test_plugin_manifests.py
git commit -m "Agent resolves rebase conflicts instead of handing them back"
```

---

### Task 2: Regenerate the README

Complexity: medium

**Files:**
- Modify: `README.md` (generated — only via the skill)
- Modify (only if needed): `.claude/skills/regenerate-readme/SKILL.md` (Design subsection, ~line 192; finish prose, ~line 232)
- Test: `tests/unit/test_cli.py` (after `test_readme_documents_squash_before_rebase`, ~line 401)

**Interfaces:**
- Consumes: the start / finish / rebase-main skill text from Task 1.
- Produces: nothing later tasks use.

- [x] **Step 1: Write the failing guard**

```python
def test_readme_says_rebase_conflicts_are_resolved_not_handed_back():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    paras = [" ".join(p.split()) for p in readme.split("\n\n")]
    start = next(p for p in paras if "primer" in p and "seed" in p)
    finish = next(p for p in paras if "squash" in p and "--force-with-lease" in p)
    assert "stops on conflicts" not in start, start
    assert "resolv" in start.lower(), start
    assert "resolv" in finish.lower(), finish
    # omc watch --rebase keeps abort-on-conflict (D2).
    assert "A conflicting rebase is aborted to restore the checkout." in readme
```

- [x] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/unit/test_cli.py::test_readme_says_rebase_conflicts_are_resolved_not_handed_back -v`
Expected: FAIL — the start paragraph still says "rebases or stops on conflicts".

- [x] **Step 3: Regenerate**

Invoke the `regenerate-readme` skill and follow it in full. In its guard step run `uv run --frozen python -m pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'` (worktree shebang trap). Expected diff: the start sentence (README line 181, "rebases or stops on conflicts") and the finish paragraph (line 233, "stops the rebase at most once") say the session resolves the conflict; the watch paragraph (line 141) is unchanged. The recipe's Lifecycle sources include `skills/start/SKILL.md` and `skills/finish/SKILL.md` (not the `rebase-main` body), and its step 1 carries facts forward from the existing README, so the stale "rebases or stops on conflicts" can survive a regeneration; this task's guard is what detects that (its name contains `readme`, so the recipe's own guard step runs it). If the regeneration does not pick the change up from the skill sources, add one clause to the recipe — Design bullet: "refreshes base (a conflicting rebase is resolved by the session through the `rebase-main` skill, never handed back; a dirty tree stops)"; finish prose: "a conflict with the base stops the rebase at most once and the session resolves it before the gates" — and regenerate those sections again. Inspect `git diff README.md`; any change beyond these must trace to a current source fact; regenerate the affected section rather than hand-editing.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/unit/test_cli.py tests/unit/test_plugin_manifests.py -q`
Expected: PASS.

- [x] **Step 5: Full suite, lint, commit**

Run the full suite and compare against the baseline; run ruff on `tests`.

```bash
git add README.md tests/unit/test_cli.py .claude/skills/regenerate-readme/SKILL.md
git commit -m "docs: regenerate README for agent-resolved rebase conflicts"
```

---

## Self-review against the spec

- §4.1 rule steps 1–6, rc 2, after-resolution `/omc:check`, rc 1 unchanged → Task 1 Step 3. §4.2 → Task 1 Step 4 (finish). §4.3 → Task 1 Step 4 (audit). §4.4 → Task 1 Step 4 (start). §4.5 → Task 1 Step 3 (check outside finish; fix-forward confined). §4.6 note → Task 1 Step 5; README → Task 2. §4.7 unchanged → Global Constraints.
- §5 test 1 → Task 1 `test_rebase_conflicts_are_resolved_not_handed_back`; test 2 → Task 1 Step 1 (needle swap); test 3 → Task 1 Step 6 runs it unchanged. README guards → Task 2.
- Plan additions beyond §5, each a red-first guard for a spec'd behavior: the `git merge-base origin/<base> REBASE_HEAD` and `/omc:check` needles, `not the way back`, start's `--base <base>`, and the README guard in Task 2.
