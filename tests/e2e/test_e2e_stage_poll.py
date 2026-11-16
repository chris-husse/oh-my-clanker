"""Live Codex long poll (design 6.5, 7): a check run that outlasts one 540 s
`omc internal stage wait` is waited for again, not judged or killed."""

from __future__ import annotations

import json

import pytest

from .codex_auth import require_codex_ready
from .conversation import Conversation
from .harness import (
    COUNT_CALLS,
    STAGE_WAIT,
    add_stage_hook,
    configure_omc,
    hold_watch_stand_in,
    hook_instructions,
    make_work_repo,
    require_token,
    run_in,
)
from .lifecycle_helpers import _codex_model

# The hook outlasts one 540 s `omc internal stage wait` by 80 s, so the second wait
# is certain, not a race against a hook finishing near the 540 s mark.
_HOOK_S = 620
# The turn: up to 60 s before `start` (reading the skill), the hook's 620 s, and up
# to 160 s for the second wait's answer and the final OMC_STAGE line: 60 + 620 + 160.
_TURN_S = 840
# Setup outside the turn: up to 240 s waiting for the account-volume lock (codex_account),
# 120 s for container start, plugin setup, Codex login and the model-pin probe, 60 s for
# the repo, hook and stand-in, and 60 s for the artifact checks and closing the TUI:
# 240 + 120 + 60 + 840 + 60 = 1320 s.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.expensive,
    pytest.mark.e2e_provider("codex"),
    pytest.mark.timeout(1320),
]


def test_codex_stage_run_outlasts_one_wait(container):
    require_token("codex")
    configure_omc(container, "codex")
    require_codex_ready(container)
    repo = make_work_repo(container, "/work/stage-poll")
    model = _codex_model()
    instructions = hook_instructions(
        timeout="15m",
        quiet="none",
        done="The hook exits after about ten silent minutes. Nothing is held.",
        passed="Exit 0.",
        fixing="Read the log. Never edit `.omc/bin/*`: that is the stage itself.",
    )
    add_stage_hook(
        container,
        repo,
        "check",
        f"echo started; sleep {_HOOK_S}; echo done > /tmp/poll.done; exit 0",
        instructions,
    )
    rc, out = run_in(
        container,
        ["bash", "-c", "git add -A && git commit -qm 'check hook' && git push -q origin main"],
        cwd=repo,
    )
    assert rc == 0, out
    hold_watch_stand_in(container, repo)

    with Conversation(container) as session:
        # Codex rejects /omc:check as an unknown TUI command; `$omc:check` is its
        # explicit skill mention (see lifecycle_helpers._direct_implement).
        session.start(
            [
                "codex",
                "--no-alt-screen",
                "--dangerously-bypass-approvals-and-sandbox",
                "-m",
                model,
                "-c",
                "tui.terminal_title=[]",
                "$omc:check",
            ],
            repo,
            "codex",
        )
        turn = session.wait_turn(_TURN_S)

    rc, _ = run_in(container, ["test", "-f", "/tmp/poll.done"])
    assert rc == 0, f"the check hook did not run to its end:\n{turn['text']}"
    rc, raw = run_in(container, ["cat", f"{repo}/.cache/omc/last-check.json"])
    assert rc == 0, raw
    last = json.loads(raw)
    assert last["state"] == "passed", last
    rc, count = run_in(container, ["python3", "-c", COUNT_CALLS, turn["session_id"], STAGE_WAIT])
    assert rc == 0, count
    assert int(count.strip()) >= 2, (
        f"expected at least two `{STAGE_WAIT}` calls, got {count.strip()}:\n{turn['text']}"
    )
