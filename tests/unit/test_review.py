import ast
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omc.config.schema import Config, NotificationsConfig
from omc.errors import Refusal
from omc.implement import IMPLEMENT_ALLOWED_TOOLS
from omc.review import REVIEW_SEED, run_review
from omc.toolctx import ToolContext

from ._stubs import HEALTHY_PLUGINS, make_claude_stub, make_stub, stub_env

SLUG = "proj-1-fix-login"
RECORD = f"docs/superpowers/specs/2026-10-02-{SLUG}-design.md"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _worktree(tmp_path, *, branch=f"feature/{SLUG}", record=True, commit=True):
    repo = tmp_path / "wt"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("checkout", "-qb", branch, cwd=repo)
    if record:
        (repo / RECORD).parent.mkdir(parents=True)
        (repo / RECORD).write_text("# design\n")
        if commit:
            _git("add", "-A", cwd=repo)
            _git("commit", "-qm", "spec", cwd=repo)
    return repo


def _ctx(tmp_path, repo, monkeypatch):
    """Real git for the record gate; stubbed provider and worktree tool."""
    bindir = tmp_path / "bin"
    make_claude_stub(bindir, plugins=HEALTHY_PLUGINS)
    make_stub(bindir, "wt", stdout="wt 0.1")
    make_stub(bindir, "codex", stdout="codex 0.156.1")
    git_dir = os.path.dirname(shutil.which("git"))
    monkeypatch.chdir(repo)
    return ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash", PATH=f"{bindir}:{git_dir}"))


def test_seed_is_the_bare_audit_command():
    assert REVIEW_SEED == "/omc:audit"


def test_dry_run_prints_record_and_audit_session(tmp_path, monkeypatch, capsys):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    assert run_review(ctx, Config(), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "omc review — plan (dry run, no changes made):" in out
    assert f"branch:       feature/{SLUG}" in out
    assert f"record:       {RECORD}" in out
    assert f"session:      {SLUG}-audit" in out
    argv_row = next(line for line in out.splitlines() if "session argv:" in line)
    assert ast.literal_eval(argv_row.split("session argv:", 1)[1].strip()) == [
        "claude",
        "-n",
        f"{SLUG}-audit",
        "/omc:audit",
    ]
    assert "shell argv:" in out and "notify:       disabled" in out


def test_dry_run_never_writes_notification_files(tmp_path, monkeypatch, capsys):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config(notifications=NotificationsConfig(enabled=True))
    assert run_review(ctx, cfg, dry_run=True) == 0
    assert "notify:       backend" in capsys.readouterr().out
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_missing_record_refuses_before_provider_probe(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, record=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    with pytest.raises(Refusal, match="/omc:design") as exc:
        run_review(ctx, Config(), headless=True)
    assert exc.value.rc == 2
    assert not (tmp_path / "bin" / "claude.calls").exists()


def test_unclean_record_refuses(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, commit=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    with pytest.raises(Refusal, match="not committed in HEAD"):
        run_review(ctx, Config(), dry_run=True)


def test_non_omc_branch_refuses_before_launch(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, branch="main-work", record=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    with pytest.raises(Refusal, match="not an omc branch"):
        run_review(ctx, Config(), dry_run=True)


def test_headless_names_session_passes_allow_list_and_return_code(tmp_path, monkeypatch):
    import omc.review as review

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    seen = {}

    def fake_headless(ctx, cfg, seed, cwd, slug, *, session_name=None, allowed_tools=None):
        seen.update(seed=seed, cwd=cwd, slug=slug, session_name=session_name, tools=allowed_tools)
        return 7

    monkeypatch.setattr(review, "run_headless", fake_headless)
    assert run_review(ctx, Config(), headless=True) == 7
    assert seen["seed"] == "/omc:audit" and seen["slug"] == SLUG
    assert seen["session_name"] == f"{SLUG}-audit"
    assert seen["tools"] is IMPLEMENT_ALLOWED_TOOLS
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(str(repo))


def test_headless_wires_notifications_idempotently(tmp_path, monkeypatch):
    import omc.review as review

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    monkeypatch.setattr(review, "run_headless", lambda *a, **k: 0)
    cfg = Config(notifications=NotificationsConfig(enabled=True))
    assert run_review(ctx, cfg, headless=True) == 0
    settings = repo / ".claude" / "settings.local.json"
    first = settings.read_text()
    assert run_review(ctx, cfg, headless=True) == 0
    assert settings.read_text() == first
    hooks = json.loads(first)["hooks"]
    assert "Notification" in hooks and "Stop" in hooks


def test_interactive_execs_in_worktree_with_slug_env(tmp_path, monkeypatch):
    import omc.review as review

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    seen = []
    monkeypatch.setattr(
        review,
        "detect_shell",
        lambda env: SimpleNamespace(exec_interactive=lambda **kwargs: seen.append(kwargs)),
    )
    monkeypatch.setattr(review, "os", SimpleNamespace(environ={}, path=os.path))
    assert run_review(ctx, Config()) == 0
    assert os.path.realpath(seen[0]["cwd"]) == os.path.realpath(str(repo))
    assert seen[0]["title"] == f"feature/{SLUG}"
    assert seen[0]["startup_argv"] == ["claude", "-n", f"{SLUG}-audit", "/omc:audit"]
    assert review.os.environ["OMC_SLUG"] == SLUG


def test_override_probes_the_overridden_provider(tmp_path, monkeypatch, capsys):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"
    assert run_review(ctx, cfg, dry_run=True) == 0
    err = capsys.readouterr().err
    assert "→ probing tools (git, wt, codex)" in err
    assert "→ omc plugin for codex: unverified" in err


def test_review_override_repairs_selected_provider_before_launch(tmp_path, monkeypatch):
    import omc.review as review
    from omc.agentsmd import BEGIN_MARKER

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"
    target = tmp_path / ".codex" / "AGENTS.md"

    def fake_headless(*args, **kwargs):
        assert BEGIN_MARKER in target.read_bytes()
        return 0

    monkeypatch.setattr(review, "run_headless", fake_headless)
    assert run_review(ctx, cfg, headless=True) == 0
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()


def test_review_continues_and_reports_malformed_global_section(tmp_path, monkeypatch, capsys):
    import omc.review as review
    from omc.agentsmd import BEGIN_MARKER

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    malformed = BEGIN_MARKER + b"\nunterminated section\n"
    target.write_bytes(malformed)
    monkeypatch.setattr(review, "run_headless", lambda *args, **kwargs: 0)

    assert run_review(ctx, cfg, headless=True) == 0
    assert target.read_bytes() == malformed
    assert "✗ codex global instructions:" in capsys.readouterr().err


def test_review_dry_run_does_not_repair_global_section(tmp_path, monkeypatch):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"

    assert run_review(ctx, cfg, dry_run=True) == 0
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()


def test_review_record_refusal_does_not_repair_global_section(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, record=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"

    with pytest.raises(Refusal):
        run_review(ctx, cfg, headless=True)
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()
