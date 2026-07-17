import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_claude_plugin_manifest():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "omc"
    # Marketplace-qualified: a bare name resolves only within the declaring
    # marketplace (oh-my-clanker), which never carries a superpowers entry —
    # see docker/PLUGIN-NOTES.md for the confirmed failure mode and fix.
    assert "superpowers@superpowers-marketplace" in data["dependencies"]


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


USER_FACING_SKILLS = ("slug", "start", "finish")
INTERNAL_SKILLS = ("create-mr", "get-mr-description")


def test_skills_have_frontmatter():
    for name in USER_FACING_SKILLS + INTERNAL_SKILLS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        assert m, f"{name}: missing frontmatter"
        assert f"name: {name}" in m.group(1)
        assert "description:" in m.group(1)


def test_internal_skills_marked_internal():
    # The layering convention: internal skills say so in their description so
    # they stay out of user-facing muscle memory (plugins can't hide skills).
    for name in INTERNAL_SKILLS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        assert "Internal" in m.group(1), f"{name}: not marked Internal"
        assert "not meant for direct invocation" in m.group(1)


def test_finish_skill_contract():
    text = (ROOT / "skills" / "finish" / "SKILL.md").read_text()
    for needle in (
        "merge-base",
        "git rebase origin/",
        "reset --soft",
        "create-mr",
        "Close the worktree",
        "review comments",
        "Chat about this",
    ):
        assert needle in text, f"finish skill missing {needle!r}"
    assert "gh pr create" not in text  # never creates the MR/PR


def test_create_mr_skill_contract():
    text = (ROOT / "skills" / "create-mr" / "SKILL.md").read_text()
    for needle in ("get-mr-description", "--force-with-lease", "--amend"):
        assert needle in text, f"create-mr skill missing {needle!r}"
    assert "Do not create the MR/PR" in text


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
