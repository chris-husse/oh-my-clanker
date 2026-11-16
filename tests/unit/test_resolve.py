import subprocess

from omc.config import resolve, store
from omc.config.schema import Config, GlobalConfig, ProjectConfig
from omc.toolctx import ToolContext


def _ctx(tmp_path, monkeypatch, cwd):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(cwd)
    return ToolContext.from_env(), home


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_load_effective_none_without_global(tmp_path, monkeypatch):
    ctx, _ = _ctx(tmp_path, monkeypatch, tmp_path)
    assert resolve.load_effective(ctx) is None


def test_load_effective_outside_repo_uses_worktree_defaults(tmp_path, monkeypatch):
    ctx, home = _ctx(tmp_path, monkeypatch, tmp_path)
    gcfg = GlobalConfig()
    gcfg.llm.default = "codex"
    store.save_global(home, gcfg)
    cfg = resolve.load_effective(ctx)
    assert cfg.llm.default == "codex"
    assert cfg.worktree.branch_prefix == "feature/"
    assert cfg.worktree.base_branch == "main"


def test_load_effective_composes_project_file(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    pcfg = ProjectConfig()
    pcfg.worktree.base_branch = "develop"
    store.save_project(repo, pcfg)
    ctx, home = _ctx(tmp_path, monkeypatch, repo)
    store.save_global(home, GlobalConfig())
    cfg = resolve.load_effective(ctx)
    assert cfg.worktree.base_branch == "develop"
    assert cfg.llm.default == "claude"


def test_project_config_defaults_in_repo_without_file(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    ctx, _ = _ctx(tmp_path, monkeypatch, repo)
    assert resolve.project_config(ctx).worktree.base_branch == "main"


def test_load_effective_ignores_a_leftover_secrets_yaml(tmp_path, monkeypatch):
    # The documentation backend's key file is neither read nor deleted (D7).
    ctx, home = _ctx(tmp_path, monkeypatch, tmp_path)
    store.save_global(home, GlobalConfig())
    leftover = home / "secrets.yaml"
    leftover.write_text("api_keys:\n  claude: 31\n")
    cfg = resolve.load_effective(ctx)
    assert isinstance(cfg, Config)
    assert not hasattr(cfg, "secrets")
    assert leftover.read_text() == "api_keys:\n  claude: 31\n"
