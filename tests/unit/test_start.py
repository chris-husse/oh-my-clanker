import json
import os
import stat

import pytest

from omc.config.schema import Config
from omc.errors import OmcError, Refusal
from omc.start import run_start
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env

OK_VERDICT = 'OMC_SLUG {"ok": true, "slug": "proj-1-fix-login"}'


def _make_git_stub(bindir):
    """git stub that's argv-aware just enough for run_start's needs: --version
    (require_tools' probe) answers like a real git; rev-parse (repo_root, now
    called unconditionally by run_start) reports "not a repo" so these
    tools-only tests don't accidentally resolve to the real project checkout."""
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / "git"
    path.write_text(
        '#!/bin/sh\ncase "$1" in\n  rev-parse) exit 128 ;;\n  *) echo "git version 2.99" ;;\nesac\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def full_env(tmp_path, *, verdict=OK_VERDICT, wt_json=None):
    bindir = tmp_path / "bin"
    _make_git_stub(bindir)
    # The static stub answers EVERY claude invocation with the same stdout, so
    # prepend an "omc@" line: ensure_plugin's `plugin list` probe sees the
    # plugin as installed, and parse_verdict ignores non-OMC_SLUG lines.
    make_stub(bindir, "claude", stdout=f"omc@oh-my-clanker\n{verdict}")
    make_stub(bindir, "wt", stdout=json.dumps(wt_json or {"path": str(tmp_path / "wtree")}))
    return ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash"))


def test_progress_lines_narrate_phases(tmp_path, capsys):
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()
    rc = run_start(ctx, Config(), "PROJ-1", headless=True)
    assert rc == 0
    err = capsys.readouterr().err
    probe_at = err.index("→ probing tools (git, wt, claude)")
    plugin_at = err.index("→ omc plugin for claude: ok")
    slug_start_at = err.index("→ generating slug via claude")
    slug_done_at = err.index("✓ slug: proj-1-fix-login")
    wt_at = err.index("→ creating worktree feature/proj-1-fix-login")
    launch_at = err.index("→ running headless claude session seeded with /omc:start")
    order = [probe_at, plugin_at, slug_start_at, slug_done_at, wt_at, launch_at]
    assert order == sorted(order), f"phase lines out of order:\n{err}"


def test_dry_run_prints_plan(tmp_path, capsys):
    ctx = full_env(tmp_path)
    rc = run_start(ctx, Config(), "PROJ-1", dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "branch:" in out and "feature/proj-1-fix-login" in out
    assert "session argv:" in out and "/omc:start PROJ-1" in out
    assert "-n" in out and "proj-1-fix-login" in out  # session named after slug
    assert "title seq:" in out


def test_probe_failure_lists_misses(tmp_path):
    bindir = tmp_path / "bin"
    make_stub(bindir, "git", stdout="git version 2.99")  # wt + claude missing
    ctx = ToolContext.from_env(stub_env(bindir))
    with pytest.raises(OmcError, match="missing tools"):
        run_start(ctx, Config(), "PROJ-1", dry_run=True)


def test_slug_refusal_propagates(tmp_path):
    bad = 'OMC_SLUG {"ok": false, "reason": "mcp-missing", "message": "add a Jira MCP"}'
    ctx = full_env(tmp_path, verdict=bad)
    with pytest.raises(Refusal, match="add a Jira MCP"):
        run_start(ctx, Config(), "PROJ-1", dry_run=True)


def test_headless_runs_seed_in_worktree(tmp_path, capsys):
    ctx = full_env(tmp_path)
    (tmp_path / "wtree").mkdir()  # the wt stub reports this path; headless runs cwd=path
    rc = run_start(ctx, Config(), "PROJ-1", headless=True)
    assert rc == 0
    # the claude stub echoes its verdict for both calls; transcript is printed
    assert "OMC_SLUG" in capsys.readouterr().out


def _notify_cfg():
    cfg = Config()
    cfg.notifications.enabled = True
    return cfg


def test_start_wires_notifications_when_enabled(tmp_path, capsys):
    ctx = full_env(tmp_path)
    wt = tmp_path / "wtree"
    wt.mkdir()
    rc = run_start(ctx, _notify_cfg(), "PROJ-1", headless=True)
    assert rc == 0
    settings = json.loads((wt / ".claude" / "settings.local.json").read_text())
    assert "Notification" in settings["hooks"]
    assert "✓ notification wiring: .claude/settings.local.json" in capsys.readouterr().err


def test_start_skips_wiring_when_disabled(tmp_path):
    ctx = full_env(tmp_path)
    wt = tmp_path / "wtree"
    wt.mkdir()
    rc = run_start(ctx, Config(), "PROJ-1", headless=True)
    assert rc == 0
    assert not (wt / ".claude").exists()


def test_dry_run_shows_notify_plan(tmp_path, capsys):
    ctx = full_env(tmp_path)
    rc = run_start(ctx, _notify_cfg(), "PROJ-1", dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "notify:       backend macos; files: .claude/settings.local.json" in out
    rc = run_start(ctx, Config(), "PROJ-1", dry_run=True)
    assert "notify:       disabled" in capsys.readouterr().out


def _repo_env(tmp_path):
    """A real git repo (repo_root/ensure_agents_chain need a real toplevel) with
    stubbed wt/claude on PATH — mirrors full_env but keeps the system git
    reachable instead of a canned stub, like test_configure's chain test does."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    bindir = tmp_path / "bin"
    make_stub(bindir, "claude", stdout=f"omc@oh-my-clanker\n{OK_VERDICT}")
    make_stub(bindir, "wt", stdout=json.dumps({"path": str(tmp_path / "wtree")}))
    env = stub_env(bindir, SHELL="/bin/bash")
    env["PATH"] = f"{bindir}:{os.environ['PATH']}"  # real git alongside the stubs
    return ToolContext.from_env(env), repo


def test_start_dry_run_ensures_the_chain(tmp_path, monkeypatch):
    from omc.agentsmd import chain_healthy

    ctx, repo = _repo_env(tmp_path)
    monkeypatch.chdir(repo)
    rc = run_start(ctx, Config(), "PROJ-1 do the thing", dry_run=True)
    assert rc == 0
    assert chain_healthy(repo)  # chain exists even on dry runs


def test_start_proceeds_when_chain_is_blocked(tmp_path, monkeypatch):
    ctx, repo = _repo_env(tmp_path)
    (repo / "AGENTS.md").write_text("# handwritten\n")
    monkeypatch.chdir(repo)
    rc = run_start(ctx, Config(), "PROJ-1 do the thing", dry_run=True)
    assert rc == 0  # blocked chain never stops start
    assert (repo / "AGENTS.md").read_text() == "# handwritten\n"
