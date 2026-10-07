"""Live gitnexus layer: index -> explain -> document against /repo (the real
omc codebase baked into the image). The GitNexus dependency itself is
pre-baked into the image at /root/.omc/dependencies/gitnexus (see
Dockerfile.e2e), so gitnexus-ensure exercises its verify path per test."""

from __future__ import annotations

import json
import os
import re

import pytest

from .harness import configure_omc, make_work_repo, require_env, require_token, run_in
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


def test_index_then_explain_on_real_repo(container):
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


def test_document_generates_wiki_docs(container):
    # A SMALL target on purpose: wiki generation is one LLM call per module, so
    # /repo would take tens of minutes. The plumbing under test — provider
    # injection, wiki run, sync into .omc/docs — is fully exercised by a
    # two-module toy repo in a couple of minutes.
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    seed = (
        f"cd {repo} && mkdir -p app && "
        "printf 'def add(a, b):\\n    return a + b\\n\\n\\n"
        "def sub(a, b):\\n    return a - b\\n' > app/calc.py && "
        "printf 'from app.calc import add\\n\\n\\n"
        "def total(xs):\\n    t = 0\\n    for x in xs:\\n        t = add(t, x)\\n    return t\\n'"
        " > app/report.py && git add -A && git commit -qm 'add app'"
    )
    rc, out = run_in(container, ["bash", "-c", seed])
    assert rc == 0, out

    rc, out = _claude_skill(container, "/omc:index", cwd=repo)
    assert rc == 0, out

    rc, out = _claude_skill(container, "/omc:document", cwd=repo, timeout=480)
    assert rc == 0, out

    rc, listing = run_in(
        container,
        ["bash", "-c", f"ls {repo}/.omc/docs/gitnexus/docs/*.md 2>/dev/null | head -5"],
    )
    assert rc == 0 and listing.strip(), (
        f"no markdown docs landed in .omc/docs/gitnexus/docs:\n{out[:2000]}"
    )


def test_explain_the_tool_architecture_judged(container):
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


def _redacted(text: str) -> str:
    """Failure detail for the api-backend test: the real key must never reach pytest
    output, even when the very assertion that would catch a leak is the one failing."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    return text.replace(key, "[redacted]") if key else text


def _assert_no_key(text: str, where: str) -> None:
    # Boolean first: pytest's assertion rewriting would echo both operands of a
    # failing `key not in text`, i.e. print the key it is trying to keep out.
    leaked = os.environ["ANTHROPIC_API_KEY"] in text
    assert not leaked, f"ANTHROPIC_API_KEY leaked into {where}"


def test_document_api_backend_generates_wiki_docs(container):
    """The api documentation backend end to end against the REAL Anthropic
    endpoint through GitNexus's custom provider (spec 2026-10-01 §7). Needs
    ANTHROPIC_API_KEY specifically: configure's probe talks to Anthropic directly."""
    require_env("ANTHROPIC_API_KEY", "put an ANTHROPIC_API_KEY in .env")
    # Deliberately NO require_token("claude"): an OAuth token alone must make this
    # test fail, not pass — the api backend talks to Anthropic directly. (Side
    # note: with ANTHROPIC_API_KEY forwarded, any `claude -p` elsewhere in the
    # session bills that key; pre-existing harness behaviour, recorded in the
    # build ledger.)
    # `--set llm.default=claude`: no documentation key yet → no probe.
    configure_omc(container, "claude")
    # One bash -c script: run_in quotes argv via shlex.join, so "$ANTHROPIC_API_KEY"
    # as a separate argv item would never expand; expanding INSIDE the container
    # keeps the key out of pytest's argv and output. Real validation runs here.
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            "omc configure --set llm.docs.backend=api "
            '--set llm.providers.claude.api_key="$ANTHROPIC_API_KEY"',
        ],
        timeout=120,
    )
    assert rc == 0, _redacted(out)
    assert "✓ key accepted" in _redacted(out)
    assert re.search(r"✓ claude-sonnet-\S+ works", _redacted(out))
    _assert_no_key(out, "command output")

    repo = make_work_repo(container)
    seed = (
        f"cd {repo} && mkdir -p app && "
        "printf 'def add(a, b):\\n    return a + b\\n\\n\\n"
        "def sub(a, b):\\n    return a - b\\n' > app/calc.py && "
        "printf 'from app.calc import add\\n\\n\\n"
        "def total(xs):\\n    t = 0\\n    for x in xs:\\n        t = add(t, x)\\n    return t\\n'"
        " > app/report.py && git add -A && git commit -qm 'add app' && git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", seed])
    assert rc == 0, _redacted(out)

    # The same _run_wiki path omc watch uses, without a second LLM in the loop.
    rc, out = run_in(
        container,
        ["omc", "internal", "gitnexus", "refresh", "--enable-documentation"],
        cwd=repo,
        timeout=600,
    )
    assert rc == 0, _redacted(out)
    assert "→ regenerating documentation via claude api (claude-sonnet-" in _redacted(out)
    # GitNexus's progress lines flowed through run_supervised's on_line into
    # omc's narration: off a TTY the refresh narrates each GitNexus phase change
    # as a `·` line (spec 2026-10-01 fix-doc-false-stall §4.2.5). The final
    # phase is deterministic, so assert on it rather than on a module name.
    assert "· Wiki generation complete" in _redacted(out), _redacted(out)[:2000]
    _assert_no_key(out, "command output")

    rc, listing = run_in(
        container,
        ["bash", "-c", f"ls {repo}/.omc/docs/gitnexus/docs/*.md 2>/dev/null | head -5"],
    )
    assert rc == 0 and listing.strip(), f"no markdown docs landed:\n{_redacted(out)[:2000]}"

    # GitNexus persisted provider/baseUrl/model — and NO key (env-supplied keys are
    # never saved). Fresh container: no prior config.json can carry an apiKey.
    # baseUrl is stored VERBATIM with its trailing slash (wiki.ts at 8de99dc).
    rc, gn = run_in(container, ["cat", "/root/.gitnexus/config.json"])
    assert rc == 0, _redacted(gn)
    saved = json.loads(gn)
    assert saved["provider"] == "custom"
    assert saved["baseUrl"] == "https://api.anthropic.com/v1/"
    assert saved["model"].startswith("claude-sonnet-")
    leaked = "apiKey" in saved
    assert not leaked, "GitNexus persisted an apiKey (value withheld)"
    # `--reasoning-model` (Anthropic 400s on `temperature` for the 5.x models) is
    # persisted by GitNexus too; harmless, but it proves the flag reached the CLI.
    assert saved.get("isReasoningModel") is True

    # omc's own files: key only in secrets.yaml, mode 600, never in config.yaml.
    rc, mode = run_in(container, ["stat", "-c", "%a", "/root/.omc/secrets.yaml"])
    assert rc == 0 and mode.strip() == "600", mode
    rc, cfg_text = run_in(container, ["cat", "/root/.omc/config.yaml"])
    assert rc == 0, _redacted(cfg_text)
    _assert_no_key(cfg_text, "config.yaml")
    assert "backend: api" in cfg_text
