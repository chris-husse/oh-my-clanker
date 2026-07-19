import os
import re
import stat
import subprocess
from pathlib import Path

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


def _push_remote_edit(origin, tmp_path):
    """Advance origin/main with an edit to f.txt (conflicts with local f.txt changes)."""
    other = tmp_path / "other-edit"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / "f.txt").write_text("remote edit\n")
    _git("add", ".", cwd=other)
    _git("commit", "-qm", "remote edit", cwd=other)
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


def _run_once(repo, ctx, *, enable_documentation=False, rebase=False):
    old = os.getcwd()
    os.chdir(repo)
    try:
        return run_watch(
            ctx,
            Config(),
            interval=1,
            once=True,
            enable_documentation=enable_documentation,
            rebase=rebase,
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


def _tick_rebase(ctx, repo, last=None):
    from omc.watch import _tick

    return _tick(
        ctx,
        Config(),
        str(repo),
        enable_documentation=False,
        force_refresh=False,
        last=last,
        rebase=True,
    )


def test_tick_rebase_syncs_dirty_tree(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)  # remote adds new.txt — no overlap with f.txt
    (repo / "f.txt").write_text("uncommitted edit\n")
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "synced"
    assert "rebased main" in capsys.readouterr().err
    assert (repo / "new.txt").exists()  # sync actually happened
    assert (repo / "f.txt").read_text() == "uncommitted edit\n"  # autostash restored the dirt
    assert "analyze" in calls.read_text()  # action tick -> index refresh


def test_tick_rebase_replays_local_commits(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    (repo / "local.txt").write_text("local\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "local work", cwd=repo)  # diverged: ahead 1, behind 1
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "synced"
    assert (repo / "new.txt").exists() and (repo / "local.txt").exists()
    subjects = subprocess.run(
        ["git", "log", "--format=%s", "-2"], cwd=repo, capture_output=True, text=True
    ).stdout.splitlines()
    assert subjects == ["local work", "remote change"]  # replayed ON TOP of origin/main


def test_tick_rebase_conflict_aborts_and_restores(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_edit(origin, tmp_path)  # remote edits f.txt
    (repo / "f.txt").write_text("conflicting local\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "local conflicting", cwd=repo)
    (repo / "g.txt").write_text("dirt\n")
    _git("add", "g.txt", cwd=repo)  # tracked dirt on top — must survive the abort
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert _tick_rebase(ctx, repo) == "rebase-failed"
    assert "aborted, checkout restored" in capsys.readouterr().err
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert head_after == head_before  # abort restored HEAD
    assert (repo / "g.txt").read_text() == "dirt\n"  # and the autostashed dirt
    assert not calls.exists()  # no index refresh on failure


def test_tick_rebase_autostash_conflict_warns_and_skips_refresh(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_edit(origin, tmp_path)  # remote edits f.txt
    (repo / "f.txt").write_text("dirty conflicting edit\n")  # UNCOMMITTED same-file edit
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "autostash-conflict"
    assert "safe in git stash" in capsys.readouterr().err
    unmerged = subprocess.run(
        ["git", "ls-files", "-u"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert unmerged  # tree left with conflict markers to resolve
    stashes = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "autostash" in stashes  # changes parked in the stash too
    assert not calls.exists()  # NOT an action tick — no index refresh


def _push_remote_file(origin, tmp_path, name, content):
    """Advance origin/main by ADDING a new file `name` (a teammate's push)."""
    other = tmp_path / f"other-{name}"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / name).write_text(content)
    _git("add", ".", cwd=other)
    _git("commit", "-qm", f"remote adds {name}", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)


def test_tick_rebase_pre_start_refusal_quiets_and_leaves_untracked_file(tmp_path, capsys):
    """Canonical pre-start refusal: remote adds a file that already exists
    UNTRACKED locally. `git rebase --autostash` refuses before starting (exit
    1, HEAD unmoved, no rebase in progress) so no abort is attempted; the tick
    quiets to `rebase-failed` and the untracked content is untouched. A second
    tick with last='rebase-failed' must narrate NOTHING (quiet-token)."""
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_file(origin, tmp_path, "collide.txt", "from remote\n")
    (repo / "collide.txt").write_text("my untracked work\n")  # untracked, would be clobbered
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert _tick_rebase(ctx, repo) == "rebase-failed"
    err = capsys.readouterr().err
    assert "refused before starting" in err  # not the misleading "aborted, checkout restored"
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert head_after == head_before  # HEAD never moved
    assert (repo / "collide.txt").read_text() == "my untracked work\n"  # untracked work untouched
    assert not calls.exists()  # not an action tick — no analyze call
    # Steady state: a second tick with the same token narrates NOTHING.
    assert _tick_rebase(ctx, repo, last="rebase-failed") == "rebase-failed"
    assert capsys.readouterr().err == ""


def test_tick_rebase_conflicted_tree_skips_quietly(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_edit(origin, tmp_path)
    (repo / "f.txt").write_text("dirty conflicting edit\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "autostash-conflict"
    _push_remote_commit(origin, tmp_path)  # behind again, tree still conflicted
    capsys.readouterr()
    assert _tick_rebase(ctx, repo, last="autostash-conflict") == "conflicted"
    assert "unmerged paths" in capsys.readouterr().err  # token changed -> narrates once
    assert _tick_rebase(ctx, repo, last="conflicted") == "conflicted"
    assert capsys.readouterr().err == ""  # same token -> silent (quiet convention)


def test_tick_rebase_clean_tree_still_syncs(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _tick_rebase(ctx, repo) == "synced"
    assert "rebased main" in capsys.readouterr().err
    assert (repo / "new.txt").exists()  # rebase fast-forwards a clean checkout


def test_watch_rebase_flag_threads_through(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    (repo / "f.txt").write_text("uncommitted edit\n")
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx, rebase=True) == 0
    assert "rebased main" in capsys.readouterr().err
    assert (repo / "new.txt").exists()
    assert (repo / "f.txt").read_text() == "uncommitted edit\n"


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


def test_watch_repairs_a_dangling_chain(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    from omc.agentsmd import chain_healthy, ensure_agents_chain

    ensure_agents_chain(ctx, repo)
    (repo / "AGENTS.md").unlink()  # simulate a broken link
    assert not chain_healthy(repo)
    assert _run_once(repo, ctx) == 0
    assert chain_healthy(repo)  # tick repaired it
    assert "AGENTS.md" in capsys.readouterr().err  # repair narrates


def test_watch_blocked_chain_warns_once_and_never_stops_the_loop(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    (repo / "AGENTS.md").write_text("# handwritten\n")
    from omc.watch import _chain_tick

    first = _chain_tick(ctx, str(repo), None)
    capsys.readouterr()
    second = _chain_tick(ctx, str(repo), first)
    assert first == second == "chain-blocked"
    assert capsys.readouterr().err == ""  # quiet-token: repeat state is silent
    assert (repo / "AGENTS.md").read_text() == "# handwritten\n"


def test_watch_blocked_chain_with_foreign_symlink_warns_once_and_never_stops_the_loop(
    tmp_path, capsys
):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    (repo / "other.md").write_text("x")
    (repo / "AGENTS.md").symlink_to("other.md")
    from omc.watch import _chain_tick

    first = _chain_tick(ctx, str(repo), None)
    capsys.readouterr()
    second = _chain_tick(ctx, str(repo), first)
    assert first == second == "chain-blocked"
    assert capsys.readouterr().err == ""  # quiet-token: repeat state is silent
    assert (repo / "AGENTS.md").resolve() == (repo / "other.md").resolve()


def test_watch_leaves_never_managed_repos_alone(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    from omc.watch import _chain_tick

    assert _chain_tick(ctx, str(repo), None) == "chain-absent"
    assert not (repo / "AGENTS.md").exists()  # watch never creates from nothing
    assert capsys.readouterr().err == ""


def test_watch_chain_tick_survives_a_broken_install(tmp_path, capsys, monkeypatch):
    """chain_healthy() raises OmcError when the installed distribution/AGENTS.md
    is missing (broken install). A tick failure must warn and skip, never
    crash the loop."""
    origin, repo = _repo_with_origin(tmp_path)
    home = tmp_path / "omc-home"
    ctx, _ = _ctx_with_node_stub(tmp_path, home)
    from omc.errors import OmcError

    def _boom(_root):
        raise OmcError("broken install: distribution/AGENTS.md is missing")

    monkeypatch.setattr("omc.watch.chain_healthy", _boom)
    from omc.watch import _chain_tick

    first = _chain_tick(ctx, str(repo), None)
    assert first == "chain-error"
    assert "chain check failed" in capsys.readouterr().err
    second = _chain_tick(ctx, str(repo), first)
    assert second == "chain-error"
    assert capsys.readouterr().err == ""  # quiet-token: repeat state is silent


def _seed_hook(repo, body):
    hooks = repo / ".omc" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "post-watch.sh").write_text(body)


def test_once_runs_post_watch_hook_after_forced_refresh(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, 'echo "$OMC_WATCH_OUTCOME" > hook-ran.txt\n')
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "→ running project post-watch hook (.omc/hooks/post-watch.sh)" in err
    assert "✓ post-watch hook done" in err
    # cwd was the repo root and OMC_WATCH_OUTCOME carried the token
    assert (repo / "hook-ran.txt").read_text().strip() == "refreshed"


def test_hook_sees_synced_outcome(tmp_path, capsys):
    origin, repo = _repo_with_origin(tmp_path)
    _push_remote_commit(origin, tmp_path)
    _seed_hook(repo, 'echo "$OMC_WATCH_OUTCOME" > hook-ran.txt\n')
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    assert (repo / "hook-ran.txt").read_text().strip() == "synced"


def test_quiet_loop_tick_does_not_run_hook(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "touch hook-ran.txt\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_loop(repo, ctx, ticks=2) == 0  # loop mode: both ticks are quiet up-to-date
    assert not (repo / "hook-ran.txt").exists()
    assert "post-watch" not in capsys.readouterr().err


def test_absent_hook_is_silent(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0
    assert "post-watch" not in capsys.readouterr().err


def test_hook_failure_links_log_and_keeps_once_rc_zero(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "echo boom-out\necho boom-err >&2\nexit 3\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0  # hook failure never changes --once's exit code
    err = capsys.readouterr().err
    m = re.search(r"✗ post-watch hook failed \(exit 3\) — log: (\S+)", err)
    assert m, f"missing failure narration:\n{err}"
    log = Path(m.group(1))
    assert log.is_file()
    content = log.read_text()
    assert "boom-out" in content and "boom-err" in content  # both streams captured


def test_hook_timeout_is_a_failure_and_loop_survives(tmp_path, capsys, monkeypatch):
    import omc.watch as watch_mod

    monkeypatch.setattr(watch_mod, "_HOOK_TIMEOUT", 1)
    origin, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "echo partial-out\necho partial-err >&2\nsleep 5\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")

    def between(i):
        if i == 1:  # teammate pushes between tick 1 and 2 -> tick 2 syncs -> hook fires
            _push_remote_commit(origin, tmp_path)

    assert _run_loop(repo, ctx, ticks=3, between=between) == 0
    err = capsys.readouterr().err
    m = re.search(r"✗ post-watch hook failed \(timeout\) — log: (\S+)", err)
    assert m, f"missing timeout narration with log link:\n{err}"
    content = Path(m.group(1)).read_text()
    # partial output decoded into the log
    assert "partial-out" in content and "partial-err" in content
    # tick 3 still ran after the hook blew up: quiet line reappears post-sync
    assert err.count("up to date") == 2, f"loop did not survive the timeout:\n{err}"


def test_hook_binary_output_never_crashes_the_loop(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_hook(repo, "printf '\\xff\\xfe'\n")
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    assert _run_once(repo, ctx) == 0  # undecodable output must never crash the loop
    err = capsys.readouterr().err
    assert "✗ post-watch hook failed (undecodable output) — log:" in err


def _stub_claude(tmp_path, transcript, rc=0):
    """A fake `claude` on the stub PATH: records argv, prints transcript."""
    bindir = tmp_path / "bin"  # same dir _ctx_with_node_stub already put on PATH
    calls = bindir / "claude.calls"
    stub = bindir / "claude"
    stub.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\ncat <<\'TRANSCRIPT\'\n'
        f"{transcript}\nTRANSCRIPT\nexit {rc}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return calls


def _seed_build_stage(repo):
    d = repo / ".omc" / "skills" / "build"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# build\nrun make\n")


def _run_once_auto_build(repo, ctx):
    old = os.getcwd()
    os.chdir(repo)
    try:
        return run_watch(ctx, Config(), interval=1, once=True, auto_build=True)
    finally:
        os.chdir(old)


def test_auto_build_passes_on_stage_verdict(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    calls = _stub_claude(
        tmp_path,
        'building...\nOMC_STAGE {"stage": "build", "configured": true, "passed": true, '
        '"summary": "ok"}',
    )
    assert _run_once_auto_build(repo, ctx) == 0
    err = capsys.readouterr().err
    assert "→ running project build stage via claude (LLM-heavy)" in err
    assert "✓ auto-build passed" in err
    recorded = calls.read_text()
    assert "-p" in recorded  # headless print-mode invocation


def test_auto_build_failure_links_log_and_keeps_rc_zero(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _stub_claude(
        tmp_path,
        'OMC_STAGE {"stage": "build", "configured": true, "passed": false, '
        '"summary": "make exploded"}',
    )
    assert _run_once_auto_build(repo, ctx) == 0  # failures never change --once's exit code
    err = capsys.readouterr().err
    m = re.search(r"✗ auto-build failed \(make exploded\) — log: (\S+)", err)
    assert m, f"missing failure narration:\n{err}"
    assert "OMC_STAGE" in Path(m.group(1)).read_text()  # full transcript logged


def test_auto_build_no_verdict_is_a_failure(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    _stub_claude(tmp_path, "rambling with no verdict line")
    assert _run_once_auto_build(repo, ctx) == 0
    assert "✗ auto-build failed (no verdict)" in capsys.readouterr().err


def test_auto_build_unconfigured_skips_llm_entirely(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)  # no .omc/skills/build
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    calls = _stub_claude(tmp_path, "should never run")
    assert _run_once_auto_build(repo, ctx) == 0
    assert "· no project build stage configured — skipping auto-build" in capsys.readouterr().err
    assert not calls.exists()  # the provider binary was NEVER invoked


def test_no_auto_build_flag_means_no_build(tmp_path, capsys):
    _, repo = _repo_with_origin(tmp_path)
    _seed_build_stage(repo)
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    calls = _stub_claude(tmp_path, "should never run")
    assert _run_once(repo, ctx) == 0  # plain --once, no auto_build
    assert "auto-build" not in capsys.readouterr().err
    assert not calls.exists()
