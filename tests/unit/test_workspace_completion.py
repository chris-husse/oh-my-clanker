"""Completion must distinguish a design/plan from an actual implementation handoff."""

import json
import os
import subprocess

import pytest

from omc.toolctx import ToolContext
from omc.workspace import run_workspace


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    ctx = ToolContext(home=tmp_path / "omc", env=env)

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")

    def commit(path, content="content"):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git("add", "--", path)
        git("commit", "-m", path)
        return git("rev-parse", "HEAD")

    commit("base.txt")
    git("switch", "-c", "feature/example")
    commit("docs/superpowers/specs/2026-10-08-example-design.md")
    return repo, ctx, commit, git


def status(ctx, capsys):
    code = run_workspace(ctx, ["implementation-status", "example"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("OMC_WORKSPACE ")
    return code, json.loads(lines[0].removeprefix("OMC_WORKSPACE "))


PLAN = "docs/superpowers/plans/2026-10-08-example-plan.md"


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("design-only", False),
        ("plan-only", False),
        ("wrong-slug-plan", False),
        ("product-before-plan", False),
        ("product-with-plan", False),
        ("design-after-plan", False),
        ("handoff", True),
        ("plan-amended-at-handoff", True),
        ("dirty-tracked", False),
        ("dirty-untracked", False),
        ("ambiguous-plan", False),
    ],
)
def test_completion_from_real_git_history(repository, capsys, scenario, expected):
    repo, ctx, commit, git = repository
    if scenario == "product-before-plan":
        commit("product.py")
    if scenario == "product-with-plan":
        (repo / "product.py").write_text("implementation")
        git("add", "product.py")
    if scenario == "wrong-slug-plan":
        commit("docs/superpowers/plans/2026-10-08-other-plan.md")
        commit("product.py")
    elif scenario != "design-only":
        commit(PLAN)
    if scenario == "design-after-plan":
        commit("docs/superpowers/specs/2026-10-08-example-design.md", "amended")
    if scenario in {
        "handoff",
        "plan-amended-at-handoff",
        "dirty-tracked",
        "dirty-untracked",
        "ambiguous-plan",
    }:
        commit("product.py")
    if scenario == "plan-amended-at-handoff":
        commit(PLAN, "completed tasks")
    if scenario == "ambiguous-plan":
        commit("docs/superpowers/plans/2026-10-09-example-plan.md")
    if scenario == "dirty-tracked":
        (repo / "product.py").write_text("unfinished")
    if scenario == "dirty-untracked":
        (repo / "untracked.txt").write_text("unfinished")
    code, payload = status(ctx, capsys)
    assert code == 0, payload
    assert payload["ok"] is True
    assert payload["complete"] is expected
    assert not ctx.home.exists(), "artifact inspection must never write a ledger"


def test_completion_inspection_error_is_not_success(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    ctx = ToolContext(home=tmp_path / "omc", env=os.environ)
    code, payload = status(ctx, capsys)
    assert code == 1
    assert payload["ok"] is False
    assert payload.get("complete") is not True
