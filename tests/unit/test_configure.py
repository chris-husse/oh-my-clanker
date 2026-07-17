from omc.cli import main
from omc.config import store


def _home(tmp_path, monkeypatch):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    return home


def test_configure_defaults(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    cfg = store.load(home)
    assert cfg.llm.default == "claude"
    out = capsys.readouterr().out
    assert "/plugin marketplace add" in out  # claude hint
    assert "codex plugin marketplace add" in out  # codex hint
    assert "opencode" in out  # opencode hint


def test_configure_set(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.default=opencode",
            "--set",
            "llm.providers.opencode.model=anthropic/claude-sonnet-5",
            "--set",
            "worktree.base_branch=master",
        ]
    )
    assert rc == 0
    cfg = store.load(home)
    assert cfg.llm.default == "opencode"
    assert cfg.llm.providers["opencode"].model == "anthropic/claude-sonnet-5"
    assert cfg.worktree.base_branch == "master"


def test_configure_defaults_and_set_combined(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(["configure", "--defaults", "--set", "llm.default=codex"])
    assert rc == 0
    cfg = store.load(home)
    assert cfg.llm.default == "codex"


def test_configure_set_bad_key(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "nope=1"]) == 1
    assert "unknown config key" in capsys.readouterr().err


def test_configure_set_bad_format(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "no-equals-sign"]) == 2


def test_interactive_requires_tty(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["configure"]) == 2
    assert "TTY" in capsys.readouterr().err


def test_configure_in_repo_creates_agents_chain(tmp_path, monkeypatch):
    import subprocess

    _home(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    assert main(["configure", "--defaults"]) == 0
    assert (repo / "AGENTS.md").is_symlink()
    assert (repo / "CLAUDE.md").is_symlink()
    assert (repo / ".omc" / "internal" / "AGENTS.md").is_file()
    assert (repo / ".omc" / "config" / "AGENTS.md").is_file()


def test_configure_outside_repo_skips_chain(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "nowhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["configure", "--defaults"]) == 0
    assert not (outside / "AGENTS.md").exists()
