# tests/e2e/test_e2e_dependency.py
"""Live dependency layer: ensure (clone at commit + index, no LLM) then query
through the --git proxy against a tiny public repo; plus a judged
/omc:explain-dependency run against omc's OWN runtime dependency
(questionary), resolved from the repo's pyproject. The wiki/LLM docs path is
NOT re-tested here — `dependency document` shares watch's wiki code path, and
dependency-watch is covered by unit-level argv assertions (per the spec)."""

from __future__ import annotations

import json

import pytest

from .harness import configure_omc, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e

_URL = "https://github.com/pypa/sampleproject.git"
_KEY = "github.com/pypa/sampleproject"


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


def test_dependency_ensure_then_query(container):
    rc, out = run_in(
        container, ["omc", "internal", "dependency", "ensure", "--git", _URL], timeout=600
    )
    assert rc == 0, out
    line = next(ln for ln in out.splitlines() if ln.startswith("OMC_DEPENDENCY "))
    v = json.loads(line.split(" ", 1)[1])
    assert v["ok"] and v["indexed"] and v["key"] == _KEY

    rc, _ = run_in(container, ["test", "-d", v["checkout"] + "/.gitnexus"])
    assert rc == 0, "ensure produced no .gitnexus index in the checkout"

    rc, manifest = run_in(container, ["omc", "internal", "dependency", "list"])
    assert rc == 0 and _KEY in manifest

    rc, out = run_in(
        container, ["omc", "internal", "gitnexus", "--git", _KEY, "query", "main entry point"]
    )
    assert rc == 0, out
    assert '"definitions"' in out or '"processes"' in out, f"no JSON graph output:\n{out[:800]}"

    # second ensure: cached, zero work
    rc, out = run_in(container, ["omc", "internal", "dependency", "ensure", "--git", _URL])
    assert rc == 0
    line = next(ln for ln in out.splitlines() if ln.startswith("OMC_DEPENDENCY "))
    assert json.loads(line.split(" ", 1)[1])["cached"] is True


def test_explain_own_dependency_judged(container):
    # The self-referential flow: /repo is the real omc codebase, whose sole
    # runtime dependency is questionary (pyproject.toml). The skill must
    # resolve the bare name from the project's own manifests, ensure the
    # dependency (clone + index, graph-only — docs stay unbuilt), query its
    # graph, and answer with citations + the status table.
    require_token("claude")
    configure_omc(container, "claude")

    question = "how does questionary implement the select prompt?"
    rc, answer = _claude_skill(
        container, f"/omc:explain-dependency [questionary] {question}", cwd="/repo"
    )
    assert rc == 0, answer

    # Deterministic side effects: the dependency landed in the manifest with
    # an index, without any LLM docs having been generated.
    rc, manifest = run_in(container, ["omc", "internal", "dependency", "list"])
    assert rc == 0, manifest
    assert "/questionary" in manifest, f"questionary not adopted into manifest:\n{manifest[:800]}"
    data = json.loads(manifest)
    key, dep = next(kv for kv in data["dependencies"].items() if kv[0].endswith("/questionary"))
    entry = next(iter(dep["commits"].values()))
    assert entry["indexed"] is True
    rc, _ = run_in(container, ["test", "-d", entry["checkout"] + "/.gitnexus"])
    assert rc == 0, "no .gitnexus index in the questionary checkout"

    verdict = judge(
        container,
        "claude",
        scenario=f"/omc:explain-dependency was asked {question!r} with the hint 'questionary' "
        "inside the omc repo, whose pyproject declares questionary as its only runtime "
        "dependency. The skill should have resolved it to the questionary git repo "
        "(github.com/tmbo/questionary), indexed it at its current commit, and answered from "
        "its knowledge graph (questionary implements select() in questionary/prompts/select.py "
        "on top of prompt_toolkit, using an InquirerControl-style choice layout).",
        rubric=[
            "the answer explains questionary's select prompt using its actual internals "
            "(e.g. select.py, InquirerControl, prompt_toolkit Application/layout)",
            "the answer cites at least one real questionary file or symbol",
            "the answer reports the dependency's indexed/documented status (a status table "
            "or equivalent note that docs are not yet generated)",
            "the answer is not a refusal, an error dump, or a generic essay about prompts",
        ],
        artifacts=answer,
    )
    assert verdict["passed"], verdict["reasons"]
