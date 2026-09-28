"""Exercise generated fish integration in a real shell and repository."""

import os
import shlex
import shutil
import subprocess

import pytest

from omc.shells.fish import FishShell


@pytest.fixture
def fish_session(tmp_path):
    fish = shutil.which("fish")
    if fish is None:
        pytest.fail(
            "fish is required for shell integration tests: "
            "brew install fish (macOS) or sudo apt-get install fish (Ubuntu)"
        )
    repo = tmp_path / "repo's space"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "file").write_text("initial\n")
    subprocess.run(["git", "-C", str(repo), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    subprocess.run(["git", "-C", str(repo), "switch", "-qc", "feature/first"], check=True)

    recorder = tmp_path / "title recorder's helper"
    recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$OMC_TITLE_LOG"\n')
    recorder.chmod(0o755)
    config = tmp_path / "config" / "fish"
    config.mkdir(parents=True)
    (config / "config.fish").write_text('echo USER_CONFIG >> "$OMC_TITLE_LOG"\n')
    log = tmp_path / "titles.log"
    env = {**os.environ, "OMC_TITLE_LOG": str(log), "XDG_CONFIG_HOME": str(tmp_path / "config")}

    def run(extra="", *, title_argv=None, startup=None):
        argv, files = FishShell().build_invocation(
            cwd=str(repo),
            title="feature/first",
            startup_argv=startup or ["/bin/sh", "-c", 'echo STARTUP >> "$OMC_TITLE_LOG"'],
            title_seq="\033]0;feature/first\007",
            title_argv=[str(recorder)] if title_argv is None else title_argv,
        )
        assert not files
        argv[0] = fish
        argv[3] += "; " + extra + "; exit"
        proc = subprocess.run(
            argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15
        )
        return proc, log.read_text().splitlines() if log.exists() else []

    return repo, run


def test_initial_title_precedes_startup_and_user_config_runs(fish_session):
    _, run = fish_session
    proc, lines = run()
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP"]


def test_branch_and_tag_with_same_name_keep_literal_branch_title(fish_session):
    repo, run = fish_session
    subprocess.run(
        ["git", "-C", str(repo), "-c", "tag.gpgSign=false", "tag", "-am", "test", "feature/first"],
        check=True,
    )
    proc, lines = run("emit fish_prompt; fish_title")
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP"]
    assert "feature/first" in proc.stdout


def test_branch_switch_and_duplicate_lifecycle_events(fish_session):
    _, run = fish_session
    proc, lines = run(
        "git switch -qc feature/second; emit fish_preexec; emit fish_prompt; emit fish_prompt; "
        "fish_title"
    )
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP", "feature/second"]
    assert "feature/second" in proc.stdout


def test_detached_and_outside_repo_keep_last_title(fish_session):
    _, run = fish_session
    proc, lines = run(
        "git switch --detach -q; emit fish_prompt; cd /; emit fish_prompt; fish_title"
    )
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP"]
    assert "feature/first" in proc.stdout


def test_literal_special_branch_and_directory(fish_session):
    _, run = fish_session
    branch = "feature/cost$USD(parent)"
    proc, lines = run("git switch -qc 'feature/cost$USD(parent)'; emit fish_prompt; fish_title")
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP", branch]
    assert branch in proc.stdout


def test_directory_change_refreshes_to_other_repository_branch(fish_session):
    repo, run = fish_session
    other = repo.parent / "other repo's space"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(other), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(other), "config", "commit.gpgsign", "false"], check=True)
    (other / "file").write_text("other\n")
    subprocess.run(["git", "-C", str(other), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-qm", "other"], check=True)
    subprocess.run(["git", "-C", str(other), "switch", "-qc", "feature/elsewhere"], check=True)
    proc, lines = run(f"cd {shlex.quote(str(other))}; fish_title")
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP", "feature/elsewhere"]
    assert "feature/elsewhere" in proc.stdout


def test_failed_title_operation_deduplicates_and_keeps_startup(fish_session):
    repo, run = fish_session
    failure = repo.parent / "failed helper"
    failure.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$OMC_TITLE_LOG"\nexit 1\n')
    failure.chmod(0o755)
    proc, lines = run(
        "emit fish_prompt; emit fish_preexec; git switch -qc feature/second; emit fish_prompt",
        title_argv=[str(failure)],
    )
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP", "feature/second"]


def test_refresh_preserves_previous_command_status(fish_session):
    _, run = fish_session
    proc, lines = run("false; __omc_refresh_title; echo STATUS:$status")
    assert proc.returncode == 0, proc.stderr
    assert "STATUS:1" in proc.stdout
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP"]
