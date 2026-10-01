"""Configure-time documentation probes (spec 2026-10-01 §3.2). http_get is
faked at the ToolContext seam; the claude CLI is a PATH stub."""

import inspect
import json

import pytest

from omc import docsllm
from omc.docsllm import DocsSelection, ModelInfo, ProbeFailed
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


def test_mask_and_redact():
    assert docsllm.mask_key(KEY) == "******1234"
    assert docsllm.mask_key("short") == "********"
    assert docsllm.redact(f"a {KEY} b {KEY}", KEY) == "a ****** b ******"
    assert docsllm.redact("untouched", "") == "untouched"


def test_api_connection_probe_success_sends_headers_and_lists_models(tmp_path):
    ctx, seen, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, MODELS)})
    ok, detail, models = docsllm.api_connection_probe(ctx, "claude", KEY)
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
    ok, detail, models = docsllm.api_connection_probe(ctx, "claude", KEY)
    assert not ok and detail == expected and models == [] and KEY not in detail


def test_error_bodies_are_redacted_before_truncation(tmp_path):
    # A non-JSON error body echoing the key many times: the 200-char cut must
    # never expose even an 8-char slice of it (spec §3.4 redact-before-truncate).
    body = f"<{KEY}>" * 60
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (400, body), "/v1/models/m": (500, body)})
    _, detail, _ = docsllm.api_connection_probe(ctx, "claude", KEY)
    assert "******" in detail and not any(KEY[i : i + 8] in detail for i in range(len(KEY) - 7))
    _, detail = docsllm.api_model_probe(ctx, "claude", KEY, "m")
    assert "******" in detail and not any(KEY[i : i + 8] in detail for i in range(len(KEY) - 7))


def test_model_choices_filters_haiku_and_foreign_sorts_newest_first():
    models = [ModelInfo(m["id"], m["created_at"]) for m in MODELS["data"]]
    assert docsllm.model_choices(models) == [
        "claude-fable-5-1",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-sonnet-5",
    ]


def test_model_choices_tolerates_missing_created_at():
    models = [ModelInfo("claude-sonnet-5-5", ""), ModelInfo("claude-opus-5-5", "2026-04-01")]
    assert docsllm.model_choices(models) == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_resolve_model_alias_to_newest_family_member_full_id_passes_through():
    models = [ModelInfo(m["id"], m["created_at"]) for m in MODELS["data"]]
    assert docsllm.resolve_model("claude", "sonnet", models) == "claude-sonnet-5-5"
    assert docsllm.resolve_model("claude", "opus", models) == "claude-opus-5-5"
    assert docsllm.resolve_model("claude", "claude-sonnet-5", models) == "claude-sonnet-5"
    with pytest.raises(ProbeFailed, match="no sonnet models"):
        docsllm.resolve_model("claude", "sonnet", [ModelInfo("claude-opus-5-5", "x")])


def test_api_model_probe_hits_the_per_id_endpoint(tmp_path):
    ctx, seen, _ = _ctx(
        tmp_path, {"/v1/models/claude-sonnet-5-5": (200, {"id": "claude-sonnet-5-5"})}
    )
    assert docsllm.api_model_probe(ctx, "claude", KEY, "claude-sonnet-5-5") == (
        True,
        "claude-sonnet-5-5 works",
    )
    assert seen[0][0] == "https://api.anthropic.com/v1/models/claude-sonnet-5-5"
    ok, detail = docsllm.api_model_probe(ctx, "claude", KEY, "claude-nope")
    assert not ok and detail == "model 'claude-nope' is not available to this key"


def test_cli_connection_probe_logged_in_and_not(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert docsllm.cli_connection_probe(ctx, "claude") == (True, "claude is logged in")
    assert "auth status" in calls.read_text()
    ctx, _, _ = _ctx(tmp_path / "b", {}, auth_logged_in=False)
    ok, detail = docsllm.cli_connection_probe(ctx, "claude")
    assert not ok and detail == "claude is not logged in — run claude auth login"
    assert "Not logged in" not in detail  # the E2E harness fails on that exact casing
    assert docsllm.cli_connection_probe(ctx, "codex") == (True, "no login check for codex")


def test_cli_model_probe_runs_one_headless_call(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert docsllm.cli_model_probe(ctx, "claude", "opus") == (True, "opus works")
    assert "-p Reply with exactly OK. --output-format text --model opus" in calls.read_text()
    ctx, _, _ = _ctx(tmp_path / "b", {}, headless_rc=1)
    ok, detail = docsllm.cli_model_probe(ctx, "claude", "opus")
    assert not ok and detail.startswith("claude rejected model 'opus'")
    ctx, _, _ = _ctx(tmp_path / "c", {}, headless_reply="")
    ok, detail = docsllm.cli_model_probe(ctx, "claude", "opus")
    assert not ok and detail == "claude returned no output for model 'opus'"


def test_cli_model_probe_works_before_the_omc_home_exists(tmp_path):
    # A fresh machine: `omc configure --set ...docs_model=` probes before any
    # write, so ctx.home (the probe's cwd) may not exist yet. It must not be
    # misreported as "CLI not found on PATH".
    bindir = tmp_path / "bin"
    make_claude_stub(bindir)
    ctx = ToolContext.from_env(stub_env(bindir))
    assert not ctx.home.exists()
    assert docsllm.cli_model_probe(ctx, "claude", "opus") == (True, "opus works")


def test_cli_model_probe_timeout_and_missing_cli(tmp_path, monkeypatch):
    ctx, _, _ = _ctx(tmp_path, {})

    def boom(argv, *, timeout, cwd=None, extra_env=None):
        raise TimeoutError("command timed out after 120s")

    monkeypatch.setattr(ctx, "run_bounded", boom)
    assert docsllm.cli_model_probe(ctx, "claude", "opus") == (
        False,
        "claude did not answer within 120s",
    )
    ctx2 = ToolContext.from_env(stub_env(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    ok, detail = docsllm.cli_model_probe(ctx2, "claude", "opus")
    assert not ok and detail == "claude CLI not found on PATH"


def test_validate_selection_api_resolves_and_narrates(tmp_path):
    ctx, seen, _ = _ctx(
        tmp_path,
        {
            "/v1/models?limit=1000": (200, MODELS),
            "/v1/models/claude-sonnet-5-5": (200, {"id": "claude-sonnet-5-5"}),
        },
    )
    said = []
    model = docsllm.validate_selection(
        ctx, DocsSelection("claude", "api", "", KEY), say=said.append
    )
    assert model == "claude-sonnet-5-5"  # blank docs_model → default family sonnet → newest
    assert said == [
        "→ checking the key against api.anthropic.com",
        "✓ key accepted (6 models)",
        "→ validating claude-sonnet-5-5 via api",
        "✓ claude-sonnet-5-5 works",
    ]
    assert KEY not in "".join(said)


def test_validate_selection_api_failure_raises_redacted(tmp_path):
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (401, {"error": {"message": "x"}})})
    said = []
    with pytest.raises(ProbeFailed, match=r"key rejected by api.anthropic.com \(HTTP 401\)"):
        docsllm.validate_selection(ctx, DocsSelection("claude", "api", "", KEY), say=said.append)
    assert said[-1] == "✗ key rejected by api.anthropic.com (HTTP 401)"


def test_validate_selection_cli_keeps_alias_and_runs_both_probes(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    said = []
    assert (
        docsllm.validate_selection(ctx, DocsSelection("claude", "cli", "opus", ""), say=said.append)
        == "opus"
    )
    log = calls.read_text()
    assert "auth status" in log and "--model opus" in log
    assert said == [
        "→ checking claude login",
        "✓ claude is logged in",
        "→ validating opus via cli",
        "✓ opus works",
    ]


def test_validate_key_only_runs_the_connection_probe_alone(tmp_path):
    ctx, seen, calls = _ctx(tmp_path, {"/v1/models?limit=1000": (200, MODELS)})
    docsllm.validate_key_only(ctx, "claude", KEY, say=lambda m: None)
    assert len(seen) == 1 and not calls.exists()
    ctx, _, _ = _ctx(tmp_path / "b", {"/v1/models?limit=1000": (403, "")})
    with pytest.raises(ProbeFailed):
        docsllm.validate_key_only(ctx, "claude", KEY, say=lambda m: None)


def test_stub_prompt_matches_the_probe_constant():
    # _stubs keeps the prompt as a literal on purpose (it never imports omc);
    # this pins the two together.
    assert docsllm.HEADLESS_PROBE_PROMPT in inspect.getsource(make_claude_stub)


def test_api_connection_probe_null_created_at_and_junk_entries(tmp_path):
    # JSON null must read as "no date" (sorts oldest), not the string "None"
    # (which sorts after every ISO date and would win family resolution).
    body = {
        "data": [
            {"id": "claude-sonnet-5-5", "created_at": "2026-06-01T00:00:00Z"},
            {"id": "claude-sonnet-4", "created_at": None},
            "junk",
            {"created_at": "2026-09-01T00:00:00Z"},
        ]
    }
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, body)})
    ok, detail, models = docsllm.api_connection_probe(ctx, "claude", KEY)
    assert ok and detail == "key accepted (2 models)"
    assert [m.id for m in models] == ["claude-sonnet-5-5", "claude-sonnet-4"]
    assert docsllm.resolve_model("claude", "sonnet", models) == "claude-sonnet-5-5"


@pytest.mark.parametrize("data", ["oops", {"id": "claude-sonnet-5-5"}, None, 3])
def test_api_connection_probe_rejects_a_non_list_data(tmp_path, data):
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, {"data": data})})
    assert docsllm.api_connection_probe(ctx, "claude", KEY) == (
        False,
        "api.anthropic.com returned an unexpected model list",
        [],
    )


@pytest.mark.parametrize(
    "model", ["claude-sonnet-5-5?x=1", "claude/../x", "claude sonnet", "claude#x", ""]
)
def test_resolve_model_refuses_ids_unsafe_in_a_url_path(model):
    models = [ModelInfo("claude-sonnet-5-5", "2026-06-01")]
    with pytest.raises(ProbeFailed, match="invalid model id"):
        docsllm.resolve_model("claude", model, models)


def test_resolve_model_accepts_ordinary_ids():
    for model in ("claude-sonnet-5-5", "claude-3.5:beta_x", "Claude-X-20250101"):
        assert docsllm.resolve_model("claude", model, []) == model


@pytest.mark.parametrize(
    ("stdout", "rc", "expected"),
    [
        ("this is not json", 0, "claude auth status failed (exit 0): this is not json"),
        ("[1, 2]", 0, "claude auth status failed (exit 0): [1, 2]"),
        ('{"loggedIn": true}', 2, 'claude auth status failed (exit 2): {"loggedIn": true}'),
    ],
)
def test_cli_connection_probe_distinguishes_a_broken_auth_status(tmp_path, stdout, rc, expected):
    bindir = tmp_path / "bin"
    make_stub(bindir, "claude", stdout=stdout, rc=rc)
    ctx = ToolContext.from_env(stub_env(bindir))
    ok, detail = docsllm.cli_connection_probe(ctx, "claude")
    assert not ok and detail == expected
    assert "not logged in" not in detail


def test_api_model_probe_refuses_an_unsafe_id_without_a_request(tmp_path):
    # configure's typed "Other" id reaches api_model_probe without resolve_model.
    ctx, seen, _ = _ctx(tmp_path, {})
    assert docsllm.api_model_probe(ctx, "claude", KEY, "claude-x?y=1") == (
        False,
        "invalid model id 'claude-x?y=1'",
    )
    assert seen == []
