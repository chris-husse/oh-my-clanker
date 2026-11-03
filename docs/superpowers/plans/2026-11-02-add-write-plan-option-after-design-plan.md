# `/omc:write-plan` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional user-facing conductor `/omc:write-plan` that writes, pressure-tests and commits the implementation plan in the design session. `/omc:implement` then invokes it as a black box, or builds from the plan it already finds.

**Architecture:** This is a skill-text change with no Python product code. Implement's Phase 1 planning text moves verbatim in substance into `skills/write-plan/SKILL.md` (D2). Implement's Phase 1 becomes "invoke `/omc:write-plan implement` when no plan exists", and its `omc internal models` call moves to the start of Phase 2. Design's closing statement and the behavior layer name the new optional step. Tests are unit contract tests over the skill texts, plus E2E changes forked from the `recorded` snapshot.

**Tech Stack:** Markdown skills, pytest, Docker E2E harness (`tests/e2e`), `just check` / `just build`.

**Spec:** `docs/superpowers/specs/2026-11-02-add-write-plan-option-after-design-design.md` (read it in full; §4.2–§4.5 hold the verbatim wording, §5 the test list).

## Global Constraints

- Red → green for every change: write the test, run it, see it FAIL for the expected reason, then implement (`.omc/config/AGENTS.md`).
- Never `pytest.skip` / `skipif`; never run `omc install`, `uv tool install`, `omc watch` or `gitnexus index`.
- Run pytest as `uv run --frozen …` (plain `uv run` dirties `uv.lock` in worktrees). If `uv.lock` shows as modified anyway, `git checkout -- uv.lock`. Never commit it.
- `README.md` is generated: change `.claude/skills/regenerate-readme/SKILL.md` and run `/regenerate-readme`; never hand-edit README.
- No change to `src/omc/implement.py`, `src/omc/review.py`, `src/omc/wtconfig.py`, `skills/audit/SKILL.md`, `skills/plan/SKILL.md`, or any plugin manifest (D4, D6, §4.6).
- No new task type: write-plan dispatches its worker with `tasks.plan` from `OMC_MODELS`.
- Codex syntax is `$omc:write-plan`; Claude syntax is `/omc:write-plan`.
- Do not commit this plan file in any task (the conductor commits it). Each task commits only its own test and skill/code files.
- Quick gate after every task: `just check` (parallel unit tests). E2E files cannot run in `just check`. Validate their edits with `OMC_E2E_NO_DOCKER=1 uv run --frozen python -m pytest --collect-only -q <file>` plus `just check`; the verify stage exercises them.

## Review Focus

1. **Literal order inside `skills/implement/SKILL.md`.** `test_implement_skill_contract` indexes first occurrences. `/omc:write-plan` must not appear in the frontmatter, intro or Phase -1, because it has to come after the first `omc internal design-record`. `omc internal models` must not appear before Phase 2. `subagent-driven-development` must first appear after the Phase 2 models line. Task 2's order assertion pins this.
2. **The caller marker must be unambiguous.** Implement invokes `/omc:write-plan implement`. Empty or other `$ARGUMENTS` means direct use. In caller mode the returned path is an argument to implement, not the end of the turn (ticket-sync doctrine). Task 1 pins the marker literal, and Task 2 pins that implement passes it.
3. **The writing-plans execution-handoff question must not leak.** `superpowers:writing-plans` ends by asking which execution approach to use. write-plan must tell the worker and the main session that this question is superseded: direct use states the continuations, caller mode returns the path. Task 1 pins this.
4. **A tracked plan is never rewritten.** That holds even if its boxes are partly ticked (D7). An untracked plan is finished and committed, never duplicated. Task 1 pins both branches.
5. **Commit scope.** write-plan commits only `docs/superpowers/plans/…-plan.md`, and any other dirty file is named, not committed or discarded. Task 1 pins the needle; Task 4's E2E variation pins the on-disk effect.

---

### Task 1: New `skills/write-plan/SKILL.md` conductor and its contract test

Complexity: medium

**Files:**
- Create: `skills/write-plan/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (`USER_FACING_SKILLS` ~line 146, `CONDUCTORS` ~line 1692, new test after `test_implement_skill_contract`)

**Interfaces:**
- Produces: the skill `write-plan` (frontmatter `name: write-plan`, user-facing). It takes the caller marker `implement` as the whole of `$ARGUMENTS` when `/omc:implement` invokes it (`/omc:write-plan implement`). In that mode it returns the committed plan path to the caller. Task 2 relies on this exact marker word.

- [ ] **Step 1: Write the failing tests.** Add `"write-plan"` to `USER_FACING_SKILLS` (after `"design"`) and to `CONDUCTORS` (`("start", "design", "write-plan", "finish", "implement", "audit")`). Add:

```python
def test_write_plan_skill_contract():
    text = (ROOT / "skills" / "write-plan" / "SKILL.md").read_text()
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "Internal" not in m.group(1) and "not meant for direct invocation" not in m.group(1)
    for needle in (
        "omc internal design-record",
        "OMC_DESIGN_RECORD",
        "/omc:design",
        "omc internal models",
        "OMC_MODELS",
        "tasks.plan.model",
        "superpowers:writing-plans",
        "`Complexity:`",
        "simple | medium | high",
        "The plan stores complexity, never model ids.",
        "/omc:explain",
        "docs/superpowers/plans/*-$OMC_SLUG-plan.md",
        "tracked",
        "untracked",
        "write nothing",
        "plan file only",
        "$ARGUMENTS",
        "/omc:write-plan implement",
        "return the plan path",
        "not the end of your turn",
        "execution approach",
        "/omc:implement",
        "$omc:implement",
        "$omc:write-plan",
        "omc implement --claude",
        "CRITICAL",
        "main session",
        "Completion contract",
    ):
        assert needle in text, f"write-plan skill missing {needle!r}"
    assert "subagent-driven-development" not in text  # it never builds
    assert "missing `Complexity:`" not in text  # the default belongs to the build
    assert (
        "> The plan is committed. To build on this provider, type `/omc:implement`\n"
        "> here (`$omc:implement` on Codex). To build on another provider, exit this\n"
        "> session and run `omc implement --claude` or `omc implement --codex` in\n"
        "> this worktree." in text
    )
    # gate before planning, planning before the commit
    order = [
        text.index("omc internal design-record"),
        text.index("omc internal models"),
        text.index("superpowers:writing-plans"),
        text.index("plan file only"),
    ]
    assert order == sorted(order)
```

- [ ] **Step 2: Run to see it fail.** `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k "write_plan or frontmatter or conductors"`. Expected: FAIL with `FileNotFoundError … skills/write-plan/SKILL.md` in all three.

- [ ] **Step 3: Write `skills/write-plan/SKILL.md`.** Template it on `skills/design/SKILL.md`'s shape and voice. Sections, in this order:
  - **Frontmatter.** `name: write-plan`; the description is user-facing and says it is optional. Suggested: "Write, pressure-test (via /omc:explain) and commit the implementation plan for this slug from the committed design record, then stop with the session open. Optional step between /omc:design and /omc:implement; /omc:implement also invokes it when no plan exists." It must not contain "Internal".
  - **Intro / authority.** Use §4.2 "Authority" nearly verbatim: `/omc:write-plan` (`$omc:write-plan` on Codex) authorizes writing, pressure-testing and committing the plan, and nothing else. It never builds, never touches product code, never pushes, and the session stays open.
  - **`## User Input`** fenced `$ARGUMENTS`. Empty, or anything other than the single word `implement`, means direct use. Exactly `implement` means `/omc:implement` is the caller (it invokes `/omc:write-plan implement`). Follow the leading-mode-word convention used by `grug` and `ticket-sync`.
  - **`## Step 0 — externalize the flow (first action, no exceptions)`.** List the tasks: record gate → existing-plan check → Plan worker writes → per-section pressure test → commit the plan → close (direct: summary + continuations; caller: return the path). This section must come before any "Invoke the"/"invoke the" text (`test_conductors_externalize_their_steps_first`).
  - **`## Step 1 — record gate`.** Use the §4.2 Gate text: `omc internal design-record`, a single `OMC_DESIGN_RECORD` line, anything but `"ok": true` → refuse with `message` and stop. Missing → point at `/omc:design`; unclean → the design session died before its commit; ambiguous → a human decision.
  - **`## Step 2 — existing plan`.** Path `docs/superpowers/plans/*-$OMC_SLUG-plan.md`, decided by `git ls-files`. Tracked → say a plan exists at `path`, state the continuations (in caller mode, return the path), write nothing; never a second dated plan; to re-plan, delete the file first. Untracked → inspect it, finish the pressure test, commit it (Step 5).
  - **`## Step 3 — plan`.** Move implement's current Phase 1 text here unchanged in substance (`skills/implement/SKILL.md` lines 51–73 today): `omc internal models` once, `"ok": false` is a blocker; Plan worker with `tasks.plan.model` / `tasks.plan.effort`; fresh worker on Claude; fresh or partial-history fork on Codex for a model override; orchestrator model with a one-line note when the harness cannot pin one. The worker invokes `superpowers:writing-plans` with the verbatim directive (copy the quoted directive paragraph exactly, including "The plan stores complexity, never model ids."). It saves to `docs/superpowers/plans/YYYY-MM-DD-$OMC_SLUG-plan.md`, then runs one `/omc:explain` per MAJOR section (implementation-level emphasis, same wording as today). It returns the path and CRITICAL questions; the main session asks them in one batch and sends the answers back. Add one sentence: writing-plans' closing question about which execution approach to use is superseded here, so neither the worker nor the main session asks it (this skill's close replaces it).
  - **`## Step 4 — commit`.** Commit the plan file only (`docs/superpowers/plans/…-plan.md`), in both modes (D1). Everything else is left exactly as found. write-plan runs no tests; anything already dirty is named in the summary, never committed or discarded.
  - **`## Step 5 — close`.** Direct use: a short summary (task count, pressure-test findings that changed the plan), then the §4.2 statement verbatim with the exact line breaks asserted in Step 1. Caller mode (`$ARGUMENTS` is `implement`): skip the summary and the statement and return the plan path to `/omc:implement`. That path is an argument to the caller, not the end of your turn; the caller continues in the same turn.
  - **`## Completion contract`.** Complete when the plan is committed and, in direct use, the two continuations are stated. Waiting for the user's next command is a valid stop. Agreement or `ok` is not `/omc:implement`. Do not build or push from here.
  - Do not write the strings `subagent-driven-development` or `missing \`Complexity:\``. Keep `omc internal models`, `superpowers:writing-plans` and "plan file only" out of the frontmatter, intro and Step 0, because the order assertion reads first occurrences. Put the closing blockquote at column 0 (not indented under a list item) so the exact-line needle matches.

- [ ] **Step 4: Run to see it pass.** Same command as Step 2. Expected: PASS. Then `just check`. Expected: all green. (`test_implement_skill_contract` is untouched by this task and still passes.)

- [ ] **Step 5: Commit.**

```bash
git add skills/write-plan/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Add /omc:write-plan conductor skill"
```

### Task 2: Rewire `skills/implement/SKILL.md` to invoke write-plan

Complexity: medium

**Files:**
- Modify: `skills/implement/SKILL.md` (Phase 0 existing-plan paragraph, Phase 1, top of Phase 2, Phase 2b red-gate sentence, Phase 3 first sentence)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_implement_skill_contract`, `test_implement_keeps_task_commits_before_a_red_milestone_gate`, `test_task_model_skill_routing_contract`)

**Interfaces:**
- Consumes: `/omc:write-plan implement` (the caller marker from Task 1) and its returned plan path.

- [ ] **Step 1: Write the failing tests.**
  - `test_implement_skill_contract`: in the needle tuple, replace `"writing-plans"` with `"/omc:write-plan implement"` and remove `"/omc:explain"` (it now lives in write-plan). Keep every other needle (`"omc internal models"`, `` "`Complexity:`" ``, `"missing `Complexity:`"`, `"plan already exists"`, …). Add `assert "superpowers:writing-plans" not in text` (D2: planning logic has one home). Replace the `order` list with:

```python
    order = [
        text.index("omc internal design-record"),
        text.index("/omc:write-plan"),
        text.index("omc internal models"),
        text.index("subagent-driven-development"),
        text.index("## Phase 2b — milestone gate"),
        text.index("/omc:build", text.index("## Phase 2b — milestone gate")),
        text.index("/omc:verify", text.index("## Phase 2b — milestone gate")),
        text.index("## Phase 3 — hand off"),
    ]
```

    and its message becomes `"implement must order record gate -> write-plan -> models -> build -> milestone gate -> handoff"`.
  - `test_implement_keeps_task_commits_before_a_red_milestone_gate`: replace the last assertion with `assert "the plan was already committed by `/omc:write-plan`" in gate` and `assert "plan uncommitted" not in gate`.
  - `test_task_model_skill_routing_contract`: read `write_plan = (ROOT / "skills/write-plan/SKILL.md").read_text()`. Replace `assert "omc internal models" in implement and "Plan" in implement` and `assert "simple | medium | high" in implement` with `assert "omc internal models" in write_plan and "Plan" in write_plan`, `assert "simple | medium | high" in write_plan` and `assert "omc internal models" in implement`. Add `write_plan` to the `for skill in (…)` "model-tier policy" loop.

- [ ] **Step 2: Run to see it fail.** `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k "implement or routing"`. Expected: `test_implement_skill_contract` FAILS on the missing `/omc:write-plan implement` needle, and `test_implement_keeps_task_commits_before_a_red_milestone_gate` FAILS on `the plan was already committed`. The routing test may already pass, because Task 1 put its write-plan needles in place; that is expected.

- [ ] **Step 3: Edit `skills/implement/SKILL.md`.**
  - Leave the frontmatter, intro and Phase -1 free of the literal `/omc:write-plan` (Review Focus 1). Phase -1's list stays "plan → subagent build → milestone gate → handoff".
  - **Phase 0**, existing-plan paragraph (§4.3), keeping the phrase "plan already exists": "If a plan already exists for this slug (`docs/superpowers/plans/*-$OMC_SLUG-plan.md`), it came either from `/omc:write-plan` or from an interrupted implement run: inspect it and continue from the next unfinished task without re-planning. A write-plan plan has no ticked tasks, so the build starts at task 1. Skip Phase 1."
  - **Phase 1 — plan**: replace the whole body. Only when no plan exists, invoke `/omc:write-plan implement` as a black box (the `implement` argument tells it implement is the caller) and take the plan path it returns. That path is an argument to Phase 2, not the end of your turn. write-plan owns the Plan worker, the writing-plans directive, the per-section `/omc:explain` pressure test and the plan commit; CRITICAL questions it returns are asked by this main session as today. Remove the directive paragraph and the `superpowers:writing-plans` mention.
  - **Phase 2 — build**: new opening paragraph before "Execute the plan via `superpowers:subagent-driven-development`": "Run `omc internal models` once and read its single `OMC_MODELS` verdict before the first task dispatch. An `"ok": false` verdict is a blocker; report its `message`." The rest is unchanged. In the "Phase 1 → 2 is NOT a gate" paragraph, change "once the plan is written and pressure-tested" to "once the plan path is in hand". Change "(writing-plans offers a choice; this conductor has already made it)" to "(this conductor has already made that choice)".
  - **Phase 2b**: replace "keep those task commits and the plan uncommitted; the gate blocks only the handoff commit." with "keep those task commits; the plan was already committed by `/omc:write-plan`; the gate blocks only the handoff commit."
  - **Phase 3**: "Commit the plan file and any tracked product…" becomes "Commit the plan's ticked checkboxes and any tracked product or documentation changes left by the implementation workers." The rest is unchanged.

- [ ] **Step 4: Run to see it pass.** Same command as Step 2, then `just check`. Expected: all green.

- [ ] **Step 5: Commit.**

```bash
git add skills/implement/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "Implement invokes /omc:write-plan for its plan phase"
```

### Task 3: Design's three continuations and the behavior layer

Complexity: simple

**Files:**
- Modify: `skills/design/SKILL.md` (Step 5 closing statement, Completion contract)
- Modify: `src/omc/distribution/AGENTS.md` ("Lifecycle scope is binding" bullet, "Externalize a composed flow" bullet)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_design_is_user_facing_and_owns_the_record`, `test_behavior_layer_names_three_authority_words`)

- [ ] **Step 1: Write the failing tests.**
  - `test_design_is_user_facing_and_owns_the_record`: add `assert "/omc:write-plan" in text and "$omc:write-plan" in text` and `assert "three continuations" in text and "two continuations" not in text`.
  - `test_behavior_layer_names_three_authority_words`: add

```python
    assert "`/omc:write-plan`" in text and "`$omc:write-plan`" in text
    flat = " ".join(text.split())
    assert "is optional: it requires a committed design record" in flat
    assert "a later `/omc:implement` builds from that plan" in flat
    externalize = text.split("Externalize a composed flow")[1].split("\n- ")[0]
    assert "/omc:write-plan" in externalize
    assert "write-plan lists end at the committed plan" in " ".join(externalize.split())
```

- [ ] **Step 2: Run to see it fail.** `uv run --frozen pytest tests/unit/test_plugin_manifests.py -q -k "design_is_user_facing or three_authority"`. Expected: FAIL on the new assertions.

- [ ] **Step 3: Edit the two texts.**
  - `skills/design/SKILL.md` Step 5: replace the blockquote with the §4.4 statement verbatim:

    > The design record is committed. To plan and build on this provider, type
    > `/omc:implement` here (`$omc:implement` on Codex). To write the plan here
    > and build on another provider, type `/omc:write-plan` here
    > (`$omc:write-plan` on Codex). To plan and build on another provider, exit
    > this session and run `omc implement --claude` or `omc implement --codex` in
    > this worktree.

    In the Completion contract, "the two continuations" becomes "the three continuations". Leave the frontmatter's "first of the two authority words" wording as is (§4.4).
  - `src/omc/distribution/AGENTS.md`: in "Lifecycle scope is binding", right after "…nothing else; the session then stays open." insert the §4.5 sentence verbatim: "`$omc:write-plan` in Codex, `/omc:write-plan` in Claude, is optional: it requires a committed design record and authorizes writing, pressure-testing and committing the plan — nothing else; the session then stays open, and a later `/omc:implement` builds from that plan." Keep "Three direct user invocations carry authority, in order" unchanged. In "Externalize a composed flow", add `/omc:write-plan` to the conductor list after `/omc:design`. After "design lists end at the committed record and the stated continuations;" add "write-plan lists end at the committed plan and the stated continuations;". Re-wrap to the file's ~78-column style.

- [ ] **Step 4: Run to see it pass.** Same command as Step 2, then `just check` (this also runs `test_design_skill_contract` and `test_distribution_agents_*`). Expected: all green.

- [ ] **Step 5: Commit.**

```bash
git add skills/design/SKILL.md src/omc/distribution/AGENTS.md tests/unit/test_plugin_manifests.py
git commit -m "Design and behavior layer name the optional /omc:write-plan step"
```

### Task 4: Codex payload check and E2E coverage

Complexity: medium

**Files:**
- Modify: `tests/e2e/codex_plugin_payload.py:17` (`OMC_SKILLS`)
- Modify: `tests/unit/test_e2e_codex_auth.py` (`_plugin_payload_fixture` loops at ~319 and ~342; new test after `test_installed_audit_skill_is_required`)
- Modify: `tests/unit/test_lifecycle_command.py` (new test after `test_default_e2e_collects_measured_failing_verify_variation`)
- Modify: `tests/e2e/lifecycle_helpers.py` (new `_direct_write_plan` beside `_direct_implement`, ~line 474)
- Modify: `tests/e2e/variations/test_from_recorded.py` (red-gate assertion; new variation)
- Modify: `tests/e2e/golden/test_golden_claude.py:192` (judge rubric)

**Interfaces:**
- Produces: `_direct_write_plan(provider: str, detail: str = "") -> str`, which returns `"$omc:write-plan"` for codex and `"/omc:write-plan"` otherwise, plus `" " + detail` when given (same shape as `_direct_implement`).

- [ ] **Step 1: Write the failing unit tests.**
  - In `tests/unit/test_e2e_codex_auth.py`, add `"write-plan"` to both fixture loops (`("start", "plan", "design", "write-plan", "implement", "audit")` and `("plan", "design", "write-plan", "implement", "audit")`), and add:

```python
def test_installed_write_plan_skill_is_required(tmp_path):
    from tests.e2e.codex_plugin_payload import validate_payloads

    home, repo_manifest, listing, additions = _plugin_payload_fixture(tmp_path)
    installed = Path(additions["omc@oh-my-clanker"]["installedPath"])
    (installed / "skills" / "write-plan" / "SKILL.md").unlink()
    with pytest.raises(ValueError, match="lacks write-plan skill"):
        validate_payloads(listing, additions, home, repo_manifest)
```

  - In `tests/unit/test_lifecycle_command.py`:

```python
def test_default_e2e_collects_write_plan_variation():
    assert "test_write_plan_commits_plan_only" in _collected("e2e-tests")
```

- [ ] **Step 2: Run to see them fail.** `uv run --frozen pytest tests/unit/test_e2e_codex_auth.py tests/unit/test_lifecycle_command.py -q -k "write_plan"`. Expected: both FAIL (no `ValueError` raised; the variation is not collected).

- [ ] **Step 3: Implement.**
  - `tests/e2e/codex_plugin_payload.py`: `OMC_SKILLS = ("start", "plan", "design", "write-plan", "implement", "audit")`.
  - `tests/e2e/lifecycle_helpers.py`: add `_direct_write_plan` per Interfaces, with the same Codex-TUI comment reference as `_direct_design`.
  - `tests/e2e/variations/test_from_recorded.py::test_failing_verify_blocks_implementation_handoff`: keep the `plans` list and `assert plans, "implementation wrote no plan"`. Replace the uncommitted loop with `rc == 0` checks: `git cat-file -e HEAD:<plan>` must succeed, with the message `f"planning did not commit the plan through write-plan: {plan}"`. Everything else (verify marker, commit count, remote refs, judge) is unchanged.
  - New variation in the same file (default tier, no `expensive`, no `timeout` override; import `_direct_write_plan`):

```python
@pytest.mark.variation("recorded")
def test_write_plan_commits_plan_only(stage_session):
    container, session, m = stage_session
    recorded, _ = _baseline(session, m)
    stages_before = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    session.send(_direct_write_plan("claude"))
    turn = session.wait_turn(240)  # planning half of implement's measured 145 s run
    after = session.snapshot(m["worktree"])
    # exactly one new plan, committed at HEAD
    # git diff --name-only <recorded head>..HEAD is non-empty and every path starts with docs/superpowers/plans/
    # git status --porcelain is empty
    # after["remote_refs"] == recorded["remote_refs"]
    # specs (spec_plan keys containing "/specs/") are identical before/after (record digest unchanged)
    # run_in(container, ["cat", "/tmp/omc-lifecycle-stages"]) == stages_before  (no project stage ran: it did not build)
```

    Each assertion message carries `turn['text']` for diagnosis, like the neighbour test. Run `run_in` git calls with `cwd=m["worktree"]`.
  - `tests/e2e/golden/test_golden_claude.py::test_stage_recorded`: the second rubric line becomes `"The answer names the three ways to continue: the implement command in this session, the write-plan command in this session, or `omc implement` with a provider flag from the shell."`

- [ ] **Step 4: Run to see it pass.** Run the Step 2 command (expect PASS), then `OMC_E2E_NO_DOCKER=1 uv run --frozen python -m pytest --collect-only -q tests/e2e/variations/test_from_recorded.py tests/e2e/golden/test_golden_claude.py`. Expected: collects, including `test_write_plan_commits_plan_only`. Then `just check`. Expected: all green. The E2E bodies themselves run in the verify stage (`/omc:verify`) at the milestone gate.

- [ ] **Step 5: Commit.**

```bash
git add tests/e2e/codex_plugin_payload.py tests/unit/test_e2e_codex_auth.py tests/unit/test_lifecycle_command.py tests/e2e/lifecycle_helpers.py tests/e2e/variations/test_from_recorded.py tests/e2e/golden/test_golden_claude.py
git commit -m "E2E: write-plan variation, committed plan on red gate, payload check"
```

### Task 5: README recipe and regeneration

Complexity: medium

**Files:**
- Modify: `.claude/skills/regenerate-readme/SKILL.md` (lifecycle section, lines ~167–186)
- Modify: `tests/unit/test_cli.py` (new guard beside `test_readme_documents_gitnexus_embeddings`)
- Regenerate: `README.md` (via `/regenerate-readme`, never by hand)

- [ ] **Step 1: Write the failing guard.** Follow the shape of `test_readme_documents_gitnexus_embeddings`:

```python
def test_readme_documents_optional_write_plan():
    root = Path(__file__).resolve().parents[2]
    recipe = (root / ".claude/skills/regenerate-readme/SKILL.md").read_text()
    readme = (root / "README.md").read_text()
    for text, name in ((recipe, "recipe"), (readme, "README")):
        assert "/omc:write-plan" in text, f"{name} does not name /omc:write-plan"
    lifecycle = readme.split("## The lifecycle", 1)[1].split("\n## ", 1)[0]
    assert "/omc:write-plan" in lifecycle and "optional" in lifecycle.lower()
```

- [ ] **Step 2: Run to see it fail.** `uv run --frozen pytest tests/unit/test_cli.py -q -k optional_write_plan`. Expected: FAIL (`recipe does not name /omc:write-plan`).

- [ ] **Step 3: Edit the recipe.** In the lifecycle subsection:
  - The flowchart sentence gains the optional edge: after "explicit /omc:design commits record", add "→ optional `/omc:write-plan` in the design session (commits the plan, stops) → implement builds from it". In other words, the flowchart has an edge Record → write-plan → Build alongside Record → Build.
  - Design bullet: after "`/omc:design` alone writes/hardens/commits record and leaves session open." add "Its closing names three continuations: `/omc:implement` here, optional `/omc:write-plan` here (plan here, build elsewhere), or `omc implement --claude|--codex`."
  - Implement bullet: "plan/subagent build" becomes "plan (via `/omc:write-plan`, or the plan it already committed)/subagent build".
  - Add `skills/write-plan/SKILL.md` to the Lifecycle row of the "Sources to read" table.

- [ ] **Step 4: Regenerate.** Invoke the project skill `/regenerate-readme` and follow it fully (its steps 1–6, including its `test_cli.py -k readme` guard run). Then check that `git diff -- README.md` is limited to the write-plan facts plus whatever the recipe's own step 4 corrections require. An unrelated rewrite of other sections is a defect to undo by regenerating, not by hand-editing.

- [ ] **Step 5: Run to see it pass.** `uv run --frozen pytest tests/unit/test_cli.py -q -k readme`, then `just check`. Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add .claude/skills/regenerate-readme/SKILL.md README.md tests/unit/test_cli.py
git commit -m "README: document the optional /omc:write-plan step"
```
