"""Codex E2E shell policy is confined to the selected disposable home."""

from __future__ import annotations

import subprocess
import sys
import tomllib

import pytest

from tests.e2e import harness


@pytest.mark.parametrize("custom_home", [False, True], ids=["default-home", "codex-home"])
def test_codex_container_policy_is_idempotent_and_preserves_config(
    tmp_path, monkeypatch, custom_home
):
    home = tmp_path / "disposable home"
    codex_home = tmp_path / "custom codex home" if custom_home else home / ".codex"
    codex_home.mkdir(parents=True)
    config = codex_home / "config.toml"
    config.write_text(
        'model = "existing-model"\n'
        '[plugins."omc@oh-my-clanker"]\n'
        "enabled = true\n"
        '[mcp_servers.jira]\ncommand = "python3"\n'
    )
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if custom_home:
        env["CODEX_HOME"] = str(codex_home)

    def local_run(_container, argv):
        result = subprocess.run(
            [sys.executable, *argv[1:]], capture_output=True, text=True, env=env, check=False
        )
        return result.returncode, result.stdout + result.stderr

    monkeypatch.setattr(harness, "run_in", local_run)
    harness.set_codex_container_policy(object())
    harness.set_codex_container_policy(object(), reasoning_effort="high")
    once = config.read_text()
    harness.set_codex_container_policy(object(), reasoning_effort="high")
    assert config.read_text() == once
    parsed = tomllib.loads(once)
    assert parsed["approval_policy"] == "never"
    assert parsed["sandbox_mode"] == "danger-full-access"
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["model"] == "existing-model"
    assert parsed["plugins"]["omc@oh-my-clanker"] == {"enabled": True}
    assert parsed["mcp_servers"]["jira"] == {"command": "python3"}
    if custom_home:
        assert not (home / ".codex" / "config.toml").exists()


def test_codex_container_policy_rejects_malformed_config_without_overwriting(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex home"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    original = '[plugins."broken"\n'
    config.write_text(original)

    def local_run(_container, argv):
        result = subprocess.run(
            [sys.executable, *argv[1:]],
            capture_output=True,
            text=True,
            env={"HOME": str(tmp_path), "CODEX_HOME": str(codex_home), "PATH": "/usr/bin:/bin"},
            check=False,
        )
        return result.returncode, result.stdout + result.stderr

    monkeypatch.setattr(harness, "run_in", local_run)
    with pytest.raises(AssertionError, match="Codex E2E container policy"):
        harness.set_codex_container_policy(object())
    assert config.read_text() == original
