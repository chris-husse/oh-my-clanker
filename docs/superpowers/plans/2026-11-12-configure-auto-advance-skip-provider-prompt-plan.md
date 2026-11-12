# Auto-advance (`LLM › Provider Selection Per Step`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one global boolean, `llm.provider_selection_per_step` (default `false`). With it off, `/omc:design`, a directly typed `/omc:write-plan` or `/omc:implement` continues through implement, audit, `finish` and the push without another typed command. With it on, every stop stays as it is today.

**Architecture:** A new `LLMConfig` bool leaf. `set_key` and `_hydrate` accept exactly `true`/`false`, and the existing menu loop shows it under `LLM` with no menu code. `omc internal models` adds the leaf to its `OMC_MODELS` ok payload. The three conductors already read that verdict. Their close checks the flag. When it is off, they print one notice line and invoke the next conductor as a black box in the same turn. The behavior layer, the E2E harness (pinned `true`, so today's stage assertions hold), one new expensive chain variation and the generated README follow.

**Tech Stack:** Python 3 dataclasses + PyYAML config, mininterface menu, Markdown skills, pytest, Docker E2E harness (`tests/e2e`), `just`.

**Spec:** `docs/superpowers/specs/2026-11-12-configure-auto-advance-skip-provider-prompt-design.md`. Read it in full. §4.2–§4.5 hold the wording, §5 the test list, and D1–D11 the decisions.

## Global Constraints

- Red → green for every change. Write the test, run it, and see it FAIL for the expected reason. Then implement and see it pass. Commit the test and the implementation together (`.omc/config/AGENTS.md`). Never `pytest.skip` / `skipif`. Never run `omc install`, `uv tool install`, `omc watch` or `gitnexus index`.
- **Run tests as `uv run --frozen python -m pytest …`**, never `uv run pytest` or `just check`. In this worktree a copied venv's `pytest` shebang can import the main checkout's code. `--frozen` avoids uv's version-stamp rewrite of `uv.lock`. If `git status` shows `uv.lock` modified anyway, run `git checkout -- uv.lock`. Never commit `uv.lock`.
- Fast-gate equivalent: `uv run --frozen python -m pytest -m "not e2e and not local_iterm2" -q -n auto`.
- **Host baseline:** about 22 unit tests fail on this host whatever the branch (`test_fish_hook`, `test_worktrunk_real`, `test_configure_pty`, `test_e2e_image_holders`, `test_codex_plugin` symlink-loop, `test_installer`, `test_lifecycle_command` in worktrees). Judge only the tests you touch or add, plus "no new failures" against that baseline. (`tests/unit/test_lifecycle_command.py` passed in this worktree on 2026-11-12 after uv recreated the venv. If it fails here for an environment reason, record the failure output and judge it by `--collect-only` of the recipe instead.)
- **No Claude E2E token exists on this host. Never ask for one.** E2E test changes (harness, `test_from_start.py`, the new chain variation) are written and must import and collect cleanly: `OMC_E2E_NO_DOCKER=1 uv run --frozen python -m pytest --collect-only -q <files>`. They cannot run here. Their unit-level counterparts (`tests/unit/test_e2e_model_fixture.py`, `tests/unit/test_lifecycle_command.py`) do run.
- Python edits pass `uvx ruff format <files>` and `uvx ruff check <files>` (line length 100) before commit, so `/omc:build` stays green.
- Each task ends with ONE commit holding its tests and implementation. Every commit message ends with the line:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  Do not commit this plan file in any task, apart from ticking its checkboxes if the conductor asks you to.
- `README.md` is generated. Change `.claude/skills/regenerate-readme/SKILL.md` and run `/regenerate-readme` (Task 9). Never hand-edit README.
- `ToolContext` stays the only subprocess/env/network boundary. No change touches it.
- Unchanged, per §4.6: `src/omc/implement.py`, `src/omc/review.py`, `src/omc/cli/__init__.py`, the `omc internal` usage line / `_USAGE`, `skills/finish`, `skills/plan`, `skills/start`, `skills/review`, `skills/grug`, the `tasks` block of `OMC_MODELS`. No new task type, no new verdict, no migration (D7).
- **Shared skill wording (Tasks 3–5 use it byte for byte):**
  - Reading rule (D5): ``Only an `OMC_MODELS` verdict with `"ok": true` and `"provider_selection_per_step": false` continues; a missing field or any other value stops as below.``
  - Notice line (D6) as a column-0 blockquote, with `<cmd>` = `/omc:implement` or `/omc:audit`:
    ```
    > Provider selection per step is off — continuing to `<cmd>`
    > (turn on: `omc configure` → LLM → Provider Selection Per Step).
    ```
    Add one plain sentence after it: on Codex the command is spelled `$omc:implement` / `$omc:audit`.
  - Frontmatter qualifier: `with provider selection per step on … off (the default) continues into the next stage`. **Frontmatter descriptions are YAML plain scalars, so never write `": "` (colon + space) inside one.**
- **Literal-order traps in skill texts** (contract tests index first occurrences):
  - `skills/implement/SKILL.md`: `/omc:write-plan` must not appear before Phase 0's `omc internal design-record`. That rules out the frontmatter, the intro and Phase -1 (write "write-plan" without the `/omc:` prefix there). `omc internal models` must appear exactly once, in Phase 2.
  - `skills/write-plan/SKILL.md`: `omc internal models` appears exactly once, after the Step 1 record-gate paragraph and before `superpowers:writing-plans`.
  - Every conductor: the first `Invoke the` / `invoke the` must come after "externalize the flow" (`test_conductors_externalize_their_steps_first`). The intro and frontmatter must not contain either phrase.

## Review Focus

1. **Fail toward a stop (D5).** Each conductor auto-advances only on `"ok": true` with `"provider_selection_per_step": false`. A missing field means today's statement. The reading-rule sentence is identical in all three skills, and the tests pin it.
2. **No shortcut past the build.** `design` and `write-plan` never name `/omc:audit` or `$omc:audit`. The chain always goes through implement. Tests pin the negative.
3. **Caller mode is untouched.** `/omc:write-plan implement` still returns the plan path and never invokes `/omc:implement` (that would recurse).
4. **The bool is a real bool on both entry points.** `set_key` must not `setattr` the string `"false"`, which is truthy. The menu's commit goes through `set_key`.
5. **E2E keeps today's stops.** `configure_omc` pins `true` in its first configure call. The exact-verdict assertion in `variations/test_from_start.py` gains the field, and the one chain variation sets `false` itself.

## File map

| File | Change | Task |
|---|---|---|
| `src/omc/config/schema.py` | `LLMConfig.provider_selection_per_step: bool = False` | 1 |
| `src/omc/config/store.py` | `set_key` bool branch; `_hydrate` bool check | 1 |
| `tests/unit/test_config_store.py`, `tests/unit/test_configure_menu.py`, `tests/unit/test_configure.py` | config/menu/CLI tests | 1 |
| `src/omc/internal.py` | `_models` ok payload gains the field | 2 |
| `tests/unit/test_taskmodels.py` | verdict test | 2 |
| `skills/design/SKILL.md` | frontmatter, intro, Step 0, Step 5, contract | 3 |
| `skills/write-plan/SKILL.md` | frontmatter, intro, Step 1 models read, Step 2/3/5, contract | 4 |
| `skills/implement/SKILL.md`, `skills/audit/SKILL.md` | implement frontmatter/intro/Phase -1/2b/3/contract; audit intro sentence | 5 |
| `src/omc/distribution/AGENTS.md` | lifecycle sentence; externalize endings | 6 |
| `tests/unit/test_plugin_manifests.py` | skill and layer contract tests | 3–6 |
| `tests/e2e/harness.py`, `tests/e2e/variations/test_from_start.py`, `tests/unit/test_e2e_model_fixture.py` | pin the setting on; exact verdict | 7 |
| `tests/e2e/variations/test_from_agreed.py`, `justfile`, `tests/unit/test_lifecycle_command.py` | chain variation + recipe pin | 8 |
| `.claude/skills/regenerate-readme/SKILL.md`, `README.md`, `tests/unit/test_cli.py` | recipe, regenerated README, guard | 9 |

---

### Task 1: The `llm.provider_selection_per_step` setting

Complexity: medium

**Files:**
- Modify: `src/omc/config/schema.py` (`LLMConfig`, after `providers`)
- Modify: `src/omc/config/store.py` (`set_key` ~line 305; `_hydrate` `if cls is LLMConfig:` ~line 410)
- Test: `tests/unit/test_config_store.py` (new block after the `gitnexus.embeddings` block ~line 776; `test_schema_menu_metadata_does_not_change_persisted_defaults` ~line 83)
- Test: `tests/unit/test_configure_menu.py` (`test_menu_groups_schema_fields_and_registry_providers` ~line 63; new toggle test after `test_gitnexus_embeddings_toggle_saves_only_the_global_file`)
- Test: `tests/unit/test_configure.py` (new test after `test_configure_set_gitnexus_embeddings_writes_only_the_global_file` ~line 130)

**Interfaces:**
- Produces: `LLMConfig.provider_selection_per_step: bool` (default `False`). Field metadata: label `"Provider Selection Per Step"`, help `"Stop after each stage to offer another provider; off = continue automatically through to the push."`. `store.set_key(cfg, "llm.provider_selection_per_step", "true"|"false")`. Error texts: `"llm.provider_selection_per_step expects true or false"` (set) and `"invalid llm.provider_selection_per_step in <path>: expected true/false"` (load). Task 2 reads `cfg.llm.provider_selection_per_step`.

Pressure-test notes (explain over the graph, verified in code):
- Only `configure._compose_menu` iterates `fields(gcfg.llm)`. It skips `docs` and `providers`, and `options` is non-`None` only for `default`, so the bool gets a plain `SavedTag` toggle, the same one `GitNexus › Embeddings` uses. `commit()` lowercases bools into `--set` strings. Every other `LLMConfig` consumer (resolve, reconcile, watch, taskmodels, start/implement/review, slug, probe) reads `default`/`providers`/`docs` by name. **No menu code is needed.**
- Explicit branch vs shared bool helper: keep **one explicit branch** in `set_key` and one check in `_hydrate`, mirroring `gitnexus.embeddings`. A helper would mean touching the two working bool leaves for three two-line checks whose messages differ by path (grug factor-late). The record leaves this to the plan.
- `asdict(GlobalConfig())` is pinned exactly in `test_schema_menu_metadata_does_not_change_persisted_defaults`, and `set(llm)` is pinned exactly in the menu test. Both grow.

- [x] **Step 1: Write the failing tests.**

In `tests/unit/test_config_store.py`, add `"provider_selection_per_step": False,` to the `"llm": {…}` dict of `test_schema_menu_metadata_does_not_change_persisted_defaults`, after `"providers": {…}`. Then append this block after `test_load_effective_carries_gitnexus_from_the_global_file`:

```python
# --- llm.provider_selection_per_step (spec 2026-11-12 §4.2) ---


def test_provider_selection_per_step_defaults_off_everywhere(tmp_path):
    from omc.config.schema import Config

    assert LLMConfig().provider_selection_per_step is False
    assert GlobalConfig().llm.provider_selection_per_step is False
    assert Config().llm.provider_selection_per_step is False
    # D7: a config.yaml written before the option existed hydrates to off
    (tmp_path / "config.yaml").write_text("schema_version: 1\nllm:\n  default: claude\n")
    assert store.load_global(tmp_path).llm.provider_selection_per_step is False


def test_provider_selection_per_step_round_trips_and_is_typed(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.provider_selection_per_step = True
    store.save_global(tmp_path, cfg)
    assert "provider_selection_per_step: true" in (tmp_path / "config.yaml").read_text()
    assert store.load_global(tmp_path).llm.provider_selection_per_step is True


@pytest.mark.parametrize("value", ['"false"', "1", "null", "[true]"])
def test_provider_selection_per_step_wrong_type_is_a_config_error(tmp_path, value):
    (tmp_path / "config.yaml").write_text(f"llm:\n  provider_selection_per_step: {value}\n")
    with pytest.raises(ConfigError, match="llm.provider_selection_per_step"):
        store.load_global(tmp_path)


def test_set_provider_selection_per_step_accepts_exactly_true_false():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.provider_selection_per_step", "true")
    assert cfg.llm.provider_selection_per_step is True
    store.set_key(cfg, "llm.provider_selection_per_step", "false")
    assert cfg.llm.provider_selection_per_step is False
    for value in ("yes", "1", "", "True", "on"):
        with pytest.raises(
            ConfigError, match="llm.provider_selection_per_step expects true or false"
        ):
            store.set_key(cfg, "llm.provider_selection_per_step", value)
    assert cfg.llm.provider_selection_per_step is False
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "llm.provider_selection_per_step.extra", "true")


def test_legacy_config_carries_provider_selection_per_step(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "llm": {"default": "claude", "provider_selection_per_step": true}}'
    )
    gcfg, _ = store.load_legacy(tmp_path)
    assert gcfg.llm.provider_selection_per_step is True
```

In `tests/unit/test_configure_menu.py`, change the exact set in `test_menu_groups_schema_fields_and_registry_providers` to `assert set(llm) == {"Default provider", "Provider Selection Per Step", "claude", "codex"}`. Then add:

```python
def test_provider_selection_per_step_toggle_saves_only_the_global_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (ctx, gcfg, _, _) = _menu(tmp_path, root=repo)
    tag = session.root["LLM"]["Provider Selection Per Step"]
    assert tag.val is False
    assert tag.description == (
        "Stop after each stage to offer another provider; "
        "off = continue automatically through to the push."
    )
    assert tag.update(True)
    assert gcfg.llm.provider_selection_per_step is True  # in-memory copy-back
    assert store.load_global(ctx.home).llm.provider_selection_per_step is True
    assert not store.project_config_path(repo).exists()
    assert session.flags == (True, False, False)
    assert tag.update(False)
    assert gcfg.llm.provider_selection_per_step is False
    assert store.load_global(ctx.home).llm.provider_selection_per_step is False
```

In `tests/unit/test_configure.py`, add (use the same `_home` / `_repo` helpers as the embeddings test):

```python
def test_configure_set_provider_selection_per_step_writes_only_the_global_file(
    tmp_path, monkeypatch
):
    home = _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.provider_selection_per_step=true"]) == 0
    assert store.load_global(home).llm.provider_selection_per_step is True
    assert not store.project_config_path(repo).exists()  # personal, never project
    assert main(["configure", "--set", "llm.provider_selection_per_step=false"]) == 0
    assert store.load_global(home).llm.provider_selection_per_step is False
    assert main(["configure", "--set", "llm.provider_selection_per_step=yes"]) == 1
    assert store.load_global(home).llm.provider_selection_per_step is False
```

- [x] **Step 2: Run to see them fail.**

Run: `uv run --frozen python -m pytest tests/unit/test_config_store.py tests/unit/test_configure_menu.py tests/unit/test_configure.py -q -k "provider_selection or persisted_defaults or menu_groups"`
Expected: FAIL. Look for `AttributeError: 'LLMConfig' object has no attribute 'provider_selection_per_step'`, `KeyError: 'Provider Selection Per Step'`, the `asdict` mismatch, and the menu `set(llm)` mismatch.

- [x] **Step 3: Implement.**

`src/omc/config/schema.py`, inside `LLMConfig` after `providers`:

```python
    # Spec 2026-11-12: off (the default) lets the lifecycle conductors continue
    # design → implement → audit → finish → push without a typed word per stage.
    provider_selection_per_step: bool = field(
        default=False,
        metadata={
            "label": "Provider Selection Per Step",
            "help": "Stop after each stage to offer another provider; "
            "off = continue automatically through to the push.",
        },
    )
```

`src/omc/config/store.py::set_key`: add this right after the `if isinstance(cfg, LLMConfig) and head == "docs":` block. It must come before the generic fallback, which would `setattr` the truthy string `"false"`:

```python
    if isinstance(cfg, LLMConfig) and head == "provider_selection_per_step":
        if tail:
            raise ConfigError(f"unknown config key: provider_selection_per_step.{tail}")
        if value not in ("true", "false"):
            raise ConfigError("llm.provider_selection_per_step expects true or false")
        cfg.provider_selection_per_step = value == "true"
        return
```

`src/omc/config/store.py::_hydrate`: change `if cls is LLMConfig:` to:

```python
    if cls is LLMConfig:
        if not isinstance(obj.provider_selection_per_step, bool):
            raise ConfigError(
                f"invalid llm.provider_selection_per_step in {path}: expected true/false"
            )
        validate_llm(obj)
```

No change to `src/omc/configure.py`.

- [x] **Step 4: Run to see them pass.** Rerun the Step 2 command (expect PASS). Then run the whole files: `uv run --frozen python -m pytest tests/unit/test_config_store.py tests/unit/test_configure_menu.py tests/unit/test_configure.py tests/unit/test_taskmodels.py -q`. Expected: green. `test_configure_pty` failures are host baseline and not in these files. Then `uvx ruff format src/omc/config tests/unit/test_config_store.py tests/unit/test_configure_menu.py tests/unit/test_configure.py && uvx ruff check src tests`.

- [x] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null; git add src/omc/config/schema.py src/omc/config/store.py tests/unit/test_config_store.py tests/unit/test_configure_menu.py tests/unit/test_configure.py
git commit -m "Add llm.provider_selection_per_step setting (off by default)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: `OMC_MODELS` carries the flag

Complexity: simple

**Files:**
- Modify: `src/omc/internal.py::_models` (~line 495)
- Test: `tests/unit/test_taskmodels.py::test_internal_models_verdict_and_refusals` (~line 640)

**Interfaces:**
- Consumes: `cfg.llm.provider_selection_per_step` (Task 1).
- Produces: the ok payload `{"ok": true, "provider": …, "tasks": {…}, "provider_selection_per_step": <bool>}`. The `"ok": false` payload stays `{"ok": false, "message": …}`. Tasks 3–5 rely on the key name.

Pressure-test notes: `_models` loads the same `GlobalConfig` (`store.load_global(ctx.home)`) the leaf lives in. `OMC_PROVIDER` changes only `provider`, not the flag. The Python parsers of the line are tests only. Every one indexes keys except `tests/e2e/variations/test_from_start.py` and its unit stub, which compare the whole dict (fixed in Task 7). The skills read `tasks.*` by key.

- [x] **Step 1: Write the failing test.** In `test_internal_models_verdict_and_refusals`, change the import to `from omc.config.store import save_global, set_key`. Then:
  - After the first refusal (`assert json.loads(line.split(" ", 1)[1])["ok"] is False`), add `assert "provider_selection_per_step" not in json.loads(line.split(" ", 1)[1])`.
  - After the `payload["tasks"] == {…}` assertion, add:

```python
    assert payload["provider_selection_per_step"] is False
    flagged = GlobalConfig()
    set_key(flagged, "llm.provider_selection_per_step", "true")
    save_global(tmp_path / "omc", flagged)
    assert run_internal(["models"]) == 0
    flagged_payload = json.loads(capsys.readouterr().out.strip().split(" ", 1)[1])
    assert flagged_payload["provider_selection_per_step"] is True
```

  - After `assert payload["ok"] is False and "codex debug models" in payload["message"]`, add `assert "provider_selection_per_step" not in payload`.

- [x] **Step 2: Run to see it fail.** `uv run --frozen python -m pytest tests/unit/test_taskmodels.py -q -k internal_models`. Expected: FAIL with `KeyError: 'provider_selection_per_step'`.

- [x] **Step 3: Implement.** In `_models`, the ok payload becomes:

```python
        payload = {
            "ok": True,
            "provider": name,
            "tasks": {
                task: {"model": choice.model_arg, "effort": choice.effort}
                for task, choice in choices.items()
            },
            # Lifecycle conductors read this (spec 2026-11-12 §4.3): only an
            # explicit false auto-advances; a missing key stops as before.
            "provider_selection_per_step": cfg.llm.provider_selection_per_step,
        }
```

- [x] **Step 4: Run to see it pass.** Run the Step 2 command, then `uv run --frozen python -m pytest tests/unit/test_taskmodels.py -q`. Then `uvx ruff format src/omc/internal.py tests/unit/test_taskmodels.py && uvx ruff check src tests`.

- [x] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null; git add src/omc/internal.py tests/unit/test_taskmodels.py
git commit -m "OMC_MODELS reports provider_selection_per_step

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: `design` continues into `/omc:implement` when the setting is off

Complexity: medium

**Files:**
- Modify: `skills/design/SKILL.md` (frontmatter, intro paragraph, Step 0, Step 5, Completion contract)
- Test: `tests/unit/test_plugin_manifests.py` (`test_design_skill_contract` ~line 1709; `test_design_is_user_facing_and_owns_the_record` ~line 1753)

**Interfaces:**
- Consumes: the `OMC_MODELS` field (Task 2); the shared reading rule and notice (Global Constraints).
- Produces: design invokes `/omc:implement` (Task 5's implement accepts that caller).

Pressure-test notes: a user-facing conductor already invokes another user-facing one. Implement Phase 1 says "Always invoke `/omc:write-plan implement` as a black box", and audit says "Invoke `finish` (`/omc:finish`) and follow its full flow". Reuse that phrasing, with no new mechanism. Design Step 1 already runs `omc internal models` unconditionally (count stays 1), so Step 5 reuses that verdict.

- [x] **Step 1: Write the failing tests.** Append to the end of `test_design_skill_contract`:

```python
    # provider selection per step (spec 2026-11-12 §4.4)
    for needle in (
        "provider_selection_per_step",
        "Provider Selection Per Step",
        "Only an `OMC_MODELS` verdict with `\"ok\": true` and "
        "`\"provider_selection_per_step\": false` continues; a missing field or any "
        "other value stops as below.",
        "> Provider selection per step is off — continuing to `/omc:implement`\n"
        "> (turn on: `omc configure` → LLM → Provider Selection Per Step).",
    ):
        assert needle in text, f"design skill missing {needle!r}"
    assert text.count("omc internal models") == 1  # Step 1's read is the flag read
    close = text[text.index("## Step 5") : text.index("## Completion contract")]
    assert "invoke `/omc:implement`" in close
    assert "Provider selection per step is off" in close
    # the on-branch statement is unchanged
    assert "> The design record is committed. To plan and build on this provider, type" in close
    step0 = text[text.index("## Step 0") : text.index("## Step 1")]
    assert "continue into `/omc:implement` when provider selection per step is off" in step0
    # the chain goes through implement, never straight to audit
    assert "/omc:audit" not in text and "$omc:audit" not in text
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "two authority words" not in m.group(1)
    assert "with provider selection per step on" in m.group(1)
    assert "off (the default) continues into the next stage" in m.group(1)
```

In `test_design_is_user_facing_and_owns_the_record`, keep the existing asserts and add `assert "`/omc:implement` has been invoked" in text`.

- [x] **Step 2: Run to see them fail.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -k "design_skill_contract or design_is_user_facing or conductors"`. Expected: the two design tests FAIL on `provider_selection_per_step`; `conductors` passes.

- [x] **Step 3: Edit `skills/design/SKILL.md`.**
  - **Frontmatter** (no `": "`; no `Invoke the`): `description: Write the design record from a converged brainstorm, harden it section by section with /omc:explain and the grug lens, and commit it; with provider selection per step on it then stops with the session open, off (the default) continues into the next stage (/omc:implement). Type it when the brainstorm has converged; it is the first of the three authority words.`
  - **Intro**: replace "It never plans, builds or pushes. When it ends, the session stays open and the user chooses how to continue." with: "By itself it never plans, builds or pushes. With provider selection per step on, the session stays open when it ends and the user chooses how to continue. With it off (the default), its close continues into `/omc:implement`, which carries the run to the push (Step 5)." Do not write "invoke the" here.
  - **Step 0**: the task list ends "… → commit and report → close: continue into `/omc:implement` when provider selection per step is off, otherwise state the three continuations and wait." Replace "The last task is literally to wait:" with "The last task is the close; with the setting on it is literally to wait:" and keep the rest of the sentence. One task, resolved by the verdict Step 1 reads; the chained phases are externalized by implement itself.
  - **Step 1**: after "An `"ok": false` verdict is a blocker; report its `message`." add: "Keep this verdict; its `provider_selection_per_step` field decides Step 5's close."
  - **Step 5**: rename the heading to `## Step 5 — commit, report, then continue or wait`. Keep the commit paragraph. Extend the grug-unavailable sentence: "…is a hard stop in both settings: do NOT commit, do NOT report success and do NOT continue". Then replace "Then post a short summary … and end with this statement — …:" plus the blockquote with:

    ```markdown
    Then post a short summary of what hardening found and changed, and close by
    the `OMC_MODELS` verdict Step 1 read. Only an `OMC_MODELS` verdict with `"ok": true` and `"provider_selection_per_step": false` continues; a missing field or any other value stops as below.

    - **Provider selection per step off** — print this one notice line, then
      invoke `/omc:implement` as a black box in this same turn (its record gate
      passes on the record just committed). State no continuations.

    > Provider selection per step is off — continuing to `/omc:implement`
    > (turn on: `omc configure` → LLM → Provider Selection Per Step).

    On Codex the command is spelled `$omc:implement`.

    - **Otherwise** — end with this statement, a statement and not a question;
      no approval is requested:

    > The design record is committed. To plan and build on this provider, type
    > … (the existing five-line blockquote, unchanged)
    ```

    Put the reading-rule sentence on ONE source line so the exact needle matches. Keep both blockquotes at column 0.
  - **Completion contract**: "`design` is complete when the record is committed and either the three continuations have been stated (provider selection per step on) or `/omc:implement` has been invoked (off). With the setting on, waiting for the user's later direct implementation handoff is a valid stop (behavior layer: …unchanged quote…). Agreement, `ok`, or praise is not that handoff. Do not plan, build, or push from this skill itself; with the setting off, `/omc:implement` does that work."
  - Never write `/omc:audit`, `$omc:audit` or `under /omc:implement` anywhere in this file.

- [x] **Step 4: Run to see them pass.** Run the Step 2 command, then `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q`. Expected: all green, including `test_task_model_skill_routing_contract` and `test_conductors_externalize_their_steps_first`.

- [x] **Step 5: Commit.**

```bash
git add skills/design/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "design continues into /omc:implement when provider selection per step is off

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: `write-plan` reads the flag in Step 1 and continues in direct use

Complexity: medium

**Files:**
- Modify: `skills/write-plan/SKILL.md` (frontmatter, intro, Step 1, Step 2 tracked branch, Step 3 opening, Step 5 direct use, Completion contract)
- Test: `tests/unit/test_plugin_manifests.py` (`test_write_plan_skill_contract` ~line 1318)

**Interfaces:**
- Consumes: the `OMC_MODELS` field (Task 2); the shared reading rule and notice.
- Produces: in direct use with the flag off, write-plan invokes `/omc:implement`. Implement's Phase 1 calls `/omc:write-plan implement` back in caller mode, finds the plan just committed (Step 2, tracked → return the path) and never re-plans (D8).

Pressure-test notes: today Step 3 reads `OMC_MODELS` only when a Plan worker is needed, so the tracked and untracked paths never see the flag. Moving the single read to Step 1 keeps `test_write_plan_skill_contract`'s order (record gate → models → `writing-plans` → plan file only). It also keeps `test_task_model_skill_routing_contract` ("omc internal models" in write-plan). Caller mode must never invoke implement, because implement is its caller and that would recurse.

- [x] **Step 1: Write the failing tests.** Append to `test_write_plan_skill_contract`:

```python
    # provider selection per step (spec 2026-11-12 §4.4, D8)
    for needle in (
        "provider_selection_per_step",
        "Only an `OMC_MODELS` verdict with `\"ok\": true` and "
        "`\"provider_selection_per_step\": false` continues; a missing field or any "
        "other value stops as below.",
        "> Provider selection per step is off — continuing to `/omc:implement`\n"
        "> (turn on: `omc configure` → LLM → Provider Selection Per Step).",
    ):
        assert needle in text, f"write-plan skill missing {needle!r}"
    assert text.count("omc internal models") == 1
    step1 = text.split("## Step 1 — record gate", 1)[1].split("## Step 2", 1)[0]
    step3 = text.split("## Step 3 — plan", 1)[1].split("## Step 4", 1)[0]
    assert "omc internal models" in step1  # one unconditional read per run
    assert "omc internal models" not in step3 and "tasks.plan.model" in step3
    close = text.split("## Step 5 — close", 1)[1].split("## Completion contract", 1)[0]
    direct, caller = close.split("**Caller mode**", 1)
    assert "invoke `/omc:implement`" in direct
    assert "Provider selection per step is off" in direct
    assert "invoke `/omc:implement`" not in caller and "return the plan path" in caller
    assert "/omc:audit" not in text and "$omc:audit" not in text
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "with provider selection per step on" in m.group(1)
    assert "off (the default) continues into the next stage" in m.group(1)
```

The existing exact four-line blockquote assertion (`> The plan is committed. To build on this provider…`) and the existing `order` list stay as they are.

- [x] **Step 2: Run to see it fail.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -k "write_plan or routes_the_plan or routing"`. Expected: `test_write_plan_skill_contract` FAILS on `provider_selection_per_step`. The other two pass.

- [x] **Step 3: Edit `skills/write-plan/SKILL.md`.**
  - **Frontmatter**: `description: Write, pressure-test (via /omc:explain) and commit the implementation plan for this slug from the committed design record; with provider selection per step on it then stops with the session open, off (the default) continues into the next stage (/omc:implement). Optional step between /omc:design and /omc:implement; /omc:implement also invokes it when no plan exists.`
  - **Intro**: replace "When it ends, the session stays open and the user chooses how to continue." with "In direct use with provider selection per step on, the session stays open when it ends and the user chooses how to continue; with it off (the default), its close continues into `/omc:implement` (Step 5)." Do not write "invoke the".
  - **Step 0**: the close task becomes "close (direct use: summary, then continue into `/omc:implement` when provider selection per step is off, otherwise the continuations; caller mode: return the plan path)".
  - **Step 1**: after the `$OMC_SLUG` sentence, add: "Then run `omc internal models` once and read its single `OMC_MODELS` verdict, in both modes. An `"ok": false` verdict is a blocker; report its `message`. Step 3's Plan worker and Step 5's close use this verdict."
  - **Step 2, tracked**: replace "state the Step 5 continuations (caller mode: return the plan path)" with "close as Step 5 does (direct use: continue or state the continuations by the flag; caller mode: return the plan path)". Keep "write nothing" and the rest.
  - **Step 3**: delete its first two sentences ("Run `omc internal models` once … report its `message`."). Start with "Using the Step 1 `OMC_MODELS` verdict, dispatch a Plan worker with `tasks.plan.model` and, where supported, `tasks.plan.effort`." The rest is unchanged.
  - **Step 5, direct use**: after "Post a short summary of the plan (task count, the pressure-test findings that changed it)", insert the reading-rule sentence on one line, then:
    - **Provider selection per step off** — the notice blockquote (`/omc:implement`), the Codex spelling sentence, then "invoke `/omc:implement` as a black box in this same turn; its Phase 1 calls `/omc:write-plan implement`, which finds this committed plan and returns its path without re-planning. State no continuations."
    - **Otherwise** — "end with this statement — a statement, not a question; no approval is requested:" followed by the existing blockquote unchanged.
  - **Caller mode**: unchanged. Add one sentence: "Caller mode never invokes `/omc:implement`, whatever the flag: the caller is implement." Careful: this adds the literal `/omc:implement` but not `invoke \`/omc:implement\`` in the caller paragraph. Phrase it exactly as given so the test's negative holds.
  - **Completion contract**: "`write-plan` is complete when the plan is committed and, in direct use, either the two continuations have been stated (provider selection per step on) or `/omc:implement` has been invoked (off). With the setting on, waiting for the user's next command is a valid stop. Agreement, `ok`, or praise is not `/omc:implement`. Do not build or push from this skill itself."

- [x] **Step 4: Run to see it pass.** Run the Step 2 command, then `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q`.

- [x] **Step 5: Commit.**

```bash
git add skills/write-plan/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "write-plan reads OMC_MODELS up front and continues into implement when the setting is off

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: `implement` continues into `/omc:audit`; audit names its third caller

Complexity: medium

**Files:**
- Modify: `skills/implement/SKILL.md` (frontmatter, intro, Phase -1, Phase 2 models paragraph, Phase 2b red-gate sentence, Phase 3, Completion contract)
- Modify: `skills/audit/SKILL.md` (opening sentence only)
- Test: `tests/unit/test_plugin_manifests.py` (`test_implement_skill_contract` ~line 1273; `test_audit_skill_contract` ~line 1445)

**Interfaces:**
- Consumes: the `OMC_MODELS` field (Task 2) via Phase 2's existing read; the shared reading rule and notice; the callers from Tasks 3–4.
- Produces: implement invokes `/omc:audit`. Audit's phases and contract are unchanged (D10).

Pressure-test notes: implement's `order` list indexes the first `/omc:write-plan` and `omc internal models`. The intro and Phase -1 must not use the `/omc:write-plan` literal, and Phase 3 must not re-run `omc internal models` (count stays 1). Audit's Phase 0 "nothing to audit" gate cannot fire after Phase 3's task-commit check. A red milestone gate already blocks Phase 3, so a chained run never reaches audit on red.

- [x] **Step 1: Write the failing tests.** Append to `test_implement_skill_contract` (keep its `order` list and needles unchanged):

```python
    # provider selection per step (spec 2026-11-12 §4.4, D9)
    for needle in (
        "provider_selection_per_step",
        "Only an `OMC_MODELS` verdict with `\"ok\": true` and "
        "`\"provider_selection_per_step\": false` continues; a missing field or any "
        "other value stops as below.",
        "> Provider selection per step is off — continuing to `/omc:audit`\n"
        "> (turn on: `omc configure` → LLM → Provider Selection Per Step).",
    ):
        assert needle in text, f"implement skill missing {needle!r}"
    assert text.count("omc internal models") == 1  # Phase 2's read is the flag read
    handoff = text.split("## Phase 3 — hand off", 1)[1].split("## Completion contract", 1)[0]
    assert "invoke `/omc:audit`" in handoff
    # the on-branch statement is unchanged and still names all three ways
    assert "> The implementation is committed on this branch and not yet published." in handoff
    for word in ("/omc:audit", "omc review", "/omc:finish"):
        assert word in handoff
    phase_m1 = text.split("## Phase -1", 1)[1].split("## Phase 0", 1)[0]
    assert "continue into `/omc:audit` when provider selection per step is off" in phase_m1
    contract = text.split("## Completion contract", 1)[1]
    assert "`/omc:audit` has been invoked" in contract and "omc implement" in contract
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "with provider selection per step on" in m.group(1)
    assert "off (the default) continues into the next stage" in m.group(1)
```

Append to `test_audit_skill_contract`:

```python
    intro = " ".join(text.split("## Phase -1", 1)[0].split())
    assert (
        "or by `/omc:implement` continuing in the same turn with provider selection per step off"
        in intro
    )
```

- [x] **Step 2: Run to see them fail.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -k "implement or audit_skill_contract or conductors"`. Expected: `test_implement_skill_contract` and `test_audit_skill_contract` FAIL on the new needles.

- [x] **Step 3: Edit the skills.**
  - **`skills/implement/SKILL.md` frontmatter**: `description: Lifecycle conductor from a committed design record to an unpublished implementation - plan (pressure-tested via explain), subagent build, handoff; with provider selection per step on it stops there, off (the default) continues into the next stage (/omc:audit). Type it after /omc:design has committed the record, in the design session or in a fresh one launched by `omc implement`.` (no `/omc:write-plan`, no `": "`).
  - **Intro**: after "…in this worktree." insert: "With provider selection per step off (the default), `/omc:design` or a directly typed write-plan invokes it in the same turn; the committed record plus that setting carry the same authority as the typed word (behavior layer)." Write "write-plan" here, NOT `/omc:write-plan` (order trap). The rest of the intro ("Four phases, strictly in order…", the authority sentences) is unchanged.
  - **Phase -1**: "plan → subagent build → milestone gate → handoff: continue into `/omc:audit` when provider selection per step is off, otherwise state the three continuations and wait."
  - **Phase 2**: after "An `"ok": false` verdict is a blocker; report its `message`." add "Keep this verdict; its `provider_selection_per_step` field decides Phase 3's close."
  - **Phase 2b**: after "Do not enter Phase 3 or make a handoff commit while the gate is red." add "With provider selection per step off too: a red gate never reaches `/omc:audit`."
  - **Phase 3**: keep the commit, drift-restore and clean-tree paragraphs. Replace "Post a short implementation summary and state this continuation verbatim as a statement, not a question:" plus its blockquote with:

    ```markdown
    Post a short implementation summary, then close by the `OMC_MODELS` verdict
    Phase 2 read. Only an `OMC_MODELS` verdict with `"ok": true` and `"provider_selection_per_step": false` continues; a missing field or any other value stops as below.

    - **Provider selection per step off** — print this one notice line, then
      invoke `/omc:audit` as a black box in this same turn. Its Phase 0
      "nothing to audit" gate cannot fire after the task-commit check above.
      State no continuations.

    > Provider selection per step is off — continuing to `/omc:audit`
    > (turn on: `omc configure` → LLM → Provider Selection Per Step).

    On Codex the command is spelled `$omc:audit`.

    - **Otherwise** — state this continuation verbatim as a statement, not a
      question:

    > The implementation is committed on this branch and not yet published. To
    > … (the existing five-line blockquote, unchanged)
    ```

  - **Completion contract**: "`implement` is complete when the branch is committed, its working tree is clean, and either the three continuations have been stated (provider selection per step on) or `/omc:audit` has been invoked (off). This holds in a fresh session seeded by `omc implement` too. With the setting on, waiting for the audit handoff is a valid stop. Before ending the turn, read the task list: a pending phase means continue, not stop."
  - **`skills/audit/SKILL.md`**: the first sentence becomes "Invoked directly (`/omc:audit` in Claude, `$omc:audit` in Codex), by the bare `/omc:audit` seed from `omc review`, or by `/omc:implement` continuing in the same turn with provider selection per step off." Nothing else in audit changes.

- [x] **Step 4: Run to see them pass.** Run the Step 2 command, then `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q`. Expected: all green, including `test_implement_always_routes_the_plan_through_write_plan`, `test_implement_keeps_task_commits_before_a_red_milestone_gate`, `test_publish_route_squashes_before_rebasing` and `test_conductors_externalize_their_steps_first`.

- [x] **Step 5: Commit.**

```bash
git add skills/implement/SKILL.md skills/audit/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "implement continues into /omc:audit when provider selection per step is off

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: Behavior layer names the setting

Complexity: simple

**Files:**
- Modify: `src/omc/distribution/AGENTS.md` ("Lifecycle scope is binding" bullet; "Externalize a composed flow" bullet)
- Test: `tests/unit/test_plugin_manifests.py::test_behavior_layer_names_three_authority_words` (~line 1914)

Pressure-test notes: the layer is rendered fresh into each harness's global file by `agentsmd.ensure_global_section`. Its callers are configure, start, implement, review and `omc internal global-instructions`, plus `omc update`. There is no hash pin and no size cap. `tests/unit/test_agentsmd.py` checks only the scope and project-pointer wording. The repo-root `CLAUDE.md`/`AGENTS.md` are symlinks into the installed package; do not edit them. The existing needle "write-plan lists end at the committed plan" is kept by adding the off-branch to the existing sentence instead of using the record's shorter rewording. The meaning is the same and the existing assertion stays.

- [x] **Step 1: Write the failing test.** Append to `test_behavior_layer_names_three_authority_words`:

```python
    # provider selection per step (spec 2026-11-12 §4.5)
    assert "`llm.provider_selection_per_step`" in flat
    assert "exactly as if the user had typed each word" in flat
    assert "With it on, each word must be typed." in flat
    lifecycle = flat.split("Lifecycle scope is binding", 1)[1].split("Worktrees are snapshots", 1)[0]
    assert (
        lifecycle.index("launch fresh seeded sessions")
        < lifecycle.index("llm.provider_selection_per_step")
        < lifecycle.index("Agreement or `ok` is none of these invocations")
    )
    assert "Three direct user invocations carry authority, in order" in lifecycle
    ext = " ".join(externalize.split())
    assert "at the invoked `/omc:implement` when provider selection per step is off" in ext
    assert "or at the invoked `/omc:audit` when it is off" in ext
    assert "write-plan lists end at the committed plan" in ext  # existing needle kept
```

- [x] **Step 2: Run to see it fail.** `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -k "behavior_layer or distribution_agents or publish_route or contract_listings"`. Expected: `test_behavior_layer_names_three_authority_words` FAILS. The others pass.

- [x] **Step 3: Edit `src/omc/distribution/AGENTS.md`.**
  - In "Lifecycle scope is binding", after "…launch fresh seeded sessions in the worktree for the second and third words." and before "Agreement or `ok` is none of these invocations.", insert the §4.5 sentence verbatim:
    "With `llm.provider_selection_per_step` off (the default; `omc configure` → LLM → Provider Selection Per Step), a committed design record carries the implement and audit authority forward: after `/omc:design`, or a directly typed `/omc:write-plan` or `/omc:implement`, the session continues through `/omc:implement` and `/omc:audit` to publication exactly as if the user had typed each word, and the skills say so in one line as they continue. With it on, each word must be typed."
  - In "Externalize a composed flow", the endings become: "Start/plan lists end at discussion and waiting for /omc:design; design lists end at the committed record and the stated continuations, write-plan lists end at the committed plan and the stated continuations — or, for both, at the invoked `/omc:implement` when provider selection per step is off; implementation lists end at the committed branch and the three stated continuations, or at the invoked `/omc:audit` when it is off; audit lists run through finish."
  - Re-wrap both bullets to the file's ~78-column style. Do not touch the `omc design <context>` data clause, "Three direct user invocations carry authority, in order", or "squash, rebase".

- [x] **Step 4: Run to see it pass.** Run the Step 2 command, then `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_agentsmd.py -q`.

- [x] **Step 5: Commit.**

```bash
git add src/omc/distribution/AGENTS.md tests/unit/test_plugin_manifests.py
git commit -m "Behavior layer: provider selection per step carries implement/audit authority forward

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7: E2E harness pins the setting on; exact `OMC_MODELS` verdict

Complexity: simple

**Files:**
- Modify: `tests/e2e/harness.py::configure_omc` (~line 229)
- Modify: `tests/e2e/variations/test_from_start.py::test_seeded_session_reports_pinned_task_models` (~line 39, the exact `verdict == {…}`)
- Test: `tests/unit/test_e2e_model_fixture.py` (`test_shared_fixture_pins_all_seven_choices`, `test_seeded_model_variation_uses_resumed_session`)

**Interfaces:**
- Produces: every E2E container is configured with `llm.provider_selection_per_step=true` (D11). Golden snapshots inherit it, so `recorded`, `implemented`, `audited`, every variation and the monolithic runs keep today's stops. Task 8's variation overrides it to `false` in its own container.

Pressure-test notes: `test_shared_fixture_pins_all_seven_choices` pins `configure_omc`'s exact argv list, so it is the token-free red test for the harness edit. `test_e2e_codex_auth.py` only checks the first exec and the absence of "claude" in argv, so it is unaffected. The only exact-dict `OMC_MODELS` assertion in the suite is `variations/test_from_start.py`. Its unit twin feeds it a stubbed verdict, so the twin is the red test for that edit. `test_e2e_task_models.py` indexes keys and needs no change.

- [x] **Step 1: Write the failing tests.** In `tests/unit/test_e2e_model_fixture.py`:
  - `test_shared_fixture_pins_all_seven_choices`: the first expected argv becomes `["omc", "configure", "--set", f"llm.default={provider}", "--set", "llm.provider_selection_per_step=true"]`.
  - `test_seeded_model_variation_uses_resumed_session`: add `"provider_selection_per_step": True,` to the stub `verdict` dict (the E2E container runs with the harness pin).

- [x] **Step 2: Run to see them fail.** `uv run --frozen python -m pytest tests/unit/test_e2e_model_fixture.py -q`. Expected: both FAIL. One fails on the argv list mismatch. The other fails with an `AssertionError` from `test_from_start`'s exact verdict comparison.

- [x] **Step 3: Implement.**
  - `tests/e2e/harness.py::configure_omc`: the first configure call becomes:

```python
    # Every E2E container keeps today's per-stage stops (spec 2026-11-12 D11);
    # the one chain variation turns the setting off in its own container.
    rc, out = run_in(
        container,
        [
            "omc",
            "configure",
            "--set",
            f"llm.default={provider}",
            "--set",
            "llm.provider_selection_per_step=true",
        ],
    )
```

  - `tests/e2e/variations/test_from_start.py`: the expected dict gains `"provider_selection_per_step": True,` after `"provider": "claude",`.

- [x] **Step 4: Run to see them pass.** Run the Step 2 command (expect PASS). Then run `uv run --frozen python -m pytest tests/unit/test_e2e_codex_auth.py tests/unit/test_e2e_ticket_evidence.py tests/unit/test_e2e_global_instructions.py -q` (no new failures). Then `OMC_E2E_NO_DOCKER=1 uv run --frozen python -m pytest --collect-only -q tests/e2e/variations/test_from_start.py tests/e2e/golden/test_golden_claude.py` (collects cleanly). Then `uvx ruff format tests/e2e/harness.py tests/e2e/variations/test_from_start.py tests/unit/test_e2e_model_fixture.py && uvx ruff check tests`.

- [x] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null; git add tests/e2e/harness.py tests/e2e/variations/test_from_start.py tests/unit/test_e2e_model_fixture.py
git commit -m "E2E: pin provider selection per step on; OMC_MODELS carries the flag

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: Expensive chain variation from `agreed`

Complexity: high

**Files:**
- Modify: `tests/e2e/variations/test_from_agreed.py` (new expensive variation; imports)
- Modify: `justfile` (`lifecycle-full` recipe line and its comment)
- Test: `tests/unit/test_lifecycle_command.py` (`FULL`; one new negative pin)

**Interfaces:**
- Consumes: the harness pin (Task 7), overridden in-container. Uses `stage_session` (`(container, session, manifest)`; manifest keys `repo`, `worktree`, `branch`, `slug`, `model`, `judge_model`). Helpers from `tests/e2e/lifecycle_helpers.py`: `_direct_design(provider, detail="")`, `_assert_implement_stage_order(markers)`, `_assert_finish_stage_order(markers)`, `_assert_published_fix(container, repo, worktree, branch, old_source)`.

Pressure-test notes (verified in `justfile` / `scripts/e2e.sh`):
- `run_rest` passes `-m "e2e and not golden and not expensive …"` and the caller's args come last, so a recipe's own `-m` wins. `lifecycle-full` selects `-m "e2e and expensive"` over two **named files** (`test_e2e_lifecycle_full.py`, `variations/test_codex_handoff.py`). A new expensive test in `test_from_agreed.py` would be collected by no recipe.
- The record allows either pinning it in `FULL` or excluding it. Pinning it makes the default behaviour's only E2E proof runnable from a named recipe, so the plan **adds `tests/e2e/variations/test_from_agreed.py` to `lifecycle-full`**. With `-m "e2e and expensive"`, only the new test in that file is collected, because its siblings are not `expensive`. `test_codex_handoff.py` already makes `lifecycle-full` depend on golden snapshots, so there is precedent.
- `_assert_audited_artifacts` is not reusable because it diffs an `implemented` snapshot that already holds the record. Assert the artifacts directly and reuse the two stage-order helpers and `_assert_published_fix`.
- The budget comes from `test_e2e_lifecycle_full.py`: design 900 + implement 1800 + audit 1800 = 4500 s, as one turn. The pytest timeout is 4800, which leaves room for the snapshot and artifact checks. Only the `expensive` tier may override the 300 s ceiling.

- [x] **Step 1: Write the failing unit pins.** In `tests/unit/test_lifecycle_command.py`:
  - Add `"test_design_auto_advances_to_push",` to `FULL`.
  - Add:

```python
def test_default_e2e_never_collects_the_auto_advance_chain():
    # one /omc:design turn that runs to the push: expensive tier only
    assert "test_design_auto_advances_to_push" not in _collected("e2e-tests")
```

- [x] **Step 2: Run to see them fail.** `uv run --frozen python -m pytest tests/unit/test_lifecycle_command.py -q`. Expected: `test_lifecycle_full_collects_the_monolithic_runs` FAILS (the set lacks `test_design_auto_advances_to_push`). The new negative pin passes, which is correct because nothing collects it yet. If the whole file fails for a host reason (baseline), capture the output and judge by the collect-only commands in Step 4.

- [x] **Step 3: Implement.**
  - `justfile`: the `lifecycle-full` comment becomes "# The monolithic lifecycle cases, the auto-advance chain from `agreed` and both cross-provider handoffs (expensive tier):". Append `tests/e2e/variations/test_from_agreed.py` to its file list, before `{{args}}`.
  - `tests/e2e/variations/test_from_agreed.py`: extend the helpers import with `_assert_finish_stage_order`, `_assert_implement_stage_order` and `_assert_published_fix`. Append:

```python
# Spec 2026-11-12 D1/D11: with provider selection per step off, ONE /omc:design
# turn runs design → implement → audit → finish → push. Budget is the
# monolithic run's design 900 + implement 1800 + audit 1800 s, as one turn.
CHAIN_TURN_BUDGET = 4500


@pytest.mark.expensive
@pytest.mark.timeout(4800)
@pytest.mark.variation("agreed")
def test_design_auto_advances_to_push(stage_session):
    """No judge: the pushed, squashed, audited branch is the proof that both
    provider-offer stops are gone."""
    container, session, m = stage_session
    worktree, branch = m["worktree"], m["branch"]
    baseline, _ = _baseline(session, m)
    rc, out = run_in(
        container, ["omc", "configure", "--set", "llm.provider_selection_per_step=false"]
    )
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "internal", "models"])
    assert rc == 0 and '"provider_selection_per_step": false' in out, out

    session.send(_direct_design("claude"))
    turn = session.wait_turn(CHAIN_TURN_BUDGET)
    final = session.snapshot(worktree)
    answer = f"chained answer (tail): {turn['text'][-2000:]}"

    assert any(ref.startswith(f"refs/heads/{branch} ") for ref in final["remote_refs"]), (
        f"the chain did not push {branch}; {answer}"
    )
    rc, commits = run_in(container, ["git", "-C", worktree, "rev-list", "--count", "origin/main..HEAD"])
    assert rc == 0 and commits.strip() == "1", f"finish did not squash: {commits!r}; {answer}"
    records = [
        key
        for key in final["spec_plan"]
        if key.startswith("docs/superpowers/specs/") and key not in baseline["spec_plan"]
    ]
    assert len(records) == 1, f"expected one new design record: {records}; {answer}"
    rc, content = run_in(container, ["git", "-C", worktree, "show", f"HEAD:{records[0]}"])
    assert rc == 0 and "## Implementation review" in content, f"audit trace missing; {answer}"
    plans = [
        key
        for key in final["spec_plan"]
        if key.startswith("docs/superpowers/plans/") and key not in baseline["spec_plan"]
    ]
    assert plans, f"the chain wrote no plan; {answer}"
    for plan in plans:
        rc, _ = run_in(container, ["git", "-C", worktree, "cat-file", "-e", f"HEAD:{plan}"])
        assert rc == 0, f"plan not committed: {plan}; {answer}"
    assert final["source"]["greeting.py"] != baseline["source"]["greeting.py"], answer
    rc, dirty = run_in(container, ["git", "-C", worktree, "status", "--porcelain"])
    assert rc == 0 and not dirty.strip(), f"the chain left a dirty tree: {dirty}; {answer}"
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0, f"project stages never executed; {answer}"
    _assert_implement_stage_order(markers)
    _assert_finish_stage_order(markers)
    _assert_published_fix(
        container, m["repo"], worktree, branch, "def greeting():\n    return 'Goodbye, world!'\n"
    )
```

  (Wrap the `rev-list` call to 100 columns; ruff will reformat.)

- [x] **Step 4: Run to see them pass.** First `uv run --frozen python -m pytest tests/unit/test_lifecycle_command.py -q` (expect PASS). Then:
  - `OMC_E2E_NO_DOCKER=1 uv run --frozen python -m pytest --collect-only -q tests/e2e/variations/test_from_agreed.py`: collects, including `test_design_auto_advances_to_push`.
  - `OMC_E2E_NO_DOCKER=1 just lifecycle-full --collect-only`: lists the eight existing ids plus `test_design_auto_advances_to_push`, and no other `test_from_agreed` test.
  - `uvx ruff format tests/e2e/variations/test_from_agreed.py tests/unit/test_lifecycle_command.py && uvx ruff check tests`.
  - The variation's body cannot run here (no token). Say so in the task report; never ask for a token.

- [x] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null; git add tests/e2e/variations/test_from_agreed.py justfile tests/unit/test_lifecycle_command.py
git commit -m "E2E: expensive chain variation proves /omc:design runs to the push

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9: README recipe, regeneration and guard

Complexity: medium

**Files:**
- Modify: `.claude/skills/regenerate-readme/SKILL.md` (`## Configure` slot; lifecycle slot; one word in the OMC_MODELS listing of "How omc is put together")
- Regenerate: `README.md` (via `/regenerate-readme`, never by hand)
- Test: `tests/unit/test_cli.py` (new `test_readme_documents_provider_selection_per_step` beside `test_readme_documents_gitnexus_embeddings` ~line 307)

**Interfaces:**
- Consumes: the final skill texts (Tasks 3–5), the layer (Task 6) and the schema label (Task 1). The recipe's step 1 reads them.

Pressure-test notes (verified in `tests/unit/test_cli.py`):
- `test_readme_documents_optional_write_plan` still requires these strings in the recipe's `- Design:` bullet: "`/omc:write-plan` needs the committed record", "pressure-tests the plan via explain", "commits only the plan file" and "stops with the session open". It also requires the flat README lifecycle to contain "commits only the plan file and stops with the session open". **Append the qualifier after these strings and never split them.** Example: "…commits only the plan file and stops with the session open when Provider Selection Per Step is on (off, the default, continues into implement)…".
- `test_readme_headings_match_regeneration_skill`: no new `##` heading. Recipe step 4 checks four Mermaid fences, so add an edge to the existing lifecycle flowchart, not a new diagram. Mermaid edge labels must avoid semicolons.
- The recipe's own step 5 command says `uv run --frozen pytest …`. On this host run `uv run --frozen python -m pytest …` instead (Global Constraints). Do not edit the recipe's command for this.

- [x] **Step 1: Write the failing guard.** In `tests/unit/test_cli.py`, after `test_readme_documents_gitnexus_embeddings`:

```python
def test_readme_documents_provider_selection_per_step():
    root = Path(__file__).resolve().parents[2]
    recipe = (root / ".claude/skills/regenerate-readme/SKILL.md").read_text()
    readme = (root / "README.md").read_text()
    recipe_configure = recipe.split("#### `## Configure`", 1)[1].split("\n#### ", 1)[0]
    recipe_lifecycle = recipe.split("#### `` ## The lifecycle:", 1)[1].split("\n#### ", 1)[0]
    for needle in (
        "Provider Selection Per Step",
        "`llm.provider_selection_per_step`",
        "--set llm.provider_selection_per_step=true",
    ):
        assert needle in recipe_configure, f"recipe Configure slot missing {needle!r}"
    assert "Provider Selection Per Step" in recipe_lifecycle
    configure = readme.split("## Configure", 1)[1].split("\n## ", 1)[0]
    flat_configure = " ".join(configure.split())
    assert "Provider Selection Per Step" in configure
    assert "`llm.provider_selection_per_step`" in configure
    assert "--set llm.provider_selection_per_step=true" in configure
    assert "off by default" in flat_configure.lower()
    lifecycle = readme.split("## The lifecycle", 1)[1].split("\n## ", 1)[0]
    flowchart = lifecycle.split("```mermaid", 1)[1].split("```", 1)[0]
    assert "Provider Selection Per Step" in flowchart
    flat = " ".join(lifecycle.split())
    assert "Provider Selection Per Step off" in flat
    assert "commits only the plan file and stops with the session open" in flat
```

- [x] **Step 2: Run to see it fail.** `uv run --frozen python -m pytest tests/unit/test_cli.py -q -k provider_selection_per_step`. Expected: FAIL with "recipe Configure slot missing 'Provider Selection Per Step'".

- [x] **Step 3: Edit the recipe.**
  - **`## Configure` slot**: after the `GitNexus → Embeddings …` paragraph, add: "LLM → Provider Selection Per Step (`llm.provider_selection_per_step`, off by default; also `--set llm.provider_selection_per_step=true`): off means `/omc:design` runs through implement, audit, finish and the push in one session, with one notice line at each automatic step; on restores the per-stage stops that offer another provider (`omc implement`/`omc review --claude|--codex`). Off also means the implementing session audits itself; turn it on, or run `omc review` after a typed stop, for a second provider's audit. Existing installs get the off default on upgrade (no migration)." In the schema-labels sentence, add "Provider Selection Per Step" to the list.
  - **Lifecycle slot**: in the flowchart sentence, add "edges labelled Provider Selection Per Step off from Record straight on to Build and from Branch straight on to Audit (no typed word), beside the typed edges". In the `- Design:` bullet, after "names three continuations: …" add "with Provider Selection Per Step on; with it off, `/omc:design` continues into implement without a typed command (one notice line)". After "…commits only the plan file and stops with the session open" add " when Provider Selection Per Step is on (off, the default, continues into implement)". In the `- Implement:` bullet add "with Provider Selection Per Step off it continues into audit without a typed command; on, it states the continuations and waits". Keep every existing needle listed in the pressure-test notes intact.
  - **How omc is put together**, item 3: "OMC_MODELS (`omc internal models`, resolved task choices)" becomes "OMC_MODELS (`omc internal models`, resolved task choices and the provider-selection-per-step flag)".

- [x] **Step 4: Regenerate.** Invoke the project skill `regenerate-readme` (`/regenerate-readme`) and follow its steps 1–6 fully. In step 5, use `uv run --frozen python -m pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'`. Then check that `git diff -- README.md` is limited to the Provider Selection Per Step facts (Configure bullet, flowchart edges, Design/write-plan/Implement sentences, OMC_MODELS listing) plus anything the recipe's own step 4 checks require. An unrelated rewrite is a defect. Undo it by regenerating, never by hand-editing.

- [x] **Step 5: Run to see it pass.** `uv run --frozen python -m pytest tests/unit/test_cli.py -q -k "readme or shell_integration_documents"` (all README guards green). Then run the fast-gate equivalent `uv run --frozen python -m pytest -m "not e2e and not local_iterm2" -q -n auto`. Expected: only the host-baseline failures listed in Global Constraints.

- [x] **Step 6: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null; git add .claude/skills/regenerate-readme/SKILL.md README.md tests/unit/test_cli.py
git commit -m "README: document LLM › Provider Selection Per Step and auto-advance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec coverage (self-review)

| Record | Task |
|---|---|
| §4.2 schema / menu / `set_key` / `_hydrate` / D7 no migration | 1 |
| §4.3 `OMC_MODELS` field; ok:false unchanged; D5 | 2 (+ reading rule in 3–5) |
| §4.4 design Step 0/1/5, contract, frontmatter | 3 |
| §4.4 write-plan Step 1 read, direct-use close (D8), caller mode unchanged | 4 |
| §4.4 implement Phase -1/2/3, red gate, contract (D9); audit opening sentence; frontmatter | 5 |
| §4.5 behavior layer sentence + externalize endings | 6 |
| §4.6 unchanged files | Global Constraints |
| §4.7 README recipe, regeneration, guard | 9 |
| §5 unit config / menu / verdict / skill contracts / negative audit assertion / README | 1, 2, 3–6, 9 |
| §5 E2E harness pin, golden/variations unchanged, chain variation, `FULL` | 7, 8 |
| D10 audit/finish unchanged | 5 (one sentence only) |
