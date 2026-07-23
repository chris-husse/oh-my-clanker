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
    assert "/omc:index" in capsys.readouterr().err  # install hint


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
    assert "--model claude-sonnet-5" in log
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
