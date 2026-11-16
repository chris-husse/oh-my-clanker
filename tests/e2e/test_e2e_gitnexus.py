"""Live gitnexus layer: index -> explain against /repo (the real
omc codebase baked into the image). The GitNexus dependency itself is
pre-baked into the image at /root/.omc/dependencies/gitnexus (see
Dockerfile.e2e), so gitnexus-ensure exercises its verify path per test."""

from __future__ import annotations

import json
import re

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e

_CLI = "/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js"


def _claude_skill(container, prompt, *, cwd, timeout=900):
    return run_in(
        container,
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
        ],
        cwd=cwd,
        timeout=timeout,
    )


@pytest.mark.expensive
@pytest.mark.timeout(900)
def test_index_then_explain_on_real_repo(container):
    """Expensive tier: the always-on embedding (spec 2026-11-16 §8) makes the
    index step of /repo slow, past the 300 s ceiling."""
    require_token("claude")
    configure_omc(container, "claude")

    rc, _ = run_in(container, ["node", _CLI, "--version"])
    assert rc == 0, "pre-baked GitNexus CLI missing from image"

    rc, out = run_in(
        container, ["git", "-C", "/repo", "worktree", "add", "/tmp/wt-e2e", "-b", "e2e-wt"]
    )
    assert rc == 0, out

    rc, original_head = run_in(container, ["git", "-C", "/repo", "rev-parse", "HEAD"])
    assert rc == 0, original_head
    submodule_source = make_work_repo(container)
    for command in [
        [
            "python3",
            "-c",
            "from pathlib import Path; "
            "Path('/repo/scope-untracked.py').write_text('def untracked(): return 1\\n'); "
            f"Path('{submodule_source}/scope.py').write_text('def nested(): return 2\\n')",
        ],
        ["git", "-C", submodule_source, "add", "scope.py"],
        ["git", "-C", submodule_source, "commit", "-qm", "add indexable submodule source"],
        [
            "git",
            "-C",
            "/repo",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            submodule_source,
            "scope-submodule",
        ],
    ]:
        rc, out = run_in(container, command)
        assert rc == 0, out

    rc, out = _claude_skill(container, "/omc:index", cwd="/repo")
    assert rc == 0, out
    rc, _ = run_in(container, ["test", "-d", "/repo/.gitnexus"])
    assert rc == 0, f"analyze produced no .gitnexus/ index:\n{out[:2000]}"
    rc, storage = run_in(
        container,
        [
            "python3",
            "-c",
            (
                "import json, os; from pathlib import Path; "
                "root=Path('/repo/.gitnexus'); "
                "meta=root/'gitnexus.json'; meta=meta if meta.exists() else root/'meta.json'; "
                "home=Path(os.environ.get('GITNEXUS_HOME', str(Path.home()/'.gitnexus'))); "
                "print(json.dumps({'metadata': "
                "json.loads(meta.read_text()) if meta.exists() else None, "
                "'pointer': (root/'store.json').exists(), 'stores': (home/'stores').exists()}))"
            ),
        ],
    )
    assert rc == 0, storage
    rc, head = run_in(container, ["git", "-C", "/repo", "rev-parse", "HEAD"])
    assert rc == 0, head
    assert head.strip() == original_head.strip(), "scope setup/index moved /repo HEAD"
    artifacts = json.loads(storage)
    assert artifacts["metadata"] is not None, storage
    assert artifacts["metadata"]["lastCommit"] == head.strip(), storage
    assert not artifacts["pointer"], storage
    assert not artifacts["stores"], storage
    rc, listed = run_in(container, ["node", _CLI, "list"], cwd="/repo")
    # The registry prints `Path:    /repo` per entry; a bare "repo" also matched
    # the "No indexed repositories found" notice and let a broken index through.
    assert rc == 0 and re.search(r":\s+/repo\s*$", listed, re.M), (
        f"repo not in gitnexus registry:\n{listed[:800]}"
    )

    # Keep the CLI banner and freshness notice on stderr out of the JSON payload.
    rc, files_json = run_in(
        container,
        [
            "bash",
            "-c",
            "omc internal gitnexus cypher 'MATCH (f:File) RETURN f.filePath' "
            "2>/tmp/scope-cypher.stderr; rc=$?; "
            'if [ "$rc" -ne 0 ]; then cat /tmp/scope-cypher.stderr >&2; fi; exit "$rc"',
        ],
        cwd="/repo",
    )
    assert rc == 0, files_json
    files = json.loads(files_json)
    assert isinstance(files, dict), files
    assert isinstance(files.get("row_count"), int) and files["row_count"] > 0, files
    assert isinstance(files.get("markdown"), str), files
    rows = files["markdown"].splitlines()
    assert rows[:2] == ["| f.filePath |", "| --- |"], files
    assert len(rows[2:]) == files["row_count"], files
    assert all(row.startswith("| ") and row.endswith(" |") for row in rows[2:]), files
    paths = {row[2:-2] for row in rows[2:]}
    assert "src/omc/gitnexus.py" in paths, paths
    unexpected = sorted(
        path
        for path in paths
        if path == "scope-untracked.py" or path.startswith("scope-submodule/")
    )
    assert not unexpected, f"out-of-scope File nodes: {unexpected}"
    metadata_bytes = (
        "from pathlib import Path; root=Path('/repo/.gitnexus'); "
        "meta=root/'gitnexus.json'; meta=meta if meta.exists() else root/'meta.json'; "
        "print(meta.read_bytes().hex())"
    )
    rc, before = run_in(container, ["python3", "-c", metadata_bytes])
    assert rc == 0, before
    rc, out = run_in(container, ["git", "-C", "/repo", "switch", "-c", "e2e-analyze-mismatch"])
    assert rc == 0, out
    try:
        rc, out = run_in(
            container,
            ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"],
            cwd="/repo",
        )
        assert rc != 0, out
        assert '--branch "main" does not match the checked-out branch "e2e-analyze-mismatch"' in out
        rc, after = run_in(container, ["python3", "-c", metadata_bytes])
        assert rc == 0, after
        assert after == before
        assert json.loads(bytes.fromhex(after.strip()))["lastCommit"] == head.strip(), after
        rc, out = run_in(container, ["test", "!", "-e", "/repo/.gitnexus/branches"])
        assert rc == 0, "analyze created a branch slot for a mismatched label"
        rc, out = run_in(container, ["omc", "internal", "gitnexus", "refresh"], cwd="/repo")
        assert rc == 1, out
        assert "requires the primary checkout to be on main (currently e2e-analyze-mismatch)" in out
    finally:
        rc, out = run_in(container, ["git", "-C", "/repo", "switch", "main"])
        assert rc == 0, out

    question = "how does omc start derive the branch slug?"
    rc, answer = _claude_skill(container, f"/omc:explain {question}", cwd="/repo")
    assert rc == 0, answer
    verdict = judge(
        container,
        "claude",
        scenario=f"/omc:explain answered {question!r} using the project's GitNexus "
        "knowledge graph (the repo implements slug derivation in src/omc/slug.py: "
        "a headless provider call runs the packaged slug skill and the CLI parses "
        "an OMC_SLUG verdict, re-sanitizes, and prefixes the branch).",
        rubric=[
            "the answer describes the actual slug flow (headless provider call with "
            "the slug skill and/or OMC_SLUG verdict parsing / fetch_slug)",
            "the answer cites at least one real file or symbol (e.g. slug.py, "
            "fetch_slug, parse_verdict, start.py)",
            "the answer is not a refusal, an error dump, or a generic essay",
        ],
        artifacts=answer,
    )
    assert verdict["passed"], verdict["reasons"]


@pytest.mark.expensive
@pytest.mark.timeout(900)
def test_explain_the_tool_architecture_judged(container):
    """Expensive tier: the always-on embedding (spec 2026-11-16 §8) makes the
    index step of /repo slow, past the 300 s ceiling."""
    require_token("claude")
    configure_omc(container, "claude")

    rc, out = _claude_skill(container, "/omc:index", cwd="/repo")
    assert rc == 0, out

    question = (
        "explain the omc tool: what happens end to end when I run omc start, "
        "and which modules are involved?"
    )
    rc, answer = _claude_skill(container, f"/omc:explain {question}", cwd="/repo", timeout=900)
    assert rc == 0, answer
    verdict = judge(
        container,
        "claude",
        scenario="/omc:explain was asked to explain omc's own start pipeline using "
        "the repo's GitNexus graph. Ground truth: cli.py gates config -> start.py "
        "probes tools (probe.py) and the plugin (plugin.py) -> slug.py runs a "
        "headless provider call and parses an OMC_SLUG verdict -> worktree.py "
        "drives wt -> shells/terminals exec the seeded, named session.",
        rubric=[
            "the answer describes the real pipeline order (probe/plugin check, "
            "slug via headless LLM call, worktree creation, seeded session launch)",
            "it cites at least three real modules or symbols (e.g. start.py, "
            "slug.py, fetch_slug, providers, worktree.py, ensure_plugin)",
            "it is a coherent architecture explanation, not an error dump, a "
            "refusal, or generic filler",
        ],
        artifacts=answer,
    )
    assert verdict["passed"], verdict["reasons"]


_INDEX_META = (
    "import json; from pathlib import Path; root=Path('/repo/.gitnexus'); "
    "meta=root/'gitnexus.json'; meta=meta if meta.exists() else root/'meta.json'; "
    "print(meta.read_text())"
)


def _knowledge_reason_codes(out: str) -> set[str]:
    """Reason codes from every OMC_KNOWLEDGE verdict line in combined output."""
    codes: set[str] = set()
    for line in out.splitlines():
        if line.startswith("OMC_KNOWLEDGE "):
            verdict = json.loads(line[len("OMC_KNOWLEDGE ") :])
            codes.update(reason["code"] for reason in verdict["reasons"])
    return codes


def test_default_refresh_embeds_the_real_repo(container):
    """Real GitNexus, real local embedding runtime, no LLM (spec 2026-10-27 §9).
    The runtime, model and VECTOR extension are pre-baked into the image
    (Dockerfile.e2e), so this needs no network for them. Artifacts, not
    transcripts: stats.embeddings on the FLAT slot, and a query whose stderr
    no longer carries GitNexus's keyword-only warning.

    Runs under the mechanical 300 s pytest-timeout ceiling: every run_in
    carries an explicit budget and they sum to 295 s — 1 x 10 (configure) +
    7 x 5 (cat/git/python3/test) + 215 (first refresh) + 25 (query) + 10
    (second refresh) — so no hung step can outlive the ceiling deep inside
    docker-py's socket read. Measured alone (2026-11-02): first refresh
    112.5 s, query 1.0 s, second refresh 0.2 s. The first refresh gets the
    slack because it is the CPU-bound step the parallel suite slows down: at
    150 s it timed out (rc 124) under `just e2e-tests`."""
    # Deliberately NOT configure_omc: its E2E model pin is a live `claude` probe
    # and this test spends no LLM call. `llm.default` alone probes nothing and is
    # the configured state `omc internal gitnexus refresh` gates on.
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], timeout=10)
    assert rc == 0, out
    rc, cfg_text = run_in(container, ["cat", "/root/.omc/config.yaml"], timeout=5)
    assert rc == 0 and "gitnexus" not in cfg_text, cfg_text  # embedding needs no option
    rc, head = run_in(container, ["git", "-C", "/repo", "rev-parse", "HEAD"], timeout=5)
    assert rc == 0, head

    # First index of /repo: a default refresh embeds every node once (about a
    # minute or two of CPU on the pre-baked model; 215 s is the budget, see docstring).
    rc, out = run_in(
        container, ["omc", "internal", "gitnexus", "refresh"], cwd="/repo", timeout=215
    )
    assert rc == 0, out
    assert "retrying keyword-only" not in out, out  # the embedding analyze itself succeeded
    assert "indexing keyword-only" not in out, out  # the pre-baked runtime was found
    rc, meta_text = run_in(container, ["python3", "-c", _INDEX_META], timeout=5)
    assert rc == 0, meta_text
    meta = json.loads(meta_text)
    assert meta["lastCommit"] == head.strip(), meta
    assert isinstance(meta.get("stats", {}).get("embeddings"), int), meta
    assert meta["stats"]["embeddings"] > 0, meta
    rc, _ = run_in(container, ["test", "!", "-e", "/repo/.gitnexus/branches"], timeout=5)
    assert rc == 0, "embedding analyze created a branch sub-slot; the flat slot must be the index"

    # The runtime and model live under ~/.omc, never ~/.gitnexus (Decision 4).
    for rel in (
        "runtime/node_modules/@huggingface/transformers/package.json",
        "runtime/node_modules/onnxruntime-node/package.json",
    ):
        rc, _ = run_in(
            container,
            ["test", "-f", f"/root/.omc/dependencies/gitnexus-embeddings/{rel}"],
            timeout=5,
        )
        assert rc == 0, rel
    rc, _ = run_in(container, ["test", "!", "-e", "/root/.gitnexus/embedding-runtime"], timeout=5)
    assert rc == 0, "a runtime landed in ~/.gitnexus behind omc's back"

    # A semantic query: exit 0 and no keyword-only warning on stderr. SIGKILL
    # after 20 s because GNU timeout signals its whole process group: a
    # GitNexus child that never exits (its embedding sidecar is a forked IPC
    # child) then fails here with rc 137 instead of as pytest's 300 s ceiling
    # deep inside docker-py's socket read. run_in's own 25 s wrapper sits just
    # outside so the KILL, not its TERM, is what ends a hung query.
    rc, out = run_in(
        container,
        [
            "timeout",
            "-s",
            "KILL",
            "20",
            "omc",
            "internal",
            "gitnexus",
            "query",
            "how is the knowledge snapshot repaired",
        ],
        cwd="/repo",
        timeout=25,
    )
    assert rc == 0, f"rc {rc} (137 = the GitNexus query never exited):\n{out[-2000:]}"
    assert "keyword-only" not in out, out
    # The refresh left the index fresh, so the query's verdict carries no reason.
    assert _knowledge_reason_codes(out) == set(), out

    # Second refresh: nothing to do, no second embedding pass.
    rc, out = run_in(container, ["omc", "internal", "gitnexus", "refresh"], cwd="/repo", timeout=10)
    assert rc == 0, out
    assert "✓ knowledge is current" in out, out
    assert "embedding the existing index" not in out, out


_REPO_META = (
    "import json, sys; from pathlib import Path; root=Path(sys.argv[1])/'.gitnexus'; "
    "meta=root/'gitnexus.json'; meta=meta if meta.exists() else root/'meta.json'; "
    "print(meta.read_text())"
)

_SOURCES = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
files = {
    "app/__init__.py": "",
    "app/greeting.py": "def greeting(name):\n    return f'hello {name}'\n",
    "app/cli.py": "from app.greeting import greeting\n\n\ndef main():\n"
    "    print(greeting('world'))\n",
    "src/proto/gen/user_pb2.py": "class User:\n    def SerializeToString(self):\n"
    "        return b''\n",
    ".gitnexusignore": "src/proto/gen/\n",
}
for name, text in files.items():
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
"""


def _repo_meta(container, repo) -> dict:
    rc, meta_text = run_in(container, ["python3", "-c", _REPO_META, repo], timeout=5)
    assert rc == 0, meta_text
    return json.loads(meta_text)


def test_refresh_backfills_a_keyword_only_index_and_honors_gitnexusignore(container):
    """Real GitNexus, no LLM: an index built keyword-only (before embedding was always
    on) gains its vectors once through the one-time backfill (`embeddings sync`),
    without moving lastCommit; the File nodes honor `.gitnexusignore`; a second
    refresh has nothing to do.

    Runs under the mechanical 300 s pytest-timeout ceiling: every run_in carries an
    explicit budget and they sum to 290 s — 10 (configure) + 5 (repo) + 10 (sources
    and push) + 5 (HEAD) + 45 (the keyword-only analyze of five files) + 2 x 5 (meta)
    + 150 (first refresh: the pre-baked model loads, a few dozen nodes embed) + 25
    (cypher) + 30 (second refresh: one JSON read, but GitNexus's CLI still boots). The
    first refresh gets the slack, as in test_default_refresh_embeds_the_real_repo."""
    # As test_default_refresh_embeds_the_real_repo: `llm.default` alone, no model probe.
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], timeout=10)
    assert rc == 0, out
    repo = make_work_repo(container, "/work/ignored-repo", timeout=5)
    rc, out = run_in(container, ["python3", "-c", _SOURCES, repo], timeout=5)
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["bash", "-c", "git add -A && git commit -qm 'app and generated code' && git push -q"],
        cwd=repo,
        timeout=5,
    )
    assert rc == 0, out
    rc, head = run_in(container, ["git", "-C", repo, "rev-parse", "HEAD"], timeout=5)
    assert rc == 0, head
    # Keyword-only: the same analyze lifecycle_helpers._fixture runs, without --embeddings.
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=45,
    )
    assert rc == 0, f"keyword-only analyze failed:\n{out[-1800:]}"
    before = _repo_meta(container, repo)
    assert before["lastCommit"] == head.strip(), before
    assert not before.get("stats", {}).get("embeddings"), before  # no vectors yet

    rc, out = run_in(container, ["omc", "internal", "gitnexus", "refresh"], cwd=repo, timeout=150)
    assert rc == 0, out
    assert "embedding the existing index (one-time)" in out, out  # the backfill, no analyze
    assert "✓ index embedded" in out, out
    after = _repo_meta(container, repo)
    assert isinstance(after.get("stats", {}).get("embeddings"), int), after
    assert after["stats"]["embeddings"] > 0, after
    assert after["lastCommit"] == head.strip(), after

    # Keep the CLI banner and freshness notice on stderr out of the JSON payload.
    rc, files_json = run_in(
        container,
        [
            "bash",
            "-c",
            "omc internal gitnexus cypher 'MATCH (f:File) RETURN f.filePath' "
            "2>/tmp/ignore-cypher.stderr; rc=$?; "
            'if [ "$rc" -ne 0 ]; then cat /tmp/ignore-cypher.stderr >&2; fi; exit "$rc"',
        ],
        cwd=repo,
        timeout=25,
    )
    assert rc == 0, files_json
    files = json.loads(files_json)
    rows = files["markdown"].splitlines()
    assert rows[:2] == ["| f.filePath |", "| --- |"], files
    paths = {row[2:-2] for row in rows[2:]}
    assert {"app/greeting.py", "app/cli.py"} <= paths, paths
    ignored = sorted(path for path in paths if path.startswith("src/proto/gen/"))
    assert not ignored, f".gitnexusignore'd File nodes: {ignored}"

    rc, out = run_in(container, ["omc", "internal", "gitnexus", "refresh"], cwd=repo, timeout=30)
    assert rc == 0, out
    assert "✓ knowledge is current" in out, out
    assert "embedding the existing index" not in out, out
