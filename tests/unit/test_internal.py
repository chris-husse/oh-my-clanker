import json
import os
import stat
import subprocess

from omc.internal import run_internal


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _setup_primary_with_origin(tmp_path):
    """A real primary clone with a bare origin holding main."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    # bare HEAD -> main so later clones check out main (init.defaultBranch-proof)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    primary = tmp_path / "primary"
    subprocess.run(["git", "clone", "-q", str(origin), str(primary)], check=True)
    _git("config", "user.email", "t@t", cwd=primary)
    _git("config", "user.name", "t", cwd=primary)
    (primary / "f.txt").write_text("one\n")
    _git("add", ".", cwd=primary)
    _git("commit", "-qm", "c1", cwd=primary)
    _git("branch", "-M", "main", cwd=primary)  # independent of init.defaultBranch
    _git("push", "-q", "-u", "origin", "main", cwd=primary)
    return origin, primary


def _add_worktree(primary, tmp_path, branch="feature/x"):
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", "-b", branch, str(wt), cwd=primary)
    _git("config", "user.email", "t@t", cwd=wt)
    _git("config", "user.name", "t", cwd=wt)
    return wt


def _advance_main(primary, content="two\n"):
    (primary / "f2.txt").write_text(content)
    _git("add", ".", cwd=primary)
    _git("commit", "-qm", "advance", cwd=primary)
    _git("push", "-q", "origin", "main", cwd=primary)


def _run(args, cwd, tmp_path, capsys):
    old = os.getcwd()
    os.chdir(cwd)
    try:
        env_home = tmp_path / "omchome"
        os.environ["OMC_HOME"] = str(env_home)
        rc = run_internal(args)
    finally:
        os.chdir(old)
        os.environ.pop("OMC_HOME", None)
    out = capsys.readouterr().out
    verdict_line = next((ln for ln in out.splitlines() if ln.startswith("OMC_REBASE_MAIN ")), None)
    verdict = json.loads(verdict_line.split(" ", 1)[1]) if verdict_line else None
    return rc, verdict, out


def test_wt_template_prints_template(capsys):
    rc = run_internal(["wt-template"])
    assert rc == 0
    assert "copy-ignored" in capsys.readouterr().out


def test_unknown_internal_subcommand(capsys):
    assert run_internal(["nope"]) == 2


def test_rebase_main_in_primary_is_noop(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    rc, verdict, _ = _run(["rebase-main", "--base", "main"], primary, tmp_path, capsys)
    assert rc == 0
    assert verdict["ok"] is True and "primary" in verdict["note"]


def test_rebase_main_rebases_and_mirrors_snapshot(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    (wt / "mine.txt").write_text("work\n")
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", "my work", cwd=wt)
    _advance_main(primary)
    # primary carries a fresher snapshot than the worktree copy
    (primary / ".gitnexus").mkdir()
    (primary / ".gitnexus" / "graph.db").write_text("fresh")
    (wt / ".gitnexus").mkdir()
    (wt / ".gitnexus" / "graph.db").write_text("stale")
    (wt / ".gitnexus" / "extraneous").write_text("x")

    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 0 and verdict["ok"] is True
    assert ".gitnexus" in verdict["synced"]
    assert verdict["rebased"]  # old..new range recorded
    assert (wt / "f2.txt").exists()  # main's commit arrived under our work
    assert (wt / "mine.txt").exists()
    assert (wt / ".gitnexus" / "graph.db").read_text() == "fresh"
    assert not (wt / ".gitnexus" / "extraneous").exists()


def test_rebase_main_conflict_bails_rc3_and_leaves_rebase_paused(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    (wt / "f.txt").write_text("worktree version\n")
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", "conflicting", cwd=wt)
    (primary / "f.txt").write_text("main version\n")
    _git("add", ".", cwd=primary)
    _git("commit", "-qm", "main change", cwd=primary)
    _git("push", "-q", "origin", "main", cwd=primary)

    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 3
    assert verdict["ok"] is False and "f.txt" in verdict["conflicts"]
    cp = subprocess.run(["git", "status"], cwd=wt, capture_output=True, text=True)
    assert "rebase" in cp.stdout.lower()  # paused, not aborted


def test_notify_usage_errors(capsys):
    assert run_internal(["notify"]) == 2  # --provider is required
    assert run_internal(["notify", "--provider", "cursor"]) == 2  # unknown provider
    assert "usage:" in capsys.readouterr().err


def test_notify_dispatches(tmp_path, monkeypatch):
    # the RED test: before the notify branch exists this hits the usage
    # fallthrough (exit 2); afterwards run_notify returns 0 (no config ->
    # silent no-op, stdin never read)
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    assert run_internal(["notify", "--provider", "claude"]) == 0


def _chdir(path):
    old = os.getcwd()
    os.chdir(path)
    return old


def _gitnexus_env(tmp_path):
    """Real git repo + linked worktree + recording node stub + fake CLI + config."""
    from omc.config import store
    from omc.config.schema import Config

    repo = tmp_path / "primary"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "feat"], check=True
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\npwd >> "{calls}"\nexit 0\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "omc-home"
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake")
    store.save(home, Config())  # base_branch defaults to "main"
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return repo, wt, calls, env


def test_gitnexus_proxy_injects_scoping_and_runs_from_primary(tmp_path, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(wt)  # invoked from a WORKTREE
    try:
        rc = run_internal(["gitnexus", "query", "how does start work"])
    finally:
        os.chdir(old)
    assert rc == 0
    logged = calls.read_text()
    assert "query how does start work" in logged
    assert f"--repo {repo}" in logged  # absolute PATH of the primary root
    assert "--branch main" in logged  # configured base branch
    assert logged.splitlines()[-1] == str(repo)  # pwd line: ran FROM the primary root


def test_gitnexus_proxy_rejects_unknown_subcommands(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        assert run_internal(["gitnexus", "analyze"]) == 2  # not a query verb
        assert run_internal(["gitnexus"]) == 2
    finally:
        os.chdir(old)


def test_gitnexus_proxy_errors_helpfully_without_the_cli(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    (tmp_path / "omc-home" / "dependencies").rename(tmp_path / "gone")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "query", "x"])
    finally:
        os.chdir(old)
    assert rc == 1
    assert "/omc:index" in capsys.readouterr().err  # install hint


def test_internal_build_progress_usage_and_dispatch(tmp_path, capsys):
    from omc.internal import run_internal

    assert run_internal(["build-progress"]) == 2  # missing logfile -> usage
    log = tmp_path / "done.log"
    log.write_text("--- omc: stage finished (rc 0) ---\n")
    assert run_internal(["build-progress", str(log)]) == 0
    out = capsys.readouterr().out
    assert out == ""  # internal stdout stays machine-clean; bar goes to stderr
