"""Container exec helpers. Every E2E test drives a FRESH container; the
container is the sandbox. Tokens are passed from the host env; a missing token
FAILS the test with guidance — it never skips."""

from __future__ import annotations

import json
import os
import re
import shlex
import time

import pytest

from omc.stagedef import parse_instructions
from omc.watchlock import ALIVE_LOCK

# Env vars that can authenticate each provider's CLI, in preference order.
# claude accepts an Anthropic API key as well as a `claude setup-token` OAuth token.
TOKEN_ENV = {
    "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "codex": ("OPENAI_API_KEY",),
}

_TOKEN_GUIDANCE = {
    "claude": "put an ANTHROPIC_API_KEY or a `claude setup-token` token in .env",
    "codex": "run `just codex-login` for account auth, or put an OPENAI_API_KEY in .env",
}

PROVIDERS = list(TOKEN_ENV)

# Fixed in-container path the stub Jira MCP appends one JSON line to per write
# (assignIssue / transitionIssue). Tracker-write assertions read THIS file, not
# the LLM transcript: the log is deterministic, transcripts are not.
MUTATIONS_LOG = "/tmp/stub-jira-mutations.jsonl"

ALL_TOKEN_VARS = tuple(dict.fromkeys(v for vars_ in TOKEN_ENV.values() for v in vars_))


def require_token(provider: str) -> None:
    varnames = TOKEN_ENV[provider]
    if provider == "codex" and os.environ.get("CODEX_AUTH_VOLUME"):
        from .codex_auth import selected_volume

        selected_volume()  # validates that this is a Docker volume, never a host path
        return
    if not any(os.environ.get(v) for v in varnames):
        wanted = " or ".join(f"${v}" for v in varnames)
        pytest.fail(
            f"live {provider} E2E needs {wanted} — {_TOKEN_GUIDANCE[provider]}; then re-run."
        )


def require_env(var: str, guidance: str) -> None:
    """A specific variable, not "any auth for the provider": the API-backend
    E2E talks to Anthropic directly, so CLAUDE_CODE_OAUTH_TOKEN cannot stand in."""
    if not os.environ.get(var):
        pytest.fail(f"live E2E needs ${var} — {guidance}; then re-run.")


# Auth failures surface INSIDE the container, printed by a provider CLI that omc
# spawns itself — run_in's argv is usually ["omc", ...], so the failing provider
# cannot be read off the command. These signatures are provider-specific strings,
# so a match identifies the provider on its own; no provider argument is needed.
#
# VERIFIED strings only, captured from live container runs (the first is also
# recorded in docker/PLUGIN-NOTES.md). Codex has no entries because
# its auth-failure output has never been observed here — do not guess one, and
# never broaden these to a fragment like "OAuth", "401", or "Authentication
# failed": the stub Jira MCP's auth-error mode emits "Authentication failed (HTTP
# 401): OAuth token expired or revoked" on purpose, and a loose matcher would fire
# on it and break test_slug_mcp_unauthenticated.
_AUTH_FAILURES = (
    (
        "Not logged in",
        "claude has no credentials in the container — "
        f"{_TOKEN_GUIDANCE['claude']} (start from `cp env.example .env`).",
    ),
    (
        "OAuth access token is invalid",
        "claude rejected CLAUDE_CODE_OAUTH_TOKEN as expired or malformed — "
        "re-run `claude setup-token` and replace it in .env.",
    ),
)


def detect_auth_failure(output: str) -> str | None:
    """Map a container CLI's auth-failure output to its remediation.

    Pure: text in, guidance or None out. run_in calls this on every exec so a bad
    credential fails at the point of use with the fix attached, instead of
    surfacing as an unrelated assertion three tests later.
    """
    for signature, remediation in _AUTH_FAILURES:
        if signature in output:
            return remediation
    return None


def run_in(container, argv, *, env=None, cwd=None, timeout=600, stdout_only=False):
    """Exec argv in the container; return (rc, combined output or stdout only).

    A provider auth failure anywhere in that output fails the test right here,
    with the remediation — otherwise it resurfaces later as a confusing
    assertion about missing text, blamed on whichever test ran first.
    """
    cmd = shlex.join(argv)
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    wrapped = ["timeout", str(timeout), "bash", "-lc", cmd]
    envs = {k: v for k, v in (env or {}).items()}
    result = container.get_wrapped_container().exec_run(
        wrapped, environment=envs or None, demux=stdout_only
    )
    if stdout_only:
        stdout, stderr = result.output
        output = (stdout or b"").decode(errors="replace")
        auth_output = output + (stderr or b"").decode(errors="replace")
    else:
        output = result.output.decode(errors="replace")
        auth_output = output
    remediation = detect_auth_failure(auth_output)
    if remediation is not None:
        pytest.fail(f"provider auth failed in container: {remediation}\n\n{auth_output}")
    return result.exit_code, output


def set_codex_container_policy(container, *, reasoning_effort=None, run=None) -> None:
    """Allow Codex shell tools inside the disposable Docker E2E container.

    Nested bubblewrap namespaces are unavailable there. Keep approval at
    ``never`` and preserve the selected CODEX_HOME's plugin and MCP tables.
    Lifecycle tests may also request high reasoning without duplicating keys.
    """
    settings = {"approval_policy": "never", "sandbox_mode": "danger-full-access"}
    if reasoning_effort is not None:
        settings["model_reasoning_effort"] = reasoning_effort
    script = """\
import json, os, sys, tomllib
from pathlib import Path

home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
path = home / "config.toml"
source = path.read_text() if path.exists() else ""
data = tomllib.loads(source)
settings = json.loads(sys.argv[1])
for key, value in settings.items():
    if key in data and data[key] != value:
        raise ValueError(f"conflicting Codex E2E setting {key}: {data[key]!r}")
missing = {key: value for key, value in settings.items() if key not in data}
if missing:
    home.mkdir(parents=True, exist_ok=True)
    prefix = "".join(f"{key} = {json.dumps(value)}\\n" for key, value in missing.items())
    path.write_text(prefix + source)
    parsed = tomllib.loads(path.read_text())
    assert all(parsed[key] == value for key, value in settings.items())
"""
    runner = run or run_in
    rc, out = runner(container, ["python3", "-c", script, json.dumps(settings)])
    assert rc == 0, f"Codex E2E container policy failed: {out}"


_E2E_MODELS = {
    "claude": ("CLAUDE_E2E_MODEL", "claude-sonnet-5-5"),
    "codex": ("CODEX_E2E_MODEL", "gpt-6-astra"),
}
_TASK_MODEL_LEAVES = (
    "model",
    "tasks.design",
    "tasks.plan",
    "tasks.review",
    "tasks.simple",
    "tasks.medium",
    "tasks.high",
)


def _e2e_model_id(container, provider: str) -> str:
    variable, default = _E2E_MODELS[provider]
    model = os.environ.get(variable, default)
    if not model:
        pytest.fail(f"{variable} must name a real model ID for live E2E")
    if provider == "claude" and model in ("fable", "opus", "sonnet"):
        rc, out = run_in(
            container,
            [
                "claude",
                "-p",
                "Reply with only your exact model id",
                "--model",
                model,
                "--output-format",
                "text",
            ],
            timeout=120,
        )
        model_id = out.strip()
        if rc != 0 or not re.fullmatch(rf"claude-{model}-[A-Za-z0-9.-]+", model_id):
            pytest.fail(
                f"Could not resolve CLAUDE_E2E_MODEL={model} to a full ID via Claude; "
                "set CLAUDE_E2E_MODEL to a full model ID or fix Claude authentication. "
                f"CLI output: {out[:500]}"
            )
        return model_id
    if not re.fullmatch(
        r"claude-[A-Za-z0-9.-]+" if provider == "claude" else r"gpt-[A-Za-z0-9.-]+", model
    ):
        pytest.fail(f"{variable} must be a full {provider} model ID")
    return model


def configure_omc(container, provider: str, *, timeout: int = 600) -> str:
    """Install the provider's plugins and configure omc for it; `timeout` bounds each
    of the two `omc configure` runs (the alias probe keeps its own 120 s)."""
    setup = container.get_wrapped_container().exec_run(
        ["bash", "/repo/docker/setup-plugins.sh", provider]
    )
    if setup.exit_code != 0:
        detail = setup.output.decode(errors="replace")[-2000:].strip()
        for var in ALL_TOKEN_VARS:
            if token := os.environ.get(var):
                detail = detail.replace(token, "[redacted]")
        pytest.fail(f"plugin setup failed for {provider} (exit {setup.exit_code}): {detail}")
    if (
        provider == "codex"
        and os.environ.get("OPENAI_API_KEY")
        and not os.environ.get("CODEX_AUTH_VOLUME")
    ):
        # Codex >=0.144 needs an explicit stdin login; bare API env is not enough.
        login = container.get_wrapped_container().exec_run(
            ["bash", "-c", "printenv OPENAI_API_KEY | codex login --with-api-key"]
        )
        if login.exit_code != 0:
            pytest.fail(f"Codex API login failed in E2E container (exit {login.exit_code}).")
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
        timeout=timeout,
    )
    assert rc == 0, f"omc configure failed in container:\n{out}"
    if provider == "codex":
        # Before the pins: their validation probe is a `codex exec` turn that
        # must already run under the container's approval/sandbox policy.
        set_codex_container_policy(container)
    model = _e2e_model_id(container, provider)
    # One configure run: seven leaves pinned to one full id cost one
    # validation probe, not seven (configure de-duplicates per run).
    pins = [
        arg
        for leaf in _TASK_MODEL_LEAVES
        for arg in ("--set", f"llm.providers.{provider}.{leaf}={model}")
    ]
    rc, out = run_in(container, ["omc", "configure", *pins], timeout=timeout)
    assert rc == 0, f"omc E2E model pin failed:\n{out}"
    return model


def make_work_repo(container, path="/work/repo", *, timeout: int = 600) -> str:
    """A throwaway git repo with an `origin` so wt + `git fetch origin` work.

    `.claude/settings.local.json` is ignored as in a real checkout: it is Claude
    Code's personal per-checkout file, and omc writes the native-notification
    channel into it for every worktree it launches."""
    script = (
        f"mkdir -p {path}-origin && cd {path}-origin && git init -q --bare && "
        f"cd / && git clone -q {path}-origin {path} && cd {path} && "
        "echo hi > README.md && echo .claude/settings.local.json > .gitignore && "
        "git add . && git commit -qm init && git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", script], timeout=timeout)
    assert rc == 0, f"work repo setup failed:\n{out}"
    return path


def _write_skill(container, repo, kind, name, description, body):
    script = (
        f"mkdir -p {repo}/.omc/{kind}/{name} && "
        f"cat > {repo}/.omc/{kind}/{name}/SKILL.md << 'SKILLEOF'\n"
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"{body}\n"
        "SKILLEOF"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def add_hook(container, repo, name, body):
    """Write the project hook `.omc/hooks/<name>/SKILL.md` (uncommitted)."""
    _write_skill(container, repo, "hooks", name, f"test project {name} hook", body)


def wire_mcp(container, provider: str, mode: str) -> None:
    """Wire the stub Jira MCP into the harness's config. mode: ok|auth-error|absent."""
    if mode == "absent":
        return
    if provider == "claude":
        jira_spec = {
            "type": "stdio",
            "command": "python3",
            "args": ["/repo/docker/stub-jira-mcp/server.py"],
            "env": {"STUB_JIRA_MODE": mode, "STUB_JIRA_MUTATIONS_LOG": MUTATIONS_LOG},
        }
        # Merge into ~/.claude.json rather than clobbering it — the file may
        # already carry other harness/session state we must not destroy.
        merge_script = f"""\
import json, os

path = os.path.expanduser("~/.claude.json")
os.makedirs(os.path.expanduser("~/.claude"), exist_ok=True)
try:
    with open(path) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {{}}
if not isinstance(data, dict):
    data = {{}}
data.setdefault("mcpServers", {{}})["jira"] = {json.dumps(jira_spec)}
with open(path, "w") as f:
    json.dump(data, f)
"""
        rc, out = run_in(container, ["python3", "-c", merge_script])
    elif provider == "codex":
        toml = (
            "[mcp_servers.jira]\n"
            'command = "python3"\n'
            'args = ["/repo/docker/stub-jira-mcp/server.py"]\n'
            f'env = {{ STUB_JIRA_MODE = "{mode}", '
            f'STUB_JIRA_MUTATIONS_LOG = "{MUTATIONS_LOG}" }}\n'
        )
        rc, out = run_in(
            container,
            [
                "bash",
                "-c",
                'codex_home="${CODEX_HOME:-$HOME/.codex}" && '
                'mkdir -p "$codex_home" && '
                f"cat >> \"$codex_home/config.toml\" <<'EOF'\n{toml}\nEOF",
            ],
        )
    else:
        raise ValueError(provider)
    assert rc == 0, f"MCP wiring failed for {provider}:\n{out}"


# --- stage hooks (design 6.2/6.3) ---


def hook_script(stage: str, body: str) -> str:
    """The conventional hook: `--show-instructions` prints its instructions file, else `body`.

    Runs in the container (and in its unit test on the host's PATH), so it calls
    `cat` and `dirname` by name, as a real project hook does."""
    return (
        "#!/bin/sh\n"
        'if [ "$#" -eq 1 ] && [ "$1" = "--show-instructions" ]; then\n'
        f'    exec cat "$(dirname "$0")/{stage}.instructions.md"\n'
        "fi\n"
        f"{body}\n"
    )


def hook_instructions(
    *, done: str, passed: str, fixing: str, timeout: str = "10m", quiet: str = "5m"
) -> str:
    """Instructions with the four §6.3 sections; validated by omc's own parser."""
    text = (
        f"## Limits\nTimeout: {timeout}\nQuiet timeout: {quiet}\n\n"
        f"## Done when\n{done}\n\n"
        f"## Passed when\n{passed}\n\n"
        f"## Fixing failures\n{fixing}\n"
    )
    parse_instructions(text)
    return text


_WRITE_HOOK = """\
import sys
from pathlib import Path

repo, stage, script, instructions = sys.argv[1:]
bindir = Path(repo) / ".omc" / "bin"
bindir.mkdir(parents=True, exist_ok=True)
(bindir / f"{stage}.instructions.md").write_text(instructions)
hook = bindir / stage
hook.write_text(script)
hook.chmod(0o755)
"""


def add_stage_hook(container, repo: str, stage: str, body: str, instructions: str) -> None:
    """Write `.omc/bin/<stage>` (executable), its instructions and the template; uncommitted."""
    parse_instructions(instructions)
    rc, out = run_in(
        container,
        ["python3", "-c", _WRITE_HOOK, repo, stage, hook_script(stage, body), instructions],
    )
    assert rc == 0, f"writing the {stage} hook failed:\n{out}"
    rc, out = run_in(
        container,
        ["bash", "-c", "omc internal stage template > .omc/bin/INSTRUCTIONS.template.md"],
        cwd=repo,
    )
    assert rc == 0, f"omc internal stage template failed:\n{out}"


# --- a watch for the container's primary checkout (design 6.4, A1) ---

STAND_IN_READY = "/tmp/omc-watch-stand-in.ready"
WATCH_PID = "/tmp/omc-watch.pid"
WATCH_LOG = "/tmp/omc-watch.log"

# argv[1] is the repo; `lock` is the alive lock in its git common dir, as watchlock resolves it.
_ALIVE_LOCK_PATH = f"""\
import fcntl, os, subprocess, sys
repo = sys.argv[1]
common = subprocess.run(
    ["git", "-C", repo, "rev-parse", "--git-common-dir"],
    check=True, capture_output=True, text=True,
).stdout.strip()
lock = os.path.join(repo, common, {ALIVE_LOCK!r})
"""

_PROBE = (
    _ALIVE_LOCK_PATH
    + """\
fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
except BlockingIOError:
    print("running")
else:
    print("not running")
"""
)


def watch_stand_in_script(ready: str = STAND_IN_READY) -> str:
    """Hold the alive lock as `omc watch` does, then write `ready` (its pid) and sleep.

    Its stderr goes to `<ready>.log`: a detached exec has no output to read."""
    return (
        f"import sys\nsys.stderr = open({f'{ready}.log'!r}, 'w', buffering=1)\n"
        + _ALIVE_LOCK_PATH
        + f"""\
import time
fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
with open({ready + ".tmp"!r}, "w") as fh:
    fh.write(str(os.getpid()))
os.replace({ready + ".tmp"!r}, {ready!r})
while True:
    time.sleep(3600)
"""
    )


def _watch_state(container, repo: str) -> str:
    rc, out = run_in(container, ["python3", "-c", _PROBE, repo])
    if rc != 0 or out.strip() not in ("running", "not running"):
        pytest.fail(f"the watch probe failed in {repo} (exit {rc}):\n{out}")
    return out.strip()


def hold_watch_stand_in(container, repo: str) -> None:
    """The lock-holding helper the unit tests use, in place of a real `omc watch`.

    A no-op while a watch (stand-in or real) already holds the lock: one per container."""
    if _watch_state(container, repo) == "running":
        return
    run_in(container, ["rm", "-f", STAND_IN_READY, f"{STAND_IN_READY}.log"])
    container.get_wrapped_container().exec_run(
        ["python3", "-c", watch_stand_in_script(), repo], detach=True
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        rc, _ = run_in(container, ["test", "-f", STAND_IN_READY])
        if rc == 0:
            return
        time.sleep(0.5)
    _, log = run_in(container, ["bash", "-c", f"cat {STAND_IN_READY}.log 2>&1"])
    pytest.fail(f"the watch stand-in did not take the alive lock in {repo} within 30 s:\n{log}")


def add_stage(container, repo: str, stage: str, body: str, passed: str) -> None:
    """A `.omc/bin/<stage>` hook running `body`, its instructions (`passed` is its
    `## Passed when` section), and a watch for the repo's stage runs; uncommitted."""
    instructions = hook_instructions(
        timeout="5m",
        quiet="2m",
        done=f"The hook exits once its {stage} command has run. Nothing is held.",
        passed=passed,
        fixing="Read the end of the log. Never edit `.omc/bin/*`: that is the stage itself.",
    )
    add_stage_hook(container, repo, stage, body, instructions)
    hold_watch_stand_in(container, repo)


def _watch_log_tail(container) -> str:
    _, tail = run_in(container, ["bash", "-c", f"tail -n 40 {WATCH_LOG} 2>&1"])
    return tail


def start_real_watch(container, repo: str, *, within: float = 240) -> None:
    """Start `omc watch` in the primary checkout, detached; return once it holds the alive
    lock. It takes the lock at boot, before its first tick; `within` bounds the wait."""
    command = (
        f"cd {shlex.quote(repo)} && echo $$ > {WATCH_PID} && "
        f"exec omc watch --interval 30 > {WATCH_LOG} 2>&1"
    )
    run_in(container, ["rm", "-f", WATCH_PID])
    container.get_wrapped_container().exec_run(["bash", "-lc", command], detach=True)
    deadline = time.monotonic() + within
    while True:
        if _watch_state(container, repo) == "running":
            return
        rc, _ = run_in(
            container, ["bash", "-c", f'test -s {WATCH_PID} && ! kill -0 "$(cat {WATCH_PID})"']
        )
        if rc == 0:
            pytest.fail(
                f"omc watch exited before it held the alive lock:\n{_watch_log_tail(container)}"
            )
        if time.monotonic() >= deadline:
            pytest.fail(
                f"omc watch did not hold the alive lock within {within:g} s:\n"
                f"{_watch_log_tail(container)}"
            )
        time.sleep(2)


def stop_real_watch(container, repo: str, *, within: float = 30) -> None:
    """Ctrl-C the watch start_real_watch started; return once its alive lock is free."""
    rc, out = run_in(container, ["bash", "-c", f'kill -INT "$(cat {WATCH_PID})"'])
    if rc != 0:
        pytest.fail(f"could not signal omc watch: {out}\n{_watch_log_tail(container)}")
    deadline = time.monotonic() + within
    while _watch_state(container, repo) == "running":
        if time.monotonic() >= deadline:
            pytest.fail(
                f"omc watch still held the alive lock {within:g} s after SIGINT:\n"
                f"{_watch_log_tail(container)}"
            )
        time.sleep(1)


# The Codex long-poll E2E (test_e2e_stage_poll) waits with this command.
STAGE_WAIT = "omc internal stage wait check"

# Counts the tool calls in one Codex session's rollout (under CODEX_HOME) whose
# command contains argv[2]. The conversation server's sanitized events drop the
# arguments, so the rollout on disk is the artifact. The session's own file is
# picked by its name or its first line (session_meta) before anything is parsed.
COUNT_CALLS = """\
import json, os, pathlib, sys
session, needle = sys.argv[1:]
home = pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex")


def is_session(path):
    if session in path.name:
        return True
    with path.open() as fh:
        first = json.loads(fh.readline() or "{}")
    return first.get("type") == "session_meta" and first.get("payload", {}).get("id") == session


paths = [path for path in sorted((home / "sessions").rglob("*.jsonl")) if is_session(path)]
if len(paths) != 1:
    sys.exit(f"expected one rollout for session {session}, found {paths}")
count = 0
for line in paths[0].read_text().splitlines():
    record = json.loads(line) if line.strip() else {}
    payload = record.get("payload")
    if record.get("type") != "response_item" or not isinstance(payload, dict):
        continue
    if payload.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
        call = {k: payload.get(k) for k in ("arguments", "input", "action")}
        if needle in json.dumps(call):
            count += 1
print(count)
"""
