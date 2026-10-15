import json
import shlex
import stat

import pytest

from omc.errors import OmcError
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
    make_worktree_git_stub(bindir)
    ctx = ToolContext.from_env(stub_env(bindir))
    path = create_worktree(ctx, "feature/x", base="origin/main")
    assert path == "/w/feature-x"
    assert calls.read_text().splitlines() == [
        "switch --create feature/x --base origin/main --no-cd --yes --format=json"
    ]


def make_worktree_git_stub(
    bindir,
    *,
    branch_before=False,
    branch_after=False,
    worktrees_before=(),
    worktrees_after=(),
    show_ref_before_rc=None,
    list_before_rc=0,
    list_after_rc=0,
    remove_rc=0,
    delete_rc=0,
):
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "git.calls"
    marker = bindir / "wt.invoked"

    def porcelain(worktrees):
        entries = [("/repo", "main"), *worktrees]
        return "".join(
            f"worktree {path}\nHEAD deadbeef\nbranch refs/heads/{branch}\n\n"
            for path, branch in entries
        )

    script = f"""#!/bin/sh
printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
if [ -e {shlex.quote(str(marker))} ]; then
  branch_rc={0 if branch_after else 1}
  list_rc={list_after_rc}
  worktrees='{porcelain(worktrees_after)}'
else
  branch_rc={show_ref_before_rc if show_ref_before_rc is not None else (0 if branch_before else 1)}
  list_rc={list_before_rc}
  worktrees='{porcelain(worktrees_before)}'
fi
case "$1 $2" in
  'show-ref --verify') exit "$branch_rc" ;;
  'worktree list') printf '%s' "$worktrees"; exit "$list_rc" ;;
  'worktree remove') exit {remove_rc} ;;
  'branch -D') exit {delete_rc} ;;
esac
exit 99
"""
    path = bindir / "git"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return calls


def make_worktree_wt_stub(bindir, *, stdout="", stderr="", rc=0):
    calls = bindir / "wt.calls"
    marker = bindir / "wt.invoked"
    script = f"""#!/bin/sh
printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
: > {shlex.quote(str(marker))}
/bin/cat <<'OMC_WT_STDOUT'
{stdout}
OMC_WT_STDOUT
/bin/cat >&2 <<'OMC_WT_STDERR'
{stderr}
OMC_WT_STDERR
exit {rc}
"""
    path = bindir / "wt"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return calls


def test_failed_new_cut_cleans_only_new_worktree_and_branch(tmp_path):
    bindir = tmp_path / "bin"
    git_calls = make_worktree_git_stub(
        bindir,
        branch_after=True,
        worktrees_after=(("/w/feature-x", "feature/x"),),
    )
    wt_calls = make_worktree_wt_stub(
        bindir,
        rc=1,
        stderr="✗ pre-start command failed: exit status: 7",
    )
    ctx = ToolContext.from_env(stub_env(bindir))

    with pytest.raises(OmcError) as error:
        create_worktree(ctx, "feature/x", base="origin/main")

    message = str(error.value)
    assert "feature/x" in message
    assert "exit 1" in message
    assert "✗ pre-start command failed: exit status: 7" in message
    assert "wt config state logs" in message
    assert wt_calls.read_text().splitlines() == [
        "switch --create feature/x --base origin/main --no-cd --yes --format=json"
    ]
    assert git_calls.read_text().splitlines() == [
        "show-ref --verify --quiet refs/heads/feature/x",
        "worktree list --porcelain",
        "worktree list --porcelain",
        "worktree remove --force /w/feature-x",
        "show-ref --verify --quiet refs/heads/feature/x",
        "branch -D feature/x",
    ]


def test_existing_branch_switches_once_without_create_or_base(tmp_path):
    bindir = tmp_path / "bin"
    make_worktree_git_stub(bindir, branch_before=True, branch_after=True)
    calls = make_worktree_wt_stub(bindir, stdout='{"path": "/w/feature-x"}')
    ctx = ToolContext.from_env(stub_env(bindir))

    assert create_worktree(ctx, "feature/x", base="origin/main") == "/w/feature-x"
    assert calls.read_text().splitlines() == ["switch feature/x --no-cd --yes --format=json"]


def test_failed_existing_worktree_and_branch_are_preserved(tmp_path):
    bindir = tmp_path / "bin"
    calls = make_worktree_git_stub(
        bindir,
        branch_before=True,
        branch_after=True,
        worktrees_before=(("/w/feature-x", "feature/x"),),
        worktrees_after=(("/w/feature-x", "feature/x"),),
    )
    make_worktree_wt_stub(bindir, rc=7)
    with pytest.raises(OmcError, match="exit 7"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    assert not any(
        line.startswith(("worktree remove", "branch -D")) for line in calls.read_text().splitlines()
    )


def test_failed_existing_branch_removes_only_new_worktree(tmp_path):
    bindir = tmp_path / "bin"
    calls = make_worktree_git_stub(
        bindir,
        branch_before=True,
        branch_after=True,
        worktrees_after=(("/w/feature-x", "feature/x"),),
    )
    make_worktree_wt_stub(bindir, rc=7, stderr="✗ pre-start command failed: exit status: 7")
    with pytest.raises(OmcError, match="exit 7"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    assert "worktree remove --force /w/feature-x" in calls.read_text().splitlines()
    assert "branch -D feature/x" not in calls.read_text().splitlines()


def test_failed_cut_before_creating_resources_does_not_remove_anything(tmp_path):
    bindir = tmp_path / "bin"
    calls = make_worktree_git_stub(bindir)
    make_worktree_wt_stub(bindir, rc=1, stderr="invalid base")
    with pytest.raises(OmcError, match="invalid base"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    assert "worktree remove" not in calls.read_text()
    assert "branch -D" not in calls.read_text()


@pytest.mark.parametrize("stdout", ["not json", "{}", '{"path": ""}'])
def test_pathless_or_malformed_success_is_failure_and_cleans_up(tmp_path, stdout):
    bindir = tmp_path / "bin"
    calls = make_worktree_git_stub(
        bindir,
        branch_after=True,
        worktrees_after=(("/w/feature-x", "feature/x"),),
    )
    make_worktree_wt_stub(bindir, stdout=stdout)
    with pytest.raises(OmcError, match="wt config state logs"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    assert "worktree remove --force /w/feature-x" in calls.read_text().splitlines()
    assert "branch -D feature/x" in calls.read_text().splitlines()


@pytest.mark.parametrize("stdout", ["not json", "{}"])
def test_pathless_success_for_existing_branch_removes_new_worktree(tmp_path, stdout):
    bindir = tmp_path / "bin"
    git_calls = make_worktree_git_stub(
        bindir,
        branch_before=True,
        branch_after=True,
        worktrees_after=(("/w/feature-x", "feature/x"),),
    )
    wt_calls = make_worktree_wt_stub(bindir, stdout=stdout)

    with pytest.raises(OmcError, match=r"returned no valid path \(exit 0\)"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")

    assert wt_calls.read_text().splitlines() == ["switch feature/x --no-cd --yes --format=json"]
    assert git_calls.read_text().splitlines() == [
        "show-ref --verify --quiet refs/heads/feature/x",
        "worktree list --porcelain",
        "worktree list --porcelain",
        "worktree remove --force /w/feature-x",
    ]


def test_invalid_success_json_preserves_stderr(tmp_path):
    bindir = tmp_path / "bin"
    make_worktree_git_stub(bindir)
    make_worktree_wt_stub(bindir, stdout="not json", stderr="wt output was incomplete")
    with pytest.raises(OmcError, match="wt output was incomplete"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")


def test_missing_wt_raises_omc_error(tmp_path):
    bindir = tmp_path / "bin"
    make_worktree_git_stub(bindir)
    with pytest.raises(OmcError, match="wt config state logs"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")


def test_cleanup_failure_preserves_original_error(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_worktree_git_stub(
        bindir,
        branch_after=True,
        worktrees_after=(("/w/feature-x", "feature/x"),),
        remove_rc=1,
        delete_rc=1,
    )
    make_worktree_wt_stub(bindir, rc=7, stderr="the hook failed")
    with pytest.raises(OmcError, match="the hook failed"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    err = capsys.readouterr().err
    assert "✗ could not remove worktree" in err
    assert "✗ could not remove branch" in err


def test_prefix_branch_worktree_is_not_removed(tmp_path):
    bindir = tmp_path / "bin"
    calls = make_worktree_git_stub(
        bindir,
        worktrees_after=(("/w/feature-x-other", "feature/x-other"),),
    )
    make_worktree_wt_stub(bindir, rc=7)
    with pytest.raises(OmcError, match="exit 7"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    assert "worktree remove" not in calls.read_text()


@pytest.mark.parametrize("probe", ["branch", "worktree"])
def test_prestate_probe_failure_never_calls_wt(tmp_path, probe):
    bindir = tmp_path / "bin"
    make_worktree_git_stub(
        bindir,
        show_ref_before_rc=128 if probe == "branch" else None,
        list_before_rc=128 if probe == "worktree" else 0,
    )
    make_worktree_wt_stub(bindir, stdout='{"path": "/w/feature-x"}')
    with pytest.raises(OmcError, match="feature/x"):
        create_worktree(ToolContext.from_env(stub_env(bindir)), "feature/x")
    assert not (bindir / "wt.calls").exists()


def test_switch_command_selects_existing_branch_for_dry_run(tmp_path):
    from omc.worktree import switch_command

    bindir = tmp_path / "bin"
    make_worktree_git_stub(bindir, branch_before=True)
    ctx = ToolContext.from_env(stub_env(bindir))
    assert switch_command(ctx, "feature/x", "origin/main") == (
        ["wt", "switch", "feature/x", "--no-cd", "--yes", "--format=json"],
        True,
    )
