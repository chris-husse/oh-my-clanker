import os
import subprocess

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
    assert "exclude" not in written  # the snapshot model copies EVERYTHING
    assert "wt.toml" in capsys.readouterr().err


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
