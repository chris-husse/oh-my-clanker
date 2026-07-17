import json
import stat

from omc.toolctx import ToolContext
from omc.worktree import create_worktree, sync_base

from ._stubs import stub_env


def make_recording_stub(bindir, name, *, stdout="", rc=0, rc_first=None):
    """Stub that appends its argv to <bindir>/<name>.calls; optional different rc on 1st call."""
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / f"{name}.calls"
    script = f"""#!/bin/sh
echo "$@" >> "{calls}"
count=$(/usr/bin/wc -l < "{calls}")
if [ -n "{rc_first if rc_first is not None else ""}" ] && [ "$count" -eq 1 ]; then
  exit {rc_first if rc_first is not None else 0}
fi
echo '{stdout}'
exit {rc}
"""
    path = bindir / name
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return calls


def test_sync_base_fetches(tmp_path):
    bindir = tmp_path / "bin"
    calls = make_recording_stub(bindir, "git")
    ctx = ToolContext.from_env(stub_env(bindir))
    assert sync_base(ctx, "main") is True
    assert calls.read_text().strip() == "fetch origin main"


def test_sync_base_failure_is_nonfatal(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_recording_stub(bindir, "git", rc=1)
    ctx = ToolContext.from_env(stub_env(bindir))
    assert sync_base(ctx, "main") is False
    assert "warning" in capsys.readouterr().err


def test_create_worktree_fresh(tmp_path):
    bindir = tmp_path / "bin"
    out = json.dumps({"path": "/w/feature-x", "action": "created"})
    calls = make_recording_stub(bindir, "wt", stdout=out)
    ctx = ToolContext.from_env(stub_env(bindir))
    path = create_worktree(ctx, "feature/x", base="origin/main")
    assert path == "/w/feature-x"
    assert calls.read_text().splitlines() == [
        "switch --create feature/x --base origin/main --no-cd --yes --format=json"
    ]


def test_create_worktree_retries_without_create(tmp_path):
    bindir = tmp_path / "bin"
    out = json.dumps({"path": "/w/feature-x"})
    calls = make_recording_stub(bindir, "wt", stdout=out, rc_first=1)
    ctx = ToolContext.from_env(stub_env(bindir))
    path = create_worktree(ctx, "feature/x", base="origin/main")
    assert path == "/w/feature-x"
    lines = calls.read_text().splitlines()
    assert len(lines) == 2
    assert lines[1] == "switch feature/x --no-cd --yes --format=json"


def test_create_worktree_both_fail(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_recording_stub(bindir, "wt", rc=1)
    ctx = ToolContext.from_env(stub_env(bindir))
    assert create_worktree(ctx, "feature/x") is None
