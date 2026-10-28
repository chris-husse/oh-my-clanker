"""refresh_knowledge — the ONLY code that repairs the snapshot (spec §2).
Judged by recomputing the verdict, never by exit codes."""

import json
import shutil
import stat

import pytest

from omc.config.schema import Config, GitNexusConfig
from omc.gitnexus import refresh_knowledge
from omc.toolctx import ToolContext

from .test_watch import (
    _ctx_with_healing_node_stub,
    _ctx_with_node_stub,
    _ctx_with_shared_store_node_stub,
    _git,
    _git_out,
    _repo_with_origin,
)

REPO_PATH_SLOT = '"repoPath":"%s"'


@pytest.fixture(autouse=True)
def _fast_wiki_poll(monkeypatch):
    import omc.gitnexus as gitnexus_mod

    monkeypatch.setattr(gitnexus_mod, "_WIKI_POLL_SECONDS", 0.05)


def _stale_index(repo):
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": "a" * 40, "repoPath": str(repo)})
    )


def _foreign_stamping_stub(tmp_path):
    """Healing stub whose analyze stamps a foreign repoPath: stale after every step."""
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert REPO_PATH_SLOT in text
    node.write_text(text.replace(REPO_PATH_SLOT, '"repoPath":"/elsewhere%s"'))


def _run(ctx, repo, **kw):
    said = []
    v = refresh_knowledge(ctx, Config(), str(repo), "main", say=said.append, **kw)
    return v, said


def test_fresh_snapshot_narrates_current_and_calls_nothing(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists()  # refresh_knowledge itself never probes --version
    assert "✓ knowledge is current" in said


def test_stale_index_heals_with_one_incremental_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh
    recorded = calls.read_text()
    assert [line.split()[1:] for line in recorded.splitlines() if " analyze " in line] == [
        ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]
    ]
    assert "clean --force" not in recorded
    assert "→ refreshing GitNexus index (incremental)" in said and "✓ index refreshed" in said


def test_analyze_that_leaves_it_stale_escalates_to_clean(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    _foreign_stamping_stub(tmp_path)
    v, said = _run(ctx, repo, documentation=False, reset=False)
    recorded = calls.read_text()
    assert "clean --force" in recorded
    assert recorded.index("analyze") < recorded.index("clean --force")
    assert any(s.startswith("✗ index still stale after analyze") for s in said)
    assert not v.fresh  # the rebuild stamps the same foreign path: honest verdict
    # never contradict the verdict: the rebuild got the branch stamp right, which
    # is a step, not a success — `✓ index rebuilt` belongs to a clean verdict only
    assert not any(s.startswith("✓ index rebuilt") for s in said)
    assert "✗ index still stale after rebuild: index-foreign" in said


@pytest.mark.parametrize("still_stale", [False, True])
def test_failed_analyze_exit_code_is_narrated_but_verdict_decides(tmp_path, still_stale):
    """Exit codes are narrated, never trusted: a non-zero analyze that DID fix the
    metadata ends fresh; one that did not escalates like any other stale result."""
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    node.write_text(node.read_text().replace("echo ok\nexit 0\n", "echo ok\nexit 7\n"))
    if still_stale:
        _foreign_stamping_stub(tmp_path)
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert any(s.startswith("✗ analyze failed") for s in said)
    assert v.fresh is not still_stale
    assert ("clean --force" in calls.read_text()) is still_stale


def test_inverted_store_destroys_first_exactly_one_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "feature/x", "lastCommit": "old"})
    )
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    recorded = calls.read_text()
    assert recorded.index("clean --force") < recorded.index("analyze")
    assert [line.split()[1:] for line in recorded.splitlines() if " analyze " in line] == [
        ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]
    ]
    assert "✓ index rebuilt for main" in said and v.fresh


def test_reset_clears_mirror_and_rebuilds(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    docs = repo / ".omc" / "docs" / "gitnexus" / "docs"
    docs.mkdir(parents=True)
    (docs / "stale.md").write_text("x")
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=True)
    recorded = calls.read_text()
    assert recorded.index("clean --force") < recorded.index("analyze")
    assert not docs.exists() and v.fresh
    hint = "· docs mirror cleared — run omc watch --once --enable-documentation to regenerate"
    assert hint in said


def test_reset_with_failed_clean_aborts_before_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # fresh fixture: the FAILED clean is the story
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home", clean_removes=False)
    v, said = _run(ctx, repo, documentation=False, reset=True)
    assert any(s.startswith("✗ clean did not remove the index") for s in said)
    assert "analyze" not in calls.read_text()
    # The clean failed, so the fixture's index survived untouched — and it was
    # fresh to begin with. The verdict never lies about what is on disk.
    assert v.fresh


def _seed_wiki_and_mirror(repo):
    """A wiki whose fromCommit is HEAD (verdict-fresh) plus its docs mirror."""
    wiki = repo / ".gitnexus" / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "meta.json").write_text(
        json.dumps({"fromCommit": _git_out(repo, "rev-parse", "HEAD"), "moduleFiles": {}})
    )
    (wiki / "index.md").write_text("page")
    docs = repo / ".omc" / "docs" / "gitnexus" / "docs"
    docs.mkdir(parents=True)
    (docs / "index.md").write_text("page")
    return docs


def test_reset_with_failed_clean_restores_the_docs_mirror(tmp_path):
    """The mirror is cleared BEFORE `clean`; a clean that leaves the index intact
    keeps the verdict FRESH, so no wiki reason would ever re-mirror and the docs
    /omc:explain reads would stay deleted forever. A fresh verdict must imply the
    mirror's presence (spec §2)."""
    _, repo = _repo_with_origin(tmp_path)
    docs = _seed_wiki_and_mirror(repo)
    ctx, _calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home", clean_removes=False)
    v, said = _run(ctx, repo, documentation=True, reset=True)
    assert any(s.startswith("✗ clean did not remove the index") for s in said)
    assert v.fresh  # index and wiki survived the failed clean: honest verdict
    assert (docs / "index.md").read_text() == "page"
    assert "✓ docs mirror restored" in said


def test_documentation_off_restores_the_cleared_mirror(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    docs = _seed_wiki_and_mirror(repo)
    ctx, _ = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home", clean_removes=False)
    v, said = _run(ctx, repo, documentation=False, reset=True)
    assert v.fresh
    assert (docs / "index.md").read_text() == "page"
    assert said.count("✓ docs mirror restored") == 1
    assert not any("docs mirror cleared" in line for line in said)


@pytest.mark.parametrize("meta", [None, "{", '{"fromCommit":"old"}'])
def test_documentation_off_missing_mirror_requires_readable_metadata(tmp_path, meta):
    _, repo = _repo_with_origin(tmp_path)
    docs = _seed_wiki_and_mirror(repo)
    shutil.rmtree(docs)
    path = repo / ".gitnexus/wiki/meta.json"
    if meta is None:
        path.unlink()
    else:
        path.write_text(meta)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists()
    assert docs.exists() == (meta == '{"fromCommit":"old"}')
    assert ("✓ docs mirror restored" in said) == docs.exists()


@pytest.mark.parametrize("documentation", [False, True])
def test_initially_fresh_restores_only_absent_mirror(tmp_path, documentation):
    _, repo = _repo_with_origin(tmp_path)
    docs = _seed_wiki_and_mirror(repo)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    (docs / "index.md").write_text("preserve")
    v, said = _run(ctx, repo, documentation=documentation, reset=False)
    assert v.fresh and (docs / "index.md").read_text() == "preserve"
    assert "✓ docs mirror restored" not in said
    shutil.rmtree(docs)
    v, said = _run(ctx, repo, documentation=documentation, reset=False)
    assert v.fresh and not calls.exists()
    assert (docs / "index.md").read_text() == "page"
    assert said.count("✓ docs mirror restored") == 1


def test_documentation_off_never_computes_wiki_reasons(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # index fresh, no wiki at all
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists()
    assert not any("docs" in s for s in said if s.startswith("✗"))


def test_wiki_behind_runs_wiki_supervised_and_mirrors(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=True, reset=False)
    recorded = calls.read_text()
    assert "wiki --provider claude" in recorded and "--model sonnet" in recorded
    assert v.fresh
    assert (repo / ".omc" / "docs" / "gitnexus" / "docs" / "index.md").read_text() == "page"
    assert "✓ documentation refreshed → .omc/docs/gitnexus/docs" in said


def test_wiki_still_behind_leaves_mirror_untouched(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert "> .gitnexus/wiki/meta.json;" in text
    # a wiki run that writes NO metadata: verdict stays wiki-missing
    node.write_text(text.replace("> .gitnexus/wiki/meta.json;", "> /dev/null;"))
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert "wiki-missing" in v.codes()
    assert not (repo / ".omc" / "docs").exists()
    assert "✗ documentation still behind after regeneration" in said


def _api_cfg(key="sk-ant-test-0123456789abcdef", model="claude-sonnet-5-5"):
    from omc.config.schema import DocsConfig, LLMConfig, ProviderConfig, SecretsConfig

    return Config(
        llm=LLMConfig(
            default="claude",
            docs=DocsConfig(backend="api"),
            providers={"claude": ProviderConfig(docs_model=model)},
        ),
        secrets=SecretsConfig(api_keys={"claude": key} if key else {}),
    )


def test_wiki_api_backend_passes_key_in_env_only(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    node.write_text(node.read_text().replace('echo "$@" >>', 'echo "KEY=$GITNEXUS_API_KEY $@" >>'))
    said = []
    v = refresh_knowledge(
        ctx, _api_cfg(), str(repo), "main", say=said.append, documentation=True, reset=False
    )
    recorded = calls.read_text()
    assert (
        "wiki --provider custom --base-url https://api.anthropic.com/v1/"
        " --model claude-sonnet-5-5 --reasoning-model" in recorded
    )
    assert "KEY=sk-ant-test-0123456789abcdef " in recorded  # the wiki line's env
    assert v.fresh
    assert "→ regenerating documentation via claude api (claude-sonnet-5-5)" in said
    assert not any("sk-ant-test" in s for s in said)


def test_wiki_api_without_key_narrates_and_does_not_crash(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # index fresh, no wiki
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    said = []
    v = refresh_knowledge(
        ctx, _api_cfg(key=""), str(repo), "main", say=said.append, documentation=True, reset=False
    )
    assert not v.fresh
    assert any(s.startswith("✗ documentation: ") and "run omc configure" in s for s in said)
    assert "wiki" not in (calls.read_text() if calls.exists() else "")


def test_wiki_api_failure_tail_is_redacted_before_truncation(tmp_path):
    # Mirror of test_dependency's run_document case: 370 chars of noise, then the
    # key spans offsets 384-412, so a naive [:400] would cut it in half and leak
    # `sk-ant-test-0123`; _run_wiki must redact first.
    key = "sk-ant-test-0123456789abcdef"
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    arm = '*" wiki --provider"*) '
    text = node.read_text()
    assert arm in text
    noise = "x" * 370 + f"LLM API error {key} boom"
    progress = 'GITNEXUS_PROGRESS {"detail":"private progress"}'
    node.write_text(
        text.replace(arm, f"{arm}echo '{progress}' >&2; printf '%s' '{noise}'; exit 1; ")
    )
    said = []
    v = refresh_knowledge(
        ctx, _api_cfg(key), str(repo), "main", say=said.append, documentation=True, reset=False
    )
    assert not v.fresh
    failed = next(s for s in said if s.startswith("✗ wiki failed (exit 1): "))
    assert "LLM API error" in failed and "private progress" not in failed
    assert not any(key in s or key[:12] in s for s in said)
    assert any("******" in s for s in said)


def test_wiki_api_success_redacts_progress_before_narration_and_tracker(tmp_path, monkeypatch):
    import omc.cli.progress_bar as pb

    key = "sk-ant-test-0123456789abcdef"
    trackers = []
    real_bar = pb.BarThread

    def capture_bar(tracker):
        trackers.append(tracker)
        return real_bar(tracker)

    monkeypatch.setattr(pb, "BarThread", capture_bar)
    progress = (
        'GITNEXUS_PROGRESS {"phase":"grouping","percent":28,'
        f'"detail":"Fallback after API error {key}; grouping by directory"}}'
    )
    ctx, repo = _wiki_behind_with_speaking_stub(tmp_path, extra_shell=f"echo '{progress}' >&2; ")
    said = []
    v = refresh_knowledge(
        ctx, _api_cfg(key), str(repo), "main", say=said.append, documentation=True, reset=False
    )
    assert v.fresh
    assert any("Fallback after API error ******" in line for line in said)
    assert not any(key in line for line in said)
    assert trackers[0].percent == 28
    assert "Fallback after API error ******" in trackers[0].detail
    assert key not in trackers[0].detail


_GN_LINES = (
    'echo \'GITNEXUS_PROGRESS {"phase":"grouping","percent":15,'
    '"detail":"Grouping files into modules (LLM)..."}\' >&2; '
    'echo \'GITNEXUS_PROGRESS {"phase":"heartbeat","percent":15,'
    '"detail":"Grouping files into modules (LLM)... (30s)"}\' >&2; '
    'echo \'GITNEXUS_PROGRESS {"phase":"grouping","percent":28,'
    '"detail":"Created 3 modules"}\' >&2; '
    'echo \'GITNEXUS_PROGRESS {"phase":"grouping","percent":28,'
    '"detail":"Created 3 modules"}\' >&2; '
)


def _wiki_behind_with_speaking_stub(tmp_path, *, extra_shell=""):
    """A wiki-behind repo whose fake GitNexus prints progress lines on stderr."""
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    arm = '*" wiki --provider"*) '
    text = node.read_text()
    assert arm in text
    node.write_text(text.replace(arm, arm + _GN_LINES + extra_shell))
    return ctx, repo


def test_wiki_off_tty_narrates_gitnexus_phase_changes_but_not_heartbeats(tmp_path):
    ctx, repo = _wiki_behind_with_speaking_stub(tmp_path)
    v, said = _run(ctx, repo, documentation=True, reset=False)  # pytest: stderr is not a TTY
    assert v.fresh
    assert "· Grouping files into modules (LLM)..." in said
    assert said.count("· Created 3 modules") == 1  # repeated identical line narrated once
    assert not any("(30s)" in s for s in said)  # heartbeats are not news
    # narration lands between the start line and the success line
    assert said.index("· Created 3 modules") > said.index(
        next(s for s in said if s.startswith("→ regenerating documentation via"))
    )


def test_wiki_on_tty_drives_the_bar_and_does_not_narrate(tmp_path, monkeypatch):
    import io

    import omc.cli.progress_bar as pb

    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    out = FakeTTY()
    real_bar = pb.BarThread
    # Seam from tests/unit/test_progress_bar.py: a bar draws on the `out=` stream
    # it is given. _run_wiki imports BarThread lazily from this module, so patching
    # it here hands _run_wiki a bar bound to a fake TTY — no global sys.stderr touch.
    monkeypatch.setattr(pb, "BarThread", lambda tracker: real_bar(tracker, out=out))
    assert real_bar(object(), out=out).enabled is True
    assert real_bar(object(), out=io.StringIO()).enabled is False
    # linger so the 1 s redraw thread paints at least once before stop
    ctx, repo = _wiki_behind_with_speaking_stub(tmp_path, extra_shell="sleep 1.3; ")
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert v.fresh
    assert not any(s.startswith("· ") for s in said)
    painted = out.getvalue()
    assert "\r[" in painted and "%" in painted  # an in-place bar redraw
    assert painted.endswith("\r\x1b[K")  # cleared before narration resumed


def test_wiki_failure_excerpt_is_the_tail_where_the_error_is(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    arm = '*" wiki --provider"*) '
    noise = "x" * 500 + " LLM API error: boom"
    progress = 'GITNEXUS_PROGRESS {"detail":"private progress"}'
    command = f"{arm}echo '{progress}' >&2; printf '%s' '{noise}'; exit 1; "
    node.write_text(node.read_text().replace(arm, command))
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert not v.fresh
    failed = next(s for s in said if s.startswith("✗ wiki failed (exit 1): "))
    assert "LLM API error: boom" in failed
    assert "private progress" not in failed


def test_shared_store_legacy_index_converges_locally(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    # Leave the local legacy index at its genuine ancestor while HEAD advances.
    (repo / "f.txt").write_text("two\n")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-qm", "c2", cwd=repo)
    ctx, calls = _ctx_with_shared_store_node_stub(tmp_path, tmp_path / "home")
    verdict, said = _run(ctx, repo, documentation=False, reset=False)
    assert verdict.fresh, said
    metadata = json.loads((repo / ".gitnexus" / "meta.json").read_text())
    assert metadata["lastCommit"] == _git_out(repo, "rev-parse", "HEAD")
    assert not (repo / ".gitnexus" / "store.json").exists()
    assert not (tmp_path / "shared-store").exists()
    recorded = calls.read_text()
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1
    assert "clean --force" not in recorded
    assert _commands(calls) == [
        ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]
    ]
    assert POINTER_NOTICE not in said
    again, _ = _run(ctx, repo, documentation=False, reset=False)
    assert again.fresh
    assert calls.read_text() == recorded


POINTER_NOTICE = (
    "· GitNexus shared-store pointer found — re-indexing locally; sharing is off under omc"
)


def _pointer(repo, tmp_path):
    shared = tmp_path / "shared-store"
    shared.mkdir()
    (shared / "meta.json").write_text("{}")
    (repo / ".gitnexus/store.json").write_text(json.dumps({"storePath": str(shared)}))
    return shared


def _commands(calls):
    import shlex

    rows = [line.split("|", 1) for line in calls.read_text().splitlines()]
    assert all(env == "off" for env, _ in rows)
    return [shlex.split(argv)[1:] for _, argv in rows]


@pytest.mark.parametrize(
    "gc_exit,gc_output", [(0, "skipped locked slot"), (17, "x" * 500 + " failure")]
)
def test_pointer_converges_and_gc_is_best_effort(tmp_path, gc_exit, gc_output):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    shared = _pointer(repo, tmp_path)
    ctx, calls = _ctx_with_shared_store_node_stub(
        tmp_path, tmp_path / "home", gc_exit=gc_exit, gc_output=gc_output
    )
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh
    assert said.count(POINTER_NOTICE) == 1
    assert _commands(calls) == [
        ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"],
        ["clean", "--gc", "--force"],
    ]
    assert not shared.exists() and not (repo / ".gitnexus/store.json").exists()
    assert "→ collecting orphaned GitNexus shared stores (clean --gc)" in said
    expected = f"✗ clean --gc failed (exit {gc_exit}): {gc_output[-400:]}"
    assert (expected if gc_exit else f"· {gc_output[-400:]}") in said


def test_already_fresh_pointer_does_not_run_gc(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _pointer(repo, tmp_path)
    ctx, calls = _ctx_with_shared_store_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists()
    assert POINTER_NOTICE not in said


def test_pointer_analyze_failure_before_registration_preserves_stale_legacy(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / "f.txt").write_text("two\n")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-qm", "c2", cwd=repo)
    old = (repo / ".gitnexus/meta.json").read_text()
    _pointer(repo, tmp_path)
    ctx, calls = _ctx_with_shared_store_node_stub(tmp_path, tmp_path / "home", analyze_fails=True)
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.codes() == ["index-behind"]
    assert (repo / ".gitnexus/meta.json").read_text() == old
    assert any("clean did not remove the index" in line for line in said)
    assert _commands(calls) == [
        ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"],
        ["clean", "--force"],
    ]


def test_pointer_gc_runs_before_documentation_completes(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    _pointer(repo, tmp_path)
    ctx, calls = _ctx_with_shared_store_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert not v.index_codes() and v.wiki_codes() == ["wiki-missing"]
    commands = _commands(calls)
    assert commands[1] == ["clean", "--gc", "--force"]
    assert commands[2][0] == "wiki"
    assert commands.count(["clean", "--gc", "--force"]) == 1


@pytest.mark.parametrize("observed", ["HEAD", None])
def test_require_base_refuses_detached_or_unreadable(tmp_path, monkeypatch, observed):
    import omc.gitnexus as gn

    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    monkeypatch.setattr(gn, "current_branch", lambda *args: observed)
    with pytest.raises(gn.CheckoutMoved) as exc:
        gn._require_base(ctx, repo, "main")
    assert exc.value.branch == (observed or "")
    assert exc.value.base == "main"
    assert not calls.exists()


@pytest.mark.parametrize("writer", ["reset", "rebuild"])
def test_detached_writer_preserves_snapshot(tmp_path, writer):
    import omc.gitnexus as gn

    _, repo = _repo_with_origin(tmp_path)
    docs = _seed_wiki_and_mirror(repo)
    meta = repo / ".gitnexus/meta.json"
    before = meta.read_bytes()
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    _git("checkout", "--detach", cwd=repo)
    with pytest.raises(gn.CheckoutMoved):
        if writer == "reset":
            _run(ctx, repo, documentation=True, reset=True)
        else:
            gn._destroy_and_rebuild(ctx, repo, "main", lambda line: None)
    assert not calls.exists()
    assert meta.read_bytes() == before
    assert (docs / "index.md").read_text() == "page"


def test_custom_base_pins_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _git("switch", "-c", "trunk", cwd=repo)
    _stale_index(repo)
    meta = repo / ".gitnexus/meta.json"
    meta.write_text(meta.read_text().replace('"main"', '"trunk"'))
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    v = refresh_knowledge(ctx, Config(), repo, "trunk", documentation=False, reset=False)
    assert v.fresh
    assert calls.read_text().split()[1:] == [
        "analyze",
        "--skip-agents-md",
        "--skip-skills",
        "--branch",
        "trunk",
    ]


def test_run_narrated_narrates_new_plain_lines_and_skips_pino_json(tmp_path):
    import omc.gitnexus as gn

    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "chatty"
    # Every narrated line goes to stdout: run_supervised reads the two pipes on
    # two threads, so cross-stream order is best-effort. Only the pino line (never
    # narrated) lives on stderr, where GitNexus's logger really writes it.
    tool.write_text(
        "#!/bin/sh\n"
        'echo "Generating embeddings"\n'
        'echo "Generating embeddings"\n'
        'echo \'{"level":30,"time":1,"msg":"pino noise"}\' >&2\n'
        'echo "   "\n'
        'echo "Generating embeddings"\n'
        'echo "Done: 3 embedded"\n'
        'echo "KEY=$GITNEXUS_LBUG_EXTENSION_INSTALL"\n'
        "exit 3\n"
    )
    tool.chmod(0o755)
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "PATH": str(bindir)})
    said = []
    cp, stalled = gn._run_narrated(
        ctx,
        [str(tool)],
        cwd=str(tmp_path),
        say=said.append,
        extra_env={"GITNEXUS_LBUG_EXTENSION_INSTALL": "auto"},
    )
    assert (cp.returncode, stalled) == (3, False)
    # "not equal to the LAST narrated line": the blank and the pino line are not
    # narrated, so the third "Generating embeddings" is still a repeat.
    assert said == ["· Generating embeddings", "· Done: 3 embedded", "· KEY=auto"]
    assert "pino noise" in cp.stderr and "KEY=auto" in cp.stdout  # capture is unaffected


def test_run_narrated_strips_line_start_ansi_before_narrating(tmp_path):
    import omc.gitnexus as gn

    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "phases"
    # Off a TTY, analyze prefixes its phase lines with "\x1b[2K\r" (verified).
    tool.write_text(
        "#!/bin/sh\n"
        "printf '\\033[2K\\rScope: 3 files\\n'\n"
        "printf '\\033[2K\\r\\033[1m{not pino}\\n'\n"
        "printf '\\033[2K\\r   \\n'\n"
    )
    tool.chmod(0o755)
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "PATH": str(bindir)})
    said = []
    cp, stalled = gn._run_narrated(ctx, [str(tool)], cwd=str(tmp_path), say=said.append)
    assert (cp.returncode, stalled) == (0, False)
    # The stripped text is what is judged: a "{" behind escapes is still pino-shaped.
    assert said == ["· Scope: 3 files"]


def test_run_narrated_silent_child_survives_on_a_changing_heartbeat(tmp_path):
    import itertools

    import omc.gitnexus as gn

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "PATH": str(tmp_path)})
    ticks = itertools.count()
    cp, stalled = gn._run_narrated(
        ctx,
        ["/bin/sleep", "0.6"],
        cwd=str(tmp_path),
        say=lambda _line: None,
        heartbeat=lambda: next(ticks),
        stall_after=0.25,
    )
    assert (cp.returncode, stalled) == (0, False)


def test_run_narrated_silent_child_stalls_on_the_constant_default_heartbeat(tmp_path):
    import omc.gitnexus as gn

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "PATH": str(tmp_path)})
    cp, stalled = gn._run_narrated(
        ctx, ["/bin/sleep", "5"], cwd=str(tmp_path), say=lambda _line: None, stall_after=0.25
    )
    assert stalled is True and cp.returncode != 0


def test_run_narrated_default_stall_reads_wiki_stall_seconds_at_call_time(tmp_path, monkeypatch):
    import omc.gitnexus as gn

    monkeypatch.setattr(gn, "_WIKI_STALL_SECONDS", 0.25)
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "PATH": str(tmp_path)})
    _cp, stalled = gn._run_narrated(
        ctx, ["/bin/sleep", "5"], cwd=str(tmp_path), say=lambda _line: None
    )
    assert stalled is True


def test_store_heartbeat_fingerprints_the_gitnexus_dir(tmp_path):
    import omc.gitnexus as gn

    beat = gn._store_heartbeat(tmp_path)
    assert beat() is None  # no <root>/.gitnexus yet
    store = tmp_path / ".gitnexus" / "lbug"
    store.mkdir(parents=True)
    empty = beat()
    assert empty is not None
    (store / "vectors.bin").write_bytes(b"x" * 10)
    first = beat()
    assert first != empty
    (store / "vectors.bin").write_bytes(b"x" * 20)
    assert beat() != first


# ─── Embeddings on refresh (spec 2026-10-27 §6) ─────────────────────────────


def _embedding_fixture(
    tmp_path,
    home,
    *,
    runtime_ready=True,
    install_rc=0,
    analyze_embed_rc=0,
    sync_rc=0,
    sync_count=7,
    phase_lines=False,
):
    """Restricted-PATH node stub for the embedding paths. Records
    `LBUG=<value or unset>|<argv>` per call so tests can pin which argv carried
    GITNEXUS_LBUG_EXTENSION_INSTALL=auto. analyze stamps a fresh meta.json like
    the healing stub; with --embeddings it also writes stats.embeddings (3) unless
    analyze_embed_rc != 0, in which case it writes nothing and exits that code.
    `embeddings install` populates the runtime prefix (install_rc == 0).
    `embeddings sync <path>` rewrites stats.embeddings = sync_count keeping
    lastCommit (sync_rc == 0) or exits sync_rc writing nothing."""
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, home)
    node = tmp_path / "bin" / "node"
    rt = home / "dependencies" / "gitnexus-embeddings" / "runtime"
    if runtime_ready:
        for pkg in ("@huggingface/transformers", "onnxruntime-node"):
            (rt / "node_modules" / pkg).mkdir(parents=True)
            (rt / "node_modules" / pkg / "package.json").write_text("{}")
    populate = (
        f'/bin/mkdir -p "{rt}/node_modules/@huggingface/transformers" '
        f'"{rt}/node_modules/onnxruntime-node"; '
        f': > "{rt}/node_modules/@huggingface/transformers/package.json"; '
        f': > "{rt}/node_modules/onnxruntime-node/package.json"; '
        if install_rc == 0
        else ""
    )
    phases = (
        'echo "Generating embeddings"; echo "Generating embeddings"; '
        'echo \'{"level":30,"msg":"pino"}\' >&2; '
        if phase_lines
        else ""
    )
    meta = (
        "/bin/mkdir -p .gitnexus; "
        'printf \'{"branch":"main","lastCommit":"%s","repoPath":"%s"%s}\' '
        '"$(/usr/bin/git rev-parse HEAD)" "$PWD" "$1" > .gitnexus/meta.json'
    )
    node.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s\\n" "${{GITNEXUS_LBUG_EXTENSION_INSTALL:-unset}}" "$*" >> "{calls}"\n'
        "shift\n"  # drop the CLI path; $* is now the verb and its arguments
        'case "$*" in\n'
        '  "clean --force") /bin/rm -rf .gitnexus ;;\n'
        f'  "embeddings install") {populate}echo "added 2 packages"; exit {install_rc} ;;\n'
        f'  "embeddings sync "*) if [ {sync_rc} -ne 0 ]; then exit {sync_rc}; fi; '
        f'stamp() {{ {meta}; }}; stamp \',"stats":{{"nodes":5,"embeddings":{sync_count}}}\' ;;\n'
        f'  "analyze --skip-agents-md --skip-skills --branch main --embeddings") {phases}'
        f"if [ {analyze_embed_rc} -ne 0 ]; then exit {analyze_embed_rc}; fi; "
        f'stamp() {{ {meta}; }}; stamp \',"stats":{{"nodes":5,"embeddings":3}}\' ;;\n'
        f'  "analyze --skip-agents-md --skip-skills --branch main") stamp() {{ {meta}; }}; '
        'stamp \',"stats":{"nodes":5}\' ;;\n'
        "esac\n"
        "echo ok\nexit 0\n"
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    ctx.env = {**ctx.env, "PATH": str(tmp_path / "bin")}
    ctx.git_bin = "/usr/bin/git"
    return ctx, calls


def _on_cfg():
    return Config(gitnexus=GitNexusConfig(embeddings=True))


def _run_on(ctx, repo, **kw):
    said = []
    v = refresh_knowledge(
        ctx, _on_cfg(), str(repo), "main", say=said.append, documentation=False, reset=False, **kw
    )
    return v, said


def _rows(calls):
    import shlex

    return [
        (env, shlex.split(argv)[1:])
        for env, argv in (line.split("|", 1) for line in calls.read_text().splitlines())
    ]


PLAIN = ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]
SKIP_BACKFILL = (
    "· skipping the one-time embedding backfill — embedding failed this run; "
    "the next refresh retries"
)
EMBED = [*PLAIN, "--embeddings"]


def test_off_keeps_todays_argv_and_never_mentions_embeddings(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", runtime_ready=False)
    v, said = _run(ctx, repo, documentation=False, reset=False)  # Config(): off
    assert v.fresh
    assert _rows(calls) == [("unset", PLAIN)]
    assert not any("embedding" in s for s in said)


def test_on_and_ready_embeds_with_extension_install_auto(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", EMBED)]
    assert "→ refreshing GitNexus index (incremental)" in said and "✓ index refreshed" in said
    assert json.loads((repo / ".gitnexus/meta.json").read_text())["stats"]["embeddings"] == 3


def test_on_and_missing_runtime_installs_then_embeds(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", runtime_ready=False)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("unset", ["embeddings", "install"]), ("auto", EMBED)]
    assert said.index("✓ GitNexus embedding runtime installed") < said.index(
        "→ refreshing GitNexus index (incremental)"
    )


def test_on_with_failing_install_indexes_keyword_only(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", runtime_ready=False, install_rc=1)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("unset", ["embeddings", "install"]), ("unset", PLAIN)]
    assert any(s.startswith("✗ embedding runtime install failed (exit 1)") for s in said)


def test_embedding_analyze_failure_retries_once_keyword_only_before_any_clean(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", analyze_embed_rc=5)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    # no backfill sync after a keyword-only fallback in the same run
    assert _rows(calls) == [("auto", EMBED), ("unset", PLAIN)]
    assert any(
        s.startswith("✗ analyze with embeddings failed (exit 5): ")
        and s.endswith(" — retrying keyword-only")
        for s in said
    )
    assert "✓ index refreshed" in said  # the retry's rc is what the existing narration judges


def test_embedding_retry_that_stays_stale_escalates_and_rebuild_also_embeds(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    _foreign_stamping_stub(tmp_path)  # every analyze stamps a foreign repoPath: stale forever
    v, said = _run_on(ctx, repo)
    rows = _rows(calls)
    assert rows[0] == ("auto", EMBED)
    assert ("unset", ["clean", "--force"]) in rows
    assert rows[rows.index(("unset", ["clean", "--force"])) + 1] == ("auto", EMBED)
    assert any(s.startswith("✗ index still stale after analyze") for s in said)
    assert not v.fresh


def test_rebuild_path_embedding_failure_retries_keyword_only(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "feature/x", "lastCommit": "old"})
    )
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", analyze_embed_rc=5)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("unset", ["clean", "--force"]), ("auto", EMBED), ("unset", PLAIN)]
    assert said.count(SKIP_BACKFILL) == 1  # the rebuild path's fallback also skips the sync
    assert "✓ index rebuilt for main" in said


PLAIN_ARM = '"analyze --skip-agents-md --skip-skills --branch main") '


def test_rebuild_after_an_embedding_fallback_runs_keyword_only(tmp_path):
    """Embedding already failed this run: the escalation's rebuild must not
    retry --embeddings (another stall budget under the busy lock). The plain
    incremental analyze stamps a foreign repoPath while the keep-stale marker
    exists; clean --force removes it, so the rebuild heals."""
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    (repo / ".gitnexus" / "keep-stale").write_text("")
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", analyze_embed_rc=5)
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert PLAIN_ARM in text
    node.write_text(
        text.replace(
            PLAIN_ARM,
            PLAIN_ARM + "if [ -e .gitnexus/keep-stale ]; then "
            "/bin/cat > .gitnexus/meta.json <<'EOF'\n"
            '{"branch":"main","lastCommit":"old","repoPath":"/elsewhere"}\n'
            "EOF\n"
            "exit 0; fi; ",
        )
    )
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [
        ("auto", EMBED),
        ("unset", PLAIN),
        ("unset", ["clean", "--force"]),
        ("unset", PLAIN),
    ]
    assert any(s.startswith("✗ index still stale after analyze") for s in said)
    assert "✓ index rebuilt for main" in said
    assert said.count(SKIP_BACKFILL) == 1


def test_embedding_analyze_phase_lines_are_narrated_once_without_pino(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, _ = _embedding_fixture(tmp_path, tmp_path / "home", phase_lines=True)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert said.count("· Generating embeddings") == 1
    assert not any("pino" in s for s in said)
    assert said.index("· Generating embeddings") > said.index(
        "→ refreshing GitNexus index (incremental)"
    )


EMBED_ARM = '"analyze --skip-agents-md --skip-skills --branch main --embeddings") '
SYNC_ARM = '"embeddings sync "*) '


def _silent_arm(tmp_path, arm, shell):
    """Prefix a stub arm with silent shell work (nothing on either pipe)."""
    node = tmp_path / "bin" / "node"
    text = node.read_text()
    assert arm in text
    node.write_text(text.replace(arm, arm + shell))


@pytest.mark.parametrize("touches_store", [True, False])
def test_silent_embedding_analyze_lives_on_store_writes_and_dies_on_the_embed_budget(
    tmp_path, monkeypatch, touches_store
):
    """Off a TTY the embedding phase prints NOTHING (verified on the real CLI), so
    liveness comes from <root>/.gitnexus changing under the _EMBED_STALL_SECONDS
    budget, not from bytes under the wiki one. The wiki budget is shrunk below
    the beat gap, so a child supervised on it would die: surviving proves the
    embed budget is the one consulted. A silent child that keeps writing the
    store survives; one that writes nothing is killed after the embed budget
    and the index still lands keyword-only."""
    import omc.gitnexus as gn

    monkeypatch.setattr(gn, "_EMBED_STALL_SECONDS", 0.5)
    monkeypatch.setattr(gn, "_WIKI_STALL_SECONDS", 0.1)
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    if touches_store:
        # 1.2 s of silence — past the 0.5 s budget — with a new store file every 0.2 s.
        beats = "; ".join(f"/bin/sleep 0.2; : > .gitnexus/beat{i}" for i in range(6))
        _silent_arm(tmp_path, EMBED_ARM, f"{beats}; ")
    else:
        _silent_arm(tmp_path, EMBED_ARM, "/bin/sleep 5; ")
    v, said = _run_on(ctx, repo)
    assert v.fresh
    if touches_store:
        assert _rows(calls) == [("auto", EMBED)]
        assert not any(s.startswith("✗") for s in said)
        assert json.loads((repo / ".gitnexus/meta.json").read_text())["stats"]["embeddings"] == 3
    else:
        assert _rows(calls) == [("auto", EMBED), ("unset", PLAIN)]  # stalled → no sync
        assert (
            f"✗ analyze with embeddings stalled — no progress for {int(gn._EMBED_STALL_SECONDS)}s;"
            " killed — retrying keyword-only"
        ) in said
        assert "✓ index refreshed" in said


def test_embed_stall_budget_is_thirty_minutes():
    import omc.gitnexus as gn

    assert gn._EMBED_STALL_SECONDS == 1800.0


# ─── One-time backfill: embeddings sync (spec 2026-10-27 §6) ────────────────


def _with_nodes(repo, nodes=5):
    """Give the seeded fresh index stats.nodes (and no embeddings): something to embed."""
    meta = repo / ".gitnexus/meta.json"
    meta.write_text(json.dumps({**json.loads(meta.read_text()), "stats": {"nodes": nodes}}))


def test_fresh_index_without_vectors_is_backfilled_once(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # fresh index, nodes but no vectors
    _with_nodes(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    head = _git_out(repo, "rev-parse", "HEAD")
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", ["embeddings", "sync", str(repo)])]
    meta = json.loads((repo / ".gitnexus/meta.json").read_text())
    assert meta["stats"]["embeddings"] == 7 and meta["lastCommit"] == head
    assert "→ embedding the existing index (one-time)…" in said
    assert "✓ index embedded (7 vectors)" in said
    assert "✓ knowledge is current" not in said  # a minutes-long pass is work
    again, said2 = _run_on(ctx, repo)
    assert again.fresh and len(_rows(calls)) == 1  # one JSON read, no second sync
    assert "✓ knowledge is current" in said2


def test_zero_count_also_backfills_but_positive_count_does_not(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    meta = repo / ".gitnexus/meta.json"
    data = json.loads(meta.read_text())
    meta.write_text(json.dumps({**data, "stats": {"nodes": 5, "embeddings": 0}}))
    v, _ = _run_on(ctx, repo)
    assert v.fresh and len(_rows(calls)) == 1
    meta.write_text(json.dumps({**data, "stats": {"nodes": 5, "embeddings": 7}}))
    v, said = _run_on(ctx, repo)
    assert v.fresh and len(_rows(calls)) == 1 and "✓ knowledge is current" in said


def test_index_with_no_embeddable_nodes_is_never_synced(tmp_path):
    """sync over a zero-node index exits 0 with 0 embeddings: re-running it every
    refresh would print ✗ forever and suppress ✓ knowledge is current."""
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    meta = repo / ".gitnexus/meta.json"
    data = json.loads(meta.read_text())
    for stats in ({"nodes": 0, "embeddings": 0}, {"nodes": "5"}, {}):
        meta.write_text(json.dumps({**data, "stats": stats}))
        v, said = _run_on(ctx, repo)
        assert v.fresh and not calls.exists(), stats
        assert "✓ knowledge is current" in said, stats
    meta.write_text(json.dumps({**data, "stats": {"nodes": 5, "embeddings": 0}}))
    v, _ = _run_on(ctx, repo)
    assert v.fresh and _rows(calls) == [("auto", ["embeddings", "sync", str(repo)])]


def test_backfill_after_a_stale_index_heals_runs_once_after_the_clean_verdict(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    v, _ = _run_on(ctx, repo)
    assert v.fresh
    # the incremental analyze already wrote stats.embeddings = 3 → no sync
    assert _rows(calls) == [("auto", EMBED)]


def test_backfill_failure_is_narrated_and_leaves_the_verdict_alone(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _with_nodes(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", sync_rc=4)
    before = (repo / ".gitnexus/meta.json").read_bytes()
    v, said = _run_on(ctx, repo)
    assert v.fresh and (repo / ".gitnexus/meta.json").read_bytes() == before
    assert _rows(calls) == [("auto", ["embeddings", "sync", str(repo)])]
    assert any(s.startswith("✗ embeddings sync failed (exit 4): ") for s in said)
    assert "✓ knowledge is current" not in said


def test_off_never_backfills(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists() and "✓ knowledge is current" in said


def test_embedding_count_reads_whichever_metadata_file_wins(tmp_path):
    import omc.gitnexus as gn

    root = tmp_path / "r"
    (root / ".gitnexus").mkdir(parents=True)
    assert gn._embedding_count(root) == 0
    (root / ".gitnexus/meta.json").write_text('{"stats":{"embeddings":"7"}}')
    assert gn._embedding_count(root) == 0  # non-int is "no vectors"
    (root / ".gitnexus/meta.json").write_text('{"stats":{"embeddings":7}}')
    assert gn._embedding_count(root) == 7
    (root / ".gitnexus/gitnexus.json").write_text('{"stats":{"embeddings":2}}')
    assert gn._embedding_count(root) == 2  # gitnexus.json wins, meta.json ignored


@pytest.mark.parametrize("touches_store", [True, False])
def test_silent_embeddings_sync_lives_on_store_writes_and_dies_on_the_embed_budget(
    tmp_path, monkeypatch, touches_store
):
    """`embeddings sync` may print nothing for long stretches while it embeds, so
    it gets the embedding analyze's liveness: <root>/.gitnexus changing under
    the _EMBED_STALL_SECONDS budget, not the wiki one (shrunk below the beat gap
    here, so surviving proves which budget is consulted). A silent sync that
    keeps writing the store survives and lands its vectors; one that writes
    nothing is reported stalled and the verdict is untouched."""
    import omc.gitnexus as gn

    monkeypatch.setattr(gn, "_EMBED_STALL_SECONDS", 0.5)
    monkeypatch.setattr(gn, "_WIKI_STALL_SECONDS", 0.1)
    _, repo = _repo_with_origin(tmp_path)
    _with_nodes(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    before = (repo / ".gitnexus/meta.json").read_bytes()
    if touches_store:
        # 1.2 s of silence — past the 0.5 s budget — with a new store file every 0.2 s.
        beats = "; ".join(f"/bin/sleep 0.2; : > .gitnexus/beat{i}" for i in range(6))
        _silent_arm(tmp_path, SYNC_ARM, f"{beats}; ")
    else:
        _silent_arm(tmp_path, SYNC_ARM, "/bin/sleep 5; ")
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", ["embeddings", "sync", str(repo)])]
    assert "✓ knowledge is current" not in said  # a sync is work, stalled or not
    if touches_store:
        assert not any(s.startswith("✗") for s in said)
        assert "✓ index embedded (7 vectors)" in said
    else:
        assert (repo / ".gitnexus/meta.json").read_bytes() == before
        assert said == [
            "→ embedding the existing index (one-time)…",
            f"✗ embeddings sync stalled — no progress for {int(gn._EMBED_STALL_SECONDS)}s; killed",
        ]


def test_embedding_fallback_skips_the_backfill_this_run_and_the_next_run_retries(tmp_path):
    """Embedding is demonstrably unavailable in a run whose embedding analyze
    fell back keyword-only: a sync would fail the same way (or hold the busy
    lock for the whole stall budget). Skip it, narrate once; the next refresh
    (index already fresh, no analyze) backfills normally."""
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", analyze_embed_rc=5)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", EMBED), ("unset", PLAIN)]
    assert said.count(SKIP_BACKFILL) == 1
    assert "→ embedding the existing index (one-time)…" not in said
    again, said2 = _run_on(ctx, repo)
    assert again.fresh
    assert _rows(calls)[2:] == [("auto", ["embeddings", "sync", str(repo)])]
    assert SKIP_BACKFILL not in said2 and "✓ index embedded (7 vectors)" in said2
