"""Run the lifecycle fixture's generated stage hooks, and the watch stand-in, locally."""

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from omc import watchlock
from omc.stagedef import parse_instructions
from tests.e2e import harness, lifecycle_helpers

from . import _mutexproc, _stagekit
from ._stubs import host_bin

ROOT = Path(__file__).resolve().parents[2]


def _local_repo(_container, path):
    """make_work_repo, locally: a repo on `main` with a bare origin beside it."""
    repo = _stagekit.git_repo(Path(path))
    origin = f"{path}-origin"
    env = _stagekit.hermetic_env()
    for argv, cwd in (
        ([host_bin("git"), "init", "-q", "--bare", origin], repo),
        ([host_bin("git"), "remote", "add", "origin", origin], repo),
        ([host_bin("git"), "push", "-q", "origin", "main"], repo),
    ):
        subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True)
    return path


def _local_transport(_container, argv, *, cwd=None, **_kwargs):
    """Run the fixture's python3 and bash scripts here; every other container call is a no-op."""
    if argv[:2] == ["bash", "-c"] and "omc internal stage template" in argv[2]:
        return 0, ""
    if argv[:2] in (["python3", "-c"], ["bash", "-c"]):
        result = subprocess.run(
            argv, cwd=cwd, env=_stagekit.hermetic_env(), capture_output=True, text=True
        )
        return result.returncode, result.stdout + result.stderr
    return 0, ""


@pytest.fixture
def local_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle_helpers, "make_work_repo", _local_repo)
    monkeypatch.setattr(lifecycle_helpers, "run_in", _local_transport)

    def build(**kwargs) -> Path:
        path = str(tmp_path / "repo")
        return Path(lifecycle_helpers._fixture(None, path=path, watch="none", **kwargs))

    return build


def _env(tmp_path, **extra):
    return {
        **_stagekit.hermetic_env(),
        "OMC_LIFECYCLE_MARKER": str(tmp_path / "stage-markers"),
        "OMC_EXTERNAL_VERIFY_SENTINEL": str(tmp_path / "verify-unavailable"),
        **extra,
    }


def test_fixture_hooks_are_valid_and_runnable(tmp_path, local_fixture):
    repo = local_fixture()
    env = _env(tmp_path)
    for stage in ("check", "build", "verify"):
        hook = repo / ".omc" / "bin" / stage
        shown = subprocess.run(
            [str(hook), "--show-instructions"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert shown.returncode == 0, shown.stderr
        assert parse_instructions(shown.stdout).limits.timeout_s is not None
        ran = subprocess.run([str(hook)], cwd=repo, env=env, capture_output=True, text=True)
        assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (tmp_path / "stage-markers").read_text().splitlines() == ["check", "build", "verify"]
    # review stays a skill over its own script; the three hook stages have no skill
    assert (repo / ".omc" / "skills" / "review" / "SKILL.md").is_file()
    assert (repo / ".omc" / "stage-scripts" / "review.sh").is_file()
    for stage in ("check", "build", "verify"):
        assert not (repo / ".omc" / "skills" / stage).exists()
    status = subprocess.run(
        [host_bin("git"), "status", "--porcelain"],
        cwd=repo,
        env=_stagekit.hermetic_env(),
        capture_output=True,
        text=True,
    )
    assert status.stdout == "", f"the fixture left files uncommitted: {status.stdout}"


def test_fixture_ships_this_repos_needs_verify_hook(local_fixture):
    repo = local_fixture()
    shipped = repo / ".omc/hooks/needs_verify/SKILL.md"
    assert shipped.is_file(), "the lifecycle fixture ships no needs_verify hook"
    assert shipped.read_bytes() == (ROOT / ".omc/hooks/needs_verify/SKILL.md").read_bytes()


def test_verify_stage_sentinel_reports_unavailable_environment(tmp_path, local_fixture):
    repo = local_fixture(failing_stage="verify")
    hook = repo / ".omc" / "bin" / "verify"
    env = _env(tmp_path)
    assert subprocess.run([str(hook)], cwd=repo, env=env, capture_output=True).returncode == 0

    (tmp_path / "verify-unavailable").touch()
    failed = subprocess.run([str(hook)], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "E2E environment unavailable" in failed.stderr
    assert (tmp_path / "stage-markers").read_text().splitlines() == ["verify", "verify"]


def test_watch_stand_in_script_holds_alive_lock(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    ready = tmp_path / "stand-in.ready"
    lock = repo / ".git" / watchlock.ALIVE_LOCK
    proc = subprocess.Popen(
        [sys.executable, "-c", harness.watch_stand_in_script(str(ready)), str(repo)],
        env=_stagekit.hermetic_env(),
    )
    try:
        _mutexproc.wait_for(ready.exists)
        assert ready.read_text() == str(proc.pid)
        assert watchlock.lock_held(lock) is True
        assert _probe(repo) == "running"
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    assert watchlock.lock_held(lock) is False
    assert _probe(repo) == "not running"


def _probe(repo: Path) -> str:
    """The harness's container probe, run here: what start/stop_real_watch poll."""
    done = subprocess.run(
        [sys.executable, "-c", harness._PROBE, str(repo)],
        env=_stagekit.hermetic_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()
