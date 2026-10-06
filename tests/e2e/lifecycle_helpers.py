"""Shared helpers for the live lifecycle E2E suites (golden path, variations,
integration cases, and the expensive monolithic runs)."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from .codex_auth import require_codex_ready
from .conversation import Conversation
from .harness import (
    configure_omc,
    make_work_repo,
    require_token,
    run_in,
    set_codex_container_policy,
)

# omc's /omc:implement exceeds the 300 s ceiling today (2026-10-01); the
# expensive evidence runs wait this long for the implement turn.
IMPLEMENT_TURN_BUDGET = float(os.environ.get("OMC_E2E_IMPLEMENT_TIMEOUT", "1800"))


def _codex_model() -> str:
    model = os.environ.get("CODEX_E2E_MODEL", "gpt-6-astra")
    if not model:
        pytest.fail("CODEX_E2E_MODEL must name a real coding model")
    return model


def _set_write_capability(container):
    set_codex_container_policy(container, reasoning_effort="high", run=run_in)


def _codex_fixture_trust_override(repo):
    if repo != "/work/capability":
        raise ValueError("Codex trust override is only for the disposable /work fixture")
    return f'projects.{json.dumps(repo)}.trust_level="trusted"'


def _image_provenance(container):
    image = container.get_wrapped_container().attrs["Config"]["Image"]
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    prebuilt = os.environ.get("OMC_E2E_PREBUILT_IMAGE")
    source = os.environ.get("OMC_E2E_PREBUILT_SOURCE")
    if prebuilt and not source:
        pytest.fail("OMC_E2E_PREBUILT_SOURCE must identify the prebuilt image's source commit")
    return {"image": image, "image_id": image_id, "prebuilt_source_commit": source}


def _worktree(container, repo):
    rc, listing = run_in(container, ["git", "-C", repo, "worktree", "list", "--porcelain"])
    assert rc == 0, listing
    for block in listing.split("\n\n"):
        if "branch refs/heads/feature/" in block:
            path = next(
                line.split(" ", 1)[1] for line in block.splitlines() if line.startswith("worktree ")
            )
            branch = next(
                line.split(" ", 1)[1].removeprefix("refs/heads/")
                for line in block.splitlines()
                if line.startswith("branch ")
            )
            return path, branch
    raise AssertionError(f"omc start made no feature worktree: {listing}")


def _installed_skill_paths(container, provider):
    import json

    script = r"""
import json, os
from pathlib import Path
provider = os.environ['OMC_E2E_SKILL_PROVIDER']
home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
cache = (home if provider == 'codex' else Path.home() / '.claude') / 'plugins' / 'cache'
paths = {}
for skill, pattern, declared in (
    ('omc:start', 'oh-my-clanker/omc/*/skills/start/SKILL.md', 'name: start'),
    ('superpowers:brainstorming',
     'superpowers-marketplace/superpowers/*/skills/brainstorming/SKILL.md',
     'name: brainstorming'),
):
    found = list(cache.glob(pattern))
    if len(found) != 1 or declared not in found[0].read_text():
        raise SystemExit(f'installed {skill} skill path is missing or ambiguous')
    paths[skill] = str(found[0].resolve())
print(json.dumps(paths))
"""
    rc, out = run_in(container, ["python3", "-c", script], env={"OMC_E2E_SKILL_PROVIDER": provider})
    assert rc == 0, f"installed skill path discovery failed: {out}"
    return json.loads(out)


def _assert_skill_reads(events, expected_paths):
    for skill, path in expected_paths.items():
        assert any(
            event.get("type") == "skill_read"
            and event.get("skill") == skill
            and event.get("path") == path
            for event in events
        ), f"no successful read of installed {skill} at {path}"


_CLI = "/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js"


def _fixture(container, *, failing_stage: str | None = None, path="/work/lifecycle") -> str:
    """A real, small Python repo with all four local stages and a bare origin."""
    assert failing_stage in (None, "build", "verify")
    repo = make_work_repo(container, path)
    payload = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
(root / ".gitignore").write_text("__pycache__/\n.claude/settings.local.json\n")
(root / "greeting.py").write_text("def greeting():\n    return 'Goodbye, world!'\n")
(root / "test_greeting.py").write_text(
    "import unittest\nfrom greeting import greeting\n\n"
    "class GreetingSmoke(unittest.TestCase):\n"
    "    def test_greeting_is_text(self):\n"
    "        self.assertIsInstance(greeting(), str)\n"
)
(root / ".omc" / "config").mkdir(parents=True, exist_ok=True)
(root / ".omc" / "config" / "AGENTS.md").write_text(
    "# Project guidance\n"
    "This is a small Python greeting module. Use unittest for verification.\n"
)
for stage in ("check", "build", "verify", "review"):
    stage_dir = root / ".omc" / "skills" / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    command = {
        "check": "python3 -m unittest discover -v",
        "build": "python3 -m compileall -q greeting.py test_greeting.py",
        "verify": "python3 -m unittest discover -v",
        "review": "git diff --check",
    }[stage]
    fail = ""
    if stage == "build":
        fail = "\ntest ! -e /tmp/omc-external-build-unavailable"
    elif stage == "verify":
        fail = (
            "\nif test -e "
            '"${OMC_EXTERNAL_VERIFY_SENTINEL:-/tmp/omc-external-verify-unavailable}"; then\n'
            "  echo 'E2E environment unavailable: verify sentinel present' >&2\n"
            "  exit 1\nfi"
        )
    script = (
        f"#!/bin/sh\nset -eu\nprintf '{stage}\\n' "
        '>> "${OMC_LIFECYCLE_MARKER:-/tmp/omc-lifecycle-stages}"\n'
        f"{command}{fail}\n"
    )
    scripts = root / ".omc" / "stage-scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / f"{stage}.sh").write_text(script)
    (stage_dir / "SKILL.md").write_text(
        f"---\nname: {stage}\n"
        f"description: Run the {stage} stage for this Python project.\n---\n\n"
        f"Run `sh .omc/stage-scripts/{stage}.sh` in the project root. "
        "Report the actual exit status.\n"
    )
"""
    rc, out = run_in(container, ["python3", "-c", payload, repo])
    assert rc == 0, out
    if failing_stage in ("build", "verify"):
        rc, out = run_in(container, ["touch", f"/tmp/omc-external-{failing_stage}-unavailable"])
        assert rc == 0, out
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            "git add -A && git commit -qm 'add greeting project and stages' "
            "&& git push -q origin main",
        ],
        cwd=repo,
    )
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, f"real GitNexus index failed: {out[-1800:]}"
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, "real GitNexus index produced no graph"
    return repo


def _configure_codex_conversation(container):
    require_token("codex")
    configure_omc(container, "codex")
    metadata = require_codex_ready(container)
    model = _codex_model()
    rc, out = run_in(container, ["omc", "configure", "--set", f"llm.providers.codex.model={model}"])
    assert rc == 0, out
    # Only the disposable container's account home is changed. OMC's normal
    # session argv then uses this effective config for an unsandboxed actor.
    _set_write_capability(container)
    return model, metadata


def _assert_boundary(before, after, label):
    for key in ("source", "index", "head", "remote_refs", "spec_plan"):
        assert before[key] == after[key], f"{label} changed {key}: {before[key]} → {after[key]}"


def _assert_audit_refusal(before: dict, after: dict, status: str, answer: str) -> None:
    """A design-only audit must stop for the stated reason without changing work."""
    for key in ("source", "spec_plan", "index", "head", "remote_refs"):
        assert after[key] == before[key], f"design-only audit changed {key}"
    assert not status.strip(), f"design-only audit left dirty status: {status}"
    lower_answer = answer.lower()
    assert "nothing to audit" in lower_answer, f"audit did not explain refusal: {answer}"
    assert "/omc:implement" in answer, f"audit did not point to implement: {answer}"


def _assert_primary_boundary(before, after, label):
    for product_file in ("greeting.py", "test_greeting.py"):
        assert before["source"][product_file] == after["source"][product_file], (
            f"{label} changed primary {product_file}"
        )
    for key in ("index", "head", "remote_refs", "spec_plan"):
        assert before[key] == after[key], f"{label} changed primary {key}"


def _assert_critical_wait(before, after, label):
    for product_file in ("greeting.py", "test_greeting.py"):
        assert before["source"][product_file] == after["source"][product_file], (
            f"{label} changed dependent {product_file} before clarification"
        )
    for key in ("index", "head", "remote_refs"):
        assert before[key] == after[key], f"{label} changed {key} before clarification"


def _assert_published_fix(container, repo, worktree, branch, old_source):
    """Check the bare-origin commit, its checked-out behavior, and test sensitivity."""
    script = r"""
import subprocess, sys, tempfile
from pathlib import Path

repo, worktree, branch, old_source = sys.argv[1:]
def git(where, *args):
    return subprocess.run(
        ['git', '-C', where, *args], check=True, capture_output=True, text=True
    ).stdout.strip()
def git_bytes(where, *args):
    return subprocess.run(
        ['git', '-C', where, *args], check=True, capture_output=True
    ).stdout
def require(condition, message):
    if not condition:
        raise SystemExit(message)

remote = git(worktree, 'remote', 'get-url', 'origin')
local_head = git(worktree, 'rev-parse', 'HEAD')
remote_head = git(remote, 'rev-parse', f'refs/heads/{branch}')
require(local_head == remote_head, 'published ref differs from final local HEAD')
require(git(remote, 'rev-list', '--count', f'main..{branch}') == '1',
        'published branch must contain exactly one described commit')
require(bool(git(remote, 'log', '-1', '--format=%b', branch)),
        'published commit description is empty')
for name in ('greeting.py', 'test_greeting.py'):
    committed = git_bytes(worktree, 'show', f'HEAD:{name}')
    pushed = git_bytes(remote, 'show', f'{branch}:{name}')
    require(Path(worktree, name).read_bytes() == committed == pushed,
            f'{name} differs between working tree, HEAD, and published tree')

with tempfile.TemporaryDirectory(prefix='omc-pushed-tree-') as checkout:
    subprocess.run(['git', 'clone', '-q', '--branch', branch, remote, checkout], check=True)
    fixed = subprocess.run(
        ['python3', '-c', 'from greeting import greeting; print(greeting())'],
        cwd=checkout, capture_output=True, text=True
    )
    require(fixed.returncode == 0 and fixed.stdout.strip() == 'Hello, world!',
            'published tree does not return the required greeting')
    good = subprocess.run(
        ['python3', '-m', 'unittest', 'discover', '-q'],
        cwd=checkout, capture_output=True, text=True
    )
    require(good.returncode == 0, 'published regression unittest does not pass')
    Path(checkout, 'greeting.py').write_text(old_source)
    old = subprocess.run(
        ['python3', '-m', 'unittest', 'discover', '-q'],
        cwd=checkout, capture_output=True, text=True
    )
    require(old.returncode != 0, 'published regression unittest does not detect old greeting')
"""
    rc, out = run_in(
        container, ["python3", "-c", script, repo, worktree, branch, old_source], timeout=90
    )
    assert rc == 0, out


def _assert_actor_settings(turn, model):
    contexts = [event for event in turn["events"] if event.get("type") == "turn_context"]
    assert contexts, f"no observed turn_context for {turn.get('turn_id')}"
    observed = contexts[0]
    assert observed["model"] == model, f"actor ran {observed['model']}, requested {model}"
    assert observed["approval_policy"] == "never", f"actor approval policy: {observed}"
    assert observed["sandbox_policy"] == "danger-full-access", (
        f"actor lacks write capability: {observed}"
    )


def _judge_codex(container, model: str, scenario: str, rubric: list[str], text: str):
    import json

    prompt = (
        f"Judge this test scenario: {scenario}\nRubric:\n"
        + "\n".join(f"- {x}" for x in rubric)
        + f"\nAssistant answer:\n{text[:16000]}\n"
        + 'Reply with only JSON: {"passed": true or false, "reasons": ["..."]}'
    )
    rc, out = run_in(
        container,
        ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-m", model, prompt],
        cwd="/tmp",
        timeout=300,
    )
    assert rc == 0, f"same-provider judge failed: {out[-1000:]}"
    for line in reversed(out.splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and isinstance(result.get("passed"), bool):
            return result
    raise AssertionError(f"same-provider judge gave no valid verdict: {out[-1000:]}")


def _require_complete_design(
    container, judge, model, session, repo, worktree, baseline, evidence, design
):
    complete_rubric = [
        "The answer presents a coherent complete solution for greeting() "
        "and a regression unittest.",
        "The answer stays in design discussion and does not claim implementation.",
    ]
    verdict = judge(
        container, model, "OMC brainstorming after seed", complete_rubric, design["text"]
    )
    if not verdict["passed"]:
        question = judge(
            container,
            model,
            "An initial material scope question during OMC brainstorming",
            [
                "The answer asks a concrete material question whose answer changes the design.",
                "The answer is not a routine request for approval to code or publish.",
            ],
            design["text"],
        )
        evidence["scope_question_judge"] = question
        assert question["passed"], verdict
        session.send(
            "Scope answer: the function has no other callers or branches. "
            "Return exactly 'Hello, world!' with the existing zero-argument signature. "
            "Use unittest for the exact value. Please present the complete design."
        )
        design = session.wait_turn(300)
        after_answer = session.snapshot(worktree)
        primary_after_answer = session.snapshot(repo)
        evidence["turns"].append(
            {
                "phase": "scope_answer",
                "turn": design,
                "snapshot": after_answer,
                "primary_snapshot": primary_after_answer,
            }
        )
        _assert_boundary(baseline, after_answer, "material scope answer")
        _assert_primary_boundary(
            evidence["primary_after_start"], primary_after_answer, "material scope answer"
        )
        verdict = judge(
            container,
            model,
            "OMC brainstorming after scope answer",
            complete_rubric,
            design["text"],
        )
    evidence["design_judge"] = verdict
    assert verdict["passed"], verdict
    return design


def _configure_claude_conversation(container):
    require_token("claude")
    configure_omc(container, "claude")
    model = os.environ.get("CLAUDE_E2E_MODEL", "claude-sonnet-5-5")
    judge_model = os.environ.get("CLAUDE_E2E_JUDGE_MODEL", "claude-sonnet-5-5")
    rc, out = run_in(
        container, ["omc", "configure", "--set", f"llm.providers.claude.model={model}"]
    )
    assert rc == 0, out
    rc, version = run_in(container, ["claude", "--version"])
    assert rc == 0, version
    return model, judge_model, {"claude_version": version.strip()}


def _judge_claude(container, model: str, scenario: str, rubric: list[str], text: str):
    import json

    prompt = (
        f"Judge this test scenario: {scenario}\nRubric:\n"
        + "\n".join(f"- {item}" for item in rubric)
        + f"\nAssistant answer:\n{text[:16000]}\n"
        + 'Reply with only JSON: {"passed": true or false, "reasons": ["..."]}'
    )
    container_id = container.get_wrapped_container().id
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-w",
            "/tmp",
            container_id,
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    envelope = json.loads(result.stdout)
    verdict = json.loads(envelope["result"])
    if not isinstance(verdict.get("passed"), bool) or not isinstance(verdict.get("reasons"), list):
        raise AssertionError(f"Claude judge returned malformed verdict: {verdict}")
    return verdict


def _scenario_setup(container, provider):
    from .conversation import ClaudeConversation

    if provider == "codex":
        model, metadata = _configure_codex_conversation(container)
        judge_model = model
        session = Conversation(container)
        judge = _judge_codex
    else:
        model, judge_model, metadata = _configure_claude_conversation(container)
        session = ClaudeConversation(container, model)
        judge = _judge_claude
    evidence = {
        "provider": provider,
        "model_requested": model,
        "judge_model_requested": judge_model,
        "metadata": metadata,
        "image": _image_provenance(container),
        "turns": [],
    }
    if provider == "codex":
        evidence.update(effort_configured="high", effort_observed_in_events=None)
    return session, judge, judge_model, evidence


def _launch_start(session, repo, provider, context):
    argv = ["omc", "start", context]
    if provider == "claude":
        argv.append("--headless")
    session.start(argv, repo, provider)


def _direct_implement(provider: str, detail: str = "") -> str:
    # Codex 0.156.1 rejects /omc:implement as an unknown TUI command. Its
    # supported explicit skill mention is $omc:implement; Claude uses /.
    command = "$omc:implement" if provider == "codex" else "/omc:implement"
    return command + (f" {detail}" if detail else "")


def _direct_audit(provider: str, detail: str = "") -> str:
    command = "$omc:audit" if provider == "codex" else "/omc:audit"
    return command + (f" {detail}" if detail else "")


def _direct_design(provider: str, detail: str = "") -> str:
    # Same TUI rule as _direct_implement: Codex accepts only the $omc: mention.
    command = "$omc:design" if provider == "codex" else "/omc:design"
    return command + (f" {detail}" if detail else "")


def _assert_recorded(before, after, label) -> str:
    """The design turn's artifact facts: product and remote untouched, HEAD
    advanced, exactly one new record under specs/. Returns its key."""
    for product_file in ("greeting.py", "test_greeting.py"):
        assert before["source"][product_file] == after["source"][product_file], (
            f"{label} changed {product_file}"
        )
    assert before["remote_refs"] == after["remote_refs"], f"{label} published something"
    assert before["head"] != after["head"], f"{label} committed nothing"
    new_specs = [
        key
        for key in after["spec_plan"]
        if key.startswith("docs/superpowers/specs/") and key not in before["spec_plan"]
    ]
    assert len(new_specs) == 1, f"{label} produced {len(new_specs)} design records: {new_specs}"
    return new_specs[0]


def _assert_reviewed_record(before: dict, after: dict, record: str) -> None:
    """The audit changes exactly the named design record among specs."""

    def specs(snapshot):
        return {key: value for key, value in snapshot["spec_plan"].items() if "/specs/" in key}

    old, new = specs(before), specs(after)
    assert record in old and record in new, f"review record missing: {record}"
    assert old[record] != new[record], f"review record unchanged: {record}"
    assert {key: value for key, value in old.items() if key != record} == {
        key: value for key, value in new.items() if key != record
    }, "audit changed another design record"


def _record_phase(session, repo, worktree, evidence, phase, turn):
    feature = session.snapshot(worktree)
    primary = session.snapshot(repo)
    evidence["turns"].append(
        {"phase": phase, "turn": turn, "snapshot": feature, "primary_snapshot": primary}
    )
    return feature, primary


def _assert_discussion_boundary(baseline, feature, primary_start, primary, phase):
    _assert_boundary(baseline, feature, phase)
    _assert_primary_boundary(primary_start, primary, phase)


def _start_discussion(container, provider, session, judge, judge_model, repo, evidence, context):
    initial = session.snapshot(repo)
    evidence["initial_snapshot"] = initial
    _launch_start(session, repo, provider, context)
    first = session.wait_turn(float(os.environ.get("OMC_E2E_FIRST_TURN_TIMEOUT", "900")))
    if provider == "codex":
        _assert_actor_settings(first, evidence["model_requested"])
    worktree, branch = _worktree(container, repo)
    baseline, primary_start = _record_phase(session, repo, worktree, evidence, "start", first)
    evidence["primary_after_start"] = primary_start
    evidence["worktree"] = worktree
    evidence["branch"] = branch
    for product_file in ("greeting.py", "test_greeting.py"):
        assert baseline["source"][product_file] == initial["source"][product_file], (
            f"start changed feature {product_file}"
        )
        assert primary_start["source"][product_file] == initial["source"][product_file], (
            f"slug/start changed primary {product_file}"
        )
    for key in ("index", "head", "remote_refs", "spec_plan"):
        assert baseline[key] == initial[key], f"start changed feature {key}"
        assert primary_start[key] == initial[key], f"slug/start changed primary {key}"
    primer = judge(
        container,
        judge_model,
        "OMC start received a greeting correction context",
        [
            "The answer presents project context or a primer about the greeting change.",
            "The answer asks the user for their seed or intended direction.",
            "The answer has not claimed implementation or publication.",
        ],
        first["text"],
    )
    evidence["primer_judge"] = primer
    assert primer["passed"], primer

    session.send(
        "My seed: change greeting() to return exactly 'Hello, world!'. "
        "Keep its zero-argument signature and add a unittest for the exact value. "
        "Please present the complete solution."
    )
    design = session.wait_turn(900)
    feature, primary = _record_phase(session, repo, worktree, evidence, "seed", design)
    _assert_discussion_boundary(baseline, feature, primary_start, primary, "seed discussion")
    _require_complete_design(
        container, judge, judge_model, session, repo, worktree, baseline, evidence, design
    )

    session.send(
        "One detail: preserve the exact capitalization and punctuation in "
        "'Hello, world!'. Please incorporate that into the design."
    )
    detail = session.wait_turn(600)
    feature, primary = _record_phase(session, repo, worktree, evidence, "detail", detail)
    _assert_discussion_boundary(baseline, feature, primary_start, primary, "design detail")

    session.send("ok")
    agreed = session.wait_turn(600)
    feature, primary = _record_phase(session, repo, worktree, evidence, "ok", agreed)
    _assert_discussion_boundary(baseline, feature, primary_start, primary, "agreement")
    return worktree, branch, baseline


def _assert_child_event(provider, events):
    if provider == "codex":
        assert any(
            event.get("type") == "function_call" and event.get("name") == "spawn_agent"
            for event in events
        ), "implementation did not dispatch a real Codex child"
    else:
        assert any(
            event.get("type") == "tool_use" and event.get("name") in ("Task", "Agent")
            for event in events
        ), "implementation did not dispatch a real Claude child"


def _assert_finish_stage_order(markers):
    seen = markers.splitlines()
    required = ("check", "build", "verify", "review")
    assert all(stage in seen for stage in required), markers
    # Retries of one stage leave adjacent markers, but do not change stage
    # order. Earlier implementation checks/reviews cannot stand in for finish.
    steps = [stage for i, stage in enumerate(seen) if i == 0 or stage != seen[i - 1]]
    assert any(
        tuple(steps[i : i + len(required)]) == required
        for i in range(len(steps) - len(required) + 1)
    ), markers


def _assert_implement_stage_order(markers: str) -> None:
    """Require an implementation check before adjacent build and verify markers."""
    seen = markers.splitlines()
    assert any(
        stage == "build" and i + 1 < len(seen) and seen[i + 1] == "verify" and "check" in seen[:i]
        for i, stage in enumerate(seen)
    ), f"implementation milestone lacks check → build → verify: {markers}"


def _assert_audit_verify_count(markers: str, *, drift_repair: bool) -> None:
    if drift_repair:
        assert markers.splitlines().count("verify") >= 3, (
            f"drift repair requires implementation, audit, and finish verification: {markers}"
        )


def _assert_successful_implementation(
    container, provider, session, repo, worktree, branch, evidence, baseline
):
    recorded = next(
        turn["snapshot"] for turn in evidence["turns"] if turn["phase"] in ("design", "resumed")
    )
    _assert_implemented_artifacts(container, repo, worktree, branch, evidence, recorded)
    evidence["events"] = session.events()
    _assert_child_event(provider, evidence["events"]["events"])
    return recorded


def _assert_audited_artifacts(
    container, repo, worktree, branch, evidence, implemented, *, drift_repair=False
):
    final = evidence["turns"][-1]["snapshot"]
    records = [key for key in implemented["spec_plan"] if "/specs/" in key]
    assert len(records) == 1, f"expected one design record: {records}"
    record = records[0]
    _assert_reviewed_record(implemented, final, record)
    rc, content = run_in(container, ["git", "-C", worktree, "show", f"HEAD:{record}"])
    assert rc == 0 and "## Implementation review" in content, "audit trace missing from record"
    rc, dirty = run_in(container, ["git", "-C", worktree, "status", "--porcelain"])
    assert rc == 0 and not dirty.strip(), f"audit left dirty tree: {dirty}"
    rc, commits = run_in(
        container, ["git", "-C", worktree, "rev-list", "--count", "origin/main..HEAD"]
    )
    assert rc == 0 and commits.strip() == "1", f"audit left {commits.strip()} commits"
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0, "project stages never executed"
    _assert_finish_stage_order(markers)
    _assert_audit_verify_count(markers, drift_repair=drift_repair)
    _assert_published_fix(
        container,
        repo,
        worktree,
        branch,
        "def greeting():\n    return 'Goodbye, world!'\n",
    )


def _assert_implemented_artifacts(container, repo, worktree, branch, evidence, recorded):
    """Implementation commits changed product and a plan without publication."""
    final = evidence["turns"][-1]["snapshot"]
    assert final["source"]["greeting.py"] != recorded["source"]["greeting.py"]
    plans = [k for k in final["spec_plan"] if "/plans/" in k]
    assert plans and not any("/plans/" in k for k in recorded["spec_plan"]), "no new plan"
    before_specs = {k: v for k, v in recorded["spec_plan"].items() if "/specs/" in k}
    after_specs = {k: v for k, v in final["spec_plan"].items() if "/specs/" in k}
    assert after_specs == before_specs, "implementation rewrote the design record"
    assert not any(ref.startswith(f"refs/heads/{branch} ") for ref in final["remote_refs"]), (
        "implementation published feature branch"
    )
    rc, dirty = run_in(container, ["git", "-C", worktree, "status", "--porcelain"])
    assert rc == 0 and not dirty.strip(), f"implementation left dirty tree: {dirty}"
    rc, commits = run_in(
        container, ["git", "-C", worktree, "rev-list", "--count", "origin/main..HEAD"]
    )
    assert rc == 0 and int(commits.strip()) >= 1, f"implementation left no task commits: {commits}"
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0, "implementation project stages never executed"
    _assert_implement_stage_order(markers)
