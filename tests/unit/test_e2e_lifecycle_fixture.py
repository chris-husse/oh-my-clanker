"""Run the lifecycle fixture's generated verify script without a model actor."""

import os
import subprocess
from pathlib import Path

from tests.e2e import lifecycle_helpers


def test_verify_stage_sentinel_reports_unavailable_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle_helpers, "make_work_repo", lambda _container, path: path)

    def local_transport(_container, argv, **_kwargs):
        if argv[:2] == ["python3", "-c"]:
            result = subprocess.run(argv, capture_output=True, text=True)
            return result.returncode, result.stdout + result.stderr
        return 0, ""

    monkeypatch.setattr(lifecycle_helpers, "run_in", local_transport)
    repo = Path(lifecycle_helpers._fixture(None, failing_stage="verify", path=str(tmp_path)))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    initial_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    script = repo / ".omc/stage-scripts/verify.sh"
    sentinel = tmp_path / "verify-unavailable"
    marker = tmp_path / "stage-markers"
    failed_heads = tmp_path / "failed-verify-heads"
    env = {
        **os.environ,
        "OMC_EXTERNAL_VERIFY_SENTINEL": str(sentinel),
        "OMC_LIFECYCLE_MARKER": str(marker),
        "OMC_FAILED_VERIFY_HEADS": str(failed_heads),
    }
    assert (
        subprocess.run(["sh", str(script)], cwd=repo, env=env, capture_output=True).returncode == 0
    )
    assert not failed_heads.exists(), "passing verify must not record a failure"

    sentinel.touch()
    failed = subprocess.run(["sh", str(script)], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "E2E environment unavailable" in failed.stderr
    assert marker.read_text().splitlines() == ["verify", "verify"]
    assert failed_heads.read_text().splitlines() == [initial_head]

    # Repair workers may commit before retrying. Capture the last failed
    # attempt's HEAD so the E2E can detect a later, forbidden handoff commit.
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "repair"], cwd=repo, check=True)
    repair_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    retry = subprocess.run(["sh", str(script)], cwd=repo, env=env, capture_output=True)
    assert retry.returncode != 0
    assert failed_heads.read_text().splitlines() == [initial_head, repair_head]
