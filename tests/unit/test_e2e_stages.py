import json
from types import SimpleNamespace

import pytest

from tests.e2e import stages


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


def test_snapshot_writes_manifest_then_commits(monkeypatch):
    seen = []
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="sha256:x")

    monkeypatch.setattr(stages.subprocess, "run", fake_run)
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="cid"))
    tag = stages.snapshot(container, {"provider": "claude", "stage": "agreed", "slug": "s"})
    assert tag == "omc-e2e-stage:claude-agreed-deadbee"
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
