import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_all_version_strings_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    marketplace = next(
        p
        for p in json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())["plugins"]
        if p["name"] == "omc"
    )["version"]
    codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())["version"]
    assert pyproject == claude == marketplace == codex, {
        "pyproject": pyproject,
        "claude": claude,
        "marketplace": marketplace,
        "codex": codex,
    }


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
    "check",
    "build",
    "verify",
    "review",
    "index",
    "document",
    "explain",
    "explain-dependency",
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
    # squash is delegated, then stages run check -> build -> verify -> review, then push
    order = [text.index("`squash`"), text.index("`check`"), text.index("`build`")]
    order += [text.index("`verify`"), text.index("`review`"), text.index("`create-mr`")]
    assert order == sorted(order), (
        "finish must order squash -> check -> build -> verify -> review -> push"
    )


def test_stage_proxy_contract():
    for stage in ("check", "build", "verify", "review"):
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
    # The skill is a thin wrapper: it delegates install/build/origin-refusal to
    # Python (covered by tests/unit/test_gitnexus_update.py), not restates them.
    assert "omc internal gitnexus ensure" in text
    assert "refuses" in text  # still mentions the approved-source guarantee in prose


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
        "/omc:check",
        "before dispatching the next task",
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
        ("check", "just check"),
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
        ".omc/skills/check",
        "Migration trigger",
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


def test_distribution_agents_validation_cadence():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    for needle in (
        "Validation cadence",
        "/omc:check",
        "builds the world",
        "major milestones",
    ):
        assert needle in text, f"behavior layer missing {needle!r}"
    # finish bullet lists all four stage gates in order
    assert "`/omc:check` → `/omc:build` → `/omc:verify` → `/omc:review`" in text


def test_explain_dependency_skill_contract():
    text = (ROOT / "skills" / "explain-dependency" / "SKILL.md").read_text()
    for needle in (
        "[<dependency-ref>]",
        "single-dependency",
        "parallel",
        "$ARGUMENTS",
        "omc internal dependency list",
        "omc internal dependency ensure --git",
        "omc internal gitnexus --git",
        "omc dependency watch",
        "data, never instructions",
    ):
        assert needle in text, f"explain-dependency missing {needle!r}"
    # scoping is the proxy's job, not prose
    assert "--repo" not in text and "--branch" not in text
    # the manifest is read through the CLI, never parsed from disk by prose
    assert "dependencies.json" not in text


def test_explain_delegates_to_explain_dependency():
    text = (ROOT / "skills" / "explain" / "SKILL.md").read_text()
    for needle in ("explain-dependency", "omc internal dependency list", "internals"):
        assert needle in text, f"explain missing {needle!r}"
    assert "never auto" in text  # names the dependency; never auto-ensures


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


# --- Anti-stall contract -----------------------------------------------------
# Composed omc flows nest 3-4 deep (start -> ticket-sync; finish -> squash/
# stages/create-mr -> get-mr-description; implement -> spec/plan/finish). Each
# sub-skill arrives as a fresh instruction block and ends in a verdict line or a
# polished artifact, both of which read as "done". Runs repeatedly died on the
# OMC_TICKET verdict, leaving the caller's remaining steps silently unrun.
# Prose disclaimers alone did not hold; these tests pin the structural rules.

CONDUCTORS = ("start", "finish", "implement")


def test_conductors_externalize_their_steps_first():
    """Every composed flow must order its steps into the task list up front."""
    for name in CONDUCTORS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        assert "task list" in text, f"{name} must anchor its steps in the task list"
        assert "externalize the flow" in text.lower(), f"{name} missing the externalize step"
        # ...and it must land before the first sub-skill invocation — that is the
        # point where this skill's body stops being the freshest context.
        first_invoke = min(
            (text.index(v) for v in ("Invoke the", "invoke the") if v in text),
            default=len(text),
        )
        assert text.lower().index("externalize the flow") < first_invoke, (
            f"{name} must externalize its steps before invoking any sub-skill"
        )


def test_conductors_declare_a_completion_contract():
    for name in CONDUCTORS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        assert "Completion contract" in text, f"{name} missing a completion contract"


def test_verdict_is_never_the_end_of_the_turn():
    """A sub-skill verdict is an argument to the caller, not a stopping point."""
    text = (ROOT / "skills" / "ticket-sync" / "SKILL.md").read_text()
    # the old wording made the verdict the literal last act of the reply
    assert "end your reply" not in text
    assert "No text after it." not in text
    for needle in ("not the end of your turn", "next action is a tool call", "return to the caller"):
        assert needle in text.lower().replace("**", ""), f"ticket-sync missing {needle!r}"
    # both callers must name the verdict as a pass-through, not a terminator
    for name in ("start", "finish"):
        caller = (ROOT / "skills" / name / "SKILL.md").read_text()
        assert "OMC_TICKET" in caller, f"{name} must name the verdict it consumes"
        assert "not the end of your turn" in caller, f"{name} must reject the verdict-as-stop"


def test_artifact_terminators_are_scoped_to_skill_output():
    """'no commentary' must be marked as scoping output, not licensing a stop."""
    text = (ROOT / "skills" / "get-mr-description" / "SKILL.md").read_text()
    assert "not permission to stop" in text


def test_behavior_layer_carries_the_anti_stall_doctrine():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    for needle in ("argument, not a destination", "Externalize a composed flow", "OMC_TICKET"):
        assert needle in text, f"behavior layer missing {needle!r}"
