"""Live /omc:finish: squash to one described commit, push. Hermetic — the
origin is a local bare repo, so this also exercises the no-forge fallback."""

from __future__ import annotations

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e


def _make_feature_branch(container, repo):
    script = (
        f"cd {repo} && git switch -qc feature/manual-fix && "
        "echo alpha >> notes.txt && git add -A && git commit -qm 'wip: alpha' && "
        "echo beta >> notes.txt && git commit -qam 'wip: beta'"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_finish_squashes_describes_and_pushes(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _make_feature_branch(container, repo)

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:finish",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )
    assert rc == 0, out

    origin = f"{repo}-origin"
    rc, count = run_in(
        container,
        ["git", "-C", origin, "rev-list", "--count", "main..feature/manual-fix"],
    )
    assert rc == 0 and count.strip() == "1", (
        f"origin should be exactly ONE commit ahead, got {count!r}\ntranscript:\n{out[:2000]}"
    )

    rc, subject = run_in(
        container, ["git", "-C", origin, "log", "-1", "--format=%s", "feature/manual-fix"]
    )
    rc2, body = run_in(
        container, ["git", "-C", origin, "log", "-1", "--format=%b", "feature/manual-fix"]
    )
    assert subject.strip() and not subject.startswith("wip"), subject
    assert len(subject.strip()) <= 100, f"title too long: {subject!r}"
    assert body.strip(), "squashed commit has no description body"

    verdict = judge(
        container,
        "claude",
        scenario="/omc:finish ran on a feature branch with two wip commits over a "
        "local bare origin (no recognizable forge).",
        rubric=[
            "the transcript shows the branch being squashed and pushed",
            "the transcript does NOT create an MR/PR via gh/glab or any API",
            "the transcript ends by offering follow-ups (close worktree / "
            "address review comments / chat), or lists them for the "
            "non-interactive context",
        ],
        artifacts=out,
    )
    assert verdict["passed"], verdict["reasons"]
