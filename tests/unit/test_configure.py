import json
import os
import subprocess

from omc.agentsmd import distribution_agents_md
from omc.cli import main
from omc.config import store

from ._stubs import HEALTHY_PLUGINS, make_claude_stub


def _home(tmp_path, monkeypatch, *, plugins=HEALTHY_PLUGINS, install_rc=0):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    # configure now installs the claude plugin — a stub MUST shadow the real
    # `claude` (a real one would install plugins over the network into the
    # temp HOME). The real PATH stays behind it: the chain step needs git.
    bindir = tmp_path / "bin"
    calls = make_claude_stub(bindir, plugins=plugins, install_rc=install_rc)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.chdir(tmp_path)  # outside any git repo
    _CLAUDE_CALLS[str(home)] = calls
    return home


_CLAUDE_CALLS: dict[str, object] = {}


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo


def test_configure_defaults(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "claude"
    out = capsys.readouterr().out
    assert "/plugin marketplace add" in out  # claude hint
    assert "codex plugin marketplace add" in out  # codex hint
    assert "opencode" in out  # opencode hint


def test_configure_set_global(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.default=opencode",
            "--set",
            "llm.providers.opencode.model=anthropic/claude-sonnet-5",
        ]
    )
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "opencode"
    assert cfg.llm.providers["opencode"].model == "anthropic/claude-sonnet-5"


def test_configure_set_worktree_routes_to_project_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    rc = main(["configure", "--set", "worktree.base_branch=master"])
    assert rc == 0
    pcfg = store.load_project(repo)
    assert pcfg.worktree.base_branch == "master"
    assert store.load_global(home) is None  # global untouched by a pure worktree set


def test_configure_set_worktree_outside_repo_refused(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "worktree.base_branch=master"]) == 2
    assert "project config" in capsys.readouterr().err


def test_configure_defaults_and_set_combined(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(["configure", "--defaults", "--set", "llm.default=codex"])
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "codex"


def test_configure_defaults_seeds_project_file_when_absent(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    pcfg = store.load_project(repo)
    assert pcfg.worktree.branch_prefix == "feature/"


def test_configure_defaults_never_clobbers_project_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".omc").mkdir()
    (repo / ".omc" / "config.yaml").write_text(
        "schema_version: 1\nworktree:\n  branch_prefix: wip/\n  base_branch: develop\n"
    )
    assert main(["configure", "--defaults"]) == 0
    pcfg = store.load_project(repo)
    assert pcfg.worktree.base_branch == "develop"  # committed team truth untouched


def test_configure_migrates_legacy_json(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    home.mkdir(parents=True)
    (home / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "llm": {"default": "codex", "providers": {"codex": {"model": "gpt-x"}}},
                "worktree": {"base_branch": "develop"},
                "notifications": {"enabled": True, "backend": "macos"},
            }
        )
    )
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.providers.codex.model=gpt-y"]) == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "codex"  # seeded from legacy
    assert cfg.llm.providers["codex"].model == "gpt-y"  # then --set applied
    assert cfg.notifications.enabled is True
    assert not (home / "config.json").exists()  # deleted after global YAML written
    assert "Migrated legacy" in capsys.readouterr().out
    pcfg = store.load_project(repo)
    assert pcfg.worktree.base_branch == "develop"  # worktree section carried into repo


def test_configure_legacy_outside_repo_warns_worktree_not_carried(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)  # chdir is outside any git repo
    home.mkdir(parents=True)
    (home / "config.json").write_text(
        json.dumps({"schema_version": 1, "worktree": {"base_branch": "develop"}})
    )
    assert main(["configure", "--set", "llm.default=codex"]) == 0
    out = capsys.readouterr().out
    assert "Migrated legacy" in out
    assert "NOT migrated" in out  # no repo this run -> worktree.* landed nowhere
    assert not (home / "config.json").exists()


def test_pure_worktree_set_keeps_legacy_json(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    home.mkdir(parents=True)
    (home / "config.json").write_text('{"schema_version": 1}')
    _repo(tmp_path, monkeypatch)
    assert main(["configure", "--set", "worktree.base_branch=master"]) == 0
    assert (home / "config.json").exists()  # global YAML not written -> no deletion


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
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    assert (repo / "AGENTS.md").is_symlink()
    assert (repo / "CLAUDE.md").is_symlink()
    assert (repo / "AGENTS.md").resolve() == distribution_agents_md().resolve()
    assert not (repo / ".omc" / "internal" / "AGENTS.md").exists()
    assert (repo / ".omc" / "config" / "AGENTS.md").is_file()


def test_configure_outside_repo_skips_chain(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "nowhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["configure", "--defaults"]) == 0
    assert not (outside / "AGENTS.md").exists()


def test_configure_installs_missing_claude_plugin(tmp_path, monkeypatch, capsys):
    # configure used to only PRINT install hints; on a fresh machine nothing
    # ever installed the plugin and the first session opened on
    # "Unknown command: /omc:start".
    home = _home(tmp_path, monkeypatch, plugins=[])
    assert main(["configure", "--defaults"]) == 0
    recorded = _CLAUDE_CALLS[str(home)].read_text().splitlines()
    assert "plugin install superpowers@claude-plugins-official --scope user" in recorded
    assert "plugin install omc@oh-my-clanker --scope user" in recorded
    captured = capsys.readouterr()
    assert "✓ claude: omc plugin installed" in captured.err
    assert "/plugin install omc@oh-my-clanker" in captured.out  # hints still printed


def test_configure_survives_plugin_install_failure(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch, plugins=[], install_rc=1)
    assert main(["configure", "--defaults"]) == 0  # config was saved; plugin is advisory
    captured = capsys.readouterr()
    assert "✗ claude:" in captured.err and "fix manually" in captured.err
    assert "/plugin install omc@oh-my-clanker" in captured.out
