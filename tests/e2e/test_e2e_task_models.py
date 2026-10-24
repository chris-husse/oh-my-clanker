"""Resolve a task family against Codex's real account model list."""

import json
import re

import pytest

from .codex_auth import require_codex_ready
from .harness import configure_omc, require_token, run_in

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("codex")]


def _expected_visible_sol_slug(raw: str) -> str:
    try:
        payload = json.loads(raw)
        models = payload["models"]
        if not isinstance(models, list):
            raise ValueError("models is not a list")
        for item in models:
            if (
                not isinstance(item["slug"], str)
                or not item["slug"]
                or not isinstance(item["priority"], int)
            ):
                raise ValueError("invalid slug or priority")
            if not isinstance(item["visibility"], str) or not isinstance(item["display_name"], str):
                raise ValueError("invalid visibility or display_name")
    except (KeyError, TypeError, ValueError) as exc:
        pytest.fail(
            f"`codex debug models` returned an invalid model list ({exc}); "
            "update Codex, run `just codex-login`, then rerun "
            "`just codex-gate tests/e2e/test_e2e_task_models.py`."
        )
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
    return min(visible_sol, key=lambda item: item["priority"])["slug"]


def test_codex_family_uses_real_model_list(container):
    require_token("codex")
    pinned = configure_omc(container, "codex")
    require_codex_ready(container)

    # Ask the binary omc launches, rather than another client's shared cache.
    rc, raw = run_in(container, ["codex", "debug", "models"], timeout=240, stdout_only=True)
    if rc != 0:
        pytest.fail(
            f"`codex debug models` failed (exit {rc}): {raw[-500:]}; "
            "run `just codex-login`, then rerun "
            "`just codex-gate tests/e2e/test_e2e_task_models.py`."
        )
    expected = _expected_visible_sol_slug(raw)

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
