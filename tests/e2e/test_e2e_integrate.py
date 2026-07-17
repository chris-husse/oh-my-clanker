"""Live /omc:integrate: grounded proposals, judged; headless writes NOTHING."""

from __future__ import annotations

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e


def _seed_project(container, repo):
    script = (
        f"cd {repo} && mkdir -p tests && "
        "printf 'build:\\n    uvx ruff check . && uv run pytest -q\\n' > justfile && "
        "printf 'def test_ok():\\n    assert True\\n' > tests/test_ok.py && "
        "git add -A && git commit -qm 'project scaffold'"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def _integrate(container, repo, extra=""):
    return run_in(
        container,
        [
            "claude",
            "-p",
            f"/omc:integrate {extra}".strip(),
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd=repo,
        timeout=900,
    )


def test_fresh_integrate_proposes_grounded_drafts_and_writes_nothing(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_project(container, repo)

    rc, out = _integrate(container, repo)
    assert rc == 0, out

    # headless = propose-only: NOTHING was written
    rc, leftovers = run_in(
        container,
        ["bash", "-c", f"cd {repo} && git status --porcelain && ls .omc 2>/dev/null"],
    )
    assert not leftovers.strip(), f"headless integrate wrote files:\n{leftovers}\n{out[:1500]}"

    verdict = judge(
        container,
        "claude",
        scenario="/omc:integrate ran headless (propose-only) on a fresh project whose "
        "ONLY build entry point is a justfile running `uvx ruff check` + `uv run "
        "pytest`, with a tests/ dir and no .omc surfaces at all.",
        rubric=[
            "the output inventories the omc surfaces and correctly reports them "
            "missing (skills, wt config, AGENTS chain, index)",
            "the proposed build/verify drafts cite the project's REAL entry point "
            "(the justfile / pytest), not generic boilerplate like npm or make",
            "it does not claim to have written or created any files",
        ],
        artifacts=out,
    )
    assert verdict["passed"], verdict["reasons"]


def test_review_integrate_flags_drifted_build_stage(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_project(container, repo)
    stale = (
        "---\\nname: build\\ndescription: project build stage\\n---\\n\\n"
        "Run `make test`. Non-zero exit means the build FAILED.\\n"
    )
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            f"cd {repo} && mkdir -p .omc/skills/build && "
            f"printf '%b' '{stale}' > .omc/skills/build/SKILL.md && "
            "git add -A && git commit -qm 'stale build stage'",
        ],
    )
    assert rc == 0, out

    rc, out = _integrate(container, repo, "review - the build stage feels wrong")
    assert rc == 0, out

    # the stale file was analyzed, not silently replaced
    rc, content = run_in(container, ["cat", f"{repo}/.omc/skills/build/SKILL.md"])
    assert "make test" in content, "headless integrate must not rewrite project skills"

    verdict = judge(
        container,
        "claude",
        scenario="/omc:integrate ran in review mode on a project whose "
        ".omc/skills/build says `make test`, while the repo has NO Makefile — its "
        "only build entry point is a justfile running ruff + pytest.",
        rubric=[
            "the output flags the drift: the build stage's `make test` does not "
            "match the project's actual justfile/pytest entry point",
            "it proposes a corrected build stage citing the real command",
            "it does not claim to have edited the file (approval-gated writes)",
        ],
        artifacts=out,
    )
    assert verdict["passed"], verdict["reasons"]
