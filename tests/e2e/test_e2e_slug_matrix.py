import pytest

from .harness import PROVIDERS, configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("provider", PROVIDERS)
def test_slug_ok(container, provider):
    require_token(provider)
    configure_omc(container, provider)
    wire_mcp(container, provider, "ok")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--dry-run"], cwd=repo)
    assert rc == 0, out
    assert "branch:" in out and "proj-1" in out, out  # slug derived from the fixture ticket


@pytest.mark.parametrize("provider", PROVIDERS)
def test_slug_mcp_unauthenticated(container, provider):
    require_token(provider)
    configure_omc(container, provider)
    wire_mcp(container, provider, "auth-error")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--dry-run"], cwd=repo)
    assert rc == 2, out
    assert "mcp-unauthenticated" in out, out


@pytest.mark.parametrize("provider", PROVIDERS)
def test_slug_mcp_missing(container, provider):
    require_token(provider)
    configure_omc(container, provider)
    wire_mcp(container, provider, "absent")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--dry-run"], cwd=repo)
    assert rc == 2, out
    assert "mcp-missing" in out, out


@pytest.mark.parametrize("provider", PROVIDERS)
def test_slug_free_text_description_needs_no_tracker(container, provider):
    require_token(provider)
    configure_omc(container, provider)
    repo = make_work_repo(container)
    rc, out = run_in(
        container,
        ["omc", "start", "add rate limiting to the public API", "--dry-run"],
        cwd=repo,
    )
    assert rc == 0, out
    assert "rate" in out and "limit" in out, out


def test_slug_context_insufficient_claude(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "start", "stuff", "--dry-run"], cwd=repo)
    assert rc == 2, out
    assert "context-insufficient" in out, out
