"""Model probes for reconciliation and the task-model pickers. http_get is
faked at the ToolContext seam; the claude CLI is a PATH stub."""

import inspect
import json

import pytest

from omc import modelprobe
from omc.modelprobe import ProbeFailed
from omc.toolctx import ToolContext

from ._stubs import make_claude_stub, make_stub, stub_env

KEY = "sk-ant-api03-SECRETKEYabcdefghijklmnop1234"
MODELS = {
    "data": [
        {"id": "claude-sonnet-5", "created_at": "2026-02-01T00:00:00Z"},
        {"id": "claude-haiku-4-5", "created_at": "2026-05-01T00:00:00Z"},
        {"id": "claude-sonnet-5-5", "created_at": "2026-06-01T00:00:00Z"},
        {"id": "claude-opus-5-5", "created_at": "2026-04-01T00:00:00Z"},
        {"id": "claude-fable-5-1", "created_at": "2026-08-01T00:00:00Z"},
        {"id": "legacy-thing", "created_at": "2026-09-01T00:00:00Z"},
    ],
    "has_more": True,
}


def _ctx(tmp_path, responses, **stub_kwargs):
    """ctx whose http_get answers from `responses` {url-suffix: (status, body)} and
    records every call; claude is a PATH stub."""
    bindir = tmp_path / "bin"
    calls = make_claude_stub(bindir, **stub_kwargs)
    (bindir.parent / ".omc").mkdir(exist_ok=True)  # ctx.home: cli_model_probe's cwd
    ctx = ToolContext.from_env(stub_env(bindir))
    seen = []

    def fake_http_get(url, *, headers=None, timeout=30.0):
        seen.append((url, dict(headers or {})))
        for suffix, (status, body) in responses.items():
            if url.endswith(suffix):
                return status, body if isinstance(body, str) else json.dumps(body)
        return 404, '{"error":{"message":"no route"}}'

    ctx.http_get = fake_http_get  # type: ignore[method-assign]
    return ctx, seen, calls


def test_redact():
    assert modelprobe.redact(f"a {KEY} b {KEY}", KEY) == "a ****** b ******"
    assert modelprobe.redact("untouched", "") == "untouched"


def test_api_connection_probe_success_sends_headers_and_lists_models(tmp_path):
    ctx, seen, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, MODELS)})
    ok, detail, models = modelprobe.api_connection_probe(ctx, "claude", KEY)
    assert ok and detail == "key accepted (6 models)"
    assert [m.id for m in models][:2] == ["claude-sonnet-5", "claude-haiku-4-5"]
    url, headers = seen[0]
    assert url == "https://api.anthropic.com/v1/models?limit=1000"  # no "//"
    assert headers == {"x-api-key": KEY, "anthropic-version": "2023-06-01"}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            401,
            {"error": {"message": "invalid x-api-key"}},
            "key rejected by api.anthropic.com (HTTP 401)",
        ),
        (403, {"error": {"message": "forbidden"}}, "key rejected by api.anthropic.com (HTTP 403)"),
        (
            400,
            {"error": {"message": "anthropic-workspace-id header is required"}},
            "api.anthropic.com answered HTTP 400: anthropic-workspace-id header is required",
        ),
        (0, f"<urlopen error> {KEY}", "could not reach api.anthropic.com: <urlopen error> ******"),
        (200, "not json", "api.anthropic.com returned an unexpected model list"),
    ],
)
def test_api_connection_probe_failures_are_redacted(tmp_path, status, body, expected):
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (status, body)})
    ok, detail, models = modelprobe.api_connection_probe(ctx, "claude", KEY)
    assert not ok and detail == expected and models == [] and KEY not in detail


def test_error_bodies_are_redacted_before_truncation(tmp_path):
    # A non-JSON error body echoing the key many times: the 200-char cut must
    # never expose even an 8-char slice of it (spec §3.4 redact-before-truncate).
    body = f"<{KEY}>" * 60
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (400, body)})
    _, detail, _ = modelprobe.api_connection_probe(ctx, "claude", KEY)
    assert "******" in detail and not any(KEY[i : i + 8] in detail for i in range(len(KEY) - 7))


def test_cli_model_probe_runs_one_headless_call(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert modelprobe.cli_model_probe(ctx, "claude", "opus") == (True, "opus works")
    assert "-p Reply with exactly OK. --output-format text --model opus" in calls.read_text()
    ctx, _, _ = _ctx(tmp_path / "b", {}, headless_rc=1)
    ok, detail = modelprobe.cli_model_probe(ctx, "claude", "opus")
    assert not ok and detail.startswith("claude rejected model 'opus'")
    ctx, _, _ = _ctx(tmp_path / "c", {}, headless_reply="")
    ok, detail = modelprobe.cli_model_probe(ctx, "claude", "opus")
    assert not ok and detail == "claude returned no output for model 'opus'"


def test_cli_model_probe_accepts_prompt_effort_and_trimmed_output(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {}, headless_reply="  claude-fable-5-1  ")
    assert modelprobe.cli_model_probe(
        ctx,
        "claude",
        "fable",
        prompt="Reply with only your exact model id",
        effort="high",
        return_output=True,
    ) == (True, "claude-fable-5-1")
    assert (
        "-p Reply with only your exact model id --output-format text --model fable --effort high"
        in calls.read_text()
    )


def test_cli_model_probe_works_before_the_omc_home_exists(tmp_path):
    # A fresh machine: `omc configure --set ...tasks.plan=` probes before any
    # write, so ctx.home (the probe's cwd) may not exist yet. It must not be
    # misreported as "CLI not found on PATH".
    bindir = tmp_path / "bin"
    make_claude_stub(bindir)
    ctx = ToolContext.from_env(stub_env(bindir))
    assert not ctx.home.exists()
    assert modelprobe.cli_model_probe(ctx, "claude", "opus") == (True, "opus works")


def test_cli_model_probe_timeout_and_missing_cli(tmp_path, monkeypatch):
    ctx, _, _ = _ctx(tmp_path, {})

    def boom(argv, *, timeout, cwd=None, extra_env=None):
        raise TimeoutError("command timed out after 120s")

    monkeypatch.setattr(ctx, "run_bounded", boom)
    assert modelprobe.cli_model_probe(ctx, "claude", "opus") == (
        False,
        "claude did not answer within 120s",
    )
    ctx2 = ToolContext.from_env(stub_env(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    ok, detail = modelprobe.cli_model_probe(ctx2, "claude", "opus")
    assert not ok and detail == "claude CLI not found on PATH"


def test_stub_prompt_matches_the_probe_constant():
    # _stubs keeps the prompt as a literal on purpose (it never imports omc);
    # this pins the two together.
    assert modelprobe.HEADLESS_PROBE_PROMPT in inspect.getsource(make_claude_stub)


def test_api_connection_probe_null_created_at_and_junk_entries(tmp_path):
    # JSON null must read as "no date", not the string "None".
    body = {
        "data": [
            {"id": "claude-sonnet-5-5", "created_at": "2026-06-01T00:00:00Z"},
            {"id": "claude-sonnet-4", "created_at": None},
            "junk",
            {"created_at": "2026-09-01T00:00:00Z"},
        ]
    }
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, body)})
    ok, detail, models = modelprobe.api_connection_probe(ctx, "claude", KEY)
    assert ok and detail == "key accepted (2 models)"
    assert [m.id for m in models] == ["claude-sonnet-5-5", "claude-sonnet-4"]
    assert models[1].created_at == ""


@pytest.mark.parametrize("data", ["oops", {"id": "claude-sonnet-5-5"}, None, 3])
def test_api_connection_probe_rejects_a_non_list_data(tmp_path, data):
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, {"data": data})})
    assert modelprobe.api_connection_probe(ctx, "claude", KEY) == (
        False,
        "api.anthropic.com returned an unexpected model list",
        [],
    )


def test_auth_status_returns_current_org_and_config_directory(tmp_path):
    config_dir = tmp_path / "claude-settings"
    status = {
        "loggedIn": True,
        "orgId": "org-current",
        "configDirectory": str(config_dir),
        "authMethod": "claude.ai",
    }
    bindir = tmp_path / "bin"
    make_stub(bindir, "claude", stdout=json.dumps(status))
    ctx = ToolContext.from_env(stub_env(bindir))
    assert modelprobe.auth_status(ctx, "claude") == status


@pytest.mark.parametrize(
    ("stdout", "rc", "message"),
    [
        ('{"loggedIn": false}', 0, "claude is not logged in — run claude auth login"),
        ("broken", 0, "claude auth status failed (exit 0): broken"),
        ("[1, 2]", 0, "claude auth status failed (exit 0): [1, 2]"),  # JSON, but not an object
        ('{"loggedIn": true}', 2, 'claude auth status failed (exit 2): {"loggedIn": true}'),
    ],
)
def test_auth_status_raises_the_existing_probe_failure_message(tmp_path, stdout, rc, message):
    bindir = tmp_path / "bin"
    make_stub(bindir, "claude", stdout=stdout, rc=rc)
    ctx = ToolContext.from_env(stub_env(bindir))
    with pytest.raises(ProbeFailed) as exc:
        modelprobe.auth_status(ctx, "claude")
    assert str(exc.value) == message


def test_cli_model_probe_forwards_served_catalog_to_the_argv(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert modelprobe.cli_model_probe(ctx, "claude", "", served_catalog=True) == (
        True,
        "default model works",
    )
    assert calls.read_text().strip() == (
        "-p Reply with exactly OK. --output-format text --settings "
        '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":""}}'
    )
    ctx, _, calls = _ctx(tmp_path / "plain", {})
    assert modelprobe.cli_model_probe(ctx, "claude", "") == (True, "default model works")
    assert "--settings" not in calls.read_text()


@pytest.mark.parametrize("bearer", [True, False], ids=["bearer", "x-api-key"])
def test_header_validation_failure_keeps_key_redacted(tmp_path, monkeypatch, bearer):
    """A CRLF `.env` leaves a trailing \\r on the key: http.client rejects the
    header with a message that carries the repr-escaped value. The REAL
    http_get runs; validation fails before any connection is opened."""
    import socket

    secret = "sk-ant-oat01-SECRETTOKENabcdefghijklmnop1234"

    def no_network(*_a, **_k):
        pytest.fail("header validation must fail before any connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    ok, detail, models = modelprobe.api_connection_probe(
        ctx, "claude", secret + "\r", bearer=bearer
    )
    assert (ok, models) == (False, [])
    assert detail.startswith("could not reach api.anthropic.com")
    assert "SECRET" not in detail
