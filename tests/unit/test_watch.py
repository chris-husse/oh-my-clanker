import os
import stat
import subprocess

from omc.config.schema import Config
from omc.toolctx import ToolContext
from omc.watch import run_watch


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    # bare HEAD -> main so later clones check out main (init.defaultBranch-proof)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("one\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)  # independent of init.defaultBranch
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    return origin, repo


def _push_remote_commit(origin, tmp_path):
    """Advance origin/main from a second clone (simulates a teammate's push)."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / "new.txt").write_text("new\n")
    _git("add", ".", cwd=other)
    _git("commit", "-qm", "remote change", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)


def _ctx_with_node_stub(tmp_path, home):
    """Real git on PATH + a recording `node` stub + a fake built gitnexus CLI."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\necho ok\nexit 0\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake built CLI")
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return ToolContext.from_env(env), calls


def _run_once(repo, ctx, *, enable_documentation=False):
    old = os.getcwd()
    os.chdir(repo)
    try:
        return run_watch(
            ctx, Config(), interval=1, once=True, enable_documentation=enable_documentation
        )
    finally:
        os.chdir(old)


def test_loop_tick_up_to_date_does_not_reindex(tmp_path, capsys):
    from omc.watch import _tick

    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _tick(ctx, Config(), str(repo), enable_documentation=False, force_refresh=False, last=None)
    assert "up to date" in capsys.readouterr().err
    assert not calls.exists()  # loop mode: nothing new -> no reindex


def test_once_refreshes_index_even_when_up_to_date(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "up to date" in err
    assert "analyze --skip-agents-md --skip-skills" in calls.read_text()  # --once = refresh NOW


def test_once_with_documentation_refreshes_docs_even_when_up_to_date(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx, enable_documentation=True) == 0
    recorded = calls.read_text()
    assert "analyze" in recorded and "wiki --provider claude" in recorded


def test_tick_syncs_and_reindexes(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "synced main" in err
    assert (repo / "new.txt").exists()  # ff-merge actually happened
    recorded = calls.read_text()
    assert "analyze --skip-agents-md --skip-skills" in recorded
    assert "wiki" not in recorded  # documentation is opt-in


def test_tick_documentation_gated_by_flag(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx, enable_documentation=True) == 0
    recorded = calls.read_text()
    assert "analyze" in recorded
    assert "wiki --provider claude" in recorded


def test_tick_refuses_off_branch(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _git("switch", "-qc", "feature/other", cwd=repo)
    _push_remote_commit(origin, tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "not on main" in err
    assert not calls.exists()
    assert not (repo / "new.txt").exists()  # never yanked the checkout


def test_tick_refuses_dirty_tree(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    (repo / "f.txt").write_text("uncommitted edit\n")
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    assert "dirty" in capsys.readouterr().err
    assert not calls.exists()


def test_watch_requires_gitnexus_cli(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    env = {"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "empty"), "PATH": os.environ["PATH"]}
    ctx = ToolContext.from_env(env)
    old = os.getcwd()
    os.chdir(repo)
    try:
        rc = run_watch(ctx, Config(), interval=1, once=True, enable_documentation=False)
    finally:
        os.chdir(old)
    assert rc == 1
    assert "/omc:index" in capsys.readouterr().err  # points at the installer path


def _run_loop(repo, ctx, ticks, between=None):
    """Run the real loop, faking sleep: `between(i)` runs after tick i; stop after `ticks`."""
    import omc.watch as watch_mod

    count = {"n": 0}

    def fake_sleep(_seconds):
        count["n"] += 1
        if between:
            between(count["n"])
        if count["n"] >= ticks:
            raise KeyboardInterrupt

    old_sleep, watch_mod.time.sleep = watch_mod.time.sleep, fake_sleep
    old_cwd = os.getcwd()
    os.chdir(repo)
    try:
        return run_watch(ctx, Config(), interval=1, once=False, enable_documentation=False)
    finally:
        os.chdir(old_cwd)
        watch_mod.time.sleep = old_sleep


def test_loop_says_up_to_date_once_then_waits_quietly(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    rc = _run_loop(repo, ctx, ticks=3)
    assert rc == 0  # Ctrl-C is a clean stop, not a crash
    err = capsys.readouterr().err
    assert err.count("up to date") == 1, f"quiet ticks must not repeat:\n{err}"
    assert "waiting for changes on origin/main" in err.lower()


def test_quiet_line_reappears_after_a_sync(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")

    def between(i):
        if i == 1:  # a teammate pushes between tick 1 and tick 2
            _push_remote_commit(origin, tmp_path)

    rc = _run_loop(repo, ctx, ticks=3, between=between)  # quiet, synced, quiet-again
    assert rc == 0
    err = capsys.readouterr().err
    assert "synced main" in err
    assert err.count("up to date") == 2, f"suppression must reset after a sync:\n{err}"
