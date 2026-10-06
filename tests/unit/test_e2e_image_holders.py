"""E2E images must survive an external image GC for the whole run.

Observed 2026-10-06: Docker Desktop's Kubernetes node was under disk pressure
and kubelet's eviction manager called `DELETE /images/<name>` for every image
on the host every ~10 s; images with no container were removed mid-run (the
base image and the golden stage snapshots), unreferenced images with a
container survived. A `sleep infinity` holder per image is the defence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e import stages

REPO = Path(__file__).resolve().parents[2]


class _Container:
    def __init__(self, cid: str) -> None:
        self._cid = cid

    def get_wrapped_container(self):
        class _W:
            id = self._cid

        return _W()


def _record(calls, *, fail_holder_with: str | None = None):
    def run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["docker", "run"] and fail_holder_with is not None:
            exc = subprocess.CalledProcessError(125, argv, output="", stderr=fail_holder_with)
            raise exc
        return subprocess.CompletedProcess(argv, 0, stdout="sha256:abc\n", stderr="")

    return run


def test_snapshot_holds_the_committed_image(monkeypatch):
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbeef0001")
    calls: list[list[str]] = []
    monkeypatch.setattr(stages.subprocess, "run", _record(calls))

    tag = stages.snapshot(_Container("c1"), {"provider": "claude", "stage": "start"})

    assert tag == "omc-e2e-stage:claude-start-deadbeef0001"
    assert [c[:2] for c in calls] == [
        ["docker", "exec"],
        ["docker", "commit"],
        ["docker", "run"],
    ]
    assert calls[2] == [
        "docker",
        "run",
        "-d",
        "--name",
        "omc-e2e-holder-claude-start-deadbeef0001",
        "--label",
        "omc.e2e=holder",
        "--label",
        "omc.e2e.source=deadbeef0001",
        tag,
        "sleep",
        "infinity",
    ]


def test_snapshot_tolerates_an_existing_holder_only(monkeypatch):
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbeef0002")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        stages.subprocess,
        "run",
        _record(calls, fail_holder_with='Conflict. The container name "/x" is already in use'),
    )
    stages.snapshot(_Container("c1"), {"provider": "claude", "stage": "start"})

    calls.clear()
    monkeypatch.setattr(stages.subprocess, "run", _record(calls, fail_holder_with="no space left"))
    with pytest.raises(subprocess.CalledProcessError):
        stages.snapshot(_Container("c1"), {"provider": "claude", "stage": "start"})


def test_runner_holds_the_base_image_before_the_golden_path(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "docker.log"
    # Stub scripts use shell builtins only (see .omc/config/AGENTS.md).
    (stubs / "docker").write_text(
        f"""#!/bin/sh
echo "$@" >> {log}
case "$1 $2" in
  "image inspect") exit 0 ;;
  "container inspect") exit 1 ;;
  images*) echo "omc-e2e-stage:claude-start-$OMC_E2E_SOURCE_ID" ;;
esac
exit 0
"""
    )
    (stubs / "uv").write_text("#!/bin/sh\nexit 0\n")
    for stub in ("docker", "uv"):
        (stubs / stub).chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    env.pop("OMC_E2E_NO_DOCKER", None)

    result = subprocess.run(
        ["/bin/bash", str(REPO / "scripts" / "e2e.sh"), "golden"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    lines = log.read_text().splitlines()
    holder = [line for line in lines if line.startswith("run -d --name omc-e2e-holder-base-")]
    assert len(holder) == 1, lines
    source = holder[0].split("omc-e2e-holder-base-")[1].split()[0]
    assert holder[0] == (
        f"run -d --name omc-e2e-holder-base-{source} --label omc.e2e=holder "
        f"--label omc.e2e.source={source} omc-e2e:{source} sleep infinity"
    )
    # Held right after the image exists, before the golden path runs.
    assert lines[0].startswith("image inspect omc-e2e:")
    assert lines[1].startswith("container inspect omc-e2e-holder-base-")
    assert lines.index(holder[0]) == 2
