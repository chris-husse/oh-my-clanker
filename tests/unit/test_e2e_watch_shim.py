"""The watch E2E build shim must leave Claude's other CLI contracts intact."""

import json
import subprocess

import pytest

from tests.e2e import test_e2e_watch


def test_build_shim_delegates_auth_and_model_turns_but_intercepts_build(tmp_path):
    real = tmp_path / "real-claude"
    real.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        "  'auth status') echo '{\"loggedIn\": true}';;\n"
        "  *) echo 'real model turn';;\n"
        "esac\n"
    )
    real.chmod(0o755)
    shim = tmp_path / "claude"
    shim.write_text(test_e2e_watch._claude_build_shim(str(real)))
    shim.chmod(0o755)

    def run(*args: str) -> str:
        cp = subprocess.run([str(shim), *args], capture_output=True, text=True, check=True)
        return cp.stdout.strip()

    assert json.loads(run("auth", "status")) == {"loggedIn": True}
    assert run("-p", "refresh models", "--output-format", "text") == "real model turn"
    assert run("-p", "build stage", "--output-format", "stream-json") == (
        'OMC_STAGE {"stage": "build", "configured": true, "passed": true, "summary": "ok"}'
    )


def test_watch_build_e2e_reports_missing_claude_token_before_container_setup(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(pytest.fail.Exception, match="live claude E2E needs"):
        test_e2e_watch.test_watch_auto_build_runs_stage_via_shim_provider(None)
