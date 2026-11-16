"""Live held verify (design 6.2, 7): a verify hook that reports red and keeps its
system up is held past the agent's turn, and ends once the project's real
`omc watch` stops."""

from __future__ import annotations

import json
import time

import pytest

from .harness import (
    _watch_state,
    add_stage_hook,
    configure_omc,
    hold_watch_stand_in,
    hook_instructions,
    make_work_repo,
    require_token,
    run_in,
    start_real_watch,
    stop_real_watch,
)

pytestmark = pytest.mark.e2e

_HOLD_PID = "/tmp/hold.pid"
# Worst-case waits under the default 300 s ceiling: 60 (the watch takes its alive lock
# at boot, before its first tick) + 180 (the claude turn) + 15 (SIGINT until the watch
# lets go) + 15 (the held run ends) = 270 s, leaving 30 s for configure, the repo and
# the five record and log checks (each a `cat`, `id` or `stat`: well under a second).
_WATCH_LOCK_S = 60
_TURN_S = 180
_WATCH_STOP_S = 15
_HELD_END_S = 15


def _run_record(container, repo, name):
    rc, raw = run_in(container, ["cat", f"{repo}/.cache/omc/{name}"])
    assert rc == 0, f"no {name}: {raw}"
    return json.loads(raw)


def _hook_alive(container) -> bool:
    rc, _ = run_in(container, ["bash", "-c", f'kill -0 "$(cat {_HOLD_PID})"'])
    return rc == 0


def _add_held_verify(container, repo) -> None:
    """A committed verify hook that reports red and keeps its system up: held."""
    instructions = hook_instructions(
        timeout="none",
        quiet="none",
        done="The hook writes `failed` to the result file when a piece is red and keeps "
        "its system up: the run is held until it is killed. Otherwise it exits.",
        passed="Exit 0: every verify piece passed.",
        fixing="Read the log: each red piece prints `E2E piece red`. Never edit `.omc/bin/*`: "
        "that is the stage itself.",
    )
    add_stage_hook(
        container,
        repo,
        "verify",
        f"echo $$ > {_HOLD_PID}; echo 'E2E piece red' ; "
        'echo failed > "$OMC_STAGE_RESULT"; exec sleep 3600',
        instructions,
    )
    rc, out = run_in(
        container,
        ["bash", "-c", "git add -A && git commit -qm 'verify hook' && git push -q origin main"],
        cwd=repo,
    )
    assert rc == 0, out


def _read(container, path) -> str:
    rc, out = run_in(container, ["cat", path], timeout=5)
    assert rc == 0, f"no {path}: {out}"
    return out


def test_failed_verify_is_held_until_watch_stops(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _add_held_verify(container, repo)
    start_real_watch(container, repo, within=_WATCH_LOCK_S)

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:verify",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=_TURN_S,
    )
    assert rc == 0, out
    run = _run_record(container, repo, "run.json")
    assert run["state"] == "held", f"{run}\ntranscript:\n{out}"
    assert _hook_alive(container), f"the held hook is gone after the agent stopped:\n{out}"
    # Where the run keeps its files (design 6.2): the per-user private root, then the
    # project's and the worktree's IDs, so two projects or worktrees never share a log.
    rc, uid = run_in(container, ["id", "-u"], timeout=5)
    assert rc == 0, uid
    project_id = _read(container, f"{repo}/.omc/project.id").strip()
    stage_root = f"/tmp/omc-stages-{uid.strip()}"
    assert run["log"].startswith(f"{stage_root}/{project_id}/{run['worktree_id']}/verify-"), run
    assert "E2E piece red" in _read(container, run["log"]), run
    rc, mode = run_in(container, ["stat", "-c", "%a", stage_root], timeout=5)
    assert rc == 0 and mode.strip() == "700", f"{stage_root} mode {mode!r}"
    worktree_id = json.loads(_read(container, f"{repo}/.cache/worktree.id"))
    assert worktree_id["id"] == run["worktree_id"], (worktree_id, run)

    stop_real_watch(container, repo, within=_WATCH_STOP_S)
    deadline = time.monotonic() + _HELD_END_S
    while True:
        last = _run_record(container, repo, "last-verify.json")
        if not _hook_alive(container) and last["state"] == "killed":
            break
        assert time.monotonic() < deadline, (
            f"the held run outlived the watch by {_HELD_END_S} s: {last}, "
            f"hook alive: {_hook_alive(container)}"
        )
        time.sleep(0.5)
    assert last["reason"].startswith("watch-gone"), last


# Worst case under the default 300 s ceiling: 30 (configure_omc, the repo and the
# hook, as above) + 30 (the stand-in takes the lock) + 2 x 15 (start, then wait
# answers `held` as soon as the hook writes its result) + 180 (the claude turn; the
# kill itself waits at most 40 s for the run to end, inside it) + 15 (the hook's
# group is gone) + 3 x 5 (the checks) = 300 s.
_KILL_START_S = 15
_KILL_GONE_S = 15


def test_verify_kill_ends_a_held_run(container):
    """`/omc:verify kill` is how a user ends a held verify (A5): it ends the run with
    `killed` for the kill itself, the hook's group goes, and the watch is untouched."""
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _add_held_verify(container, repo)
    hold_watch_stand_in(container, repo)
    rc, out = run_in(
        container,
        ["omc", "internal", "stage", "start", "verify"],
        cwd=repo,
        timeout=_KILL_START_S,
    )
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["omc", "internal", "stage", "wait", "verify"],
        cwd=repo,
        timeout=_KILL_START_S,
    )
    assert rc == 0, out
    held = _run_record(container, repo, "run.json")
    assert held["state"] == "held", f"{held}\n{out}"
    assert _hook_alive(container), held

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:verify kill",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=_TURN_S,
    )
    assert rc == 0, out
    last = _run_record(container, repo, "last-verify.json")
    assert last["run"] == held["run"] and last["state"] == "killed", f"{last}\ntranscript:\n{out}"
    assert not last["reason"].startswith("watch-gone"), last
    assert last["reason"].startswith("kill:"), last
    deadline = time.monotonic() + _KILL_GONE_S
    while _hook_alive(container):
        assert time.monotonic() < deadline, f"the held hook outlived the kill by {_KILL_GONE_S} s"
        time.sleep(0.5)
    assert _watch_state(container, repo) == "running", "the kill took the watch stand-in down"
