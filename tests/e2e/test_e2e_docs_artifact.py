"""The documentation system, judged — with a PERMANENT committed artifact.

`tests/e2e/artifacts/omc-wiki/` holds the generated wiki for THIS repo. The
test seeds the container's primary root with it, runs /omc:document (an
incremental update over the existing wiki state rather than a from-scratch
generation), syncs the refreshed wiki back OUT to the artifact dir (its git
diff is reviewable), and an LLM judge verifies the docs are genuinely about
omc and deep enough that the pipeline demonstrably works.

Marked `expensive`: wiki generation is one LLM call per module. Run via
`just expensive-e2e-tests`, only with explicit user agreement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .harness import configure_omc, require_token, run_in
from .judge import judge

pytestmark = [pytest.mark.e2e, pytest.mark.expensive]

ARTIFACTS = Path(__file__).parent / "artifacts" / "omc-wiki"


def test_document_updates_artifact_and_docs_are_judged(container_with_artifacts):
    container = container_with_artifacts
    require_token("claude")
    configure_omc(container, "claude")

    # Seed the container with the committed artifact (empty on first ever run).
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            'mkdir -p /repo/.gitnexus && if [ -n "$(ls -A /artifacts/omc-wiki 2>/dev/null)" ]; '
            "then cp -R /artifacts/omc-wiki /repo/.gitnexus/wiki; fi",
        ],
    )
    assert rc == 0, out

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:index",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd="/repo",
        timeout=900,
    )
    assert rc == 0, out

    rc, out = run_in(
        container,
        [
            "claude",
            "-p",
            "/omc:document",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd="/repo",
        timeout=2400,
    )
    assert rc == 0, out

    # Sync the refreshed wiki back OUT into the permanent artifact.
    rc, sync_out = run_in(
        container,
        [
            "bash",
            "-c",
            "test -d /repo/.gitnexus/wiki && rm -rf /artifacts/omc-wiki && "
            "cp -R /repo/.gitnexus/wiki /artifacts/omc-wiki && ls /artifacts/omc-wiki | head -20",
        ],
    )
    assert rc == 0 and sync_out.strip(), f"no wiki produced:\n{out[:2000]}"

    pages = sorted(ARTIFACTS.glob("*.md"))
    assert pages, "artifact has no markdown pages after sync-back"
    # Deliberately truncated excerpts — the judge is told so, and judges
    # topicality/depth, never completeness of the excerpt itself.
    sample = "\n\n---PAGE (excerpt)---\n\n".join(p.read_text()[:4000] for p in pages[:3])

    verdict = judge(
        container,
        "claude",
        scenario="GitNexus generated architecture documentation for the omc repo "
        "(a Python CLI + multi-harness skills plugin: start/slug/finish skills, "
        "provider adapters, ToolContext subprocess boundary, Docker E2E harness). "
        f"You are shown TRUNCATED EXCERPTS of the first 3 of {len(pages)} pages — "
        "mid-sentence cutoffs are the sampling, NOT a documentation defect; judge "
        "topicality and depth only.",
        rubric=[
            "the documentation is specifically about the omc codebase — it names "
            "real modules, skills, or flows (e.g. start/slug/providers/toolctx/"
            "watch, OMC_SLUG verdicts, worktrees), not a generic project",
            "it goes deeper than boilerplate: at least one page explains how "
            "components interact (a flow, a pipeline, a contract)",
            "it does not contain obvious hallucinations about the repo's language or purpose",
        ],
        artifacts=sample,
    )
    assert verdict["passed"], verdict["reasons"]
