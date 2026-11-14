# herdr sidebar follows an omc session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inside herdr, the sidebar shows the worktree folder (and herdr's branch row below it) for every `omc design|implement|review` launch and for every Claude session start, via a host-less OSC 7 report.

**Architecture:** One pure helper module `src/omc/herdr.py` percent-encodes a path into `ESC ] 7 ; file://<path> BEL`. The fish/zsh/bash builders emit a `TERM_PROGRAM=herdr`-guarded block after `cd <worktree>` (launch report, fish `PWD` follow, exit report of the origin). `session_plan` reads the origin from `ctx.env["PWD"]` and carries it as `SessionPlan.origin_cwd` to the entry points, which forward it to `exec_interactive`. A plugin `hooks/hooks.json` `SessionStart` hook runs the new quiet verb `omc internal herdr-cwd`, which walks `ps -o ppid=,tty=` to the first ancestor with a terminal and writes the report there.

**Tech Stack:** Python 3.12 (uv), pytest, fish/zsh/bash, Claude Code plugin hooks, Docker E2E (testcontainers), herdr 0.9.5.

**Spec:** `docs/superpowers/specs/2026-11-14-support-herdr-tab-titles-design.md` (read it in full; it is the truth this plan argues from).

## Global Constraints

- Project rules: `.omc/config/AGENTS.md`. Red → green for every change: write the test, RUN it, see it fail for the expected reason, implement, see it pass, commit test + implementation together.
- Never `pytest.skip`/`skipif`; a missing prerequisite is `pytest.fail("<exact fix command>")`.
- Stub scripts run on a restricted PATH: shell builtins (`printf`, `:`, `case`) or absolute paths only. **This host is NixOS: `/bin/cat` does not exist** (only `/bin/sh`), so `make_stub`'s `stdout=` path (`/bin/cat <<…`) fails here. New stubs in this plan print with the `printf` builtin.
- `ToolContext` (`src/omc/toolctx.py`) is the only subprocess/env/network boundary. `src/omc/herdr.py` runs `ps` only through `ctx.run_bounded`, reads env only from `ctx.env`, never imports `urllib` or `subprocess`.
- Argv lists only; every value embedded into generated shell text goes through `shlex.quote`.
- Exit codes: `omc internal herdr-cwd` returns 0 in every outcome except wrong arguments (usage on stderr, 2).
- New fish function names use the `__omc_herdr_` prefix (never `__omc_title_*`).
- `README.md` is generated: edit `.claude/skills/regenerate-readme/SKILL.md` and run `/regenerate-readme`; never hand-edit it.
- Never run `omc install`, `uv tool install` or `just install`.
- Run unit tests from the worktree as `uv run --frozen python -m pytest <files> -q` (the venv-shebang trap: bare `uv run pytest` / `just check` may import the primary checkout's code; `--frozen` stops uv from rewriting `uv.lock` — an unfrozen `uv run` here bumped its revision and the omc version, observed while writing this plan; never commit that).
- Unit gate: `just check` has ~22 pre-existing host-environment failures on this host (mostly `make_stub`/`/bin/cat`). The gate is "same failures as on `main`, no new ones": compare `uv run --frozen python -m pytest -m "not e2e and not local_iterm2" -q -n auto 2>&1 | grep ^FAILED | sort` against the same command on a scratch checkout of `main`.
- Host-blocked red→green: a test that fails on this host before reaching the new assertion (e.g. `test_start.py`'s interactive test, whose `wt` stub needs `/bin/cat`) is observed red→green in Debian with:
  `docker run --rm -v "$PWD":/src:ro -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uvcache ghcr.io/astral-sh/uv:python3.12-bookworm sh -c 'apt-get -qq update >/dev/null && apt-get -qq install -y git fish >/dev/null; cp -r /src /tmp/w && cd /tmp/w && uv run -q python -m pytest -q -p no:cacheprovider <node ids>'` (verified working on this host).
- Lint before each commit: `uvx ruff format <changed .py files> && uvx ruff check <changed .py files>`.
- No LLM token exists on this host: never ask for one. Token-free Docker E2E runs with `just e2e-rest <selector>` (`just e2e-tests` runs the token-gated golden path first and would fail here). `scripts/e2e.sh` runs an unfrozen `uv run pytest`: check `git status` afterwards and `git checkout -- uv.lock` if it was rewritten.
- herdr v0.9.5 release assets (sha256 computed 2026-11-14 with `curl -fLsS … | sha256sum`, matching GitHub's published asset digests; both binaries are statically linked):
  - `herdr-linux-x86_64`: `14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d`
  - `herdr-linux-aarch64`: `17d7536f2da50859523680ef279dffcac8e4a235f4e46fe8da6c2ec44141897c`

## Review Focus

1. **A user `EXIT` trap / `zshexit` in `~/.bashrc` / `~/.zshrc`** must still run after the exit report is added — Task 2 adds a real-bash test with a user trap; zsh uses `zshexit_functions+=(…)`, which never replaces a user `zshexit` function.
2. **`ps` answers that never reach pid 1** (a cycle, a stub that always names a non-terminal parent) must not hang the hook — Task 4 caps the walk at `_MAX_ANCESTORS = 32` and tests it.
3. **A tty column that is not a device name** (`../../etc/passwd`, `?`, `??`, empty) is never joined to `/dev/` — Task 4 validates against `^(pts/[0-9]+|tty[A-Za-z0-9]+)$` and tests a hostile value.
4. **The hook process is a session leader with no controlling terminal** (it was started with `setsid`); opening the pane's tty must not make it the hook's controlling terminal — Task 4 opens with `os.O_WRONLY | os.O_NOCTTY`, the E2E drives it through `setsid -w`.
5. **Paths needing encoding** (space, `'`, `%`, non-ASCII, non-UTF-8 bytes) reach herdr byte-exact — Task 1 tests the encoder including a surrogate-escaped byte; Task 2's real-fish test compares fish's runtime encoding to the Python helper.

---

### Task 1: The OSC 7 report helper

Complexity: simple

**Files:**
- Create: `src/omc/herdr.py`
- Test: `tests/unit/test_herdr.py` (new)

**Interfaces:**
- Produces: `percent_encode_path(path: str) -> str` and `osc7_report(path: str) -> str` in `omc.herdr`. `osc7_report(p) == "\033]7;file://" + percent_encode_path(p) + "\a"`.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_herdr.py`

```python
import os
from omc.herdr import osc7_report, percent_encode_path

def test_percent_encode_matches_fish_url_escape():
    assert percent_encode_path("/home/a b/x%y/ü") == "/home/a%20b/x%25y/%C3%BC"
    assert percent_encode_path("/t/repo's space/A-Z_a.z~0") == "/t/repo%27s%20space/A-Z_a.z~0"
    assert percent_encode_path("/x/a+b,c=d@e:f!g(h)*") == "/x/a%2Bb%2Cc%3Dd%40e%3Af%21g%28h%29%2A"

def test_percent_encode_never_raises_on_non_utf8_bytes():
    assert percent_encode_path(os.fsdecode(b"/x/\xff")) == "/x/%FF"

def test_osc7_report_is_hostless_file_uri():
    assert osc7_report("/w/a b") == "\x1b]7;file:///w/a%20b\x07"
```
(The first test's expected values are fish 4.9.3's own `string escape --style=url` output, verified on this host.)

- [x] **Step 2: Run, expect FAIL** — `uv run --frozen python -m pytest tests/unit/test_herdr.py -q` → `ModuleNotFoundError: No module named 'omc.herdr'`.
- [x] **Step 3: Implement** both functions in `src/omc/herdr.py` (module docstring: herdr sidebar cwd reports, spec 2026-11-14). Encode `os.fsencode(path)` byte by byte: keep `A–Z a–z 0–9 - . _ ~ /`, everything else `%XX` uppercase hex. No `urllib`.
- [x] **Step 4: Run, expect PASS** (same command).
- [x] **Step 5: Commit** — `git add src/omc/herdr.py tests/unit/test_herdr.py && git commit -m "feat(herdr): host-less OSC 7 report helper"`

---

### Task 2: Guarded herdr block in the fish, zsh and bash builders

Complexity: high

**Files:**
- Modify: `src/omc/shells/base.py` (`Shell.build_invocation`, `Shell.exec_interactive` signatures; `exec_interactive` forwards the value)
- Modify: `src/omc/shells/fish.py`, `src/omc/shells/zsh.py`, `src/omc/shells/bash.py`, `src/omc/shells/registry.py` (`ShShell` accepts and ignores)
- Test: `tests/unit/test_shells.py`, `tests/unit/test_fish_title.py`

**Interfaces:**
- Consumes: `osc7_report` (Task 1).
- Produces: `build_invocation(*, cwd, title, startup_argv, title_seq, title_argv=None, origin_cwd: str | None = None)` and `exec_interactive(…, origin_cwd: str | None = None)` on every `Shell`.

The exact generated text (these test constants ARE the spec of the block; the implementation produces them byte for byte):

```python
# tests/unit/test_shells.py
from omc.herdr import osc7_report

def fish_herdr(origin=None):
    parts = [
        'if test "$TERM_PROGRAM" = herdr',
        "function __omc_herdr_report; "
        "printf '\\e]7;file://%s\\a' (string escape --style=url -- $argv[1]); end",
        "__omc_herdr_report $PWD",
        "function __omc_herdr_pwd --on-variable PWD; __omc_herdr_report $PWD; end",
    ]
    if origin:
        parts.append(
            f"function __omc_herdr_exit --on-event fish_exit; "
            f"__omc_herdr_report {shlex.quote(origin)}; end"
        )
    return "; ".join([*parts, "end"])

def zsh_herdr(cwd, origin=None):
    lines = ['if [ "$TERM_PROGRAM" = herdr ]; then',
             f"printf '%s' {shlex.quote(osc7_report(cwd))}"]
    if origin:
        lines += [f"__omc_herdr_exit() {{ printf '%s' {shlex.quote(osc7_report(origin))} }}",
                  "zshexit_functions+=(__omc_herdr_exit)"]
    return "\n".join([*lines, "fi"])

def bash_herdr(cwd, origin=None):
    lines = ['if [ "$TERM_PROGRAM" = herdr ]; then',
             f"printf '%s' {shlex.quote(osc7_report(cwd))}"]
    if origin:
        lines += ["__omc_herdr_prev_exit=$(trap -p EXIT)",
                  'eval "__omc_herdr_prev_exit=(${__omc_herdr_prev_exit})"',
                  f"trap -- \"printf '%s' {shlex.quote(osc7_report(origin))}; "
                  '${__omc_herdr_prev_exit[2]}" EXIT']
    return "\n".join([*lines, "fi"])
```

Placement: fish — one more `"; "`-joined part after the whole iTerm2 `if … else … end` (or after the plain branch's `printf`), before the startup command. zsh/bash — lines after the title-helper line (after `cd`), before the startup line. The bash `trap -p` chaining was verified with bash 5.3: a user `trap -- "echo USER_BYE" EXIT` still runs after the origin report. Each builder keeps its block in a small private module-level helper beside it (the `joined_title`/`joined_startup` style of `base.py`); no `Terminal` subclass, no Python read of `TERM_PROGRAM` (spec D6), nothing added to `omc-title.fish`.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_shells.py` (add the constants above):
  - `test_fish_herdr_block_sits_between_title_code_and_startup` — parametrize `title_argv` over `[None, FISH_TITLE_ARGV]`, `origin_cwd="/launch dir"`: `body.index("cd /w/tree") < body.index(fish_herdr("/launch dir")) < body.index(shlex.join(ARGS["startup_argv"]))`; for `FISH_TITLE_ARGV` also `"; end; " + fish_herdr("/launch dir") + "; " in body`.
  - `test_fish_herdr_exit_handler_only_with_origin` — `origin_cwd=None` → `fish_herdr() in body and "__omc_herdr_exit" not in body`.
  - `test_zsh_and_bash_herdr_block_after_cd_before_startup` — parametrize shell × origin `[None, "/launch dir"]`: `rc.index("cd /w/tree") < rc.index(block) < rc.index(startup)` with `block = zsh_herdr("/w/tree", origin)` / `bash_herdr(...)`; with `None`, `"__omc_herdr_exit" not in rc` / `"trap -- " not in rc`.
  - `test_sh_ignores_origin_cwd` — `detect_shell({}).build_invocation(**ARGS) == detect_shell({}).build_invocation(**ARGS, origin_cwd="/launch dir")`.
  - `test_exec_interactive_forwards_origin_cwd` — `FishShell().exec_interactive(**ARGS, origin_cwd="/o")` with `os.chdir`/`os.execvp` monkeypatched in `omc.shells.base` (as `test_exec_interactive_forwards_title_helper` does); `"__omc_herdr_report /o; end" in executed[0][3]`.
  - `test_bash_exit_report_keeps_user_exit_trap` — real bash (`shutil.which("bash")` or `pytest.fail("bash is required: sudo apt-get install bash")`): build with `cwd=<tmp>/"wt dir"`, `origin_cwd=<tmp>/"launch dir"`, `startup_argv=["true"]`, `title_seq=""` (so `PROMPT_COMMAND`'s title printf adds no bytes to stdout); write the rc file into a tmp dir and substitute `TMPDIR_PLACEHOLDER`; `HOME=<tmp>/home` whose `.bashrc` is `trap -- "echo USER_BYE" EXIT`; run `subprocess.run([bash, "--rcfile", rc, "-i"], input="exit\n", env={"HOME": …, "PATH": os.environ["PATH"], "TERM_PROGRAM": "herdr"}, capture_output=True, text=True, timeout=25)`; assert `proc.stdout == osc7_report(wt) + osc7_report(launch) + "USER_BYE\n"`.
  - Update existing tests the block changes: `test_fish_without_title_argv_is_unchanged` → rename `test_fish_without_title_argv_exact_text`, expected text gains `f"; {fish_herdr()}"` before `"; " + startup`. In `test_fish_generated_command_is_one_if_else_around_the_hook`, first `assert body.endswith("; end; " + fish_herdr() + "; " + startup)`, strip `"; " + fish_herdr()` out of `body`, then run the existing assertions unchanged on the remainder.
- [x] **Step 2: Write the failing real-fish tests** in `tests/unit/test_fish_title.py`: give the fixture's `run` an `origin_cwd=None` keyword passed to `build_invocation`. Then:
  - `test_herdr_reports_worktree_cd_and_origin_without_host(fish_session, tmp_path)` — `other = tmp_path / "other dir ü"` (mkdir), `origin = tmp_path / "launch 100%"`; `proc, _ = run(f"cd {shlex.quote(str(other))}", origin_cwd=str(origin), env_extra={"TERM_PROGRAM": "herdr"})`; `assert re.findall("\x1b\\]7;file:///[^\x07]*\x07", proc.stdout) == [osc7_report(str(repo)), osc7_report(str(other)), osc7_report(str(origin))]`.
  - `test_no_hostless_report_outside_herdr` — parametrize `env_extra` over `{}` and `{"TERM_PROGRAM": "tmux"}`, same `run(...)` otherwise: `"\x1b]7;file:///" not in proc.stdout`. (fish's own host-ful OSC 7 may appear; it is not matched.)
- [x] **Step 3: Run, expect FAIL** — `uv run --frozen python -m pytest tests/unit/test_shells.py tests/unit/test_fish_title.py -q` → `TypeError: … unexpected keyword argument 'origin_cwd'` / missing block. (`test_generated_start_in_iterm2_dispatches_exactly_one_set_despite_double_sourcing` is a pre-existing host failure — `cat` not found on its stub PATH — and stays as it is.)
- [x] **Step 4: Implement** the signatures in `base.py` (abstract + `exec_interactive`, which passes `origin_cwd` into its `build_invocation` call), the blocks in fish/zsh/bash, the ignored keyword in `ShShell`. Each builder keeps its block in one small private helper (`_herdr_block(...)`) next to the builder.
- [x] **Step 5: Run, expect PASS** (same command, only the pre-existing host failure left).
- [x] **Step 6: Commit** — `git add src/omc/shells tests/unit/test_shells.py tests/unit/test_fish_title.py && git commit -m "feat(shells): report the worktree to herdr at launch and the origin at exit"`

---

### Task 3: Carry the origin from `PWD` through `SessionPlan` to the three entry points

Complexity: medium

**Files:**
- Modify: `src/omc/session.py` (`SessionPlan.origin_cwd: str | None` as the last field, no default — `session_plan` is its only constructor; `session_plan` reads `ctx.env.get("PWD") or None`, the same `ctx.env` it already hands to `detect_terminal`/`detect_shell`, and passes it to `build_invocation`). The dry-run printers (`_print_plan` in start/implement/review) print `shell_argv` only and get no new row.
- Modify: `src/omc/start.py:209-215`, `src/omc/implement.py:142-148`, `src/omc/review.py:105-111` (add `origin_cwd=plan.origin_cwd` to `exec_interactive`)
- Test: `tests/unit/test_session.py`, `tests/unit/test_start.py`, `tests/unit/test_implement.py`, `tests/unit/test_review.py`

**Interfaces:**
- Consumes: `build_invocation(..., origin_cwd=...)`, `exec_interactive(..., origin_cwd=...)` (Task 2).
- Produces: `SessionPlan.origin_cwd: str | None` (last field).

- [x] **Step 1: Write the failing tests**
  - `tests/unit/test_session.py::test_session_plan_carries_pwd_as_origin_cwd` — `ctx = ToolContext.from_env({"HOME": "/nowhere", "PATH": "/nowhere", "SHELL": "/usr/bin/fish", "PWD": "/launch dir"})`; `plan = session_plan(ctx, Config(), seed="s", slug="s", session_name="s", title="t", cwd="/w/tree")`; `assert plan.origin_cwd == "/launch dir"` and `"__omc_herdr_report '/launch dir'; end" in plan.shell_argv[3]`.
  - `tests/unit/test_session.py::test_session_plan_origin_cwd_is_none_without_pwd` — `_ctx()` → `plan.origin_cwd is None`.
  - In `test_implement.py::test_interactive_execs_in_the_worktree_with_slug_env`, `test_review.py::test_interactive_execs_in_worktree_with_slug_env` and `test_start.py::test_interactive_handoff_uses_branch_title_and_slug_session`: set `ctx.env["PWD"] = "/launch dir"` before the run; add `assert seen[0]["origin_cwd"] == "/launch dir"`.
- [x] **Step 2: Run, expect FAIL** — `uv run --frozen python -m pytest tests/unit/test_session.py tests/unit/test_implement.py tests/unit/test_review.py -q -k "origin_cwd or interactive_execs"` → `AttributeError: 'SessionPlan' object has no attribute 'origin_cwd'` / `KeyError: 'origin_cwd'`. `test_start.py`'s interactive test is a host baseline failure here (its `wt` stub needs `/bin/cat`): observe its red with the Debian `docker run` command from Global Constraints, node id `tests/unit/test_start.py::test_interactive_handoff_uses_branch_title_and_slug_session` → `KeyError: 'origin_cwd'`.
- [x] **Step 3: Implement** the field, the read and the three forwards.
- [x] **Step 4: Run, expect PASS** — same commands (host + Debian container for `test_start`).
- [x] **Step 5: Commit** — `git add src/omc/session.py src/omc/start.py src/omc/implement.py src/omc/review.py tests/unit/test_session.py tests/unit/test_start.py tests/unit/test_implement.py tests/unit/test_review.py && git commit -m "feat(session): forward the launch directory to the shell builders"`

---

### Task 4: `omc internal herdr-cwd`

Complexity: medium

**Files:**
- Modify: `src/omc/herdr.py` (add `run_herdr_cwd`, `_DEV_DIR`, `_MAX_ANCESTORS`, the tty pattern)
- Modify: `src/omc/internal.py` (`_USAGE` gains ` | herdr-cwd`; dispatcher branch)
- Test: `tests/unit/test_herdr.py`

**Interfaces:**
- Consumes: `osc7_report` (Task 1); `ToolContext.run_bounded(argv, *, timeout)`.
- Produces: `run_herdr_cwd(ctx: ToolContext, stdin: TextIO) -> int` (always 0); `_DEV_DIR: Path = Path("/dev")` (tests monkeypatch it); dispatcher branch shaped like `design-record`/`models` (no-argument verbs): `if cmd == "herdr-cwd": if rest → print(_USAGE, file=sys.stderr); return 2`, then a lazy `from .herdr import run_herdr_cwd` inside the branch (as `global-instructions`/`dependency` do — this runs on every Claude start) and `return run_herdr_cwd(ToolContext.from_env(), sys.stdin)`.

There is no ancestor-walk or tty-write precedent in `src/omc`; `ctx.run_bounded` is the right seam (short captured call with a hard deadline, as `terminals.py` uses for the iTerm2 worker with `timeout=5`). Algorithm, in order (spec §4.5): return unless `ctx.env.get("TERM_PROGRAM") == "herdr"`; `json.loads(stdin.read())` must be a dict whose `cwd` is a `str` with `os.path.isabs`; walk from `os.getppid()`: `ctx.run_bounded(["ps", "-o", "ppid=,tty=", "-p", str(pid)], timeout=2)`, nonzero rc or unparsable line (`ppid tty`, two fields, int ppid) ends; tty `?`/`??` → next `pid = ppid` unless `ppid <= 1` or `_MAX_ANCESTORS = 32` steps done; a tty not matching `re.fullmatch(r"pts/[0-9]+|tty[A-Za-z0-9]+", tty)` ends; write `osc7_report(cwd).encode("ascii")` with `fd = os.open(_DEV_DIR / tty, os.O_WRONLY | os.O_NOCTTY)` / `os.write` / `os.close`. Catch only `OSError`, `TimeoutError`, `ValueError` (covers `json.JSONDecodeError`). Prints nothing anywhere.

- [x] **Step 1: Write the failing tests** in `tests/unit/test_herdr.py`. Helper `_ps_stub(bindir, line, rc=0)` writes `#!/bin/sh\nprintf '%s\n' "$*" >> <log>\nprintf '%s\n' <shlex.quote(line)>\nexit <rc>\n` (builtins only — no `/bin/cat` on this host). Fixture `herdr_env(tmp_path, monkeypatch)`: `dev = tmp_path/"dev"`, `(dev/"pts").mkdir(parents=True)`, `(dev/"pts"/"7").write_bytes(b"")`, `monkeypatch.setattr("omc.herdr._DEV_DIR", dev)`, `monkeypatch.setenv("PATH", str(bindir))`, `monkeypatch.setenv("TERM_PROGRAM", "herdr")`, a `stdin(text)` setter doing `monkeypatch.setattr("sys.stdin", io.StringIO(text))`.
  - `test_herdr_cwd_writes_encoded_report_to_first_ancestor_terminal` — stub line `"  4242 pts/7"`, stdin `{"cwd": "/home/a b/x%y/ü", "hook_event_name": "SessionStart"}`: `run_internal(["herdr-cwd"]) == 0`; `(dev/"pts"/"7").read_bytes() == b"\x1b]7;file:///home/a%20b/x%25y/%C3%BC\x07"`; ps log's first line `== f"-o ppid=,tty= -p {os.getppid()}"`; `capsys.readouterr()` out and err both `""`.
  - `test_herdr_cwd_outside_herdr_does_nothing` — parametrize `TERM_PROGRAM` unset / `"tmux"`: rc 0, device empty, ps log absent, no output.
  - `test_herdr_cwd_ends_silently` — one parametrized test, each case rc 0, device `pts/7` still empty, no output: stdin `""`; stdin `"not json"`; stdin `{"session_id": "x"}`; stdin `{"cwd": "relative/dir"}`; ps line `"    1 ?"` (no ancestor with a terminal); ps rc 1 with empty line; ps line `"4242 ?"` (never reaches pid 1 — bounded by `_MAX_ANCESTORS`); ps line `"4242 ../../etc/passwd"` (not a device name); ps line `"4242 pts/9"` (device absent → unwritable).
  - `test_herdr_cwd_rejects_extra_arguments` — `run_internal(["herdr-cwd", "x"]) == 2`.
- [x] **Step 2: Run, expect FAIL** — `uv run --frozen python -m pytest tests/unit/test_herdr.py -q` → rc 2 (unknown verb → usage) / `AttributeError: … '_DEV_DIR'`.
- [x] **Step 3: Implement** `run_herdr_cwd` in `src/omc/herdr.py` and the dispatcher branch + `_USAGE` entry in `src/omc/internal.py`.
- [x] **Step 4: Run, expect PASS** — `uv run --frozen python -m pytest tests/unit/test_herdr.py tests/unit/test_internal.py::test_unknown_internal_subcommand -q`.
- [x] **Step 5: Commit** — `git add src/omc/herdr.py src/omc/internal.py tests/unit/test_herdr.py && git commit -m "feat(internal): herdr-cwd reports a Claude session's directory to herdr"`

---

### Task 5: Plugin `SessionStart` hook

Complexity: simple

**Files:**
- Create: `hooks/hooks.json` (repository root, beside `skills/`; no manifest change)
- Test: `tests/unit/test_plugin_manifests.py`

**Interfaces:**
- Consumes: the verb name `herdr-cwd` (Task 4).
- Produces: `HERDR_HOOK_COMMAND = "omc internal herdr-cwd >/dev/null 2>&1 || true"` (test constant; the file's command string). stdin is left alone (it carries the hook JSON); both redirections and `|| true` absorb a missing `omc` (`sh: omc: not found` goes to the redirected stderr) and an older CLI's usage + exit 2.

`hooks/hooks.json`:
```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "omc internal herdr-cwd >/dev/null 2>&1 || true", "timeout": 5 } ] }
    ]
  }
}
```

- [x] **Step 1: Write the failing tests** in `tests/unit/test_plugin_manifests.py`:
  - `test_plugin_registers_one_quiet_session_start_hook` — load `ROOT/"hooks"/"hooks.json"`; `list(data["hooks"]) == ["SessionStart"]`; `[entry] = data["hooks"]["SessionStart"]`; `"matcher" not in entry`; `entry["hooks"] == [{"type": "command", "command": HERDR_HOOK_COMMAND, "timeout": 5}]`.
  - `test_herdr_hook_command_is_a_silent_success_whatever_omc_is` — reads the command FROM `hooks/hooks.json` (so it runs what ships, and is red until the file exists); parametrize `omc_body` over `None` (no `omc` on PATH), `"printf 'usage: omc internal {…}\\n' >&2; exit 2"` (older CLI), `"exit 1"`: write `#!/bin/sh\n<body>\n` as `bindir/"omc"` (chmod 755) unless `None`; `subprocess.run(["/bin/sh", "-c", command], input='{"cwd": "/x"}', env={"PATH": str(bindir)}, capture_output=True, text=True, timeout=10)` → `returncode == 0`, `stdout == ""`, `stderr == ""`.
- [x] **Step 2: Run, expect FAIL** — `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py -q -k hook` → `FileNotFoundError: …/hooks/hooks.json` in both tests.

Context (verified): Claude Code's plugin cache holds a copy of the whole repository tree (`~/.claude/plugins/cache/oh-my-clanker/omc/0.1.46/` has `docker/ docs/ skills/ src/ …`), so a root `hooks/hooks.json` reaches it with no manifest change. `notify.py` strips only the exact `OLD_CLAUDE_COMMAND` (`omc internal notify --provider claude`) from worktree settings — no collision. The Codex payload check (`plugin.py:_codex_skill_files`, `tests/e2e/codex_plugin_payload.py`) compares `skills/` only.
- [x] **Step 3: Create** `hooks/hooks.json` as above.
- [x] **Step 4: Run, expect PASS** — same command, plus `uv run --frozen python -m pytest tests/unit/test_plugin_manifests.py tests/unit/test_plugin.py tests/unit/test_codex_plugin.py -q` (Codex payload compares `skills/` only; must stay green).
- [x] **Step 5: Commit** — `git add hooks/hooks.json tests/unit/test_plugin_manifests.py && git commit -m "feat(plugin): SessionStart hook reports the session directory to herdr"`

---

### Task 6: Docker E2E — real herdr, real fish/zsh/bash, real hook command

Complexity: high

**Files:**
- Modify: `docker/Dockerfile.e2e`
- Create: `tests/e2e/test_e2e_herdr.py`

**Interfaces:**
- Consumes: the installed omc in the image (`exec_interactive(..., origin_cwd=...)`, `omc internal herdr-cwd`), `hooks/hooks.json` via the image's plugin install, harness `run_in`, `require_token`, `_e2e_model_id`.

- [x] **Step 1: Image (apply only after Step 4's red run).** Add one new layer AFTER the GitNexus embeddings pre-bake and BEFORE `COPY . /repo` (so the expensive cached layers stay cached; do not touch the first `apt-get` line):
```dockerfile
# herdr (sidebar E2E, spec 2026-11-14) + the shells its launch blocks target.
# Static release binaries; bump HERDR_VERSION and both sha256 values together.
ARG HERDR_VERSION=0.9.5
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends fish zsh procps \
      && rm -rf /var/lib/apt/lists/*; \
    case "${TARGETARCH}" in \
      amd64) HERDR_ASSET=herdr-linux-x86_64; \
             HERDR_SHA256=14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d ;; \
      arm64) HERDR_ASSET=herdr-linux-aarch64; \
             HERDR_SHA256=17d7536f2da50859523680ef279dffcac8e4a235f4e46fe8da6c2ec44141897c ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -LsSf -o /tmp/herdr "https://github.com/herdrdev/herdr/releases/download/v${HERDR_VERSION}/${HERDR_ASSET}"; \
    echo "${HERDR_SHA256}  /tmp/herdr" | sha256sum -c -; \
    install -m 0755 /tmp/herdr /usr/local/bin/herdr; rm /tmp/herdr; \
    herdr --version
```
If `herdr --version` or the headless server does not run on `node:22.23.4-bookworm-slim`, STOP and report (spec §7) — do not weaken the test.

- [x] **Step 2: Write the token-free tests** in `tests/e2e/test_e2e_herdr.py` (`pytestmark = pytest.mark.e2e`; function-scoped `container` fixture, one fresh container per test). Helpers in the module:
  - `_herdr(container, *args) -> dict` — `run_in(container, ["herdr", "--session", "e2e", *args], stdout_only=True)`, assert rc 0, return `json.loads(out)["result"]`.
  - `_start(container, launch: str) -> tuple[str, str]` — `run_in(container, ["bash", "-c", f"mkdir -p {q(launch)} && git init -q {q(launch)}"])`; start the server with docker-py's detached exec, `container.get_wrapped_container().exec_run(["herdr", "--session", "e2e", "server"], detach=True, environment={"SHELL": "/bin/bash"})` (the pane shell is `$SHELL`); poll `herdr --session e2e workspace list` until rc 0 (≤ 15 s, else `pytest.fail` naming the image's herdr); then `result = _herdr(container, "workspace", "create", "--cwd", launch, "--no-focus")` and return `(result["workspace"]["workspace_id"], result["root_pane"]["pane_id"])` (shapes verified against herdr 0.9.5 on this host).
  - `_type(container, pane, command)` — `_herdr(container, "pane", "run", pane, command)`.
  - `_expect(container, workspace, pane, path)` — poll ≤ 15 s until `_herdr(…, "pane", "get", pane)["pane"]["cwd"] == path` and `_herdr(…, "workspace", "get", workspace)["workspace"]["label"] == os.path.basename(path)`; on timeout assert with the last values and `pane read <pane>` output.
  - Launch script written into the container at `/tmp/omc_launch.py`: imports `FishShell`/`ZshShell`/`BashShell`, picks by `sys.argv[1]`, calls `exec_interactive(cwd=sys.argv[2], title="e2e", startup_argv=["true"], title_seq="", origin_cwd=os.environ["PWD"])` — `true` stands where the provider would be. Typed into the pane as `"$(uv tool dir)/omc/bin/python" /tmp/omc_launch.py <shell> '/work/wt dir'` (the installed omc's interpreter).
  - `test_launch_block_moves_sidebar_and_exit_restores_it[fish|zsh|bash]` — launch `/work/launch`, `mkdir -p '/work/wt dir'`; type the launch command; `_expect(… "/work/wt dir")`; for fish only, type `cd /work/other` (mkdir first) and `_expect(… "/work/other")`; type `exit`; `_expect(… "/work/launch")`.
  - `test_hook_command_without_terminal_reports_session_cwd` — launch `/work/launch`; write `/tmp/hook.json` = `{"cwd": "/work/hook dir", "hook_event_name": "SessionStart"}` and `mkdir -p '/work/hook dir'`; type `setsid -w omc internal herdr-cwd < /tmp/hook.json` (`-w` keeps the `setsid` parent — still on the pane's terminal — alive so the walk finds it; verified on this host's herdr: the setsid child shows tty `?`, its parent `pts/N`); `_expect(… "/work/hook dir")`.
- [x] **Step 3: Write the token-gated test** in the same module: `test_claude_session_start_hook_reports_session_cwd` — `require_token("claude")` first; `model = _e2e_model_id(container, "claude")`; launch `/work/launch`; `mkdir -p '/work/claude dir'`; type `env -C '/work/claude dir' claude -p 'Reply with OK' --model <model> --output-format text >/tmp/claude.out 2>&1; echo done >/tmp/claude.done`; poll for `/tmp/claude.done` (≤ 180 s); `_expect(… "/work/claude dir")` (the pane's own shell never left `/work/launch`, so only the plugin hook can have moved it). This host has no token: this test is written but cannot be run here; it runs wherever the token-gated tier runs.
  Module layout follows `tests/e2e/test_e2e_interactive.py`: module-level `pytestmark = pytest.mark.e2e`, token-free tests take `container` directly, the token-gated one calls `require_token` in its body.
- [x] **Step 4: Run, expect FAIL** — do Steps 2–3 BEFORE Step 1 and run `just e2e-rest tests/e2e/test_e2e_herdr.py -k "not claude"` → 4 failed, `herdr: command not found`. Then apply Step 1.
- [x] **Step 5: Run, expect PASS** — `just e2e-rest tests/e2e/test_e2e_herdr.py -k "not claude"` → 4 passed. Also `just e2e-rest tests/e2e/test_e2e_smoke.py` (image still sane) and `uv run --frozen python -m pytest tests/unit/test_e2e_provider_support.py -q` (its Dockerfile guard reads only the `RUN npm install` line; must stay green).
- [x] **Step 6: Commit** — `git add docker/Dockerfile.e2e tests/e2e/test_e2e_herdr.py && git commit -m "test(e2e): herdr sidebar follows omc launches and the hook command"`

---

### Task 7: `docker/PLUGIN-NOTES.md` — what each harness did with `hooks/`

Complexity: simple

**Files:**
- Modify: `docker/PLUGIN-NOTES.md` (new dated section `## Plugin hooks/ directory (2026-11-14)`)

- [x] **Step 1: Observe in the Task 6 image** (newest `omc-e2e:*` tag from `docker images omc-e2e --format '{{.Tag}} {{.CreatedAt}}'`), token-free:
  - `docker run --rm <image> bash -c 'claude plugin list --json'` — omc enabled, no `errors`.
  - `docker run --rm <image> bash -c 'find /root/.claude/plugins -path "*omc*" -name hooks.json -print -exec cat {} \;'` — whether Claude's installed copy carries `hooks/hooks.json`.
  - `docker run --rm <image> bash -c 'codex plugin list --json; find / -xdev -path "*omc*/hooks/hooks.json" -not -path "/repo/*" 2>/dev/null'` — Codex: did `codex plugin add` copy `hooks/`; the image build's `codex_plugin_payload record` step passed (it ran in `setup-plugins.sh`).
- [x] **Step 2: Write the section**: the commands, their observed output (trimmed), and the conclusion per harness; state that the live firing of the hook is proven only by `test_claude_session_start_hook_reports_session_cwd`, which needs a token and was not run on this host. No test (observation record only).
- [x] **Step 3: Commit** — `git add docker/PLUGIN-NOTES.md && git commit -m "docs(plugin-notes): record how Claude and Codex treat hooks/"`

---

### Task 8: Documentation — `docs/shell-integration.md` and the generated README

Complexity: simple

**Files:**
- Modify: `docs/shell-integration.md` (new herdr paragraph after the iTerm2 paragraph)
- Modify: `.claude/skills/regenerate-readme/SKILL.md:85`
- Regenerate: `README.md` via `/regenerate-readme` (never by hand)
- Test: `tests/unit/test_cli.py`

- [x] **Step 1: Write the failing guards** in `tests/unit/test_cli.py`, in the styles already there:
  - `test_shell_integration_documents_herdr_sidebar` — needle list like `test_shell_integration_documents_every_user_facing_title_surface`, over `docs/shell-integration.md`: `"herdr"`, `"TERM_PROGRAM=herdr"`, `"SessionStart"`, `"omc internal herdr-cwd"`, `"tmux"`, `"plain \`cd\`"`.
  - `test_readme_links_herdr_to_shell_integration` — recipe + README dual check like `test_readme_documents_provider_selection_per_step`: the recipe's `#### \`## Install\`` slot (`recipe.split("#### \`## Install\`", 1)[1].split("\n#### ", 1)[0]`) contains `"herdr"`, and README has a line containing both `"herdr"` and `"(docs/shell-integration.md)"`.
- [x] **Step 2: Run, expect FAIL** — `uv run --frozen python -m pytest tests/unit/test_cli.py -q -k "herdr"` → both fail on the missing needles.
- [x] **Step 3: Write the paragraph** (it must name the guard `TERM_PROGRAM=herdr` and the hook verb `omc internal herdr-cwd` literally) with exactly the content of spec §6 bullet 1: what updates (folder and branch rows), when (launch; `cd` in fish; shell exit), sessions inside tmux within herdr are left alone, ordinary fish shells are not followed (fish's hostname in its own OSC 7), every Claude session started/resumed inside herdr reports its directory through the plugin hook (Claude only), and the two limits of spec §7 (plain `cd` after a report; only the workspace's first pane names the workspace).
- [x] **Step 4: Recipe line** — in the `## Install` slot, change line 85 to: "One sentence links macOS fish/iTerm2 branch titles and the herdr sidebar's worktree folder and branch to `docs/shell-integration.md`." Then invoke `/regenerate-readme` (Skill `regenerate-readme`) and let it rewrite `README.md` (it also picks up the new `herdr-cwd` verb from `src/omc/internal.py` if its recipe covers internal verbs).
- [x] **Step 5: Run, expect PASS** — `uv run --frozen python -m pytest tests/unit/test_cli.py -q` (README guards and the new guard; `test_aws_credential_process_runs_bannerless_and_needs_no_config` is a pre-existing host failure).
- [x] **Step 6: Commit** — `git add docs/shell-integration.md .claude/skills/regenerate-readme/SKILL.md README.md tests/unit/test_cli.py && git commit -m "docs: herdr sidebar support in shell integration and README"`

---

## Final gate (conductor, after Task 8)

- [x] Unit: baseline-identical failure set vs `main` (Global Constraints).
- [x] `just build` (ruff format check, ruff, `uv build`).
- [x] Token-free E2E: `just e2e-rest tests/e2e/test_e2e_herdr.py -k "not claude"` plus `tests/e2e/test_e2e_smoke.py`. The token-gated `test_claude_session_start_hook_reports_session_cwd` and the Codex plugin gate cannot run on this host; say so in the handoff.
