"""Exercise generated fish integration in a real shell and repository."""

import os
import shlex
import shutil
import subprocess
import sys

import pytest

from omc.fish_integration import fish_hook_path
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
    scrub = (
        "ITERM_SESSION_ID", "TERM_PROGRAM", "LC_TERMINAL", "TMUX", "STY", "OMC_FISH_TITLE_DISABLE",
        "XDG_CACHE_HOME", "XDG_DATA_HOME",
    )  # fmt: skip
    base = {k: v for k, v in os.environ.items() if k not in scrub}
    # Pre-created so fish's interactive init never runs a bare `mkdir` on a restricted PATH
    # (the double-sourcing case below runs with PATH = stub dir).
    for base_dir in ("cache", "data"):
        (tmp_path / base_dir / "fish" / "generated_completions").mkdir(parents=True)
    env = {
        **base,
        "OMC_TITLE_LOG": str(log),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }

    def run(extra="", *, title_argv=None, startup=None, env_extra=None):
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
            argv,
            cwd=tmp_path,
            env={**env, **(env_extra or {})},
            capture_output=True,
            text=True,
            timeout=25,
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


def test_generated_start_in_iterm2_dispatches_exactly_one_set_despite_double_sourcing(
    fish_session, tmp_path, monkeypatch
):
    repo, run = fish_session
    # The builder embeds sys.executable as the hook's helper; point it at a recorder that
    # accepts `-m omc title apply <file>` so no real SDK worker can run.
    fake_python = tmp_path / "fake python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$OMC_TITLE_LOG"\n'
        'if [ "$4" = apply ] && [ -r "$5" ]; then IFS= read -r l < "$5"; '
        'printf \'request: %s\\n\' "$l" >> "$OMC_TITLE_LOG"; fi\n'
        "exit 0\n"
    )
    fake_python.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake_python))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "git").symlink_to(shutil.which("git"))
    (stubs / "omc").write_text(
        '#!/bin/sh\nprintf \'PATH-OMC %s\\n\' "$*" >> "$OMC_TITLE_LOG"\nexit 0\n'
    )
    (stubs / "omc").chmod(0o755)
    confd = tmp_path / "config" / "fish" / "conf.d"
    confd.mkdir(parents=True)
    shutil.copyfile(fish_hook_path(), confd / "omc-title.fish")  # conf.d copy: the first sourcing
    wait = (
        "set -l t 0; while test $t -lt 200; "
        "and test (count (string match -r '^request:' <$OMC_TITLE_LOG)) -lt 1; "
        "set t (math $t + 1); /bin/sleep 0.05; end"
    )
    proc, lines = run(
        f"emit fish_prompt; emit fish_prompt; {wait}; /bin/sleep 0.2",
        env_extra={
            "ITERM_SESSION_ID": "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632",
            "TERM_PROGRAM": "iTerm.app",
            "PATH": str(stubs),
            "OMC_HOME": str(tmp_path / "omc-home"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "USER_CONFIG" in lines and "STARTUP" in lines
    assert len([ln for ln in lines if ln.startswith("-m omc title apply ")]) == 1
    assert [ln for ln in lines if ln.startswith("request: ")] == ["request: set feature/first"]
    # the preset helper is never overridden
    assert not any(ln.startswith("PATH-OMC") for ln in lines)
    assert "feature/first" not in lines  # the inline recorder (else branch) did not run


def test_generated_start_with_session_disable_uses_inline_path(fish_session):
    _, run = fish_session
    proc, lines = run(
        env_extra={
            "ITERM_SESSION_ID": "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632",
            "TERM_PROGRAM": "iTerm.app",
            "OMC_FISH_TITLE_DISABLE": "1",
        }
    )
    assert proc.returncode == 0, proc.stderr
    assert lines == ["USER_CONFIG", "feature/first", "STARTUP"]
