import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from omc.dependency import (
    checkout_dir,
    docs_dir,
    load_manifest,
    manifest_path,
    parse_git_url,
    resolve_ref,
    run_document,
    run_ensure,
    run_list,
    save_manifest,
)
from omc.errors import OmcError
from omc.toolctx import ToolContext

H = "a" * 40
H2 = "b" * 40


def test_parse_https_variants():
    for url in (
        "https://github.com/foo/bar.git",
        "https://github.com/foo/bar",
        "https://github.com/foo/bar/",
    ):
        ref = parse_git_url(url)
        assert (ref.host, ref.path) == ("github.com", "foo/bar")
        assert ref.key == "github.com/foo/bar"
        assert ref.url == "https://github.com/foo/bar.git"


def test_parse_strips_https_credentials():
    ref = parse_git_url("https://oauth2:glpat-SECRET@gitlab.com/g/sub/proj.git")
    assert ref.url == "https://gitlab.com/g/sub/proj.git"  # userinfo gone
    assert "SECRET" not in ref.url
    assert ref.path == "g/sub/proj"  # arbitrary depth (GitLab subgroups)


def test_parse_ssh_and_scp_forms():
    assert (
        parse_git_url("ssh://git@github.com/foo/bar.git").url == "ssh://git@github.com/foo/bar.git"
    )
    ref = parse_git_url("git@github.com:foo/bar.git")
    assert (ref.host, ref.path) == ("github.com", "foo/bar")
    assert ref.url == "git@github.com:foo/bar.git"


def test_parse_rejects_insecure_and_local():
    for bad in (
        "git://github.com/foo/bar.git",
        "http://github.com/foo/bar.git",
        "file:///etc/passwd",
        "/local/path",
        "./relative",
        "~/home/repo",
        "",
    ):
        with pytest.raises(OmcError):
            parse_git_url(bad)


def test_parse_ssh_preserves_port():
    ref = parse_git_url("ssh://git@example.com:2222/foo/bar.git")
    assert ref.url == "ssh://git@example.com:2222/foo/bar.git"
    assert ref.host == "example.com"


def test_parse_ssh_drops_password():
    ref = parse_git_url("ssh://user:pw@host.example.com/p.git")
    assert ref.url == "ssh://user@host.example.com/p.git"
    assert "pw" not in ref.url


def test_parse_error_messages_never_leak_credentials():
    # scp-form misparse must not echo the token into the error message.
    with pytest.raises(OmcError) as exc:
        parse_git_url("oauth2:glpat-SECRET@gitlab.com:g/proj.git")
    assert "SECRET" not in str(exc.value)
    # unparseable input routes through the redacting fallback too.
    with pytest.raises(OmcError) as exc2:
        parse_git_url("oauth2:glpat-SECRET@")
    assert "SECRET" not in str(exc2.value)


def test_parse_rejects_path_traversal():
    with pytest.raises(OmcError):
        parse_git_url("https://github.com/foo/../../etc")


def test_layout_paths(tmp_path):
    ref = parse_git_url("https://github.com/foo/bar.git")
    assert (
        checkout_dir(tmp_path, ref, H)
        == tmp_path / "dependencies" / "github.com" / "foo" / "bar" / H
    )
    assert (
        docs_dir(tmp_path, ref, H)
        == tmp_path / "gitnexus" / "github.com" / "foo" / "bar" / H / "docs"
    )


def test_manifest_roundtrip_and_atomicity(tmp_path):
    assert load_manifest(tmp_path) == {"version": 1, "dependencies": {}}
    data = {"version": 1, "dependencies": {"github.com/foo/bar": {"url": "u", "commits": {}}}}
    save_manifest(tmp_path, data)
    assert load_manifest(tmp_path) == data
    assert manifest_path(tmp_path).is_file()
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers  # atomic write cleans up


def test_manifest_corrupt_raises(tmp_path):
    manifest_path(tmp_path).write_text("{nope")
    with pytest.raises(OmcError):
        load_manifest(tmp_path)


def test_manifest_non_dict_raises(tmp_path):
    manifest_path(tmp_path).write_text("[1, 2]")
    with pytest.raises(OmcError):
        load_manifest(tmp_path)


def _ctx(tmp_path, *, ls_remote_hash=H):
    """ToolContext with recording git + node stubs and a fake GitNexus CLI."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gitcalls = bindir / "git.calls"
    fakegit = bindir / "fakegit"
    # clone --no-checkout <url> <dest>: create <dest>/.git; checkout -b: no-op;
    # ls-remote: print "<hash>\tHEAD"; everything logged.
    fakegit.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{gitcalls}"\n'
        'case "$1" in\n'
        '  clone) mkdir -p "$4/.git" ;;\n'
        f'  ls-remote) printf "{ls_remote_hash}\\tHEAD\\n" ;;\n'
        "esac\nexit 0\n"
    )
    fakegit.chmod(fakegit.stat().st_mode | stat.S_IXUSR)
    nodecalls = bindir / "node.calls"
    node = bindir / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{nodecalls}"\npwd >> "{nodecalls}"\nexit 0\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "omc-home"
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// fake")
    env = {"HOME": str(tmp_path), "PATH": f"{bindir}:{os.environ['PATH']}"}
    ctx = ToolContext(home=home, env=env, git_bin=str(fakegit))
    return ctx, gitcalls, nodecalls


def _verdict(capsys):
    out = capsys.readouterr().out
    # Take the LAST verdict: a test may capture several calls' output in one
    # readouterr (e.g. the idempotency test), and the latest is the one under test.
    lines = [ln for ln in out.splitlines() if ln.startswith("OMC_DEPENDENCY ")]
    return json.loads(lines[-1].split(" ", 1)[1])


def test_ensure_clones_pins_indexes_and_records(tmp_path, capsys):
    ctx, gitcalls, nodecalls = _ctx(tmp_path)
    rc = run_ensure(ctx, "https://github.com/foo/bar.git", None)
    assert rc == 0
    v = _verdict(capsys)
    dest = ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H
    assert v["ok"] and v["commit"] == H and v["indexed"] and not v["documented"]
    assert Path(v["checkout"]) == dest and (dest / ".git").is_dir()
    git_log = gitcalls.read_text()
    assert "ls-remote https://github.com/foo/bar.git HEAD" in git_log
    assert "clone --no-checkout https://github.com/foo/bar.git" in git_log
    assert f"checkout -b omc-pin {H}" in git_log
    node_log = nodecalls.read_text()
    assert f"analyze --index-only --name github.com/foo/bar@{H[:7]}" in node_log
    assert node_log.splitlines()[-1] == str(dest)  # analyze ran FROM the checkout

    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["indexed"] is True and entry["documented"] is False and entry["created"]


def test_ensure_is_idempotent_on_manifest_hit(tmp_path, capsys):
    ctx, gitcalls, nodecalls = _ctx(tmp_path)
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 0
    before = nodecalls.read_text()
    git_before = gitcalls.read_text() if gitcalls.exists() else ""
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 0
    assert nodecalls.read_text() == before  # zero new node work
    git_after = gitcalls.read_text() if gitcalls.exists() else ""
    assert git_after == git_before  # zero new git work either
    assert _verdict(capsys)["cached"] is True


def test_ensure_adopts_existing_checkout_without_cloning(tmp_path, capsys):
    ctx, gitcalls, nodecalls = _ctx(tmp_path)
    dest = ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H
    (dest / ".git").mkdir(parents=True)
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 0
    # A full hash + an existing checkout means git is never invoked at all, so
    # git.calls may not exist; either way it must not record a clone.
    git_log = gitcalls.read_text() if gitcalls.exists() else ""
    assert "clone" not in git_log
    assert "analyze --index-only" in nodecalls.read_text()


def test_ensure_requires_full_hash_and_cli(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    assert run_ensure(ctx, "https://github.com/foo/bar.git", "abc123") == 1  # short hash
    (ctx.home / "dependencies" / "gitnexus").rename(tmp_path / "gone")
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 1
    assert "omc update" in capsys.readouterr().err  # install hint


def test_ensure_reloads_manifest_before_save_no_lost_update(tmp_path, capsys):
    # ensure loads the manifest, then spends minutes cloning+indexing. A
    # concurrent writer (dependency-watch) flips documented:true on ANOTHER
    # commit entry meanwhile. ensure must re-load before saving so that flip
    # survives (rather than being reverted by the stale in-memory snapshot).
    ctx, gitcalls, nodecalls = _ctx(tmp_path)
    other = "e" * 40
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            other: {
                "checkout": "x",
                "docs": "y",
                "indexed": True,
                "documented": False,
                "created": "2026-07-01T00:00:00+00:00",
            }
        },
    }
    save_manifest(ctx.home, m)
    # Rewrite the fake git so its clone step (mid-ensure, after the initial load)
    # flips the OTHER entry's documented:true — standing in for dependency-watch.
    mp = manifest_path(ctx.home)
    flip = (
        "import json,sys;p=sys.argv[1];d=json.load(open(p));"
        f'd["dependencies"]["github.com/foo/bar"]["commits"]["{other}"]["documented"]=True;'
        'json.dump(d,open(p,"w"))'
    )
    fakegit = tmp_path / "bin" / "fakegit"
    fakegit.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{gitcalls}"\n'
        'case "$1" in\n'
        f'  clone) mkdir -p "$4/.git"; python3 -c \'{flip}\' "{mp}" ;;\n'
        f'  ls-remote) printf "{H}\\tHEAD\\n" ;;\n'
        "esac\nexit 0\n"
    )
    fakegit.chmod(fakegit.stat().st_mode | stat.S_IXUSR)
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 0
    commits = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"]
    assert commits[other]["documented"] is True  # concurrent flip survived
    assert commits[H]["indexed"] is True  # ensure's own write landed too


def test_clone_failure_redacts_before_truncating(tmp_path, capsys):
    # A credentialed URL positioned so the 400-char cut would split its token:
    # redaction MUST run before truncation, else a bare token fragment leaks.
    ctx, gitcalls, nodecalls = _ctx(tmp_path)
    # token straddles position 400: chars start before, the '@' lands after.
    line = "fatal: " + "x" * 383 + "user:" + "token" * 8 + "@host/r.git bad"
    fakegit = tmp_path / "bin" / "fakegit"
    fakegit.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{gitcalls}"\n'
        'case "$1" in\n'
        f'  clone) echo "{line}" >&2; exit 1 ;;\n'
        f'  ls-remote) printf "{H}\\tHEAD\\n" ;;\n'
        "esac\nexit 1\n"
    )
    fakegit.chmod(fakegit.stat().st_mode | stat.S_IXUSR)
    assert run_ensure(ctx, "https://github.com/foo/bar.git", H) == 1
    err = capsys.readouterr().err
    assert "[REDACTED]" in err  # redaction happened first, within the kept window
    assert "token" not in err  # no raw token fragment survived the truncation


def test_resolve_ref_selects_hash_and_newest(tmp_path):
    ctx, _, _ = _ctx(tmp_path)

    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            H: {"created": "2026-07-01T00:00:00+00:00", "indexed": True, "checkout": "x"},
            H2: {"created": "2026-07-20T00:00:00+00:00", "indexed": True, "checkout": "y"},
        },
    }
    save_manifest(ctx.home, m)
    key, commit, _ = resolve_ref(ctx.home, f"github.com/foo/bar@{H}")
    assert commit == H
    key, commit, _ = resolve_ref(ctx.home, "https://github.com/foo/bar.git")
    assert commit == H2  # newest created wins

    with pytest.raises(Exception) as exc:
        resolve_ref(ctx.home, "github.com/nope/nope")
    assert "omc internal dependency ensure --git" in str(exc.value)


def test_resolve_ref_scp_form_and_credential_safety(tmp_path):
    ctx, _, _ = _ctx(tmp_path)
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {H: {"created": "2026-07-01T00:00:00+00:00", "indexed": True}},
    }
    save_manifest(ctx.home, m)
    # scp-form URL ref must NOT split at its userinfo @ — it resolves to the key.
    key, commit, _ = resolve_ref(ctx.home, "git@github.com:foo/bar")
    assert key == "github.com/foo/bar" and commit == H
    # a credentialed https ref for an unknown dep raises without leaking the token.
    with pytest.raises(OmcError) as exc:
        resolve_ref(ctx.home, "https://oauth2:glpat-SECRET@github.com/nope/nope")
    assert "SECRET" not in str(exc.value)
    assert "omc internal dependency ensure --git" in str(exc.value)


def _seed_indexed(ctx, *, with_wiki=True):
    """Manifest entry + checkout as ensure would leave them."""
    from omc.config import store
    from omc.config.schema import GlobalConfig
    from omc.dependency import load_manifest, save_manifest

    store.save_global(ctx.home, GlobalConfig())  # llm.default == "claude"
    dest = ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H
    (dest / ".git").mkdir(parents=True, exist_ok=True)
    if with_wiki:
        wiki = dest / ".gitnexus" / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "overview.md").write_text("# bar\n")
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            H: {
                "checkout": str(dest),
                "docs": str(ctx.home / "gitnexus" / "github.com" / "foo" / "bar" / H / "docs"),
                "indexed": True,
                "documented": False,
                "created": "2026-07-22T00:00:00+00:00",
            }
        },
    }
    save_manifest(ctx.home, m)
    return dest


def test_document_runs_wiki_mirrors_and_flips_flag(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    dest = _seed_indexed(ctx)
    rc = run_document(ctx, f"github.com/foo/bar@{H}")
    assert rc == 0
    log = nodecalls.read_text()
    assert "wiki --provider claude" in log
    assert log.splitlines()[-1] == str(dest)  # wiki ran FROM the checkout
    docs = ctx.home / "gitnexus" / "github.com" / "foo" / "bar" / H / "docs"
    assert (docs / "overview.md").read_text() == "# bar\n"
    from omc.dependency import load_manifest

    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["documented"] is True
    assert _verdict(capsys)["documented"] is True


def test_document_without_config_or_index_errors(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    (ctx.home / "config.yaml").unlink()
    assert run_document(ctx, "github.com/foo/bar") == 1
    assert "omc configure" in capsys.readouterr().err
    assert run_document(ctx, "github.com/nope/nope") == 1
    assert "ensure --git" in capsys.readouterr().err


def test_document_failed_wiki_keeps_documented_false(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx, with_wiki=False)  # stub creates no wiki dir -> mirror impossible
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    missing = ctx.home / "dependencies" / "github.com" / "foo" / "bar" / H / ".gitnexus" / "wiki"
    assert str(missing) in err
    assert "exit 0" not in err
    from omc.dependency import load_manifest

    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["documented"] is False


def test_document_rejects_empty_checkout_even_in_a_git_repo(tmp_path, capsys, monkeypatch):
    # A corrupted entry (indexed:true, checkout:"") must be treated as not-indexed
    # BEFORE building a Path — otherwise Path("")/".git" == "./.git" spuriously
    # passes the guard whenever cwd is a git repo, yielding wrong-repo answers.
    from omc.config import store
    from omc.config.schema import GlobalConfig

    ctx, _, _ = _ctx(tmp_path)
    store.save_global(ctx.home, GlobalConfig())
    m = load_manifest(ctx.home)
    m["dependencies"]["github.com/foo/bar"] = {
        "url": "https://github.com/foo/bar.git",
        "commits": {
            H: {
                "checkout": "",
                "docs": "",
                "indexed": True,
                "documented": False,
                "created": "2026-07-22T00:00:00+00:00",
            }
        },
    }
    save_manifest(ctx.home, m)
    repo = tmp_path / "cwd-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)  # cwd IS a git repo — the empty checkout must still fail
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    assert "ensure --git" in capsys.readouterr().err


def test_list_prints_manifest_json(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    assert run_list(ctx.home) == 0
    data = json.loads(capsys.readouterr().out)
    assert "github.com/foo/bar" in data["dependencies"]


def test_document_wiki_nonzero_rc_keeps_documented_false(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)  # wiki dir exists...
    # ...but the wiki step exits non-zero: the rc != 0 half of the gate.
    node = tmp_path / "bin" / "node"
    node.write_text(f'#!/bin/sh\necho "$@" >> "{nodecalls}"\npwd >> "{nodecalls}"\nexit 1\n')
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    assert run_document(ctx, "github.com/foo/bar") == 1
    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["documented"] is False


def test_document_ignores_session_model_uses_docs_floor(tmp_path, capsys):
    # The SESSION model (providers.claude.model) must never reach wiki; with
    # docs_model unset the standard-coding-tier floor is passed explicitly.
    from omc.config import store
    from omc.config.schema import GlobalConfig, LLMConfig, ProviderConfig

    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    store.save_global(
        ctx.home,
        GlobalConfig(
            llm=LLMConfig(
                default="claude", providers={"claude": ProviderConfig(model="claude-fable-5")}
            )
        ),
    )
    assert run_document(ctx, "github.com/foo/bar") == 0
    log = nodecalls.read_text()
    assert "--model sonnet" in log
    assert "claude-fable-5" not in log


def test_document_passes_model_when_configured(tmp_path, capsys):
    from omc.config import store
    from omc.config.schema import GlobalConfig, LLMConfig, ProviderConfig

    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    store.save_global(
        ctx.home,
        GlobalConfig(
            llm=LLMConfig(
                default="claude", providers={"claude": ProviderConfig(docs_model="opus-x")}
            )
        ),
    )
    assert run_document(ctx, "github.com/foo/bar") == 0
    log = nodecalls.read_text()
    assert "wiki --provider claude --model opus-x" in log


def test_update_manifest_locked_read_modify_write(tmp_path):
    # Two writers hammering the same manifest must not lose updates — a lost
    # documented:true re-runs an entire LLM wiki (parallel-document spec).
    from concurrent.futures import ThreadPoolExecutor

    from omc.dependency import load_manifest, update_manifest

    home = tmp_path / "omc-home"
    home.mkdir()

    def add_keys(prefix):
        for i in range(25):

            def mutate(m, key=f"github.com/{prefix}/repo{i}"):
                m["dependencies"][key] = {"url": "u", "commits": {}}

            update_manifest(home, mutate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(add_keys, ["a", "b"]))
    assert len(load_manifest(home)["dependencies"]) == 50


# ─── PageCountTracker ────────────────────────────────────────────────


def _tree(dirpath, nodes):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "first_module_tree.json").write_text(json.dumps(nodes))


def test_tracker_indeterminate_without_tree(tmp_path):
    from omc.dependency import PageCountTracker

    t = PageCountTracker(tmp_path)
    t.refresh()
    assert t.percent is None
    assert t.state() == (None, 0)


def test_tracker_counts_modules_pages_and_overview(tmp_path):
    from omc.dependency import PageCountTracker

    # 3 modules (a, its child b, c) + 1 overview page = 4 total
    _tree(tmp_path, [{"slug": "a", "children": [{"slug": "b"}]}, {"slug": "c"}])
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.md").write_text("x")
    t = PageCountTracker(tmp_path)
    assert t.beat() == (4, 2)
    assert t.percent == 50


def test_tracker_corrupt_tree_degrades_to_indeterminate(tmp_path):
    from omc.dependency import PageCountTracker

    _tree(tmp_path, [{"slug": "a"}])
    (tmp_path / "first_module_tree.json").write_text("{not json")
    t = PageCountTracker(tmp_path)
    t.refresh()  # must not raise
    assert t.percent is None


def test_tracker_invalid_utf8_degrades_to_indeterminate(tmp_path):
    from omc.dependency import PageCountTracker

    (tmp_path / "first_module_tree.json").write_bytes(b"\xff\xfe\x80")
    t = PageCountTracker(tmp_path)
    t.refresh()  # must not raise (UnicodeDecodeError is a ValueError, not OSError)
    assert t.percent is None


def test_tracker_clamps_overshoot(tmp_path):
    from omc.dependency import PageCountTracker

    _tree(tmp_path, [{"slug": "a"}])  # total = 1 module + 1 overview = 2
    for name in ("a.md", "overview.md", "stale-extra.md"):
        (tmp_path / name).write_text("x")
    t = PageCountTracker(tmp_path)
    t.refresh()
    assert t.percent == 100  # 3 pages / 2 expected — clamped, never >100


# ─── run_document: resume line, bar, stall guard ─────────────────────


def test_document_announces_resume_when_pages_exist(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    # with_wiki=False: _seed_indexed's default writes overview.md, which would
    # skew the done-count; build the wiki dir by hand instead.
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])  # 3 modules + overview = 4
    (wiki / "a.md").write_text("x")
    (wiki / "b.md").write_text("x")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    assert "· resuming — 2/4 pages already on disk" in capsys.readouterr().err


def test_document_no_resume_line_on_fresh_run(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)  # wiki dir exists with overview.md but no module tree
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    assert "resuming" not in capsys.readouterr().err


def test_document_stall_kill_exits_1_and_keeps_documented_false(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", 0.3)
    # Replace the node stub with a silent sleeper: no output, no page writes.
    node = tmp_path / "bin" / "node"
    node.write_text("#!/bin/sh\nsleep 30\n")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    err = capsys.readouterr().err
    assert "stalled — no progress" in err
    entry = load_manifest(ctx.home)["dependencies"]["github.com/foo/bar"]["commits"][H]
    assert entry["documented"] is False


def _progress_values(capsys):
    out = capsys.readouterr().out
    vals = []
    verdict_seen = False
    for ln in out.splitlines():
        if ln.startswith("OMC_PROGRESS "):
            assert not verdict_seen, "progress line after the verdict"
            vals.append(json.loads(ln.split(" ", 1)[1])["percent"])
        elif ln.startswith("OMC_DEPENDENCY "):
            verdict_seen = True
    assert verdict_seen
    return vals


def test_document_reports_initial_percent_when_resuming(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])  # 3 + overview = 4
    (wiki / "a.md").write_text("x")
    (wiki / "b.md").write_text("x")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    vals = _progress_values(capsys)
    assert vals[0] == 50  # 2/4 known at start
    assert vals[-1] == 100  # deterministic completion signal


def test_document_reports_only_final_100_on_fresh_run(tmp_path, capsys):
    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)  # wiki exists, no tree -> percent unknown throughout
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    assert _progress_values(capsys) == [100]


def test_document_emits_progress_as_pages_land(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    (wiki / "a.md").write_text("x")  # 1/4 at start
    monkeypatch.setattr(dep, "_WIKI_POLL_SECONDS", 0.05)
    # node stub: write one more page mid-run (cwd is the checkout), then linger
    # long enough for a 0.05s poll to observe it.
    node = tmp_path / "bin" / "node"
    node.write_text("#!/bin/sh\nprintf x > .gitnexus/wiki/b.md\nsleep 0.4\nexit 0\n")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    vals = _progress_values(capsys)
    assert vals[0] == 25 and vals[-1] == 100
    assert 50 in vals  # the mid-run beat saw b.md land


def test_document_stall_emits_no_100(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    _seed_indexed(ctx)
    monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", 0.3)
    monkeypatch.setattr(dep, "_WIKI_POLL_SECONDS", 0.05)
    node = tmp_path / "bin" / "node"
    node.write_text("#!/bin/sh\nsleep 30\n")
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 1
    out = capsys.readouterr().out
    assert 'OMC_PROGRESS {"percent": 100}' not in out
    assert "OMC_DEPENDENCY" not in out


def _api_config(ctx, *, model="claude-sonnet-5-5", key="sk-ant-test-0123456789abcdef"):
    from omc.config import store
    from omc.config.schema import DocsConfig, GlobalConfig, LLMConfig, ProviderConfig, SecretsConfig

    store.save_global(
        ctx.home,
        GlobalConfig(
            llm=LLMConfig(
                default="claude",
                docs=DocsConfig(backend="api"),
                providers={"claude": ProviderConfig(docs_model=model)},
            )
        ),
    )
    if key:
        store.save_secrets(ctx.home, SecretsConfig(api_keys={"claude": key}))
    return key


def _env_echoing_node(tmp_path, nodecalls, *, rc=0, stderr="", stdout=""):
    node = tmp_path / "bin" / "node"
    err = f'echo "{stderr}" >&2\n' if stderr else ""
    out = f'echo "{stdout}"\n' if stdout else ""
    node.write_text(
        f'#!/bin/sh\necho "$@" >> "{nodecalls}"\n'
        f'echo "KEY=$GITNEXUS_API_KEY" >> "{nodecalls}"\npwd >> "{nodecalls}"\n'
        f"{err}{out}exit {rc}\n"
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)


def test_document_api_backend_key_only_in_child_env(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    key = _api_config(ctx)
    _env_echoing_node(tmp_path, nodecalls)
    assert run_document(ctx, "github.com/foo/bar") == 0
    log = nodecalls.read_text()
    argv_line, key_line = log.splitlines()[0], log.splitlines()[1]
    assert argv_line.endswith(
        "wiki --provider custom --base-url https://api.anthropic.com/v1/"
        " --model claude-sonnet-5-5 --reasoning-model"
    )
    assert key_line == f"KEY={key}"  # reached the child env ...
    assert key not in argv_line  # ... never the argv
    out = capsys.readouterr()
    assert key not in out.out + out.err
    assert "· via claude api (claude-sonnet-5-5)" in out.err


def test_document_api_success_redacts_progress_before_tracking(tmp_path, capsys, monkeypatch):
    import omc.dependency as dep

    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    key = _api_config(ctx)
    trackers = []
    real_tracker = dep.GitNexusProgress

    def capture_tracker():
        tracker = real_tracker()
        trackers.append(tracker)
        return tracker

    monkeypatch.setattr(dep, "GitNexusProgress", capture_tracker)
    node = tmp_path / "bin" / "node"
    node.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{nodecalls}"\n'
        'echo \'GITNEXUS_PROGRESS {"phase":"grouping","percent":28,'
        f'"detail":"Fallback after API error {key}; grouping by directory"}}\' >&2\n'
        "exit 0\n"
    )
    assert run_document(ctx, "github.com/foo/bar") == 0
    output = capsys.readouterr()
    assert key not in output.out + output.err
    assert trackers[0].percent == 28
    assert "Fallback after API error ******" in trackers[0].detail
    assert key not in trackers[0].detail


def test_document_cli_backend_with_a_stored_key_passes_no_key(tmp_path, capsys):
    from omc.config import store
    from omc.config.schema import SecretsConfig

    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)  # default config: backend cli
    store.save_secrets(ctx.home, SecretsConfig(api_keys={"claude": "sk-ant-test-0123456789abcdef"}))
    _env_echoing_node(tmp_path, nodecalls)
    assert run_document(ctx, "github.com/foo/bar") == 0
    lines = nodecalls.read_text().splitlines()
    assert lines[0].endswith("wiki --provider claude --model sonnet") and lines[1] == "KEY="


def test_document_api_failure_tail_is_redacted_before_truncation(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    key = _api_config(ctx)
    # 370 chars of noise, then the key spans offsets 384-412: a naive [:400] would
    # cut it in half and leak `sk-ant-test-0123`; redaction must happen first,
    # and the redacted line (395 chars) keeps its ****** inside the cut.
    _env_echoing_node(
        tmp_path,
        nodecalls,
        rc=1,
        stderr='GITNEXUS_PROGRESS {"detail":"private progress"}',
        stdout="x" * 370 + f"LLM API error {key} boom",
    )
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    assert key not in err and key[:12] not in err and "******" in err
    assert "error: gitnexus wiki failed (exit 1): " in err
    assert "LLM API error" in err and "private progress" not in err


def test_document_api_without_key_is_a_clean_error(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    _api_config(ctx, key="")
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    assert "error:" in err and "run omc configure" in err
    assert not nodecalls.exists()  # resolution fails before any gitnexus call


def test_document_reports_gitnexus_percent_over_the_page_count_once_it_speaks(
    tmp_path, capsys, monkeypatch
):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])  # 3 + overview = 4
    (wiki / "a.md").write_text("x")  # page count says 25 at start
    monkeypatch.setattr(dep, "_WIKI_POLL_SECONDS", 0.05)
    # The fake GitNexus speaks on stderr (restricted PATH: builtins only), and a
    # second page lands mid-run — once GitNexus has spoken, the disk count must
    # no longer drive OMC_PROGRESS. Foreign and malformed lines are ignored.
    node = tmp_path / "bin" / "node"
    node.write_text(
        "#!/bin/sh\n"
        'echo \'GITNEXUS_PROGRESS {"phase":"grouping","percent":17,'
        '"detail":"Grouping batch 1/3 (LLM)..."}\' >&2\n'
        "sleep 0.3\n"
        "printf x > .gitnexus/wiki/b.md\n"
        'echo \'GITNEXUS_PROGRESS {"phase":"modules","percent":40,"detail":"b"}\' >&2\n'
        "echo 'not a progress line' >&2\n"
        'echo \'GITNEXUS_PROGRESS {"phase":"junk","percent":250}\' >&2\n'
        "sleep 0.3\n"
        "exit 0\n"
    )
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    vals = _progress_values(capsys)
    assert vals[0] == 25  # disk count before GitNexus spoke
    assert 17 in vals and 40 in vals  # GitNexus's whole-run percent, in order
    assert vals.index(17) < vals.index(40)
    assert 50 not in vals  # b.md landing did not re-assert the disk count
    assert vals[-1] == 100  # deterministic completion signal unchanged


def test_document_failure_excerpt_is_the_tail_where_the_error_is(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    # Progress arrives on stderr while GitNexus's final error arrives on stdout.
    _env_echoing_node(
        tmp_path,
        nodecalls,
        rc=1,
        stderr='GITNEXUS_PROGRESS {"detail":"private progress"}',
        stdout="x" * 500 + " LLM API error: boom",
    )
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    assert "error: gitnexus wiki failed (exit 1): " in err
    assert "LLM API error: boom" in err
    assert "private progress" not in err
