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


def _seed_manifest(home, *, indexed=True, documented=False):
    from omc.dependency import load_manifest, save_manifest

    m = load_manifest(home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            H: {
                "checkout": str(home / "dependencies" / "github.com" / "foo" / "bar" / H),
                "docs": str(home / "gitnexus" / "github.com" / "foo" / "bar" / H / "docs"),
                "indexed": indexed,
                "documented": documented,
                "created": "2026-07-22T00:00:00+00:00",
            }
        },
    }
    save_manifest(home, m)


def test_tick_documents_undocumented(tmp_path):
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    assert f"internal dependency document --git github.com/foo/bar@{H}" in calls.read_text()


def test_tick_ensures_unindexed(tmp_path):
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=False, documented=False)
    assert run_dependency_watch(ctx, once=True) == 0
    logged = calls.read_text()
    assert f"internal dependency ensure --git https://github.com/foo/bar.git --commit {H}" in logged
    assert "document" not in logged  # documentation waits for the NEXT tick's fresh manifest


def test_tick_quiet_when_reconciled(tmp_path):
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True, documented=True)
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
    # (watch.py _chain_tick doctrine + the module docstring's never-crash cite).
    ctx, calls = _ctx(tmp_path)
    _seed_manifest(ctx.home, indexed=True, documented=False)  # triggers a document spawn
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
    # loop (watch.py _chain_tick doctrine: warn and skip, never crash).
    from omc.dependency import load_manifest, save_manifest

    ctx, calls = _ctx(tmp_path)
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "commits": {
            H: {
                "checkout": str(ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H),
                "indexed": False,
                "documented": False,
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


def test_cli_parser_accepts_dependency_watch():
    from omc.cli import build_parser

    args = build_parser().parse_args(["dependency-watch", "--once", "--interval", "5"])
    assert args.command == "dependency-watch" and args.once and args.interval == 5
