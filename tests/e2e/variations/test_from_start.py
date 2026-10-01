"""Variations forked from the `start` snapshot (worktree created, primer given)."""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


@pytest.mark.variation("start")
def test_resume_from_primer_snapshot(stage_session):
    """A clone of the snapshot resumes the named session: same slug, live turn."""
    container, session, manifest = stage_session
    session.send("Reply with exactly SNAPSHOT-OK and nothing else.")
    turn = session.wait_turn(120)
    assert "SNAPSHOT-OK" in turn["text"], turn["text"]
    assert turn["session_id"] == manifest["slug"]
