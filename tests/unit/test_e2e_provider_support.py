"""The E2E environment provisions and exercises only supported providers."""

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e import harness, judge, test_e2e_smoke

ROOT = Path(__file__).resolve().parents[2]


def test_provider_matrix_collects_claude_and_codex_cases():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/test_e2e_slug_matrix.py",
            "tests/e2e/test_e2e_start.py",
            "--collect-only",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    collected = {
        line.split("::", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("tests/e2e/") and "[" in line
    }
    assert collected == {
        f"{name}[{provider}]"
        for name in (
            "test_slug_ok",
            "test_slug_mcp_unauthenticated",
            "test_slug_mcp_missing",
            "test_slug_free_text_description_needs_no_tracker",
            "test_start_headless_creates_worktree_and_seeds",
        )
        for provider in ("claude", "codex")
    }


def test_plugin_bootstrap_rejects_unknown_provider_before_running_tools(tmp_path):
    script = ROOT / "docker" / "setup-plugins.sh"
    result = subprocess.run(
        ["/bin/bash", str(script), "unsupported"],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "unknown provider: unsupported"


def test_docker_provider_install_invokes_only_supported_packages(tmp_path):
    recipe = next(
        line.removeprefix("RUN ")
        for line in (ROOT / "docker" / "Dockerfile.e2e").read_text().splitlines()
        if line.startswith("RUN npm install ")
    )
    calls = tmp_path / "npm-args"
    npm = tmp_path / "npm"
    npm.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$OMC_NPM_ARGS"\n')
    npm.chmod(0o755)
    subprocess.run(
        ["/bin/sh", "-c", recipe],
        env={
            "PATH": str(tmp_path),
            "CODEX_VERSION": "test-version",
            "OMC_NPM_ARGS": str(calls),
        },
        check=True,
    )
    assert calls.read_text().splitlines() == [
        "install",
        "-g",
        "@anthropic-ai/claude-code",
        "@openai/codex@test-version",
    ]


def test_smoke_checks_supported_toolchain_only(monkeypatch):
    calls = []

    def run_in(_container, argv):
        calls.append(shlex.join(argv))
        return 0, "version"

    monkeypatch.setattr(test_e2e_smoke, "run_in", run_in)
    test_e2e_smoke.test_container_toolchain(object())
    assert calls == [
        "git --version",
        "wt --version",
        "omc version",
        "claude --version",
        "codex --version",
    ]


def test_unsupported_provider_cannot_launch_judge(monkeypatch):
    def unexpected_launch(*args, **kwargs):
        pytest.fail("unsupported provider must not launch a judge")

    monkeypatch.setattr(judge, "run_in", unexpected_launch)
    with pytest.raises(KeyError):
        judge.judge(object(), "unsupported", "scenario", ["rubric"], "artifact")


def test_unsupported_provider_cannot_write_mcp_config(monkeypatch):
    def unexpected_write(*args, **kwargs):
        pytest.fail("unsupported provider must not write MCP configuration")

    monkeypatch.setattr(harness, "run_in", unexpected_write)
    with pytest.raises(ValueError, match="unsupported"):
        harness.wire_mcp(object(), "unsupported", "ok")
