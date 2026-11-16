# Polled stage hook scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `check`, `build` and `verify` become executables `.omc/bin/<stage>.sh` that omc starts detached under a supervisor, polls with one fixed loop, limits per the hook's own instructions, kills, and ties to a running `omc watch` and to its worktree.

**Architecture:** Three new modules. `stageid.py` resolves the project and worktree IDs. `stagedef.py` holds the hook states, the instruction template and the validator. `stagerun.py` holds the supervisor, `run.json`, start, wait and kill, and a `python -m omc.stagerun supervise` entry. They sit behind a new `omc internal stage` verb that prints `OMC_STAGE_RUN`. `ToolContext` gains the detached spawn, the logged spawn and the two signal calls. `watchlock.py` gains an "alive" lock that watch holds, which is what a run probes. The three proxy skills share one loop text, the behavior layer's stage-gate rule learns CRITICAL STOP verdicts and the `Fix cycles` bound, and `watch --auto-build` drives `build.sh` with no LLM.

**Tech Stack:** Python 3.12 (stdlib `fcntl`, `signal`, `uuid`, `json`), filelock (existing), pytest + xdist, Markdown skills, bash/just, Docker E2E.

**Spec:** `docs/superpowers/specs/2026-11-16-polled-stage-hook-scripts-design.md`. Read it whole before any task. Section numbers below (§6.4, U6, A1, …) refer to it.

## Bootstrap constraint (orchestrator call; binding for every task)

This branch's own gates run through the **installed** omc plugin 0.1.50. Its proxies read `.omc/skills/{check,build,verify}/SKILL.md`, and the installed `omc` binary has no `omc internal stage`. If this branch deleted those three stage skills, the installed proxies would report every stage unconfigured, and this branch's own check and verify gates, finish included, would pass vacuously. Therefore:

- Task 10 **adds** `.omc/bin/{check,build,verify}.sh` and their `.instructions.md` for this repo as §6.7 says, and **keeps** `.omc/skills/{check,build,verify}/SKILL.md` unchanged. This is harmless under the new design: a present hook wins, and "unmigrated" fires only when there is no hook (§6.2).
- **Deviation from record §6.7** ("`.omc/skills/{check,build,verify}` are removed"): the audit amends the record to say they are removed in a follow-up, once the user has updated omc to this version. The dogfood tests that read them (`test_dogfood_build_stage`, `test_dogfood_verify_stage_gates_on_parallel_e2e_only`, `test_dogfood_stage_and_context_skills`) stay green and untouched.
- No task runs `omc install`, `uv tool install` or `just install`.

## Global Constraints

- Red first, per `.omc/config/AGENTS.md`: write the test, run it, see it FAIL for the expected reason, implement, see it pass, and commit test and code together. Never `skip`, `skipif` or a conditional guard. A missing prerequisite is a `pytest.fail` naming the command that provides it.
- Each task's gate afterwards is the project's `/omc:check` (= `just check`, the unit suite). E2E (`just e2e-tests`) never runs during implement. E2E edits are validated by their unit-tested helpers and by `uv run python -m pytest --collect-only -q <module>`.
- Run tests from the worktree as `uv run python -m pytest <paths> -q -p no:cacheprovider`. After any `uv run`, restore `uv.lock` when it is the only drift (`git checkout uv.lock`), and never commit it.
- **Gate:** every task's gate is an honest full-green `/omc:check` (`just check`, every unit test passing). This host is NixOS: only `/bin/sh` and `/usr/bin/env` exist (no `/bin/cat`, `/bin/sleep`, `/bin/mkdir`, `/bin/bash`, `/usr/bin/git`). Tasks 0 and 0b make the suite fully green here before any feature work starts; Task 1 starts only on a fully green `just check`.
- New hook scripts in unit tests start with `#!/bin/sh`. They use shell builtins (`printf`, `echo`, `:`, `read`, `trap`, `exit`) and call any other binary by the absolute path Task 0's helper resolves (`_stubs.SLEEP`, `_stubs.host_tool("name")`), never a literal `/bin/<tool>` (it does not exist here) and never a bare name. Instructions are printed with `printf '%s' <shlex.quote(text)>`, never `cat`.
- No unit test writes outside `tmp_path`. Stage logs go to `OMC_STAGE_ROOT` (Task 5) pointed at `tmp_path`. Every test must run under `-n auto`. No test sleeps more than about 15 s or sets a timeout above 300 s outside the `expensive` tier (this is the `.omc/skills/review` rule).
- `ToolContext` stays the only subprocess, signal and env boundary. `stagerun.py` and `stagedef.py` import neither `subprocess` nor `os.kill`/`os.killpg`; they call `ctx.*`. Argv lists only.
- Exit codes: 0 answered, 1 error (`error: …` on stderr, no verdict line), 2 `refused`.
- `OMC_STAGE_RUN` is a single JSON line on stdout, never wrapped.
- The `OMC_STAGE` line is byte-identical to today's in all three proxies.
- Run states are `Literal` strings, not an `Enum`, matching `src/omc/fish_integration.py:17`. Verdict objects are `@dataclass(frozen=True)` with `to_json()`, matching `wtconfig.py:194` and `gitnexus.py:424`.
- `README.md` is regenerated with `/regenerate-readme` (Task 12), never hand-edited.
- Each task ends with the implementer's commit. Complexity labels dispatch the workers; the plan stores no model ids.

## Builder choices (record §8, resolved here; each task repeats the ones it owns)

| §8 item | Choice | Grounding |
|---|---|---|
| How a process tells a watch is running | A second lock, `omc-watch-alive.lock`, in the same shared git dir as the instance lock. Watch takes it `LOCK_EX` **blocking**, right after `acquire_instance`, and holds it for its lifetime (`--once`: one tick, auto-build included). `--clear-mutex` unlinks it like the instance lock. A probe takes `LOCK_SH|LOCK_NB`: acquired → no watch (release at once); refused → a watch is running. | `acquire_instance` takes `LOCK_EX|LOCK_NB`, so any probe flock on the instance file would make a starting watch bail. Shared probes never conflict with each other, and a starting watch waits microseconds behind a probe instead of bailing. flock conflicts are per open file description, so watch's in-process auto-build probe still sees watch's own lock. |
| `run.json` fields beyond those named | `run, stage, state, pid, pgid, started, ended, boot, head, dirty, exit_code, verdict, reason, worktree_id, log, instructions, result, fix_cycles, warnings, timeout_s, quiet_s`. The supervisor also mirrors every write to `.cache/omc/last-<stage>.json`, so `wait`/`kill` can answer for a stage whose run another stage superseded. | §6.4 `kill` "answers the last recorded state of that stage". One writer per file is kept: the supervisor. |
| Where `wait` keeps its log position | `.cache/omc/wait.json` = `{"run": <id>, "offset": <bytes>}`, written only by `wait`. A different `run` resets the offset to 0. | Not in `run.json` (§8). |
| New log lines, cut to 40 | Read the bytes from `offset` to EOF, decode UTF-8 with `errors="replace"`, `splitlines()`, keep the last 40. The new offset is EOF. More than 1 MiB new: read only the last 64 KiB. | §6.4 `tail`. |
| `quiet` in answers | `now - log mtime`, integer seconds; 0 when the log is missing. | No extra writes. |
| The 7-day prune | `start` removes, under `<stage_root>/<project-id>/*/`, every entry whose mtime is older than 7 days (files with `unlink`, `*.d` dirs with `shutil.rmtree`), then empty worktree dirs. | §6.4 start. |
| Stage root | `/tmp/omc-stages`, overridable by `OMC_STAGE_ROOT` in `ctx.env`. Tests set it. | §6.4 path; the review rule forbids fixed host paths in tests. |
| `ToolContext` signatures | `spawn_detached(argv, *, cwd, extra_env=None) -> Popen`; `spawn_logged(argv, *, cwd, log_path, extra_env=None) -> Popen`; `signal_pid(pid, sig) -> bool`; `signal_group(pgid, sig) -> bool`; `boot_time() -> float` (Task 2). | `run`/`run_bounded`/`run_supervised`/`stream` all wait for the child, so a new parameter cannot express "detached". |
| How omc starts itself as the supervisor | `[sys.executable, "-m", "omc.stagerun", "supervise", <spec JSON>]`, as `terminal_title.terminal_title_argv` does. | Keeps `omc internal stage` at the four verbs of §6.4. |
| How kill and supersede reach the supervisor | `SIGTERM` = reason `kill`, `SIGUSR1` = reason `superseded`; no request file. | Keeps one writer of `run.json`. |
| Recorded process group after a machine restart | The supervisor records `boot = ctx.boot_time()`. Recovery of a `died` run signals the recorded group only when `abs(ctx.boot_time() - boot) < 60`. | §8 risk; apuntalo compares the start with the boot time. |
| The 10 s cap as a parameter | `show_instructions(..., warn_s=1.0, stop_s=10.0)`. The unit test calls it with `stop_s=0.5`; the CLI always uses 10 s. | No 10 s test in the fast gate. |
| Where the Codex >540 s test lives | The `expensive` tier, in a new file with its own `@pytest.mark.timeout(900)` and a new `just codex-stage-poll` recipe: an evidence run outside the gate (coordinator decision). A deviation from §6.5 for the audit. | `.omc/skills/review` forbids >300 s ceilings outside `expensive`, and no stage gates on it. |
| How `watch --auto-build` shows progress | `stagerun.run_foreground` polls `run.json` and the log every `POLL_S` and passes each new log line to `on_line`, which feeds `ProgressTracker`. Ctrl-C kills the run, then re-raises. | `_auto_build` feeds lines today (`watch.py:_auto_build`). |
| This repo's limits | `check`: `Timeout: 10m`, `Quiet timeout: 5m` (unit suite measured at 15 s on 2026-11-16). `build`: the implementer measures `just build` and sets `Timeout` to `10m` or 10× the measurement, whichever is larger, and `Quiet timeout: 5m`. `verify`: `none`/`none` (not measurable here). | §8 "none until measured". |
| How `verify.sh` decides Codex code changed; subset selection | The changed files are `git diff --name-only origin/main...HEAD` plus `git diff --name-only HEAD`. A shell `case` lists `src/omc/providers/codex.py`, `tests/e2e/conversation.py`, `tests/e2e/codex_plugin_payload.py`, `tests/e2e/codex_auth.py`, `docker/conversation.py` and `docker/setup-plugins.sh` (the whole file: the "codex half" is not detectable by name). With arguments: `verify.sh codex-gate <node ids>` runs `just codex-gate <ids>`; any other arguments run `just e2e-tests <args>`. | `.omc/skills/verify/SKILL.md` today. |
| `.cache/.gitignore` | Written (`*\n`) whenever `.cache/.gitignore` is missing, not only when omc creates `.cache/`. | A pre-existing `.cache/` would otherwise show `run.json` in `git status`. |

## Review Focus

1. **A recycled PID or process group.** A `died` run's recorded `pgid` may belong to a stranger after a reboot. In a Docker variation forked from a snapshot, the recorded PIDs belong to the old container's PID namespace. Expected: omc signals a recorded group only when the boot marker matches, and only while `run.json` says `running` or `held`. Task 5b pins it with a forged `run.json` whose `boot` is 0: `start` must not signal its `pgid`, which points at a live helper process the test owns. Golden snapshots are taken only after stage runs have ended (Task 11).
2. **A probe that makes a starting watch bail.** Expected: `omc watch` never prints `WATCH_BAIL_MSG` because a stage probe ran. Task 4 pins it: a helper holds the alive lock `LOCK_SH`, the way a probe does for that instant, while a real `run_watch --once` starts. Watch must block, then run, never bail. Shared probes never block each other: two concurrent probes both answer "no watch".
3. **A hook that ignores SIGTERM or double-forks.** Expected: after 30 s, `SIGKILL` reaches the whole process group, and the supervisor reaps its direct child: a zombie leader keeps `killpg(pgid, 0)` succeeding. Task 5b pins it with a hook that sets `trap '' TERM` and runs `kill` with a grace of 1 s (the grace is a parameter of the internal `_end_group`; the CLI always uses 30 s).
4. **Two sessions in one worktree.** Session A waits on a verify run, and session B starts a check. Expected: A's next `wait verify` answers from `last-verify.json` with state `killed`, a reason starting `superseded`, and A's own `run`, never B's check outcome. Task 5b pins it.
5. **Instructions with CRLF line endings or a trailing blank section.** Expected: `\r\n` parses like `\n`, and a heading with an empty body is valid (sizes still count). Task 3 pins both.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/omc/stageid.py` (new) | `project.id`, `worktree.id`, `.cache/` + `.gitignore` | 1 |
| `src/omc/toolctx.py` | `spawn_detached`, `spawn_logged`, `signal_pid`, `signal_group`, `boot_time` | 2 |
| `src/omc/stagedef.py` (new) | Hook states, `STAGE_TEMPLATE`, `parse_instructions`, `show_instructions`, `refresh_template`, CRITICAL STOP sentences | 3 |
| `src/omc/watchlock.py`, `src/omc/watch.py` | Alive lock: `ALIVE_LOCK`, `hold_alive`, `release_alive`, `watch_alive`; watch holds it | 4 |
| `src/omc/stagerun.py` (new) | Supervisor, `run.json`, `start_run`/`wait_run`/`kill_run`/`run_foreground`, prune | 5a, 5b |
| `src/omc/internal.py` | `omc internal stage {start,wait,kill,template}`, `OMC_STAGE_RUN` | 6 |
| `skills/{check,build,verify}/SKILL.md` | The shared loop; verify's subset and `kill` inputs | 7 |
| `src/omc/distribution/AGENTS.md`, `skills/finish/SKILL.md`, `skills/integrate/SKILL.md` | Stage-gate rule, finish wording, integrate migration | 8 |
| `src/omc/watch.py`, `src/omc/cli/__init__.py` | `--auto-build` runs `build.sh` | 9 |
| `.omc/bin/*`, `.omc/config/AGENTS.md`, `.omc/skills/explain-context/SKILL.md`, `justfile` | This repo's hooks and contracts | 10 |
| `tests/e2e/*` | Hook fixtures, watch stand-in, held-verify E2E, Codex poll E2E | 11 |
| `.claude/skills/regenerate-readme/SKILL.md`, `README.md` | Regenerated docs | 12 |
| `tests/unit/_stubs.py` (+ the files Task 0 lists), `src/omc/assets/omc-title.fish` | Host-portable tool paths; `host_tool`, `tool_path_dir` | 0, 0b |
| `tests/unit/_stagekit.py` (new) | Shared unit helpers: repo, hook writer, watch holder, `SLEEP` | 1, extended in 3–5 |

---

### Task 0: Host-portable tool paths in unit tests (+ the fish asset's `/bin/mkdir`)

Complexity: medium

**Files:**
- Modify: `tests/unit/_stubs.py` (shared helper; the `/bin/cat` uses at lines 48, 50, 76, 79)
- Modify, every hard-coded host tool path in:
  - `tests/unit/test_worktree.py:119,122`
  - `tests/unit/test_gitnexus_refresh.py` (lines 680, 694, 705, 756, 770, 772, 779, 793, 931, 995, 998, 1140, 1143)
  - `tests/unit/test_gitnexus_update.py:374,573,605,606,608`
  - `tests/unit/test_internal.py:816,817,819,840`
  - `tests/unit/test_fish_hook.py:50,268,300,332`
  - `tests/unit/test_fish_title.py:204,207`
  - `tests/unit/test_installed_wheel.py:159,166`
  - `tests/unit/test_e2e_image_holders.py:119,249`
  - `tests/unit/test_e2e_provider_support.py:52`
  - `tests/unit/_mutexproc.py:59` (shebang only: watch runs it as `bash <hook>`, so it becomes `#!/bin/sh`-agnostic text: keep `bash`, drop `/bin/`, or use `host_tool("bash")`)
- Modify: `src/omc/assets/omc-title.fish:65` (`command /bin/mkdir -p $dir` → `command mkdir -p $dir`; `command` already bypasses functions and aliases, which is why the absolute path was there)
- Leave alone:
  - `SHELL="/bin/bash"` / `"SHELL": "/bin/bash"` values that are only data for shell detection (`test_shells.py`, `test_implement.py:58`, `test_review.py:60`, `test_session.py`, `test_start.py:65`, `test_start_mutex.py:91`). They are never executed; verify that with a grep for `SHELL` consumers in `src/omc/shells`.
  - `tests/e2e/**`: it runs in Docker, where `/bin/*` exists.
- Test: `tests/unit/test_host_tools.py`, `tests/unit/test_fish_title.py` (the asset test)

**Interfaces:**
- Produces in `tests/unit/_stubs.py`:
  - `host_tool(name: str) -> str` returns `shutil.which(name)` resolved to an absolute path and cached (`functools.cache`). When the tool is missing it calls `pytest.fail(f"host tool {name!r} not found on PATH: install it (e.g. `nix profile add nixpkgs#coreutils`); unit stubs call it by absolute path", pytrace=False)`. Never a skip.
  - Constants, resolved lazily through `host_tool` the first time each is used, so a missing tool fails only the tests that need it: `CAT`, `SLEEP`, `MKDIR`, `RM`, `BASH` and `GIT`, plus any other tool the replacements need.
  - Paths stay absolute, so stubs keep working on their restricted PATH (`.omc/config/AGENTS.md`).
- Every replaced literal becomes an f-string interpolation of the helper value. A string that is not an f-string today becomes one, with its `{`/`}` doubled where needed. Python argv lists such as `["/bin/sleep", "0.6"]` become `[SLEEP, "0.6"]`, and `ctx.git_bin = "/usr/bin/git"` becomes `ctx.git_bin = GIT`.

- [x] **Step 1: Write the failing tests:**
  - `tests/unit/test_host_tools.py::test_no_hardcoded_host_tool_paths`: scan `tests/unit/*.py` and `src/omc/assets/*.fish` with `re.compile(r"/(?:usr/)?bin/(?!sh\b|env\b)[a-z][a-z0-9_.-]*")`. Skip lines containing `SHELL` and the file `test_shells.py` (detection data); there must be no other match.
  - `test_host_tool_resolves_absolute`: `Path(host_tool("cat")).is_absolute()`.
  - `test_host_tool_missing_fails_loud`: `pytest.raises(pytest.fail.Exception, match="host tool 'no-such-tool-omc'")`.
  - `test_make_stub_runs_on_restricted_path`: `make_stub(bindir, "tool", stdout='{"a": 1}', stderr="warn")` run with `env={"PATH": str(bindir)}` → stdout `'{"a": 1}\n'`, stderr `"warn\n"`, rc 0.
  - In `tests/unit/test_fish_title.py`, `test_title_hook_creates_request_dir_with_host_mkdir`: fish sources the asset with `$__omc_title_home` under `tmp_path` and a PATH holding the stub `omc` helper dir plus `dirname(host_tool("mkdir"))`. Use the existing title-hook harness in that file, and assert `title-request/` exists after one prompt.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_host_tools.py tests/unit/test_fish_title.py -q -p no:cacheprovider` (expected: FAIL, the scan lists the literals). Then run `just check` (expected: 245 failed on this host, measured 2026-11-16).
- [x] **Step 3: Implement** the helper, the replacements and the one-word fish fix.
- [x] **Step 4: Run** the two files (expected: PASS), then `just check`. Expected: about 67 failures remain (measured with a trial patch: `/bin/cat` alone leaves 129, and all paths leave 67). None of them may be in `test_host_tools.py`. Record the remaining list in the commit body for Task 0b.
- [x] **Step 5: Commit** `git commit -m "test: resolve host tools by absolute path; fish title hook uses command mkdir"` (test files + `omc-title.fish`)

---

### Task 0b: Fully green `just check` on this host — root-cause the remaining failures

Complexity: high

**Files:** whatever each root cause needs. Expected, from the 2026-11-16 measurement after Task 0's trial patch:
- `tests/unit/test_codex_plugin.py` (1 failure)
- `tests/unit/test_e2e_wire_mcp.py` (4)
- `tests/unit/test_fish_hook.py` (11)
- `tests/unit/test_gitnexus_refresh.py` (17)
- `tests/unit/test_watch.py` (26)
- `tests/unit/test_internal.py` (1)
- `tests/unit/test_installed_wheel.py` (1)
- `tests/unit/test_e2e_image_holders.py` (2)
- `tests/unit/test_worktrunk_real.py` (3)
- `tests/unit/test_configure_pty.py` (1)

Product files are in scope only when the root cause is in the product.

**Rules (binding):**
- Work file by file, using `superpowers:systematic-debugging`. Before any fix, write down the root cause with evidence in the commit body: the failing assertion, the command that reproduces it, and what differs on this host.
- Never skip a test, never add `skipif`, never weaken an assertion's intent.
- A missing host tool may be installed with `nix profile add nixpkgs#<pkg>`, noted in one line in the commit body. The unit tests then still resolve it via `host_tool`.

**Known first findings (from the measurement; confirm before fixing):**
- **Restricted-PATH tests that need `git` or `bash`** (`test_codex_plugin.py::test_remote_preflight_keeps_git_config_from_normal_home`: `No such file or directory: 'git'`; `test_e2e_wire_mcp.py`: `No such file or directory: 'bash'`). The tests build PATH from a stub dir alone and relied on `/usr/bin` being searched. Fix: add a shared `_stubs.tool_path_dir(*names) -> str`. It creates a per-test `tmp_path`-scoped directory of symlinks to `host_tool(name)` for exactly the named tools, and the test puts it on the stub PATH. On NixOS, never put `dirname(host_tool("git"))` there: `/run/current-system/sw/bin` holds every tool, which would defeat the restricted PATH.
- **A bare `env` in fish-hook scripts** (`test_fish_hook.py`: `bash: line 1: env: command not found`). Find which script runs `env` on a restricted PATH: the test harness or `src/omc/assets/omc-title.fish` and its helper invocation. Make it portable: `/usr/bin/env` exists on every POSIX host this repo supports, or use the `host_tool` symlink dir. If the bare `env` is in the shipped fish asset, the fix is product code with a test.
- **Worktrunk message drift** (`test_worktrunk_real.py`: the expected regex is `pre-start command failed: exit status: 7`, but the installed `wt` says `✗ pre-start project hook failed (exit code 7)`). Make the assertion accept the installed `wt`'s wording while keeping its intent: the failure names the pre-start hook and exit 7, e.g. `r"pre-start (command|project hook) failed.*(exit status: 7|exit code 7)"`. Never pin or downgrade `wt`. Also check whether product code parses that message (`grep -rn "pre-start" src/omc`); if it does, the product needs the same tolerance, with a test.
- **The PTY I/O error** (`test_configure_pty.py::test_terminal_loss_is_an_error_and_does_not_loop`: `OSError: [Errno 5] Input/output error` from `mininterface`'s `sys.stdin.read(1)`). Root-cause whether it is the code (the error path the test pins) or this agent sandbox's PTY. If it is truly the sandbox, say so in the task's commit body with evidence: the same test run outside the agent sandbox, or a minimal `pty.openpty` repro showing the sandbox behavior. Then ask the coordinator; do not change the test.
- **Stale-knowledge assertions** (`test_watch.py::test_healed_token_returned_by_tick`: `'knowledge-stale:index-unknown' == 'healed'`, plus 25 more in `test_watch.py` and 17 in `test_gitnexus_refresh.py`). These use a node-stub GitNexus. `index-unknown` suggests the stubbed `analyze` never wrote `.gitnexus/meta.json` (an earlier error: `No such file or directory: '/usr/bin/git'` inside the stub). Confirm after Task 0: if they pass then, nothing more is needed. Otherwise trace the stub script's stderr to its cause.
- `test_internal.py`, `test_installed_wheel.py` and `test_e2e_image_holders.py` (`assert [['rm', '-f']] == []`): root-cause each the same way. The image-holder case is the `scripts/e2e.sh` runner under a stub PATH; check for a bare tool the runner calls that resolved differently on this host.

- [x] **Step 1: Reproduce** each file's failures one by one (`uv run python -m pytest <file> -q -p no:cacheprovider -x`), and write the root cause down before touching code.
- [x] **Step 2: Red:** the failing tests are the red tests. Where the fix adds a helper (`tool_path_dir`), first add a test for it in `tests/unit/test_host_tools.py` and see it fail.
- [x] **Step 3: Fix** per root cause, one commit per root cause.
- [x] **Step 4: Run** `just check`. Expected: fully green, zero failures. Run it twice to rule out flakes.
- [x] **Step 5: Commit** each root cause separately, e.g. `git commit -m "test: restricted-PATH stubs get a symlink dir for git and bash"`.

---

### Task 1: Identity — `project.id`, `worktree.id`, `.cache/`

Complexity: medium

**Files:**
- Create: `src/omc/stageid.py`
- Create: `tests/unit/_stagekit.py`
- Test: `tests/unit/test_stageid.py`

**Interfaces:**
- Produces:
  - `ensure_cache(root: Path) -> Path` returns `<root>/.cache`. It creates the directory if needed and writes `.cache/.gitignore` = `"*\n"` when that file is missing.
  - `project_id(primary: Path) -> str` reads or creates `<primary>/.omc/project.id` (one UUID and `\n`). Missing or empty → create with `uuid.uuid4()`. Content that is not a UUID → `OmcError(f"{path}: not a UUID — fix or delete it")`.
  - `worktree_id(root: Path) -> str` reads or creates `<root>/.cache/worktree.id` = `{"id": "<uuid>", "path": "<str(root.resolve())>"}`. Unparseable, or a `path` that is not this root → replace it with a new ID.
  - `@dataclass(frozen=True) class StageIds: project: str; worktree: str; primary: Path; root: Path`.
  - `resolve_ids(ctx: ToolContext) -> StageIds` uses `wtconfig.repo_root(ctx)` / `primary_root(ctx)` (process cwd). Outside a repo → `Refusal("not inside a git repository")`.
  - `tests/unit/_stagekit.py`: `git_repo(path: Path) -> Path` (init, one commit on `main`) and `add_worktree(repo: Path, path: Path, branch: str) -> Path` (`git worktree add -b`).
- Writes are atomic: a private `_write_atomic(path, text)` (temp sibling + `os.replace`), copied from `title.py:_write_atomic` as each module does today.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_stageid.py`:
  - `test_project_id_created_once_and_stable`: two calls give the same string, `uuid.UUID(value)` parses it, and the file is `value + "\n"`.
  - `test_project_id_from_worktree_lands_in_primary`: `monkeypatch.chdir(worktree)`, `resolve_ids(ctx).project` equals `(repo / ".omc/project.id").read_text().strip()`, and `not (worktree / ".omc/project.id").exists()`.
  - `test_project_id_rejects_garbage`: file content `"nope\n"` → `OmcError` whose message contains `project.id`.
  - `test_worktree_id_copied_from_other_worktree_is_regenerated`: copy primary's `.cache/worktree.id` into a worktree. `worktree_id(worktree) != worktree_id(primary)`, and the file's `path == str(worktree.resolve())`.
  - `test_primary_has_its_own_worktree_id`.
  - `test_cache_gitignore_hides_everything`: after `ensure_cache(repo)` and writing `.cache/x`, `git status --porcelain` is empty.
  - `test_resolve_ids_outside_repo_refuses` → `Refusal`.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_stageid.py -q -p no:cacheprovider`. Expected: FAIL, `ModuleNotFoundError: omc.stageid`.
- [x] **Step 3: Implement** `src/omc/stageid.py` and `tests/unit/_stagekit.py` with the interfaces above.
- [x] **Step 4: Run** the same command. Expected: PASS.
- [x] **Step 5: Commit** `git add src/omc/stageid.py tests/unit/_stagekit.py tests/unit/test_stageid.py && git commit -m "feat(stage): project and worktree IDs"`

---

### Task 2: `ToolContext` — detached spawn, logged spawn, signals, boot time

Complexity: medium

**Files:**
- Modify: `src/omc/toolctx.py` (new methods after `stream`)
- Test: `tests/unit/test_toolctx_detach.py`

**Interfaces:**
- Produces, all on `ToolContext`:
  - `spawn_detached(self, argv: Sequence[str], *, cwd: str | os.PathLike[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen[bytes]`. The env is `{**child_env(), **extra_env}`. stdin, stdout and stderr are `DEVNULL`, `start_new_session=True`, and nothing is waited for.
  - `spawn_logged(self, argv, *, cwd, log_path: Path, extra_env=None) -> subprocess.Popen[bytes]`. Same env. stdin is `DEVNULL`; stdout and stderr both go to one fd opened with `open(log_path, "ab")`, which the parent closes after `Popen`. `start_new_session=True`, so pgid == pid.
  - `signal_pid(self, pid: int, sig: int) -> bool` and `signal_group(self, pgid: int, sig: int) -> bool` (`os.kill` / `os.killpg`). `ProcessLookupError` or `PermissionError` → `False`. Signal 0 is the liveness probe.
  - `boot_time(self) -> float` = `time.time() - time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))`. The docstring says: Linux `CLOCK_BOOTTIME` counts suspend; on macOS `CLOCK_MONOTONIC` counts sleep; the result is stable to well under 60 s within one boot.

- [x] **Step 1: Write the failing tests:**
  - `test_spawn_detached_runs_in_own_session`: spawn `[sys.executable, "-c", "import os,sys; open(sys.argv[1],'w').write(f'{os.getsid(0)} {os.getpgrp()}')", out]`, wait for the file, and assert its sid ≠ `os.getsid(0)`.
  - `test_spawn_detached_passes_extra_env`.
  - `test_spawn_logged_appends_both_streams`: pre-write `"old\n"`. The child prints to stdout and stderr, and the log contains `old`, both lines and nothing else.
  - `test_signal_group_kills_whole_group`: `spawn_logged` a `#!/bin/sh` script that starts `"$SLEEP" 60 &` (`_stubs.SLEEP`), writes `$!` to a file, then waits. `signal_group(proc.pid, SIGKILL)` is `True`; after reaping, `signal_pid(grandchild, 0)` turns `False` within 5 s.
  - `test_signal_missing_pid_is_false`: a reaped child's pid → `False`.
  - `test_boot_time_stable`: two calls differ by < 1.0.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_toolctx_detach.py -q -p no:cacheprovider`. Expected: FAIL with `AttributeError: 'ToolContext' object has no attribute 'spawn_detached'`.
- [x] **Step 3: Implement** the five methods with the signatures above.
- [x] **Step 4: Run** the same command. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(toolctx): detached and logged spawns, signal calls, boot time"` (test + `toolctx.py`).

---

### Task 3: Stage definition — hook states, template, instruction validator

Complexity: medium

**Files:**
- Create: `src/omc/stagedef.py`
- Modify: `tests/unit/_stagekit.py` (hook writer)
- Test: `tests/unit/test_stagedef.py`

**Interfaces:**
- Consumes: `ToolContext.run_bounded` (existing; raises `TimeoutError` and kills the group).
- Produces:
  - `Stage = Literal["check", "build", "verify"]`, `STAGES: tuple[Stage, ...]`.
  - `HEADINGS = ("Limits", "Done when", "Passed when", "Fixing failures", "Rerunning a subset")`. Only `"Rerunning a subset"` may be missing.
  - `SECTION_MAX = 5120`, `TOTAL_MAX = 20480` (UTF-8 bytes), `SHOW_WARN_S = 1.0`, `SHOW_STOP_S = 10.0`, `DEFAULT_FIX_CYCLES = 2`, `MAX_FIX_CYCLES = 10`.
  - `class StageBroken(Refusal)`: its message is the whole sentence, always starting `"CRITICAL STOP: "`.
  - `HookState = Literal["present", "absent", "unmigrated", "not-executable"]`; `hook_path(root: Path, stage: str) -> Path` (`<root>/.omc/bin/<stage>.sh`); `hook_state(root: Path, stage: str) -> HookState`. A present hook wins over an old skill.
  - `check_hook(root, stage) -> None` raises `StageBroken` for `unmigrated` and `not-executable`, with the exact sentences below.
  - `@dataclass(frozen=True) class Limits: timeout_s: float | None; quiet_s: float | None; fix_cycles: int`.
  - `@dataclass(frozen=True) class Instructions: text: str; limits: Limits; subset: str | None; warnings: tuple[str, ...]`. `subset` is the body of `## Rerunning a subset`, or `None`.
  - `parse_duration(value: str) -> float | None`: `^(\d+)(s|m|h)$` or `none`; anything else raises `ValueError`.
  - `format_duration(seconds: float) -> str`: `h` when divisible by 3600, else `m` when divisible by 60, else `s` (`2700.0 → "45m"`).
  - `parse_instructions(text: str) -> Instructions`. Normalize `\r\n` to `\n`. A second-level heading is any line matching `^## (.+?)\s*$`, inside code fences too; lines starting `### ` are content. Whitespace-only lines before the first heading are allowed.
  - `show_instructions(ctx, root: Path, stage: str, *, warn_s=SHOW_WARN_S, stop_s=SHOW_STOP_S) -> Instructions`. It runs `ctx.run_bounded([str(hook), "--show-instructions"], timeout=stop_s, cwd=root)`, timed with `time.monotonic()`. Over `warn_s` adds the warning `f".omc/bin/{stage}.sh --show-instructions took {t:.1f}s (over 1s)"`.
  - `STAGE_TEMPLATE: str`: the five headings in order, each followed by one paragraph saying what belongs there (§6.3 table, "Reader" and "Content" columns, condensed to 2–4 lines). `## Limits` shows the three example lines `Timeout: 45m`, `Quiet timeout: 5m`, `Fix cycles: 4` and says `none` switches a limit off.
  - `refresh_template(root: Path) -> bool`: when `<root>/.omc/bin/` is a directory and `INSTRUCTIONS.template.md` is missing or differs from `STAGE_TEMPLATE`, it writes the file atomically and returns `True`.
  - `_stagekit.write_hook(root, stage, *, body: str = "exit 0", instructions: str = VALID_INSTRUCTIONS, executable=True, show_delay: float = 0) -> Path`. It writes `#!/bin/sh`; then `if [ "$#" -eq 1 ] && [ "$1" = "--show-instructions" ]; then` (an optional `"$SLEEP" <show_delay>`) `printf '%s' <quoted>; exit 0; fi`; then `body`. `VALID_INSTRUCTIONS` is the §6.3 example with one-line bodies and `Timeout: none`, `Quiet timeout: none`.
- Exact CRITICAL STOP sentences (tests assert them verbatim; `<s>` is the stage):
  - unmigrated: `CRITICAL STOP: migrate this stage with /omc:integrate (.omc/skills/<s>/SKILL.md exists, .omc/bin/<s>.sh does not)`
  - not executable: `CRITICAL STOP: .omc/bin/<s>.sh is not executable`
  - slow: `CRITICAL STOP: .omc/bin/<s>.sh --show-instructions did not answer within 10s` (the number is `stop_s` formatted `:g`)
  - bad exit: `CRITICAL STOP: .omc/bin/<s>.sh --show-instructions exited <rc>`; empty: `CRITICAL STOP: .omc/bin/<s>.sh --show-instructions printed nothing`
  - sizes: `CRITICAL STOP: instructions are <n> bytes, over the 20480-byte limit` / `CRITICAL STOP: section "## <h>" is <n> bytes, over the 5120-byte limit`
  - structure: `CRITICAL STOP: text before the first heading: "<first line>"`, `CRITICAL STOP: unknown heading "## <h>"`, `CRITICAL STOP: repeated heading "## <h>"`, `CRITICAL STOP: heading "## <h>" out of order`, `CRITICAL STOP: required heading "## <h>" missing`
  - limits: `CRITICAL STOP: ## Limits is missing "Timeout:"` (or `"Quiet timeout:"`), `CRITICAL STOP: ## Limits has an unknown line "<line>"`, `CRITICAL STOP: ## Limits: duration does not parse in "<line>"`, `CRITICAL STOP: ## Limits: Fix cycles must be 1 to 10 in "<line>"`. A repeated key counts as an unknown line.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_stagedef.py`. Each refusal asserts the exact sentence:
  - `test_hook_states` (four cases of the §6.2 table, plus "hook and old skill both present → present").
  - `test_valid_instructions_parse`: limits `None, None, 2`, `subset` text, no warnings.
  - `test_fix_cycles` parametrized: `4 → 4`, absent → 2, `none` → 2, `11` and `0` → refused naming the line.
  - `test_durations`: `90s → 90.0`, `45m → 2700.0`, `2h → 7200.0`, `none → None`, `5 minutes` → refused.
  - `test_structure_refusals` parametrized: extra heading, missing `## Passed when`, text before the first heading, repeated heading, out of order.
  - `test_limits_unknown_line`.
  - `test_section_over_5kb`, `test_total_over_20kb`: the total test uses sections under 5 KB each.
  - `test_crlf_and_empty_section_are_valid`.
  - `test_subset_heading_optional`.
  - `test_show_instructions_slow_is_refused`: `write_hook(show_delay=1)`, `stop_s=0.5`.
  - `test_show_instructions_warns_over_warn_s`: `show_delay=0.4`, `warn_s=0.2`, `stop_s=5`; one warning.
  - `test_show_instructions_nonzero_and_empty`.
  - `test_template_headings_match_validator`: the `## ` lines of `STAGE_TEMPLATE` equal `HEADINGS`.
  - `test_refresh_template_writes_only_into_existing_bin`: no `.omc/bin` → `False` and no file. With the dir → `True`, then `False` on the second call. A modified file → `True` and restored.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_stagedef.py -q -p no:cacheprovider`. Expected: FAIL, `ModuleNotFoundError: omc.stagedef`.
- [x] **Step 3: Implement** `src/omc/stagedef.py` and the `_stagekit.write_hook` helper per the interfaces above.
- [x] **Step 4: Run** the same command. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(stage): hook states, instruction template and validator"`

---

### Task 4: The watch alive lock

Complexity: medium

**Files:**
- Modify: `src/omc/watchlock.py` (module docstring: a third lock), `src/omc/watch.py:run_watch` (take it after `acquire_instance`, release it in `finally`)
- Modify: `tests/unit/_mutexproc.py` (helpers)
- Test: `tests/unit/test_watchlock_alive.py`

**Interfaces:**
- Produces in `watchlock.py`:
  - `ALIVE_LOCK = "omc-watch-alive.lock"`.
  - `alive_lock_path(ctx, cwd: str | None = None) -> Path | None` = `locks_dir(ctx, cwd) / ALIVE_LOCK`.
  - `hold_alive(path: Path, *, clear: bool) -> int`. When `clear`, it first unlinks the file (`missing_ok`), mirroring `acquire_instance(clear=)`. It opens the file `O_RDWR|O_CREAT` (close-on-exec, the Python default), takes `fcntl.flock(fd, LOCK_EX)` **blocking**, and returns the fd.
  - `release_alive(fd: int) -> None`: `LOCK_UN` + close.
  - `watch_alive(path: Path) -> bool`: open (create), `flock(LOCK_SH|LOCK_NB)`; `BlockingIOError` → `True`; acquired → unlock, close, `False`.
- `run_watch`: right after `acquire_instance` succeeds, call `alive_fd = hold_alive(alive_lock_path(ctx, cwd=root), clear=clear_mutex)`. Release it in the existing `finally` before the instance release. When `locks is None`, there is no alive lock either (same doctrine).
- `_mutexproc.hold_alive_lock(repo, *, shared=False) -> subprocess.Popen` spawns `sys.executable -c` that imports `omc.watchlock` and holds the lock: `hold_alive`, or `LOCK_SH` when `shared`. It prints `ready` on a line, then sleeps, and the helper waits for `ready`. Killing the process releases the lock. This is "a watch is running" for every later unit test (§7).

- [x] **Step 1: Write the failing tests:**
  - `test_no_watch_means_not_alive`.
  - `test_helper_holding_alive_lock_reads_alive_until_killed`.
  - `test_real_watch_holds_alive_lock_while_running`: use `_mutexproc.spawn_watch` with the repo, ctx and `_sub_env` setup of `tests/unit/test_watch_mutex.py` (those real-watch tests pass on this host), and `wait_for(lambda: watch_alive(path))`.
  - `test_two_concurrent_probes_both_read_not_alive`: one helper holds `LOCK_SH` (`shared=True`); `watch_alive` is still `False`.
  - `test_probe_never_makes_starting_watch_bail`: a `shared=True` helper holds the alive lock, then a real `run_watch_once`. Assert `WATCH_BAIL_MSG not in stderr` and that it has not exited after 2 s (it blocks). Kill the helper; then watch exits 0.
  - `test_clear_mutex_takes_a_fresh_alive_lock`: a helper holds the old inode. `run_watch_once(clear=True)` returns 0 and does not block.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_watchlock_alive.py -q -p no:cacheprovider`. Expected: FAIL with `ImportError: cannot import name 'ALIVE_LOCK'`.
- [x] **Step 3: Implement** the four functions, the `run_watch` hold and release, and the `_mutexproc` helper.
- [x] **Step 4: Run** the new file and `tests/unit/test_watch.py`. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(watch): alive lock that stage runs probe"`

---

### Task 5a: The supervisor — start, verdicts, limits, wait

Complexity: high

**Files:**
- Create: `src/omc/stagerun.py`
- Modify: `tests/unit/_stagekit.py` (`stage_ctx(tmp_path) -> ToolContext`, with `OMC_STAGE_ROOT=tmp_path/"stages"` and `OMC_HOME=tmp_path/"home"` over `os.environ`)
- Test: `tests/unit/test_stagerun.py`

**Interfaces:**
- Consumes: `stageid.resolve_ids`, `stageid.ensure_cache` (Task 1); `ctx.spawn_detached`, `ctx.spawn_logged`, `ctx.signal_*`, `ctx.boot_time` (Task 2); `stagedef.hook_state`, `check_hook`, `show_instructions`, `refresh_template`, `format_duration`, `StageBroken` (Task 3); `watchlock.alive_lock_path`, `watch_alive` (Task 4).
- Produces:
  - `State = Literal["running", "passed", "failed", "held", "timed-out", "quiet-timed-out", "killed", "died", "refused", "unconfigured"]`; `LIVE = frozenset({"running", "held"})`.
  - Constants: `WAIT_PERIOD_S = 540.0`, `POLL_S = 1.0`, `KILL_GRACE_S = 30.0`, `STARTUP_S = 10.0`, `TAIL_LINES = 40`, `PRUNE_AGE_S = 7 * 86400`, `STAGE_ROOT = "/tmp/omc-stages"`.
  - `@dataclass(frozen=True) class RunAnswer: stage: str; state: State; run: str; log: str; instructions: str; elapsed: int; quiet: int; tail: tuple[str, ...]; reason: str; fix_cycles: int; warnings: tuple[str, ...]`. `to_json()` always carries all 11 keys (`""`, `0` or `[]` when there is nothing to report).
  - `start_run(ctx, stage: str, args: list[str], *, extra_env: dict[str, str] | None = None) -> RunAnswer`. A broken definition or a missing watch → `state="refused"` with `reason` = the CRITICAL STOP sentence. No hook → `state="unconfigured"`. Errors (lost race, startup timeout) → `OmcError`.
  - `wait_run(ctx, stage: str, *, period: float = WAIT_PERIOD_S) -> RunAnswer`. No run of that stage → `OmcError(f"no {stage} run in this worktree")`.
  - `supervise(spec: dict) -> int` and `main(argv: list[str] | None = None) -> int` (`python -m omc.stagerun supervise <spec-json>`), with `if __name__ == "__main__": raise SystemExit(main())`.
  - Files under `<root>/.cache/omc/`: `stage.lock` (filelock `FileLock`; the supervisor holds it from before it spawns the hook until it exits), `run.json`, `last-<stage>.json`, `wait.json`.
  - Paths per run: `<stage_root>/<project>/<worktree>/<stage>-<YYYYmmddTHHMMSS>-<run>` + `.log`, `.d/` (`OMC_STAGE_DIR`), `.instructions.md` (the validated text, C3) and `.result` (`OMC_STAGE_RESULT`, absent). `stage_root = Path(ctx.env.get("OMC_STAGE_ROOT") or STAGE_ROOT)`, and `run = uuid.uuid4().hex[:12]`.
  - The watch-missing refusal: `CRITICAL STOP: no \`omc watch\` running for this project — the user starts \`omc watch\` in the primary checkout (<primary>)`.
- `start_run` order (§6.4): `refresh_template(root)`; `hook_state` → `unconfigured` / `check_hook`; `resolve_ids` + `ensure_cache`; `show_instructions`; `watch_alive(alive_lock_path(ctx, cwd=root))`; end the live run (Task 5b; in this task only "no live run"); prune (Task 5b); write the instructions file; `ctx.spawn_detached([sys.executable, "-m", "omc.stagerun", "supervise", json.dumps(spec)], cwd=root, extra_env=extra_env)`; then poll every 0.1 s for up to `STARTUP_S` until `run.json["run"] == run` and the stage lock is held. Answer from `run.json`.
- The spec JSON carries: `run, stage, root, argv ([str(hook), *args]), log, stage_dir, result, instructions, timeout_s, quiet_s, fix_cycles, warnings, project_id, worktree_id, alive_lock, head` (`git rev-parse HEAD`) and `dirty` (`git status --porcelain` non-empty).
- Each supervisor pass, every `POLL_S`, in this order. Every state change rewrites `run.json` atomically and mirrors it to `last-<stage>.json`.
  1. A stop signal is pending (Task 5b).
  2. Watch gone, or worktree gone (Task 5b).
  3. `running` and the result file exists → read it once and strip it. `failed` for `verify` with the hook still alive → `held`, and keep looping with only steps 1–2 and "hook exited". Otherwise the verdict is `passed`/`failed`, and the group ends via `_end_group`. Any other content → `failed`, reason `result file says "<content>"`.
  4. `proc.poll()` is not None → if the result file exists it decides; otherwise exit 0 → `passed`, else `failed` with reason `exit <rc>`. A held run whose hook exits → `failed`.
  5. `running` and `timeout_s` reached → `_end_group`, `timed-out`, reason `timed out after <format_duration>`.
  6. `running`, the log size unchanged for `quiet_s` → `_end_group`, `quiet-timed-out`, reason `no log progress for <format_duration> at: <last non-empty log line, or "(empty log)">`.
  - On any ended state: append `buildprogress.sentinel_line(exit_code)` to the log, release the lock, exit 0.
- `_end_group(ctx, proc, pgid, *, grace: float = KILL_GRACE_S) -> None`: `SIGTERM` to the group, then every 0.1 s `proc.poll()` (which reaps the leader) and `signal_group(pgid, 0)` until it is gone or `grace` has passed, then `SIGKILL`.
- `wait_run`: `refresh_template`; read `run.json` (ignore it when its `worktree_id` is not this worktree's, U6), or `last-<stage>.json` when `run.json` is another stage's. Poll every 0.5 s until the state is not `running` or `period` has passed. The state is `died` when `run.json` says live but the stage lock is free; the reason is `the supervisor is gone (killed from outside, or the machine restarted)`, and nothing is written. `tail` and `wait.json` per the choices table; `quiet` = now − log mtime.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_stagerun.py`. Every test except the first holds a watch via `_mutexproc.hold_alive_lock(repo)`, uses `monkeypatch.chdir(repo)` and `ctx = stage_ctx(tmp_path)`, and has hook bodies that write lines with `echo`:
  - `test_no_watch_refuses_and_starts_nothing`: the hook body `echo started > <marker>`. The state is `refused`, the reason equals the watch sentence, and after 2 s the marker does not exist.
  - `test_exit_zero_passes` / `test_exit_one_fails` (reason `exit 1`).
  - `test_verify_failed_result_file_is_held_and_alive`: body `echo failed > "$OMC_STAGE_RESULT"; echo $$ > <pidfile>; exec "$SLEEP" 60`. The state is `held` and `ctx.signal_pid(pid, 0)` is `True`. Cleanup kills the group.
  - `test_check_failed_result_file_ends_at_once`: the same body for `check` → `failed`, and the pid is gone within 5 s.
  - `test_verify_passed_result_file_ends_at_once` → `passed`, pid gone.
  - `test_result_file_wins_over_exit_code`: `echo failed > "$OMC_STAGE_RESULT"; exit 0` → `failed`.
  - `test_quiet_timeout_quotes_last_line`: `Quiet timeout: 2s`, body `echo last words; exec "$SLEEP" 30` → `quiet-timed-out`, the reason is `no log progress for 2s at: last words`, and the group is gone.
  - `test_timeout_kills_chatty_hook`: `Timeout: 2s`, body `while :; do echo tick; "$SLEEP" 0.2; done` → `timed-out`, reason `timed out after 2s`.
  - `test_hook_sees_caller_env_and_stage_vars`: `extra_env={"PROBE": "x"}`. The hook writes `$PROBE $OMC_STAGE_NAME $OMC_PROJECT_ID $OMC_WORKTREE_ID` and tests `-d "$OMC_STAGE_DIR"`, `[ ! -e "$OMC_STAGE_RESULT" ]` and that `$OMC_STAGE_LOG` is the answer's `log`.
  - `test_answer_carries_instructions_file_and_fix_cycles`: `Fix cycles: 4` → `fix_cycles == 4`, and the `instructions` file text equals the hook's output.
  - `test_tail_is_new_lines_only_and_capped`: a body printing 100 numbered lines, then exit. The first `wait` gives the last 40; a second `wait` gives `()`.
  - `test_wait_without_run_errors`: `OmcError` "no check run in this worktree".
  - `test_unconfigured_and_unmigrated`: no hook → `unconfigured`; an old skill only → `refused` with the migrate sentence.
  - `test_supervisor_sigkilled_reads_died`: `signal_pid(run_json["pid"], SIGKILL)` → `wait_run` state `died`.
  - `test_copied_run_json_is_ignored`: copy a finished worktree's `.cache/` into a second worktree, then `wait_run` in the second raises `OmcError`.
  - `test_log_gets_sentinel_line`.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_stagerun.py -q -p no:cacheprovider`. Expected: FAIL, `ModuleNotFoundError: omc.stagerun`.
- [x] **Step 3: Implement** `src/omc/stagerun.py` per the interfaces and pass order above. Read the clock with `time.monotonic()` inside the supervisor, and epoch seconds (`time.time()`) for `started`/`ended` in `run.json`.
- [x] **Step 4: Run** the same command, then `-n auto` over the file twice. Expected: PASS both times (no flakes).
- [x] **Step 5: Commit** `git commit -m "feat(stage): detached supervisor, verdicts, limits and wait"`

---

### Task 5b: Run lifecycle — kill, supersede, died recovery, liveness, prune, foreground

Complexity: high

**Files:**
- Modify: `src/omc/stagerun.py`
- Test: `tests/unit/test_stagerun_lifecycle.py`

**Interfaces:**
- Consumes: Task 5a.
- Produces:
  - `kill_run(ctx, stage: str) -> RunAnswer`. It calls `refresh_template(root)` first, as `start_run` and `wait_run` do (A4). A live run of that stage with the lock held → `signal_pid(pid, SIGTERM)`, wait up to `KILL_GRACE_S + 10` for the lock to free, then answer from `last-<stage>.json`. A live record with a free lock (`died`) → `_recover_group` and answer `died`. No live run of that stage → answer the last record unchanged. No record → `OmcError`.
  - `run_foreground(ctx, root: Path, stage: str, *, on_line: Callable[[str], None], extra_env=None, poll: float = POLL_S) -> RunAnswer`. It calls `start_run`, then loops: new log lines → `on_line`, until the state is not `running`. `KeyboardInterrupt` → `kill_run`, then re-raise. A `held` state is returned as is: watch never runs verify.
  - `_recover_group(ctx, record: dict) -> None` signals `record["pgid"]` (`_end_group` without a `proc`) only when `abs(ctx.boot_time() - record["boot"]) < 60`.
  - `prune(stage_root: Path, project: str, *, now: float, age: float = PRUNE_AGE_S) -> None`.
- Supervisor additions:
  - Signal handlers: `SIGTERM` → reason `kill`, `SIGUSR1` → reason `superseded`. Each sets a flag; pass step 1 acts on it only while the state is live: `_end_group`, `killed`, reason `kill: ended by omc internal stage kill` / `superseded: a newer start in this worktree`. When the state is already final, it exits without rewriting.
  - Pass step 2: `not watch_alive(spec["alive_lock"])` → `killed`, `watch-gone: the project's omc watch stopped`. `not Path(root).is_dir()` → `_end_group`, `worktree-gone: the worktree was removed`; the status write may fail with `OSError`, which is ignored. Both run while `held` too (A1).
- `start_run` additions: before spawning, the run in `run.json` (this worktree's, any stage) is live with the lock held → `SIGUSR1` and wait for the lock to free. Live with the lock free → `_recover_group`. Then `prune(...)`. Two starts at once: the loser's supervisor fails `FileLock.acquire(timeout=2)` and exits 1, and its `start_run` raises `OmcError(f"another start won: run {winner} ({stage})")`.

- [x] **Step 1: Write the failing tests** (the same fixtures as 5a):
  - `test_kill_ends_held_verify`: held → `kill_run` → `killed`, reason starts `kill`, the pid is gone, and the supervisor's pid is gone.
  - `test_kill_without_live_run_answers_last_state`: a passed check → `kill_run(ctx, "check")` answers `passed` and changes nothing. `kill_run(ctx, "build")` with no record → `OmcError`.
  - `test_second_start_supersedes_first`: a sleeping check run, then a second `start_run(check)`. The first group is gone, the second answer's `run` differs, and `wait_run` carries the second `run`.
  - `test_superseded_other_stage_answers_from_last`: a sleeping verify, then `start_run(check)`. `wait_run(ctx, "verify")` gives `killed`, the reason starts `superseded`, and `run` is the verify run's id.
  - `test_watch_release_kills_run_and_hold`: parametrized over a running check and a held verify. Kill the alive-lock helper → within 5 s the state is `killed`, the reason starts `watch-gone`, and the group is gone.
  - `test_worktree_removed_kills_group_and_supervisor`: run in a linked worktree, `shutil.rmtree(worktree)` → the hook pid and the supervisor pid are gone within 5 s.
  - `test_died_run_group_is_ended_by_next_start`: SIGKILL the supervisor of a sleeping run. The hook is still alive; the next `start_run` ends it.
  - `test_died_record_from_another_boot_is_not_signalled`: forge `run.json` (live state, `boot: 0`, `pgid` = the pgid of a sleeping helper the test owns via `ctx.spawn_logged`). After `start_run` the helper is still alive.
  - `test_term_ignoring_hook_gets_sigkill`: body `trap '' TERM; exec "$SLEEP" 60`. Call `_end_group(..., grace=1)` directly on a `spawn_logged` proc: the group is gone within 3 s.
  - `test_concurrent_starts_one_wins`: two threads call `start_run(check)`. One returns `running`, the other raises `OmcError` naming the winner's run.
  - `test_prune_removes_only_old_entries`: forged mtimes 8 days and 1 day old.
  - `test_run_foreground_streams_lines_and_kills_on_interrupt`: `on_line` collects lines; a second case raises `KeyboardInterrupt` from `on_line` at the first line → re-raised, and the run is `killed`.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_stagerun_lifecycle.py -q -p no:cacheprovider`. Expected: FAIL with `ImportError: cannot import name 'kill_run'`.
- [x] **Step 3: Implement** per the interfaces above.
- [x] **Step 4: Run** both stagerun test files with `-n auto`, twice. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(stage): kill, supersede, died recovery, watch and worktree liveness"`

---

### Task 6: `omc internal stage` and the `OMC_STAGE_RUN` contract

Complexity: medium

**Files:**
- Modify: `src/omc/internal.py` (`_USAGE` gains `stage <start STAGE [-- ARGS…]|wait STAGE|kill STAGE|template>`, plus a `stage` branch in `run_internal`)
- Test: `tests/unit/test_internal_stage.py`

**Interfaces:**
- Consumes: `stagerun.start_run`, `wait_run`, `kill_run`, `RunAnswer.to_json`; `stagedef.STAGE_TEMPLATE`, `STAGES`.
- Produces: `_stage(ctx, rest: list[str]) -> int`. It prints `OMC_STAGE_RUN {json}` with `flush=True`. rc 0 for every answer except `refused` → 2. `OmcError` → `error: <msg>` on stderr, rc 1, no verdict line. `template` prints `STAGE_TEMPLATE` with `end=""`, rc 0. Bad usage (an unknown verb, a stage outside `STAGES`, `wait`/`kill` with extra arguments, `start` arguments not after a literal `--`) → `_USAGE`, rc 2.

- [x] **Step 1: Write the failing tests:**
  - `test_stage_template_prints_template`: stdout == `STAGE_TEMPLATE`.
  - `test_stage_start_wait_roundtrip`: a watch helper and a passing check hook. `run_internal(["stage","start","check"])` returns 0; stdout is exactly one line, `OMC_STAGE_RUN {...}`, whose JSON has the 11 keys. Then `wait`, with `state == "passed"`.
  - `test_stage_start_refused_exits_2_with_verdict`.
  - `test_stage_wait_without_run_exits_1_no_verdict`: stdout is empty, stderr starts `error:`.
  - `test_stage_start_passes_args_after_dashdash`: the hook writes `"$*"`, and `start check -- a b` records `a b`.
  - `test_stage_usage` parametrized → rc 2.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_internal_stage.py -q -p no:cacheprovider`. Expected: FAIL (rc 2, the usage line).
- [x] **Step 3: Implement** `_stage` and the dispatch branch, imported lazily like `herdr`.
- [x] **Step 4: Run** the new file and `tests/unit/test_internal.py`. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(internal): omc internal stage start|wait|kill|template"`

---

### Task 7: The proxy loop — `skills/{check,build,verify}/SKILL.md`

Complexity: medium

**Files:**
- Modify: `skills/check/SKILL.md`, `skills/build/SKILL.md`, `skills/verify/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (`test_stage_proxy_contract`, `test_verify_proxy_runs_only_in_finish_and_reruns_subsets`, plus a new `test_stage_proxies_share_the_loop`)

**Interfaces:**
- Consumes: the commands and states of Task 6.
- Produces:
  - Frontmatter descriptions name `.omc/bin/<stage>.sh` in place of `.omc/skills/<stage>`. The rest of each description stays word for word.
  - The bodies keep the intended-semantics paragraphs. `## Steps` is replaced by `## The loop`, and the `OMC_STAGE` paragraph stays byte-identical.
  - verify keeps `## User Input` and adds the `kill` mode.

The shared loop, written into all three files with `check` replaced by the stage name. The words `build` and `verify` must not appear in it otherwise, and the stage name appears only where shown:

````markdown
## The loop

Run each command from the project root (`git rev-parse --show-toplevel`). Each
prints one `OMC_STAGE_RUN {…}` line; act on its `state`, nothing else.

1. `omc internal stage start check` — a subset run appends ` -- <args>`, the
   arguments the instructions' `## Rerunning a subset` section gives for the
   named pieces.
   - `unconfigured` → no project `check` stage configured — nothing to do:
     `"configured": false, "passed": true`. End.
   - `refused` → `"passed": false` with the `reason` as the summary. End.
   - `running` → read the file at `instructions` once. Go to 2.
   - any other state → handle it as step 2 does.
   - no `OMC_STAGE_RUN` line (exit 1) → `"passed": false` with the `error:`
     line as the summary. End.
2. `omc internal stage wait check`, with the shell command's timeout set to
   600 s (600000 ms). By `state`:
   - `running` → say one progress line (`elapsed` and the last line of
     `tail`) and repeat step 2. Nothing else: no sleep, no kill, no judgment
     about how long it has been.
   - `passed` → `"passed": true`.
   - `failed`, `held` → `"passed": false`; the summary says what failed as
     far as `tail` and the instructions' `## Fixing failures` tell, gives the
     `log` path, says `held` when the run is held, and says
     `fix cycles: <n>` when `fix_cycles` is above 2.
   - `timed-out`, `quiet-timed-out`, `killed`, `died` → `"passed": false`;
     the summary is the `reason` and the `log` path. A `reason` starting
     `watch-gone` makes the summary start with `CRITICAL STOP:`.
   - a `run` other than the one `start` returned → `"passed": false`,
     summary "superseded by another run"; the other run's outcome is never
     reported as this one's.
   - no `OMC_STAGE_RUN` line (exit 1) → as in step 1.

After a compaction, call `omc internal stage wait check` again: every answer
carries the `state` and the `instructions` path.
````

The verify `## User Input` modes become:
- `rerun only: <pieces>`: first run `.omc/bin/verify.sh --show-instructions` and read its `## Rerunning a subset` section. With that section, start with ` -- <args>` for those pieces. Without it, run the full stage and say so; the stage-gate rule's repair loop then asks for no targeted rerun, so this fallback only serves a direct call. `rerun only:` with no pieces runs the full stage.
- `kill`: run `omc internal stage kill verify` and report its `state` and `reason`. End with `OMC_STAGE` `"passed": false`, summary `verify run <run> <state>: <reason>`. This is how a user ends a held verify; no conductor calls it (A5).
- Empty, or anything else → the full stage. Any text is context the caller attached.
- The existing verify-only sentences stay ("names the failing pieces", "describes the subset", finish-only).

- [x] **Step 1: Write the failing tests:**
  - `test_stage_proxies_share_the_loop`: for each stage, take the text from `## The loop` up to the next `\n## ` (or the `OMC_STAGE` paragraph), apply `re.sub(rf"\b{stage}\b", "<stage>", section)`, and assert the three are equal. Each section contains `omc internal stage start <stage>`, `omc internal stage wait <stage>`, `600 s`, `no sleep, no kill`, `CRITICAL STOP:`, `superseded by another run` and `instructions`.
  - Rewrite `test_stage_proxy_contract`: for check/build/verify, `".omc/skills/" not in text`, `f".omc/bin/{stage}.sh" in text`, `OMC_STAGE`, `"configured"` and `nothing to do`. review keeps its `.omc/skills/review` assertion.
  - In `test_verify_proxy_runs_only_in_finish_and_reruns_subsets`: replace `"Running a subset"` with `"Rerunning a subset"`. Add the needles `"--show-instructions"`, `"omc internal stage kill verify"` and `"no conductor calls it"`. Keep the byte-identical `OMC_STAGE` assertion.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q -p no:cacheprovider -k "proxy"`. Expected: FAIL on the new needles.
- [x] **Step 3: Rewrite** the three skills as specified.
- [x] **Step 4: Run** `tests/unit/test_plugin_manifests.py` whole. Expected: PASS, apart from the tests Task 8 owns; list them in the commit body. Run `tests/unit/test_codex_plugin.py` too (skills are packaged for Codex). Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(skills): check, build and verify proxies share the polling loop"`

---

### Task 8: Stage-gate rule, finish and integrate text

Complexity: medium

**Files:**
- Modify: `src/omc/distribution/AGENTS.md` (the stage-gate rule and machine-contracts bullets)
- Modify: `skills/finish/SKILL.md` (Step 3's "Each stage is a proxy for …" sentence)
- Modify: `skills/integrate/SKILL.md` (Phase 1 inventory, the Phase 2 stage slots, the Phase 3 wrap-up)
- Modify: `tests/unit/test_plugin_manifests.py` (the layer gate test near line 1944, `test_integrate_skill_contract`, `test_finish_runs_verify_before_review_and_last`, `test_distribution_agents_task_model_policy`'s neighbours as needed)

**Interfaces:**
- Consumes: the `OMC_STAGE` summary conventions of Task 7 (`CRITICAL STOP:` prefix, `fix cycles: <n>`, `held`, log path).
- Produces, in the layer's stage-gate bullet (keep every existing needle the test pins unless replaced here):
  - **Brief:** "dispatch one fix subagent at the heavy coding tier, briefed with the failed stage's `summary`, its log path and the stage's `## Fixing failures` section". For a held verify, add: "the system is still up; `/omc:investigate local` inspects it; the `/omc:check` that follows the fix ends the hold, so look first and check afterwards".
  - **Guard:** "a fix repairs the code under test, never the stage's own definition (`.omc/bin/<stage>.sh` and its instructions, or `.omc/skills/review/`) or the script or command it runs". The `.omc/skills/<stage>/` needle is replaced by these two.
  - **CRITICAL STOP verdicts:** "A verdict whose summary starts with `CRITICAL STOP:` is the CRITICAL question at once: no fix is dispatched and no cycle is spent."
  - **Bound:** "Allow at most two fix-and-rerun cycles, or `<n>` while the red stage's summary carries `fix cycles: <n>` (its `## Limits` `Fix cycles`, at most 10)". The one-counter wording stays, and the §6.6 example becomes one sentence: "with the counter at 3, a stage without a bound of its own that turns red is the CRITICAL question".
  - **Subset:** `Without a "Rerunning a subset" section in the verify stage's instructions` replaces `Without a "Running a subset" section in the project's verify skill`.
  - **Holds:** "No conductor kills a stage run; a CRITICAL question, or any other question to the user, leaves a held verify up."
  - **Contracts bullet:** add `OMC_STAGE_RUN`.
- finish Step 3: "Each of `check`, `build` and `verify` is a proxy for the project's hook `.omc/bin/<stage>.sh`; `review` for `.omc/skills/review/SKILL.md`." Add no `stage kill`.
- integrate:
  - Inventory row: `.omc/bin/check.sh` · `.omc/bin/build.sh` · `.omc/bin/verify.sh` (each with its `.instructions.md`) · `.omc/skills/review` · … in place of the three stage skills.
  - The three slots become `### \`.omc/bin/check.sh\``, `### \`.omc/bin/build.sh\`` and `### \`.omc/bin/verify.sh\``. Each says: design the hook plus `.omc/bin/<stage>.instructions.md` from `omc internal stage template`, and validate it with a real `omc internal stage start <stage>`.
  - A new paragraph, **Unmigrated stage:** "`.omc/skills/<stage>/SKILL.md` without a hook → convert it: commands into the hook, prose into the four sections; delete the old skill once the user approves the pair."
  - The **Migration trigger** stays, reading hooks and old skills alike.
  - The verify slot says "Rerunning a subset".
  - The wrap-up adds "stages need a running `omc watch` in the primary checkout".

- [x] **Step 1: Write the failing tests:**
  - In the layer gate test: add needles `"`.omc/bin/<stage>.sh` and its instructions"`, `"## Fixing failures"`, `"`CRITICAL STOP:` is the CRITICAL question at once"`, `"no cycle is spent"`, `"fix cycles: <n>"`, `"at most 10"`, `"leaves a held verify up"`, `"No conductor kills a stage run"` and `"/omc:investigate local"`. Replace the `.omc/skills/<stage>/` needle, and update the `skip` sentence to the new wording. Assert `"OMC_STAGE_RUN"` in the contracts bullet.
  - New `test_conductors_never_kill_stage_runs`: `"stage kill"` is not in `skills/finish/SKILL.md` or `skills/audit/SKILL.md`.
  - `test_integrate_skill_contract`: needles `.omc/bin/check.sh`, `.omc/bin/build.sh`, `.omc/bin/verify.sh`, `omc internal stage template`, `Unmigrated stage`, `omc watch`, `Rerunning a subset` and `Migration trigger`. The verify paragraph split key becomes `` ### `.omc/bin/verify.sh` ``, with the "only within `/omc:finish`" check kept.
  - `test_finish_runs_verify_before_review_and_last`: add the needle `.omc/bin/<stage>.sh` in Step 3.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_agentsmd.py -q -p no:cacheprovider`. Expected: FAIL on the new needles.
- [x] **Step 3: Edit** the three documents.
- [x] **Step 4: Run** the same command plus `tests/unit/test_configure.py`. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "feat(layer): stage-gate rule for hook stages; finish and integrate follow"`

---

### Task 9: `omc watch --auto-build` runs `build.sh`

Complexity: medium

**Files:**
- Modify: `src/omc/watch.py`: rewrite `_auto_build`; delete `_STAGE_RE` and `_parse_stage`; `run_watch` no longer adds `"orchestrator"` to `model_groups`; remove the now-unused imports `get_provider` (if unused elsewhere), `skill_prompt`, `orchestrator` and `json`/`re` if unused.
- Modify: `src/omc/cli/__init__.py:123` (help text: "After each action tick, run the project's `.omc/bin/build.sh` stage (no LLM)")
- Modify: `tests/unit/test_watch.py`: the parametrize at line 43 drops `"orchestrator"`; replace the LLM auto-build tests at lines 956–1170 (`_seed_build_stage`, `_stub_claude` users, `test_auto_build_*`, `test_codex_auto_build_*`, `test_build_timeout_constant_is_gone` stays if still true).

**Interfaces:**
- Consumes: `stagerun.run_foreground` (Task 5b), `stagedef.hook_state` (Task 3).
- Produces: `_auto_build(ctx: ToolContext, root: str) -> None` (the `cfg` parameter goes; update the two call sites).
  - Behavior: `hook_state(root, "build") == "absent"` → the existing line `· no project build stage configured — skipping auto-build`.
  - Otherwise it says `→ running project build stage (.omc/bin/build.sh)`, followed by `— log: <log>` once the first answer has the log path. It runs `run_foreground(ctx, Path(root), "build", on_line=tracker.feed, extra_env=_CARGO_PROGRESS_ENV)` under `BarThread(tracker)`.
  - Outcome: `passed` → `✓ auto-build passed`. `refused`, `failed` and the rest → `✗ auto-build failed (<state>: <reason>) — log: <log>`. An `OmcError` → `✗ auto-build failed (<msg>)`.
  - `KeyboardInterrupt` propagates after `run_foreground` killed the run, so watch's loop prints `· stopped`.
  - Watch's own alive lock (Task 4) satisfies A1 in-process.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_watch.py`. Use `_stagekit.write_hook(repo, "build", body=...)` and an `OMC_STAGE_ROOT` in the ctx env:
  - `test_auto_build_runs_real_build_hook`: body `echo building; exit 0` → `✓ auto-build passed`, and the announced log contains `building`.
  - `test_auto_build_reports_exit_code`: body `exit 3` → `✗ auto-build failed (failed: exit 3)`, and `--once` still returns 0.
  - `test_auto_build_asks_no_provider_for_a_model`: a `claude` stub that records calls stays uncalled (no `-p` in its log). `model_groups` is `set()` when only `auto_build`, which the parametrize at line 43 now asserts.
  - `test_auto_build_unconfigured_skips` (the existing `test_auto_build_unconfigured_skips_llm_entirely`, renamed and kept).
  - `test_auto_build_refused_instructions_report_critical_stop`: an extra heading → `✗ auto-build failed (refused: CRITICAL STOP: unknown heading …`.
  - `test_watch_module_has_no_stage_parser`: `not hasattr(watch, "_parse_stage")`.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_watch.py -q -p no:cacheprovider -k "auto_build or reconciles or stage_parser"`. Expected: FAIL.
- [x] **Step 3: Implement** the rewrite and delete the LLM path and its now-dead tests.
- [x] **Step 4: Run** `tests/unit/test_watch.py` and `tests/unit/test_cli.py`. Expected: PASS. Then `uvx ruff check src/omc/watch.py`: no unused imports.
- [x] **Step 5: Commit** `git commit -m "feat(watch): --auto-build runs .omc/bin/build.sh without an LLM"`

---

### Task 10: This repo's hooks, the amended invariant and the contract lists

Complexity: medium

**Files:**
- Create: `.omc/bin/check.sh`, `.omc/bin/build.sh`, `.omc/bin/verify.sh` (mode 755), and `.omc/bin/{check,build,verify}.instructions.md`
- Create: `.omc/bin/INSTRUCTIONS.template.md` (= `uv run omc internal stage template` output)
- Modify: `.omc/config/AGENTS.md` (invariant sentence, contract list, "Project stages for this repo" line)
- Modify: `.omc/skills/explain-context/SKILL.md` (contract list)
- Modify: `justfile` (new `codex-stage-poll` recipe, used by Task 11)
- **Keep unchanged:** `.omc/skills/{check,build,verify}/SKILL.md` (bootstrap constraint)
- Test: `tests/unit/test_dogfood_hooks.py`

**Interfaces:**
- Consumes: `stagedef.parse_instructions`, `STAGE_TEMPLATE` (Task 3).
- Produces:
  - Each hook is the §6.3 conventional form (`exec cat "$(dirname "$0")/<stage>.instructions.md"` for `--show-instructions`; this repo's hosts have `cat` on PATH) followed by `exec just check "$@"` or `exec just build "$@"`.
  - `verify.sh` runs `bash` with `set -euo pipefail`:
    - with arguments: if `$1` is `codex-gate`, run `shift; exec just codex-gate "$@"`; otherwise `exec just e2e-tests "$@"`;
    - without arguments: run `just e2e-tests`, then, when the changed-file set (choices table) hits the Codex `case` list, `echo "codex code changed: running the Codex gate"` and run `just codex-gate`;
    - exit non-zero on either failure.
  - The instructions carry what today's three stage skills say (U7), adapted. **Done when:** the hook exits; nothing is held. **Passed when:** exit 0, plus each skill's meaning. **Fixing failures:** read the log, never edit `.omc/bin/*`; for verify, a missing token, missing Docker or a missing `just codex-login` is infrastructure, not code. `verify.instructions.md`'s **Rerunning a subset** carries the node-id rules and `-- codex-gate tests/e2e/<file>::<test>`. Limits per the choices table.
  - `.omc/config/AGENTS.md`:
    - the invariant gains verbatim: "The one exception is a stage run: a detached supervisor bound to one hook run; it ends when the hook ends or is killed, is never restarted or scheduled, and never outlives a running `omc watch` for its project, or its worktree."
    - the contract list adds `OMC_STAGE_RUN`;
    - the stages line reads: "Project stages for this repo: `.omc/bin/{check,build,verify}.sh` (with their `.instructions.md`) and `.omc/skills/review`. `.omc/skills/{check,build,verify}` stay until the installed omc reads hooks; remove them in a follow-up. Stage runs need `omc watch` running in the primary checkout."
  - `justfile` `codex-stage-poll *args`: `CODEX_AUTH_VOLUME=${CODEX_AUTH_VOLUME:-omc-e2e-codex-auth} bash scripts/e2e.sh rest -m "e2e and expensive and codex_gate" -n 1 tests/e2e/test_e2e_stage_poll.py {{args}}`, with a comment "evidence run: one stage run that outlasts a single 540 s wait; expensive tier".

- [x] **Step 1: Write the failing tests** in `tests/unit/test_dogfood_hooks.py`:
  - `test_repo_hooks_print_valid_instructions`, parametrized over the three stages: `subprocess.run([hook, "--show-instructions"], env=os.environ)` exits 0, `parse_instructions(stdout)` succeeds, the hook is executable, and `verify`'s `subset` is not `None`.
  - `test_repo_hook_commands`: `check.sh` contains `just check`, `build.sh` contains `just build`, and `verify.sh` contains `just e2e-tests`, `just codex-gate`, `origin/main...HEAD` and each of the six Codex paths.
  - `test_repo_template_file_is_current`: `.omc/bin/INSTRUCTIONS.template.md` == `STAGE_TEMPLATE`.
  - `test_agents_md_amends_daemon_invariant_and_contracts`: the verbatim sentence, `OMC_STAGE_RUN` in `.omc/config/AGENTS.md`, `OMC_STAGE_RUN` in `explain-context`, `.omc/bin/` in the stages line.
  - `test_old_stage_skills_kept_for_bootstrap`: the three `.omc/skills/<stage>/SKILL.md` files exist. The comment says this pins the bootstrap constraint and is removed with them in the follow-up.
  - `test_codex_stage_poll_recipe`: the `justfile` contains `codex-stage-poll` and `test_e2e_stage_poll.py`.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_dogfood_hooks.py -q -p no:cacheprovider`. Expected: FAIL (the files do not exist).
- [x] **Step 3: Write** the files. Measure `time just build` once and set build's `Timeout` per the choices table. Generate the template file with `uv run omc internal stage template > .omc/bin/INSTRUCTIONS.template.md`.
- [x] **Step 4: Run** the same command plus `tests/unit/test_plugin_manifests.py -k dogfood`. Expected: PASS.
- [x] **Step 5: Commit** `git commit -m "chore(repo): stage hooks for check, build, verify; amend the daemon invariant"`

---

### Task 11: E2E — hook fixtures, watch stand-in, held verify, Codex poll

Complexity: high

**Files:**
- Modify: `tests/e2e/harness.py`: add `hold_watch_stand_in(container, repo) -> None`, `start_real_watch(container, repo) -> None` and `stop_real_watch(container, repo) -> None`.
- Modify: `tests/e2e/lifecycle_helpers.py:_fixture`. check/build/verify become `.omc/bin/<stage>.sh` hooks (executable, with the existing marker plus command scripts inlined) and `.omc/bin/<stage>.instructions.md`; review stays a skill over `.omc/stage-scripts/review.sh`. Add a new parameter `watch: Literal["stand-in", "real", "none"] = "stand-in"`, applied after the commit. Write `INSTRUCTIONS.template.md` via `run_in(container, ["bash", "-c", "omc internal stage template > .omc/bin/INSTRUCTIONS.template.md"], cwd=repo)` before the commit.
- Modify: `tests/e2e/golden/test_golden_claude.py:Flow.__init__` (`_fixture(container, watch="real")`; `stop_real_watch` in the `flow` fixture teardown).
- Modify: `tests/e2e/conftest.py:stage_session` (after `read_manifest`: `hold_watch_stand_in(c, manifest["repo"])`).
- Modify: `tests/e2e/test_e2e_finish.py`: `_add_stage` writes a hook and its instructions instead of a stage skill, and calls `hold_watch_stand_in`. `stage_path` becomes `.omc/bin/build.sh` / `.omc/bin/check.sh`; the "finish changed the committed stage" assertions compare the hook.
- Modify: `tests/e2e/test_e2e_watch.py`: replace `test_watch_auto_build_runs_stage_via_shim_provider` with `test_watch_auto_build_runs_build_hook`. `omc watch --once --auto-build` with a real `.omc/bin/build.sh` that does `echo built > marker; exit 0` asserts `auto-build passed` and the marker. No provider shim.
- Modify: `tests/e2e/test_e2e_integrate.py:test_review_integrate_flags_drifted_build_stage`: keep the stale old skill (it is now an unmigrated repo) and add a rubric line: "it proposes converting the stage to `.omc/bin/build.sh` with its instructions".
- Create: `tests/e2e/test_e2e_stage_hold.py`, `tests/e2e/test_e2e_stage_poll.py`
- Modify: `tests/unit/test_e2e_lifecycle_fixture.py` (the generated hooks run locally)

**Interfaces:**
- Consumes: `watchlock.ALIVE_LOCK` (imported host-side and interpolated into the container scripts), `stagedef.parse_instructions`.
- Produces:
  - `hold_watch_stand_in(container, repo)`: `exec_run(["python3", "-c", <script>, repo], detach=True)`. The script resolves `git -C repo rev-parse --git-common-dir`, opens `<dir>/<ALIVE_LOCK>`, takes `fcntl.flock(LOCK_EX)`, writes `/tmp/omc-watch-stand-in.ready`, then sleeps forever. The helper polls for the ready file for up to 30 s, else `pytest.fail` with the script output.
  - `start_real_watch(container, repo)`: `exec_run(["bash", "-lc", f"cd {repo} && echo $$ > /tmp/omc-watch.pid && exec omc watch --interval 30 > /tmp/omc-watch.log 2>&1"], detach=True)`. It then polls up to 240 s, every 2 s, with a container `python3` probe (`LOCK_SH|LOCK_NB` refused → running); on timeout it calls `pytest.fail` with the tail of `/tmp/omc-watch.log`.
  - `stop_real_watch(container, repo)`: `kill -INT $(cat /tmp/omc-watch.pid)`, then waits up to 30 s for the probe to read "not running".
  - `test_e2e_stage_hold.py::test_failed_verify_is_held_until_watch_stops`, which runs `configure_omc(container, "claude")` and `require_token("claude")`:
    - the fixture is `make_work_repo` plus a verify hook whose body is `echo $$ > /tmp/hold.pid; echo 'E2E piece red' ; echo failed > "$OMC_STAGE_RESULT"; exec sleep 3600`, with instructions `Timeout: none`/`Quiet timeout: none`; it is committed;
    - then `start_real_watch` and `claude -p /omc:verify --output-format text --allowed-tools Bash Read Skill` (timeout 240).
    - Artifact assertions: `.cache/omc/run.json` `state == "held"`; `kill -0 $(cat /tmp/hold.pid)` succeeds after claude exited; after `stop_real_watch`, within 15 s, the pid is gone and `last-verify.json` has `state == "killed"` and `reason` starting `watch-gone`.
  - `test_e2e_stage_poll.py::test_codex_stage_run_outlasts_one_wait` has the markers `e2e`, `expensive`, `e2e_provider("codex")` and `timeout(900)`. Its check hook is `echo started; sleep 560; echo done > /tmp/poll.done; exit 0` with `Quiet timeout: none` and the stand-in watch. A Codex conversation is told `/omc:check`, in the session-driving style of `tests/e2e/test_e2e_lifecycle.py::test_codex_conversation_capabilities`. Assertions: `/tmp/poll.done` exists, `last-check.json` `state == "passed"`, and the turn events hold at least 2 command executions containing `omc internal stage wait check`.

- [x] **Step 1: Write the failing unit test** in `tests/unit/test_e2e_lifecycle_fixture.py`:
  - `test_fixture_hooks_are_valid_and_runnable`: use the local-transport pattern of the existing test, plus a transport that runs `bash -c` locally and makes `omc internal stage template` a no-op. For check/build/verify, `<repo>/.omc/bin/<stage>.sh --show-instructions` exits 0, its output parses with `parse_instructions`, and running the hook appends `<stage>` to the marker.
  - Update `test_verify_stage_sentinel_reports_unavailable_environment` to run `.omc/bin/verify.sh`.
  - New `test_watch_stand_in_script_holds_alive_lock`: run the stand-in script locally (`subprocess.Popen` on `sys.executable -c <script> <tmp git repo>`), wait for the ready file, assert `watchlock.watch_alive(...)` is `True`, kill it, and assert `False`. The script's ready-file path becomes a parameter for this.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_e2e_lifecycle_fixture.py -q -p no:cacheprovider`. Expected: FAIL (no `.omc/bin/` hooks; no stand-in helper).
- [x] **Step 3: Implement** the fixtures, helpers and the two new E2E files per the interfaces above.
- [x] **Step 4: Run** the unit file (expected PASS), then `uv run python -m pytest --collect-only -q tests/e2e/test_e2e_stage_hold.py tests/e2e/test_e2e_stage_poll.py tests/e2e/test_e2e_finish.py tests/e2e/test_e2e_watch.py tests/e2e/test_e2e_integrate.py tests/e2e/golden tests/e2e/variations`. Expected: everything collects, and `test_e2e_stage_poll.py` carries the `expensive` and `codex_gate` markers (check with `-m "e2e and expensive and codex_gate" --collect-only`). Do not run E2E.
- [x] **Step 5: Commit** `git commit -m "test(e2e): hook-stage fixtures, watch stand-in, held-verify and long-poll E2E"`

---

### Task 12: README regeneration (last)

Complexity: simple

**Files:**
- Modify: `.claude/skills/regenerate-readme/SKILL.md`: the Lifecycle sources row adds `src/omc/stagerun.py`, `src/omc/stagedef.py`, `skills/check/SKILL.md` and `skills/build/SKILL.md`. The recipe's lifecycle slot says the subset repair depends on a "Rerunning a subset" section in the verify stage's instructions. A slot for this repo's stages lists `.omc/bin/{check,build,verify}.sh`, and the machine contracts list adds `OMC_STAGE_RUN`.
- Modify: `README.md` (regenerated by `/regenerate-readme` only)
- Modify: `tests/unit/test_cli.py` (the README lifecycle test near line 400)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - The README names `.omc/bin/<stage>.sh`, `OMC_STAGE_RUN`, that stages need a running `omc watch`, and that `watch --auto-build` runs `build.sh` without an LLM.
  - Its stage table rows are `.omc/bin/check.sh` / `build.sh` / `verify.sh`, with the verify row mentioning "Rerunning a subset" and node ids.
  - It no longer says `--auto-build` runs "via the default LLM".

- [x] **Step 1: Write the failing test:**
  - In `test_cli.py`, `subset_repair` becomes `'when the verify stage\'s instructions have a "Rerunning a subset" section'`, with the rest of the sentence as today.
  - The `verify_row` lookup key becomes ``"| `.omc/bin/verify.sh`"``, and `"subset" in verify_row` is kept.
  - New needles in the README: `OMC_STAGE_RUN`, `.omc/bin/check.sh` and `omc watch`. Stale needles: `"via the default LLM"` is not in the README.
- [x] **Step 2: Run** `uv run python -m pytest tests/unit/test_cli.py -q -p no:cacheprovider -k readme`. Expected: FAIL.
- [x] **Step 3: Update** the recipe, then run `/regenerate-readme`. Do not hand-edit `README.md`.
- [x] **Step 4: Run** `tests/unit/test_cli.py`, then the full unit suite (`just check`). Expected: PASS, fully green.
- [x] **Step 5: Commit** `git commit -m "docs(readme): regenerate for hook stages"`

---

## Self-review notes

- **Spec coverage:**

  | Section | Task |
  |---|---|
  | §6.1 | 1 |
  | §6.2 | 3, 5a |
  | §6.3 | 3 (validation), 5a (limits enforced) |
  | §6.4 | 2, 4, 5a, 5b, 6 |
  | §6.4 invariant | 10 |
  | §6.5 | 7; Codex: 11 |
  | §6.6 | 8 |
  | §6.7 integrate, finish | 8 |
  | §6.7 auto-build | 9 |
  | §6.7 this repo | 10, with the bootstrap deviation |
  | §6.7 README | 12 |
  | §7 unit list | 1, 3, 4, 5a, 5b, 9 |
  | §7 manifests list | 7, 8 |
  | §7 E2E list | 11 |

- **Recorded deviations for the audit:**
  - §6.7: the old stage skills are kept (bootstrap).
  - §6.1: `.cache/.gitignore` is written whenever it is missing.
  - §6.4: exit 1 prints no `OMC_STAGE_RUN` line, as house errors do.
  - §6.5: the Codex >540 s test lives in the `expensive` tier with its own `just codex-stage-poll` recipe. It is an evidence run outside the gate (coordinator decision), so it is not part of `just codex-gate`.
  - §6.5: verify's subset arguments are read from `--show-instructions` before `start`, because the agent needs them before any run exists.
