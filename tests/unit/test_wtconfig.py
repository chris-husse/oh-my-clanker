import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from omc.config.schema import Config, ProjectConfig, WorktreeConfig
from omc.errors import OmcError
from omc.toolctx import ToolContext
from omc.wtconfig import (
    SPECS_DIR,
    WT_TEMPLATE,
    branch_for,
    ensure_wt_config,
    find_design_record,
    resolve_design_record,
    sanitize_slug,
    slug_for,
)


def _ctx(tmp_path):
    return ToolContext.from_env({"HOME": str(tmp_path)})


def test_creates_starter_when_absent(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    status = ensure_wt_config(_ctx(tmp_path), root)
    assert status == "created"
    written = (root / ".config" / "wt.toml").read_text()
    assert written == WT_TEMPLATE
    assert "copy-ignored" in written
    assert tomllib.loads(written)["step"]["copy-ignored"]["exclude"] == [
        ".gitnexus/",
        "target/",
    ]
    assert "wt.toml" in capsys.readouterr().err


def test_starter_blocks_on_ordered_setup_and_excludes_large_caches():
    data = tomllib.loads(WT_TEMPLATE)
    assert data["pre-start"] == (
        "{ ! test -f .gitmodules || git submodule update --init --recursive; } && "
        "wt step copy-ignored && "
        "{ ! test -f .envrc || ! command -v direnv >/dev/null || direnv allow .; }"
    )
    assert data["step"]["copy-ignored"]["exclude"] == [".gitnexus/", "target/"]
    assert "post-start" not in data
    repo_config = Path(__file__).resolve().parents[2] / ".config" / "wt.toml"
    assert repo_config.read_text() == WT_TEMPLATE


@pytest.mark.parametrize(
    "config",
    [
        WT_TEMPLATE,
        'pre-start = "wt step copy-ignored"\n',
        '[pre-start]\ncopy = "wt step copy-ignored"\n',
        'post-start = "wt step copy-ignored"\n',
        '[post-start]\ncopy = "wt step copy-ignored"\n',
    ],
)
def test_existing_copy_command_in_either_hook_shape_is_ok(tmp_path, capsys, config):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config
    assert capsys.readouterr().err == ""


def test_existing_conditional_copy_command_is_ok(tmp_path):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = 'pre-start = "if true; then wt step copy-ignored; fi"\n'
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


def test_here_document_copy_text_is_suspicious_and_untouched(tmp_path):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = "pre-start = '''cat <<'EOF'\nwt step copy-ignored\nEOF\n'''\n"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert path.read_text() == config


@pytest.mark.parametrize("delimiter", ["''", '""'])
def test_empty_quoted_here_document_copy_text_is_suspicious(tmp_path, delimiter):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = f"pre-start = '''cat <<{delimiter}\nwt step copy-ignored\n\n'''\n"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert path.read_text() == config


@pytest.mark.parametrize(
    ("opener", "terminator"),
    [("<<''", ""), ('<<-""', "\t")],
)
def test_copy_command_after_empty_quoted_here_document_is_ok(tmp_path, opener, terminator):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = (
        f"pre-start = '''cat {opener}\n'unbalanced shell text\n"
        f"{terminator}\nwt step copy-ignored\n'''\n"
    )
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


def test_copy_text_in_empty_second_here_document_is_suspicious(tmp_path):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = "pre-start = '''cat <<END <<''\nfirst body\nEND\nwt step copy-ignored\n\n'''\n"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert path.read_text() == config


def test_copy_command_after_here_document_is_ok(tmp_path):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = "pre-start = '''cat <<EOF\n'unbalanced shell text\nEOF\nwt step copy-ignored\n'''\n"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


@pytest.mark.parametrize("terminator", ["EOF", "\tEOF"])
def test_copy_command_after_tab_stripping_here_document_is_ok(tmp_path, terminator):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = (
        "pre-start = '''cat <<-EOF\n'unbalanced shell text\n"
        f"{terminator}\nwt step copy-ignored\n'''\n"
    )
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


def test_copy_command_after_hyphenated_here_document_delimiter_is_ok(tmp_path):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = "pre-start = '''cat << -EOF\n'unbalanced shell text\n-EOF\nwt step copy-ignored\n'''\n"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


@pytest.mark.parametrize(
    ("operator", "terminator"),
    [("<<", "END-OF-FILE"), ("<<-", "\tEND-OF-FILE")],
)
def test_copy_command_after_hyphenated_word_delimiter_is_ok(tmp_path, operator, terminator):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = (
        f"pre-start = '''cat {operator}END-OF-FILE\n'unbalanced shell text\n"
        f"{terminator}\nwt step copy-ignored\n'''\n"
    )
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


@pytest.mark.parametrize(
    ("operator", "terminator"),
    [("<<", "<<-EOF"), ("<<-", "\t<<-EOF")],
)
def test_copy_command_after_quoted_here_document_delimiter_is_ok(tmp_path, operator, terminator):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = (
        f"pre-start = '''cat {operator}'<<-EOF'\n'unbalanced shell text\n"
        f"{terminator}\nwt step copy-ignored\n'''\n"
    )
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


def test_copy_command_after_here_string_is_ok(tmp_path):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    config = "pre-start = '''cat <<<value\nwt step copy-ignored\n'''\n"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert path.read_text() == config


@pytest.mark.parametrize(
    "config",
    [
        '# wt step copy-ignored\npre-start = "echo ready"\n',
        'description = "wt step copy-ignored"\n',
        '[step.copy-ignored]\nexclude = ["target/"]\n',
        'pre-start = "echo wt step copy-ignored"\n',
        'pre-start = "echo then wt step copy-ignored"\n',
        '[post-start]\nnotice = "echo wt step copy-ignored"\n',
        "[post-start]\nnotice = \"echo 'wt step copy-ignored'\"\n",
    ],
)
def test_incidental_copy_mentions_are_suspicious_and_untouched(tmp_path, config):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    path = root / ".config" / "wt.toml"
    path.write_text(config)
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert path.read_text() == config


def test_existing_config_with_copy_ignored_is_ok(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    (root / ".config" / "wt.toml").write_text(
        '[post-start]\ncopy-ignored = "wt step copy-ignored"\n'
    )
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert capsys.readouterr().err == ""  # silent when fine


def test_existing_config_without_copy_is_flagged_never_edited(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    original = '[post-start]\nnotify = "echo hi"\n'
    (root / ".config" / "wt.toml").write_text(original)
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert (root / ".config" / "wt.toml").read_text() == original  # NEVER edited
    assert "/omc:check-wt-config" in capsys.readouterr().err


def test_unparseable_config_is_flagged_never_edited(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    (root / ".config" / "wt.toml").write_text("not [ valid toml")
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert (root / ".config" / "wt.toml").read_text() == "not [ valid toml"
    assert "/omc:check-wt-config" in capsys.readouterr().err


def test_branch_for_and_slug_for_round_trip():
    cfg = Config()
    assert branch_for(cfg, "proj-1-fix-login") == "feature/proj-1-fix-login"
    assert slug_for(cfg, "feature/proj-1-fix-login") == "proj-1-fix-login"
    # ProjectConfig alone is enough (the internal verb runs unconfigured globally)
    assert slug_for(ProjectConfig(), "feature/x-y") == "x-y"


def test_slug_for_accepts_an_empty_prefix():
    cfg = Config(worktree=WorktreeConfig(branch_prefix=""))
    assert branch_for(cfg, "proj-1") == "proj-1"
    assert slug_for(cfg, "proj-1") == "proj-1"


def test_slug_for_refuses_non_omc_branches():
    cfg = Config()
    assert slug_for(cfg, "main") is None
    assert slug_for(cfg, "HEAD") is None  # detached HEAD as reported by rev-parse
    assert slug_for(cfg, "feature/") is None
    assert slug_for(cfg, "feature/Has_Upper") is None  # not a sanitized slug
    assert slug_for(cfg, "bugfix/proj-1") is None


def test_sanitize_slug_lives_in_wtconfig():
    assert sanitize_slug("Fix: Login Timeout!") == "fix-login-timeout"
    assert len(sanitize_slug("x" * 99)) <= 50


SLUG = "proj-1-fix-login"
RECORD = f"{SPECS_DIR}/2026-10-02-{SLUG}-design.md"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path, branch=f"feature/{SLUG}"):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("checkout", "-qb", branch, cwd=repo)
    return repo


def _write(repo, rel, text="# design\n"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _commit_all(repo, msg="spec"):
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", msg, cwd=repo)


def _repo_ctx(repo, monkeypatch):
    monkeypatch.chdir(repo)
    return ToolContext.from_env({**os.environ, "HOME": str(repo.parent)})


def test_record_missing(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    v = find_design_record(_repo_ctx(repo, monkeypatch), str(repo), SLUG)
    assert v.ok is False and v.reason == "missing" and v.slug == SLUG
    assert "/omc:design" in v.message and SPECS_DIR in v.message
    assert v.to_json() == {"ok": False, "slug": SLUG, "reason": "missing", "message": v.message}


def test_record_committed_and_clean_is_ok(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write(repo, RECORD)
    _commit_all(repo)
    v = find_design_record(_repo_ctx(repo, monkeypatch), str(repo), SLUG)
    assert v == find_design_record(_repo_ctx(repo, monkeypatch), str(repo), SLUG)
    assert v.ok is True and v.path == RECORD and v.slug == SLUG
    assert v.to_json() == {"ok": True, "slug": SLUG, "path": RECORD}


@pytest.mark.parametrize("state", ["untracked", "staged", "modified"])
def test_record_not_committed_clean_is_unclean(tmp_path, monkeypatch, state):
    repo = _repo(tmp_path)
    path = _write(repo, RECORD)
    if state == "staged":
        _git("add", RECORD, cwd=repo)  # ls-files would call this tracked; HEAD does not have it
    if state == "modified":
        _commit_all(repo)
        path.write_text("# edited after the commit\n")
    v = find_design_record(_repo_ctx(repo, monkeypatch), str(repo), SLUG)
    assert v.ok is False and v.reason == "unclean" and v.path == RECORD
    assert "committed" in v.message


def test_two_dated_records_are_ambiguous_and_listed(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write(repo, RECORD)
    _write(repo, f"{SPECS_DIR}/2026-10-03-{SLUG}-design.md")
    _commit_all(repo)
    v = find_design_record(_repo_ctx(repo, monkeypatch), str(repo), SLUG)
    assert v.ok is False and v.reason == "ambiguous"
    assert "2026-10-02" in v.message and "2026-10-03" in v.message


def test_suffix_slug_is_not_a_match(tmp_path, monkeypatch):
    # "login" is a suffix of "fix-login": a loose glob would call this ambiguous
    repo = _repo(tmp_path, branch="feature/login")
    _write(repo, RECORD)  # ...-proj-1-fix-login-design.md
    _write(repo, f"{SPECS_DIR}/2026-10-02-login-design.md")
    _commit_all(repo)
    v = find_design_record(_repo_ctx(repo, monkeypatch), str(repo), "login")
    assert v.ok is True and v.path.endswith("2026-10-02-login-design.md")


def test_resolve_from_branch_end_to_end(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write(repo, RECORD)
    _commit_all(repo)
    v = resolve_design_record(_repo_ctx(repo, monkeypatch), Config())
    assert v.ok and v.slug == SLUG and v.path == RECORD


def test_resolve_refuses_non_omc_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path, branch="hotfix/x")
    v = resolve_design_record(_repo_ctx(repo, monkeypatch), Config())
    assert v.ok is False and v.reason == "no-prefix"
    assert "hotfix/x" in v.message and "feature/" in v.message


def test_resolve_detached_head_gets_its_own_message(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _git("checkout", "-q", "--detach", cwd=repo)
    v = resolve_design_record(_repo_ctx(repo, monkeypatch), Config())
    assert v.ok is False and v.reason == "no-prefix"
    assert "detached HEAD" in v.message


def test_resolve_outside_a_repo_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = ToolContext.from_env({**os.environ, "HOME": str(tmp_path)})
    with pytest.raises(OmcError, match="not inside a git repository"):
        resolve_design_record(ctx, Config())
