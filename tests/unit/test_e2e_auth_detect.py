"""The e2e auth-failure detector is pure text matching — no Docker needed, so it
lives in the fast tier."""

from tests.e2e.harness import detect_auth_failure

# Captured verbatim from live container runs against omc-e2e:test.
NOT_LOGGED_IN = "Not logged in · Please run /login"
INVALID_TOKEN = "Failed to authenticate. API Error: 401 OAuth access token is invalid."

# The stub Jira MCP's auth-error mode emits this ON PURPOSE and
# test_slug_mcp_unauthenticated depends on it reaching omc's own verdict.
STUB_MCP_AUTH_ERROR = (
    "Authentication failed (HTTP 401): OAuth token expired or revoked. "
    "Re-authenticate this MCP server and retry."
)


def test_detects_missing_credentials():
    remediation = detect_auth_failure(NOT_LOGGED_IN)
    assert remediation is not None
    assert "setup-token" in remediation


def test_detects_invalid_token():
    remediation = detect_auth_failure(INVALID_TOKEN)
    assert remediation is not None
    assert "CLAUDE_CODE_OAUTH_TOKEN" in remediation


def test_ignores_stub_mcp_auth_error():
    """The regression guard: a broader matcher would fire here and break
    test_slug_mcp_unauthenticated."""
    assert detect_auth_failure(STUB_MCP_AUTH_ERROR) is None


def test_ignores_omc_verdicts():
    assert detect_auth_failure("omc: mcp-unauthenticated") is None
    assert detect_auth_failure("omc: context-insufficient") is None


def test_clean_output_is_none():
    assert detect_auth_failure("branch: proj-1-add-rate-limiting\n") is None
    assert detect_auth_failure("") is None
