"""Real Claude registry transitions; no model calls or account credentials."""

import json

import pytest

from .harness import configure_omc, make_work_repo, run_in

pytestmark = pytest.mark.e2e


def _run(container, argv):
    rc, out = run_in(container, argv)
    assert rc == 0, out
    return out


def _old_marketplace(container):
    configure_omc(container, "claude")
    _run(container, ["claude", "plugin", "marketplace", "remove", "oh-my-clanker"])
    _run(
        container,
        [
            "python3",
            "-c",
            "import shutil; from pathlib import Path; "
            "p=Path('/tmp/old-omc'); p.mkdir(); "
            "shutil.copytree('/repo/.claude-plugin', p/'.claude-plugin'); "
            "shutil.copytree('/repo/skills', p/'skills')",
        ],
    )
    _run(container, ["claude", "plugin", "marketplace", "add", "/tmp/old-omc"])
    _run(container, ["claude", "plugin", "install", "omc@oh-my-clanker", "--scope", "user"])


def _repair(container, source, *, cwd=None):
    # Keep the checkout's actual implementation, but select the source via the
    # real uv receipt boundary. No network/model stubs replace the Claude CLI.
    script = """
import json, sys
from pathlib import Path
from omc.plugin import ensure_plugin
from omc.toolctx import ToolContext
source = sys.argv[1]
receipt = Path('/tmp/repair-uv/omc/uv-receipt.toml')
receipt.parent.mkdir(parents=True, exist_ok=True)
key = 'git' if source.startswith('https://') else 'directory'
requirement = '{name = "omc", ' + key + ' = ' + json.dumps(source) + '}'
receipt.write_text('[tool]\\nrequirements = [' + requirement + ']\\n')
ctx = ToolContext.from_env()
ctx.env = {**ctx.env, 'UV_TOOL_DIR': '/tmp/repair-uv'}
print(ensure_plugin(ctx, 'claude', update=True))
"""
    return run_in(
        container,
        ["/root/.local/share/uv/tools/omc/bin/python", "-c", script, source],
        cwd=cwd,
    )


@pytest.mark.parametrize("source", ["/repo", "https://github.com/chris-husse/oh-my-clanker"])
def test_deleted_worktree_marketplace_is_replaced(container, source):
    _old_marketplace(container)
    _run(container, ["rm", "-rf", "/tmp/old-omc"])
    before = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    assert next(p for p in before if p["id"] == "omc@oh-my-clanker")["errors"]

    rc, out = _repair(container, source)
    assert rc == 0, out
    marketplaces = json.loads(
        _run(container, ["claude", "plugin", "marketplace", "list", "--json"])
    )
    omc_marketplace = next(m for m in marketplaces if m["name"] == "oh-my-clanker")
    if source == "/repo":
        assert omc_marketplace["source"] == "directory"
        assert omc_marketplace["path"] == "/repo"
    else:
        assert omc_marketplace["source"] == "github"
        assert omc_marketplace["repo"] == "chris-husse/oh-my-clanker"
    plugins = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    omc = next(p for p in plugins if p["id"] == "omc@oh-my-clanker")
    assert omc["enabled"] and not omc.get("errors"), omc
    assert any(p["id"].startswith("superpowers@") for p in plugins)


def test_conflicting_project_source_is_detected_before_removal(container):
    _old_marketplace(container)
    repo = make_work_repo(container)
    settings = {
        "extraKnownMarketplaces": {
            "oh-my-clanker": {"source": {"source": "directory", "path": "/tmp/old-omc"}},
        },
    }
    _run(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path; import sys; p=Path(sys.argv[1])/'.claude'; "
            "p.mkdir(exist_ok=True); (p/'settings.json').write_text(sys.argv[2])",
            repo,
            json.dumps(settings),
        ],
    )
    before = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    rc, out = _repair(container, "https://github.com/chris-husse/oh-my-clanker", cwd=repo)
    assert rc != 0, out
    assert "project marketplace declaration" in out, out
    after = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    assert after == before, "a conflicting project declaration must not remove user plugins"
    assert _run(container, ["cat", f"{repo}/.claude/settings.json"]) == json.dumps(settings)
    marketplaces = json.loads(
        _run(container, ["claude", "plugin", "marketplace", "list", "--json"])
    )
    assert next(m for m in marketplaces if m["name"] == "oh-my-clanker")["path"] == "/tmp/old-omc"


def test_unavailable_replacement_preserves_existing_plugin(container):
    _old_marketplace(container)
    before = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    rc, out = _repair(container, "/tmp/nonexistent-replacement")
    assert rc != 0, out
    after = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    assert after == before, "failed replacement must preserve the installed plugin"
    marketplaces = json.loads(
        _run(container, ["claude", "plugin", "marketplace", "list", "--json"])
    )
    assert next(m for m in marketplaces if m["name"] == "oh-my-clanker")["path"] == "/tmp/old-omc"
