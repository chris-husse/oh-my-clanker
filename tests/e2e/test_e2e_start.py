import json

import pytest

from .harness import PROVIDERS, configure_omc, make_work_repo, require_token, run_in, wire_mcp
from .judge import judge

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("provider", PROVIDERS)
def test_start_headless_creates_worktree_and_seeds(container, provider):
    require_token(provider)
    configure_omc(container, provider)
    wire_mcp(container, provider, "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out
    assert "Unknown command" not in out, f"seeded /omc:start did not resolve:\n{out[:2000]}"

    # worktree exists on disk with an omc-shaped branch
    rc2, wtout = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert rc2 == 0 and "feature/proj-1" in wtout, wtout

    # the busy-lock probe ran: filelock touched the lock file in the shared .git
    rc3, lockout = run_in(container, ["test", "-f", ".git/omc-watch-busy.lock"], cwd=repo)
    assert rc3 == 0, f"busy-lock file missing in primary .git: {lockout}"

    if provider == "claude":
        decoder = json.JSONDecoder()
        entries = None
        for offset, char in enumerate(wtout):
            if char != "[":
                continue
            try:
                parsed, _ = decoder.raw_decode(wtout, offset)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                entries = parsed
                break
        assert entries is not None, wtout
        matches = [
            entry
            for entry in entries
            if (entry.get("branch") or "") == "feature/proj-1"
            or (entry.get("branch") or "").startswith("feature/proj-1-")
        ]
        assert len(matches) == 1 and matches[0].get("path"), wtout
        rc4, settings_text = run_in(
            container, ["cat", f"{matches[0]['path']}/.claude/settings.local.json"]
        )
        assert rc4 == 0, settings_text
        settings = json.loads(settings_text)
        assert settings["preferredNotifChannel"] == "auto"
        assert "omc internal notify --provider claude" not in settings_text
    else:
        rc4, plan = run_in(container, ["omc", "start", "PROJ-1", "--dry-run"], cwd=repo)
        assert rc4 == 0 and "tui.notifications=true" in plan, plan

    verdict = judge(
        container,
        provider,
        scenario="`omc start PROJ-1 --headless` ran the seeded /omc:start session "
        "for fixture ticket PROJ-1 ('Fix login timeout in auth service').",
        rubric=[
            "the transcript engages with the PROJ-1 ticket (login timeout topic)",
            "the transcript moves toward brainstorming/requirements, or explains "
            "precisely which prerequisite blocked it (e.g. superpowers missing)",
            "the transcript is not an error dump or empty output",
        ],
        artifacts=out,
    )
    assert verdict["passed"], verdict["reasons"]


def test_start_idempotent_reentry_claude(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)
    rc1, out1 = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert rc1 == 0, out1
    rc2, out2 = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert rc2 == 0, out2  # same ticket re-enters the same worktree, no failure
    rcl, wtout = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert wtout.count("feature/proj-1") >= 1
