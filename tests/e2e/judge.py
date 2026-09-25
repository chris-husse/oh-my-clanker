"""LLM judge: headless, no tools, strict JSON verdict. Unparseable output raises."""

from __future__ import annotations

import json
import os

from .harness import run_in

_JUDGE_PROMPT = """You are a strict test judge. Scenario: {scenario}

Rubric — the artifacts must satisfy EVERY point:
{rubric}

Artifacts:
{artifacts}

Reply with ONLY this JSON on one line: {{"passed": true|false, "reasons": ["..."]}}"""

_HEADLESS = {
    "claude": ["claude", "-p", "{prompt}", "--output-format", "text"],
    # The judge runs outside a trusted repo and must override the Docker
    # actor's write policy. Match the lifecycle judge's isolated launch.
    "codex": [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-m",
        "{model}",
        "{prompt}",
    ],  # fmt: skip
}


def judge(container, provider: str, scenario: str, rubric: list[str], artifacts: str) -> dict:
    prompt = _JUDGE_PROMPT.format(
        scenario=scenario,
        rubric="\n".join(f"- {r}" for r in rubric),
        artifacts=artifacts[:20000],
    )
    substitutions = {"{prompt}": prompt}
    if provider == "codex":
        model = os.environ.get("CODEX_E2E_MODEL", "gpt-6-astra")
        assert model, "CODEX_E2E_MODEL must name a real coding model"
        substitutions["{model}"] = model
    argv = [substitutions.get(a, a) for a in _HEADLESS[provider]]
    rc, out = run_in(container, argv, timeout=300)
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and '"passed"' in line:
            try:
                verdict = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(verdict.get("passed"), bool):
                return verdict
    raise AssertionError(f"judge returned no parseable verdict (rc {rc}):\n{out}")
