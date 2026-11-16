import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.e2e import stages

from . import _stagekit


def test_source_id_is_the_tree_content_hash(monkeypatch):
    monkeypatch.delenv("OMC_E2E_SOURCE_ID", raising=False)
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs["env"]["GIT_INDEX_FILE"]))
        return SimpleNamespace(returncode=0)

    def fake_output(argv, **kwargs):
        seen.append((argv, kwargs["env"]["GIT_INDEX_FILE"]))
        return "abcdef0123456789abcdef0123456789abcdef01\n"

    monkeypatch.setattr(stages.subprocess, "run", fake_run)
    monkeypatch.setattr(stages.subprocess, "check_output", fake_output)
    assert stages.source_id() == "abcdef012345"
    # staged into a TEMPORARY index (never the real one), then hashed
    assert seen[0][0] == ["git", "add", "-A"] and seen[1][0] == ["git", "write-tree"]
    assert seen[0][1] == seen[1][1] and "omc-e2e-index-" in seen[0][1]


def test_source_id_prefers_runner_env(monkeypatch):
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")
    assert stages.stage_tag("claude", "agreed") == "omc-e2e-stage:claude-agreed-deadbee"


def _fake_docker(seen, live="[]"):
    def fake_run(argv, **kwargs):
        seen.append(argv)
        out = live if stages.LIVE_RUNS_PY in argv else "sha256:x"
        return SimpleNamespace(returncode=0, stdout=out)

    return fake_run


def test_snapshot_writes_manifest_then_commits(monkeypatch):
    seen = []
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")
    monkeypatch.setattr(stages.subprocess, "run", _fake_docker(seen))
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="cid"))
    manifest = {"provider": "claude", "stage": "agreed", "slug": "s", "repo": "/work/repo"}
    tag = stages.snapshot(container, manifest)
    assert tag == "omc-e2e-stage:claude-agreed-deadbee"
    # first: no stage run is live in the repo or its worktrees
    assert seen[0] == ["docker", "exec", "cid", "python3", "-c", stages.LIVE_RUNS_PY, "/work/repo"]
    seen.pop(0)
    assert seen[0][:3] == ["docker", "exec", "cid"] and stages.MANIFEST in seen[0]
    assert json.loads(seen[0][-1])["stage"] == "agreed"
    assert seen[1][:2] == ["docker", "commit"] and seen[1][-2:] == ["cid", tag]
    # Ryuk must not reap the snapshot: the session label is overridden on commit.
    assert "LABEL org.testcontainers.session-id=omc-stage-snapshot" in seen[1]


def test_read_manifest_parses_the_in_image_file(monkeypatch):
    monkeypatch.setattr(
        stages.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=0, stdout='{"slug": "greet-fix"}'),
    )
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="cid"))
    assert stages.read_manifest(container) == {"slug": "greet-fix"}


def test_stage_image_fails_loud_when_absent(monkeypatch):
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")
    monkeypatch.setattr(stages.subprocess, "run", lambda argv, **k: SimpleNamespace(returncode=1))
    with pytest.raises(pytest.fail.Exception, match="run `just golden` first"):
        stages.stage_image("claude", "agreed")


def test_snapshot_refuses_a_live_stage_run(monkeypatch):
    """A golden snapshot is taken only after stage runs have ended: an image with a
    `running` or `held` record would fork variations with a recorded group that is gone."""
    seen = []
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")
    live = json.dumps(["/work/wt/.cache/omc/run.json: held"])
    monkeypatch.setattr(stages.subprocess, "run", _fake_docker(seen, live))
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="cid"))
    manifest = {"provider": "claude", "stage": "s", "slug": "s", "repo": "/work/repo"}
    with pytest.raises(AssertionError, match=r"/work/wt/\.cache/omc/run\.json: held"):
        stages.snapshot(container, manifest)
    assert len(seen) == 1  # no manifest, no commit


def test_live_runs_script_reads_the_repo_and_its_worktrees(tmp_path):
    """The in-container check, run here on a real repo with a linked worktree."""
    repo = _stagekit.git_repo(tmp_path / "repo")
    other = _stagekit.add_worktree(repo, tmp_path / "wt", "b")
    third = _stagekit.add_worktree(repo, tmp_path / "wt3", "c")
    for root, state in ((repo, "passed"), (other, "held"), (third, "running")):
        omc = root / ".cache" / "omc"
        omc.mkdir(parents=True)
        (omc / "run.json").write_text(json.dumps({"state": state}) + "\n")
    out = subprocess.run(
        [sys.executable, "-I", "-c", stages.LIVE_RUNS_PY, str(repo)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert json.loads(out) == [
        f"{other / '.cache' / 'omc' / 'run.json'}: held",
        f"{third / '.cache' / 'omc' / 'run.json'}: running",
    ]
    (other / ".cache" / "omc" / "run.json").write_text("{}\n")
    (third / ".cache" / "omc" / "run.json").write_text("not json\n")
    out = subprocess.run(
        [sys.executable, "-I", "-c", stages.LIVE_RUNS_PY, str(repo)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert json.loads(out) == []
