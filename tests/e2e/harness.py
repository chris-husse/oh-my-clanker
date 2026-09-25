"""Container exec helpers. Every E2E test drives a FRESH container; the
container is the sandbox. Tokens are passed from the host env; a missing token
FAILS the test with guidance — it never skips."""

from __future__ import annotations

import json
import os
import shlex

import pytest

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


def run_in(container, argv, *, env=None, cwd=None, timeout=600):
    """Exec argv in the container; returns (rc, combined-output).

    A provider auth failure anywhere in that output fails the test right here,
    with the remediation — otherwise it resurfaces later as a confusing
    assertion about missing text, blamed on whichever test ran first.
    """
    cmd = shlex.join(argv)
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    wrapped = ["timeout", str(timeout), "bash", "-lc", cmd]
    envs = {k: v for k, v in (env or {}).items()}
    result = container.get_wrapped_container().exec_run(wrapped, environment=envs or None)
    output = result.output.decode(errors="replace")
    remediation = detect_auth_failure(output)
    if remediation is not None:
        pytest.fail(f"provider auth failed in container: {remediation}\n\n{output}")
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


def configure_omc(container, provider: str) -> None:
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
    rc, out = run_in(
        container,
        ["omc", "configure", "--set", f"llm.default={provider}"],
    )
    assert rc == 0, f"omc configure failed in container:\n{out}"
    if provider == "codex":
        set_codex_container_policy(container)


def make_work_repo(container, path="/work/repo") -> str:
    """A throwaway git repo with an `origin` so wt + `git fetch origin` work."""
    script = (
        f"mkdir -p {path}-origin && cd {path}-origin && git init -q --bare && "
        f"cd / && git clone -q {path}-origin {path} && cd {path} && "
        "echo hi > README.md && git add . && git commit -qm init && git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, f"work repo setup failed:\n{out}"
    return path


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
