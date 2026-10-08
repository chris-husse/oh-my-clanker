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
    # NO `dependencies`. Claude Code resolves a dependency by its exact
    # marketplace-qualified id and refuses to load omc ("failed to load") when
    # superpowers came from any other marketplace (claude-plugins-official is
    # the common case). It never installs the dependency for you either. omc
    # installs superpowers itself in ensure_plugin — see docker/PLUGIN-NOTES.md,
    # "Resolution 2: no manifest dependency".
    assert "dependencies" not in data


def test_claude_marketplace_lists_omc():
    data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert data["name"] == "oh-my-clanker"
    assert any(p["name"] == "omc" for p in data["plugins"])


def test_codex_plugin_manifest():
    data = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
    assert data["name"] == "omc"
    assert data["skills"] == "./skills/"


def test_distributed_plugin_payloads_match_supported_providers():
    payloads = {path.parent.name for path in ROOT.glob(".*-plugin/plugin.json")}
    payloads.update(path.parents[1].name for path in ROOT.glob(".*/plugins/*.js"))
    assert payloads == {".claude-plugin", ".codex-plugin"}


USER_FACING_SKILLS = (
    "slug",
    "start",
    "plan",
    "design",
    "implement",
    "audit",
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
    "workspace",
)
INTERNAL_SKILLS = (
    "create-mr",
    "get-mr-description",
    "squash",
    "gitnexus-ensure",
    "gitnexus-index",
    "gitnexus-document",
    "gitnexus-explain",
    "ticket-sync",
    "grug",
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
        "grug",  # review runs omc's own lens, so a review failure may come from it
        'regardless of `"configured"`',  # unconfigured review can still fail via grug
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
        # the project half of an unconfigured stage is a pass (review's grug half still runs)
        assert "nothing to do" in text


def test_review_proxy_runs_grug():
    text = (ROOT / "skills" / "review" / "SKILL.md").read_text()
    for needle in (
        "grug diff",
        "fix now",
        "waive",
        "/omc:check",
        "Deliberate complexity",
        'regardless of `"configured"`',
        "grug skill unavailable — plugin stale? run omc update",
        "Review",
        "batched",
    ):
        assert needle in text, f"review proxy missing {needle!r}"
    # grug runs after the project stage (anchor on the Steps body: the frontmatter
    # already names .omc/skills/review)
    steps = text.index("## Steps")
    assert text.index(".omc/skills/review", steps) < text.index("grug diff", steps)
    # the lens is unavailable → the stage FAILS, it never passes lens-less
    unavailable = text.index("grug skill unavailable")
    assert '"passed": false' in text[unavailable - 400 : unavailable + 200]
    # fix first (configured Review choice), waive second, ask last — and batched
    assert text.index("Fix first, Review") < text.index("**waive**") < text.index("batched")
    assert "nothing to do" in text and '"configured"' in text and "OMC_STAGE" in text
    # the other three proxies stay pure pass-throughs
    for stage in ("check", "build", "verify"):
        assert "grug" not in (ROOT / "skills" / stage / "SKILL.md").read_text()


def test_review_proxy_dispatches_grug_judgment_on_review_choice():
    text = (ROOT / "skills" / "review" / "SKILL.md").read_text()
    judgment = text.split("3. **Grug lens.**", 1)[1].split("- **Grug unavailable.", 1)[0]
    assert "Dispatch a grug judge worker" in judgment
    assert "tasks.review.model" in judgment
    assert "tasks.review.effort" in judgment
    assert "grug diff <base>" in judgment
    assert "main session" in judgment
    assert "orchestrator model" in judgment


def test_squash_skill_contract():
    text = (ROOT / "skills" / "squash" / "SKILL.md").read_text()
    for needle in ("reset --soft", "OMC_SQUASH", "rev-list --count"):
        assert needle in text, f"squash skill missing {needle!r}"


def test_dogfood_build_stage():
    text = (ROOT / ".omc" / "skills" / "build" / "SKILL.md").read_text()
    assert "just build" in text


def test_dogfood_review_stage_rejects_slow_and_serial_tests():
    text = (ROOT / ".omc" / "skills" / "review" / "SKILL.md").read_text()
    for needle in (
        "No slow tests",
        "5-minute",
        "No serial-only tests",
        "-n auto",
        "xdist_group",
        "Important",
    ):
        assert needle in text, f"review stage missing {needle!r}"


def test_dogfood_verify_stage_gates_on_parallel_e2e_only():
    text = (ROOT / ".omc" / "skills" / "verify" / "SKILL.md").read_text()
    assert "just e2e-tests" in text and "just codex-gate" in text
    assert "never a gate" in text
    assert "lifecycle-tests" not in text  # the hour-long matrix is gone as a gate


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
        "omc design",
        "alias",
        "/omc:design",
        "$ARGUMENTS",
        "merge-base",
        "OMC_KNOWLEDGE",
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
    for needle in (
        "omc internal gitnexus refresh",
        "OMC_KNOWLEDGE",
        "rc 3",
        "git worktree list",
        "primary",
    ):
        assert needle in text, f"gitnexus-index missing {needle!r}"
    assert "--skip-agents-md" not in text  # analyze flags live in Python now


def test_gitnexus_document_contract():
    text = (ROOT / "skills" / "gitnexus-document" / "SKILL.md").read_text()
    for needle in (
        "omc internal gitnexus refresh --enable-documentation",
        ".omc/docs/gitnexus/docs",
        "OMC_KNOWLEDGE",
    ):
        assert needle in text, f"gitnexus-document missing {needle!r}"
    assert "cp -R" not in text and "--provider" not in text  # sync + model choice are Python's


def test_gitnexus_explain_contract():
    text = (ROOT / "skills" / "gitnexus-explain" / "SKILL.md").read_text()
    assert "omc internal gitnexus" in text
    assert "OMC_KNOWLEDGE" in text and "stderr" in text
    assert "--repo" not in text and "--branch" not in text
    assert "node <CLI> query" not in text


def test_explain_user_facing_contract():
    text = (ROOT / "skills" / "explain" / "SKILL.md").read_text()
    for needle in (
        ".omc/skills/explain-context",
        "gitnexus-explain",
        "$ARGUMENTS",
        "OMC_KNOWLEDGE",
    ):
        assert needle in text, f"explain missing {needle!r}"


def test_explain_discovers_context_and_sources_in_order():
    text = (ROOT / "skills" / "explain" / "SKILL.md").read_text()
    assert "## Step 2b" in text
    context = text.split("## Step 1", 1)[1].split("## Step 2", 1)[0]
    sources = text.split("## Step 2b", 1)[1].split("## Step 3", 1)[0]

    assert "omc internal skills list explain-context" in context
    assert "project" in context and "primary" in context and "global" in context
    assert "omc internal skills list explain-source" in sources
    assert "question" in sources and "primary" in sources
    assert "base" in sources and "repository key" in sources
    assert text.index("## Step 1") < text.index("## Step 2") < text.index("## Step 2b")


def test_explain_source_evidence_and_failure_contract():
    text = (ROOT / "skills" / "explain" / "SKILL.md").read_text()
    assert "## Step 2b" in text
    sources = text.split("## Step 2b", 1)[1].split("## Step 3", 1)[0]
    synthesis = text.split("## Step 4", 1)[1]

    for needle in ("availability", "cited findings", "freshness", "unknowns"):
        assert needle in sources
    assert "non-fatal" in sources and "independently" in sources
    assert "current repository" in synthesis and "conflict" in synthesis
    assert "select evidence" in synthesis and "cite-back" in synthesis
    assert "budget" in synthesis
    dependency = text.split("## Step 3", 1)[1].split("## Step 4", 1)[0]
    assert "dependency keys" in dependency and "named" in dependency
    assert "exactly once" in synthesis and "end" in synthesis
    assert "empty" in synthesis
    assert "`/omc:index` first" in text and "and stop" in text
    assert "FIRST line" in text and "fresh: false" in text


def test_integrate_inventories_global_knowledge_sources():
    text = (ROOT / "skills" / "integrate" / "SKILL.md").read_text()
    inventory = text.split("## Phase 1", 1)[1].split("## Phase 2", 1)[0]
    assert "~/.omc/skills/explain-context" in inventory
    assert "~/.omc/skills/explain-source" in inventory
    assert "omc internal skills list" in inventory


def test_machine_contract_listings_include_knowledge_and_models():
    for rel in (
        "src/omc/distribution/AGENTS.md",
        ".omc/config/AGENTS.md",
        ".omc/skills/review/SKILL.md",
        ".omc/skills/explain-context/SKILL.md",
    ):
        assert "OMC_KNOWLEDGE" in (ROOT / rel).read_text(), rel
        assert "OMC_DESIGN_RECORD" in (ROOT / rel).read_text(), rel
        assert "OMC_MODELS" in (ROOT / rel).read_text(), rel
        assert "OMC_WORKSPACE" in (ROOT / rel).read_text(), rel


def test_workspace_skill_lists_verdict_fields_without_forge_api():
    path = ROOT / "skills/workspace/SKILL.md"
    assert path.is_file(), "workspace must be a user-facing skill"
    text = path.read_text()
    listing = text.split("## List", 1)[1].split("## Close", 1)[0]
    assert "omc internal workspace list" in listing
    for field in ("role", "branch", "worktree", "record", "ahead", "behind", "compare_url"):
        assert f"`{field}`" in listing
    for instruction in ("OMC_WORKSPACE", "null", "unknown", "empty", "forge API"):
        assert instruction in listing
    frontmatter = text.split("---", 2)[1]
    assert "not meant for direct invocation" not in frontmatter


def test_workspace_close_reports_refusal_identity_and_preserves_remaining_entries():
    path = ROOT / "skills/workspace/SKILL.md"
    assert path.is_file(), "workspace close needs a user entry point"
    closing = path.read_text().split("## Close", 1)[1]
    for instruction in (
        "omc internal workspace close",
        "unmerged",
        "not-master",
        "repository",
        "branch",
        "master_worktree",
        "stop",
        "remaining",
        "primary",
        "session",
    ):
        assert instruction in closing


def test_start_and_plan_keep_explicit_modification_targets_for_design_gate():
    for name, section, following in (
        ("start", "## Step 2 — gather context", "## Step 2.5"),
        ("plan", "## Step 3 — seed", "## Step 4"),
    ):
        text = (ROOT / f"skills/{name}/SKILL.md").read_text()
        scope = text.split(section, 1)[1].split(following, 1)[0]
        for instruction in ("modification target", "repository", "/omc:design", "read-only"):
            assert instruction in scope, f"{name} missing {instruction}"


def test_design_registers_all_targets_between_record_gate_and_first_write():
    text = (ROOT / "skills/design/SKILL.md").read_text()
    gate = text.split("## Step 1a — workspace gate", 1)[1].split("## Step 1b", 1)[0]
    order = [
        text.index(marker)
        for marker in (
            "Run `omc internal design-record`",
            "## Step 1a",
            "## Step 1b",
            "## Step 1c",
            "Write the design doc",
        )
    ]
    assert order == sorted(order)
    for instruction in (
        "omc internal workspace add <target>",
        "OMC_WORKSPACE",
        "unresolved",
        "URL",
        "not-a-projects-folder",
        "--path",
        "not-omc-aware",
        "retry",
        "drop",
        "registered",
        "handled by hand",
        "ledger",
        "no second modification target",
        "skip all workspace calls",
    ):
        assert instruction in gate
    assert "workspace gate" in text.split("## Step 0", 1)[1].split("## Step 1", 1)[0]


def test_design_dependency_explain_is_rooted_and_discloses_missing_index():
    text = (ROOT / "skills/design/SKILL.md").read_text()
    explain = text.split("## Step 1b — dependency explain", 1)[1].split("## Step 1c", 1)[0]
    for instruction in (
        "cd",
        "worktree",
        ".omc/config/AGENTS.md",
        ".omc/skills/explain-context/SKILL.md",
        "/omc:explain",
        "converged",
        "constraints",
        "affected components",
        "open questions",
        "no index",
        "files",
        "fallback",
        "harness instructions",
        "plugin settings",
    ):
        assert instruction in explain


def test_design_master_snapshot_and_narrowed_records_survive_resume():
    text = (ROOT / "skills/design/SKILL.md").read_text()
    master = text.split("## Step 1c", 1)[1].split("## Step 2", 1)[0]
    for instruction in (
        "## Repositories",
        "## Cross-repo contract",
        "omc internal workspace list",
        "snapshot",
        "never regenerate",
        "dependency",
        "findings",
    ):
        assert instruction in master
    dependency = text.split("## Step 4b — dependency records", 1)[1].split("## Step 5", 1)[0]
    for instruction in (
        "cd",
        "registration order",
        "design this repository only",
        "docs/superpowers/specs/<date>-<slug>-design.md",
        "omc internal design-record",
        "existing",
        "second dated",
        "whole-record",
        "/omc:explain",
        "grug spec",
        "never commit",
    ):
        assert instruction in dependency
    commits = text.split("## Step 5", 1)[1].split("## Completion contract", 1)[0]
    for instruction in (
        "dependencies first",
        "master last",
        "git -C <worktree>",
        "omc internal design-record",
        "ok: true",
        "repository and path",
    ):
        assert instruction in commits


def test_finish_reports_workspace_and_closes_by_current_role():
    text = (ROOT / "skills/finish/SKILL.md").read_text()
    followups = text.split("## Step 6", 1)[1].split("## Completion contract", 1)[0]
    for instruction in (
        "omc internal workspace list",
        "non-empty",
        "current_role",
        "master",
        "dependency",
        "single-repository",
        "workspace close",
        "wt remove",
        "primary",
        "session",
        "three options",
    ):
        assert instruction in followups
    assert "this repository only" in text.split("## Step 4", 1)[1].split("## Step 5", 1)[0]


def test_finish_retains_dependency_worktree_until_master_closes_workspace():
    text = (ROOT / "skills/finish/SKILL.md").read_text()
    close = text.split("1. **Close the worktree**", 1)[1].split(
        "2. **Address review comments**", 1
    )[0]
    close = " ".join(close.split())
    dependency = close.split("When `current_role` is `dependency`", 1)[1].split(
        "For a single-repository run", 1
    )[0]
    for instruction in (
        "keep this worktree",
        "master_worktree",
        "master closes the workspace",
        "Do not run `wt remove`",
        "ledger",
    ):
        assert instruction in dependency
    single = close.split("For a single-repository run", 1)[1]
    assert "empty list, null role" in single
    assert "wt -C <primary> remove <branch>" in single


def test_behavior_layer_authorizes_dependency_lifecycles_without_direct_code_edits():
    text = (ROOT / "src/omc/distribution/AGENTS.md").read_text()
    workspace = text.split("**Workspace lifecycle**", 1)[1].split("\n- **", 1)[0]
    for instruction in ("master", "authorizes", "dependency", "implement", "audit", "code"):
        assert instruction in workspace


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
        "`Complexity:`",
        "OMC_KNOWLEDGE",
        "/omc:design",
    ):
        assert needle in text, f"plan skill missing {needle!r}"
    # composition rule: explain is called as a command, never unpacked
    assert "never reach into" in text
    # the brainstorm's handoff is the design record command, not implementation
    assert "wait for `/omc:design`" in text


def test_implement_skill_contract():
    text = (ROOT / "skills" / "implement" / "SKILL.md").read_text()
    # Lifecycle tests cover direct implementation authority and continuation;
    # this manifest check must not require the old existing-spec approval gate.
    for needle in (
        "omc internal design-record",
        "OMC_DESIGN_RECORD",
        "/omc:design",
        "writing-plans",
        "subagent-driven-development",
        "/omc:audit",
        "omc review",
        "/omc:explain",
        "omc internal models",
        "`Complexity:`",
        "missing `Complexity:`",
        "/omc:check",
        "before dispatching the next task",
        "plan already exists",
        "## Phase 2b — milestone gate",
        "stage-gate rule",
        "bounded fix-forward, then a CRITICAL stop",
        '"passed": true',
        '"configured": false',
    ):
        assert needle in text, f"implement skill missing {needle!r}"
    assert "Invoke the internal `spec` skill" not in text
    order = [
        text.index("omc internal design-record"),
        text.index("writing-plans"),
        text.index("subagent-driven-development"),
        text.index("## Phase 2b — milestone gate"),
        text.index("/omc:build", text.index("## Phase 2b — milestone gate")),
        text.index("/omc:verify", text.index("## Phase 2b — milestone gate")),
        text.index("## Phase 3 — hand off"),
    ]
    assert order == sorted(order), (
        "implement must order record gate -> plan -> build -> milestone gate -> handoff"
    )
    assert "finish runs it" not in text
    assert "Invoke the `finish` skill" not in text


def test_task_model_skill_routing_contract():
    design = (ROOT / "skills/design/SKILL.md").read_text()
    implement = (ROOT / "skills/implement/SKILL.md").read_text()
    audit = (ROOT / "skills/audit/SKILL.md").read_text()
    review = (ROOT / "skills/review/SKILL.md").read_text()
    plan = (ROOT / "skills/plan/SKILL.md").read_text()
    behavior = (ROOT / "src/omc/distribution/AGENTS.md").read_text()

    assert "omc internal models" in design and "Design" in design
    assert "fresh" in design and "converged" in design
    assert "CRITICAL" in design and "main session" in design
    assert "omc internal models" in implement and "Plan" in implement
    assert "simple | medium | high" in implement
    assert "missing `Complexity:`" in implement and "medium" in implement
    assert "Review" in implement and "reasoning_effort" in implement
    assert "Complexity:" in audit and "Review" in audit
    assert "omc internal models" in review and "Review" in review
    assert "`Complexity:`" in plan and "model-tier" not in plan
    assert "Orchestrator" in behavior and "omc internal models" in behavior
    assert "orchestrator model" in behavior and "test actors and test judges" in behavior.lower()
    for skill in (design, implement, audit, review):
        assert "model-tier policy" not in skill
    assert "Do not request a model override on a full-history" in design


def test_audit_skill_contract():
    text = (ROOT / "skills" / "audit" / "SKILL.md").read_text()
    for needle in (
        "omc internal design-record",
        "OMC_DESIGN_RECORD",
        "/omc:design",
        "git fetch origin <base>",
        "origin/<base>...HEAD",
        "docs/superpowers/",
        '"nothing to audit — run `/omc:implement` first"',
        "Decisions taken during brainstorm",
        "file:line",
        "Important",
        "Minor",
        "code deviates from the record",
        "record is stale",
        "CRITICAL",
        "/omc:check",
        "/omc:build",
        "/omc:verify",
        "stage-gate rule",
        "bounded fix-forward, then a CRITICAL stop",
        "tracked product files",
        "Implementation review",
        "## Deliberate complexity",
        "provider",
        "date",
        "re-audit",
        "commit",
        "`finish`",
    ):
        assert needle in text, f"audit skill missing {needle!r}"
    assert text.index("## Phase 0") < text.index("## Phase 1") < text.index("## Phase 3")
    assert text.index("## Phase 2b") < text.index("## Phase 3")
    gate = text[
        text.index("## Phase 2 — disposition") : text.index("## Phase 2b — trace and commit")
    ]
    assert gate.index("/omc:check") < gate.index("/omc:build") < gate.index("/omc:verify")
    assert text.index("commit", text.index("## Phase 2b")) < text.index("Invoke `finish`")
    prose = " ".join(text.split())
    assert "immediately before the terminal `## Deliberate complexity`" in prose
    assert "append a new dated entry below the previous review entry" in prose
    assert "plan" in text and "context" in text and "absence" in text
    assert "conformance only" in text
    assert "unrelated or undisposed" in prose
    assert "Before invoking `finish`" in text
    assert "preserve" in text and "CRITICAL" in text
    assert "explicitly authorizes including" in prose
    assert "left untouched and outside publication" in prose
    assert "re-read `git status --porcelain`" in text


def test_index_and_document_delegate():
    assert "gitnexus-index" in (ROOT / "skills" / "index" / "SKILL.md").read_text()
    assert "gitnexus-document" in (ROOT / "skills" / "document" / "SKILL.md").read_text()


def test_dogfood_stage_and_context_skills():
    for name, needle in (
        ("check", "just check"),
        ("build", "just build"),
        ("verify", "just e2e-tests"),
        ("review", "ToolContext"),
        ("explain-context", "docs/superpowers/specs"),
    ):
        text = (ROOT / ".omc" / "skills" / name / "SKILL.md").read_text()
        assert needle in text, f".omc/skills/{name} missing {needle!r}"


def test_rebase_main_skill_contract():
    text = (ROOT / "skills" / "rebase-main" / "SKILL.md").read_text()
    for needle in ("omc internal rebase-main", "OMC_REBASE_MAIN", "rc 3", "conflict", "knowledge"):
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


def test_integrate_skill_describes_global_section():
    text = (ROOT / "skills" / "integrate" / "SKILL.md").read_text()
    assert "root symlinks" not in text
    assert "~/.claude/CLAUDE.md" in text
    assert "~/.codex/AGENTS.md" in text
    assert "omc configure" in text


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


def test_distribution_agents_task_model_policy():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    for needle in (
        "Orchestrator",
        "omc internal models",
        "OMC_MODELS",
        "Complexity: simple | medium | high",
        "Review",
        "never used",
        "E2E_MODEL",
    ):
        assert needle in text, f"behavior layer missing {needle!r}"
    assert "model-tier policy" not in text
    # the old guidance invited cheap-tier models for execution work
    assert "efficient models" not in text, "old Model selection phrasing must be gone"


def test_audit_and_project_review_dispatch_review_judgment_even_without_findings():
    audit = (ROOT / "skills" / "audit" / "SKILL.md").read_text()
    conformance = audit.split("## Phase 1 — conformance", 1)[1].split("## Phase 2", 1)[0]
    assert "omc internal models" in conformance
    assert "Dispatch a Review worker" in conformance
    assert "even when" in conformance
    review = (ROOT / "skills" / "review" / "SKILL.md").read_text()
    project = review.split("2. Look for", 1)[1].split("3. **Grug lens.**", 1)[0]
    assert "Dispatch a Review worker" in project
    assert "even when" in project


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
    prose = " ".join(text.split())
    for needle in (
        "end of `/omc:implement`",
        "`/omc:audit`",
        "stage-gate rule",
        '"passed": false',
        "no verdict",
        '"configured": false',
        "heavy coding tier",
        "at most two fix-and-rerun cycles",
        "three gate runs",
        "restart from `/omc:build`",
        "failed `/omc:check`",
        "CRITICAL",
        "no handoff commit",
        "no `finish`",
    ):
        assert needle in prose, f"behavior layer missing stage-gate contract {needle!r}"


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


def test_design_skill_contract():
    text = (ROOT / "skills" / "design" / "SKILL.md").read_text()
    for needle in (
        "/omc:explain",
        "EACH section",
        "whole-record",
        "architectural",
        "follow-up",
        "review",
        "grug section",
        "grug spec",
        "Deliberate complexity",
        "None.",
        "grug skill unavailable — plugin stale? run omc update",
        "Fix, by Design",
        "Ask, batched",
        "$ARGUMENTS",
        "omc internal design-record",
        "OMC_DESIGN_RECORD",
        "missing",
        "unclean",
        "ambiguous",
        "omc implement",
        "valid stop",
    ):
        assert needle in text, f"design skill missing {needle!r}"
    # design-phase emphasis is architecture; implementation choices are plan-phase
    assert "plan phase" in text
    # grug judges each section AFTER explain has answered, with that answer as context
    # (anchor on Step 2: the frontmatter already mentions /omc:explain)
    step2 = text.index("## Step 2")
    assert text.index("/omc:explain", step2) < text.index("grug section", step2)
    step4 = text.index("## Step 4")
    assert text.index("Fix, by Design", step4) < text.index("Waive by record", step4)
    assert text.index("Waive by record", step4) < text.index("Ask, batched", step4)
    # grug unavailable is a hard stop: Step 5 must not commit or continue past it
    step5 = text.index("## Step 5")
    assert "grug skill unavailable" in text[step5:] and "hard stop" in text[step5:]
    assert "under /omc:implement" not in text  # the continue-branch is gone
    assert "Completion contract" in text
    # the waiver section is unconditional so review can rely on it
    assert text.index("Deliberate complexity") < text.index("## Step 2")


def test_design_is_user_facing_and_owns_the_record():
    text = (ROOT / "skills" / "design" / "SKILL.md").read_text()
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "name: design" in m.group(1)
    assert "Internal" not in m.group(1) and "not meant for direct invocation" not in m.group(1)
    assert "/omc:implement" in text and "omc implement" in text  # the two continuations
    assert not (ROOT / "skills" / "spec").exists()


GRUG_RULE_IDS = (
    "grug:say-no",
    "grug:80-20",
    "grug:factor-late",
    "grug:cut-point",
    "grug:simple-repeat",
    "grug:locality",
    "grug:debuggable",
    "grug:fence",
    "grug:small-refactor",
    "grug:generics",
    "grug:test-level",
    "grug:evidence-perf",
    "grug:api-common-case",
    "grug:logging",
    "grug:simple-concurrency",
    "grug:too-complex-for-grug",
)


def test_grug_skill_contract():
    text = (ROOT / "skills" / "grug" / "SKILL.md").read_text()
    for rule in GRUG_RULE_IDS:
        assert rule in text, f"grug skill missing rule {rule!r}"
    for needle in (
        "$ARGUMENTS",
        "spec <path>",
        "section <text>",
        "diff [<base>]",
        'grug: "',
        "simpler:",
        "none — needs a human answer",
        "Deliberate complexity",
        "Important",
        "Minor",
        "fix now",
        "waive",
        ".omc/config/AGENTS.md",
        ".omc/config/coding-convention.md",
        "worktree.base_branch",
        "no design record found",
        "behavior-preserving",
        "grug summary:",
        "not the end of your turn",
        "A rule not named in either list is Minor",
    ):
        assert needle in text, f"grug skill missing {needle!r}"
    # internal leaf: no user entry point, no graph, no explain
    assert "/omc:grug" not in text
    assert "omc internal gitnexus" not in text
    assert "/omc:explain" not in text
    # the two owners are named in the frontmatter description
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "/omc:design" in m.group(1) and "/omc:review" in m.group(1)


# --- Anti-stall contract -----------------------------------------------------
# Composed omc flows nest 3-4 deep (start -> ticket-sync; finish -> squash/
# stages/create-mr -> get-mr-description; design -> explain/grug; implement ->
# plan/finish). Each sub-skill arrives as a fresh instruction block and ends in
# a verdict line or a polished artifact, both of which read as "done". Runs
# repeatedly died on the OMC_TICKET verdict, leaving the caller's remaining
# steps silently unrun.
# Prose disclaimers alone did not hold; these tests pin the structural rules.

CONDUCTORS = ("start", "design", "finish", "implement", "audit")


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
    for needle in (
        "not the end of your turn",
        "next action is a tool call",
        "return to the caller",
    ):
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


def test_behavior_layer_names_three_authority_words():
    text = (ROOT / "src" / "omc" / "distribution" / "AGENTS.md").read_text()
    assert "`/omc:design`" in text and "`$omc:design`" in text
    assert "`/omc:implement`" in text and "`$omc:implement`" in text
    assert "`/omc:audit`" in text and "`$omc:audit`" in text
    assert "omc review" in text
    assert "requires a committed design record" in text
    # the conductor list in the "Externalize a composed flow" bullet names design too
    assert "/omc:design" in text.split("Externalize a composed flow")[1]
    assert "/omc:audit" in text.split("Externalize a composed flow")[1]


def test_dependency_lifecycles_run_between_record_gate_and_local_work():
    for skill, command in (("implement", "implement"), ("audit", "review")):
        text = (ROOT / f"skills/{skill}/SKILL.md").read_text()
        assert "## Phase 0.5 — dependencies" in text
        phase = text.split("## Phase 0.5 — dependencies", 1)[1].split("## Phase 1", 1)[0]
        assert text.index("omc internal design-record") < text.index("## Phase 0.5")
        assert text.index("## Phase 0.5") < text.index("## Phase 1")
        assert "dependencies" in text.split("## Phase -1", 1)[1].split("## Phase 0", 1)[0]
        prose = " ".join(phase.split())
        for requirement in (
            "omc internal workspace list",
            'current_role == "master"',
            "empty",
            "dependency role",
            "no child",
            "list order",
            'role == "dependency"',
            "Never launch the master",
            f"omc {command} --headless",
            "background",
            "announce",
            "wait for process exit",
            "before inspecting artifacts",
            "skip",
            "nonzero",
            "verbatim",
            "CRITICAL",
            "wait for the answer",
            "even when artifacts appear complete",
            "same child session",
            "--resume",
            "--allowed-tools",
            "IMPLEMENT_ALLOWED_TOOLS",
            "provider environment",
            "one prompt argument",
            "shlex.quote",
            "ambiguous",
            "exact session ID",
            "manual",
            "Codex",
            f"`omc {command}`",
            "launch failure",
            "Never auto-close",
        ):
            assert requirement in prose, (skill, requirement)


def test_implement_completion_and_plan_chronology_contract():
    text = (ROOT / "skills/implement/SKILL.md").read_text()
    dependencies = text.split("## Phase 0.5", 1)[1].split("## Phase 1", 1)[0]
    for requirement in (
        "omc internal workspace implementation-status <slug>",
        '"complete": true',
        "clean tree",
        "unique",
        "first adds",
        "later commit",
        "docs/superpowers/specs",
        "docs/superpowers/plans",
        "<slug>-implement",
    ):
        assert requirement in dependencies
    plan = text.split("## Phase 1 — plan", 1)[1].split("## Phase 2", 1)[0]
    assert "Commit the plan" in plan
    assert "before dispatching" in plan


def test_audit_dependency_completion_requires_known_pushed_state():
    text = (ROOT / "skills/audit/SKILL.md").read_text()
    phase = text.split("## Phase 0.5", 1)[1].split("## Phase 1", 1)[0]
    for requirement in (
        "upstream",
        "ahead == 0",
        "null",
        "inspection failure",
        "never zero",
        "<slug>-audit",
    ):
        assert requirement in phase
    assert '"nothing to audit — run `/omc:implement` first"' in text


def test_audit_dependency_first_publication_is_not_an_inspection_failure():
    text = (ROOT / "skills/audit/SKILL.md").read_text()
    phase = text.split("## Phase 0.5", 1)[1].split("## Phase 1", 1)[0]
    prose = " ".join(phase.split())
    for requirement in (
        "No upstream: publication remains; launch the dependency audit",
        "Upstream exists and `ahead` is unknown: block",
        "Upstream exists and integer `ahead == 0`: skip",
        "Upstream exists and integer `ahead > 0`: launch",
    ):
        assert requirement in prose
