"""Container exec helpers. Every E2E test drives a FRESH container; the
container is the sandbox. Tokens are passed from the host env; a missing token
FAILS the test with guidance — it never skips."""

from __future__ import annotations

import json
import os
import shlex

import pytest

TOKEN_ENV = {
    "claude": "CLAUDE_CODE_OAUTH_TOKEN",
    "codex": "OPENAI_API_KEY",
    "opencode": "ANTHROPIC_API_KEY",
}

_TOKEN_GUIDANCE = {
    "claude": "run `claude setup-token` and put the token in .env (cp env.example .env)",
    "codex": "put an OPENAI_API_KEY (platform.openai.com) in .env (cp env.example .env)",
    "opencode": "put an ANTHROPIC_API_KEY (console.anthropic.com) in .env (cp env.example .env)",
}

PROVIDERS = list(TOKEN_ENV)


def require_token(provider: str) -> None:
    var = TOKEN_ENV[provider]
    if not os.environ.get(var):
        pytest.fail(f"live {provider} E2E needs ${var} — {_TOKEN_GUIDANCE[provider]}; then re-run.")


def run_in(container, argv, *, env=None, cwd=None, timeout=600):
    """Exec argv in the container; returns (rc, combined-output)."""
    cmd = shlex.join(argv)
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    wrapped = ["timeout", str(timeout), "bash", "-lc", cmd]
    envs = {k: v for k, v in (env or {}).items()}
    result = container.get_wrapped_container().exec_run(wrapped, environment=envs or None)
    return result.exit_code, result.output.decode(errors="replace")


def configure_omc(container, provider: str) -> None:
    rc, out = run_in(
        container,
        ["omc", "configure", "--set", f"llm.default={provider}"],
    )
    assert rc == 0, f"omc configure failed in container:\n{out}"


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
    stub_env = f"STUB_JIRA_MODE={mode}"
    if provider == "claude":
        jira_spec = {
            "type": "stdio",
            "command": "python3",
            "args": ["/repo/docker/stub-jira-mcp/server.py"],
            "env": {"STUB_JIRA_MODE": mode},
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
            f'env = {{ STUB_JIRA_MODE = "{mode}" }}\n'
        )
        rc, out = run_in(
            container,
            [
                "bash",
                "-c",
                f"mkdir -p ~/.codex && cat >> ~/.codex/config.toml <<'EOF'\n{toml}\nEOF",
            ],
        )
    elif provider == "opencode":
        spec = {
            "mcp": {
                "jira": {
                    "type": "local",
                    "command": ["env", stub_env, "python3", "/repo/docker/stub-jira-mcp/server.py"],
                    "enabled": True,
                }
            }
        }
        rc, out = run_in(
            container,
            [
                "bash",
                "-c",
                "mkdir -p ~/.config/opencode && "
                f"cat > ~/.config/opencode/opencode.json <<'EOF'\n{json.dumps(spec)}\nEOF",
            ],
        )
    else:
        raise ValueError(provider)
    assert rc == 0, f"MCP wiring failed for {provider}:\n{out}"
