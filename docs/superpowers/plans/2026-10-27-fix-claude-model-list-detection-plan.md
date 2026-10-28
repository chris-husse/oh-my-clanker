# Fix Claude Model List Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At a command's start, refresh Claude's on-disk model catalog from omc itself (with nonessential traffic enabled on that turn) when the catalog is missing or past its own `staleAt`, so model reconciliation runs against a current list on machines whose user settings freeze it.

**Architecture:** `ClaudeProvider.headless_argv` learns one keyword that appends the verified `--settings` override; `docsllm.cli_model_probe` forwards it. `taskmodels` turns its single Claude catalog read into a small struct (`ClaudeCatalog`: list + freshness judged against a new `ToolContext.now_ms()`), and `catalog()` unwraps it so no caller changes. `reconcile` handles the Claude CLI bucket before the compatibility check: at most two override turns at a command's start, then fail loud (missing) or continue with the cached list (old); a later `omc watch` tick passes `command_start=False` and spends no turn.

**Tech Stack:** Python 3, `ToolContext` (`run_bounded`, `list_dir`, `read_text`, new `now_ms`), pytest with restricted-PATH CLI stubs, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-27-fix-claude-model-list-detection-design.md` — the design truth; this plan argues from it and changes none of its decisions. Read §2 (decisions), §4 (mechanism) and §7 (testing) before any task.

## Global Constraints

- **Red → green for every task.** Write the test, run it, watch it fail for the expected reason, then implement; commit test and implementation together. Never `pytest.skip` / `mark.skip` / `skipif`: a missing prerequisite is a `pytest.fail` naming the command that satisfies it.
- **Pytest invocation in this worktree is `uv run python -m pytest …`** (never `uv run pytest` or `just check` by hand: `.venv/bin/pytest`'s shebang points at the primary checkout's venv and would test `main`'s code — verified on this machine). The project gate `/omc:check` (which runs `just check`) is the conductor's; a focused run that disagrees with the gate is the truth, and the discrepancy is reported.
- **`ToolContext` (`src/omc/toolctx.py`) is the only subprocess/env/network/clock boundary.** The wall clock is read only through the new `ToolContext.now_ms()`; nothing in `taskmodels` or `reconcile` imports `time`.
- **Provider CLI quirks are comments at the code site** in `src/omc/providers/claude.py`; the verified `--settings` facts (record §1 table, §4.1) go there verbatim in substance. Do not "clean up" flags without re-verifying against the real CLI.
- **Argv lists only, never `shell=True`.** The `--settings` JSON is ONE argv element, never shell-quoted.
- **Stub scripts run on a restricted PATH** (only the stub dir). The reconcile stubs are Python scripts under `#!{sys.executable}`; `tests/unit/_stubs.py` stubs use shell builtins, absolute paths and quoted heredocs.
- **Exact-argv assertions** over "was called"; assert on files, config bytes and argv logs, not on transcripts.
- **Never run `omc install` / `uv tool install`.** The live-verify task runs omc code through `uv run --frozen python`, never through an installed `omc`.
- **`README.md` is generated:** change `.claude/skills/regenerate-readme/SKILL.md`, then run `/regenerate-readme`; never hand-edit README.
- **Codex is untouched** beyond the keyword its `headless_argv` signature must accept. The API-key auth path and the remap prompt/parser/write-back are untouched.
- **Commit messages end with** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Narration lines keep the project's `→` / `✓` / `✗` / `·` convention on stderr through the caller's `say`.

## Wording fixed by this plan (the record left it to the plan)

| Where | Exact text |
|---|---|
| `SERVED_CATALOG_SETTINGS` (claude.py) | `{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}` |
| Refresh narration | `→ refreshing claude model list (nonessential traffic enabled)` then the probe's `✓ default model works` / `✗ …` |
| `CatalogMissing` message | `Claude model catalog missing under <dir>/cache/model-catalog` (hint retired) |
| Still missing after the turns (`ConfigError`, rc 1) | `Claude model catalog missing under <dir>/cache/model-catalog after two refresh turns; check the [servedCatalog] lines in ~/.claude/debug/latest` |
| Missing on a later watch tick (`ConfigError`, no turn) | `Claude model catalog missing under <dir>/cache/model-catalog; it is refreshed at the next command start` |
| Present but still old after the turns | `· claude catalog is N min past its refresh time; continuing with the cached list` (or `· claude catalog carries no refresh time; continuing with the cached list` when `staleAt` is unusable) |
| Dry run, missing | `· Claude model catalog missing under <dir>/cache/model-catalog; would refresh` then `DryRunStale` |
| Dry run, old | `· claude catalog is N min past its refresh time; would refresh` (or `· claude catalog carries no refresh time; would refresh`) then the per-leaf verdicts as today |
| Two-turn constant | `reconcile.CLAUDE_REFRESH_TURNS = 2` |
| Initialization signal | `reconcile(..., command_start: bool = True)`; `watch.py` passes `command_start=first_tick`; the one-shot commands pass nothing (default True) |

## File Map

| File | Responsibility after this plan |
|---|---|
| `src/omc/toolctx.py` | `now_ms()` — the wall clock in epoch ms, the time sibling of `read_text`/`list_dir`. |
| `src/omc/providers/base.py` | `headless_argv(..., served_catalog: bool = False)` declared on the ABC. |
| `src/omc/providers/claude.py` | `SERVED_CATALOG_SETTINGS`; appends `--settings <json>` when asked; the verified quirk comment. |
| `src/omc/providers/codex.py` | Accepts and ignores the keyword. |
| `src/omc/docsllm.py` | `cli_model_probe(..., served_catalog: bool = False)` forwarded to `headless_argv`. |
| `src/omc/taskmodels.py` | `ClaudeCatalog` struct; public `claude_catalog(ctx)` (one read: auth status + listing + file + `now_ms`); `catalog()` unwraps; hint-free `CatalogMissing`. |
| `src/omc/reconcile.py` | `CLAUDE_REFRESH_TURNS`; `_claude_cli_catalog` (refresh-at-start, bound, dry-run report, failure wording); `command_start` keyword; the old leaf-triggered refresh block removed. |
| `src/omc/watch.py` | Passes `command_start=first_tick`. |
| `tests/unit/test_toolctx.py`, `test_providers.py`, `test_docsllm.py`, `test_taskmodels.py`, `test_reconcile.py`, `test_watch.py`, `test_cli.py` | Red→green coverage per task. |
| `.claude/skills/regenerate-readme/SKILL.md`, `README.md` | Recipe sentence corrected; README regenerated. |
| `docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md` | Amendment notes in §6 and §8. |

## Pressure-test notes (source-verified; the GitNexus graph is 96 commits stale and lacks every symbol this plan touches)

- **Provider seam (Task 2).** `Provider.headless_argv` has exactly three callers in current source: `session.py:96` (`run_headless`), `slug.py:83`, `docsllm.py:215` (`cli_model_probe`); `Provider.headless_stream_argv`'s base default delegates to `headless_argv` (codex only — `ClaudeProvider` overrides it with its own builder and never calls `headless_argv`). `cli_model_probe` has five callers: `reconcile.py:268` (refresh), `reconcile.py:296` (remap), `docsllm.py:271` (`validate_selection`), `taskmodels.py:439` and `:450` (`validate_selection`). None passes the new keyword, so every one of their argvs stays byte-identical. A **bool keyword on the existing builder** is the right shape: builders are pure argv adapters, the override is one more element of the same one-shot turn, and the alternatives fail — an `extra_env` route through `run_bounded` is exactly the process-env form the record verified NOT to win (§1 variants A/I/K), and a separate provider method would duplicate the `-p`/`--allowed-tools`-last ordering rule.
- **Freshness reader (Task 3).** `_claude_catalog` has one caller, `catalog()`. Renaming it to a public `claude_catalog()` that returns a struct, with `catalog()` returning `.models`, keeps the record's one-read constraint (`auth status` + `list_dir` + `read_text` once) and leaves every `catalog()` caller untouched (`reconcile`, `tests/unit/test_taskmodels.py`, `test_reconcile.py`'s `recording_catalog`). Tests already fake `ToolContext` by instance attribute (`ctx.http_get = …`, `monkeypatch.setattr(ctx, "list_dir", …)`), so `ctx.now_ms = lambda: NOW_MS` is the established seam. Rejected: a second `catalog_fresh(ctx)` function (pays a second `auth status`); a raw `stale_at` on the struct with the comparison in `reconcile` (puts clock arithmetic outside the reader and would need `now_ms` in two modules).
- **Reconcile flow and the signal (Task 4).** `reconcile` is called from `cli/__init__.py:293/310/327` (session, after `_with_provider`), `:360` (`dependency watch`, docs) and `watch.py:462` (tick loop, which already derives `report_current` from `first_tick or last_reconcile_failed`). A keyword `command_start: bool = True` mirrors `report_current`'s shape: the four one-shot call sites stay unchanged (record §8 anticipates this), watch passes `command_start=first_tick`, and it cannot ride `report_current` because watch also sets that after a failed tick (the Task 4 watch test proves the two differ: `[True, False, True, False]` vs `[True, False, False, False]`). A default of `False` was rejected: it would change four call sites and seven keyword-exact test fakes for no behavioral gain. **One refinement to the record's §4.3 text:** with the Claude bucket handled in the bucket loop and a missing catalog raising (or, on a dry run, reporting then `DryRunStale`) before `stale_leaves` runs, `catalogs` never holds `None` any more — so this plan removes the dead `models is None → stale` branch and the `or []` / `is not None` guards rather than keeping an unreachable path; the dry run's missing-catalog report comes from `_claude_cli_catalog` instead. No decision changes.
- **Hidden API-key cost today (found while reading the tests).** `test_api_key_stale_id_remaps_to_listed_id_keeping_effort` currently asserts TWO headless turns under API-key auth: the old leaf-triggered block spends a refresh turn even though the API listing is live. The record's §4.4 ("no refresh turn is spent" under API-key auth) makes that one turn; Task 4 rewrites the assertion to `1`.
- **E2E helper string.** `tests/e2e/test_e2e_reconcile.py:64` tells the E2E operator "open claude once to fetch it, then rerun the E2E gate" for the account-auth branch of its container script. It is operator guidance for a test harness, not omc output; the record keeps E2E untouched ("No new E2E"), so it stays.

---

### Task 1: `ToolContext.now_ms()` — the clock seam

Complexity: simple

**Files:**
- Modify: `src/omc/toolctx.py` (add a method after `list_dir`, ~line 70)
- Test: `tests/unit/test_toolctx.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ToolContext.now_ms(self) -> int` — wall clock in epoch milliseconds (`int(time.time() * 1000)`). Tasks 3 and 4 fake it per instance (`ctx.now_ms = lambda: NOW_MS`).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_toolctx.py` (`time` is already imported there):

```python
def test_now_ms_is_the_wall_clock_in_epoch_milliseconds(tmp_path):
    ctx = _ctx(tmp_path)
    before = int(time.time() * 1000)
    stamp = ctx.now_ms()
    after = int(time.time() * 1000)
    assert isinstance(stamp, int) and not isinstance(stamp, bool)
    assert before <= stamp <= after
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python -m pytest tests/unit/test_toolctx.py -q -k now_ms`
Expected: FAIL with `AttributeError: 'ToolContext' object has no attribute 'now_ms'`.

- [ ] **Step 3: Implement** — in `src/omc/toolctx.py`, directly after `list_dir` (the module already imports `time`):

```python
    def now_ms(self) -> int:
        """Wall clock in epoch milliseconds — the unit Claude Code stamps its
        catalog files with (``fetchedAt``/``staleAt``). The time sibling of
        ``read_text``/``list_dir``: tests fake the clock at the same seam as
        every other provider-state read, and nothing outside this class reads it."""
        return int(time.time() * 1000)
```

- [ ] **Step 4: Run the test and the module's suite**

Run: `uv run python -m pytest tests/unit/test_toolctx.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omc/toolctx.py tests/unit/test_toolctx.py
git commit -m "Add ToolContext.now_ms, the clock seam for catalog freshness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Provider seam — `served_catalog` on `headless_argv` and `cli_model_probe`

Complexity: medium

**Files:**
- Modify: `src/omc/providers/base.py` (`headless_argv` signature + docstring, ~lines 30-45)
- Modify: `src/omc/providers/claude.py` (module constant; `headless_argv`, ~lines 66-78)
- Modify: `src/omc/providers/codex.py` (`headless_argv` signature, ~line 40)
- Modify: `src/omc/docsllm.py` (`cli_model_probe`, ~lines 203-216)
- Test: `tests/unit/test_providers.py`, `tests/unit/test_docsllm.py`

**Interfaces:**
- Produces: `Provider.headless_argv(prompt, *, model, effort="", allowed_tools=None, session_name="", served_catalog=False) -> list[str]` on the ABC and both providers.
- Produces: `omc.providers.claude.SERVED_CATALOG_SETTINGS = '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'` (a module constant; Task 4's tests re-declare the literal rather than import it, so the stub file stays independent of `omc` imports in its script text).
- Produces: `docsllm.cli_model_probe(ctx, provider, model, *, prompt=HEADLESS_PROBE_PROMPT, effort="", return_output=False, served_catalog=False) -> tuple[bool, str]`.
- Claude argv with the keyword: `["claude", "-p", <prompt>, "--output-format", "text", (-n …), (--model …), (--effort …), "--settings", SERVED_CATALOG_SETTINGS, (--allowed-tools … LAST)]`.

- [ ] **Step 1: Write the failing provider tests** — append to `tests/unit/test_providers.py` (`json` is imported there):

```python
def test_claude_served_catalog_override_is_one_settings_argument():
    p = get_provider("claude")
    plain = p.headless_argv("Reply with exactly OK.", model="")
    assert plain == ["claude", "-p", "Reply with exactly OK.", "--output-format", "text"]
    assert p.headless_argv("Reply with exactly OK.", model="", served_catalog=False) == plain
    override = p.headless_argv("Reply with exactly OK.", model="", served_catalog=True)
    assert override == [
        *plain,
        "--settings",
        '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}',
    ]
    # ONE argv element that Claude Code parses as JSON itself; never shell-quoted.
    assert json.loads(override[-1]) == {"env": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": ""}}
    with_tools = p.headless_argv(
        "x", model="m1", effort="high", allowed_tools=["mcp__jira"], served_catalog=True
    )
    assert with_tools[-2:] == ["--allowed-tools", "mcp__jira"]  # variadic flag stays LAST
    assert with_tools[-4:-2] == ["--settings", override[-1]]
    assert with_tools[:3] == ["claude", "-p", "x"]


def test_codex_accepts_and_ignores_served_catalog():
    p = get_provider("codex")
    assert p.headless_argv("x", model="m", served_catalog=True) == p.headless_argv("x", model="m")
    assert "--settings" not in p.headless_argv("x", model="", served_catalog=True)
```

- [ ] **Step 2: Write the failing probe test** — append to `tests/unit/test_docsllm.py` (`make_claude_stub` appends every argv as one space-joined line to `calls`, and matches the probe on `args[:2]`, so extra trailing flags still get the `OK` reply):

```python
def test_cli_model_probe_forwards_served_catalog_to_the_argv(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert docsllm.cli_model_probe(ctx, "claude", "", served_catalog=True) == (
        True,
        "default model works",
    )
    assert calls.read_text().strip() == (
        "-p Reply with exactly OK. --output-format text --settings "
        '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'
    )
    ctx, _, calls = _ctx(tmp_path / "plain", {})
    assert docsllm.cli_model_probe(ctx, "claude", "") == (True, "default model works")
    assert "--settings" not in calls.read_text()
```

- [ ] **Step 3: Run both and watch them fail**

Run: `uv run python -m pytest tests/unit/test_providers.py tests/unit/test_docsllm.py -q -k "served_catalog"`
Expected: 3 FAIL, each with `TypeError: … got an unexpected keyword argument 'served_catalog'`.

- [ ] **Step 4: Implement the ABC** — in `src/omc/providers/base.py`, change the abstract signature and docstring:

```python
    @abstractmethod
    def headless_argv(
        self,
        prompt: str,
        *,
        model: str,
        effort: str = "",
        allowed_tools: list[str] | None = None,
        session_name: str = "",
        served_catalog: bool = False,
    ) -> list[str]:
        """One-shot print-mode run against the user's system config.

        ``session_name`` names the resulting session where the CLI supports it
        (claude: ``-n``, resumable via ``--resume <name>`` — verified live);
        providers without session naming ignore it.

        ``served_catalog`` asks the CLI to fetch its served model catalog on
        this turn even where the user's settings would stop it (claude: an
        inline ``--settings`` env override; design record 2026-10-27 §4.1).
        Providers without such a catalog ignore it.
        """
```

- [ ] **Step 5: Implement Claude** — in `src/omc/providers/claude.py`, add the constant below `_API_FAMILIES` and extend `headless_argv`:

```python
# The inline settings object that re-enables Claude Code's served model-catalog
# fetch for ONE headless turn. One argv element, never shell-quoted; the quirk
# it works around is documented at headless_argv.
SERVED_CATALOG_SETTINGS = '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'
```

```python
    def headless_argv(
        self,
        prompt,
        *,
        model,
        effort="",
        allowed_tools=None,
        session_name="",
        served_catalog=False,
    ):
        # Prompt must come RIGHT AFTER -p: --allowed-tools is variadic and would
        # swallow a trailing positional as a tool name. Keep --allowed-tools LAST
        # and omit it entirely when empty (an empty value parses as a bogus tool).
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if session_name:
            argv += ["-n", session_name]  # -p sessions persist; resumable by name
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        if served_catalog:
            # Claude Code applies the settings-file `env` block OVER the process
            # env, so CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC set in
            # ~/.claude/settings.json cannot be beaten from the environment
            # (=0, =false and empty all still log "[servedCatalog] off"). It
            # counts as set whenever NON-EMPTY, so only "" disables it, and
            # only an inline --settings object reaches that layer. This keeps
            # every other user setting (plugins, permissions, proxy env,
            # apiKeyHelper); `--setting-sources project,local` also works but
            # drops the whole user settings file. Verified on 2.1.312,
            # 2026-10-28 (design record 2026-10-27 §1 table, variant G).
            argv += ["--settings", SERVED_CATALOG_SETTINGS]
        if allowed_tools:
            argv += ["--allowed-tools", *allowed_tools]
        return argv
```

- [ ] **Step 6: Implement Codex** — in `src/omc/providers/codex.py`, widen the signature only:

```python
    def headless_argv(
        self, prompt, *, model, effort="", allowed_tools=None, session_name="", served_catalog=False
    ):
        # `codex exec` is the non-interactive entry point; prompt is the trailing
        # positional; -m is the model flag. allowed_tools has no codex equivalent;
        # served_catalog has none either (`codex debug models` is already live).
        # --skip-git-repo-check: verified against codex 0.144 — without it, exec
        # refuses to run in a directory the user hasn't interactively trusted,
        # which a headless one-shot call can never satisfy.
```

(the body is unchanged.)

- [ ] **Step 7: Implement the probe pass-through** — in `src/omc/docsllm.py`:

```python
def cli_model_probe(
    ctx: ToolContext,
    provider: str,
    model: str,
    *,
    prompt: str = HEADLESS_PROBE_PROMPT,
    effort: str = "",
    return_output: bool = False,
    served_catalog: bool = False,
) -> tuple[bool, str]:
    """One headless turn on the user's login. run_bounded, not run: `claude -p`
    spawns MCP grandchildren that a plain subprocess timeout would orphan.
    ``served_catalog`` is forwarded to the provider's argv builder (the Claude
    catalog refresh turn, reconcile.py)."""
    p = get_provider(provider)
    argv = p.headless_argv(prompt, model=model, effort=effort, served_catalog=served_catalog)
```

(the rest of the function is unchanged.)

- [ ] **Step 8: Run the provider, probe and argv-consumer suites**

Run: `uv run python -m pytest tests/unit/test_providers.py tests/unit/test_docsllm.py tests/unit/test_session.py tests/unit/test_slug.py tests/unit/test_taskmodels.py -q`
Expected: all PASS (the existing exact-argv tests prove no caller's argv moved).

- [ ] **Step 9: Commit**

```bash
git add src/omc/providers/base.py src/omc/providers/claude.py src/omc/providers/codex.py src/omc/docsllm.py tests/unit/test_providers.py tests/unit/test_docsllm.py
git commit -m "Add the served_catalog override to Claude's headless argv and the probe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Freshness reader — `ClaudeCatalog` beside `catalog()`, hint retired

Complexity: medium

**Files:**
- Modify: `src/omc/taskmodels.py` (`CatalogMissing` docstring ~line 40; new dataclass after `CatalogModel`; `_claude_catalog` → `claude_catalog`, ~lines 160-260; `catalog()` ~line 283)
- Test: `tests/unit/test_taskmodels.py` (Claude reader section, ~lines 764-940)

**Interfaces:**
- Consumes: `ToolContext.now_ms()` (Task 1).
- Produces:

```python
@dataclass(frozen=True)
class ClaudeCatalog:
    models: list[CatalogModel]
    fresh: bool               # False: staleAt in the past, missing or malformed → a refresh turn may change the list
    overdue_minutes: int | None  # whole minutes past staleAt when usable and past; None otherwise

def claude_catalog(ctx: ToolContext) -> ClaudeCatalog   # ONE read; raises CatalogMissing / ConfigError / ProbeFailed as today
```
- `catalog(ctx, "claude")` returns `claude_catalog(ctx).models` — unchanged contract for every caller.
- `CatalogMissing` message: `f"Claude model catalog missing under {directory}"` (no hint).
- API-key auth path: `ClaudeCatalog(_api_catalog(...), fresh=True, overdue_minutes=None)` — never reads the clock.

- [ ] **Step 1: Write the failing reader tests** — in `tests/unit/test_taskmodels.py`, replace `test_claude_catalog_missing_has_open_once_hint` and add the freshness tests beside the Claude reader tests. The existing fixture `_claude_catalog(models, fetched_at)` already writes `"staleAt": fetched_at + 3_304_030`.

```python
FETCHED_AT = 1792022400000
STALE_AT = FETCHED_AT + 3_304_030  # what the _claude_catalog fixture writes
ABSENT = object()


def test_claude_catalog_missing_names_the_directory_without_the_open_once_hint(tmp_path):
    ctx, directory, _ = _claude_catalog_ctx(tmp_path)
    with pytest.raises(taskmodels.CatalogMissing) as no_file:
        taskmodels.catalog(ctx, "claude")  # directory exists, no file for this org
    assert str(no_file.value) == f"Claude model catalog missing under {directory}"
    directory.rmdir()
    with pytest.raises(taskmodels.CatalogMissing) as no_dir:
        taskmodels.claude_catalog(ctx)  # directory absent
    assert str(no_dir.value) == f"Claude model catalog missing under {directory}"
    assert "open claude once" not in str(no_file.value) + str(no_dir.value)


@pytest.mark.parametrize(
    ("now_ms", "stale_at", "fresh", "overdue"),
    [
        (STALE_AT - 1, STALE_AT, True, None),  # one ms before staleAt: fresh
        (STALE_AT, STALE_AT, False, 0),  # at staleAt: stale, zero whole minutes
        (STALE_AT + 5 * 60_000 + 59_999, STALE_AT, False, 5),  # whole minutes, floored
        (FETCHED_AT, ABSENT, False, None),  # no stamp: cannot vouch → stale
        (FETCHED_AT, "2026-10-15T00:55:04Z", False, None),  # ISO string: malformed
        (FETCHED_AT, True, False, None),  # bool is not an int here
        (FETCHED_AT, None, False, None),
        (FETCHED_AT, 1.5, False, None),
    ],
)
def test_claude_catalog_freshness_from_stale_at_under_a_faked_clock(
    tmp_path, now_ms, stale_at, fresh, overdue
):
    ctx, directory, calls = _claude_catalog_ctx(tmp_path)
    ctx.now_ms = lambda: now_ms
    data = _claude_catalog([_claude_model("claude-opus-5-5", "Opus", "main")], FETCHED_AT)
    if stale_at is ABSENT:
        del data["staleAt"]
    else:
        data["staleAt"] = stale_at
    (directory / "org-current-hash-cc.json").write_text(json.dumps(data))

    read = taskmodels.claude_catalog(ctx)

    assert (read.fresh, read.overdue_minutes) == (fresh, overdue)
    assert read.models == [taskmodels.CatalogModel("claude-opus-5-5", "opus", ("medium", "high"))]
    assert taskmodels.catalog(ctx, "claude") == read.models
    assert calls.read_text().strip() == "auth status"


def test_claude_catalog_judges_the_newest_files_stale_at(tmp_path):
    ctx, directory, _ = _claude_catalog_ctx(tmp_path)
    old = _claude_catalog([_claude_model("claude-opus-old", "Opus", "main")], 1790812800000)
    old["staleAt"] = 2_000_000_000_000  # an older file with a far-future stamp must not vouch
    new = _claude_catalog([_claude_model("claude-opus-5-5", "Opus", "main")], FETCHED_AT)
    (directory / "org-current-old-cc.json").write_text(json.dumps(old))
    (directory / "org-current-new-cc.json").write_text(json.dumps(new))
    ctx.now_ms = lambda: STALE_AT + 60_000

    read = taskmodels.claude_catalog(ctx)

    assert [model.id for model in read.models] == ["claude-opus-5-5"]
    assert (read.fresh, read.overdue_minutes) == (False, 1)


def test_claude_api_key_listing_is_fresh_and_never_reads_the_clock(tmp_path):
    bindir = tmp_path / "bin"
    make_stub(
        bindir,
        "claude",
        stdout=json.dumps(
            {
                "loggedIn": True,
                "authMethod": "api_key",
                "apiKeySource": "ANTHROPIC_API_KEY",
                "configDirectory": str(tmp_path / "claude-config"),
            }
        ),
    )
    ctx = ToolContext.from_env(stub_env(bindir, ANTHROPIC_API_KEY="private-key"))
    ctx.http_get = lambda url, *, headers=None, timeout=30.0: (
        200,
        json.dumps({"data": [{"id": "claude-opus-5-5", "created_at": "2026-05-01T00:00:00Z"}]}),
    )
    ctx.now_ms = lambda: pytest.fail("API-key listing has no file and no age to judge")

    read = taskmodels.claude_catalog(ctx)

    assert [model.id for model in read.models] == ["claude-opus-5-5"]
    assert (read.fresh, read.overdue_minutes) == (True, None)
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run python -m pytest tests/unit/test_taskmodels.py -q -k "claude_catalog"`
Expected: the new tests FAIL with `AttributeError: module 'omc.taskmodels' has no attribute 'claude_catalog'` and, for the missing-hint test, an assertion on the message that still carries `; open claude once to fetch it`. The older Claude reader tests still PASS.

- [ ] **Step 3: Implement** — in `src/omc/taskmodels.py`:

(a) `CatalogMissing` docstring:

```python
class CatalogMissing(ConfigError):
    """Claude has not written its model catalog. A refresh turn with nonessential
    traffic enabled can create it (reconcile.py); the message names the directory."""
```

(b) the struct, directly after `CatalogModel`:

```python
@dataclass(frozen=True)
class ClaudeCatalog:
    """One read of Claude's model list and what its on-disk file says about its age."""

    models: list[CatalogModel]
    # False when the file's staleAt is in the past, missing or malformed — a
    # refresh turn may change the list. True when Claude would still serve the
    # file from cache, and for the live API-key listing (no file, no age).
    fresh: bool
    # Whole minutes past staleAt when the stamp is usable and in the past;
    # None otherwise (fresh, unusable stamp, or the live listing).
    overdue_minutes: int | None
```

(c) rename `_claude_catalog` to `claude_catalog`, returning the struct. The API-key branch becomes:

```python
        return ClaudeCatalog(_api_catalog(ctx, "claude", key), fresh=True, overdue_minutes=None)
```

Both `CatalogMissing` raises lose the hint:

```python
        raise CatalogMissing(f"Claude model catalog missing under {directory}") from exc
...
        raise CatalogMissing(f"Claude model catalog missing under {directory}")
```

After `_, path, data = max(dated, key=lambda entry: entry[0])`, before the `try:` that parses the models:

```python
    # Claude Code writes staleAt beside fetchedAt, also epoch milliseconds (live
    # 2.1.312: staleAt − fetchedAt ≈ 62 min), and refetches past it when
    # nonessential traffic is allowed. A missing or malformed stamp cannot vouch
    # for the list's age, so it counts as stale: one extra refresh turn is the
    # cheap side of that doubt, never an error. The list below is still read.
    stale_at = data.get("staleAt")
    if not isinstance(stale_at, int) or isinstance(stale_at, bool):
        fresh, overdue = False, None
    elif ctx.now_ms() < stale_at:
        fresh, overdue = True, None
    else:
        fresh, overdue = False, (ctx.now_ms() - stale_at) // 60_000
```

Call `ctx.now_ms()` once — bind `now = ctx.now_ms()` before the `if` and use `now` in both branches. The parse block's `return result` becomes `return ClaudeCatalog(result, fresh=fresh, overdue_minutes=overdue)`.

(d) `catalog()`:

```python
    if provider == "claude":
        return claude_catalog(ctx).models
```

- [ ] **Step 4: Run the taskmodels and reconcile suites**

Run: `uv run python -m pytest tests/unit/test_taskmodels.py tests/unit/test_reconcile.py -q`
Expected: `test_taskmodels.py` all PASS; in `test_reconcile.py` exactly one test fails, `test_missing_claude_catalog_after_refresh_has_path_and_open_hint`, because its regex still expects the retired hint. Narrow that test in this task so the commit lands green (Task 4 replaces it with the G4 reproducer): change its `pytest.raises` to `match=r"cache/model-catalog"` and add `assert "open claude once" not in str(exc.value)` (bind the context manager as `as exc`). Re-run the same command: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omc/taskmodels.py tests/unit/test_taskmodels.py
git commit -m "Read Claude's catalog freshness from staleAt and retire the open-once hint

claude_catalog() returns the list with its freshness from one read;
catalog() unwraps it. The CatalogMissing message names the directory
only; the next commit teaches reconcile to create the file itself.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Reconcile flow — refresh at command start, two-turn bound, dry run, initialization signal

Complexity: high

**Files:**
- Modify: `src/omc/reconcile.py` (module constant + three helpers before `reconcile`; `reconcile` signature, docstring, bucket loop, `stale_leaves`, the removed refresh block, `say_current_models`, `offered`)
- Modify: `src/omc/watch.py:462-469` (one keyword)
- Test: `tests/unit/test_reconcile.py` (fixtures + rewritten + new tests), `tests/unit/test_watch.py` (six fakes), `tests/unit/test_cli.py` (one fake, guard only)

**Interfaces:**
- Consumes: `taskmodels.claude_catalog`, `taskmodels.ClaudeCatalog`, `taskmodels.CatalogMissing` (Task 3); `docsllm.cli_model_probe(..., served_catalog=True)` (Task 2).
- Produces: `reconcile(ctx, cfg, leaves, *, say, dry_run, report_current=True, command_start=True) -> Config`; `reconcile.CLAUDE_REFRESH_TURNS = 2`; `watch.py` passes `command_start=first_tick`.
- Test fixtures produced for the file: `FETCHED_AT`, `NOW_MS`, `FRESH_STALE_AT`, `PAST_STALE_AT`, `SERVED_CATALOG_SETTINGS`, `REFRESH_TURN`, `_claude_catalog_path(ctx)`, `_claude_catalog_text(models, *, stale_at)`, `_claude_catalog(ctx, models, *, stale_at)`, `_cli_stub(..., written_catalog=None, written_on_turn=1)`.

- [ ] **Step 1: Update the test fixtures** — in `tests/unit/test_reconcile.py`:

(a) constants beside `CLAUDE_CURRENT`:

```python
FETCHED_AT = 1792065600000
NOW_MS = FETCHED_AT + 10 * 60_000  # the faked clock: ten minutes after the fixture's fetch
FRESH_STALE_AT = FETCHED_AT + 62 * 60_000  # Claude's own ~62 min window: fresh under NOW_MS
PAST_STALE_AT = FETCHED_AT + 5 * 60_000  # five whole minutes past under NOW_MS
# Literal on purpose: the stub script must not import omc at write time.
SERVED_CATALOG_SETTINGS = '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'
REFRESH_TURN = [
    "-p",
    "Reply with exactly OK.",
    "--output-format",
    "text",
    "--settings",
    SERVED_CATALOG_SETTINGS,
]
```

(b) `_context` fakes the clock for every test in the file (Codex tests never consult it):

```python
def _context(tmp_path: Path) -> ToolContext:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc"), "PATH": str(bindir)}
    )
    ctx.now_ms = lambda: NOW_MS  # the Claude fixture is fresh by default under this clock
    return ctx
```

(c) replace `_claude_catalog` with a path helper, a text builder and a writer:

```python
def _claude_catalog_path(ctx: ToolContext) -> Path:
    return Path(ctx.env["HOME"]) / "claude-state" / "cache" / "model-catalog" / "org-main-a-cc.json"


def _claude_catalog_text(models=CLAUDE_CURRENT, *, stale_at: object = FRESH_STALE_AT) -> str:
    data: dict[str, object] = {
        "fetchedAt": FETCHED_AT,
        "catalog": {
            "config": {
                "models": [
                    {
                        "id": model_id,
                        "short_name": family.title(),
                        "thinking": {"effort_options": [{"id": effort} for effort in efforts]},
                    }
                    for model_id, family, efforts in models
                ]
            }
        },
    }
    if stale_at is not None:
        data["staleAt"] = stale_at
    return json.dumps(data)


def _claude_catalog(ctx: ToolContext, models=CLAUDE_CURRENT, *, stale_at: object = FRESH_STALE_AT) -> Path:
    path = _claude_catalog_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_claude_catalog_text(models, stale_at=stale_at))
    return path
```

(d) `_cli_stub` gains two keywords; the Claude branch writes the catalog only on an override turn, counting override turns from its own log (`json.loads(line)` — the log stores argv as JSON lists, so the override element is matched exactly, not as a substring):

```python
def _cli_stub(
    ctx: ToolContext,
    provider: str,
    *,
    auth_status: dict[str, object] | None = None,
    remap_output: str = "",
    remap_rc: int = 0,
    remap_edit: tuple[Path, str] | None = None,
    written_catalog: str | None = None,
    written_on_turn: int = 1,
) -> Path:
    """Real argv through a restricted PATH; only the external CLI is replaced.

    ``written_catalog``: catalog JSON the Claude stub writes to the fixture path
    when — and only when — a turn carries the ``--settings`` override, on the
    ``written_on_turn``-th such turn (the flag-cache layer needs two).
    """
    log = Path(ctx.env["HOME"]) / f"{provider}.argv.jsonl"
    status = (
        auth_status
        if auth_status is not None
        else {
            "loggedIn": True,
            "orgId": "org-main",
            "configDirectory": str(Path(ctx.env["HOME"]) / "claude-state"),
        }
    )
    script = f"""#!{sys.executable}
import json
import sys
from pathlib import Path
args = sys.argv[1:]
log = Path({str(log)!r})
with log.open("a") as handle:
    handle.write(json.dumps(args) + "\\n")
if {provider!r} == "claude" and args == ["auth", "status"]:
    print(json.dumps({status!r}))
    sys.exit(0)
if {provider!r} == "codex" and args == ["debug", "models"]:
    catalog = Path({str(Path(ctx.env["HOME"]) / "codex.catalog.json")!r})
    if not catalog.exists():
        print("catalog unavailable", file=sys.stderr)
        sys.exit(1)
    print(catalog.read_text())
    sys.exit(0)
override = {SERVED_CATALOG_SETTINGS!r}
served = {written_catalog!r}
if {provider!r} == "claude" and served is not None and "--settings" in args:
    if args[args.index("--settings") + 1] == override:
        turns = sum(1 for line in log.read_text().splitlines() if override in json.loads(line))
        if turns >= {written_on_turn}:
            target = Path({str(_claude_catalog_path(ctx))!r})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(served)
prompt = args[1] if {provider!r} == "claude" else args[-1]
if prompt == "Reply with exactly OK.":
    print("OK")
    sys.exit(0)
edit = {None if remap_edit is None else (str(remap_edit[0]), remap_edit[1])!r}
if edit is not None:
    Path(edit[0]).write_text(edit[1])
print({remap_output!r})
sys.exit({remap_rc})
"""
    executable = Path(ctx.env["PATH"]) / provider
    executable.write_text(script)
    executable.chmod(0o755)
    return log
```

- [ ] **Step 2: Rewrite the existing Claude assertions to the new trigger** (Decision 2: a fresh catalog never spends a refresh turn; API-key auth never does):

1. `test_missing_claude_catalog_after_refresh_has_path_and_open_hint` → delete; its replacement is the G4 reproducer in Step 3.
2. `test_two_claude_leaves_share_one_default_remap_turn`: replace the three `calls` assertions with

```python
    calls = _headless(_calls(log))
    assert len(calls) == 1 and calls[0][:1] == ["-p"] and calls[0][2:] == ["--output-format", "text"]
    assert '"model"' in calls[0][1] and '"tasks.review"' in calls[0][1]
    assert not any(line.startswith("→ refreshing") for line in lines)
```

3. `test_api_key_stale_id_remaps_to_listed_id_keeping_effort`: both `assert len(_headless(_calls(log))) == 2` become `== 1` (the API listing is live; today's second turn was the leaf-triggered refresh, now gone).
4. `test_cli_docs_model_remaps_and_keeps_other_provider_leaves`: `assert len(_headless(_calls(log))) == 1`.
5. `test_saved_docs_effort_remaps_to_full_id_without_effort`: `assert len(_headless(_calls(log))) == 1` for both backends (drop the conditional).
6. `test_api_docs_and_cli_session_use_separate_catalogs_but_one_remap_turn`: replace the `calls` block with

```python
    calls = _headless(_calls(log))
    assert len(calls) == 1  # fresh CLI catalog: no refresh; API docs: no refresh; one remap
    assert calls[0][:1] == ["-p"] and calls[0][2:] == ["--output-format", "text"]
    assert '"model"' in calls[0][1] and '"docs_model"' in calls[0][1]
```

(`test_suppression_omits_current_provider_during_other_provider_remap`, `test_failed_mixed_provider_remap_does_not_report_current`, `test_api_key_cli_saved_effort_is_current_without_turn_or_write`, `test_api_docs_only_gets_list_and_remaps_without_refresh` and `test_api_docs_family_default_remaps_to_launchable_full_id` already assert zero refresh turns and stay as they are.)

- [ ] **Step 3: Add the new reconcile tests** — append to `tests/unit/test_reconcile.py`:

```python
def _catalog_dir(ctx: ToolContext) -> Path:
    return _claude_catalog_path(ctx).parent


def test_missing_claude_catalog_is_created_by_one_override_turn_without_remap(tmp_path):
    """G4: with nonessential traffic disabled in user settings only the override turn writes the file."""
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-5-5")
    before = (ctx.home / "config.yaml").read_bytes()
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text())
    lines = []

    assert under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False) is cfg

    assert _headless(_calls(log)) == [REFRESH_TURN]
    assert lines == [
        "→ refreshing claude model list (nonessential traffic enabled)",
        "✓ default model works",
        "✓ claude models current (3 listed)",
    ]
    assert _claude_catalog_path(ctx).exists()
    assert (ctx.home / "config.yaml").read_bytes() == before


def test_second_override_turn_writes_the_catalog_when_the_first_only_thaws_flags(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-5-5")
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text(), written_on_turn=2)
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    assert _headless(_calls(log)) == [REFRESH_TURN, REFRESH_TURN]
    assert lines.count("→ refreshing claude model list (nonessential traffic enabled)") == 2
    assert lines[-1] == "✓ claude models current (3 listed)"


def test_catalog_still_missing_after_two_override_turns_fails_loud_without_a_third(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    before = (ctx.home / "config.yaml").read_bytes()
    log = _cli_stub(ctx, "claude")  # never writes a catalog
    lines = []

    with pytest.raises(ConfigError) as caught:
        under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    message = str(caught.value)
    assert message == (
        f"Claude model catalog missing under {_catalog_dir(ctx)} after two refresh turns; "
        "check the [servedCatalog] lines in ~/.claude/debug/latest"
    )
    assert _headless(_calls(log)) == [REFRESH_TURN, REFRESH_TURN]
    assert lines.count("→ refreshing claude model list (nonessential traffic enabled)") == 2
    assert not any("→ model:" in line for line in lines)
    assert (ctx.home / "config.yaml").read_bytes() == before


def test_past_stale_at_spends_one_override_turn_then_remaps_against_the_new_list(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    _claude_catalog(ctx, stale_at=PAST_STALE_AT)
    log = _cli_stub(
        ctx,
        "claude",
        written_catalog=_claude_catalog_text(),
        remap_output='{"model": "claude-opus-5-5"}',
    )
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    calls = _headless(_calls(log))
    assert len(calls) == 2 and calls[0] == REFRESH_TURN
    assert calls[1][:1] == ["-p"] and calls[1][2:] == ["--output-format", "text"]
    assert '"model"' in calls[1][1]
    assert lines[0] == "→ refreshing claude model list (nonessential traffic enabled)"
    assert not any("past its refresh time" in line for line in lines)
    saved = store.load_global(ctx.home)
    assert saved is not None and saved.llm.providers["claude"].model == "claude-opus-5-5"


def test_fresh_catalog_with_a_stale_leaf_goes_straight_to_the_remap_turn(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    _claude_catalog(ctx)  # fresh under NOW_MS
    log = _cli_stub(ctx, "claude", remap_output='{"model": "claude-opus-5-5"}')
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    calls = _headless(_calls(log))
    assert len(calls) == 1 and "--settings" not in calls[0] and '"model"' in calls[0][1]
    assert not any(line.startswith("→ refreshing") for line in lines)
    assert cfg.llm.providers["claude"].model == "claude-opus-5-5"


@pytest.mark.parametrize("stale_at", [None, "soon", True], ids=["absent", "string", "bool"])
def test_unusable_stale_at_counts_as_stale_without_an_error(tmp_path, stale_at):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-5-5")
    _claude_catalog(ctx, stale_at=stale_at)
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text())
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    assert _headless(_calls(log)) == [REFRESH_TURN]
    assert lines[-1] == "✓ claude models current (3 listed)"


def test_catalog_still_old_after_two_turns_narrates_age_and_continues(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    _claude_catalog(ctx, stale_at=PAST_STALE_AT)
    log = _cli_stub(ctx, "claude", remap_output='{"model": "claude-opus-5-5"}')  # turns never rewrite
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    calls = _headless(_calls(log))
    assert calls[:2] == [REFRESH_TURN, REFRESH_TURN] and len(calls) == 3
    age = "· claude catalog is 5 min past its refresh time; continuing with the cached list"
    assert age in lines
    remap = next(index for index, line in enumerate(lines) if line.startswith("→ model:"))
    assert lines.index(age) < remap
    saved = store.load_global(ctx.home)
    assert saved is not None and saved.llm.providers["claude"].model == "claude-opus-5-5"


def test_dry_run_with_missing_catalog_reports_would_refresh_and_runs_no_turn(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    before = (ctx.home / "config.yaml").read_bytes()
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text())
    lines = []

    with pytest.raises(under_test.DryRunStale):
        under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=True)

    assert lines == [f"· Claude model catalog missing under {_catalog_dir(ctx)}; would refresh"]
    assert _headless(_calls(log)) == []
    assert not _claude_catalog_path(ctx).exists()
    assert (ctx.home / "config.yaml").read_bytes() == before


def test_dry_run_with_old_catalog_reports_age_then_per_leaf_verdicts_without_turn(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1", tasks_review="opus")
    _claude_catalog(ctx, stale_at=PAST_STALE_AT)
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text())
    lines = []

    with pytest.raises(under_test.DryRunStale):
        under_test.reconcile(ctx, cfg, {"session"}, say=lines.append, dry_run=True)

    assert lines[0] == "· claude catalog is 5 min past its refresh time; would refresh"
    assert "· model: claude-opus-4-1 is unavailable (would remap)" in lines
    assert not any(line.startswith("· tasks.review:") for line in lines)  # opus is listed
    assert _headless(_calls(log)) == []


def test_dry_run_with_fresh_catalog_and_current_leaves_is_unchanged(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-5-5")
    _claude_catalog(ctx)
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text())
    lines = []

    assert under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=True) is cfg

    assert lines == ["✓ claude models current (3 listed)"]
    assert _headless(_calls(log)) == []


def test_later_tick_spends_no_turn_and_prints_no_freshness_line(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    _claude_catalog(ctx, stale_at=PAST_STALE_AT)
    log = _cli_stub(
        ctx,
        "claude",
        written_catalog=_claude_catalog_text(),
        remap_output='{"model": "claude-opus-5-5"}',
    )
    lines = []

    under_test.reconcile(
        ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False, command_start=False
    )

    calls = _all_calls(log)
    assert calls[0] == ["auth", "status"]
    headless = _headless(calls)
    assert len(headless) == 1 and "--settings" not in headless[0]  # the remap only
    assert not any("refresh" in line for line in lines)
    saved = store.load_global(ctx.home)
    assert saved is not None and saved.llm.providers["claude"].model == "claude-opus-5-5"


def test_later_tick_with_missing_catalog_fails_without_a_turn(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    before = (ctx.home / "config.yaml").read_bytes()
    log = _cli_stub(ctx, "claude", written_catalog=_claude_catalog_text())

    with pytest.raises(ConfigError) as caught:
        under_test.reconcile(
            ctx, cfg, {"orchestrator"}, say=lambda _: None, dry_run=False, command_start=False
        )

    assert str(caught.value) == (
        f"Claude model catalog missing under {_catalog_dir(ctx)}; "
        "it is refreshed at the next command start"
    )
    assert _all_calls(log) == [["auth", "status"]]
    assert (ctx.home / "config.yaml").read_bytes() == before
```

- [ ] **Step 4: Update the watch and CLI fakes** — in `tests/unit/test_watch.py` every `reconcile` fake is keyword-exact; add `command_start` to all six and assert it is the first tick only:

1. `_current_models` autouse fixture (line 37): `lambda ctx, cfg, groups, *, say, dry_run, report_current, command_start: cfg`.
2. `test_watch_reconciles_selected_models_at_each_locked_tick` (`fake_reconcile`, line 94): signature gains `command_start`; add `assert command_start is (not calls)`.
3. `test_watch_first_tick_reconcile_failure_fails_loud` (line 184): signature gains `command_start`; add `assert command_start`.
4. `test_watch_later_tick_reconcile_failure_warns_and_keeps_previous_cfg` (line 210): signature gains `command_start`; add `assert command_start is (len(outcomes) == 3)` as the first line (true only on the first call, before the pop).
5. `test_watch_reports_current_on_entry_and_recovery_only` (line 246): signature gains `command_start`; add `starts = []` beside `requested`, `starts.append(command_start)`, and after `assert requested == [True, False, True, False]` add `assert starts == [True, False, False, False]  # recovery re-reports; it never re-refreshes`.
6. `test_watch_once_requests_current_report` (line 291): signature gains `command_start`; add `assert command_start`.

In `tests/unit/test_cli.py::test_provider_command_reconciles_before_worker` change the fake to `def fake_reconcile(ctx, cfg, leaves, *, say, dry_run, command_start=True):` and add `assert command_start is True` as its first assertion. This one is a guard, not a red test: the one-shot call sites do not change (the default carries the signal), so it passes before and after — the red tests for the signal are the two `later_tick` reconcile tests and the watch fakes above.

- [ ] **Step 5: Run everything touched and watch it fail for the right reasons**

Run: `uv run python -m pytest tests/unit/test_reconcile.py tests/unit/test_watch.py tests/unit/test_cli.py -q -x --no-header -p no:cacheprovider 2>&1 | tail -40` (drop `-x` to see the full set):
Expected: the new reconcile tests FAIL — the G4 reproducer and the override/freshness tests with `ConfigError: Claude model catalog missing under …` or with a plain `["-p", "Reply with exactly OK.", "--output-format", "text"]` turn where `REFRESH_TURN` is expected; the `command_start` tests with `TypeError: reconcile() got an unexpected keyword argument 'command_start'`; the rewritten `== 1` assertions with `2`; the watch tests with `TypeError` (missing `command_start`) from the fakes. `test_cli.py` passes.

- [ ] **Step 6: Implement `reconcile.py`**

(a) Module constant and helpers, placed directly above `def reconcile(`:

```python
# Claude Code never fetches its served model catalog while the user's settings
# env sets CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, so a plain no-model turn
# can neither create nor refresh the file; a turn carrying the --settings
# override (ClaudeProvider.headless_argv) can. The same setting freezes Claude's
# feature-flag cache, so the first override turn may only thaw that cache
# ("[servedCatalog] off (flag)" in ~/.claude/debug/latest) and the second writes
# the file. Two turns cover the observed worst case; there is never a third
# (design record 2026-10-27, Decision 3). The error text spells the bound out.
CLAUDE_REFRESH_TURNS = 2


def _read_claude_catalog(
    ctx: ToolContext,
) -> tuple[taskmodels.ClaudeCatalog | None, taskmodels.CatalogMissing | None]:
    try:
        return taskmodels.claude_catalog(ctx), None
    except taskmodels.CatalogMissing as exc:
        return None, exc


def _claude_age(read: taskmodels.ClaudeCatalog) -> str:
    if read.overdue_minutes is None:
        return "claude catalog carries no refresh time"
    return f"claude catalog is {read.overdue_minutes} min past its refresh time"


def _claude_cli_catalog(
    ctx: ToolContext, *, say: Callable[[str], object], dry_run: bool, command_start: bool
) -> taskmodels.ClaudeCatalog | None:
    """Claude's on-disk list, refreshed at a command's start when missing or past staleAt.

    Returns None only on a dry run that found no catalog (reported, never
    refreshed: a dry run runs no turn). Otherwise a catalog still missing
    raises — after the bounded turns, or at once on a later watch tick.
    """
    read, missing = _read_claude_catalog(ctx)
    if dry_run:
        if read is None:
            say(f"· {missing}; would refresh")
        elif not read.fresh:
            say(f"· {_claude_age(read)}; would refresh")
        return read
    turns = 0
    while command_start and turns < CLAUDE_REFRESH_TURNS and (read is None or not read.fresh):
        say("→ refreshing claude model list (nonessential traffic enabled)")
        ok, detail = docsllm.cli_model_probe(ctx, "claude", "", served_catalog=True)
        docsllm._step(say, ok, detail)
        turns += 1
        read, missing = _read_claude_catalog(ctx)
    if read is None:
        if turns:
            raise ConfigError(
                f"{missing} after two refresh turns; "
                "check the [servedCatalog] lines in ~/.claude/debug/latest"
            )
        raise ConfigError(f"{missing}; it is refreshed at the next command start")
    if turns and not read.fresh:
        # A cached list is a list of real models: its age is narrated, not fatal.
        say(f"· {_claude_age(read)}; continuing with the cached list")
    return read
```

(b) `reconcile` itself — signature, docstring, bucket loop; remove the leaf-triggered refresh block and the `None` catalog handling:

```python
def reconcile(
    ctx: ToolContext,
    cfg: Config,
    leaves: Collection[str],
    *,
    say: Callable[[str], object],
    dry_run: bool,
    report_current: bool = True,
    command_start: bool = True,
) -> Config:
    """Check only selected model leaves, then remap stale values as one transaction.

    At a command's start (``command_start``) a Claude CLI catalog that is
    missing or past its own ``staleAt`` is refreshed first — at most
    ``CLAUDE_REFRESH_TURNS`` no-model turns with nonessential traffic enabled —
    so the check runs against a current list; a later ``omc watch`` tick passes
    ``command_start=False`` and judges the on-disk list as it is, spending no
    turn. Codex's live catalog read is already its refresh. API documentation
    catalogs are already live, but their remap still uses the provider CLI's
    default model. A successful dry run reports every stale leaf (and a Claude
    catalog it would refresh) and signals the command dispatcher to stop before
    it resolves any model.
    """
    selected = _selected_leaves(cfg, leaves)
    if not selected:
        return cfg

    buckets = {(leaf.provider, leaf.source) for leaf in selected}
    catalogs: dict[tuple[str, str], list[CatalogModel]] = {}
    for provider, source in sorted(buckets):
        if source == "api":
            key = cfg.secrets.api_keys.get(provider, "")
            if not key:
                raise ConfigError(
                    f"api documentation backend configured for {provider} but no key is stored — "
                    "run omc configure"
                )
            catalogs[(provider, source)] = taskmodels.catalog(ctx, provider, docs_api_key=key)
        elif provider == "claude":
            # Before the compatibility check: a stale leaf is judged against the
            # refreshed list, and the remap turn is never spent against no list.
            read = _claude_cli_catalog(
                ctx, say=say, dry_run=dry_run, command_start=command_start
            )
            if read is None:
                raise DryRunStale()  # reported above; no list to judge the leaves against
            catalogs[(provider, source)] = read.models
        else:
            catalogs[(provider, source)] = taskmodels.catalog(ctx, provider)
```

In `stale_leaves().stale()` delete the two lines `if models is None: return True`. Delete the whole block from `refresh = {` through `catalogs[(provider, "cli")] = taskmodels.catalog(ctx, provider)` and the `stale = stale_leaves()` re-read that follows it (keep the single `stale = stale_leaves()` before the dry-run report; the `current_providers` line stays). In `say_current_models` drop `and models is not None`. In the remap loop `offered = {leaf.path: catalogs[(provider, leaf.source)] for leaf in provider_leaves}` (drop `or []`).

(c) `src/omc/watch.py`, the tick's call:

```python
                        cfg = reconcile(
                            ctx,
                            cfg,
                            model_groups,
                            say=_say,
                            dry_run=False,
                            report_current=first_tick or last_reconcile_failed,
                            command_start=first_tick,  # the refresh turn belongs to the entry tick only
                        )
```

- [ ] **Step 7: Run the touched suites to green**

Run: `uv run python -m pytest tests/unit/test_reconcile.py tests/unit/test_watch.py tests/unit/test_cli.py tests/unit/test_taskmodels.py tests/unit/test_docsllm.py -q`
Expected: all PASS. Then the whole unit tier once: `uv run python -m pytest -m "not e2e and not local_iterm2" -q -n auto` — all PASS.

- [ ] **Step 8: Lint and format** (the project build stage runs ruff):

Run: `uvx ruff format src/omc/reconcile.py src/omc/taskmodels.py tests/unit/test_reconcile.py tests/unit/test_watch.py tests/unit/test_cli.py && uvx ruff check src tests`
Expected: clean. Re-run Step 7's focused command if format touched a test.

- [ ] **Step 9: Commit**

```bash
git add src/omc/reconcile.py src/omc/watch.py tests/unit/test_reconcile.py tests/unit/test_watch.py tests/unit/test_cli.py
git commit -m "Refresh Claude's model catalog at command start with nonessential traffic enabled

The refresh turn now runs only when the on-disk catalog is missing or past
its own staleAt, at most twice per command start, carrying the --settings
override that lets Claude Code fetch the served catalog under user settings
that disable nonessential traffic. A later watch tick spends no turn; a
fresh catalog with a stale leaf goes straight to the remap turn.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Documentation — README recipe, regeneration, record amendment

Complexity: simple

**Files:**
- Modify: `.claude/skills/regenerate-readme/SKILL.md:108-114` (the model-reconciliation paragraph)
- Regenerate: `README.md` (via `/regenerate-readme`; never hand-edited)
- Modify: `docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md` (§6 and §8 amendment notes)

**Interfaces:** none (prose only).

- [ ] **Step 1: Correct the recipe sentence** — in `.claude/skills/regenerate-readme/SKILL.md` replace the paragraph

```
Stale-model reconciliation (`src/omc/reconcile.py`): design/implement/review
check the session models, dependency watch the docs model, against the
provider's listing with no model turn when current. Codex reads `codex debug
models` on the binary it launches and remaps a still-stale value immediately;
Claude keeps one no-model list refresh before remap. The provider's default
model picks a listed full id, which is saved and narrated; a failed remap
saves nothing.
```

with

```
Stale-model reconciliation (`src/omc/reconcile.py`): design/implement/review
check the session models, dependency watch the docs model, against the
provider's listing with no model turn when current. Codex reads `codex debug
models` on the binary it launches and remaps a still-stale value immediately.
At a command's start (and `omc watch`'s first tick only), a Claude model list
that is missing or past its own refresh time is refreshed first — one or two
quick no-model turns with nonessential traffic enabled, so a user setting
that disables it cannot freeze the list — then a still-stale value is
remapped; a list still missing after two turns stops the command. The
provider's default model picks a listed full id, which is saved and narrated;
a failed remap saves nothing.
```

- [ ] **Step 2: Regenerate the README** — invoke the `regenerate-readme` skill (`/regenerate-readme`) and follow its recipe in full (its step 5 runs the README guards: `uv run --frozen pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'` — in this worktree run that as `uv run python -m pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'`). Expected: the Configure section's reconciliation paragraph now describes the refresh-at-start behaviour; `README.md` no longer contains "Claude gets one no-model list refresh before remap"; guards PASS.

Run: `grep -n "no-model list refresh" README.md .claude/skills/regenerate-readme/SKILL.md` → no output. `git diff --check` → clean.

- [ ] **Step 3: Amend the 2026-10-15 record** — in `docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md` insert, as the first paragraph under the `## 6. Failure handling` heading and again as the first paragraph under `## 8. Edge cases and live-verify items`:

```
> **Amendment 2026-10-27** (see
> `2026-10-27-fix-claude-model-list-detection-design.md`): catalog creation
> under account auth has now been observed — Claude Code never writes the
> file while the user's settings set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`,
> so the "open claude once" hint below cannot succeed and is retired. The
> Claude refresh turn now runs at a command's start when the catalog is
> missing or past its own `staleAt` (at most two turns, carrying a
> `--settings` override that enables nonessential traffic), not when a leaf
> is stale. The text below is kept as written.
```

- [ ] **Step 4: Verify and commit**

Run: `uv run python -m pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'` → PASS; `git status --short` shows exactly `README.md`, the SKILL.md and the 2026-10-15 record.

```bash
git add README.md .claude/skills/regenerate-readme/SKILL.md docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md
git commit -m "Document the refresh-at-start Claude model list and amend the 2026-10-15 record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Live verification on this machine (record §6, last bullet)

Complexity: simple

**Files:**
- Modify: `docs/superpowers/specs/2026-10-27-fix-claude-model-list-detection-design.md` §6 (append the observation to the "Still to verify live during implementation" bullet, as the 2026-10-15 record did with its "Observed during implementation" notes)

**Interfaces:** consumes `reconcile._claude_cli_catalog` (Task 4) directly, so no remap and no config write can happen. **Side effects on the user's real Claude state, stated up front:** the current catalog file is moved to a backup directory (restored by hand if the refresh fails) and one or two real `claude -p` turns are spent on the default model. The conductor runs this task itself or dispatches it with those facts in the brief; it needs the user's settings file to still set the variable (verified present on 2026-10-27 — `grep -c CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC ~/.claude/settings.json` → `1`).

- [ ] **Step 1: Confirm preconditions and move the catalog aside**

```bash
grep -c CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC ~/.claude/settings.json   # expect 1
STATUS="$(claude auth status)"
CFG="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["configDirectory"])' "$STATUS")"
ORG="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["orgId"])' "$STATUS")"
ls -la "$CFG/cache/model-catalog/"
mkdir -p /tmp/omc-catalog-backup && mv "$CFG/cache/model-catalog/$ORG"-*-cc.json /tmp/omc-catalog-backup/
ls "$CFG/cache/model-catalog/" | grep -c "^$ORG-" || echo "catalog gone"
```

- [ ] **Step 2: Run the refresh step through omc's own code** (the worktree's omc, via `uv run --frozen python`; never an installed `omc`):

```bash
uv run --frozen python - <<'EOF'
from omc.reconcile import _claude_cli_catalog
from omc.toolctx import ToolContext

ctx = ToolContext.from_env()
read = _claude_cli_catalog(ctx, say=print, dry_run=False, command_start=True)
print("fresh:", read.fresh, "overdue:", read.overdue_minutes, "models:", len(read.models))
EOF
```

Expected: `→ refreshing claude model list (nonessential traffic enabled)`, `✓ default model works`, possibly a second pair, then `fresh: True overdue: None models: N` with N > 0 — within two turns.

- [ ] **Step 3: Confirm the file and the debug line**

```bash
ls -la "$CFG/cache/model-catalog/$ORG"-*-cc.json
grep -n "servedCatalog" ~/.claude/debug/latest | tail -5
```

Expected: a new catalog file whose mtime is now; the last `[servedCatalog]` line of the writing turn reads `ON`/"using served rows" (a preceding turn may read `off (flag)` — that is the two-turn layer). If the refresh FAILED instead: restore with `mv /tmp/omc-catalog-backup/* "$CFG/cache/model-catalog/"`, and stop — report the exact `[servedCatalog]` lines as a blocker for the conductor; do not loosen the bound.

- [ ] **Step 4: Record the observation and commit** — append to the record's §6 last bullet one sentence of the form: "Observed during implementation (YYYY-MM-DD, Claude Code X.Y.Z): with the setting on and the catalog deleted, `_claude_cli_catalog` wrote the file on turn N; the writing turn's `[servedCatalog]` line read `…`." Then:

```bash
rm -rf /tmp/omc-catalog-backup
git add docs/superpowers/specs/2026-10-27-fix-claude-model-list-detection-design.md
git commit -m "Record the live catalog refresh observed on Claude Code with nonessential traffic disabled

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review against the record

- **§2 Decisions:** 0/1 → Task 2 (override on the refresh turn only, variant G); 2 → Task 4 (missing or past `staleAt`, `command_start`, watch first tick); 3 → Task 4 (`CLAUDE_REFRESH_TURNS`, re-read after each); 4 → Tasks 1 + 3 (`now_ms`, struct beside `catalog()`, unusable `staleAt` = stale); 5 → Task 4 (ConfigError wording, `·` line for old-but-present); 6 → Task 3 (API path fresh, no clock) + Task 4 (watch); 7 → Task 5.
- **§4.1:** base/claude/codex signatures, `--allowed-tools` last, one argv element, no other caller changes — Task 2 (pressure-test note lists all callers).
- **§4.2:** one read, `fetchedAt` validation unchanged, malformed `staleAt` never raises, clock only via `now_ms` — Task 3.
- **§4.3:** bucket handled before the compatibility check, two turns max, old block and comment removed, dry-run contract (missing → one line + `DryRunStale`; old → one line + per-leaf verdicts; fresh → unchanged) — Task 4.
- **§4.4:** all five failure cases — Task 4 (`ProbeFailed` path is `docsllm._step`, unchanged).
- **§5:** amendment note, README recipe + regeneration, three quirk comments (claude.py, reconcile.py call site, taskmodels.py `staleAt`) — Tasks 2, 3, 4, 5.
- **§6 live-verify item** — Task 6.
- **§7 tests:** every listed test exists in Tasks 1–4; the watch fakes grow the keyword; `test_cli` dispatch fake guards it; E2E untouched.
- **Type consistency:** `ClaudeCatalog(models, fresh, overdue_minutes)` is used identically in Tasks 3, 4 and 6; `claude_catalog(ctx)` is the one reader name; `served_catalog` is the one keyword name across base/claude/codex/docsllm; `command_start` is the one signal name across reconcile/watch/tests; `REFRESH_TURN` matches Task 2's argv exactly.
