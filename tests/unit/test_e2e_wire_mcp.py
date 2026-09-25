"""The E2E Jira fixture writes to the Codex home used by the CLI."""

from __future__ import annotations

import subprocess
import tomllib

import pytest

from tests.e2e import harness


@pytest.mark.parametrize("mode", ["ok", "auth-error"])
@pytest.mark.parametrize("custom_home", [False, True], ids=["default-home", "codex-home"])
def test_wire_mcp_uses_selected_codex_home_and_preserves_config(
    tmp_path, monkeypatch, mode, custom_home
):
    home = tmp_path / "disposable home"
    codex_home = tmp_path / "custom codex home" if custom_home else home / ".codex"
    codex_home.mkdir(parents=True)
    config = codex_home / "config.toml"
    config.write_text(
        'model = "existing-model"\n'
        '[plugins."omc@oh-my-clanker"]\n'
        "enabled = true\n"
        '[plugins."superpowers@superpowers-marketplace"]\n'
        "enabled = true\n"
    )

    # Run the command emitted by wire_mcp under a disposable home, without
    # exposing the host's Codex settings or credentials to the subprocess.
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if custom_home:
        env["CODEX_HOME"] = str(codex_home)

    def local_run(_container, argv):
        result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
        return result.returncode, result.stdout + result.stderr

    monkeypatch.setattr(harness, "run_in", local_run)
    harness.wire_mcp(object(), "codex", mode)

    parsed = tomllib.loads(config.read_text())
    assert parsed["model"] == "existing-model"
    assert parsed["plugins"] == {
        "omc@oh-my-clanker": {"enabled": True},
        "superpowers@superpowers-marketplace": {"enabled": True},
    }
    assert parsed["mcp_servers"]["jira"] == {
        "command": "python3",
        "args": ["/repo/docker/stub-jira-mcp/server.py"],
        "env": {
            "STUB_JIRA_MODE": mode,
            "STUB_JIRA_MUTATIONS_LOG": "/tmp/stub-jira-mutations.jsonl",
        },
    }
    if custom_home:
        assert not (home / ".codex" / "config.toml").exists()
