"""Real Worktrunk regression for a failing pre-start hook."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from omc.config.schema import Config
from omc.errors import OmcError
from omc.session import run_headless
from omc.toolctx import ToolContext
from omc.worktree import create_worktree
from omc.wtconfig import WT_TEMPLATE

# The oldest Worktrunk these tests support; CI installs exactly this one
# (.github/workflows/ci.yml).
WT_MINIMUM = (0, 79, 0)
_WT_MIN = ".".join(map(str, WT_MINIMUM))
WT_INSTALL = (
    f"Worktrunk v{_WT_MIN} or newer is required. On Linux run: "
    'target="$(uname -m)-unknown-linux-musl"; '
    'asset="worktrunk-${target}.tar.xz"; '
    f"release=https://github.com/max-sixty/worktrunk/releases/download/v{_WT_MIN}; "
    'curl -fsSLO "$release/$asset"; '
    'curl -fsSLO "$release/$asset.sha256"; '
    'sha256sum -c "$asset.sha256"; '
    'tar -xJf "$asset"; '
    'sudo install -m 0755 "worktrunk-${target}/wt" /usr/local/bin/wt'
)


# The pre-start hook's `exit 7`, as each supported Worktrunk reports it:
# v0.79: "pre-start command failed: exit status: 7"
# v0.82: "✗ pre-start project hook failed (exit code 7)"
HOOK_EXIT_7 = r"pre-start (command failed: exit status: 7\b|project hook failed \(exit code 7\))"


def _require_wt(wt):
    """`wt` when it is WT_MINIMUM or newer; older or unparseable fails loud with the install."""
    if wt is None:
        pytest.fail(WT_INSTALL)
    found = subprocess.run(
        [wt, "--version"], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    match = re.match(r"wt v?(\d+)\.(\d+)\.(\d+)\b", found)
    if match is None or tuple(map(int, match.groups())) < WT_MINIMUM:
        pytest.fail(f"{WT_INSTALL} (found `{found}` at {wt})")
    return wt


@pytest.fixture
def failing_hook_repo(tmp_path, request):
    wt = _require_wt(shutil.which("wt"))

    private = {
        "HOME": tmp_path / "home",
        "XDG_CONFIG_HOME": tmp_path / "config",
        "XDG_STATE_HOME": tmp_path / "state",
        "XDG_DATA_HOME": tmp_path / "data",
        "XDG_CACHE_HOME": tmp_path / "cache",
    }
    for path in private.values():
        path.mkdir()
    # Host direnv state (DIRENV_DIFF reverts PATH inside `direnv exec`), host omc
    # overrides (OMC_HOME, OMC_*_BIN read by ToolContext.from_env), host Worktrunk
    # config overrides (any WORKTRUNK_* var, e.g. WORKTRUNK_WORKTREE_PATH, redirects
    # every worktree) and host git state (GIT_DIR/GIT_INDEX_FILE from a hook context)
    # must not leak in. Hermetic git config is re-set explicitly below.
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DIRENV_", "OMC_", "WORKTRUNK_", "GIT_"))
    }
    env = {**inherited, **{key: str(path) for key, path in private.items()}}
    env["GIT_CONFIG_GLOBAL"] = str(private["XDG_CONFIG_HOME"] / "gitconfig")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # Worktrunk abbreviates paths under HOME as `~/...` in its output while git lists
    # them absolutely; "under-home" places the repo where real projects live.
    under_home = getattr(request, "param", None) == "under-home"
    repo = (private["HOME"] if under_home else tmp_path) / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, text=True, capture_output=True, check=True
        ).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.name", "omc tests")
    git("config", "user.email", "tests@omc.invalid")
    git("config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text("SECRET=private\n")
    (repo / ".config").mkdir()
    (repo / ".config" / "wt.toml").write_text('pre-start = "exit 7"\n')
    git("add", ".gitignore", ".config/wt.toml")
    git("commit", "-qm", "seed failing hook")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, env, wt, git


@pytest.fixture(params=["symlink", "directory"])
def copy_hook_repo(failing_hook_repo, request):
    repo, env, wt, git = failing_hook_repo
    (repo / ".gitignore").write_text(".env\n.secrets/\n.gitnexus/\ntarget/\n.omc\nAGENTS.md\n")
    (repo / ".config" / "wt.toml").write_text(WT_TEMPLATE)
    (repo / ".secrets").mkdir()
    (repo / ".secrets" / "key").write_text("private-key\n")
    (repo / ".gitnexus").mkdir()
    (repo / ".gitnexus" / "meta.json").write_text("{}\n")
    (repo / "target" / "debug").mkdir(parents=True)
    (repo / "target" / "debug" / "app").write_text("cache\n")
    agent_target = repo.parent / "shared-agents.md"
    agent_target.write_text("# agents\n")
    (repo / "AGENTS.md").symlink_to(agent_target)
    omc_root = repo / ".omc"
    if request.param == "symlink":
        omc_target = repo.parent / "shared-omc"
        omc_root.symlink_to(omc_target, target_is_directory=True)
    else:
        omc_target = omc_root
    (omc_target / "docs").mkdir(parents=True)
    (omc_target / "docs" / "snapshot.md").write_text("# snapshot\n")
    git("add", ".gitignore", ".config/wt.toml")
    git("commit", "-qm", "seed blocking copy")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, env, wt, git, request.param, agent_target, omc_target


@pytest.fixture
def direnv_worktree(failing_hook_repo, tmp_path, monkeypatch, request):
    if shutil.which("direnv") is None:
        pytest.fail(
            "direnv is required. Install with `brew install direnv` "
            "or `sudo apt-get install direnv`."
        )
    repo, env, wt, git = failing_hook_repo
    (repo / ".config" / "wt.toml").write_text(WT_TEMPLATE)
    if getattr(request, "param", False):
        (repo / ".gitignore").write_text(".env\n.envrc\n")
        (repo / ".envrc").write_text("export OMC_TEST_MARK=from-ignored-envrc\n")
    git("add", ".gitignore", ".config/wt.toml")
    git("commit", "-qm", "seed direnv launch")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.chdir(repo)
    ctx = ToolContext.from_env(env)
    ctx.wt_bin = wt
    branch = "feature/direnv-launch"
    worktree = Path(create_worktree(ctx, branch, base="origin/main"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    provider = bindir / "claude"
    provider.write_text('#!/bin/sh\nprintf "%s\\n" "${OMC_TEST_MARK-unset}"\n')
    provider.chmod(0o755)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.pop("OMC_TEST_MARK", None)
    try:
        yield worktree, ToolContext.from_env(env)
    finally:
        git("worktree", "remove", "--force", str(worktree))
        git("branch", "-D", branch)


def test_real_direnv_exec_without_envrc_preserves_provider_output(direnv_worktree, capsys):
    worktree, ctx = direnv_worktree
    assert not any((directory / ".envrc").exists() for directory in (worktree, *worktree.parents))
    assert run_headless(ctx, Config(), "seed", str(worktree), "slug", direnv=True) == 0
    assert capsys.readouterr().out == "unset\n"


@pytest.mark.parametrize("direnv_worktree", [True], indirect=True)
def test_real_wt_allows_copied_ignored_envrc_before_provider(direnv_worktree, capsys):
    worktree, ctx = direnv_worktree
    assert (worktree / ".envrc").read_text() == "export OMC_TEST_MARK=from-ignored-envrc\n"
    assert ctx.run(["git", "check-ignore", ".envrc"], cwd=worktree).returncode == 0
    assert run_headless(ctx, Config(), "seed", str(worktree), "slug", direnv=True) == 0
    assert capsys.readouterr().out == "from-ignored-envrc\n"


def test_real_direnv_exec_loads_allowed_worktree_envrc(direnv_worktree, capsys):
    worktree, ctx = direnv_worktree
    (worktree / ".envrc").write_text("export OMC_TEST_MARK=from-worktree\n")
    allowed = ctx.run(["direnv", "allow", "."], cwd=worktree)
    assert allowed.returncode == 0, allowed.stderr
    assert run_headless(ctx, Config(), "seed", str(worktree), "slug", direnv=True) == 0
    assert capsys.readouterr().out == "from-worktree\n"


def test_real_direnv_exec_rejects_changed_unallowed_envrc(direnv_worktree, capsys):
    worktree, ctx = direnv_worktree
    envrc = worktree / ".envrc"
    envrc.write_text("export OMC_TEST_MARK=allowed\n")
    allowed = ctx.run(["direnv", "allow", "."], cwd=worktree)
    assert allowed.returncode == 0, allowed.stderr
    envrc.write_text("export OMC_TEST_MARK=changed-without-allow\n")
    assert run_headless(ctx, Config(), "seed", str(worktree), "slug", direnv=True) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "is blocked" in output.err


def test_real_direnv_exec_loads_allowed_parent_envrc(direnv_worktree, capsys):
    worktree, ctx = direnv_worktree
    assert not (worktree / ".envrc").exists()
    parent = worktree.parent
    (parent / ".envrc").write_text("export OMC_TEST_MARK=from-parent\n")
    allowed = ctx.run(["direnv", "allow", "."], cwd=parent)
    assert allowed.returncode == 0, allowed.stderr
    assert run_headless(ctx, Config(), "seed", str(worktree), "slug", direnv=True) == 0
    assert capsys.readouterr().out == "from-parent\n"


def test_real_wt_blocking_copy_preserves_ignored_files_and_clean_status(copy_hook_repo):
    repo, env, wt, git, omc_kind, agent_target, omc_target = copy_hook_repo
    assert not (repo / ".envrc").exists()
    assert git("status", "--porcelain") == ""
    branch = "feature/blocking-copy"
    completed = subprocess.run(
        [
            wt,
            "switch",
            "--create",
            branch,
            "--base",
            "origin/main",
            "--no-cd",
            "--yes",
            "--format=json",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    try:
        assert completed.returncode == 0, completed.stderr
        block = next(
            block
            for block in git("worktree", "list", "--porcelain").split("\n\n")
            if f"branch refs/heads/{branch}" in block.splitlines()
        )
        worktree = Path(block.splitlines()[0].removeprefix("worktree "))
        assert not (worktree / ".envrc").exists()
        assert (worktree / ".env").read_text() == "SECRET=private\n"
        assert (worktree / ".secrets" / "key").read_text() == "private-key\n"
        assert (worktree / "AGENTS.md").is_symlink()
        assert os.readlink(worktree / "AGENTS.md") == str(agent_target)
        assert not (worktree / ".gitnexus").exists()
        assert not (worktree / "target").exists()
        assert (worktree / ".omc").is_symlink() == (omc_kind == "symlink")
        if omc_kind == "symlink":
            assert os.readlink(worktree / ".omc") == str(omc_target)
        else:
            assert (worktree / ".omc").is_dir()
        assert (worktree / ".omc" / "docs" / "snapshot.md").read_text() == "# snapshot\n"
        assert git("status", "--porcelain") == ""
        assert git("-C", str(worktree), "status", "--porcelain") == ""
    finally:
        for block in git("worktree", "list", "--porcelain").split("\n\n"):
            if f"branch refs/heads/{branch}" in block.splitlines():
                worktree = block.splitlines()[0].removeprefix("worktree ")
                git("worktree", "remove", "--force", worktree)
        if (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=repo,
                env=env,
            ).returncode
            == 0
        ):
            git("branch", "-D", branch)


def test_real_wt_hook_failure_leaves_worktree_and_branch(failing_hook_repo):
    repo, env, wt, git = failing_hook_repo
    completed = subprocess.run(
        [
            wt,
            "switch",
            "--create",
            "feature/hook-failure",
            "--base",
            "origin/main",
            "--no-cd",
            "--yes",
            "--format=json",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    try:
        assert completed.returncode != 0
        assert re.search(HOOK_EXIT_7, completed.stderr), completed.stderr
        porcelain = git("worktree", "list", "--porcelain")
        assert "branch refs/heads/feature/hook-failure\n" in porcelain
        assert git("show-ref", "--verify", "refs/heads/feature/hook-failure")
        failed_block = next(
            block
            for block in porcelain.split("\n\n")
            if "branch refs/heads/feature/hook-failure" in block.splitlines()
        )
        failed_path = failed_block.splitlines()[0].removeprefix("worktree ")
        assert (
            git("-C", failed_path, "ls-files", "--cached", "--ignored", "--exclude-standard") == ""
        )
    finally:
        for block in git("worktree", "list", "--porcelain").split("\n\n"):
            if "branch refs/heads/feature/hook-failure" in block.splitlines():
                path = block.splitlines()[0].removeprefix("worktree ")
                git("worktree", "remove", "--force", path)
        if (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", "refs/heads/feature/hook-failure"],
                cwd=repo,
                env=env,
            ).returncode
            == 0
        ):
            git("branch", "-D", "feature/hook-failure")


@pytest.mark.parametrize("failing_hook_repo", ["outside-home", "under-home"], indirect=True)
def test_create_worktree_cleans_real_failed_cut(failing_hook_repo, monkeypatch):
    repo, env, wt, git = failing_hook_repo
    monkeypatch.chdir(repo)
    ctx = ToolContext.from_env(env)
    ctx.wt_bin = wt

    with pytest.raises(OmcError, match=HOOK_EXIT_7):
        create_worktree(ctx, "feature/hook-failure", base="origin/main")

    porcelain = git("worktree", "list", "--porcelain")
    assert "branch refs/heads/feature/hook-failure\n" not in porcelain
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/feature/hook-failure"],
            cwd=repo,
            env=env,
        ).returncode
        == 1
    )
    assert git("status", "--porcelain") == ""


def _fake_wt(tmp_path, version_line):
    wt = tmp_path / "wt"
    wt.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    wt.chmod(0o755)
    return str(wt)


@pytest.mark.parametrize("line", ["wt v0.78.1", "wt 0.9.0", "garbled"])
def test_too_old_or_unknown_worktrunk_fails_loud_naming_the_install(tmp_path, line):
    # Below the minimum (or unparseable) must fail with the install command, never as a
    # confusing assertion mismatch. 0.9.0 < 0.79.0: compared numerically, not as text.
    with pytest.raises(pytest.fail.Exception) as failure:
        _require_wt(_fake_wt(tmp_path, line))
    assert f"found `{line}`" in str(failure.value)
    assert "releases/download/v0.79.0" in str(failure.value)


@pytest.mark.parametrize("line", ["wt 0.79.0", "wt v0.79.0", "wt 0.82.0"])
def test_minimum_or_newer_worktrunk_is_accepted(tmp_path, line):
    wt = _fake_wt(tmp_path, line)
    assert _require_wt(wt) == wt


@pytest.mark.parametrize(
    "stderr",
    [
        "pre-start command failed: exit status: 7",  # v0.79
        "✗ pre-start project hook failed (exit code 7)\n",  # v0.82
    ],
)
def test_hook_exit_7_matches_each_supported_worktrunk(stderr):
    assert re.search(HOOK_EXIT_7, stderr)


@pytest.mark.parametrize(
    "stderr",
    ["pre-start command failed: exit status: 70", "pre-start project hook failed (exit code 8)"],
)
def test_hook_exit_7_rejects_other_exit_codes(stderr):
    assert not re.search(HOOK_EXIT_7, stderr)
