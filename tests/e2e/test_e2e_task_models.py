"""Resolve a task family against Codex's real account model list."""

import json
import re

import pytest

from .codex_auth import require_codex_ready
from .harness import configure_omc, require_token, run_in

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("codex")]


def test_codex_family_uses_real_model_list(container):
    require_token("codex")
    pinned = configure_omc(container, "codex")
    require_codex_ready(container)

    # Codex 0.158 refreshes models_cache.json during a no-model exec. This
    # exercises the provider's real list path before omc resolves the family.
    rc, out = run_in(
        container,
        ["codex", "exec", "--skip-git-repo-check", "Reply with exactly READY"],
        timeout=240,
    )
    assert rc == 0 and "READY" in out, out
    rc, raw = run_in(
        container,
        [
            "python3",
            "-c",
            "import json, os, pathlib; "
            "p=pathlib.Path(os.environ['CODEX_HOME'])/'models_cache.json'; "
            "print(json.dumps(json.loads(p.read_text())['models']))",
        ],
    )
    if rc != 0:
        pytest.fail(
            "Codex did not write CODEX_HOME/models_cache.json after a no-model exec; "
            "run `just codex-login`, then retry the Codex gate."
        )
    models = json.loads(raw)
    visible_sol = [
        item
        for item in models
        if item["visibility"] == "list"
        and re.search(r"(?<![A-Za-z0-9])sol(?![A-Za-z0-9])", item["display_name"], re.I)
    ]
    if not visible_sol:
        pytest.fail(
            "The real Codex model list has no visible Sol family for this account. "
            "Update Codex, run `just codex-login` for an account with Sol access, "
            "and rerun `just codex-gate tests/e2e/test_e2e_task_models.py`."
        )
    expected = min(visible_sol, key=lambda item: item["priority"])["slug"]

    rc, out = run_in(
        container, ["omc", "configure", "--set", "llm.providers.codex.tasks.simple=sol"]
    )
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "internal", "models"])
    assert rc == 0 and out.startswith("OMC_MODELS "), out
    verdict = json.loads(out.removeprefix("OMC_MODELS "))
    assert verdict["ok"] is True and verdict["provider"] == "codex", verdict
    assert verdict["tasks"]["simple"] == {"model": expected, "effort": ""}, verdict
    for task in ("orchestrator", "design", "plan", "review", "medium", "high"):
        assert verdict["tasks"][task] == {"model": pinned, "effort": ""}, verdict
