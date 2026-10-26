"""Malformed real Codex catalogs fail at the E2E setup boundary."""

import json

import pytest

from tests.e2e.test_e2e_task_models import _expected_visible_sol_slug


def test_empty_visible_sol_slug_has_setup_guidance():
    raw = json.dumps(
        {
            "models": [
                {
                    "slug": "",
                    "display_name": "GPT-6 Sol",
                    "priority": 0,
                    "visibility": "list",
                }
            ]
        }
    )
    with pytest.raises(pytest.fail.Exception, match="invalid model list.*update Codex"):
        _expected_visible_sol_slug(raw)
