"""This repo's own stage hooks (`.omc/bin/`), the daemon invariant and the contract lists."""

import os
import subprocess
from pathlib import Path

import pytest

from omc.stagedef import STAGE_TEMPLATE, parse_instructions

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".omc" / "bin"

CODEX_PATHS = (
    "src/omc/providers/codex.py",
    "tests/e2e/conversation.py",
    "tests/e2e/codex_plugin_payload.py",
    "tests/e2e/codex_auth.py",
    "docker/conversation.py",
    "docker/setup-plugins.sh",
)

INVARIANT = (
    "The one exception is a stage run: a detached supervisor bound to one hook run; it "
    "ends when the hook ends or is killed, is never restarted or scheduled, and never "
    "outlives a running `omc watch` for its project, or its worktree."
)


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("stage", ["check", "build", "verify"])
def test_repo_hooks_print_valid_instructions(stage):
    hook = BIN / stage
    assert os.access(hook, os.X_OK), f"{hook} is not executable"
    # A real project hook: it runs with the user's PATH (it calls `cat`), not a stub PATH.
    done = subprocess.run(
        [str(hook), "--show-instructions"],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert done.returncode == 0, done.stderr
    parsed = parse_instructions(done.stdout)
    assert parsed.text.strip() == (BIN / f"{stage}.instructions.md").read_text().strip()
    if stage == "verify":
        assert parsed.subset is not None


def test_repo_hook_commands():
    assert "just check" in (BIN / "check").read_text()
    assert "just build" in (BIN / "build").read_text()
    verify = (BIN / "verify").read_text()
    for needle in ("just e2e-tests", "just codex-gate", "origin/main...HEAD", *CODEX_PATHS):
        assert needle in verify, f"verify missing {needle!r}"


def test_repo_template_file_is_current():
    assert (BIN / "INSTRUCTIONS.template.md").read_text() == STAGE_TEMPLATE


def test_agents_md_amends_daemon_invariant_and_contracts():
    agents = (ROOT / ".omc" / "config" / "AGENTS.md").read_text()
    assert INVARIANT in _flat(agents)
    assert "OMC_STAGE_RUN" in agents
    context = (ROOT / ".omc" / "skills" / "explain-context" / "SKILL.md").read_text()
    assert "OMC_STAGE_RUN" in context
    flat = _flat(agents)
    start = flat.index("Project stages for this repo:")
    stages_line = flat[start : flat.index("Stage runs need `omc watch`", start)]
    assert ".omc/bin/" in stages_line


def test_old_stage_skills_kept_for_bootstrap():
    # Pins the bootstrap constraint: the installed omc 0.1.50 still reads these. Remove
    # this test together with the three skills in the follow-up.
    for stage in ("check", "build", "verify"):
        assert (ROOT / ".omc" / "skills" / stage / "SKILL.md").is_file()


def test_codex_stage_poll_recipe():
    justfile = (ROOT / "justfile").read_text()
    assert "codex-stage-poll" in justfile
    assert "test_e2e_stage_poll.py" in justfile
