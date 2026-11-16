"""Golden-path stage snapshots: commit the container after a stage, fork
variations from the image.

`docker commit` captures the filesystem only: mounts and processes are not
part of it, so snapshots are taken between turns and the Codex account volume
is remounted per clone. Claude's named session lives under ~/.claude and
Codex's under CODEX_HOME, both on the filesystem, so a clone resumes the
session by name exactly like the original container would.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time

MANIFEST = "/tmp/omc-stage.json"
SNAPSHOT_LABELS = (
    "--change",
    "LABEL org.testcontainers.session-id=omc-stage-snapshot",
    "--change",
    "LABEL org.testcontainers=false",
)


def source_id() -> str:
    """The identity snapshots are keyed by: the runner's OMC_E2E_SOURCE_ID,
    else the content of the working tree (tracked and untracked, minus
    ignored) as a 12-char tree hash. Content, not a commit sha: an edit gets
    its own id and identical trees share one, so reuse is exact."""
    if env := os.environ.get("OMC_E2E_SOURCE_ID"):
        return env
    with tempfile.TemporaryDirectory(prefix="omc-e2e-index-") as scratch:
        # A path that does not exist yet: git rejects an empty pre-created index.
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(scratch, "index")}
        subprocess.run(["git", "add", "-A"], check=True, env=env, capture_output=True)
        tree = subprocess.check_output(["git", "write-tree"], text=True, env=env).strip()
    return tree[:12]


def stage_tag(provider: str, stage: str) -> str:
    return f"omc-e2e-stage:{provider}-{stage}-{source_id()}"


# Run in the container with the repo as argv[1]: a JSON list naming each
# `.cache/omc/run.json` of the repo or a linked worktree that says `running` or
# `held`. Linked worktrees are read from `.git/worktrees/*/gitdir`, so no git
# call is needed.
LIVE_RUNS_PY = """\
import json, pathlib, sys
repo = pathlib.Path(sys.argv[1])
roots = [repo] + [
    pathlib.Path(gitdir.read_text().strip()).parent
    for gitdir in sorted((repo / ".git" / "worktrees").glob("*/gitdir"))
]
live = []
for root in roots:
    path = root / ".cache" / "omc" / "run.json"
    try:
        state = json.loads(path.read_text()).get("state")
    except (OSError, ValueError, AttributeError):
        continue
    if state in ("running", "held"):
        live.append(f"{path}: {state}")
print(json.dumps(live))
"""


def snapshot(container, manifest: dict) -> str:
    """Write the manifest into the container, commit it, return the tag.

    Only once every stage run has ended (the golden-snapshot rule): an image
    whose `run.json` says `running` or `held` forks variations that record a
    process group the image does not have."""
    cid = container.get_wrapped_container().id
    live = json.loads(
        subprocess.run(
            ["docker", "exec", cid, "python3", "-c", LIVE_RUNS_PY, manifest["repo"]],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    if live:
        raise AssertionError(f"golden snapshot with a live stage run: {', '.join(live)}")
    subprocess.run(
        [
            "docker",
            "exec",
            cid,
            "python3",
            "-c",
            "import sys; open(sys.argv[1], 'w').write(sys.argv[2])",
            MANIFEST,
            json.dumps(manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    tag = stage_tag(manifest["provider"], manifest["stage"])
    # A committed image inherits the container's labels, including
    # testcontainers' session id. Ryuk's exit-time image prune removed the
    # snapshots even with the value overridden (observed 2026-10-01), so the
    # E2E runner disables Ryuk (scripts/e2e.sh); the override stays as a
    # second line of defence for runs started outside the runner. `docker
    # commit` has no quiet flag; the sha it prints is captured.
    subprocess.run(
        ["docker", "commit", *SNAPSHOT_LABELS, cid, tag],
        check=True,
        capture_output=True,
        text=True,
    )
    hold_image(tag, f"omc-e2e-holder-{manifest['provider']}-{manifest['stage']}-{source_id()}")
    return tag


def hold_image(tag: str, name: str) -> None:
    """Pin an image with an idle container so an external image GC cannot
    remove it. Observed 2026-10-06: Docker Desktop's kubelet, under disk
    pressure, deleted every image without a container every ~10 s — the base
    image and the stage snapshots vanished mid-run with no Docker event left
    in the (256-entry) buffer. The daemon refuses to remove an image that a
    container references, so one `sleep infinity` per image is the whole
    defence. The runner's `prune_stale` removes a holder 72 h after its
    creation once its tree is not the current one, and manual
    `scripts/e2e.sh prune` removes it immediately; a holder that already
    exists (same source id, earlier run) is kept."""
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "omc.e2e=holder",
                "--label",
                f"omc.e2e.source={source_id()}",
                "--label",
                f"omc.e2e.created={int(time.time())}",
                tag,
                "sleep",
                "infinity",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        if "already in use" not in (exc.stderr or ""):
            raise


def read_manifest(container) -> dict:
    cid = container.get_wrapped_container().id
    out = subprocess.run(
        ["docker", "exec", cid, "cat", MANIFEST], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def stage_image(provider: str, stage: str) -> str:
    """The snapshot a variation forks from; absent means the golden path has
    not run for this source — fail loud, never build one implicitly."""
    import pytest

    tag = stage_tag(provider, stage)
    probe = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    if probe.returncode != 0:
        pytest.fail(f"stage snapshot {tag} is absent — run `just golden` first (same source)")
    return tag
