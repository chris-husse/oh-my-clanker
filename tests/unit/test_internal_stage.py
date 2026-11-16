"""`omc internal stage start|wait|kill|template` and the OMC_STAGE_RUN line (design 6.4).

Driven through `run_internal`, so the ToolContext is the process environment's: the
`stage` fixture points OMC_STAGE_ROOT and OMC_HOME under tmp_path, holds a watch alive
lock and, on teardown, kills every run still live (conftest.py, `_stagekit.Stage`)."""

from __future__ import annotations

import json
import shlex

import pytest

from omc.internal import _USAGE, run_internal
from omc.stagedef import STAGE_TEMPLATE

from . import _stagekit


def _verdict(out: str) -> dict:
    """The one stdout line, `OMC_STAGE_RUN {json}`, as its JSON."""
    lines = out.splitlines()
    assert len(lines) == 1, out
    prefix, _, payload = lines[0].partition(" ")
    assert prefix == "OMC_STAGE_RUN", out
    data = json.loads(payload)
    assert set(data) == _stagekit.ANSWER_KEYS
    return data


def test_stage_template_prints_template(capsys):
    assert run_internal(["stage", "template"]) == 0
    assert capsys.readouterr().out == STAGE_TEMPLATE


def test_stage_start_wait_roundtrip(stage, capsys):
    _stagekit.write_hook(stage.repo, "check", body="exit 0")
    assert run_internal(["stage", "start", "check"]) == 0
    started = _verdict(capsys.readouterr().out)
    assert started["stage"] == "check" and started["run"]
    assert run_internal(["stage", "wait", "check"]) == 0
    waited = _verdict(capsys.readouterr().out)
    assert waited["run"] == started["run"]
    assert waited["state"] == "passed", waited


def test_stage_kill_answers_the_ended_run(stage, capsys):
    _stagekit.write_hook(stage.repo, "check", body="exit 0")
    assert run_internal(["stage", "start", "check"]) == 0
    started = _verdict(capsys.readouterr().out)
    assert run_internal(["stage", "wait", "check"]) == 0
    capsys.readouterr()
    assert run_internal(["stage", "kill", "check"]) == 0
    killed = _verdict(capsys.readouterr().out)
    assert killed["run"] == started["run"] and killed["state"] == "passed", killed


def test_stage_start_refused_exits_2_with_verdict(nowatch, capsys):
    _stagekit.write_hook(nowatch.repo, "check", body="exit 0")
    assert run_internal(["stage", "start", "check"]) == 2
    answer = _verdict(capsys.readouterr().out)
    assert answer["state"] == "refused"
    assert answer["reason"].startswith("CRITICAL STOP:"), answer["reason"]
    assert "omc watch" in answer["reason"]


def test_stage_start_unconfigured_exits_0_with_verdict(stage, capsys):
    assert run_internal(["stage", "start", "build"]) == 0
    assert _verdict(capsys.readouterr().out)["state"] == "unconfigured"


def test_stage_wait_without_run_exits_1_no_verdict(stage, capsys):
    assert run_internal(["stage", "wait", "check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:"), captured.err


@pytest.mark.parametrize(("after", "recorded"), [(["--", "a", "b"], "a b"), (["--"], "")])
def test_stage_start_passes_args_after_dashdash(stage, capsys, after, recorded):
    out = stage.tmp / "args.txt"
    _stagekit.write_hook(stage.repo, "check", body=f"printf '%s' \"$*\" > {shlex.quote(str(out))}")
    assert run_internal(["stage", "start", "check", *after]) == 0
    _verdict(capsys.readouterr().out)
    assert run_internal(["stage", "wait", "check"]) == 0
    assert _verdict(capsys.readouterr().out)["state"] == "passed"
    assert out.read_text() == recorded


def test_stage_start_os_error_exits_1_no_verdict(stage, capsys, monkeypatch):
    blocker = stage.tmp / "not-a-dir"
    blocker.write_text("x\n")
    monkeypatch.setenv("OMC_STAGE_ROOT", str(blocker))
    _stagekit.write_hook(stage.repo, "check", body="exit 0")
    assert run_internal(["stage", "start", "check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: File exists ({blocker})\n"


def test_stage_wait_outside_a_repo_exits_1_no_verdict(tmp_path, monkeypatch, capsys):
    """stagerun's Refusal ("not inside a git repository") is an error, not a refused
    verdict: rc 2 belongs to the OMC_STAGE_RUN `refused` line alone (design 6.4)."""
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("OMC_STAGE_ROOT", str(tmp_path / "stages"))
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    norepo = tmp_path / "norepo"
    norepo.mkdir()
    monkeypatch.chdir(norepo)
    assert run_internal(["stage", "wait", "check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: not inside a git repository"), captured.err


def test_stage_start_passes_callers_environment_to_hook(stage, capsys, monkeypatch):
    monkeypatch.setenv("OMC_TEST_CALLER_VAR", "from the caller")
    out = stage.tmp / "env.txt"
    _stagekit.write_hook(
        stage.repo, "check", body=f"printf '%s' \"$OMC_TEST_CALLER_VAR\" > {shlex.quote(str(out))}"
    )
    assert run_internal(["stage", "start", "check"]) == 0
    _verdict(capsys.readouterr().out)
    assert run_internal(["stage", "wait", "check"]) == 0
    assert _verdict(capsys.readouterr().out)["state"] == "passed"
    assert out.read_text() == "from the caller"


@pytest.mark.parametrize(
    "rest",
    [
        [],
        ["bogus"],
        ["start"],
        ["start", "lint"],
        ["start", "check", "a"],
        ["start", "check", "a", "--", "b"],
        ["wait"],
        ["wait", "lint"],
        ["wait", "check", "extra"],
        ["kill"],
        ["kill", "check", "extra"],
        ["template", "extra"],
    ],
)
def test_stage_usage(rest, capsys):
    assert run_internal(["stage", *rest]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _USAGE + "\n"
