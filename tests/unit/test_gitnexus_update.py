import os
import stat
import subprocess

import pytest

from omc.config.schema import Config, GitNexusConfig, GlobalConfig
from omc.gitnexus import embedding_runtime_dir, ensure_embeddings, ensure_gitnexus, update_gitnexus
from omc.toolctx import ToolContext


@pytest.fixture(autouse=True)
def _fast_wiki_poll(monkeypatch):
    import omc.gitnexus as gitnexus_mod

    monkeypatch.setattr(gitnexus_mod, "_WIKI_POLL_SECONDS", 0.05)


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
            f'#!/bin/sh\necho "{name} $@ [cwd=$PWD] [CXXFLAGS=$CXXFLAGS]" >> "{calls}"\n'
            f'echo "{out}"\nexit {rc}\n'
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


def _rewrite_origin(seed, dest):
    _advance_origin(seed)
    _git("fetch", "origin", cwd=dest)
    _git("merge", "--ff-only", "origin/main", cwd=dest)
    _git("commit", "--amend", "-qm", "rewritten c2", cwd=seed)
    _git("push", "-q", "--force", "origin", "main", cwd=seed)


def test_ensure_noops_silently_when_healthy(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, dest = _seed_clone(tmp_path, home)
    # _seed_clone wrote a fake built CLI; the node stub reports 9.9.9, so the
    # CLI is "healthy" — ensure must not clone or build, and must stay silent.
    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 0
    recorded = calls.read_text() if calls.exists() else ""
    assert "npm" not in recorded  # no build on the healthy path
    assert capsys.readouterr().err == ""  # silent when healthy


def test_ensure_installs_when_missing(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
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
    # NO managed clone at home/dependencies/gitnexus yet — ensure must create it.
    dest = home / "dependencies" / "gitnexus"
    assert not dest.exists()
    # The node stub always reports 9.9.9, so post-build verify passes once the
    # build "creates" dist/cli/index.js. The npm stub is a no-op, so seed the
    # CLI the way _seed_clone does, to stand in for `npm run build` output.
    # ensure clones first; then we let the build stub run. To make the post-
    # build --version succeed, the CLI file must exist after clone: create it
    # here by having the origin seed carry it.
    (seed / "gitnexus" / "dist" / "cli").mkdir(parents=True)
    (seed / "gitnexus" / "dist" / "cli" / "index.js").write_text("// built")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "add cli", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)

    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 0
    assert (dest / ".git").exists()  # cloned
    recorded = calls.read_text()
    npm = [ln for ln in recorded.splitlines() if ln.startswith("npm")]
    assert "install" in npm[0] and "gitnexus-shared" in npm[0]
    assert npm[1].startswith("npm ci")
    assert "run build" in npm[2]
    assert "installed" in capsys.readouterr().err.lower()


def test_ensure_reports_build_without_cli(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    origin = tmp_path / "gitnexus-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    # Origin carries the package layout but NO dist/cli/index.js: the no-op npm
    # stub "builds" successfully yet the CLI never materializes, so the post-
    # build --version verify must fail rather than claim success.
    for pkg in ("gitnexus-shared", "gitnexus"):
        (seed / pkg).mkdir()
        (seed / pkg / "package.json").write_text("{}")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c1", cwd=seed)
    _git("branch", "-M", "main", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 1
    assert "won't report --version" in capsys.readouterr().err


def test_ensure_refuses_wrong_origin(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _seed_clone(tmp_path, home)
    # A present clone whose origin differs from approved, but make the CLI look
    # BROKEN so ensure reaches the clone/verify path: point node at a bad rc.
    dest = home / "dependencies" / "gitnexus"
    (dest / "gitnexus" / "dist" / "cli" / "index.js").unlink()  # CLI missing -> unhealthy
    assert ensure_gitnexus(ctx, approved_origin="https://example.com/other.git") == 1
    assert "refusing" in capsys.readouterr().err
    assert "npm" not in (calls.read_text() if calls.exists() else "")  # never built


def test_ensure_build_failure_is_nonzero(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home, npm_rc=1)
    origin, _, dest = _seed_clone(tmp_path, home)
    (dest / "gitnexus" / "dist" / "cli" / "index.js").unlink()  # unhealthy -> must build
    assert ensure_gitnexus(ctx, approved_origin=str(origin)) == 1
    assert "failed" in capsys.readouterr().err


def test_update_installs_when_missing(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin = tmp_path / "gitnexus-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    for pkg in ("gitnexus-shared", "gitnexus"):
        (seed / pkg).mkdir()
        (seed / pkg / "package.json").write_text("{}")
    (seed / "gitnexus" / "dist" / "cli").mkdir(parents=True)
    (seed / "gitnexus" / "dist" / "cli" / "index.js").write_text("// built")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c1", cwd=seed)
    _git("branch", "-M", "main", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    dest = home / "dependencies" / "gitnexus"
    assert not dest.exists()

    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    assert (dest / ".git").exists()  # installed, not skipped
    assert "npm" in calls.read_text()  # built


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


def test_rewritten_origin_forces_main_and_rebuilds(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, dest = _seed_clone(tmp_path, home)
    _rewrite_origin(seed, dest)

    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "origin/main"], capture_output=True, text=True
    ).stdout.strip()
    assert head == remote
    npm = [ln for ln in calls.read_text().splitlines() if ln.startswith("npm")]
    assert len(npm) == 3
    assert npm[0].startswith("npm install ")
    assert f"[cwd={dest / 'gitnexus-shared'}]" in npm[0]
    assert npm[1].startswith("npm ci ")
    assert npm[2].startswith("npm run build ")
    assert "updated" in capsys.readouterr().err


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


def test_build_forces_cxx20_for_native_addons(tmp_path):
    """Node >= 26 headers require C++20, but GitNexus's pinned tree-sitter 0.21.1
    hardcodes -std=c++17 in its binding.gyp; on platforms without a prebuilt
    binary (linux-arm64) node-gyp compiles from source and fails. gyp appends
    the CXXFLAGS env after its own flags, so the last -std wins."""
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _dest = _seed_clone(tmp_path, home)
    _advance_origin(seed)

    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    npm = [ln for ln in calls.read_text().splitlines() if ln.startswith("npm ci")]
    assert len(npm) == 1
    assert "-std=c++20" in npm[0].split("[CXXFLAGS=", 1)[1]


def test_build_keeps_callers_cxxflags(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    ctx = ToolContext.from_env({**ctx.env, "CXXFLAGS": "-O2 -fno-omit-frame-pointer"})
    origin, seed, _dest = _seed_clone(tmp_path, home)
    _advance_origin(seed)

    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    npm = [ln for ln in calls.read_text().splitlines() if ln.startswith("npm ci")]
    flags = npm[0].split("[CXXFLAGS=", 1)[1].rstrip("]")
    assert flags == "-O2 -fno-omit-frame-pointer -std=c++20"


def _embedding_stub(tmp_path, calls, *, install_rc=0):
    """Replace the node stub: `embeddings install` populates the runtime prefix
    like npm would (or fails), everything else answers --version. Records env."""
    node = tmp_path / "bin" / "node"
    populate = (
        '/bin/mkdir -p "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/@huggingface/transformers" '
        '"$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/onnxruntime-node"; '
        ': > "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/@huggingface/transformers'
        '/package.json"; '
        ': > "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/onnxruntime-node/package.json"; '
        if install_rc == 0
        else ""
    )
    # Both npm lines on stdout: run_supervised's two reader threads make
    # cross-stream order best-effort, and the test pins the narration order.
    record = (
        'echo "node RT=$GITNEXUS_EMBEDDING_RUNTIME_DIR HF=$HF_HOME '
        f'LBUG=${{GITNEXUS_LBUG_EXTENSION_INSTALL:-unset}} $@" >> "{calls}"\n'
    )
    node.write_text(
        "#!/bin/sh\n"
        f"{record}"
        'case "$*" in\n'
        f'  *" embeddings install") {populate}echo "added 2 packages"; '
        f'echo "npm warn deprecated"; exit {install_rc} ;;\n'
        "esac\n"
        "echo 9.9.9\nexit 0\n"
    )


def _on():
    return Config(gitnexus=GitNexusConfig(embeddings=True))


def _install_calls(calls):
    text = calls.read_text() if calls.exists() else ""
    return [ln for ln in text.splitlines() if ln.endswith(" embeddings install")]


def test_ensure_embeddings_off_runs_nothing_and_says_nothing(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    said = []
    for cfg in (Config(), GlobalConfig(), None):
        assert ensure_embeddings(ctx, cfg, say=said.append) is False
    assert said == [] and not calls.exists()


def test_ensure_embeddings_on_and_ready_probes_only(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    rt = embedding_runtime_dir(ctx)
    assert rt == home / "dependencies" / "gitnexus-embeddings" / "runtime"
    for pkg in ("@huggingface/transformers", "onnxruntime-node"):
        (rt / "node_modules" / pkg).mkdir(parents=True)
        (rt / "node_modules" / pkg / "package.json").write_text("{}")
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is True
    assert said == [] and not calls.exists()


def test_ensure_embeddings_installs_once_into_the_omc_runtime_dir(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    rt = embedding_runtime_dir(ctx)
    # transformers alone is an interrupted install, not a ready runtime
    (rt / "node_modules" / "@huggingface" / "transformers").mkdir(parents=True)
    (rt / "node_modules" / "@huggingface" / "transformers" / "package.json").write_text("{}")
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is True
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    assert _install_calls(calls) == [
        f"node RT={rt} HF={home / 'dependencies/gitnexus-embeddings/hf'} LBUG=unset "
        f"{cli} embeddings install"
    ]
    assert said == [
        "→ installing GitNexus embedding runtime…",
        "· added 2 packages",
        "· npm warn deprecated",
        "✓ GitNexus embedding runtime installed",
    ]
    assert (rt / "node_modules" / "onnxruntime-node" / "package.json").is_file()
    # second call: the probe answers, no node call
    assert ensure_embeddings(ctx, _on(), say=said.append) is True
    assert len(_install_calls(calls)) == 1


def test_ensure_embeddings_failed_install_narrates_and_returns_false(tmp_path):
    home = tmp_path / "home"
    home.mkdir()  # the CLI lives under home, so home exists whenever an install is reachable
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls, install_rc=1)
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is False
    assert len(_install_calls(calls)) == 1
    failed = [s for s in said if s.startswith("✗ embedding runtime install failed (exit 1): ")]
    assert len(failed) == 1 and failed[0].endswith(" — indexing keyword-only")
    assert "npm warn deprecated" in failed[0]
    assert not any(s.startswith("✓") for s in said)


def test_ensure_embeddings_reinstall_runs_even_when_ready(tmp_path):
    home = tmp_path / "home"
    home.mkdir()  # see above: install runs with cwd=home
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    assert ensure_embeddings(ctx, _on(), say=lambda s: None) is True  # installs
    assert ensure_embeddings(ctx, _on(), reinstall=True, say=lambda s: None) is True
    assert len(_install_calls(calls)) == 2


def test_ensure_embeddings_missing_node_is_a_narrated_miss(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    real = ToolContext.run_supervised

    def no_node(self, argv, **kw):
        if argv and argv[0] == "node":
            raise FileNotFoundError("node")
        return real(self, argv, **kw)

    monkeypatch.setattr(ToolContext, "run_supervised", no_node)
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is False
    assert any(
        s.startswith("✗ embedding runtime install failed: ")
        and s.endswith(" — indexing keyword-only")
        for s in said
    )


def _global_on():
    return GlobalConfig(gitnexus=GitNexusConfig(embeddings=True))


def test_update_rebuild_with_option_on_reinstalls_runtime_even_when_ready(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    _embedding_stub(tmp_path, calls)
    assert ensure_embeddings(ctx, _on(), say=lambda s: None) is True  # ready beforehand
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    assert len(_install_calls(calls)) == 2  # the rebuild re-pins the runtime
    err = capsys.readouterr().err
    assert "✓ GitNexus embedding runtime installed" in err
    # the build (npm via _run_tool) happened before the runtime install
    recorded = calls.read_text()
    assert recorded.index("npm run build") < recorded.rindex("embeddings install")


def test_update_up_to_date_with_option_on_probes_only(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, _ = _seed_clone(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    assert ensure_embeddings(ctx, _on(), say=lambda s: None) is True
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    assert len(_install_calls(calls)) == 1  # no second install on the short-circuit
    assert "up to date" in capsys.readouterr().err


def test_update_up_to_date_with_missing_runtime_installs_it(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, _ = _seed_clone(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    assert len(_install_calls(calls)) == 1


def test_update_failed_runtime_install_does_not_fail_update(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    _embedding_stub(tmp_path, calls, install_rc=1)
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    err = capsys.readouterr().err
    assert "✗ embedding runtime install failed (exit 1)" in err and "updated" in err


@pytest.mark.parametrize("cfg", [None, GlobalConfig()])
def test_update_with_option_off_or_unconfigured_never_touches_embeddings(tmp_path, cfg):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    _embedding_stub(tmp_path, calls)
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=cfg) == 0
    assert _install_calls(calls) == []
    # the argv word, not the always-pinned gitnexus-embeddings env paths the stub records
    assert " embeddings " not in calls.read_text()


def _silent_install_stub(tmp_path, calls, *, shell):
    """A node stub whose `embeddings install` prints NOTHING and runs `shell`
    (npm downloading: minutes without a byte) before populating the runtime like
    npm would. Everything else answers --version."""
    node = tmp_path / "bin" / "node"
    populate = (
        '/bin/mkdir -p "$RT/node_modules/@huggingface/transformers" '
        '"$RT/node_modules/onnxruntime-node"; '
        ': > "$RT/node_modules/@huggingface/transformers/package.json"; '
        ': > "$RT/node_modules/onnxruntime-node/package.json"; '
    )
    node.write_text(
        "#!/bin/sh\n"
        f'echo "node $@" >> "{calls}"\n'
        'RT="$GITNEXUS_EMBEDDING_RUNTIME_DIR"\n'
        'case "$*" in\n'
        f'  *" embeddings install") {shell}{populate}exit 0 ;;\n'
        "esac\n"
        "echo 9.9.9\nexit 0\n"
    )


@pytest.mark.parametrize("writes_runtime", [True, False])
def test_silent_install_lives_on_runtime_writes_and_dies_on_the_wiki_budget(
    tmp_path, monkeypatch, writes_runtime
):
    """npm may print nothing for minutes while it downloads, so bytes alone would
    kill a healthy install: liveness is the runtime dir changing under the
    _WIKI_STALL_SECONDS budget. A silent install that keeps writing there
    survives and ends ready; one that writes nothing is reported stalled."""
    import omc.gitnexus as gn

    monkeypatch.setattr(gn, "_WIKI_STALL_SECONDS", 0.5)
    home = tmp_path / "home"
    home.mkdir()
    ctx, calls = _make_ctx(tmp_path, home)
    if writes_runtime:
        # 1.2 s of silence — past the 0.5 s budget — with a new runtime file every 0.2 s.
        beats = "; ".join(f'/bin/sleep 0.2; : > "$RT/beat{i}"' for i in range(6))
        _silent_install_stub(tmp_path, calls, shell=f'/bin/mkdir -p "$RT"; {beats}; ')
    else:
        _silent_install_stub(tmp_path, calls, shell="/bin/sleep 5; ")
    said = []
    ready = ensure_embeddings(ctx, _on(), say=said.append)
    assert len(_install_calls(calls)) == 1
    if writes_runtime:
        assert ready is True
        assert said == [
            "→ installing GitNexus embedding runtime…",
            "✓ GitNexus embedding runtime installed",
        ]
    else:
        assert ready is False
        assert said == [
            "→ installing GitNexus embedding runtime…",
            "✗ embedding runtime install stalled — no progress for 0s; killed"
            " — indexing keyword-only",
        ]
