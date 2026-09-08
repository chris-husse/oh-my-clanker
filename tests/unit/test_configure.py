import json
import os
import subprocess

import pytest

from omc.agentsmd import distribution_agents_md
from omc.cli import main
from omc.config import store
from omc.configure import run_configure
from omc.errors import Refusal
from omc.toolctx import ToolContext

from ._stubs import HEALTHY_PLUGINS, make_claude_stub, make_stub, stub_env


def _home(tmp_path, monkeypatch, *, plugins=HEALTHY_PLUGINS, install_rc=0):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    # configure now installs the claude plugin — a stub MUST shadow the real
    # `claude` (a real one would install plugins over the network into the
    # temp HOME). The real PATH stays behind it: the chain step needs git.
    bindir = tmp_path / "bin"
    calls = make_claude_stub(bindir, plugins=plugins, install_rc=install_rc)
    # Same reasoning for `codex`, now that --set validates model ids against
    # the harness's catalog: without a stub these tests would shell out to the
    # REAL codex CLI and their fake slugs would be correctly rejected. rc 1 =
    # "catalog unavailable", which `known_models` degrades to "no list", so
    # tests that are not about validation are unaffected by it.
    make_stub(bindir, "codex", rc=1)
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


def _catalog_stub(bindir, *, stdout, rc=0):
    from ._stubs import make_stub

    make_stub(bindir, "codex", stdout=stdout, rc=rc)
    return ToolContext.from_env(stub_env(bindir))


_CATALOG = '{"models": [{"slug": "gpt-6-astra", "visibility": "list", "priority": 1}]}'


def test_known_models_prefers_the_harness_catalog(tmp_path):
    from omc.configure import known_models

    ctx = _catalog_stub(tmp_path / "bin", stdout=_CATALOG)
    assert known_models(ctx, "codex") == ["gpt-6-astra"]


def test_known_models_falls_back_when_the_catalog_is_unavailable(tmp_path):
    from omc.configure import known_models

    # non-zero exit, garbage output, and a missing binary must all degrade to
    # the static list rather than raising — a probe failure is not fatal.
    assert known_models(_catalog_stub(tmp_path / "b1", stdout=_CATALOG, rc=1), "codex") == []
    assert known_models(_catalog_stub(tmp_path / "b2", stdout="not json"), "codex") == []
    assert known_models(ToolContext.from_env(stub_env(tmp_path / "empty")), "codex") == []
    # claude has no catalog command, so its static aliases come straight back
    assert known_models(_catalog_stub(tmp_path / "b3", stdout=_CATALOG), "claude") == [
        "fable",
        "opus",
        "sonnet",
    ]


def test_set_rejects_a_model_the_harness_does_not_offer(tmp_path):
    # THE BUG: `--set llm.providers.codex.model=astra` used to be accepted and
    # then fail at `omc start` with a 400 blaming org policy.
    ctx = _catalog_stub(tmp_path / "bin", stdout=_CATALOG)
    with pytest.raises(Refusal, match="does not offer a model named 'astra'"):
        run_configure(ctx, defaults=False, sets=["llm.providers.codex.model=astra"])
    # nothing was written
    assert not store.global_config_path(ctx.home).exists()


def test_set_accepts_a_real_model_and_an_empty_one(tmp_path):
    ctx = _catalog_stub(tmp_path / "bin", stdout=_CATALOG)
    assert run_configure(ctx, defaults=False, sets=["llm.providers.codex.model=gpt-6-astra"]) == 0
    # blank means "let the harness pick its default" and must stay allowed
    assert run_configure(ctx, defaults=False, sets=["llm.providers.codex.model="]) == 0


def test_set_of_a_non_model_key_never_probes_the_catalog(tmp_path):
    # a catalog probe per --set would be gratuitous; only model keys validate
    bindir = tmp_path / "bin"
    ctx = _catalog_stub(bindir, stdout=_CATALOG)
    calls = bindir / "codex.calls"
    run_configure(ctx, defaults=False, sets=["notifications.enabled=false"])
    assert not calls.exists()
