import os
import stat
import subprocess

from omc.depwatch import run_dependency_watch
from omc.toolctx import ToolContext

H = "d" * 40


def _ctx(tmp_path):
    """ToolContext whose PATH serves a recording `omc` stub."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "omc.calls"
    omc = bindir / "omc"
    omc.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 0\n')
    omc.chmod(omc.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "omc-home"
    home.mkdir()
    env = {"HOME": str(tmp_path), "PATH": f"{bindir}:{os.environ['PATH']}"}
    return ToolContext(home=home, env=env), calls


def _seed_manifest(home, *, indexed=True, documented=None):
    """One manifest entry as ensure writes it. ``documented`` (with its ``docs``
    sibling) is only for the old manifest shape written before documentation
    was removed."""
    from omc.dependency import load_manifest, save_manifest

    entry = {
        "checkout": str(home / "dependencies" / "github.com" / "foo" / "bar" / H),
        "indexed": indexed,
        "created": "2026-07-22T00:00:00+00:00",
    }
    if documented is not None:
        entry["docs"] = str(home / "gitnexus" / "github.com" / "foo" / "bar" / H / "docs")
        entry["documented"] = documented
    m = load_manifest(home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {H: entry},
    }
    save_manifest(home, m)


def test_indexed_entry_is_done_even_without_documented(tmp_path):
    # An old manifest entry that was indexed but never documented needs no work:
    # a commit is done once it is indexed.
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()


def test_tick_ensures_unindexed(tmp_path):
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=False)
    assert run_dependency_watch(ctx, once=True) == 0
    logged = calls.read_text()
    assert f"internal dependency ensure --git https://github.com/foo/bar.git --commit {H}" in logged
    # The stub never flips `indexed`, so the drain's re-scan sees the same
    # unindexed entry and skips it (already attempted this pass): the pass
    # must not spin on the retry.
    assert logged.count("internal dependency ensure") == 1


def test_tick_quiet_when_reconciled(tmp_path):
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True)
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()  # zero subprocess work


def test_tick_adopts_unknown_checkout(tmp_path):
    ctx, calls = _ctx(tmp_path)
    dest = ctx.home / "dependencies" / "github.com" / "baz" / "qux" / H
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin", "https://github.com/baz/qux.git"],
        check=True,
    )
    assert run_dependency_watch(ctx, once=True) == 0
    assert (
        f"internal dependency ensure --git https://github.com/baz/qux.git --commit {H}"
        in calls.read_text()
    )


def test_tick_adopt_redacts_credentialed_origin(tmp_path):
    # An origin carrying a token (https://oauth2:TOKEN@host/…) must be parsed to
    # its credential-free clone URL before it reaches the spawned child argv.
    ctx, calls = _ctx(tmp_path)
    dest = ctx.home / "dependencies" / "github.com" / "baz" / "qux" / H
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(dest),
            "remote",
            "add",
            "origin",
            "https://oauth2:glpat-SECRET@github.com/baz/qux.git",
        ],
        check=True,
    )
    assert run_dependency_watch(ctx, once=True) == 0
    logged = calls.read_text()
    assert f"internal dependency ensure --git https://github.com/baz/qux.git --commit {H}" in logged
    assert "SECRET" not in logged  # token never rode into the child argv


def test_tick_adopt_skips_file_origin(tmp_path):
    # A file:/// (or otherwise unparseable/local) origin must warn-and-skip, and
    # NOT re-spawn a doomed ensure on every tick.
    ctx, calls = _ctx(tmp_path)
    dest = ctx.home / "dependencies" / "github.com" / "baz" / "qux" / H
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin", "file:///tmp/somewhere.git"],
        check=True,
    )
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()  # no spawn for a local/unparseable origin


def test_tick_survives_missing_omc_binary(tmp_path):
    # omc absent from PATH must warn-and-continue, never crash the loop
    # The watch loop must warn and skip, never crash.
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=False)  # triggers an ensure spawn
    (tmp_path / "bin" / "omc").unlink()  # remove the stub
    # Restrict PATH to the now-omc-less bindir so no real omc is discovered either.
    ctx = ToolContext(home=ctx.home, env={**ctx.env, "PATH": str(tmp_path / "bin")})
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()  # the FileNotFoundError was contained


def test_tick_skips_the_managed_gitnexus_clone(tmp_path):
    ctx, calls = _ctx(tmp_path)
    tool = ctx.home / "dependencies" / "gitnexus"
    (tool / ".git").mkdir(parents=True)  # the managed tool clone — never a dependency
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()


def test_tick_skips_manifest_entry_without_url(tmp_path):
    # A malformed entry (no "url") must warn-and-skip, not KeyError out of the
    # loop must warn and skip, never crash.
    from omc.dependency import load_manifest, save_manifest

    ctx, calls = _ctx(tmp_path)
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "commits": {
            H: {
                "checkout": str(ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H),
                "indexed": False,
                "created": "2026-07-22T00:00:00+00:00",
            }
        }
    }
    save_manifest(ctx.home, m)
    assert run_dependency_watch(ctx, once=True) == 0
    assert not calls.exists()  # no ensure spawned for the url-less entry


def test_tick_survives_oserror_during_scan(tmp_path):
    # A dir that raises OSError on scan (unreadable) must be contained per-dir;
    # the walk continues and later work still runs (loop never crashes).
    ctx, calls = _ctx(tmp_path)
    bad = ctx.home / "dependencies" / "aaa-locked"  # sorts before github.com
    (bad / "child").mkdir(parents=True)
    good = ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H
    good.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(good)], check=True)
    subprocess.run(
        ["git", "-C", str(good), "remote", "add", "origin", "https://github.com/foo/bar.git"],
        check=True,
    )
    bad.chmod(0o000)  # iterdir(bad) now raises PermissionError (an OSError)
    try:
        assert run_dependency_watch(ctx, once=True) == 0
    finally:
        bad.chmod(0o755)  # restore so pytest tmp cleanup succeeds
    # the OSError was contained and the walk still reached + adopted the good checkout
    assert (
        f"internal dependency ensure --git https://github.com/foo/bar.git --commit {H}"
        in calls.read_text()
    )


def _stateful_ctx(tmp_path):
    """ToolContext whose `omc` stub actually mutates the manifest the way the
    real internal ensure would (indexed:true). Proves the drain reaches
    completion in ONE pass."""
    import shutil

    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = bindir / "omc.calls"
    home = tmp_path / "omc-home"
    home.mkdir()
    python = shutil.which("python3")
    flip = (
        "import json,sys\n"
        f"p = {str(home / 'dependencies.json')!r}\n"
        "d = json.load(open(p))\n"
        "for dep in d['dependencies'].values():\n"
        "    for entry in dep['commits'].values():\n"
        "        entry['indexed'] = True\n"
        "json.dump(d, open(p, 'w'))\n"
    )
    script = bindir / "flip.py"
    script.write_text(flip)
    omc = bindir / "omc"
    omc.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'case "$3" in\n'
        f'  ensure) "{python}" "{script}" ;;\n'
        "esac\nexit 0\n"
    )
    omc.chmod(omc.stat().st_mode | stat.S_IXUSR)
    env = {"HOME": str(tmp_path), "PATH": f"{bindir}:{os.environ['PATH']}"}
    return ToolContext(home=home, env=env), calls


def test_once_pass_drains_to_completion_and_announces(tmp_path, capsys):
    ctx, calls = _stateful_ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=False)
    assert run_dependency_watch(ctx, once=True) == 0
    logged = calls.read_text()
    assert f"internal dependency ensure --git https://github.com/foo/bar.git --commit {H}" in logged
    assert "document" not in logged  # indexing is the only step
    err = capsys.readouterr().err
    assert "✓ Finished indexing all dependencies! (1 dependency, 1 commit)" in err


def test_once_pass_reports_pending_not_finished_on_failure(tmp_path, capsys):
    # The no-op stub never flips `indexed`, so the pass ends with work
    # remaining: say so plainly, never claim completion, and don't spin.
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=False)
    assert run_dependency_watch(ctx, once=True) == 0
    assert calls.read_text().count("internal dependency ensure") == 1
    err = capsys.readouterr().err
    assert "still pending" in err and "Finished indexing" not in err


def test_dependency_list_prints_status_table(tmp_path, capsys):
    from omc.depwatch import run_dependency_list

    ctx, _ = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True)
    assert run_dependency_list(ctx.home) == 0
    out = capsys.readouterr().out
    header = out.splitlines()[0].split()
    assert header == ["DEPENDENCY", "COMMIT", "REF", "INDEXED", "CREATED"]
    assert "DOCUMENTED" not in out
    assert "github.com/foo/bar" in out
    assert H[:7] in out
    assert "✓" in out  # indexed


def test_dependency_list_empty_manifest_is_friendly(tmp_path, capsys):
    from omc.depwatch import run_dependency_list

    ctx, _ = _ctx(tmp_path)
    assert run_dependency_list(ctx.home) == 0
    out = capsys.readouterr().out
    assert "no dependencies" in out.lower()


def test_cli_parser_dependency_group():
    import pytest

    from omc.cli import build_parser

    args = build_parser().parse_args(["dependency", "watch", "--once", "--interval", "5"])
    assert args.command == "dependency" and args.dep_command == "watch"
    assert args.once and args.interval == 5
    args = build_parser().parse_args(["dependency", "list"])
    assert args.dep_command == "list"
    with pytest.raises(SystemExit):  # the old top-level spelling is gone
        build_parser().parse_args(["dependency-watch", "--once"])


def test_cli_bare_dependency_is_usage_error(tmp_path, monkeypatch, capsys):
    from omc.cli import main

    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    assert main(["dependency"]) == 2


def test_failed_adoption_never_claims_finished(tmp_path, capsys):
    # The no-op stub's ensure leaves the manifest empty, so the pass's only
    # "action" produced nothing adopted — the announcement must say pending,
    # not "Finished indexing all dependencies! (0 dependencies, …)".
    ctx, calls = _ctx(tmp_path)
    dest = ctx.home / "dependencies" / "github.com" / "baz" / "qux" / H
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin", "https://github.com/baz/qux.git"],
        check=True,
    )
    assert run_dependency_watch(ctx, once=True) == 0
    err = capsys.readouterr().err
    assert "still pending" in err
    assert "Finished indexing" not in err
