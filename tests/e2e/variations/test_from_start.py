"""Variations forked from the `start` snapshot (worktree created, primer given)."""

import json

import pytest

from ..harness import run_in

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


@pytest.mark.variation("start")
def test_resume_from_primer_snapshot(stage_session):
    """A clone of the snapshot resumes the named session: same slug, live turn."""
    container, session, manifest = stage_session
    session.send("Reply with exactly SNAPSHOT-OK and nothing else.")
    turn = session.wait_turn(120)
    assert "SNAPSHOT-OK" in turn["text"], turn["text"]
    assert turn["session_id"] == manifest["slug"]


@pytest.mark.variation("start")
def test_seeded_session_reports_pinned_task_models(stage_session):
    """The saved live Claude session exposes every configured full ID."""
    container, session, manifest = stage_session
    artifact = "/tmp/omc-seeded-task-models-verdict.txt"
    session.send(
        "In this resumed worktree session, run exactly "
        f"`omc internal models > {artifact}` with the shell tool. "
        "Do not set OMC_PROVIDER or change configuration. Reply DONE after it finishes."
    )
    turn = session.wait_turn(120)
    assert turn["session_id"] == manifest["slug"], turn
    rc, out = run_in(container, ["cat", artifact])
    assert rc == 0, f"resumed Claude session did not create {artifact}: {out}"
    lines = out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("OMC_MODELS "), out
    verdict = json.loads(lines[0].removeprefix("OMC_MODELS "))
    assert verdict == {
        "ok": True,
        "provider": "claude",
        "tasks": {
            task: {"model": manifest["model"], "effort": ""}
            for task in ("orchestrator", "design", "plan", "review", "simple", "medium", "high")
        },
    }
