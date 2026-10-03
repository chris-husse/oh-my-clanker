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
    assert verdict["synced"] == [] and verdict["shared"] == []


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
    assert verdict["shared"] == []  # stable schema: key present even when empty
    assert verdict["rebased"]  # old..new range recorded
    assert (wt / "f2.txt").exists()  # main's commit arrived under our work
    assert (wt / "mine.txt").exists()
    assert (wt / ".gitnexus" / "graph.db").read_text() == "fresh"
    assert not (wt / ".gitnexus" / "extraneous").exists()


def test_rebase_main_reports_shared_omc_docs(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    shared = tmp_path / "shared-omc"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "page.md").write_text("docs")
    os.symlink(shared, primary / ".omc")
    os.symlink(shared, wt / ".omc")

    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)

    assert rc == 0 and verdict["ok"] is True
    assert verdict["shared"] == [".omc/docs"]
    assert verdict["synced"] == []  # no .gitnexus in this fixture; nothing else synced
    assert (shared / "docs" / "page.md").read_text() == "docs"  # nothing destroyed


def test_rebase_main_never_runs_gitnexus_index(tmp_path, capsys):
    """Indexing is omc watch's EXCLUSIVE feature. rebase-main used to register
    the copied snapshot (`gitnexus index` in the worktree), which minted a
    same-named registry entry frozen at cut time per worktree — never
    unregistered, and stale-entry lookups then reported "N commits behind"
    while the primary index was current. Even with a healthy built CLI
    present, rebase-main must not invoke gitnexus at all."""
    _, primary = _setup_primary_with_origin(tmp_path)
    wt = _add_worktree(primary, tmp_path)
    _advance_main(primary)
    (primary / ".gitnexus").mkdir()
    (primary / ".gitnexus" / "graph.db").write_text("fresh")
    # Derive the CLI path from the real locator so this trap stays armed even
    # if the managed-install layout ever moves.
    from omc.gitnexus import gitnexus_cli
    from omc.toolctx import ToolContext

    cli = gitnexus_cli(ToolContext.from_env({"OMC_HOME": str(tmp_path / "omchome")}))
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake built CLI")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\necho ok\nexit 0\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{bindir}:{old_path}"
    try:
        rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)
    finally:
        os.environ["PATH"] = old_path

    assert rc == 0 and verdict["ok"] is True
    assert ".gitnexus" in verdict["synced"]  # mirroring stays
    assert not calls.exists()  # registration goes: node never invoked


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
    assert "knowledge" in verdict
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
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return repo, wt, calls, env


def test_gitnexus_ensure_calls_ensure_gitnexus(monkeypatch, tmp_path):
    import omc.internal as internal

    seen = []
    monkeypatch.setattr(internal, "ensure_gitnexus", lambda ctx: seen.append(True) or 0)
    # ensure runs with no repo and no CLI present — must not hit the
    # CLI-present guard or primary-root resolution.
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert internal.run_internal(["gitnexus", "ensure"]) == 0
    assert seen == [True]


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
    assert "omc update" in capsys.readouterr().err  # install hint


def test_internal_build_progress_usage_and_dispatch(tmp_path, capsys):
    from omc.internal import run_internal

    assert run_internal(["build-progress"]) == 2  # missing logfile -> usage
    log = tmp_path / "done.log"
    log.write_text("--- omc: stage finished (rc 0) ---\n")
    assert run_internal(["build-progress", str(log)]) == 0
    out = capsys.readouterr().out
    assert out == ""  # internal stdout stays machine-clean; bar goes to stderr


def test_dependency_usage_errors(capsys):
    assert run_internal(["dependency"]) == 2
    assert run_internal(["dependency", "nope"]) == 2
    assert run_internal(["dependency", "ensure"]) == 2  # --git is required
    assert run_internal(["dependency", "document"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_dependency_list_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    assert run_internal(["dependency", "list"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": 1, "dependencies": {}}


def _seed_dep(home, commit="c" * 40):
    """Manifest entry + checkout dir as `dependency ensure` leaves them."""
    from omc.dependency import load_manifest, save_manifest

    dest = home / "dependencies" / "github.com" / "foo" / "bar" / commit
    (dest / ".git").mkdir(parents=True)
    m = load_manifest(home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            commit: {
                "checkout": str(dest),
                "docs": str(home / "gitnexus" / "github.com" / "foo" / "bar" / commit / "docs"),
                "indexed": True,
                "documented": False,
                "created": "2026-07-22T00:00:00+00:00",
            }
        },
    }
    save_manifest(home, m)
    return dest


def test_gitnexus_proxy_git_scopes_to_dependency(tmp_path, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    dest = _seed_dep(tmp_path / "omc-home")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(wt)
    try:
        rc = run_internal(["gitnexus", "--git", "github.com/foo/bar", "query", "how"])
    finally:
        os.chdir(old)
    assert rc == 0
    logged = calls.read_text()
    assert f"--repo {dest}" in logged
    assert "--branch omc-pin" in logged
    assert "--branch main" not in logged  # project scoping must NOT leak in
    assert logged.splitlines()[-1] == str(dest)  # ran FROM the checkout


def test_gitnexus_proxy_git_unknown_ref_hints_ensure(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "--git", "github.com/no/pe", "query", "x"])
    finally:
        os.chdir(old)
    assert rc == 1
    assert "omc internal dependency ensure --git" in capsys.readouterr().err
    assert not calls.exists() or calls.read_text() == ""  # nothing was run


def test_gitnexus_proxy_git_empty_checkout_hints_ensure(tmp_path, capsys, monkeypatch):
    # A corrupted entry (indexed:true, checkout:"") must be treated as not-indexed
    # BEFORE building a Path — Path("")/".git" == "./.git" would spuriously pass
    # the guard whenever cwd is a git repo and answer against the WRONG repo.
    from omc.dependency import load_manifest, save_manifest

    repo, wt, calls, env = _gitnexus_env(tmp_path)
    home = tmp_path / "omc-home"
    m = load_manifest(home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            "c" * 40: {
                "checkout": "",
                "docs": "",
                "indexed": True,
                "documented": False,
                "created": "2026-07-22T00:00:00+00:00",
            }
        },
    }
    save_manifest(home, m)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)  # cwd IS a real git repo
    try:
        rc = run_internal(["gitnexus", "--git", "github.com/foo/bar", "query", "x"])
    finally:
        os.chdir(old)
    assert rc == 1
    assert "omc internal dependency ensure --git" in capsys.readouterr().err
    assert not calls.exists() or calls.read_text() == ""  # the CLI was never invoked


def test_gitnexus_proxy_git_still_rejects_bad_verbs(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    _seed_dep(tmp_path / "omc-home")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        assert run_internal(["gitnexus", "--git", "github.com/foo/bar", "analyze"]) == 2
        assert run_internal(["gitnexus", "--git"]) == 2
    finally:
        os.chdir(old)


def _seed_fresh_index(repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    (repo / ".gitnexus").mkdir(exist_ok=True)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": head, "repoPath": str(repo)})
    )


def _knowledge_line(out):
    line = next(ln for ln in out.splitlines() if ln.startswith("OMC_KNOWLEDGE "))
    return json.loads(line.split(" ", 1)[1])


_HEALING_NODE = (
    "#!/bin/sh\n"
    'echo "$@" >> "{calls}"\n'
    'case "$*" in\n'
    '  *" clean --force") rm -rf .gitnexus ;;\n'
    '  *" analyze --skip-agents-md --skip-skills") mkdir -p .gitnexus; '
    'printf \'{{"branch":"main","lastCommit":"%s","repoPath":"%s"}}\' '
    '"$(/usr/bin/git rev-parse HEAD)" "$PWD" > .gitnexus/meta.json ;;\n'
    "esac\n"
    "echo ok\nexit 0\n"  # unconditional ok: ensure_gitnexus's --version probe must pass
)


def _gitnexus_env_with_config(tmp_path, monkeypatch, *, node_body=_HEALING_NODE):
    """_gitnexus_env + a GLOBAL config (load_effective needs GlobalConfig, never
    Config — _hydrate rejects the `worktree` key) + a forwarding git stub that
    LOGS argv then execs the real git (the verdict needs real git)."""
    from omc.config import store
    from omc.config.schema import GlobalConfig

    repo, wt, calls, env = _gitnexus_env(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
    home = tmp_path / "omc-home"
    store.save_global(home, GlobalConfig())
    node = tmp_path / "bin" / "node"
    node.write_text(node_body.format(calls=calls))
    gitlog = tmp_path / "bin" / "git.calls"
    git = tmp_path / "bin" / "git"
    git.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{gitlog}"\nexec /usr/bin/git "$@"\n')
    git.chmod(git.stat().st_mode | stat.S_IXUSR)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return repo, wt, calls, gitlog


def test_status_prints_verdict_without_node(tmp_path, capsys, monkeypatch):
    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch)
    old = _chdir(wt)
    try:
        rc = run_internal(["gitnexus", "status"])
    finally:
        os.chdir(old)
    assert rc == 0
    v = _knowledge_line(capsys.readouterr().out)
    assert v["fresh"] is False and v["reasons"][0]["code"] == "index-missing"
    assert v["run_in"] == str(repo.resolve())
    assert v["basis"] == "unresolved"  # no origin in this fixture
    assert not calls.exists()


def test_refresh_heals_and_exits_zero(tmp_path, capsys, monkeypatch):
    repo, wt, calls, gitlog = _gitnexus_env_with_config(tmp_path, monkeypatch)
    old = _chdir(wt)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    out = capsys.readouterr().out
    assert rc == 0
    v = _knowledge_line(out)
    assert v["fresh"] is True and v["basis"] == "HEAD"
    assert out.strip().splitlines()[-1].startswith("OMC_KNOWLEDGE ")
    assert not any(line.startswith("fetch ") for line in gitlog.read_text().splitlines())


def test_refresh_still_stale_bails_rc3(tmp_path, capsys, monkeypatch):
    # node stub that never writes metadata: analyze, clean, analyze all leave index-missing
    inert = '#!/bin/sh\necho "$@" >> "{calls}"\necho ok\nexit 0\n'
    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch, node_body=inert)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 3
    assert _knowledge_line(capsys.readouterr().out)["fresh"] is False


def test_refresh_refuses_off_base_branch(tmp_path, capsys, monkeypatch):
    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "switch", "-qc", "feature/z"], check=True)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 1
    err = capsys.readouterr().err
    assert "requires the primary checkout to be on main (currently feature/z)" in err
    recorded = calls.read_text() if calls.exists() else ""
    assert "analyze" not in recorded and "clean" not in recorded


def test_refresh_unconfigured_exits_2(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 2 and "omc configure" in capsys.readouterr().err


def test_refresh_waits_for_busy_lock_holder(tmp_path, capsys, monkeypatch):
    from omc.toolctx import ToolContext
    from omc.watchlock import busy_lock

    from .test_watchlock import _hold_in_subprocess

    repo, wt, calls, _ = _gitnexus_env_with_config(tmp_path, monkeypatch)
    _seed_fresh_index(repo)
    lock = busy_lock(ToolContext.from_env(), cwd=str(repo))
    p = _hold_in_subprocess(lock.lock_file, 1.5)
    old = _chdir(repo)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    p.wait()
    assert rc == 0
    assert "· waiting for another omc knowledge refresh to finish" in capsys.readouterr().err


def test_refresh_hints_when_primary_is_behind_origin(tmp_path, capsys, monkeypatch):
    from omc.config import store
    from omc.config.schema import GlobalConfig

    _, primary = _setup_primary_with_origin(tmp_path)  # has an origin
    home = tmp_path / "omchome"
    store.save_global(home, GlobalConfig())
    monkeypatch.setenv("OMC_HOME", str(home))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(_HEALING_NODE.format(calls=calls))
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake")
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    # advance origin from another clone, fetch so the tracking ref is ahead of HEAD
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / "x").write_text("x")
    _git("add", ".", cwd=other)
    _git("commit", "-qm", "x", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)
    _git("fetch", "origin", "main", cwd=primary)
    old = _chdir(primary)
    try:
        rc = run_internal(["gitnexus", "refresh"])
    finally:
        os.chdir(old)
    assert rc == 0
    err = capsys.readouterr().err
    assert "· primary is 1 commits behind origin/main — omc watch syncs it" in err


def test_proxy_prints_stale_verdict_on_stderr_only_when_stale(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    old = _chdir(wt)
    try:
        assert run_internal(["gitnexus", "query", "x"]) == 0
        captured = capsys.readouterr()
        assert captured.err.splitlines()[0].startswith("OMC_KNOWLEDGE ")
        assert "OMC_KNOWLEDGE" not in captured.out
        _seed_fresh_index(repo)
        (repo / ".gitnexus" / "wiki").mkdir()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        (repo / ".gitnexus" / "wiki" / "meta.json").write_text(json.dumps({"fromCommit": head}))
        assert run_internal(["gitnexus", "query", "x"]) == 0
        assert "OMC_KNOWLEDGE" not in capsys.readouterr().err
    finally:
        os.chdir(old)


def test_proxy_git_path_never_prints_verdict(tmp_path, capsys, monkeypatch):
    repo, wt, calls, env = _gitnexus_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    _seed_dep(tmp_path / "omc-home")
    old = _chdir(repo)
    try:
        run_internal(["gitnexus", "--git", "github.com/foo/bar", "query", "x"])
    finally:
        os.chdir(old)
    assert "OMC_KNOWLEDGE" not in capsys.readouterr().err


def test_rebase_main_payload_carries_knowledge_in_all_shapes(tmp_path, capsys):
    _, primary = _setup_primary_with_origin(tmp_path)
    rc, verdict, _ = _run(["rebase-main", "--base", "main"], primary, tmp_path, capsys)
    assert verdict["knowledge"]["fresh"] is False
    assert verdict["knowledge"]["reasons"][0]["code"] == "index-missing"
    wt = _add_worktree(primary, tmp_path)
    _advance_main(primary)
    rc, verdict, _ = _run(["rebase-main", "--base", "main"], wt, tmp_path, capsys)
    assert rc == 0 and "knowledge" in verdict
    assert verdict["knowledge"]["run_in"] == str(primary.resolve())


def _feature_repo(tmp_path, branch="feature/proj-1-fix-login"):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("checkout", "-qb", branch, cwd=repo)
    return repo


def _design_record_line(out):
    lines = [ln for ln in out.splitlines() if ln.startswith("OMC_DESIGN_RECORD ")]
    assert len(lines) == 1, out
    return json.loads(lines[0].split(" ", 1)[1])


def test_design_record_ok_exits_zero(tmp_path, capsys, monkeypatch):
    repo = _feature_repo(tmp_path)
    rel = "docs/superpowers/specs/2026-10-02-proj-1-fix-login-design.md"
    (repo / rel).parent.mkdir(parents=True)
    (repo / rel).write_text("# d\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "spec", cwd=repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run_internal(["design-record"]) == 0
    assert _design_record_line(capsys.readouterr().out) == {
        "ok": True,
        "slug": "proj-1-fix-login",
        "path": rel,
    }


def test_design_record_missing_exits_two_with_verdict(tmp_path, capsys, monkeypatch):
    repo = _feature_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run_internal(["design-record"]) == 2
    data = _design_record_line(capsys.readouterr().out)
    assert data["ok"] is False and data["reason"] == "missing"
    assert "/omc:design" in data["message"]


def test_design_record_non_omc_branch(tmp_path, capsys, monkeypatch):
    repo = _feature_repo(tmp_path, branch="main-ish")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run_internal(["design-record"]) == 2
    assert _design_record_line(capsys.readouterr().out)["reason"] == "no-prefix"


def test_design_record_outside_repo_refuses_like_the_other_verbs(tmp_path, capsys, monkeypatch):
    # Every internal verb prints this line and returns 2 when not in a repo
    # (_primary_and_base, _rebase_main, _gitnexus); design-record matches them.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run_internal(["design-record"]) == 2
    captured = capsys.readouterr()
    assert "error: not inside a git repository" in captured.err
    assert "OMC_DESIGN_RECORD" not in captured.out


def test_design_record_rejects_arguments(capsys):
    assert run_internal(["design-record", "--bogus"]) == 2
    assert "design-record" in capsys.readouterr().err  # usage names the verb


def test_design_record_invalid_config_reaches_the_rc1_boundary(tmp_path, capsys, monkeypatch):
    from omc.cli import main

    repo = _feature_repo(tmp_path)
    (repo / ".omc").mkdir()
    (repo / ".omc" / "config.yaml").write_text("not: [valid\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    assert main(["internal", "design-record"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "OMC_DESIGN_RECORD" not in captured.out
