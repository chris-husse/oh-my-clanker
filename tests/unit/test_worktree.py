import json
import stat
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from omc.toolctx import ToolContext
from omc.worktree import create_worktree, sync_base

from ._stubs import stub_env


@pytest.fixture
def recorded_context(tmp_path, monkeypatch):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    calls = []
    responses = []

    def run(argv, *, cwd=None):
        calls.append(SimpleNamespace(argv=list(argv), cwd=cwd))
        return responses.pop(0)

    monkeypatch.setattr(ctx, "run", run)
    return ctx, calls, responses


def test_create_worktree_explicit_cwd_and_retry(tmp_path, recorded_context):
    ctx, calls, responses = recorded_context
    primary = tmp_path / "primary with spaces"
    target = tmp_path / "shared worktree"
    responses.extend(
        [
            CompletedProcess([], 1, "", "branch already exists"),
            CompletedProcess([], 0, json.dumps({"path": str(target)}), ""),
        ]
    )

    assert create_worktree(ctx, "topic/shared", "origin/develop", cwd=primary) == str(target)
    assert len(calls) == 2
    assert calls[0].argv == [
        ctx.wt_bin,
        "-C",
        str(primary),
        "switch",
        "--create",
        "topic/shared",
        "--base",
        "origin/develop",
        "--no-cd",
        "--yes",
        "--format=json",
    ]
    assert calls[1].argv == [
        ctx.wt_bin,
        "-C",
        str(primary),
        "switch",
        "topic/shared",
        "--no-cd",
        "--yes",
        "--format=json",
    ]
    assert [call.cwd for call in calls] == [None, None]


@pytest.mark.parametrize("returncode", [0, 1])
def test_sync_base_explicit_cwd(tmp_path, recorded_context, returncode, capsys):
    ctx, calls, responses = recorded_context
    primary = tmp_path / "primary with spaces"
    responses.append(CompletedProcess([], returncode, "", "offline" if returncode else ""))

    assert sync_base(ctx, "develop", cwd=primary) is (returncode == 0)
    assert len(calls) == 1
    fetch = calls[0]
    assert fetch.argv == [ctx.git_bin, "fetch", "origin", "develop"]
    assert fetch.cwd == str(primary)
    assert bool(capsys.readouterr().err) is (returncode != 0)


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
