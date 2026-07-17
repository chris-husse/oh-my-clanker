import json

import pytest

from omc.config.schema import Config
from omc.errors import OmcError, Refusal
from omc.start import run_start
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env

OK_VERDICT = 'OMC_SLUG {"ok": true, "slug": "proj-1-fix-login"}'


def full_env(tmp_path, *, verdict=OK_VERDICT, wt_json=None):
    bindir = tmp_path / "bin"
    make_stub(bindir, "git", stdout="git version 2.99")
    make_stub(bindir, "claude", stdout=verdict)
    make_stub(bindir, "wt", stdout=json.dumps(wt_json or {"path": str(tmp_path / "wtree")}))
    return ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash"))


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
