import os
import stat
import subprocess

from omc.gitnexus import update_gitnexus
from omc.toolctx import ToolContext


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_ctx(tmp_path, home, *, npm_rc=0):
    """Real git on PATH + recording npm/node stubs."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "tool.calls"
    for name, out, rc in (("npm", "ok", npm_rc), ("node", "9.9.9", 0)):
        stub = bindir / name
        stub.write_text(
            f'#!/bin/sh\necho "{name} $@ [cwd=$PWD]" >> "{calls}"\necho "{out}"\nexit {rc}\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return ToolContext.from_env(env), calls


def _seed_clone(tmp_path, home):
    """Local bare 'approved origin' + managed clone at home/dependencies/gitnexus."""
    origin = tmp_path / "gitnexus-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    (seed / "gitnexus-shared").mkdir()
    (seed / "gitnexus-shared" / "package.json").write_text("{}")
    (seed / "gitnexus").mkdir()
    (seed / "gitnexus" / "package.json").write_text("{}")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c1", cwd=seed)
    _git("branch", "-M", "main", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    dest = home / "dependencies" / "gitnexus"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
    cli = dest / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// built")
    return origin, seed, dest


def _advance_origin(seed):
    (seed / "new.txt").write_text("new\n")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c2", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)


def test_skips_when_not_installed(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    assert update_gitnexus(ctx) == 0
    assert "/omc:index" in capsys.readouterr().err


def test_refuses_wrong_origin(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _seed_clone(tmp_path, home)
    # approved origin deliberately differs from the clone's actual origin
    assert update_gitnexus(ctx, approved_origin="https://example.com/other.git") == 1
    assert "refusing" in capsys.readouterr().err
    assert not calls.exists()  # never built


def test_up_to_date_short_circuits(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, _ = _seed_clone(tmp_path, home)
    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    err = capsys.readouterr().err
    assert "up to date" in err
    recorded = calls.read_text() if calls.exists() else ""
    assert "npm" not in recorded  # no build on the short-circuit path


def test_moved_pulls_builds_and_verifies(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, dest = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    # clone fast-forwarded to origin/main
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "origin/main"], capture_output=True, text=True
    ).stdout.strip()
    assert head == remote
    recorded = calls.read_text()
    lines = [ln for ln in recorded.splitlines() if ln.startswith("npm")]
    assert "install" in lines[0] and "gitnexus-shared" in lines[0]
    assert lines[1].startswith("npm ci") and "gitnexus-shared" not in lines[1]
    assert "run build" in lines[2]
    assert "9.9.9" in capsys.readouterr().err  # new version reported


def test_build_failure_is_nonzero(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home, npm_rc=1)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    assert update_gitnexus(ctx, approved_origin=str(origin)) == 1
    assert "failed" in capsys.readouterr().err


def test_credential_redaction(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    _seed_clone(tmp_path, home)
    dest = home / "dependencies" / "gitnexus"
    subprocess.run(
        [
            "git",
            "-C",
            str(dest),
            "remote",
            "set-url",
            "origin",
            "https://user:sekret123@example.com/other.git",
        ],
        check=True,
    )
    # default approved origin (GITNEXUS_ORIGIN) won't match the tampered origin
    assert update_gitnexus(ctx) == 1
    err = capsys.readouterr().err
    assert "[REDACTED]" in err
    assert "sekret123" not in err


def test_missing_node_is_clean_failure(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)

    real_run = ToolContext.run

    def _stub_run(self, argv, **kwargs):
        if argv and argv[0] in ("npm", "node"):
            raise FileNotFoundError(argv[0])
        return real_run(self, argv, **kwargs)

    monkeypatch.setattr(ToolContext, "run", _stub_run)

    assert update_gitnexus(ctx, approved_origin=str(origin)) == 1
    err = capsys.readouterr().err
    assert "not found" in err
    assert "Traceback" not in err
