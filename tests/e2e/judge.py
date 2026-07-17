"""LLM judge: headless, no tools, strict JSON verdict. Unparseable output raises."""

from __future__ import annotations

import json

from .harness import run_in

_JUDGE_PROMPT = """You are a strict test judge. Scenario: {scenario}

Rubric — the artifacts must satisfy EVERY point:
{rubric}

Artifacts:
{artifacts}

Reply with ONLY this JSON on one line: {{"passed": true|false, "reasons": ["..."]}}"""

_HEADLESS = {
    "claude": ["claude", "-p", "{prompt}", "--output-format", "text"],
    "codex": ["codex", "exec", "{prompt}"],
    "opencode": ["opencode", "run", "{prompt}"],
}


def judge(container, provider: str, scenario: str, rubric: list[str], artifacts: str) -> dict:
    prompt = _JUDGE_PROMPT.format(
        scenario=scenario,
        rubric="\n".join(f"- {r}" for r in rubric),
        artifacts=artifacts[:20000],
    )
    argv = [a.replace("{prompt}", prompt) for a in _HEADLESS[provider]]
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
