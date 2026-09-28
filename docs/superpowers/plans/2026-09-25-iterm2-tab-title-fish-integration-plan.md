# iTerm2 Branch Title Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the full Git branch visible as the iTerm2 tab title throughout omc's Claude/Codex sessions and fish shell transitions.

**Architecture:** A generic, short-lived title command selects a terminal adapter. The iTerm2 adapter launches a bounded Python API worker that resolves the caller session and pins its tab title; fish owns branch discovery and deduplicated refresh timing. Other shells retain their current prompt behavior and initialize through the generic command.

**Tech Stack:** Python 3.12+, iterm2 2.24 SDK on macOS, fish, pytest, uv, existing Docker lifecycle harness.

**Spec:** `docs/superpowers/specs/2026-09-25-iterm2-tab-title-fish-integration-design.md`

## Global Constraints

- Display the full branch, including the configured prefix; provider session names and `OMC_SLUG` retain the slug.
- Resolve the caller's session and its containing tab, never the currently focused tab.
- Branch text is literal data; use a tab user variable and a constant interpolation expression.
- `ToolContext` is the subprocess/environment boundary. New process launches use argv lists, never `shell=True`.
- API work is short-lived and bounded, including SDK authorization; no daemon, polling service, global fish configuration, or iTerm2 preference changes.
- API failures warn and fall back to OSC; this fallback does not guarantee a pinned title.
- Dry-run and headless paths perform no API operations.
- Fish retains the last valid branch outside repositories and on detached HEAD, and attempts an API refresh only when the desired branch changes.
- Preserve `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` and `tui.terminal_title=[]` without global configuration changes.
- Tests must run or fail with an actionable prerequisite message; never skip.
- Every task follows red/green for changed production behavior. Required finish gates cannot be bypassed.

## Review Focus

- A stale/malformed caller ID must never rename the focused tab: Task 1 targeting tests and Task 3 real unrelated-tab assertion.
- Legal branch names containing quotes, dollar signs, or parentheses must remain literal: Task 1 API argument tests and Task 2 real fish quoting test.
- Hanging SDK authorization must not strand a prompt or descendant process: Task 1 bounded process test.
- Duplicate fish lifecycle events and failed API attempts must not repeatedly spawn workers or print warnings: Task 2 real fish event/count tests.
- A failing command's status and a changed branch must survive prompt handling: Task 2 real fish status/branch tests, Task 3 live shell transitions.

## File Map

- `src/omc/terminals.py`: terminal detection, escape fallback, and generic effectful title-setting interface.
- `src/omc/terminal_title.py`: executable module entry point and stable argv builder for shell callers.
- `src/omc/iterm2_title.py`: isolated SDK worker, caller-session targeting, literal explicit tab title.
- `src/omc/toolctx.py`: bounded subprocess execution and timeout/process-group cleanup.
- `src/omc/start.py`, `src/omc/shells/{base,fish,bash,zsh,registry}.py`: full branch title and shell refresh integration.
- `tests/unit/test_terminals.py`, new focused terminal/worker/fish test modules, `tests/unit/test_start.py`: fast behavior and orchestration coverage.
- `tests/local/test_iterm2_title.py`: separately selected real host iTerm2/fish/provider acceptance coverage.
- `pyproject.toml`, `uv.lock`, `justfile`, `README.md`, `tests/e2e/test_e2e_interactive.py`: dependency, tier selection, user documentation, full-branch regression.

### Task 1: Generic terminal title command and bounded iTerm2 worker

Model: heavy coding tier

**Files:** Modify `src/omc/terminals.py`, `src/omc/toolctx.py`, `pyproject.toml`, `uv.lock`, `tests/unit/test_terminals.py`; create `src/omc/terminal_title.py`, `src/omc/iterm2_title.py`, `tests/unit/test_iterm2_title.py`, `tests/unit/test_terminal_title.py`, and focused bounded-worker tests as needed.

**Interfaces:**
- Consumes: existing `ToolContext.env`, `child_env()`, terminal detection and OSC formatting.
- Produces: `terminal_title_argv() -> list[str]` in `omc.terminal_title`, returning `[sys.executable, "-m", "omc.terminal_title"]` (prefix; caller appends `"--", title`).
- Produces: `run_title(ctx: ToolContext, title: str) -> int` in `omc.terminal_title`; exit 0 for adapter success, 1 for API failure with OSC fallback and concise stderr diagnostic.
- Produces: `Terminal.set_title(ctx: ToolContext, title: str) -> bool` selected through existing `detect_terminal`; preserve `title_sequence()` for fallback and existing callers.
- Produces: `ToolContext.run_bounded(argv, *, timeout, cwd=None, extra_env=None) -> subprocess.CompletedProcess[str]`; captures output, no stdin, kills/reaps the process group on timeout, raises built-in `TimeoutError` so callers do not import subprocess.
- Produces: worker `python -m omc.iterm2_title --session-id ID -- TITLE` with nonzero errors and no sensitive auth output.

- [ ] Write failing tests before implementation. Pin the generic command result, exact subprocess argv, failure fallback, malformed IDs, caller lookup independent of focus, missing session/tab, and literal title expression. Example:

```python
def test_portable_title_is_written(capsys, tmp_path):
    from omc.terminal_title import run_title
    ctx = ToolContext(home=tmp_path, env={})
    assert run_title(ctx, "feature/title") == 0
    assert capsys.readouterr().out == "\033]0;feature/title\007"
```

Use an asynchronous fake API only for unit-level protocol assertions: desired branch becomes `user.omc_title`, and `async_set_title` receives the constant `r"\(user.omc_title)"`. Wrong focus must not affect the chosen session. Add a real subprocess that exceeds the timeout and prove it is reaped; add descendant cleanup coverage. Reject control characters for the reusable explicit-title command to prevent OSC injection.

- [ ] Run `uv run pytest tests/unit/test_terminals.py tests/unit/test_iterm2_title.py tests/unit/test_terminal_title.py -q` and any bounded-worker tests, recording the expected red results.
- [ ] Implement the operation. Package `iterm2>=2.24,<3; sys_platform == 'darwin'` and lazy-import it only in the worker. The adapter uses a 5-second outer deadline. Parse the UUID suffix of `ITERM_SESSION_ID` (accept raw UUID or iTerm's prefixed form), reject malformed/missing identities without starting the SDK. Worker resolves `app.get_session_by_id(id)` then `app.get_window_and_tab_for_session(session)` and writes only that tab:

```python
await tab.async_set_variable("user.omc_title", title)
await tab.async_set_title(r"\(user.omc_title)")
```

The SDK may block while authenticating, so use the bounded subprocess seam. Do not mutate host preferences or install omc. Catch worker failure/timeout at the adapter, print a concise actionable warning once per command, emit portable OSC as best effort, and return failure. Avoid forwarding SDK exception text that could contain auth values.
- [ ] Run focused tests green, `just check`, formatting/lint for touched files, and self-review. Commit the task plus red/green evidence in its report.

Pressure-test evidence: `run_internal` is hidden CLI plumbing but no existing `omc.__main__` or `omc.cli.__main__` exists; the executable `omc.terminal_title` module avoids depending on a different omc found on PATH. `ToolContext.run` already accepts a timeout but does not provide process-group cleanup or a built-in timeout exception, hence the narrow bounded seam. The graph/source show terminal selection currently only returns strings; retaining the existing method keeps callers compatible until Task 2.

### Task 2: Full branch launch title and fish lifecycle refresh

Model: heavy coding tier

**Files:** Modify `src/omc/start.py`, `src/omc/shells/base.py`, `src/omc/shells/fish.py`, `src/omc/shells/bash.py`, `src/omc/shells/zsh.py`, `src/omc/shells/registry.py`, `tests/unit/test_start.py`, `tests/unit/test_shells.py`, `tests/e2e/test_e2e_interactive.py`, `README.md`; create `tests/unit/test_fish_title.py`.

**Interfaces:**
- Consumes: Task 1 `terminal_title_argv()` prefix and positional-title command contract; exit 1 must not prevent shell/provider execution.
- Produces: optional `title_argv: list[str] | None = None` on `Shell.build_invocation`, `exec_interactive`, and each concrete adapter. Existing callers without it retain compatible OSC behavior. Start always supplies the generic prefix.
- Produces: generated fish session-local refresh hooks with current branch/title and last-attempt caching; no externally installed fish function.

- [ ] Write failing launch tests: full branch passed to title/shell, provider `session_name` and `OMC_SLUG` still slug, helper represented in dry-run but never run there, no helper on headless, all four adapters call helper before startup when supplied. Update the existing real interactive OSC assertion to include `feature/`.
- [ ] Write real fish tests before changing fish production code. Use a temporary real Git repository and generated fish initialization. A fixture title recorder stands in only for the operation already tested in Task 1. Assert initialization records the full branch before the startup command, branch switches produce a new record, duplicate prompt/preexec/PWD events produce no new record, outside repo and detached HEAD retain the prior title, and failure still launches startup. For example:

```python
# The test harness runs the generated fish init with a recording title command.
# On branch feature/first it must record feature/first before STARTUP.
# Then run: git switch -c feature/second; emit fish_prompt; emit fish_prompt
# The title recorder must contain exactly ["feature/first", "feature/second"].
```

Cover a path containing spaces and quotes and a legal branch `feature/cost$USD(parent)`; verify no expansion/injection and `fish_title` emits the literal branch. Run a failing command followed by the refresh hook and assert the original status remains observable. Test a user's normal fish startup still runs. Missing fish fails with `brew install fish` (or the platform package-manager equivalent), never skips.
- [ ] Run focused tests and record red failures. Implement hooks with safe `shlex.quote` for generated literals, and quoted fish variable expansions for runtime branch values. Use `command git symbolic-ref --quiet --short HEAD` at lifecycle boundaries. Initial refresh must be explicit before startup because fish's interactive preexec hook does not reliably run for `-C` startup commands. Record the attempted desired title even after helper failure, so repeated events do not spam retries. A changed title permits another attempt. Do not run the Python helper from `fish_title` itself.
- [ ] Update all shell builders and `exec_interactive` forwarding together. Bash/zsh retain existing prompt OSC hooks, but use the generic operation once before startup. The sh fallback must also initialize its title when the helper is supplied. Use full branch for `title_seq` and title args; retain provider slug and suppression. Dry-run prints the title command without executing it, and headless returns before interactive title behavior.
- [ ] Update README with full-branch semantics, fish refresh boundaries, API enablement requirement, best-effort fallback, and no global settings changes. Run focused tests green, `just check`, formatting/lint and self-review. Commit and report evidence.

Pressure-test evidence: graph context/impact for `Shell.exec_interactive` and `Shell.build_invocation` identifies both start's dry-run builder and the real exec path, plus all concrete adapters. `FishShell.build_invocation` already emits its title before startup, so the rewrite must preserve ordering. Provider title suppression already exists and should not be rewritten. Shell code can execute the Git CLI as part of generated integration; new Python subprocess launches stay within ToolContext.

### Task 3: Real iTerm2 and provider acceptance harness

Model: heavy coding tier

**Files:** Create `tests/local/test_iterm2_title.py` and small local-only helpers if required; modify `pyproject.toml` marker declarations, `justfile` tier selection, and README verification instructions.

**Interfaces:**
- Consumes: production generic title command, generated fish integration, and existing provider launch argv/suppression.
- Produces: `just iterm2-tests` selecting the host-only `local_iterm2` marker, explicitly outside the normal unit/Docker tiers.

- [ ] Add a host test that fails explicitly unless macOS, iTerm2 API, fish, and selected provider authentication are usable. The test creates disposable iTerm2 test tabs only, records their IDs, and closes only those tabs in `finally` cleanup. It must never rename unrelated user tabs.
- [ ] Drive the production title command using the disposable tab session identity, read back the evaluated tab title, and assert full-branch equality. Focus a different disposable tab and ensure caller targeting remains correct; include a split session. Store hostile expression-looking text as branch data and prove literal rendering.
- [ ] Inject competing OSC 0/1/2 title writes as terminal output (`Session.async_inject`) and assert the explicit tab title remains. This is real iTerm2 protocol verification, not a stub.
- [ ] Launch real Claude and Codex TUI sessions in the disposable terminal through the generated fish integration, with existing production suppression. Keep runs bounded and prompts trivial, use ephemeral provider-session configuration where needed, and verify the title during the session and after exit. Do not modify normal host provider config or credentials. Log versions and results without credentials; session naming stays the slug.
- [ ] Select host tests separately using:

```make
check:
    uv run pytest -m "not e2e and not local_iterm2" -q

iterm2-tests *args:
    uv run pytest -m local_iterm2 -q tests/local {{args}}
```

Declare the marker in pyproject; default Docker `e2e` selection excludes these because they have only the host marker. Add unit assertions only where selection has an existing contract test. Run `just iterm2-tests` with actual prerequisites and record result. Missing prerequisites are a genuine blocker to full verification, not a reason to weaken assertions.
- [ ] Run `just check`, formatting/lint, self-review and commit harness/docs. Root then performs final whole-branch review and omc:finish, including the existing Docker smoke and full serial provider lifecycle matrix. Do not run that matrix from the task worker.

Pressure-test evidence: Linux Docker cannot host macOS iTerm2. Existing interactive tests prove emitted OSC and provider session naming but not GUI tab override persistence. A distinct marker/recipe preserves fast checks and explicit fail-loud integration selection. The repository already has real TUI conversation drivers and account-backed Docker lifecycle coverage; this task supplements them without duplicating their lifecycle assertions.

## Self-review and execution decisions

All four spec sections map to the three tasks. Every shared interface is produced before its consumer. Review Focus cases are assigned above. Optional shell arguments preserve old direct-builder tests while start uses the new operation. Bounded worker cleanup includes SDK descendants. No global install or preferences change is part of this plan. Current source `pyproject.toml` is version 0.1.8 while the initial lock was 0.1.7; uv's normal lock refresh is expected with Task 1's dependency change.

Model tiers are resolved at dispatch: top tier for reviews and judging, heavy coding tier for these multi-file integration tasks, never the cheap/fast tier. Implementation and finishing are already authorized by the user's direct omc:implement invocation.
