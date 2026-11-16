"""The stage supervisor (design 6.2-6.4): start, verdicts, limits and wait.

Every run is real: `start_run` spawns `python -m omc.stagerun supervise`, which
runs a `#!/bin/sh` hook in its own process group. The `stage` fixture (conftest.py) holds a
watch alive lock for the repo and, on teardown, SIGKILLs the process group and
supervisor of every run still recorded live, and reaps them, pass or fail."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import stat
import time
from pathlib import Path

import pytest

from omc import stagerun
from omc.buildprogress import sentinel_line
from omc.errors import OmcError
from omc.stagedef import STAGE_TEMPLATE
from omc.stagerun import start_run
from omc.toolctx import ToolContext

from . import _mutexproc, _stagekit

WATCH_SENTENCE = (
    "CRITICAL STOP: no `omc watch` running for this project — "
    "the user starts `omc watch` in the primary checkout ({primary})"
)


def _instructions(*, timeout="none", quiet="none", cycles=None):
    limits = f"Timeout: {timeout}\nQuiet timeout: {quiet}\n"
    if cycles is not None:
        limits += f"Fix cycles: {cycles}\n"
    return _stagekit.VALID_INSTRUCTIONS.replace("Timeout: none\nQuiet timeout: none\n", limits, 1)


def _gate(s):
    """A shell line that blocks the hook until the test calls the returned release."""
    gate = s.tmp / "gate"
    line = f"while [ ! -e {shlex.quote(str(gate))} ]; do {_stagekit.sleep_line(0.1)}; done"
    return line, gate.touch


def test_no_watch_refuses_and_starts_nothing(nowatch):
    marker = nowatch.tmp / "started"
    _stagekit.write_hook(nowatch.repo, "check", body=f"echo started > {shlex.quote(str(marker))}")
    answer = start_run(nowatch.ctx, "check", [])
    assert answer.state == "refused"
    assert answer.reason == WATCH_SENTENCE.format(primary=nowatch.repo)
    time.sleep(2)
    assert not marker.exists()
    assert not (nowatch.repo / ".cache" / "omc" / "run.json").exists()


def test_exit_zero_passes(stage):
    answer = _stagekit.finished(stage, "check", "echo ok; exit 0")
    assert answer.state == "passed"
    assert answer.reason == ""
    assert set(answer.to_json()) == _stagekit.ANSWER_KEYS
    record = stage.record()
    assert record["exit_code"] == 0
    assert record["verdict"] == "passed"
    assert record["ended"] >= record["started"]


def test_exit_one_fails(stage):
    answer = _stagekit.finished(stage, "check", "echo bad; exit 1")
    assert answer.state == "failed"
    assert answer.reason == "exit 1"


def test_verify_failed_result_file_is_held_and_alive(stage):
    pidfile = stage.pidfile()
    body = (
        'echo failed > "$OMC_STAGE_RESULT"\n'
        f"echo $$ > {shlex.quote(str(pidfile))}\n"
        f"exec {_stagekit.sleep_line(60)}"
    )
    answer = _stagekit.finished(stage, "verify", body)
    assert answer.state == "held"
    pid = stage.read_pid(pidfile)
    assert stage.ctx.signal_pid(pid, 0) is True
    assert stage.record()["state"] == "held"
    time.sleep(1.5)  # a held run stays held across passes
    assert _stagekit.wait(stage.ctx, "verify", 0).state == "held"
    assert stage.ctx.signal_pid(pid, 0) is True


def test_check_failed_result_file_ends_at_once(stage):
    pidfile = stage.pidfile()
    body = (
        'echo failed > "$OMC_STAGE_RESULT"\n'
        f"echo $$ > {shlex.quote(str(pidfile))}\n"
        f"exec {_stagekit.sleep_line(60)}"
    )
    answer = _stagekit.finished(stage, "check", body)
    assert answer.state == "failed"
    assert answer.reason == 'result file says "failed"'
    stage.gone(stage.read_pid(pidfile))


def test_verify_passed_result_file_ends_at_once(stage):
    pidfile = stage.pidfile()
    body = (
        'echo passed > "$OMC_STAGE_RESULT"\n'
        f"echo $$ > {shlex.quote(str(pidfile))}\n"
        f"exec {_stagekit.sleep_line(60)}"
    )
    answer = _stagekit.finished(stage, "verify", body)
    assert answer.state == "passed"
    stage.gone(stage.read_pid(pidfile))


@pytest.mark.parametrize(
    ("stage_name", "word", "state"), [("check", "passed", "passed"), ("verify", "failed", "held")]
)
def test_empty_result_file_is_no_verdict_yet(stage, stage_name, word, state):
    """`echo x > f` creates the file before it writes: while the hook lives, an empty
    result file is no verdict yet, never a final `failed`."""
    body = (
        ': > "$OMC_STAGE_RESULT"\n'
        f"{_stagekit.sleep_line(2.5)}\n"
        f'echo {word} > "$OMC_STAGE_RESULT"\n'
        f"exec {_stagekit.sleep_line(60)}"
    )
    answer = _stagekit.finished(stage, stage_name, body)
    assert answer.state == state, answer


def test_empty_result_file_of_an_exited_hook_fails(stage):
    answer = _stagekit.finished(stage, "check", ': > "$OMC_STAGE_RESULT"; exit 0')
    assert answer.state == "failed"
    assert answer.reason == 'result file says ""'


def test_omc_package_in_the_worktree_root_does_not_shadow_the_supervisor(stage):
    shadow = stage.repo / "omc" / "__init__.py"
    shadow.parent.mkdir()
    shadow.write_text('raise ImportError("the project\'s own omc package was imported")\n')
    assert _stagekit.finished(stage, "check", "exit 0").state == "passed"


def test_result_file_wins_over_exit_code(stage):
    answer = _stagekit.finished(stage, "check", 'echo failed > "$OMC_STAGE_RESULT"; exit 0')
    assert answer.state == "failed"
    assert answer.reason == 'result file says "failed"'


def test_verify_failed_then_exited_is_failed_not_held(stage):
    """The supervisor may see the result file before or after the exit; either way
    a verify that wrote `failed` and exited has nothing left to hold."""
    _stagekit.run_hook(stage, "verify", 'echo failed > "$OMC_STAGE_RESULT"; exit 0')
    _mutexproc.wait_for(lambda: _stagekit.wait(stage.ctx, "verify", 0).state == "failed", 10)
    assert stage.record()["reason"] == 'result file says "failed"'


def test_unknown_result_content_fails(stage):
    answer = _stagekit.finished(stage, "check", 'echo maybe > "$OMC_STAGE_RESULT"; exit 0')
    assert answer.state == "failed"
    assert answer.reason == 'result file says "maybe"'


def test_quiet_timeout_quotes_last_line(stage):
    answer = _stagekit.finished(
        stage,
        "check",
        f"echo last words; exec {_stagekit.sleep_line(30)}",
        instructions=_instructions(quiet="2s"),
    )
    assert answer.state == "quiet-timed-out"
    assert answer.reason == "no log progress for 2s at: last words"
    assert not stage.ctx.signal_group(stage.record()["pgid"], 0)


def test_timeout_kills_chatty_hook(stage):
    answer = _stagekit.finished(
        stage,
        "check",
        f"while :; do echo tick; {_stagekit.sleep_line(0.2)}; done",
        instructions=_instructions(timeout="2s"),
    )
    assert answer.state == "timed-out"
    assert answer.reason == "timed out after 2s"
    assert not stage.ctx.signal_group(stage.record()["pgid"], 0)


def test_hook_sees_caller_env_and_stage_vars(stage):
    out = stage.tmp / "env.out"
    o = shlex.quote(str(out))
    body = (
        f'echo "$PROBE $OMC_STAGE_NAME $OMC_PROJECT_ID $OMC_WORKTREE_ID" > {o}\n'
        f'[ -d "$OMC_STAGE_DIR" ] && echo dir-ok >> {o}\n'
        f'[ ! -e "$OMC_STAGE_RESULT" ] && echo result-absent >> {o}\n'
        f'echo "$OMC_STAGE_LOG" >> {o}\n'
        f'echo "$OMC_STAGE_DIR" >> {o}\n'
        f'echo "$OMC_STAGE_RESULT" >> {o}\n'
        f"pwd >> {o}\n"
    )
    answer = _stagekit.finished(stage, "check", body, extra_env={"PROBE": "x"})
    assert answer.state == "passed", answer
    project = (stage.repo / ".omc" / "project.id").read_text().strip()
    worktree = json.loads((stage.repo / ".cache" / "worktree.id").read_text())["id"]
    log = Path(answer.log)
    base = str(log)[: -len(".log")]
    assert out.read_text().splitlines() == [
        f"x check {project} {worktree}",
        "dir-ok",
        "result-absent",
        answer.log,
        base + ".d",
        base + ".result",
        str(stage.repo),
    ]
    assert log.parent == stage.tmp / "stages" / project / worktree
    assert log.name.startswith("check-") and log.name.endswith(f"-{answer.run}.log")


def test_hook_sees_the_callers_gitnexus_env_as_is(stage):
    """§6.2: the hook gets the caller's environment, not omc's GitNexus overlay."""
    out = stage.tmp / "env.out"
    env = {k: v for k, v in stage.ctx.env.items() if not k.startswith(("HF_", "GITNEXUS_"))}
    stage.ctx = ToolContext.from_env(
        {**env, "HF_HOME": "/caller/hf", "GITNEXUS_SHARED_STORE": "caller"}
    )
    body = (
        'printf \'%s\\n\' "$HF_HOME" "$GITNEXUS_SHARED_STORE" '
        f'"${{GITNEXUS_EMBEDDING_RUNTIME_DIR-unset}}" > {shlex.quote(str(out))}'
    )
    assert _stagekit.finished(stage, "check", body).state == "passed"
    assert out.read_text().splitlines() == ["/caller/hf", "caller", "unset"]


def test_default_stage_root_is_per_user(tmp_path):
    """/tmp is shared: one user's stage root must not block another's runs."""
    env = {k: v for k, v in os.environ.items() if k != "OMC_STAGE_ROOT"}
    default = stagerun._stage_root(ToolContext.from_env(env))
    assert default == Path(f"/tmp/omc-stages-{os.getuid()}")
    overridden = ToolContext.from_env({**env, "OMC_STAGE_ROOT": str(tmp_path)})
    assert stagerun._stage_root(overridden) == tmp_path


def test_new_stage_root_is_private(stage):
    root = stage.tmp / "stages"
    _stagekit.finished(stage, "check", "exit 0")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_shared_stage_root_refuses(stage):
    """A stage root others can write to could be swapped under a run: CRITICAL STOP."""
    root = stage.tmp / "stages"
    root.mkdir()
    root.chmod(0o777)
    _stagekit.write_hook(stage.repo, "check", body="exit 0")
    answer = start_run(stage.ctx, "check", [])
    assert (answer.state, answer.run) == ("refused", "")
    assert answer.reason == f"CRITICAL STOP: stage log root {root} is not private to this user"
    assert list(root.iterdir()) == []
    assert not (stage.repo / ".cache" / "omc" / "run.json").exists()


def test_hook_gets_start_args(stage):
    out = stage.tmp / "args.out"
    body = f'echo "$#:$1:$2" > {shlex.quote(str(out))}'
    answer = _stagekit.finished(stage, "check", body, args=("a b", "c"))
    assert answer.state == "passed"
    assert out.read_text() == "2:a b:c\n"


def test_answer_carries_instructions_file_and_fix_cycles(stage):
    text = _instructions(cycles=4)
    answer = _stagekit.run_hook(stage, "check", "exit 0", instructions=text)
    assert answer.fix_cycles == 4
    assert Path(answer.instructions).read_text() == text
    assert Path(answer.instructions).name == Path(answer.log).name.replace(
        ".log", ".instructions.md"
    )
    assert _stagekit.wait(stage.ctx, "check", 30).fix_cycles == 4


def test_no_stage_bound_and_slow_instructions_warning(stage):
    wait_gate, release = _gate(stage)
    _stagekit.write_hook(stage.repo, "check", body=f"{wait_gate}\nexit 0", show_delay=1.2)
    answer = start_run(stage.ctx, "check", [])
    assert answer.state == "running"
    release()
    assert answer.fix_cycles == 0  # no `Fix cycles` of its own: omc's default applies
    assert len(answer.warnings) == 1 and "over 1s" in answer.warnings[0]
    assert _stagekit.wait(stage.ctx, "check", 30).warnings == answer.warnings


def test_tail_is_new_lines_only_and_capped(stage):
    body = 'i=1; while [ "$i" -le 100 ]; do echo "line $i"; i=$((i + 1)); done'
    first = _stagekit.finished(stage, "check", body)
    assert first.state == "passed"
    assert first.tail == (*(f"line {i}" for i in range(62, 101)), sentinel_line(0))
    second = _stagekit.wait(stage.ctx, "check", 30)
    assert second.run == first.run
    assert second.tail == ()


def test_wait_without_run_errors(stage):
    with pytest.raises(OmcError, match="^no check run in this worktree$"):
        _stagekit.wait(stage.ctx, "check", 1)


def test_unconfigured_and_unmigrated(stage):
    answer = start_run(stage.ctx, "check", [])
    assert answer.state == "unconfigured"
    assert answer.to_json() == {
        "stage": "check",
        "state": "unconfigured",
        "run": "",
        "log": "",
        "instructions": "",
        "elapsed": 0,
        "quiet": 0,
        "tail": [],
        "reason": "",
        "fix_cycles": 0,
        "warnings": [],
    }
    skill = stage.repo / ".omc" / "skills" / "build" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: build\n---\n")
    answer = start_run(stage.ctx, "build", [])
    assert answer.state == "refused"
    assert answer.reason == (
        "CRITICAL STOP: migrate this stage with /omc:integrate "
        "(.omc/skills/build/SKILL.md exists, .omc/bin/build does not)"
    )
    assert not (stage.repo / ".cache" / "omc" / "run.json").exists()


def test_broken_instructions_refuse(stage):
    _stagekit.write_hook(stage.repo, "check", body="exit 0", instructions="## Limits\n")
    answer = start_run(stage.ctx, "check", [])
    assert answer.state == "refused"
    assert answer.reason.startswith("CRITICAL STOP: ")
    assert not (stage.repo / ".cache" / "omc" / "run.json").exists()


def test_template_is_refreshed_in_a_linked_worktree_never_in_the_primary(stage, monkeypatch):
    """A4: watch's --auto-build runs in the primary, and a dirty tracked file there makes
    watch skip its sync for good; so start, wait and kill refresh only a linked worktree."""
    template = Path(".omc") / "bin" / "INSTRUCTIONS.template.md"
    _stagekit.write_hook(stage.repo, "check", body="exit 0")
    (stage.repo / template).write_text("stale\n")
    _stagekit.commit_all(stage.repo)
    started = start_run(stage.ctx, "check", [])
    assert _stagekit.wait(stage.ctx, "check", 30).run == started.run
    stagerun.kill_run(stage.ctx, "check")
    assert (stage.repo / template).read_text() == "stale\n"
    assert _stagekit.tracked_changes(stage.repo) == ""

    other = _stagekit.add_worktree(stage.repo, stage.tmp / "wt2", "b")
    stage.roots.append(other)
    monkeypatch.chdir(other)
    start_run(stage.ctx, "check", [])
    assert (other / template).read_text() == STAGE_TEMPLATE


def test_supervisor_sigkilled_reads_died(stage):
    pidfile = stage.pidfile()
    started = _stagekit.run_hook(
        stage, "check", f"echo $$ > {shlex.quote(str(pidfile))}; exec {_stagekit.sleep_line(30)}"
    )
    record = stage.record()
    assert stage.ctx.signal_pid(record["pid"], signal.SIGKILL)
    answer = _stagekit.wait(stage.ctx, "check", 10)
    assert answer.state == "died"
    assert answer.run == started.run
    assert answer.reason == "the supervisor is gone (killed from outside, or the machine restarted)"
    assert stage.record() == record  # nothing is written
    assert stage.ctx.signal_pid(stage.read_pid(pidfile), 0)  # left for 5b's recovery


def test_copied_run_json_is_ignored(stage, monkeypatch):
    assert _stagekit.finished(stage, "check", "exit 0").state == "passed"
    other = _stagekit.add_worktree(stage.repo, stage.tmp / "wt2", "b")
    stage.roots.append(other)
    shutil.copytree(stage.repo / ".cache", other / ".cache", dirs_exist_ok=True)
    monkeypatch.chdir(other)
    with pytest.raises(OmcError, match="^no check run in this worktree$"):
        _stagekit.wait(stage.ctx, "check", 1)


def test_log_gets_sentinel_line(stage):
    answer = _stagekit.finished(stage, "check", "echo hi; exit 3")
    assert answer.state == "failed"
    assert Path(answer.log).read_text().splitlines() == ["hi", sentinel_line(3)]


def test_wait_answers_last_record_of_its_stage(stage):
    """`run.json` holds the newest run; a stage's own last record answers for it."""
    verify = _stagekit.finished(stage, "verify", "exit 0")
    _stagekit.finished(stage, "check", "exit 0")
    answer = _stagekit.wait(stage.ctx, "verify", 1)
    assert (answer.state, answer.run) == ("passed", verify.run)


def test_wait_period_answers_running(stage):
    started = _stagekit.run_hook(stage, "check", f"exec {_stagekit.sleep_line(30)}")
    t0 = time.monotonic()
    answer = _stagekit.wait(stage.ctx, "check", 1)
    assert answer.state == "running"
    assert answer.run == started.run
    assert 1 <= time.monotonic() - t0 < 10


def test_sentinel_starts_its_own_line(stage):
    answer = _stagekit.finished(stage, "check", "printf 'no newline'; exit 0")
    assert Path(answer.log).read_text().splitlines() == ["no newline", sentinel_line(0)]
