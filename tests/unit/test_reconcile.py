"""Contracts for the default-model remap prompt and its atomic answer parser."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from omc import reconcile as under_test
from omc.config import store
from omc.config.schema import Config, GlobalConfig, LLMConfig, ProviderConfig, SecretsConfig
from omc.errors import ConfigError, OmcError
from omc.providers.registry import docs_llm_for
from omc.reconcile import StaleLeaf, build_remap_prompt, parse_remap_output
from omc.taskmodels import CatalogModel
from omc.toolctx import ToolContext

REVIEW = StaleLeaf("tasks.review", "codex", "sol:high", "Review", "high", "cli")
MODEL = StaleLeaf("model", "codex", "astra", "Orchestrator", "", "cli")
DOCS = StaleLeaf("docs_model", "claude", "sonnet", "Documentation model", "", "api")
CODEX_OFFERED = {
    "tasks.review": [CatalogModel("gpt-6-sol", "sol", ("medium", "high"))],
    "model": [CatalogModel("gpt-6-astra", "astra", ("high", "xhigh"))],
}
# GET /v1/models lists IDs but no effort data: efforts are unknown (None).
DOCS_OFFERED = {"docs_model": [CatalogModel("claude-sonnet-5-5", "sonnet", None)]}
API_ORCHESTRATOR = StaleLeaf("model", "claude", "claude-opus-4-1:max", "Orchestrator", "max", "cli")
API_OFFERED = {"model": [CatalogModel("claude-opus-5-5", "opus", None)]}


def test_prompt_names_each_same_provider_leaf_and_its_own_catalog_without_secret():
    prompt = build_remap_prompt([REVIEW, MODEL], CODEX_OFFERED)

    assert "one json object" in prompt.lower()
    assert '"tasks.review"' in prompt and '"model"' in prompt
    assert "Review" in prompt and "sol:high" in prompt
    assert "Orchestrator" in prompt and "astra" in prompt
    review_section = prompt.split('"tasks.review"', 1)[1].split('"model"', 1)[0]
    model_section = prompt.split('"model"', 1)[1]
    assert "gpt-6-sol" in review_section and "sol" in review_section
    assert "medium" in review_section and "high" in review_section
    assert "gpt-6-astra" not in review_section
    assert "gpt-6-astra" in model_section and "xhigh" in model_section
    assert "gpt-6-sol" not in model_section
    assert "api_key" not in prompt and "secret" not in prompt.lower()


def test_docs_prompt_excludes_effort_even_when_catalog_lists_it():
    leaf = StaleLeaf("docs_model", "claude", "claude-sonnet-4-1", "Documentation model", "", "cli")
    offered = {"docs_model": [CatalogModel("claude-sonnet-5-5", "sonnet", ("high",))]}

    prompt = build_remap_prompt([leaf], offered)

    assert '"docs_model"' in prompt
    assert "no :effort" in prompt


def test_prompt_marks_unknown_efforts_distinctly_from_none_supported():
    prompt = build_remap_prompt([API_ORCHESTRATOR], API_OFFERED)

    assert '"efforts": "unknown"' in prompt
    assert '"efforts": []' not in prompt


def test_parser_accepts_effort_on_model_with_unknown_efforts():
    output = '{"model": "claude-opus-5-5:max"}'

    assert parse_remap_output(output, [API_ORCHESTRATOR], API_OFFERED) == {
        "model": "claude-opus-5-5:max"
    }


def test_parser_validates_effort_statically_when_catalog_efforts_unknown():
    with pytest.raises(ConfigError) as caught:
        parse_remap_output('{"model": "claude-opus-5-5:turbo"}', [API_ORCHESTRATOR], API_OFFERED)

    message = str(caught.value)
    assert "claude-opus-5-5:turbo" in message and "effort must be one of" in message


def test_parser_accepts_one_json_object_surrounded_by_cli_lines():
    output = 'Codex is working\n{"tasks.review": "gpt-6-sol:high", "model": "gpt-6-astra"}\nDone'

    assert parse_remap_output(output, [REVIEW, MODEL], CODEX_OFFERED) == {
        "tasks.review": "gpt-6-sol:high",
        "model": "gpt-6-astra",
    }


@pytest.mark.parametrize(
    ("answer", "bad_leaf", "bad_answer"),
    [
        ('{"tasks.review": "gpt-6-sol:high"}', "model", "missing"),
        (
            '{"tasks.review": "gpt-6-sol:high", "model": "gpt-6-astra", "extra": "x"}',
            "extra",
            "x",
        ),
        ('{"tasks.review": "gpt-6-sol:high", "model": ', "tasks.review", "{"),
        ('{"tasks.review": "gpt-5-sol", "model": "gpt-6-astra"}', "tasks.review", "gpt-5-sol"),
        (
            '{"tasks.review": "gpt-6-luna", "model": "gpt-6-astra"}',
            "tasks.review",
            "gpt-6-luna",
        ),
        (
            '{"tasks.review": "gpt-6-sol:ultra", "model": "gpt-6-astra"}',
            "tasks.review",
            "gpt-6-sol:ultra",
        ),
        (
            '{"tasks.review": "gpt-6-sol:", "model": "gpt-6-astra"}',
            "tasks.review",
            "gpt-6-sol:",
        ),
    ],
)
def test_parser_rejects_bad_task_answer_with_leaf_answer_and_offered_ids(
    answer, bad_leaf, bad_answer
):
    with pytest.raises(ConfigError) as caught:
        parse_remap_output(answer, [REVIEW, MODEL], CODEX_OFFERED)

    message = str(caught.value)
    assert bad_leaf in message and bad_answer in message
    offered_ids = message.partition("offered IDs: ")[2]
    if bad_leaf == "extra" or bad_answer == "{":
        assert offered_ids == "gpt-6-sol, gpt-6-astra"
    else:
        assert offered_ids == CODEX_OFFERED[bad_leaf][0].id


def test_wrong_leaf_catalog_id_error_offers_only_that_leafs_ids():
    output = '{"tasks.review": "gpt-6-astra", "model": "gpt-6-astra"}'

    with pytest.raises(ConfigError) as caught:
        parse_remap_output(output, [REVIEW, MODEL], CODEX_OFFERED)

    message = str(caught.value)
    assert "tasks.review" in message and "gpt-6-astra" in message
    assert message.partition("offered IDs: ")[2] == "gpt-6-sol"


@pytest.mark.parametrize("answer", ["claude-sonnet-4-0", "claude-haiku-5-5"])
def test_parser_rejects_unlisted_docs_id(answer):
    with pytest.raises(ConfigError) as caught:
        parse_remap_output(json.dumps({"docs_model": answer}), [DOCS], DOCS_OFFERED)

    message = str(caught.value)
    assert "docs_model" in message and answer in message and "claude-sonnet-5-5" in message


def test_parser_rejects_malformed_docs_id_even_when_listed():
    offered = {"docs_model": [CatalogModel("bad/id", "", ())]}

    with pytest.raises(ConfigError) as caught:
        parse_remap_output('{"docs_model": "bad/id"}', [DOCS], offered)

    message = str(caught.value)
    assert "docs_model" in message and "invalid model id 'bad/id'" in message
    assert "offered IDs: bad/id" in message


def test_parser_does_not_return_valid_leaf_when_another_is_bad():
    with pytest.raises(ConfigError):
        parse_remap_output(
            '{"tasks.review": "gpt-6-sol:high", "model": "gpt-5-astra"}',
            [REVIEW, MODEL],
            CODEX_OFFERED,
        )


def test_parser_rejects_cheap_id_even_if_it_is_accidentally_offered():
    offered = {"tasks.review": [CatalogModel("gpt-6-luna", "luna", ("high",))]}
    with pytest.raises(ConfigError) as caught:
        parse_remap_output('{"tasks.review": "gpt-6-luna"}', [REVIEW], offered)
    message = str(caught.value)
    assert "tasks.review" in message and "gpt-6-luna" in message
    # validate_task_model's provider-aware family ban is the one rejecting check.
    assert "cheap/fast family is not allowed" in message


def test_parser_rejects_more_than_one_json_object():
    output = '{"docs_model": "claude-sonnet-5-5"}\n{"docs_model": "claude-sonnet-5-5"}'
    with pytest.raises(ConfigError, match="docs_model"):
        parse_remap_output(output, [DOCS], DOCS_OFFERED)


def test_docs_effort_must_be_supported_by_the_listed_model():
    with pytest.raises(ConfigError) as caught:
        parse_remap_output('{"docs_model": "claude-sonnet-5-5:high"}', [DOCS], DOCS_OFFERED)
    assert "docs_model" in str(caught.value) and "claude-sonnet-5-5:high" in str(caught.value)


def test_docs_remap_rejects_effort_even_when_catalog_supports_it():
    leaf = StaleLeaf("docs_model", "claude", "claude-sonnet-4-1", "Documentation model", "", "cli")
    offered = {"docs_model": [CatalogModel("claude-sonnet-5-5", "sonnet", ("high",))]}

    with pytest.raises(ConfigError) as caught:
        parse_remap_output('{"docs_model": "claude-sonnet-5-5:high"}', [leaf], offered)

    message = str(caught.value)
    assert "docs_model" in message and "claude-sonnet-5-5:high" in message
    assert "effort" in message and "claude-sonnet-5-5" in message


@pytest.mark.parametrize(
    "output",
    [
        '{"docs_model": "claude-sonnet-5-5", "docs_model": "claude-sonnet-5-5"}',
        '{"docs_model": "claude-sonnet-5-5"} trailing garbage',
    ],
)
def test_parser_rejects_ambiguous_or_trailing_json_content(output):
    with pytest.raises(ConfigError, match="docs_model"):
        parse_remap_output(output, [DOCS], DOCS_OFFERED)


def _context(tmp_path: Path) -> ToolContext:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    return ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc"), "PATH": str(bindir)}
    )


def _config(ctx: ToolContext, provider: str = "codex", **leaves: str) -> Config:
    pcfg = ProviderConfig()
    for path, value in leaves.items():
        if path.startswith("tasks_"):
            setattr(pcfg.tasks, path.removeprefix("tasks_"), value)
        else:
            setattr(pcfg, path, value)
    store.save_global(
        ctx.home, GlobalConfig(llm=LLMConfig(default=provider, providers={provider: pcfg}))
    )
    loaded = store.load_global(ctx.home)
    assert loaded is not None
    return Config(llm=loaded.llm)


def _codex_catalog(ctx: ToolContext, models: list[tuple[str, str, tuple[str, ...]]]) -> Path:
    path = Path(ctx.env["HOME"]) / ".codex" / "models_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": model_id,
                        "display_name": f"GPT {family.title()}",
                        "visibility": "list",
                        "priority": index,
                        "supported_reasoning_levels": [
                            {"effort": effort, "description": effort.title()} for effort in efforts
                        ],
                    }
                    for index, (model_id, family, efforts) in enumerate(models)
                ]
            }
        )
    )
    return path


CODEX_CURRENT = [
    ("gpt-6-astra", "astra", ("medium", "high")),
    ("gpt-6-sol", "sol", ("medium", "high")),
]
CLAUDE_CURRENT = [
    ("claude-opus-5-5", "opus", ("medium", "high")),
    ("claude-fable-5-5", "fable", ("medium", "high")),
    ("claude-sonnet-5-5", "sonnet", ("medium", "high")),
]


def _claude_catalog(ctx: ToolContext, models=CLAUDE_CURRENT) -> Path:
    path = Path(ctx.env["HOME"]) / "claude-state" / "cache" / "model-catalog" / "org-main-a-cc.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetchedAt": "2026-10-15T12:00:00Z",
                "catalog": {
                    "config": {
                        "models": [
                            {
                                "id": model_id,
                                "short_name": family.title(),
                                "thinking": {
                                    "effort_options": [{"id": effort} for effort in efforts]
                                },
                            }
                            for model_id, family, efforts in models
                        ]
                    }
                },
            }
        )
    )
    return path


def _cli_stub(
    ctx: ToolContext,
    provider: str,
    *,
    auth_status: dict[str, object] | None = None,
    refreshed: tuple[Path, str] | None = None,
    refresh_output: str = "OK",
    refresh_rc: int = 0,
    remap_output: str = "",
    remap_rc: int = 0,
    remap_edit: tuple[Path, str] | None = None,
) -> Path:
    """Real argv through a restricted PATH; only the external CLI is replaced."""
    log = Path(ctx.env["HOME"]) / f"{provider}.argv.jsonl"
    status = (
        auth_status
        if auth_status is not None
        else {
            "loggedIn": True,
            "orgId": "org-main",
            "configDirectory": str(Path(ctx.env["HOME"]) / "claude-state"),
        }
    )
    script = f"""#!{sys.executable}
import json
import sys
from pathlib import Path
args = sys.argv[1:]
with Path({str(log)!r}).open("a") as handle:
    handle.write(json.dumps(args) + "\\n")
if {provider!r} == "claude" and args == ["auth", "status"]:
    print(json.dumps({status!r}))
    sys.exit(0)
prompt = args[1] if {provider!r} == "claude" else args[-1]
if prompt == "Reply with exactly OK.":
    refreshed = {None if refreshed is None else (str(refreshed[0]), refreshed[1])!r}
    if refreshed is not None:
        Path(refreshed[0]).parent.mkdir(parents=True, exist_ok=True)
        Path(refreshed[0]).write_text(refreshed[1])
    print({refresh_output!r})
    sys.exit({refresh_rc})
edit = {None if remap_edit is None else (str(remap_edit[0]), remap_edit[1])!r}
if edit is not None:
    Path(edit[0]).write_text(edit[1])
print({remap_output!r})
sys.exit({remap_rc})
"""
    executable = Path(ctx.env["PATH"]) / provider
    executable.write_text(script)
    executable.chmod(0o755)
    return log


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _headless(calls: list[list[str]]) -> list[list[str]]:
    return [args for args in calls if args != ["auth", "status"]]


def test_empty_groups_do_not_read_provider_state_or_global_config(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    cfg = Config()
    monkeypatch.setattr(ctx, "read_text", lambda path: pytest.fail(f"read {path}"))
    monkeypatch.setattr(ctx, "list_dir", lambda path: pytest.fail(f"list {path}"))
    monkeypatch.setattr(ctx, "run_bounded", lambda *args, **kwargs: pytest.fail("ran CLI"))
    lines = []

    assert under_test.reconcile(ctx, cfg, set(), say=lines.append, dry_run=False) is cfg
    assert lines == []
    assert not ctx.home.exists()


def test_old_blank_session_config_is_current_without_turn_or_write(tmp_path):
    ctx = _context(tmp_path)
    ctx.home.mkdir()
    yaml_path = ctx.home / "config.yaml"
    yaml_path.write_text(
        "schema_version: 1\nllm:\n  default: codex\n  providers:\n    codex:\n      model: ''\n"
    )
    loaded = store.load_global(ctx.home)
    assert loaded is not None
    cfg = Config(llm=loaded.llm)
    before = yaml_path.read_bytes()
    _codex_catalog(ctx, CODEX_CURRENT)
    log = _cli_stub(ctx, "codex")
    lines = []

    assert under_test.reconcile(ctx, cfg, {"session"}, say=lines.append, dry_run=False) is cfg
    assert lines == ["✓ codex models current (2 listed)"]
    assert _calls(log) == []
    assert yaml_path.read_bytes() == before


def test_dry_run_reports_every_stale_leaf_before_success_sentinel(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model="gpt-5-sol", tasks_review="gpt-5-astra")
    before = (ctx.home / "config.yaml").read_bytes()
    _codex_catalog(ctx, CODEX_CURRENT)
    log = _cli_stub(ctx, "codex")
    lines = []

    with pytest.raises(under_test.DryRunStale):
        under_test.reconcile(ctx, cfg, {"session"}, say=lines.append, dry_run=True)

    assert (
        len([line for line in lines if line.startswith("· model:") and "(would remap)" in line])
        == 1
    )
    assert (
        len(
            [
                line
                for line in lines
                if line.startswith("· tasks.review:") and "(would remap)" in line
            ]
        )
        == 1
    )
    assert _calls(log) == []
    assert (ctx.home / "config.yaml").read_bytes() == before


def test_refresh_rereads_cli_catalog_and_preserves_new_original_id(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model="gpt-7-sol")
    before = (ctx.home / "config.yaml").read_bytes()
    path = _codex_catalog(ctx, CODEX_CURRENT)
    fresh = path.read_text().replace("gpt-6-sol", "gpt-7-sol")
    log = _cli_stub(ctx, "codex", refreshed=(path, fresh))
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    assert _headless(_calls(log)) == [["exec", "--skip-git-repo-check", "Reply with exactly OK."]]
    assert (ctx.home / "config.yaml").read_bytes() == before
    assert cfg.llm.providers["codex"].model == "gpt-7-sol"


def test_missing_claude_catalog_after_refresh_has_path_and_open_hint(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1")
    log = _cli_stub(ctx, "claude")
    before = (ctx.home / "config.yaml").read_bytes()

    with pytest.raises(ConfigError, match=r"cache/model-catalog.*open claude once"):
        under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lambda _: None, dry_run=False)

    assert _headless(_calls(log)) == [["-p", "Reply with exactly OK.", "--output-format", "text"]]
    assert (ctx.home / "config.yaml").read_bytes() == before


def test_failed_refresh_reports_redacted_probe_detail_and_preserves_yaml(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model="gpt-5-sol")
    _codex_catalog(ctx, CODEX_CURRENT)
    log = _cli_stub(ctx, "codex", refresh_output="rejected model default", refresh_rc=7)
    before = (ctx.home / "config.yaml").read_bytes()

    with pytest.raises(OmcError, match="rejected model 'default model'"):
        under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lambda _: None, dry_run=False)

    assert _headless(_calls(log)) == [["exec", "--skip-git-repo-check", "Reply with exactly OK."]]
    assert (ctx.home / "config.yaml").read_bytes() == before


@pytest.mark.parametrize(
    ("stale", "answer"),
    [("gpt-5-sol", "gpt-6-sol"), ("sol:ultra", "gpt-6-sol:high"), ("sol", "gpt-6-astra")],
)
def test_codex_stale_model_remaps_full_id_and_only_one_yaml_leaf(tmp_path, stale, answer):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model=stale, tasks_review="astra")
    _codex_catalog(ctx, CODEX_CURRENT if stale != "sol" else CODEX_CURRENT[:1])
    log = _cli_stub(ctx, "codex", remap_output=json.dumps({"model": answer}))
    lines = []
    original = store.load_global(ctx.home)

    returned = under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    calls = _headless(_calls(log))
    assert calls[0] == ["exec", "--skip-git-repo-check", "Reply with exactly OK."]
    assert len(calls) == 2
    assert calls[1][:2] == ["exec", "--skip-git-repo-check"] and len(calls[1]) == 3
    assert "model" in calls[1][-1] and "gpt-6-astra" in calls[1][-1]
    updated = store.load_global(ctx.home)
    assert updated is not None and original is not None
    assert updated.llm.providers["codex"].model == answer
    assert updated.llm.providers["codex"].tasks == original.llm.providers["codex"].tasks
    assert returned is cfg and cfg.llm.providers["codex"].model == answer
    assert (
        len([line for line in lines if line.startswith("→ model:") and "config updated" in line])
        == 1
    )


def test_two_claude_leaves_share_one_default_remap_turn(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1", tasks_review="claude-fable-4-1")
    _claude_catalog(ctx)
    log = _cli_stub(
        ctx,
        "claude",
        remap_output=json.dumps({"model": "claude-opus-5-5", "tasks.review": "claude-fable-5-5"}),
    )
    lines = []

    under_test.reconcile(ctx, cfg, {"session"}, say=lines.append, dry_run=False)

    calls = _headless(_calls(log))
    assert calls[0] == ["-p", "Reply with exactly OK.", "--output-format", "text"]
    assert (
        len(calls) == 2 and calls[1][:1] == ["-p"] and calls[1][2:] == ["--output-format", "text"]
    )
    assert '"model"' in calls[1][1] and '"tasks.review"' in calls[1][1]
    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["claude"].model == "claude-opus-5-5"
    assert persisted.llm.providers["claude"].tasks.review == "claude-fable-5-5"
    assert len([line for line in lines if "config updated" in line]) == 2


API_KEY_STATUS = {
    "loggedIn": True,
    "authMethod": "api_key",
    "apiKeySource": "ANTHROPIC_API_KEY",
}


def _api_key_listing(url, *, headers, timeout=30):
    return 200, '{"data": [{"id": "claude-opus-5-5", "created_at": "2026-05-01T00:00:00Z"}]}'


def test_api_key_cli_saved_effort_is_current_without_turn_or_write(tmp_path):
    """GET /v1/models lists no efforts; unknown support must not make opus:max stale."""
    ctx = _context(tmp_path)
    ctx.env["ANTHROPIC_API_KEY"] = "private-key"
    cfg = _config(ctx, "claude", model="opus:max")
    before = (ctx.home / "config.yaml").read_bytes()
    log = _cli_stub(
        ctx,
        "claude",
        auth_status={**API_KEY_STATUS, "configDirectory": str(tmp_path / "claude-config")},
    )
    ctx.http_get = _api_key_listing
    lines = []

    assert under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False) is cfg

    assert _headless(_calls(log)) == []
    assert (ctx.home / "config.yaml").read_bytes() == before
    assert cfg.llm.providers["claude"].model == "opus:max"
    assert lines == ["✓ claude models current (1 listed)"]


def test_api_key_stale_id_remaps_to_listed_id_keeping_effort(tmp_path):
    ctx = _context(tmp_path)
    ctx.env["ANTHROPIC_API_KEY"] = "private-key"
    cfg = _config(ctx, "claude", model="claude-opus-4-1:max")
    log = _cli_stub(
        ctx,
        "claude",
        auth_status={**API_KEY_STATUS, "configDirectory": str(tmp_path / "claude-config")},
        remap_output='{"model": "claude-opus-5-5:max"}',
    )
    ctx.http_get = _api_key_listing
    lines = []

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    saved = store.load_global(ctx.home)
    assert saved is not None
    assert saved.llm.providers["claude"].model == "claude-opus-5-5:max"
    assert cfg.llm.providers["claude"].model == "claude-opus-5-5:max"
    assert len(_headless(_calls(log))) == 2
    assert len([line for line in lines if "model:" in line and "config updated" in line]) == 1
    written = (ctx.home / "config.yaml").read_bytes()

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lines.append, dry_run=False)

    assert (ctx.home / "config.yaml").read_bytes() == written
    assert len(_headless(_calls(log))) == 2


def test_cli_docs_model_remaps_and_keeps_other_provider_leaves(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", docs_model="claude-sonnet-4-1", tasks_review="opus")
    _claude_catalog(ctx)
    log = _cli_stub(ctx, "claude", remap_output='{"docs_model": "claude-sonnet-5-5"}')

    under_test.reconcile(ctx, cfg, {"docs"}, say=lambda _: None, dry_run=False)

    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert persisted.llm.providers["claude"].tasks.review == "opus"
    assert cfg.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert len(_headless(_calls(log))) == 2


@pytest.mark.parametrize("backend", ["cli", "api"])
def test_saved_docs_effort_remaps_to_full_id_without_effort(tmp_path, monkeypatch, backend):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", docs_model="claude-sonnet-5-5:high")
    cfg.llm.docs.backend = backend
    store.save_global(ctx.home, GlobalConfig(llm=cfg.llm))
    if backend == "api":
        cfg.secrets = SecretsConfig(api_keys={"claude": "secret-token-1234"})
        monkeypatch.setattr(
            ctx,
            "http_get",
            lambda url, *, headers, timeout=30: (
                200,
                '{"data": [{"id": "claude-sonnet-5-5"}]}',
            ),
        )
    else:
        _claude_catalog(ctx)
    log = _cli_stub(ctx, "claude", remap_output='{"docs_model": "claude-sonnet-5-5"}')
    lines = []

    returned = under_test.reconcile(ctx, cfg, {"docs"}, say=lines.append, dry_run=False)

    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert returned is cfg and cfg.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    docs_run = docs_llm_for(cfg)
    assert docs_run.model == "claude-sonnet-5-5"
    assert docs_run.wiki_args[docs_run.wiki_args.index("--model") + 1] == "claude-sonnet-5-5"
    assert len(_headless(_calls(log))) == (2 if backend == "cli" else 1)
    assert len([line for line in lines if "docs_model" in line and "config updated" in line]) == 1


def test_api_docs_and_cli_session_use_separate_catalogs_but_one_remap_turn(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", model="claude-opus-4-1", docs_model="claude-sonnet-4-1")
    cfg.llm.docs.backend = "api"
    store.save_global(ctx.home, GlobalConfig(llm=cfg.llm))
    cfg.secrets = SecretsConfig(api_keys={"claude": "secret-token-1234"})
    _claude_catalog(ctx)
    log = _cli_stub(
        ctx,
        "claude",
        remap_output='{"model": "claude-opus-5-5", "docs_model": "claude-sonnet-5-5"}',
    )
    gets = []

    def http_get(url, *, headers, timeout=30):
        gets.append((url, headers))
        return 200, json.dumps({"data": [{"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}]})

    monkeypatch.setattr(ctx, "http_get", http_get)
    lines = []

    under_test.reconcile(ctx, cfg, {"session", "docs"}, say=lines.append, dry_run=False)

    assert len(gets) == 1 and gets[0][0] == "https://api.anthropic.com/v1/models?limit=1000"
    assert gets[0][1]["x-api-key"] == "secret-token-1234"
    calls = _headless(_calls(log))
    assert len(calls) == 2  # CLI session stale: one refresh; API docs: no extra refresh
    assert calls[0] == ["-p", "Reply with exactly OK.", "--output-format", "text"]
    assert '"model"' in calls[1][1] and '"docs_model"' in calls[1][1]
    assert all("--model" not in call and "-m" not in call for call in calls)
    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["claude"].model == "claude-opus-5-5"
    assert persisted.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert cfg.llm.providers["claude"].model == "claude-opus-5-5"
    assert cfg.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert len([line for line in lines if "config updated" in line]) == 2


def test_api_docs_only_gets_list_and_remaps_without_refresh(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", docs_model="claude-sonnet-4-1")
    cfg.llm.docs.backend = "api"
    store.save_global(ctx.home, GlobalConfig(llm=cfg.llm))
    cfg.secrets = SecretsConfig(api_keys={"claude": "secret-token-1234"})
    log = _cli_stub(ctx, "claude", remap_output='{"docs_model": "claude-sonnet-5-5"}')
    gets = []
    monkeypatch.setattr(
        ctx,
        "http_get",
        lambda url, *, headers, timeout=30: (
            gets.append(url) or 200,
            '{"data": [{"id": "claude-sonnet-5-5"}]}',
        ),
    )

    under_test.reconcile(ctx, cfg, {"docs"}, say=lambda _: None, dry_run=False)

    assert gets == ["https://api.anthropic.com/v1/models?limit=1000"]
    calls = _headless(_calls(log))
    assert len(calls) == 1 and calls[0][:1] == ["-p"]
    assert calls[0][2:] == ["--output-format", "text"]
    assert "Reply with exactly OK." not in calls[0]


@pytest.mark.parametrize("configured", ["", "sonnet"])
def test_api_docs_family_default_remaps_to_launchable_full_id(tmp_path, monkeypatch, configured):
    ctx = _context(tmp_path)
    cfg = _config(ctx, "claude", docs_model=configured)
    cfg.llm.docs.backend = "api"
    store.save_global(ctx.home, GlobalConfig(llm=cfg.llm))
    cfg.secrets = SecretsConfig(api_keys={"claude": "secret-token-1234"})
    log = _cli_stub(ctx, "claude", remap_output='{"docs_model": "claude-sonnet-5-5"}')
    gets = []

    def http_get(url, *, headers, timeout=30):
        gets.append(url)
        return 200, '{"data": [{"id": "claude-sonnet-5-5"}]}'

    monkeypatch.setattr(ctx, "http_get", http_get)

    returned = under_test.reconcile(ctx, cfg, {"docs"}, say=lambda _: None, dry_run=False)

    assert returned is cfg
    assert gets == ["https://api.anthropic.com/v1/models?limit=1000"]
    calls = _headless(_calls(log))
    assert len(calls) == 1 and calls[0][:1] == ["-p"]
    assert calls[0][2:] == ["--output-format", "text"]
    assert "Reply with exactly OK." not in calls[0]
    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert cfg.llm.providers["claude"].docs_model == "claude-sonnet-5-5"
    assert docs_llm_for(cfg).model == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    ("output", "rc"),
    [('{"model": "gpt-5-sol", "tasks.review": "gpt-6-astra"}', 0), ("failure", 5)],
)
def test_invalid_or_failed_batched_remap_keeps_yaml_and_memory_unchanged(tmp_path, output, rc):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model="gpt-5-sol", tasks_review="gpt-5-astra")
    _codex_catalog(ctx, CODEX_CURRENT)
    log = _cli_stub(ctx, "codex", remap_output=output, remap_rc=rc)
    before = (ctx.home / "config.yaml").read_bytes()

    with pytest.raises(OmcError):
        under_test.reconcile(ctx, cfg, {"session"}, say=lambda _: None, dry_run=False)

    assert len(_headless(_calls(log))) == 2
    assert (ctx.home / "config.yaml").read_bytes() == before
    assert cfg.llm.providers["codex"].model == "gpt-5-sol"
    assert cfg.llm.providers["codex"].tasks.review == "gpt-5-astra"


def test_fresh_global_reload_preserves_newer_unrelated_edit(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model="gpt-5-sol", tasks_review="astra")
    _codex_catalog(ctx, CODEX_CURRENT)
    yaml_path = ctx.home / "config.yaml"
    newer = store.load_global(ctx.home)
    assert newer is not None
    newer.llm.providers["codex"].tasks.review = "sol"
    # The other writer saves while the remap turn is in progress.
    _cli_stub(
        ctx,
        "codex",
        remap_output='{"model": "gpt-6-sol"}',
        remap_edit=(yaml_path, yaml.safe_dump(asdict(newer), sort_keys=False)),
    )

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lambda _: None, dry_run=False)

    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["codex"].model == "gpt-6-sol"
    assert persisted.llm.providers["codex"].tasks.review == "sol"
    assert cfg.llm.providers["codex"].model == "gpt-6-sol"
    assert cfg.llm.providers["codex"].tasks.review == "astra"


def test_concurrent_edit_to_same_leaf_uses_last_writer_wins(tmp_path):
    ctx = _context(tmp_path)
    cfg = _config(ctx, model="gpt-5-sol")
    _codex_catalog(ctx, CODEX_CURRENT)
    newer = store.load_global(ctx.home)
    assert newer is not None
    newer.llm.providers["codex"].model = "gpt-6-astra"
    _cli_stub(
        ctx,
        "codex",
        remap_output='{"model": "gpt-6-sol"}',
        remap_edit=(ctx.home / "config.yaml", yaml.safe_dump(asdict(newer), sort_keys=False)),
    )

    under_test.reconcile(ctx, cfg, {"orchestrator"}, say=lambda _: None, dry_run=False)

    persisted = store.load_global(ctx.home)
    assert persisted is not None
    assert persisted.llm.providers["codex"].model == "gpt-6-sol"
