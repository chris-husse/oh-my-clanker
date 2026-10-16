"""A retired saved model is repaired by each real provider before design starts."""

from __future__ import annotations

import json
import re

import pytest
import yaml

from .codex_auth import require_codex_ready
from .harness import configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e

_CONFIG = "/root/.omc/config.yaml"


def _config_text(container) -> str:
    rc, content = run_in(container, ["cat", _CONFIG], timeout=240)
    assert rc == 0, f"global config is missing at {_CONFIG}: {content}"
    return content


def _retire_orchestrator(container, provider: str, pinned: str, retired: str) -> None:
    config = yaml.safe_load(_config_text(container))
    saved = config["llm"]["providers"][provider]
    assert saved["model"] == pinned, saved
    assert all(value == pinned for value in saved["tasks"].values()), saved
    saved["model"] = retired
    replacement = yaml.safe_dump(config, sort_keys=False)
    rc, out = run_in(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])",
            _CONFIG,
            replacement,
        ],
        timeout=240,
    )
    assert rc == 0, f"could not write the retired ID directly to {_CONFIG}: {out}"


def _catalog_models(container, provider: str) -> dict[str, set[str]]:
    if provider == "claude":
        rc, status_text = run_in(container, ["claude", "auth", "status"], timeout=240)
        assert rc == 0, f"Claude auth status failed: {status_text}"
        status = json.loads(status_text)
        if status.get("authMethod") == "api_key" and not status.get("orgId"):
            # Claude Code 2.1.286 does not cache a model catalog for API-key
            # auth. Check the saved ID against Anthropic's independent list.
            rc, raw = run_in(
                container, ["python3", "-m", "tests.e2e.claude_catalog"], cwd="/repo", timeout=240
            )
            assert rc == 0, raw
        else:
            script = """\
import json, pathlib, sys
directory = pathlib.Path(sys.argv[1]) / 'cache' / 'model-catalog'
paths = list(directory.glob(sys.argv[2] + '-*-cc.json'))
if not paths:
    sys.exit('Claude catalog missing; open claude once to fetch it, then rerun the E2E gate')
catalogs = [(json.loads(path.read_text()), path) for path in paths]
current, _ = max(catalogs, key=lambda pair: pair[0]['fetchedAt'])
print(json.dumps({entry['id']: [option['id'] for option in
                  entry['thinking']['effort_options']]
                  for entry in current['catalog']['config']['models']}))
"""
            rc, raw = run_in(
                container,
                ["python3", "-c", script, status["configDirectory"], status["orgId"]],
                timeout=240,
            )
            assert rc == 0, raw
    else:
        script = """\
import json, os, pathlib, sys
home = pathlib.Path(os.environ.get('CODEX_HOME') or pathlib.Path.home() / '.codex')
path = home / 'models_cache.json'
if not path.is_file():
    sys.exit('Codex catalog missing; run `just codex-login`, then rerun '
             '`just codex-gate tests/e2e/test_e2e_reconcile.py`')
print(json.dumps({entry['slug']: [level['effort'] for level in
                  entry['supported_reasoning_levels']]
                  for entry in json.loads(path.read_text())['models']
                  if entry['visibility'] == 'list'}))
"""
        rc, raw = run_in(container, ["python3", "-c", script], timeout=240)
        assert rc == 0, raw
    models = {model_id: set(efforts) for model_id, efforts in json.loads(raw).items()}
    assert models, f"{provider} returned an empty current model catalog"
    return models


def _worktree_path(container, repo: str) -> str:
    rc, listing = run_in(
        container, ["git", "-C", repo, "worktree", "list", "--porcelain"], timeout=240
    )
    assert rc == 0, listing
    for entry in listing.strip().split("\n\n"):
        lines = entry.splitlines()
        if any(line.startswith("branch refs/heads/feature/proj-1") for line in lines):
            return lines[0].removeprefix("worktree ")
    raise AssertionError(f"design did not create the PROJ-1 worktree:\n{listing}")


def _session_files(container, provider: str, worktree: str) -> list[str]:
    script = """\
import json, os, pathlib, sys
provider, worktree = sys.argv[1:]
home = pathlib.Path.home()
root = (home / '.claude' / 'projects' if provider == 'claude' else
        pathlib.Path(os.environ.get('CODEX_HOME') or home / '.codex') / 'sessions')
paths = [str(path) for path in root.rglob('*.jsonl') if worktree.encode() in path.read_bytes()]
print(json.dumps(paths))
"""
    rc, raw = run_in(container, ["python3", "-c", script, provider, worktree], timeout=240)
    assert rc == 0, raw
    return json.loads(raw)


def _prove_recovery(container, provider: str, retired: str) -> None:
    require_token(provider)
    pinned = configure_omc(container, provider)
    if provider == "codex":
        require_codex_ready(container)
    wire_mcp(container, provider, "ok")
    repo = make_work_repo(container)
    _retire_orchestrator(container, provider, pinned, retired)

    rc, out = run_in(container, ["omc", "design", "PROJ-1", "--headless"], cwd=repo, timeout=240)
    assert rc == 0, f"{provider} design failed after stale-model recovery:\n{out}"
    worktree = _worktree_path(container, repo)
    assert _session_files(container, provider, worktree), (
        f"{provider} left no session artifact for {worktree}; design output:\n{out[-2000:]}"
    )

    current_models = _catalog_models(container, provider)
    assert retired not in current_models, f"retired fixture {retired} is still listed by {provider}"
    repaired_text = _config_text(container)
    saved = yaml.safe_load(repaired_text)["llm"]["providers"][provider]
    repaired = saved["model"]
    model_id, separator, effort = repaired.partition(":")
    assert re.fullmatch(
        r"claude-[A-Za-z0-9.-]+" if provider == "claude" else r"gpt-[A-Za-z0-9.-]+", model_id
    ), repaired
    assert model_id in current_models, (
        f"saved ID {model_id!r} is absent from {provider}'s live catalog"
    )
    assert not separator or effort in current_models[model_id], (
        f"saved effort {effort!r} is unsupported for {model_id}"
    )
    assert all(value == pinned for value in saved["tasks"].values()), saved
    assert f"→ model: {retired} is unavailable;" in out, out

    # The second entry is a real provider-backed check. It must see the saved
    # full ID as current without another refresh, remap turn, or YAML write.
    rc, second = run_in(container, ["omc", "design", "PROJ-1", "--dry-run"], cwd=repo, timeout=240)
    assert rc == 0, second
    assert f"✓ {provider} models current" in second, second
    assert "config updated" not in second and "→ refreshing " not in second, second
    assert _config_text(container) == repaired_text, "a current second launch rewrote config.yaml"


@pytest.mark.e2e_provider("claude")
def test_claude_design_recovers_retired_orchestrator(container):
    _prove_recovery(container, "claude", "claude-opus-4-1")


@pytest.mark.e2e_provider("codex")
def test_codex_design_recovers_retired_orchestrator(container):
    _prove_recovery(container, "codex", "gpt-5.1-sol")
