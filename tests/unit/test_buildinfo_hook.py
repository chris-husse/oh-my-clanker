"""Unit tests for hatch_build.py's pure helpers (the hook itself runs only at build
time). Imported from its file path explicitly — the repo root is not a package."""

import importlib.util
import subprocess
from pathlib import Path

_HOOK_PATH = Path(__file__).parents[2] / "hatch_build.py"
_spec = importlib.util.spec_from_file_location("hatch_build", _HOOK_PATH)
hatch_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hatch_build)


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "c"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "branch", "-M", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@example.com:x/y.git"],
        check=True,
    )
    return tmp_path


def test_resolve_from_git(tmp_path, monkeypatch):
    for var in ("OMC_BUILD_BRANCH", "OMC_BUILD_COMMIT", "OMC_BUILD_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    branch, commit, source = hatch_build._resolve(_git_repo(tmp_path))
    assert branch == "main"
    assert commit and commit != "unknown" and len(commit) >= 7
    assert source == "git@example.com:x/y.git"


def test_env_overrides_git(tmp_path, monkeypatch):
    monkeypatch.setenv("OMC_BUILD_BRANCH", "release/x")
    monkeypatch.setenv("OMC_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("OMC_BUILD_SOURCE", "/some/checkout")
    assert hatch_build._resolve(_git_repo(tmp_path)) == ("release/x", "abc1234", "/some/checkout")


def test_resolve_without_git_is_unknown(tmp_path, monkeypatch):
    for var in ("OMC_BUILD_BRANCH", "OMC_BUILD_COMMIT", "OMC_BUILD_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    assert hatch_build._resolve(tmp_path) == ("unknown", "unknown", "unknown")


def test_redact_strips_credentials_keeps_ssh_user():
    assert hatch_build._redact("https://oauth2:glpat-abc@host/x.git") == "https://host/x.git"
    assert hatch_build._redact("git@example.com:x/y.git") == "git@example.com:x/y.git"
    assert (
        hatch_build._redact("git+ssh://git@example.com/x.git") == "git+ssh://git@example.com/x.git"
    )
    # colonless tokens are credentials too (RED before the fix)
    assert hatch_build._redact("https://ghp_abc123@github.com/x.git") == "https://github.com/x.git"
    assert hatch_build._redact("ssh://git@example.com/x.git") == "ssh://git@example.com/x.git"


def test_render_shape():
    out = hatch_build._render("main", "abc1234", "git@example.com:x/y.git")
    assert 'BRANCH = "main"' in out and 'COMMIT = "abc1234"' in out
    assert out.startswith("# Auto-generated")


def test_render_survives_hostile_values():
    # backslashes (Windows paths) and quote+newline injection attempts must
    # yield a module that COMPILES and round-trips the values verbatim
    hostile_source = 'x"\nINJECTED = "pwned'
    out = hatch_build._render("main", "abc1234", hostile_source)
    ns: dict = {}
    exec(compile(out, "<generated>", "exec"), ns)  # must not raise
    assert ns["SOURCE"] == hostile_source
    assert "INJECTED" not in ns

    out = hatch_build._render("main", "abc1234", "C:\\Users\\dev\\omc-checkout")
    ns = {}
    exec(compile(out, "<generated>", "exec"), ns)  # SyntaxError before the fix
    assert ns["SOURCE"] == "C:\\Users\\dev\\omc-checkout"
