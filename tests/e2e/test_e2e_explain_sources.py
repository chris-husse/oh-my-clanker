"""Live explain-source layer: global `explain-source` skills under OMC_HOME
feed /omc:explain additive evidence. Two neutral fixtures are written into the
container's `~/.omc/skills/explain-source/`: `echo` returns one fixed evidence
block that contradicts the local graph, `vanished` depends on an MCP tool that
is not registered. The resolver must list both (no LLM); explain must keep the
local answer, surface the echo evidence as a conflict, and report each source's
status exactly once, with `vanished` degrading to an unavailable line instead of
failing the run."""

from __future__ import annotations

import json
import re

import pytest

from .harness import configure_omc, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e

_SOURCES_DIR = "/root/.omc/skills/explain-source"
_ECHO_MARKER = "OMC-ECHO-EVIDENCE-7f3a"
_ECHO_URL = "https://example.invalid/echo-fixture/blob/0123456789abcdef/docs/ECHO.md"

_ECHO_SKILL = f"""---
name: echo
description: Fixture explain source that returns one fixed evidence block.
---

# Echo evidence source

You are an evidence source for omc explain. Call no tool. Do not answer the
question. Return exactly this evidence report, verbatim, and nothing else:

- availability: available
- findings: one finding, cited as `echo-fixture:docs/ECHO.md`
  ({_ECHO_URL}): "{_ECHO_MARKER}: the echo fixture claims omc derives the
  branch slug by flipping a coin." This contradicts the current repository's
  code; the caller keeps the local graph's answer and reports the conflict.
- freshness: indexed commit 0123456789abcdef, distance from the base branch
  unknown
- unknowns: none
- dependency keys: none
- cite-back: none
"""

_VANISHED_SKILL = """---
name: vanished
description: Fixture explain source whose MCP tool is not registered.
---

# Vanished evidence source

You are an evidence source for omc explain. Your only evidence tool is the MCP
tool `mcp__vanished__lookup`. Try to load it (ToolSearch `+vanished lookup`)
and call it with the question. The tool is not registered in this harness, so
when it cannot be loaded or called, stop at once and return exactly:

- availability: unavailable
- reason: MCP server `vanished` is not registered
- fix: register the `vanished` MCP server, then retry

Never answer the question yourself and never invent findings.
"""


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
            "Read",
            # The global sources live outside the repo; headless Claude may
            # only read them when their directory is granted explicitly.
            "--add-dir",
            "/root/.omc",
        ],
        cwd=cwd,
        timeout=timeout,
    )


def _write_global_sources(container) -> None:
    for name, text in (("echo", _ECHO_SKILL), ("vanished", _VANISHED_SKILL)):
        rc, out = run_in(
            container,
            [
                "bash",
                "-c",
                f"mkdir -p {_SOURCES_DIR}/{name} && cat > {_SOURCES_DIR}/{name}/SKILL.md "
                f"<<'OMC_FIXTURE_EOF'\n{text}OMC_FIXTURE_EOF\n",
            ],
        )
        assert rc == 0, f"writing fixture source {name} failed:\n{out}"


def _assert_source_statuses(answer: str) -> None:
    # A prose heading such as "Source conflict:" is not an availability report.
    statuses = [
        ln
        for ln in answer.splitlines()
        if re.match(r"\W*Source \w+:\W*(?:available|unavailable)\b", ln)
    ]
    assert len(statuses) == 2, statuses
    assert re.search(r"Source echo:\W*available", statuses[0]), statuses
    assert re.search(r"Source vanished:\W*unavailable", statuses[1]), statuses
    assert "not registered" in statuses[1], statuses


def test_global_sources_feed_explain_and_fail_independently(container):
    require_token("claude")
    configure_omc(container, "claude")
    _write_global_sources(container)

    # The resolver is the contract explain consumes: both global sources, in
    # name order, as canonical paths. Deterministic, no LLM.
    rc, out = run_in(
        container, ["omc", "internal", "skills", "list", "explain-source"], cwd="/repo"
    )
    assert rc == 0, out
    assert json.loads(out.strip().splitlines()[-1]) == [
        f"{_SOURCES_DIR}/echo/SKILL.md",
        f"{_SOURCES_DIR}/vanished/SKILL.md",
    ]

    rc, out = _claude_skill(container, "/omc:index", cwd="/repo")
    assert rc == 0, out

    question = "how does omc start derive the branch slug?"
    rc, answer = _claude_skill(container, f"/omc:explain {question}", cwd="/repo")
    assert rc == 0, answer

    print(answer)
    # Deterministic shape, per the explain skill's contract: the echo evidence
    # reached the synthesis under its own citation, and each discovered source
    # has exactly one status line — echo available, vanished unavailable with
    # the fixture's reason — in resolver order.
    assert "echo-fixture" in answer or _ECHO_MARKER in answer, "echo evidence missing"
    _assert_source_statuses(answer)

    verdict = judge(
        container,
        "claude",
        scenario=f"/omc:explain answered {question!r} from the project's GitNexus graph "
        "(the repo implements slug derivation in src/omc/slug.py: a headless provider "
        "call runs the packaged slug skill and the CLI parses an OMC_SLUG verdict) "
        "while two global explain sources were configured: `echo`, which returned one "
        f"fixed finding tagged {_ECHO_MARKER} claiming the slug is derived by flipping "
        "a coin (a deliberate contradiction of the code), and `vanished`, whose MCP "
        "tool is not registered. Sources are breadth evidence; the local graph is the "
        "truth for the current repository, and each source's status is reported once.",
        rubric=[
            "the answer's actual explanation describes the real slug flow (headless "
            "provider call with the slug skill and/or OMC_SLUG verdict parsing) and does "
            "NOT present the coin-flip claim as how the code works",
            "the answer cites at least one real file or symbol (e.g. slug.py, fetch_slug, "
            "parse_verdict, start.py)",
            "the answer mentions the echo source's finding and states that it conflicts "
            "with (or is contradicted by) the current repository evidence",
            "the answer reports the echo source as available and the vanished source as "
            "unavailable (with a reason or fix), each exactly once",
            "the answer is not a refusal, an error dump, or a generic essay",
        ],
        artifacts=answer,
    )
    assert verdict["passed"], verdict["reasons"]
