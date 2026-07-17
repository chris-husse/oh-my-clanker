"""The interactive handoff, live: omc start (no --headless) under a real PTY.

This is the only tier that can verify the two launch-time properties omc exists
for: the terminal title is emitted BEFORE the session takes the terminal, and
the session is created NAMED after the slug (resumable via `claude --resume`).
"""

from __future__ import annotations

import json

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e

_ONBOARDED = (
    "python3 - << 'PYEOF'\n"
    "import json, pathlib\n"
    "p = pathlib.Path.home() / '.claude.json'\n"
    "data = json.loads(p.read_text()) if p.exists() else {}\n"
    "data.setdefault('hasCompletedOnboarding', True)\n"
    "data.setdefault('theme', 'dark')\n"
    "p.write_text(json.dumps(data))\n"
    "PYEOF"
)


def _worktree_for(container, repo, key_prefix):
    rc, wtout = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert rc == 0, wtout
    data = json.loads(wtout[wtout.index("[") : wtout.rindex("]") + 1])
    entry = next(w for w in data if str(w.get("branch", "")).startswith(f"feature/{key_prefix}"))
    branch = entry["branch"]
    return branch.split("/", 1)[1], entry["path"]  # (slug == session name, worktree path)


def test_interactive_start_sets_title_and_names_session(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    # Interactive claude in a fresh HOME would stall on first-run onboarding;
    # mark it complete the way a real logged-in machine already has.
    rc, out = run_in(container, ["bash", "-c", _ONBOARDED])
    assert rc == 0, out
    repo = make_work_repo(container)

    # Drive the REAL handoff under a PTY: probes -> live slug -> wt worktree ->
    # exec bash rc (title, cd) -> interactive `claude -n <slug> "/omc:start ..."`.
    # `timeout` ends the session after it has started; artifacts are asserted after.
    rc, out = run_in(
        container,
        [
            "script",
            "-q",
            "/tmp/typescript",
            "-c",
            "export SHELL=/bin/bash; timeout 90 omc start PROJ-1",
        ],
        cwd=repo,
        timeout=300,
    )

    slug, wt_path = _worktree_for(container, repo, "proj-1")

    # 1) Terminal title: the OSC 0 sequence for the slug hit the terminal stream
    #    (emitted by the shell rc BEFORE the session took the terminal).
    rc, ts = run_in(container, ["cat", "/tmp/typescript"])
    assert rc == 0
    assert f"\x1b]0;{slug}\x07" in ts, f"OSC title for {slug!r} not in PTY stream:\n{ts[:1500]}"

    # 2) Session naming: a session NAMED after the slug exists and is resumable —
    #    launch-time `-n <slug>` is the only way it could have gotten that name.
    rc, res = run_in(
        container,
        [
            "claude",
            "--resume",
            slug,
            "-p",
            "Reply with exactly: RESUMED-OK",
            "--output-format",
            "text",
        ],
        cwd=wt_path,
        timeout=240,
    )
    assert rc == 0 and "RESUMED-OK" in res, f"resume by name {slug!r} failed:\n{res}"
