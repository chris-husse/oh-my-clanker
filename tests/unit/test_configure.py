import json
import os
import subprocess
from pathlib import Path

import pytest

from omc import agentsmd
from omc.cli import main
from omc.config import store
from omc.config.schema import GlobalConfig, ProviderConfig
from omc.toolctx import ToolContext

from ._stubs import HEALTHY_PLUGINS, make_claude_stub, make_stub


def _home(tmp_path, monkeypatch, *, plugins=HEALTHY_PLUGINS, install_rc=0):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
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


def test_configure_set_global(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.default=codex",
            "--set",
            "llm.providers.codex.model=gpt-6-sol",
        ]
    )
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "codex"
    assert cfg.llm.providers["codex"].model == "gpt-6-sol"


def test_configure_rejects_unsupported_provider_before_writing(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.default=retired"]) == 1
    assert not store.global_config_path(home).exists()
    err = capsys.readouterr().err
    assert "retired" in err and "claude" in err and "codex" in err


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


def test_configure_set_malformed_refusal_never_echoes_the_argument(tmp_path, monkeypatch, capsys):
    # A ':' typo for '=' on an api_key pair must not print the key to stderr.
    _home(tmp_path, monkeypatch)
    rc = main(["configure", "--set", "llm.providers.claude.api_key:sk-ant-TYPO-1234567890"])
    out, err = capsys.readouterr()[:2]
    assert rc == 2
    assert "sk-ant-TYPO" not in err
    assert "sk-ant-TYPO" not in out
    assert "KEY=VALUE" in err


def test_interactive_requires_tty(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["configure"]) == 2
    assert "TTY" in capsys.readouterr().err


def test_configure_in_repo_seeds_project_and_writes_global_section(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    (repo / "AGENTS.md").write_bytes(b"root instructions\n")
    (repo / "legacy.md").write_bytes(b"old symlink target\n")
    (repo / "CLAUDE.md").symlink_to("legacy.md")
    legacy_internal = repo / ".omc/internal/AGENTS.md"
    legacy_internal.parent.mkdir(parents=True)
    legacy_internal.write_bytes(b"old internal layer\n")
    (repo / ".gitignore").write_bytes(b"existing ignore\n")
    assert main(["configure", "--defaults"]) == 0
    assert (tmp_path / ".claude/CLAUDE.md").read_bytes().count(agentsmd.BEGIN_MARKER) == 1
    assert (
        agentsmd.distribution_agents_md().read_bytes()
        in (tmp_path / ".claude/CLAUDE.md").read_bytes()
    )
    assert (repo / ".omc/config/AGENTS.md").is_file()
    assert (repo / "AGENTS.md").read_bytes() == b"root instructions\n"
    assert (repo / ".gitignore").read_bytes() == b"existing ignore\n"
    assert (repo / "CLAUDE.md").is_symlink()
    assert (repo / "CLAUDE.md").readlink() == Path("legacy.md")
    assert (repo / "legacy.md").read_bytes() == b"old symlink target\n"
    assert legacy_internal.read_bytes() == b"old internal layer\n"


def test_configure_outside_repo_still_writes_global_section(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "nowhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["configure", "--defaults"]) == 0
    assert agentsmd.BEGIN_MARKER in (tmp_path / ".claude/CLAUDE.md").read_bytes()
    assert not (outside / "AGENTS.md").exists()


def test_configure_writes_all_providers_when_one_global_file_is_malformed(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    claude_file = tmp_path / ".claude/CLAUDE.md"
    claude_file.parent.mkdir()
    claude_file.write_bytes(agentsmd.BEGIN_MARKER + b"\nmissing end\n")
    assert main(["configure", "--defaults", "--set", "llm.providers.codex.model="]) == 0
    assert claude_file.read_bytes() == agentsmd.BEGIN_MARKER + b"\nmissing end\n"
    assert agentsmd.BEGIN_MARKER in (tmp_path / ".codex/AGENTS.md").read_bytes()
    assert (repo / ".omc/config/AGENTS.md").exists()


def test_configure_set_mode_writes_all_configured_global_sections(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.providers.codex.model="]) == 0
    for path in (tmp_path / ".claude/CLAUDE.md", tmp_path / ".codex/AGENTS.md"):
        assert agentsmd.BEGIN_MARKER in path.read_bytes()


def test_configure_writes_default_provider_even_without_provider_entry(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    cfg = GlobalConfig()
    cfg.llm.default = "codex"
    store.save_global(home, cfg)
    assert "codex" not in cfg.llm.providers

    assert main(["configure", "--set", "llm.default=codex"]) == 0
    assert agentsmd.BEGIN_MARKER in (tmp_path / ".codex/AGENTS.md").read_bytes()
    assert (tmp_path / ".claude/CLAUDE.md").read_bytes().count(agentsmd.BEGIN_MARKER) == 1


def test_configure_interactive_mode_writes_global_sections(tmp_path, monkeypatch):
    from omc import configure

    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def choose_providers(ctx, gcfg, scfg):
        gcfg.llm.providers["codex"] = ProviderConfig()

    monkeypatch.setattr(configure, "_walkthrough_global", choose_providers)
    monkeypatch.setattr(configure, "_ensure_plugins", lambda ctx, cfg: None)
    assert main(["configure"]) == 0
    assert agentsmd.BEGIN_MARKER in (tmp_path / ".claude/CLAUDE.md").read_bytes()
    assert agentsmd.BEGIN_MARKER in (tmp_path / ".codex/AGENTS.md").read_bytes()


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


def test_legacy_json_with_secrets_key_is_rejected_not_dropped(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"llm": {"default": "claude"}, "secrets": {"api_keys": {"claude": "sk-x"}}})
    )
    assert main(["configure", "--set", "llm.default=claude"]) == 1
    err = capsys.readouterr().err
    assert "secrets" in err and "config.json" in err
    assert not (home / "secrets.yaml").exists()  # migration never writes the secrets file


KEY = "sk-ant-api03-SECRETKEYabcdefghijklmnop1234"
_MODELS = json.dumps(
    {
        "data": [
            {"id": "claude-sonnet-5", "created_at": "2026-02-01"},
            {"id": "claude-sonnet-5-5", "created_at": "2026-06-01"},
            {"id": "claude-haiku-4-5", "created_at": "2026-07-01"},
        ]
    }
)


def _fake_http(monkeypatch, *, list_status=200):
    """Fake the ToolContext seam for every ctx main() builds; records URLs."""
    seen = []

    def http_get(self, url, *, headers=None, timeout=30.0):
        seen.append(url)
        if "/models?" in url:
            return list_status, _MODELS if list_status == 200 else '{"error":{"message":"bad key"}}'
        if url.endswith("/models/claude-sonnet-5-5") or url.endswith("/models/claude-sonnet-5"):
            return 200, '{"id":"x"}'
        return 404, '{"error":{"message":"no"}}'

    monkeypatch.setattr(ToolContext, "http_get", http_get)
    return seen


def _claude_calls(home):
    return _CLAUDE_CALLS[str(home)].read_text() if _CLAUDE_CALLS[str(home)].exists() else ""


def test_set_api_key_routes_to_secrets_and_probes_the_key(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", f"llm.providers.claude.api_key={KEY}"]) == 0
    assert store.load_secrets(home).api_keys == {"claude": KEY}
    assert oct(os.stat(home / "secrets.yaml").st_mode & 0o777) == "0o600"
    assert not (home / "config.yaml").exists()  # a key-only --set touches secrets.yaml only
    out = capsys.readouterr()
    assert f"Updated {home / 'secrets.yaml'} (mode 0600)" in out.out
    assert "✓ claude API key stored (******1234)" in out.err
    assert KEY not in out.out + out.err
    assert len(seen) == 1 and seen[0].endswith(
        "/v1/models?limit=1000"
    )  # key probe, backend still cli
    assert " -p " not in f" {_claude_calls(home)} " and "auth status" not in _claude_calls(home)


def test_set_backend_api_resolves_default_model_and_writes_full_id(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.docs.backend=api",
            "--set",
            f"llm.providers.claude.api_key={KEY}",
        ]
    )
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.docs.backend == "api"
    assert cfg.llm.providers["claude"].docs_model == "claude-sonnet-5-5"  # sonnet → newest, full id
    assert any(u.endswith("/v1/models/claude-sonnet-5-5") for u in seen)
    err = capsys.readouterr().err
    assert "→ validating claude-sonnet-5-5 via api" in err and "✓ claude-sonnet-5-5 works" in err


def test_set_backend_api_without_key_is_refused_before_any_probe(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", "llm.docs.backend=api"]) == 1
    assert "llm.providers.claude.api_key is required for the api backend" in capsys.readouterr().err
    assert not (home / "config.yaml").exists() and not (home / "secrets.yaml").exists()
    assert seen == []


def test_probe_failure_writes_nothing_and_never_echoes_the_key(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _fake_http(monkeypatch, list_status=401)
    rc = main(
        [
            "configure",
            "--set",
            "llm.docs.backend=api",
            "--set",
            f"llm.providers.claude.api_key={KEY}",
        ]
    )
    assert rc == 1
    out = capsys.readouterr()
    assert "error: key rejected by api.anthropic.com (HTTP 401)" in out.err
    assert KEY not in out.out + out.err
    assert not (home / "config.yaml").exists() and not (home / "secrets.yaml").exists()


def test_cli_docs_model_change_runs_login_and_headless_probes(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", "llm.providers.claude.docs_model=opus"]) == 0
    calls = _claude_calls(home)
    assert "auth status" in calls
    assert "-p Reply with exactly OK. --output-format text --model opus" in calls
    assert seen == []
    assert store.load_global(home).llm.providers["claude"].docs_model == "opus"  # alias kept on cli
    err = capsys.readouterr().err
    assert "→ checking claude login" in err and "✓ opus works" in err


def test_codex_docs_model_runs_only_the_cli_model_probe(tmp_path, monkeypatch, capsys):
    # The spec's worked example: switching the default to codex and setting its
    # docs model probes on the CLI — codex has no auth-status, so the headless
    # model probe is the whole check — and never touches the network.
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    codex_argv = tmp_path / "codex.argv"
    make_stub(tmp_path / "bin", "codex", stdout="OK", argv_log=codex_argv)
    argv = ["configure", "--set", "llm.default=codex", "--set", "llm.providers.codex.docs_model=x"]
    assert main(argv) == 0
    assert "-m x" in codex_argv.read_text()
    assert "exec --skip-git-repo-check -m x Reply with exactly OK." in codex_argv.read_text()
    assert seen == []
    cfg = store.load_global(home)
    assert cfg.llm.default == "codex" and cfg.llm.providers["codex"].docs_model == "x"
    err = capsys.readouterr().err
    assert "no login check for codex" in err and "✓ x works" in err


def test_cli_not_logged_in_fails_without_writing(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _CLAUDE_CALLS[str(home)] = make_claude_stub(
        tmp_path / "bin", plugins=HEALTHY_PLUGINS, auth_logged_in=False
    )
    assert main(["configure", "--set", "llm.providers.claude.docs_model=opus"]) == 1
    assert "claude is not logged in — run claude auth login" in capsys.readouterr().err
    assert not (home / "config.yaml").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["configure", "--defaults"],
        ["configure", "--set", "llm.default=codex"],
        ["configure", "--set", "llm.providers.claude.model=fable"],
        ["configure", "--set", "notifications.enabled=true"],
    ],
)
def test_non_documentation_changes_run_no_probe(tmp_path, monkeypatch, argv):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(argv) == 0
    calls = _claude_calls(home)
    assert seen == [] and "auth status" not in calls and "-p" not in calls


def test_api_for_codex_refused_on_set_path(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _fake_http(monkeypatch)
    rc = main(["configure", "--set", "llm.default=codex", "--set", "llm.docs.backend=api"])
    assert rc == 1
    assert "codex: API documentation backend not supported yet, use cli" in capsys.readouterr().err
    assert not (home / "config.yaml").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--set", f"llm.providers.codex.api_key={KEY}"],
        ["--set", "llm.default=codex", "--set", f"llm.providers.codex.api_key={KEY}"],
        ["--set", "llm.docs.provider=codex", "--set", f"llm.providers.codex.api_key={KEY}"],
    ],
    ids=["key-only", "default-codex", "docs-provider-codex"],
)
def test_api_key_for_a_provider_without_an_api_backend_is_refused(
    tmp_path, monkeypatch, capsys, argv
):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", *argv]) == 1  # rc 1, not an IndexError traceback
    out = capsys.readouterr()
    assert "codex: API documentation backend not supported yet, use cli" in out.err
    leaked = KEY in out.out + out.err
    assert not leaked, "the api key was echoed (value withheld)"
    assert not (home / "secrets.yaml").exists() and seen == []


@pytest.mark.parametrize("value", ["", "op://Employee/item/password", "sk x"])
def test_bad_api_key_values_refused_without_writing(tmp_path, monkeypatch, capsys, value):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", f"llm.providers.claude.api_key={value}"]) == 1
    err = capsys.readouterr().err
    assert "llm.providers.claude.api_key" in err
    assert not value or value not in err.replace("llm.providers.claude.api_key", "")
    assert not (home / "secrets.yaml").exists() and seen == []


def test_combined_cli_model_and_key_change_runs_both_probe_sets(tmp_path, monkeypatch):
    # Two independent triggers (spec §3.2): the key probe AND the backend's probes.
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.providers.claude.docs_model=opus",
            "--set",
            f"llm.providers.claude.api_key={KEY}",
        ]
    )
    assert rc == 0
    assert len(seen) == 1 and seen[0].endswith("/v1/models?limit=1000")
    calls = _claude_calls(home)
    assert "auth status" in calls and "--model opus" in calls
    assert store.load_global(home).llm.providers["claude"].docs_model == "opus"
    assert store.load_secrets(home).api_keys == {"claude": KEY}


def test_stale_api_config_without_key_blocks_unrelated_sets(tmp_path, monkeypatch, capsys):
    # A hand-deleted secrets.yaml under backend=api: every --set is refused until
    # the key is restored or the backend set back to cli (documented behaviour).
    home = _home(tmp_path, monkeypatch)
    _fake_http(monkeypatch)
    g = GlobalConfig()
    g.llm.docs.backend = "api"
    g.llm.providers["claude"].docs_model = "claude-sonnet-5-5"
    store.save_global(home, g)
    assert main(["configure", "--set", "notifications.enabled=true"]) == 1
    assert "llm.providers.claude.api_key is required" in capsys.readouterr().err
    assert main(["configure", "--set", "llm.docs.backend=cli"]) == 0  # the escape hatch


def test_malformed_api_key_routing_key_is_unknown(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.providers.api_key=x"]) == 1
    assert "unknown config key" in capsys.readouterr().err
