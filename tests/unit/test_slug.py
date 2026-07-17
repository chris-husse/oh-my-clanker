import pytest

from omc.config.schema import Config
from omc.errors import OmcError, Refusal
from omc.slug import build_prompt, fetch_slug, parse_verdict, sanitize_slug
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env


def test_sanitize_slug():
    assert sanitize_slug("Fix: Login Timeout!") == "fix-login-timeout"
    assert sanitize_slug("a\nb") == "a-b"
    assert sanitize_slug("-" * 5) == ""
    assert len(sanitize_slug("x" * 99)) <= 50
    assert not sanitize_slug("x" * 99).endswith("-")


def test_parse_verdict_ok_and_fail_and_last_wins():
    ok = parse_verdict('noise\nOMC_SLUG {"ok": true, "slug": "a-b"}')
    assert ok.ok and ok.slug == "a-b"
    bad = parse_verdict(
        'OMC_SLUG {"ok": true, "slug": "x"}\n'
        'OMC_SLUG {"ok": false, "reason": "mcp-missing", "message": "add jira"}'
    )
    assert not bad.ok and bad.reason == "mcp-missing" and bad.message == "add jira"
    assert parse_verdict("no verdict here") is None
    assert parse_verdict("OMC_SLUG {broken") is None


def test_build_prompt_substitutes_and_strips_frontmatter():
    p = build_prompt("PROJ-9 do a thing")
    assert "PROJ-9 do a thing" in p
    assert "$ARGUMENTS" not in p
    assert not p.startswith("---")  # frontmatter stripped


def _ctx_with_claude_stub(tmp_path, verdict_line: str, rc: int = 0):
    bindir = tmp_path / "bin"
    make_stub(bindir, "claude", stdout=verdict_line, rc=rc)
    return ToolContext.from_env(stub_env(bindir))


def test_fetch_slug_ok(tmp_path):
    ctx = _ctx_with_claude_stub(tmp_path, 'OMC_SLUG {"ok": true, "slug": "Proj-1-Fix!"}')
    assert fetch_slug(ctx, Config(), "PROJ-1") == "proj-1-fix"  # re-sanitized by the CLI


def test_fetch_slug_refusal_carries_message(tmp_path):
    ctx = _ctx_with_claude_stub(
        tmp_path,
        'OMC_SLUG {"ok": false, "reason": "mcp-unauthenticated", "message": "run /mcp auth"}',
    )
    with pytest.raises(Refusal, match="run /mcp auth"):
        fetch_slug(ctx, Config(), "PROJ-1")


def test_fetch_slug_unparseable_is_error(tmp_path):
    ctx = _ctx_with_claude_stub(tmp_path, "garbage")
    with pytest.raises(OmcError, match="no OMC_SLUG verdict"):
        fetch_slug(ctx, Config(), "PROJ-1")


def test_fetch_slug_empty_slug_is_error(tmp_path):
    ctx = _ctx_with_claude_stub(tmp_path, 'OMC_SLUG {"ok": true, "slug": "!!!"}')
    with pytest.raises(OmcError, match="empty slug"):
        fetch_slug(ctx, Config(), "PROJ-1")


def test_mcp_tool_patterns_are_server_scoped():
    # Verified live (spec §10.2): claude -p honors server-scoped grants like
    # `mcp__jira`, but NOT a global `mcp__*` glob — keep patterns glob-free.
    from omc.slug import MCP_TOOL_PATTERNS

    assert MCP_TOOL_PATTERNS
    for pat in MCP_TOOL_PATTERNS:
        assert pat.startswith("mcp__") and "*" not in pat
