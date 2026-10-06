import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omc.config.schema import Config, NotificationsConfig
from omc.errors import Refusal
from omc.implement import IMPLEMENT_ALLOWED_TOOLS, IMPLEMENT_SEED, run_implement
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


def _ctx(tmp_path, repo, monkeypatch, *, provider_stdout=""):
    """Real git on PATH (the gate runs real git), stubbed claude/codex/wt."""
    bindir = tmp_path / "bin"
    make_claude_stub(bindir, plugins=HEALTHY_PLUGINS, stdout=provider_stdout)
    make_stub(bindir, "wt", stdout="wt 0.1")
    make_stub(bindir, "codex", stdout="codex 0.156.1")
    git_dir = os.path.dirname(shutil.which("git"))
    monkeypatch.chdir(repo)
    return ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash", PATH=f"{bindir}:{git_dir}"))


def test_seed_is_the_bare_native_command():
    assert IMPLEMENT_SEED == "/omc:implement"


def test_allow_list_can_write_and_dispatch():
    for tool in ("Bash", "Read", "Edit", "Write", "Glob", "Grep", "Agent", "Skill"):
        assert tool in IMPLEMENT_ALLOWED_TOOLS
    assert "mcp__jira" in IMPLEMENT_ALLOWED_TOOLS


def test_dry_run_prints_record_and_named_session(tmp_path, monkeypatch, capsys):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    assert run_implement(ctx, Config(), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "omc implement — plan (dry run, no changes made):" in out
    assert "branch:" in out and f"feature/{SLUG}" in out
    assert "record:" in out and RECORD in out
    assert "session:" in out and f"{SLUG}-implement" in out
    assert "session argv:" in out and "'/omc:implement'" in out
    assert f"'-n', '{SLUG}-implement'" in out
    assert "shell argv:" in out and "notify:" in out


def test_dry_run_never_writes_notification_files(tmp_path, monkeypatch):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config(notifications=NotificationsConfig(enabled=True))
    assert run_implement(ctx, cfg, dry_run=True) == 0
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_missing_record_refuses_with_exit_two_and_never_launches(tmp_path, monkeypatch, capsys):
    repo = _worktree(tmp_path, record=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    with pytest.raises(Refusal, match="/omc:design") as exc:
        run_implement(ctx, Config(), headless=True)
    assert exc.value.rc == 2
    # The gate runs before the probe and the plugin check: claude was never
    # invoked at all (the stub creates its calls file on first invocation).
    assert not (tmp_path / "bin" / "claude.calls").exists()


def test_unclean_record_refuses(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, commit=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    with pytest.raises(Refusal, match="not committed in HEAD"):
        run_implement(ctx, Config(), dry_run=True)


def test_non_omc_branch_refuses_before_any_launch(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, branch="main-work", record=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    with pytest.raises(Refusal, match="not an omc branch") as exc:
        run_implement(ctx, Config(), dry_run=True)
    assert "feature/" in str(exc.value)


def test_headless_names_the_session_and_widens_tools(tmp_path, monkeypatch):
    import omc.implement as impl

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    seen = {}

    def fake_headless(ctx, cfg, seed, cwd, slug, *, session_name=None, allowed_tools=None):
        seen.update(seed=seed, cwd=cwd, slug=slug, session_name=session_name, tools=allowed_tools)
        return 0

    monkeypatch.setattr(impl, "run_headless", fake_headless)
    assert run_implement(ctx, Config(), headless=True) == 0
    assert seen["seed"] == "/omc:implement" and seen["slug"] == SLUG
    assert seen["session_name"] == f"{SLUG}-implement"
    assert seen["tools"] == IMPLEMENT_ALLOWED_TOOLS
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(str(repo))


def test_headless_wires_notifications_idempotently(tmp_path, monkeypatch):
    import omc.implement as impl

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    monkeypatch.setattr(impl, "run_headless", lambda *a, **k: 0)
    cfg = Config(notifications=NotificationsConfig(enabled=True))
    assert run_implement(ctx, cfg, headless=True) == 0
    settings = repo / ".claude" / "settings.local.json"
    first = settings.read_text()
    assert run_implement(ctx, cfg, headless=True) == 0
    assert settings.read_text() == first  # second launch merges, never duplicates
    hooks = json.loads(first)["hooks"]
    assert "Notification" in hooks and "Stop" in hooks


def test_interactive_execs_in_the_worktree_with_slug_env(tmp_path, monkeypatch):
    import omc.implement as impl

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    seen = []
    monkeypatch.setattr(
        impl,
        "detect_shell",
        lambda env: SimpleNamespace(exec_interactive=lambda **kwargs: seen.append(kwargs)),
    )
    monkeypatch.setattr(impl, "os", SimpleNamespace(environ={}, path=os.path))
    assert run_implement(ctx, Config()) == 0
    assert os.path.realpath(seen[0]["cwd"]) == os.path.realpath(str(repo))
    assert seen[0]["title"] == f"feature/{SLUG}"
    assert seen[0]["startup_argv"][-1] == "/omc:implement"
    assert impl.os.environ["OMC_SLUG"] == SLUG


def test_override_probes_the_overridden_provider(tmp_path, monkeypatch, capsys):
    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"  # what _with_provider(cfg, "codex") yields
    assert run_implement(ctx, cfg, dry_run=True) == 0
    err = capsys.readouterr().err
    assert "→ probing tools (git, wt, codex)" in err
    assert "→ omc plugin for codex: unverified" in err


def test_implement_override_ensures_global_section_before_launch(tmp_path, monkeypatch):
    import omc.implement as impl
    from omc.agentsmd import BEGIN_MARKER

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"
    target = tmp_path / ".codex" / "AGENTS.md"

    def fake_headless(*args, **kwargs):
        assert BEGIN_MARKER in target.read_bytes()
        return 0

    monkeypatch.setattr(impl, "run_headless", fake_headless)
    assert run_implement(ctx, cfg, headless=True) == 0


def test_implement_continues_when_global_section_is_malformed(tmp_path, monkeypatch, capsys):
    import omc.implement as impl
    from omc.agentsmd import BEGIN_MARKER

    repo = _worktree(tmp_path)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    malformed = BEGIN_MARKER + b"\nunterminated section\n"
    target.write_bytes(malformed)
    monkeypatch.setattr(impl, "run_headless", lambda *args, **kwargs: 0)

    assert run_implement(ctx, cfg, headless=True) == 0
    assert target.read_bytes() == malformed
    assert "✗ codex global instructions:" in capsys.readouterr().err


def test_implement_record_refusal_does_not_write_global_section(tmp_path, monkeypatch):
    repo = _worktree(tmp_path, record=False)
    ctx = _ctx(tmp_path, repo, monkeypatch)
    cfg = Config()
    cfg.llm.default = "codex"
    with pytest.raises(Refusal):
        run_implement(ctx, cfg, headless=True)
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()
