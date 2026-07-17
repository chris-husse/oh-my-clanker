"""Live gitnexus layer: index -> explain -> document against /repo (the real
omc codebase baked into the image). The GitNexus dependency itself is
pre-baked into the image at /root/.omc/dependencies/gitnexus (see
Dockerfile.e2e), so gitnexus-ensure exercises its verify path per test."""

from __future__ import annotations

import pytest

from .harness import configure_omc, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e

_CLI = "/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js"


def _claude_skill(container, prompt, *, cwd, timeout=900):
    return run_in(
        container,
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd=cwd,
        timeout=timeout,
    )


def test_index_then_explain_on_real_repo(container):
    require_token("claude")
    configure_omc(container, "claude")

    rc, _ = run_in(container, ["node", _CLI, "--version"])
    assert rc == 0, "pre-baked GitNexus CLI missing from image"

    rc, out = _claude_skill(container, "/omc:index", cwd="/repo")
    assert rc == 0, out
    rc, _ = run_in(container, ["test", "-d", "/repo/.gitnexus"])
    assert rc == 0, f"analyze produced no .gitnexus/ index:\n{out[:2000]}"
    rc, listed = run_in(container, ["node", _CLI, "list"], cwd="/repo")
    assert rc == 0 and "repo" in listed, f"repo not in gitnexus registry:\n{listed[:800]}"

    question = "how does omc start derive the branch slug?"
    rc, answer = _claude_skill(container, f"/omc:explain {question}", cwd="/repo")
    assert rc == 0, answer
    verdict = judge(
        container,
        "claude",
        scenario=f"/omc:explain answered {question!r} using the project's GitNexus "
        "knowledge graph (the repo implements slug derivation in src/omc/slug.py: "
        "a headless provider call runs the packaged slug skill and the CLI parses "
        "an OMC_SLUG verdict, re-sanitizes, and prefixes the branch).",
        rubric=[
            "the answer describes the actual slug flow (headless provider call with "
            "the slug skill and/or OMC_SLUG verdict parsing / fetch_slug)",
            "the answer cites at least one real file or symbol (e.g. slug.py, "
            "fetch_slug, parse_verdict, start.py)",
            "the answer is not a refusal, an error dump, or a generic essay",
        ],
        artifacts=answer,
    )
    assert verdict["passed"], verdict["reasons"]


def test_document_generates_wiki_docs(container):
    require_token("claude")
    configure_omc(container, "claude")

    rc, out = _claude_skill(container, "/omc:index", cwd="/repo")
    assert rc == 0, out

    rc, out = _claude_skill(container, "/omc:document", cwd="/repo", timeout=1800)
    assert rc == 0, out

    rc, listing = run_in(
        container,
        ["bash", "-c", "ls /repo/.omc/docs/gitnexus/docs/*.md 2>/dev/null | head -5"],
    )
    assert rc == 0 and listing.strip(), (
        f"no markdown docs landed in .omc/docs/gitnexus/docs:\n{out[:2000]}"
    )
