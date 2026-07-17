import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_claude_plugin_manifest():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "omc"
    assert "superpowers" in data["dependencies"]


def test_claude_marketplace_lists_omc():
    data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert data["name"] == "oh-my-clanker"
    assert any(p["name"] == "omc" for p in data["plugins"])


def test_codex_plugin_manifest():
    data = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
    assert data["name"] == "omc"
    assert data["skills"] == "./skills/"


def test_opencode_entry_registers_skills_dir():
    js = (ROOT / ".opencode" / "plugins" / "omc.js").read_text()
    assert "skills" in js and "config" in js


def test_skills_have_frontmatter():
    for name in ("slug", "start"):
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        assert m, f"{name}: missing frontmatter"
        assert f"name: {name}" in m.group(1)
        assert "description:" in m.group(1)


def test_start_skill_contract():
    text = (ROOT / "skills" / "start" / "SKILL.md").read_text()
    for needle in (
        "OMC_SLUG",
        "superpowers:brainstorming",
        "omc start",
        "$ARGUMENTS",
        "merge-base",
    ):
        assert needle in text, f"start skill missing {needle!r}"
