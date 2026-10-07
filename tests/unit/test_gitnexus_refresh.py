"""refresh_knowledge — the ONLY code that repairs the snapshot (spec §2).
Judged by recomputing the verdict, never by exit codes."""

import json
import shutil

import pytest

from omc.config.schema import Config
from omc.gitnexus import refresh_knowledge

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
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1
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


def test_failed_analyze_exit_code_is_narrated_but_verdict_decides(tmp_path):
    """Exit codes are narrated, never trusted: a non-zero analyze that DID fix the
    metadata ends fresh; one that did not escalates like any other stale result."""
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    node.write_text(node.read_text().replace("echo ok\nexit 0\n", "echo ok\nexit 7\n"))
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert any(s.startswith("✗ analyze failed") for s in said)
    assert v.fresh  # the stub wrote fresh metadata despite exit 7
    assert "clean --force" not in calls.read_text()


def test_inverted_store_destroys_first_exactly_one_analyze(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "feature/x", "lastCommit": "old"})
    )
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    recorded = calls.read_text()
    assert recorded.index("clean --force") < recorded.index("analyze")
    assert recorded.count("analyze --skip-agents-md --skip-skills") == 1
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
    assert _commands(calls) == [["analyze", "--skip-agents-md", "--skip-skills"]]
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
        ["analyze", "--skip-agents-md", "--skip-skills"],
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
        ["analyze", "--skip-agents-md", "--skip-skills"],
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
