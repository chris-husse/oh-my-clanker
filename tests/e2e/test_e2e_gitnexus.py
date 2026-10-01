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

    rc, out = _claude_skill(container, "/omc:index", cwd="/repo")
    assert rc == 0, out
    rc, _ = run_in(container, ["test", "-d", "/repo/.gitnexus"])
    assert rc == 0, f"analyze produced no .gitnexus/ index:\n{out[:2000]}"
    rc, listed = run_in(container, ["node", _CLI, "list"], cwd="/repo")
    assert rc == 0 and "repo" in listed, f"repo not in gitnexus registry:\n{listed[:800]}"

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
