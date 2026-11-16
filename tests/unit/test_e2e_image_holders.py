"""E2E images must survive an external image GC for the whole run.

Observed 2026-10-06: Docker Desktop's Kubernetes node was under disk pressure
and kubelet's eviction manager called `DELETE /images/<name>` for every image
on the host every ~10 s; images with no container were removed mid-run (the
base image and the golden stage snapshots), unreferenced images with a
container survived. A `sleep infinity` holder per image is the defence."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e import stages

from ._stubs import host_bin

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
        out = "[]\n" if stages.LIVE_RUNS_PY in argv else "sha256:abc\n"  # no live stage run
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    return run


def test_snapshot_holds_the_committed_image(monkeypatch):
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbeef0001")
    monkeypatch.setattr(stages, "time", SimpleNamespace(time=lambda: 1_700_000_000), raising=False)
    calls: list[list[str]] = []
    monkeypatch.setattr(stages.subprocess, "run", _record(calls))

    tag = stages.snapshot(_Container("c1"), {"provider": "claude", "stage": "start", "repo": "/r"})

    assert tag == "omc-e2e-stage:claude-start-deadbeef0001"
    assert [c[:2] for c in calls] == [
        ["docker", "exec"],  # the live-run check
        ["docker", "exec"],  # the manifest
        ["docker", "commit"],
        ["docker", "run"],
    ]
    assert calls[3] == [
        "docker",
        "run",
        "-d",
        "--name",
        "omc-e2e-holder-claude-start-deadbeef0001",
        "--label",
        "omc.e2e=holder",
        "--label",
        "omc.e2e.source=deadbeef0001",
        "--label",
        "omc.e2e.created=1700000000",
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
    stages.snapshot(_Container("c1"), {"provider": "claude", "stage": "start", "repo": "/r"})

    calls.clear()
    monkeypatch.setattr(stages.subprocess, "run", _record(calls, fail_holder_with="no space left"))
    with pytest.raises(subprocess.CalledProcessError):
        stages.snapshot(_Container("c1"), {"provider": "claude", "stage": "start", "repo": "/r"})


def test_runner_holds_the_base_image_before_the_golden_path(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "docker.log"
    # Stub scripts use shell builtins only (see .omc/config/AGENTS.md).
    (stubs / "docker").write_text(
        f"""#!/bin/sh
echo "$@" >> {log}
case "$1 $2" in
  "ps -aq") exit 0 ;;
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
        [host_bin("bash"), str(REPO / "scripts" / "e2e.sh"), "golden"],
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
    argv = shlex.split(holder[0])
    assert re.fullmatch(r"omc\.e2e\.created=[0-9]+", argv[9])
    assert argv == [
        "run",
        "-d",
        "--name",
        f"omc-e2e-holder-base-{source}",
        "--label",
        "omc.e2e=holder",
        "--label",
        f"omc.e2e.source={source}",
        "--label",
        argv[9],
        f"omc-e2e:{source}",
        "sleep",
        "infinity",
    ]
    # Prune first, then hold the base image before the golden path runs.
    assert lines[:2] == [
        "ps -aq --filter label=omc.e2e=holder",
        "images --format {{.Repository}}:{{.Tag}}",
    ]
    assert lines[2].startswith("image inspect omc-e2e:")
    assert lines[3].startswith("container inspect omc-e2e-holder-base-")
    assert lines.index(holder[0]) == 4
    assert "pruned" not in result.stderr


def _runner_with_docker_stub(
    tmp_path,
    mode: str,
    scenario: str,
    *,
    no_docker=False,
    collect_only=False,
    extra_args: tuple[str, ...] = (),
    uv_body: str = "#!/bin/sh\nexit 0\n",
):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "docker.log"
    # Every Docker argument is separated by an ASCII unit separator, preserving
    # spaces in the inspect format while using shell builtins on the stub PATH.
    (stubs / "docker").write_text(r"""#!/bin/sh
printf '%s\037' "$@" >> "$DOCKER_LOG"
printf '\n' >> "$DOCKER_LOG"
case "$1" in
  ps)
    if [ "$2" = "-aq" ]; then
      case "$DOCKER_SCENARIO" in
        split)
          printf '%s\n' old missing fresh current_old current_fresh \
            malformed mixed_old mixed_fresh
          ;;
        boundary) printf '%s\n' boundary ;;
        failure) printf '%s\n' old failure ;;
      esac
    fi ;;
  inspect)
    case "$4" in
      old) printf 'old|aaaaaaaaaaaa|1699737200\n' ;;
      missing) printf 'missing|bbbbbbbbbbbb|<no value>\n' ;;
      fresh) printf 'fresh|cccccccccccc|1699996400\n' ;;
      current_old) printf 'current_old|%s|1699737200\n' "$OMC_E2E_SOURCE_ID" ;;
      current_fresh) printf 'current_fresh|%s|1699996400\n' "$OMC_E2E_SOURCE_ID" ;;
      malformed) printf 'malformed|dddddddddddd|not-a-time\n' ;;
      mixed_old) printf 'mixed_old|eeeeeeeeeeee|1699737200\n' ;;
      mixed_fresh) printf 'mixed_fresh|eeeeeeeeeeee|1699996400\n' ;;
      boundary) printf 'boundary|aaaaaaaaaaaa|1699740800\n' ;;
      failure) exit 1 ;;
    esac ;;
  image)
    [ "$2" = "inspect" ] && exit 0 ;;
  container)
    [ "$2" = "inspect" ] && exit 0 ;;
  images)
    case "$DOCKER_SCENARIO" in
      split)
        printf '%s\n' \
          omc-e2e:aaaaaaaaaaaa omc-e2e-stage:claude-start-aaaaaaaaaaaa \
          omc-e2e:bbbbbbbbbbbb omc-e2e-stage:claude-start-bbbbbbbbbbbb \
          omc-e2e:cccccccccccc omc-e2e-stage:claude-start-cccccccccccc \
          "omc-e2e:$OMC_E2E_SOURCE_ID" "omc-e2e-stage:claude-start-$OMC_E2E_SOURCE_ID" \
          omc-e2e:dddddddddddd omc-e2e-stage:claude-start-dddddddddddd \
          omc-e2e:eeeeeeeeeeee omc-e2e-stage:claude-start-eeeeeeeeeeee \
          omc-e2e:ffffffffffff omc-e2e-stage:claude-start-ffffffffffff \
          ubuntu:latest omc-e2e-extra:aaaaaaaaaaaa
        ;;
      boundary)
        printf '%s\n' omc-e2e:aaaaaaaaaaaa omc-e2e-stage:claude-start-aaaaaaaaaaaa \
          "omc-e2e-stage:claude-start-$OMC_E2E_SOURCE_ID"
        ;;
      snapshot_with_tail)
        printf '%s\n' "omc-e2e-stage:claude-start-$OMC_E2E_SOURCE_ID"
        padding=x
        while [ "${#padding}" -lt 65536 ]; do padding="$padding$padding"; done
        printf '%s\n' "$padding"
        ;;
      bare_stage) printf '%s\n' omc-e2e-stage:ffffffffffff ;;
      *) printf '%s\n' "omc-e2e-stage:claude-start-$OMC_E2E_SOURCE_ID" ;;
    esac ;;
esac
exit 0
""")
    (stubs / "date").write_text("#!/bin/sh\nprintf '1700000000\\n'\n")
    (stubs / "uv").write_text(uv_body)
    for stub in ("docker", "date", "uv"):
        (stubs / stub).chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "DOCKER_SCENARIO": scenario,
    }
    if no_docker:
        env["OMC_E2E_NO_DOCKER"] = "1"
    else:
        env.pop("OMC_E2E_NO_DOCKER", None)
    argv = [host_bin("bash"), str(REPO / "scripts" / "e2e.sh"), mode, *extra_args]
    if collect_only:
        argv.append("--collect-only")
    result = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True)
    calls = (
        [line.split("\x1f")[:-1] for line in log.read_text().split("\n") if line]
        if log.exists()
        else []
    )
    return result, calls


def test_runner_prunes_expired_holders_and_unpinned_images(tmp_path):
    result, calls = _runner_with_docker_stub(tmp_path, "golden", "split")

    assert result.returncode == 0, result.stderr
    assert [call for call in calls if call[:2] == ["rm", "-f"]] == [
        ["rm", "-f", "old", "missing", "malformed", "mixed_old"]
    ]
    assert [call for call in calls if call[:2] == ["rmi", "-f"]] == [
        [
            "rmi",
            "-f",
            "omc-e2e-stage:claude-start-aaaaaaaaaaaa",
            "omc-e2e-stage:claude-start-bbbbbbbbbbbb",
            "omc-e2e-stage:claude-start-dddddddddddd",
            "omc-e2e-stage:claude-start-ffffffffffff",
        ],
        [
            "rmi",
            "-f",
            "omc-e2e:aaaaaaaaaaaa",
            "omc-e2e:bbbbbbbbbbbb",
            "omc-e2e:dddddddddddd",
            "omc-e2e:ffffffffffff",
        ],
    ]
    assert calls[0] == ["ps", "-aq", "--filter", "label=omc.e2e=holder"]
    source = next(call[2].split(":")[1] for call in calls if call[:2] == ["image", "inspect"])
    assert calls.index(["images", "--format", "{{.Repository}}:{{.Tag}}"]) < calls.index(
        ["image", "inspect", f"omc-e2e:{source}"]
    )
    assert result.stderr == "pruned 4 stale holders and 8 images (older than 72 h)\n"


def test_runner_accepts_golden_snapshot_before_later_image_output(tmp_path):
    result, calls = _runner_with_docker_stub(tmp_path, "golden", "snapshot_with_tail")

    assert result.returncode == 0, result.stderr
    assert [call for call in calls if call[:1] == ["images"]] == [
        ["images", "--format", "{{.Repository}}:{{.Tag}}"],
        ["images", "--format", "{{.Repository}}:{{.Tag}}"],
    ]


def test_runner_prunes_unpinned_stage_tag_with_direct_source_suffix(tmp_path):
    result, calls = _runner_with_docker_stub(tmp_path, "rest", "bare_stage")

    assert result.returncode == 0, result.stderr
    assert [call for call in calls if call[:2] == ["rmi", "-f"]] == [
        ["rmi", "-f", "omc-e2e-stage:ffffffffffff"]
    ]


@pytest.mark.parametrize("mode", ["rest", "all"])
def test_runner_prunes_before_image_inspection_in_every_mode(tmp_path, mode):
    result, calls = _runner_with_docker_stub(tmp_path, mode, "empty")

    assert result.returncode == 0, result.stderr
    assert calls[0] == ["ps", "-aq", "--filter", "label=omc.e2e=holder"]
    assert calls[1] == ["images", "--format", "{{.Repository}}:{{.Tag}}"]
    assert calls[2][:2] == ["image", "inspect"]


def test_manual_prune_with_nothing_to_remove_calls_no_removal(tmp_path):
    # GNU xargs runs its command once on empty input unless -r; BSD xargs never does.
    result, calls = _runner_with_docker_stub(tmp_path, "prune", "empty")

    assert result.returncode == 0, result.stderr
    assert calls == [
        ["ps", "-aq", "--filter", "label=omc.e2e=holder"],
        ["images", "--format", "{{.Repository}}:{{.Tag}}"],
    ]


@pytest.mark.parametrize("no_docker,collect_only", [(True, False), (False, True)])
def test_runner_no_docker_never_calls_docker(tmp_path, no_docker, collect_only):
    result, calls = _runner_with_docker_stub(
        tmp_path, "golden", "split", no_docker=no_docker, collect_only=collect_only
    )

    assert result.returncode == 0, result.stderr
    assert calls == []


def test_runner_keeps_holder_at_exact_ttl_boundary(tmp_path):
    result, calls = _runner_with_docker_stub(tmp_path, "golden", "boundary")

    assert result.returncode == 0, result.stderr
    assert calls[0] == ["ps", "-aq", "--filter", "label=omc.e2e=holder"]
    assert [call for call in calls if call[:2] in (["rm", "-f"], ["rmi", "-f"])] == []
    assert "pruned" not in result.stderr


def test_runner_inspect_failure_stops_before_deletion(tmp_path):
    result, calls = _runner_with_docker_stub(tmp_path, "golden", "failure")

    assert result.returncode != 0
    assert [call for call in calls if call[:2] in (["rm", "-f"], ["rmi", "-f"])] == []


def test_runner_silent_when_nothing_to_prune(tmp_path):
    result, calls = _runner_with_docker_stub(tmp_path, "golden", "empty")

    assert result.returncode == 0, result.stderr
    assert calls[0] == ["ps", "-aq", "--filter", "label=omc.e2e=holder"]
    assert "pruned" not in result.stderr


# pytest exits 5 when its marker filter leaves nothing: a golden node id
# collects nothing in the parallel (non-golden) pass.
_EMPTY_PARALLEL_PASS_UV = """#!/bin/sh
case "$*" in *"not golden"*) exit 5 ;; esac
exit 0
"""
_GOLDEN_NODE_ID = "tests/e2e/golden/test_golden_claude.py::test_stage_recorded"


def test_runner_all_tolerates_an_empty_parallel_pass_only_with_a_golden_selection(tmp_path):
    (tmp_path / "selected").mkdir()
    (tmp_path / "unselected").mkdir()
    selected, _ = _runner_with_docker_stub(
        tmp_path / "selected",
        "all",
        "default",
        extra_args=(_GOLDEN_NODE_ID,),
        uv_body=_EMPTY_PARALLEL_PASS_UV,
    )
    unselected, _ = _runner_with_docker_stub(
        tmp_path / "unselected", "all", "default", uv_body=_EMPTY_PARALLEL_PASS_UV
    )

    assert selected.returncode == 0, selected.stderr
    assert "no non-golden test matched the selection" in selected.stderr
    assert unselected.returncode == 5, unselected.stderr


@pytest.mark.parametrize(
    "selection",
    [
        ("tests/e2e/test_e2e_codex_gate.py::test_codex_gate",),
        ("tests/e2e/variations/test_from_recorded.py::test_expensive_variation",),
        (_GOLDEN_NODE_ID, "tests/e2e/variations/test_from_recorded.py::test_expensive_variation"),
    ],
    ids=["codex-gate-node-id", "expensive-variation-node-id", "golden-plus-non-golden"],
)
def test_runner_all_never_tolerates_an_empty_parallel_pass_for_a_non_golden_node_id(
    tmp_path, selection
):
    # a non-golden node id the parallel pass deselects (codex_gate, expensive)
    # ran nowhere: the golden pass only ran the golden path
    result, _ = _runner_with_docker_stub(
        tmp_path, "all", "default", extra_args=selection, uv_body=_EMPTY_PARALLEL_PASS_UV
    )

    assert result.returncode == 5, result.stderr
    assert "no non-golden test matched the selection" not in result.stderr


def test_runner_rest_mode_still_fails_on_an_empty_selection(tmp_path):
    result, _ = _runner_with_docker_stub(
        tmp_path,
        "rest",
        "default",
        extra_args=(_GOLDEN_NODE_ID,),
        uv_body=_EMPTY_PARALLEL_PASS_UV,
    )

    assert result.returncode == 5, result.stderr


def test_runner_all_never_tolerates_an_empty_parallel_pass_for_a_keyword_filter(tmp_path):
    # `-k` deselects golden stages; its empty parallel pass is no golden node-id subset
    result, _ = _runner_with_docker_stub(
        tmp_path,
        "all",
        "default",
        extra_args=("-k", "something"),
        uv_body=_EMPTY_PARALLEL_PASS_UV,
    )

    assert result.returncode == 5, result.stderr
    assert "no non-golden test matched the selection" not in result.stderr


def test_runner_all_with_a_selection_still_fails_on_a_parallel_failure(tmp_path):
    result, _ = _runner_with_docker_stub(
        tmp_path,
        "all",
        "default",
        extra_args=(_GOLDEN_NODE_ID,),
        uv_body='#!/bin/sh\ncase "$*" in *"not golden"*) exit 1 ;; esac\nexit 0\n',
    )

    assert result.returncode == 1, result.stderr


def _uv_runs_without_frozen(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\buv run\b", line) and "uv run --frozen" not in line
    ]


def test_runner_never_lets_uv_rewrite_the_lock():
    # The source id hashes the working tree: a `uv run` that rewrites uv.lock
    # during the golden pass gives the parallel pass another id, and its stage
    # snapshots are then "absent" (observed on the 0.1.53 baseline run).
    assert _uv_runs_without_frozen((REPO / "scripts" / "e2e.sh").read_text()) == []


def test_e2e_recipes_never_let_uv_rewrite_the_lock():
    justfile = (REPO / "justfile").read_text()
    recipes = re.split(r"\n(?=[a-z0-9-]+(?: [^\n]*)?:\n)", justfile)
    e2e = [r for r in recipes if r.split(":", 1)[0].split()[0].endswith(("e2e-tests", "-gate"))]
    assert e2e, recipes
    assert _uv_runs_without_frozen("\n".join(e2e)) == []


@pytest.mark.parametrize("mode", ["golden", "rest", "all"])
def test_runner_invokes_uv_frozen_in_every_mode(tmp_path, mode):
    log = tmp_path / "uv.log"
    result, _ = _runner_with_docker_stub(
        tmp_path,
        mode,
        "default",
        no_docker=True,
        uv_body=f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n',
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls, result.stderr
    assert all(call.startswith("run --frozen ") for call in calls), calls
