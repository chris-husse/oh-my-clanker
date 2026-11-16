"""Live /omc:finish: squash to one described commit, push. Hermetic — the
origin is a local bare repo, so this also exercises the no-forge fallback."""

from __future__ import annotations

import pytest

from .harness import (
    _watch_state,
    _write_skill,
    add_stage,
    add_stage_hook,
    configure_omc,
    hold_watch_stand_in,
    hook_instructions,
    make_work_repo,
    require_token,
    run_in,
)
from .judge import judge

pytestmark = pytest.mark.e2e


def _make_feature_branch(container, repo):
    script = (
        f"cd {repo} && git switch -qc feature/manual-fix && "
        "echo alpha >> notes.txt && git add -A && git commit -qm 'wip: alpha' && "
        "echo beta >> notes.txt && git commit -qam 'wip: beta'"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_finish_squashes_describes_and_pushes(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )
    assert rc == 0, out

    origin = f"{repo}-origin"
    rc, count = run_in(
        container,
        ["git", "-C", origin, "rev-list", "--count", "main..feature/manual-fix"],
    )
    assert rc == 0 and count.strip() == "1", (
        f"origin should be exactly ONE commit ahead, got {count!r}\ntranscript:\n{out[:2000]}"
    )

    rc, subject = run_in(
        container, ["git", "-C", origin, "log", "-1", "--format=%s", "feature/manual-fix"]
    )
    rc2, body = run_in(
        container, ["git", "-C", origin, "log", "-1", "--format=%b", "feature/manual-fix"]
    )
    assert subject.strip() and not subject.startswith("wip"), subject
    assert len(subject.strip()) <= 100, f"title too long: {subject!r}"
    assert body.strip(), "squashed commit has no description body"

    verdict = judge(
        container,
        "claude",
        scenario="/omc:finish ran on a feature branch with two wip commits over a "
        "local bare origin (no recognizable forge).",
        rubric=[
            "the transcript shows the branch being squashed and pushed",
            "the transcript does NOT create an MR/PR via gh/glab or any API",
            "the transcript ends by offering follow-ups (close worktree / "
            "address review comments / chat), or lists them for the "
            "non-interactive context",
        ],
        artifacts=out,
    )
    assert verdict["passed"], verdict["reasons"]


def test_finish_runs_passing_build_and_check_stages_then_pushes(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)
    # spec 2026-11-16 D2: build runs before check. Both stages append to one
    # order file, so a check that ran first stays on record even when the
    # stage-gate repair later restarts at build and the push still happens.
    add_stage(
        container,
        repo,
        "build",
        "echo build >> /tmp/omc-finish-order && echo BUILD-OK",
        "Exit 0, after BUILD-OK is printed. Any other exit is failed.",
    )
    add_stage(
        container,
        repo,
        "check",
        "echo check >> /tmp/omc-finish-order && echo CHECK-OK",
        "Exit 0, after CHECK-OK is printed. Any other exit is failed.",
    )
    # the stage hooks must be part of the branch (a real project tracks .omc/)
    rc, out = run_in(
        container,
        ["bash", "-c", f"cd {repo} && git add -A && git commit -qm 'add build and check stages'"],
    )
    assert rc == 0, out

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )
    assert rc == 0, out
    # NOTE: `claude -p --output-format text` prints only the FINAL message, so
    # mid-session OMC_STAGE verdict lines are not visible here — the on-disk
    # marker and the pushed origin state below are the real evidence.

    rc, order = run_in(container, ["cat", "/tmp/omc-finish-order"], stdout_only=True)
    assert rc == 0, "neither project stage executed"
    stages = order.split()
    assert stages[:1] == ["build"], f"finish did not run build first: {stages}"
    assert "check" in stages, f"project check stage never executed: {stages}"
    rc, count = run_in(
        container,
        ["git", "-C", f"{repo}-origin", "rev-list", "--count", "main..feature/manual-fix"],
    )
    assert rc == 0 and count.strip() == "1", f"expected pushed squash, got {count!r}"


# The red build stage cannot be fixed: it fails whatever the code says. Under
# the fix-cycle bound (spec 2026-11-16 D17) a cycle that brings neither
# progress nor a new idea stops at once with the CRITICAL question, so this
# run should stop after its first cycle; this headless run's
# `--allowed-tools Bash Read Skill` cannot dispatch a fix subagent, so it may
# stop sooner. Nothing is pushed while a stage is red, so the artifact below
# still holds. Not measured on this host (no token): the next token-bearing
# /omc:verify run re-measures this test against the default 300 s ceiling and
# moves it to `expensive` if it exceeds it.
def test_finish_stops_before_push_on_failing_stage(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)
    add_stage(
        container,
        repo,
        "build",
        "echo the build is broken >&2; exit 1",
        "Exit 0. A non-zero exit code means the build FAILED.",
    )
    rc, out = run_in(
        container, ["bash", "-c", f"cd {repo} && git add -A && git commit -qm 'add build stage'"]
    )
    assert rc == 0, out
    stage_path = ".omc/bin/build"
    rc, original_stage = run_in(container, ["git", "-C", repo, "show", f"HEAD:{stage_path}"])
    assert rc == 0, original_stage

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )
    rc, stage = run_in(container, ["git", "-C", repo, "show", f"feature/manual-fix:{stage_path}"])
    assert rc == 0 and stage == original_stage, (
        f"finish changed the committed build stage; transcript:\n{out}"
    )
    # the branch must NOT reach origin when a stage fails
    rc2, _ = run_in(
        container,
        ["git", "-C", f"{repo}-origin", "rev-parse", "--verify", "feature/manual-fix"],
    )
    assert rc2 != 0, f"failing build stage must stop the push!\ntranscript:\n{out[:2000]}"


# The red check stage cannot be fixed: it fails whatever the code says. Under
# the fix-cycle bound (spec 2026-11-16 D17) a cycle that brings neither
# progress nor a new idea stops at once with the CRITICAL question, so this
# run should stop after its first cycle; this headless run's
# `--allowed-tools Bash Read Skill` cannot dispatch a fix subagent, so it may
# stop sooner. Nothing is pushed while a stage is red, so the artifact below
# still holds. Not measured on this host (no token): the next token-bearing
# /omc:verify run re-measures this test against the default 300 s ceiling and
# moves it to `expensive` if it exceeds it.
def test_finish_stops_before_push_on_failing_check_stage(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)
    add_stage(
        container,
        repo,
        "check",
        "echo the unit tests are broken >&2; exit 1",
        "Exit 0. A non-zero exit code means the check FAILED.",
    )
    rc, out = run_in(
        container, ["bash", "-c", f"cd {repo} && git add -A && git commit -qm 'add check stage'"]
    )
    assert rc == 0, out
    stage_path = ".omc/bin/check"
    rc, original_stage = run_in(container, ["git", "-C", repo, "show", f"HEAD:{stage_path}"])
    assert rc == 0, original_stage

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )
    rc, stage = run_in(container, ["git", "-C", repo, "show", f"feature/manual-fix:{stage_path}"])
    assert rc == 0 and stage == original_stage, (
        f"finish changed the committed check stage; transcript:\n{out}"
    )
    # the branch must NOT reach origin when the check stage fails
    rc2, _ = run_in(
        container,
        ["git", "-C", f"{repo}-origin", "rev-parse", "--verify", "feature/manual-fix"],
    )
    assert rc2 != 0, f"failing check stage must stop the push!\ntranscript:\n{out[:2000]}"


_CRIT_RAN = "/tmp/omc-crit-ran"
# Worst case under the default 300 s ceiling: 30 (configure_omc, as in
# test_e2e_stage_hold) + 4 x 5 (repo, branch, stage files, commit) + 30 (the
# stand-in takes the lock; `unmigrated` only) + 190 (the finish turn: squash,
# then a refused build stage is a CRITICAL STOP with no fix cycle, spec C4)
# + 6 x 5 (the before snapshot and the five checks) = 300 s. The turn's own
# 190 s timeout bounds it, and the turn must end by itself within it.
_CRIT_TURN_S = 190


def _tree_bytes(container, repo, paths) -> dict[str, str]:
    """Each path's committed (on the feature branch) and on-disk bytes, hex-encoded."""
    script = (
        "import subprocess, sys\n"
        "repo, *paths = sys.argv[1:]\n"
        "for path in paths:\n"
        "    shown = subprocess.run(['git', '-C', repo, 'show', f'feature/manual-fix:{path}'],"
        " capture_output=True).stdout\n"
        "    disk = open(f'{repo}/{path}', 'rb').read()\n"
        "    print(path, shown.hex(), disk.hex())\n"
    )
    rc, out = run_in(container, ["python3", "-c", script, repo, *paths], timeout=5)
    assert rc == 0, out
    return {line.split()[0]: line for line in out.splitlines()}


@pytest.mark.parametrize("case", ["no-watch", "unmigrated"])
def test_finish_stops_at_once_on_a_critical_stop_stage(container, case):
    """A refused build (spec C4) stops finish at once: no hook run, no push.

    `no-watch`: a real `.omc/bin/build` but no `omc watch`, so `stage start`
    refuses with the no-watch CRITICAL STOP; the agent must not start a watch
    itself. `unmigrated`: only an old `.omc/skills/build/SKILL.md` (with a watch
    held), so `stage start` refuses with the migrate CRITICAL STOP; the agent
    must neither follow the old skill nor write the hook itself."""
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)
    if case == "no-watch":
        instructions = hook_instructions(
            timeout="5m",
            quiet="2m",
            done="The hook exits once its build command has run. Nothing is held.",
            passed="Exit 0. Any other exit is failed.",
            fixing="Read the end of the log. Never edit `.omc/bin/*`: that is the stage itself.",
        )
        add_stage_hook(container, repo, "build", f"echo build >> {_CRIT_RAN}", instructions)
        stage_paths = [".omc/bin/build", ".omc/bin/build.instructions.md"]
    else:
        _write_skill(
            container,
            repo,
            "skills",
            "build",
            "Build the project (old-style stage skill).",
            f"Run `echo build >> {_CRIT_RAN}` in the project root. Then end with "
            '`OMC_STAGE {"stage": "build", "configured": true, "passed": true, '
            '"summary": "built"}`.',
        )
        stage_paths = [".omc/skills/build/SKILL.md"]
        hold_watch_stand_in(container, repo)
    rc, out = run_in(
        container,
        ["bash", "-c", f"cd {repo} && git add -A && git commit -qm 'add build stage'"],
        timeout=5,
    )
    assert rc == 0, out
    before = _tree_bytes(container, repo, stage_paths)
    if case == "no-watch":
        assert _watch_state(container, repo) == "not running"

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Read",
            "Skill",
        ],
        cwd=repo,
        timeout=_CRIT_TURN_S,
    )
    turn_rc = rc
    # the branch must NOT reach origin while a stage is a CRITICAL STOP
    rc2, _ = run_in(
        container,
        ["git", "-C", f"{repo}-origin", "rev-parse", "--verify", "feature/manual-fix"],
        timeout=5,
    )
    assert rc2 != 0, f"a CRITICAL STOP stage must stop the push!\ntranscript:\n{out[:2000]}"
    rc, _ = run_in(container, ["test", "!", "-e", _CRIT_RAN], timeout=5)
    assert rc == 0, f"the build ran (hook or old skill) despite the refusal:\n{out[:2000]}"
    rc, _ = run_in(container, ["test", "!", "-e", f"{repo}/.cache/omc/run.json"], timeout=5)
    assert rc == 0, f"a stage run was started:\n{out[:2000]}"
    assert _tree_bytes(container, repo, stage_paths) == before, (
        f"finish changed the stage files; transcript:\n{out[:2000]}"
    )
    if case == "unmigrated":
        rc, _ = run_in(container, ["test", "!", "-e", f"{repo}/.omc/bin/build"], timeout=5)
        assert rc == 0, f"finish wrote the build hook itself:\n{out[:2000]}"
    else:
        assert _watch_state(container, repo) == "not running", (
            f"the agent started a watch to get past the refusal:\n{out[:2000]}"
        )
    assert turn_rc == 0, f"the finish turn did not stop by itself within {_CRIT_TURN_S} s:\n{out}"
