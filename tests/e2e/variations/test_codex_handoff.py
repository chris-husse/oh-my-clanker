"""The cross-provider handoff, as evidence: a Claude-designed `recorded`
snapshot, then `omc implement --codex --headless` in its worktree. Expensive
and serialized on the Codex account volume; never a gate. Standalone on
purpose: the stage fixture binds the fork provider's session, and this test
launches a NEW session on another provider (spec §3.10)."""

from __future__ import annotations

import json

import pytest
from testcontainers.core.container import DockerContainer

from ..codex_auth import codex_account, require_codex_ready
from ..conftest import _finish_container_setup, _forward_tokens
from ..harness import configure_omc, run_in
from ..lifecycle_helpers import (
    IMPLEMENT_TURN_BUDGET,
    _assert_implemented_artifacts,
    _codex_model,
    _set_write_capability,
)
from ..stages import read_manifest, stage_image

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("codex"), pytest.mark.expensive]

# Same mechanism as ClaudeConversation.snapshot (tests/e2e/conversation.py):
# the driver copied docker/conversation.py to /tmp/omc-conversation.py inside
# the golden container, and docker commit keeps it in every stage image. runpy
# avoids importing `docker` as a package (no __init__.py, and docker-py may
# shadow it on the system python3).
_SNAPSHOT_PY = (
    "import json, runpy, sys; "
    "mod = runpy.run_path('/tmp/omc-conversation.py'); "
    "print(json.dumps(mod['snapshot_repo'](sys.argv[1])))"
)


def _snapshot(container, path):
    """snapshot_repo() inside the container, same shape the drivers record."""
    rc, out = run_in(container, ["python3", "-c", _SNAPSHOT_PY, path])
    assert rc == 0, out
    return json.loads(out.strip().splitlines()[-1])


# The 1800 s run_in budget plus the account-volume lock wait (up to 240 s),
# plugin setup and snapshots; still above run_in, so `timeout` fires first.
@pytest.mark.timeout(2100)
def test_codex_implement_handoff_from_claude_record():
    image = stage_image("claude", "recorded")
    c = _forward_tokens(
        DockerContainer(image).with_command("sleep infinity"), use_codex_account=True
    )
    with codex_account(c, _finish_container_setup, use_account=True):
        m = read_manifest(c)
        # configure_omc runs docker/setup-plugins.sh codex (plugins into the
        # account CODEX_HOME) and then `omc configure --set llm.default=codex`;
        # the default is restored to claude below so the FLAG is what selects
        # Codex and the run proves the override, not a default swap.
        configure_omc(c, "codex")
        require_codex_ready(c)
        rc, out = run_in(
            c, ["omc", "configure", "--set", f"llm.providers.codex.model={_codex_model()}"]
        )
        assert rc == 0, out
        rc, out = run_in(c, ["omc", "configure", "--set", "llm.default=claude"])
        assert rc == 0, out
        _set_write_capability(c)
        recorded = _snapshot(c, m["worktree"])

        rc, out = run_in(
            c,
            ["omc", "implement", "--codex", "--headless"],
            cwd=m["worktree"],
            timeout=IMPLEMENT_TURN_BUDGET,
        )
        assert rc == 0, out
        assert "→ probing tools (git, wt, codex)" in out, out

        evidence = {"turns": [{"snapshot": _snapshot(c, m["worktree"]), "text": out}]}
        _assert_implemented_artifacts(c, m["repo"], m["worktree"], m["branch"], evidence, recorded)
