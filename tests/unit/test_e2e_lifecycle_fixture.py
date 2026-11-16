"""Run the lifecycle fixture's generated files without a model actor."""

import os
import subprocess
from pathlib import Path

from tests.e2e import lifecycle_helpers

ROOT = Path(__file__).resolve().parents[2]


def _local_transport(monkeypatch):
    """Run the fixture's python payload on the host; every other step is a no-op."""
    monkeypatch.setattr(lifecycle_helpers, "make_work_repo", lambda _container, path: path)

    def local_transport(_container, argv, **_kwargs):
        if argv[:2] == ["python3", "-c"]:
            result = subprocess.run(argv, capture_output=True, text=True)
            return result.returncode, result.stdout + result.stderr
        return 0, ""

    monkeypatch.setattr(lifecycle_helpers, "run_in", local_transport)


def test_fixture_ships_this_repos_needs_verify_hook(tmp_path, monkeypatch):
    _local_transport(monkeypatch)
    repo = Path(lifecycle_helpers._fixture(None, path=str(tmp_path)))
    shipped = repo / ".omc/hooks/needs_verify/SKILL.md"
    assert shipped.is_file(), "the lifecycle fixture ships no needs_verify hook"
    assert shipped.read_bytes() == (ROOT / ".omc/hooks/needs_verify/SKILL.md").read_bytes()


def test_verify_stage_sentinel_reports_unavailable_environment(tmp_path, monkeypatch):
    _local_transport(monkeypatch)
    repo = Path(lifecycle_helpers._fixture(None, failing_stage="verify", path=str(tmp_path)))
    script = repo / ".omc/stage-scripts/verify.sh"
    sentinel = tmp_path / "verify-unavailable"
    marker = tmp_path / "stage-markers"
    env = {
        **os.environ,
        "OMC_EXTERNAL_VERIFY_SENTINEL": str(sentinel),
        "OMC_LIFECYCLE_MARKER": str(marker),
    }
    assert (
        subprocess.run(["sh", str(script)], cwd=repo, env=env, capture_output=True).returncode == 0
    )

    sentinel.touch()
    failed = subprocess.run(["sh", str(script)], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "E2E environment unavailable" in failed.stderr
    assert marker.read_text().splitlines() == ["verify", "verify"]
