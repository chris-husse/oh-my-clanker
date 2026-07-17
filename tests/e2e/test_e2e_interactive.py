"""Launch-time properties, live: terminal title and session naming.

The interactive handoff can't be fully automated (a real TUI needs a real
terminal), but its two load-bearing properties can:

- TITLE: `omc start` (no --headless) is driven TTY-less. bash `-i` still
  sources the generated rcfile, so the REAL execvp handoff runs and the OSC 0
  title bytes for the slug land in captured output — emitted before the
  session command, per the shell adapters' ordering contract.
- SESSION NAME: omc names seeded sessions after the slug (interactive AND
  headless). A headless `omc start` run is followed by `claude --resume
  <slug>`, which only succeeds if a session with exactly that name exists.
"""

from __future__ import annotations

import json
import re

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e


def _extract_json_array(text: str):
    """docker exec merges stderr (ANSI-styled wt log lines) into stdout — try every
    '[' until one parses as the JSON array."""
    end = text.rindex("]") + 1
    for m in re.finditer(r"\[", text):
        try:
            data = json.loads(text[m.start() : end])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    raise AssertionError(f"no JSON array in wt output:\n{text[:800]}")


def _worktree_for(container, repo, key_prefix):
    rc, wtout = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert rc == 0, wtout
    data = _extract_json_array(wtout)
    entry = next(
        (w for w in data if str(w.get("branch", "")).startswith(f"feature/{key_prefix}")),
        None,
    )
    assert entry, f"no feature/{key_prefix}* worktree found — omc start died early?\n{wtout[:800]}"
    branch = entry["branch"]
    return branch.split("/", 1)[1], entry["path"]  # (slug == session name, worktree path)


def test_interactive_exec_emits_title_before_session(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    # The REAL interactive path: probes -> live slug -> wt worktree -> execvp
    # bash --rcfile (title, cd, seeded claude). Without a TTY the claude TUI
    # exits/errs after the rc runs — the title bytes are already out by then.
    # `timeout` caps the run either way; assertions are on artifacts, not rc.
    rc, out = run_in(
        container,
        ["timeout", "90", "omc", "start", "PROJ-1"],
        env={"SHELL": "/bin/bash"},
        cwd=repo,
        timeout=240,
    )

    slug, _ = _worktree_for(container, repo, "proj-1")
    assert f"\x1b]0;{slug}\x07" in out, (
        f"OSC title for {slug!r} not emitted by the exec handoff:\n{out[:1500]}"
    )


def test_seeded_session_is_named_after_slug(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")
    repo = make_work_repo(container)

    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert rc == 0, out

    slug, wt_path = _worktree_for(container, repo, "proj-1")
    # Resume-by-name only works if the seeded session was created NAMED <slug>.
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
