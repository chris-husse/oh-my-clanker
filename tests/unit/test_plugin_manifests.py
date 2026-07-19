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


USER_FACING_SKILLS = (
    "slug",
    "start",
    "plan",
    "implement",
    "finish",
    "build",
    "verify",
    "review",
    "index",
    "document",
    "explain",
    "investigate",
    "rebase-main",
    "check-wt-config",
    "integrate",
)
INTERNAL_SKILLS = (
    "create-mr",
    "get-mr-description",
    "squash",
    "spec",
    "gitnexus-ensure",
    "gitnexus-index",
    "gitnexus-document",
    "gitnexus-explain",
)


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
        "rebase-main",  # the inline rebase was replaced by the rebase-main skill
        "create-mr",
        "Close the worktree",
        "review comments",
        "Chat about this",
    ):
        assert needle in text, f"finish skill missing {needle!r}"
    assert "gh pr create" not in text  # never creates the MR/PR
    # squash is delegated, then stages run build -> verify -> review, then push
    order = [text.index("`squash`"), text.index("`build`"), text.index("`verify`")]
    order += [text.index("`review`"), text.index("`create-mr`")]
    assert order == sorted(order), "finish must order squash -> build -> verify -> review -> push"


def test_stage_proxy_contract():
    for stage in ("build", "verify", "review"):
        text = (ROOT / "skills" / stage / "SKILL.md").read_text()
        for needle in (f".omc/skills/{stage}", "OMC_STAGE", '"configured"'):
            assert needle in text, f"{stage} proxy missing {needle!r}"
        assert "nothing to do" in text  # unconfigured is a pass, not a failure


def test_squash_skill_contract():
    text = (ROOT / "skills" / "squash" / "SKILL.md").read_text()
    for needle in ("reset --soft", "OMC_SQUASH", "rev-list --count"):
        assert needle in text, f"squash skill missing {needle!r}"


def test_dogfood_build_stage():
    text = (ROOT / ".omc" / "skills" / "build" / "SKILL.md").read_text()
    assert "just build" in text


def test_create_mr_skill_contract():
    text = (ROOT / "skills" / "create-mr" / "SKILL.md").read_text()
    for needle in ("get-mr-description", "--force-with-lease", "--amend"):
        assert needle in text, f"create-mr skill missing {needle!r}"
    assert "Do not create the MR/PR" in text


def test_start_skill_contract():
    text = (ROOT / "skills" / "start" / "SKILL.md").read_text()
    for needle in (
        "OMC_SLUG",
        "omc:plan",
        "omc start",
        "$ARGUMENTS",
        "merge-base",
    ):
        assert needle in text, f"start skill missing {needle!r}"
    # start hands off to plan; plan owns the brainstorming handoff now
    assert "Invoke `superpowers:brainstorming`" not in text


def test_gitnexus_ensure_contract():
    text = (ROOT / "skills" / "gitnexus-ensure" / "SKILL.md").read_text()
    assert "https://github.com/chris-husse/GitNexus.git" in text  # the ONLY source
    assert "REFUSE" in text  # unapproved origins are refused, never re-pointed
    assert text.index("gitnexus-shared") < text.index("npm ci"), (
        "shared sibling deps must install before the main build"
    )


def test_gitnexus_index_contract():
    text = (ROOT / "skills" / "gitnexus-index" / "SKILL.md").read_text()
    for needle in ("--skip-agents-md", "--skip-skills", "git worktree list", "primary"):
        assert needle in text, f"gitnexus-index missing {needle!r}"


def test_gitnexus_document_contract():
    text = (ROOT / "skills" / "gitnexus-document" / "SKILL.md").read_text()
    for needle in ("--provider", ".omc/docs/gitnexus/docs", ".gitnexus/wiki"):
        assert needle in text, f"gitnexus-document missing {needle!r}"
    assert "openai" in text and "default" in text  # never fall through to it


def test_gitnexus_explain_contract():
    text = (ROOT / "skills" / "gitnexus-explain" / "SKILL.md").read_text()
    assert "omc internal gitnexus" in text  # queries go through the proxy
    assert "--repo" not in text  # scoping is the proxy's job, not prose
    assert "--branch" not in text
    assert "node <CLI> query" not in text  # no raw CLI recipes remain


def test_explain_user_facing_contract():
    text = (ROOT / "skills" / "explain" / "SKILL.md").read_text()
    for needle in (".omc/skills/explain-context", "gitnexus-explain", "$ARGUMENTS"):
        assert needle in text, f"explain missing {needle!r}"


def test_investigate_skill_contract():
    text = (ROOT / "skills" / "investigate" / "SKILL.md").read_text()
    for needle in (
        "/omc:investigate <environment> <prompt>",
        ".omc/skills/investigation-context",
        "$ARGUMENTS",
        "worker-mission.md",
        "read-only",
        "/omc:integrate",
        "standard coding tier",
        "environments the project defines",
        "/tmp/omc-investigations/",
        "/omc:explain",
    ):
        assert needle in text, f"investigate missing {needle!r}"
    # required context hook: refusal, not graceful degradation
    assert "REFUSE" in text
    # env names are the project's, never omc's
    assert "opaque" in text
    # the worker template exists and stays generic (no project namespaces)
    mission = (ROOT / "skills" / "investigate" / "worker-mission.md").read_text()
    for needle in ("<env>", "<mission>", "FORBIDDEN", "verbatim"):
        assert needle in mission, f"worker-mission missing {needle!r}"
    assert "cops" not in mission


def test_plan_skill_contract():
    text = (ROOT / "skills" / "plan" / "SKILL.md").read_text()
    for needle in (
        "/omc:explain",
        "superpowers:brainstorming",
        "primer",
        "$ARGUMENTS",
        "OMC_SLUG",
        "non-fatal",
        "model-tier",
    ):
        assert needle in text, f"plan skill missing {needle!r}"
    # composition rule: explain is called as a command, never unpacked
    assert "never reach into" in text


def test_implement_skill_contract():
    text = (ROOT / "skills" / "implement" / "SKILL.md").read_text()
    for needle in (
        "`spec`",
        "writing-plans",
        "subagent-driven-development",
        "finish",
        "silently resume",
        "/omc:explain",
        "model-tier policy",
        "`Model:`",
        "top tier",
    ):
        assert needle in text, f"implement skill missing {needle!r}"
    # phases run strictly spec -> plan -> build -> ship
    order = [
        text.index("`spec`"),
        text.index("writing-plans"),
        text.index("subagent-driven-development"),
        text.index("`finish`"),
    ]
    assert order == sorted(order), "implement must order spec -> plan -> build -> ship"


def test_index_and_document_delegate():
    assert "gitnexus-index" in (ROOT / "skills" / "index" / "SKILL.md").read_text()
    assert "gitnexus-document" in (ROOT / "skills" / "document" / "SKILL.md").read_text()


def test_dogfood_stage_and_context_skills():
    for name, needle in (
        ("build", "just build"),
        ("verify", "test_e2e_smoke"),
        ("review", "ToolContext"),
        ("explain-context", "docs/superpowers/specs"),
    ):
        text = (ROOT / ".omc" / "skills" / name / "SKILL.md").read_text()
        assert needle in text, f".omc/skills/{name} missing {needle!r}"


def test_rebase_main_skill_contract():
    text = (ROOT / "skills" / "rebase-main" / "SKILL.md").read_text()
    for needle in ("omc internal rebase-main", "OMC_REBASE_MAIN", "rc 3", "conflict"):
        assert needle in text, f"rebase-main skill missing {needle!r}"
    assert "rsync" not in text  # the mirror is Python; the skill never shells rsync


def test_check_wt_config_skill_contract():
    text = (ROOT / "skills" / "check-wt-config" / "SKILL.md").read_text()
    for needle in ("omc internal wt-template", ".config/wt.toml", "never edit"):
        assert needle in text, f"check-wt-config skill missing {needle!r}"


def test_finish_starts_with_rebase_main():
    text = (ROOT / "skills" / "finish" / "SKILL.md").read_text()
    assert "rebase-main" in text
    order = [text.index("`rebase-main`"), text.index("`squash`"), text.index("`create-mr`")]
    assert order == sorted(order), "finish must order rebase-main -> squash -> push"


def test_integrate_skill_describes_chain_v2():
    text = (ROOT / "skills" / "integrate" / "SKILL.md").read_text()
    assert ".omc/internal/AGENTS.md" not in text  # v1 layer is retired
    assert "distribution" in text or "install" in text  # points at the v2 chain


def test_integrate_skill_contract():
    text = (ROOT / "skills" / "integrate" / "SKILL.md").read_text()
    for needle in (
        ".omc/skills/build",
        ".omc/skills/verify",
        ".omc/skills/review",
        ".omc/skills/explain-context",
        ".omc/skills/investigation-context",
        ".omc/config/AGENTS.md",
        ".config/wt.toml",
        "check-wt-config",
        "omc configure",
        "/omc:index",
        "explicit approval",
    ):
        assert needle in text, f"integrate skill missing {needle!r}"
    # both modes + headless discipline
    assert "review" in text.lower() and "fresh" in text.lower()
    assert "zero writes" in text.lower() or "no writes" in text.lower()
    assert "--defaults" in text  # the do-NOT-reset-config warning


def test_distribution_agents_model_tier_policy():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    for needle in (
        "model-tier policy",
        "top tier",
        "heavy coding tier",
        "standard coding tier",
        "never used",
        "OpenAI",
    ):
        assert needle in text, f"behavior layer missing {needle!r}"
    # the old guidance invited cheap-tier models for execution work
    assert "efficient models" not in text, "old Model selection phrasing must be gone"


def test_spec_skill_contract():
    text = (ROOT / "skills" / "spec" / "SKILL.md").read_text()
    for needle in (
        "/omc:explain",
        "EACH section",
        "whole-spec",
        "architectural",
        "follow-up",
        "review",
    ):
        assert needle in text, f"spec skill missing {needle!r}"
    # spec-phase emphasis is architecture; implementation choices are plan-phase
    assert "plan phase" in text
