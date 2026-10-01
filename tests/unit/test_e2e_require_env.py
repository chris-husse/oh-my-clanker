"""require_env: a stricter gate than require_token — the API-backend E2E needs
ANTHROPIC_API_KEY specifically; an OAuth token alone must FAIL, never skip."""

import pytest

from tests.e2e.harness import require_env


def test_require_env_passes_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    require_env("ANTHROPIC_API_KEY", "put an ANTHROPIC_API_KEY in .env")


@pytest.mark.parametrize("value", [None, ""])
def test_require_env_fails_naming_the_variable(monkeypatch, value):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-only")  # not enough for the API backend
    if value is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    with pytest.raises(
        pytest.fail.Exception, match=r"\$ANTHROPIC_API_KEY.*put an ANTHROPIC_API_KEY"
    ):
        require_env("ANTHROPIC_API_KEY", "put an ANTHROPIC_API_KEY in .env")
