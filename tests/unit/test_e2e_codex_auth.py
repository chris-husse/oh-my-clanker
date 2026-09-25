"""Account credentials stay in a named Docker volume, outside E2E artifacts."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e import conftest
from tests.e2e.harness import configure_omc, require_token


class FakeWrapped:
    def __init__(self, *, auth=b'{"tokens": {}}', status=0):
        self.calls = []
        self.auth = auth
        self.status = status

    def exec_run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv == ["codex", "login", "status"]:
            return SimpleNamespace(exit_code=self.status, output=b"Logged in using ChatGPT")
        return SimpleNamespace(exit_code=0, output=b"")


class FakeContainer:
    def __init__(self, wrapped=None):
        self.wrapped = wrapped or FakeWrapped()
        self.mounts = []
        self.envs = []
        self.events = []

    def with_volume_mapping(self, source, target, mode):
        self.mounts.append((source, target, mode))
        return self

    def with_command(self, _command):
        return self

    def with_env(self, key, value):
        self.envs.append((key, value))
        return self

    def start(self):
        self.events.append("start")
        return self

    def stop(self):
        self.events.append("stop")

    def get_wrapped_container(self):
        return self.wrapped


def test_codex_account_volume_satisfies_auth_selection(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    require_token("codex")


def test_account_selection_does_not_forward_api_key(monkeypatch):
    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OPENAI_API_KEY", "private-api-key")
    c = FakeContainer()
    conftest._forward_tokens(c, use_codex_account=True)
    assert ("OPENAI_API_KEY", "private-api-key") not in c.envs


def test_unrelated_account_selection_does_not_forward_api_key(monkeypatch):
    monkeypatch.setenv("CODEX_AUTH_VOLUME", "/unrelated-invalid-volume")
    monkeypatch.setenv("OPENAI_API_KEY", "private-api-key")
    c = FakeContainer()
    conftest._forward_tokens(c, use_codex_account=False)
    assert ("OPENAI_API_KEY", "private-api-key") not in c.envs


@pytest.mark.parametrize(
    ("parameter", "marker", "expected"),
    [
        ("codex", None, "codex"),
        ("claude", None, "claude"),
        (None, "codex", "codex"),
        (None, None, None),
    ],
)
def test_e2e_provider_intent_uses_explicit_selection(parameter, marker, expected):
    params = {"provider": parameter} if parameter is not None else {}
    node = SimpleNamespace(
        callspec=SimpleNamespace(params=params),
        get_closest_marker=lambda name: (
            SimpleNamespace(args=(marker,)) if name == "e2e_provider" and marker else None
        ),
    )
    assert conftest.e2e_provider.__wrapped__(SimpleNamespace(node=node)) == expected


def test_missing_codex_credentials_name_both_setup_paths(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_AUTH_VOLUME", raising=False)
    with pytest.raises(pytest.fail.Exception, match="just codex-login"):
        require_token("codex")


def test_account_lifecycle_mounts_volume_and_copies_without_artifacts(monkeypatch, tmp_path):
    from tests.e2e.codex_auth import codex_account

    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OMC_E2E_AUTH_LOCK_DIR", str(tmp_path))
    c = FakeContainer()
    with codex_account(c, lambda _: None):
        assert c.mounts == [("omc-e2e-codex-auth", "/codex-auth", "rw")]
        assert ("CODEX_HOME", "/tmp/omc-codex-home") in c.envs
        assert c.events == ["start"]
    assert c.events == ["start", "stop"]
    assert [argv[:2] for argv, _ in c.wrapped.calls] == [
        ["python3", "-c"],
        ["codex", "login"],
        ["python3", "-c"],
    ]
    assert all("/artifacts" not in str(call) for call in c.wrapped.calls)
    assert all("/root/.codex" not in str(call) for call in c.wrapped.calls)


def test_account_lock_rejects_concurrent_use(monkeypatch, tmp_path):
    from tests.e2e.codex_auth import codex_account

    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OMC_E2E_AUTH_LOCK_DIR", str(tmp_path))
    first, second = FakeContainer(), FakeContainer()
    with codex_account(first, lambda _: None):
        with pytest.raises(pytest.fail.Exception, match="already in use"):
            with codex_account(second, lambda _: None):
                pass
    assert second.events == []


def test_account_copy_back_runs_when_test_raises(monkeypatch, tmp_path):
    from tests.e2e.codex_auth import codex_account

    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OMC_E2E_AUTH_LOCK_DIR", str(tmp_path))
    c = FakeContainer()
    with pytest.raises(ValueError, match="scenario failed"):
        with codex_account(c, lambda _: None):
            raise ValueError("scenario failed")
    assert len(c.wrapped.calls) == 3
    assert c.wrapped.calls[-1][0][:2] == ["python3", "-c"]
    assert c.events[-1] == "stop"


def test_account_rejects_corrupt_json_without_echoing_it(monkeypatch, tmp_path):
    from tests.e2e.codex_auth import codex_account

    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OMC_E2E_AUTH_LOCK_DIR", str(tmp_path))
    c = FakeContainer(FakeWrapped())
    c.wrapped.exec_run = lambda argv, **kwargs: SimpleNamespace(exit_code=22, output=b"SECRET")
    with pytest.raises(pytest.fail.Exception, match="corrupt") as exc:
        with codex_account(c, lambda _: None):
            pass
    assert "SECRET" not in str(exc.value)
    assert c.events[-1] == "stop"


@pytest.mark.parametrize("fixture_name", ["container", "container_with_artifacts"])
@pytest.mark.parametrize("provider", [None, "claude"])
@pytest.mark.parametrize("unrelated_auth", ["missing", "corrupt", "locked"])
def test_generic_fixture_ignores_unrelated_codex_account(
    monkeypatch, tmp_path, fixture_name, provider, unrelated_auth
):
    import testcontainers.core.container as container_module

    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OMC_E2E_AUTH_LOCK_DIR", str(tmp_path))
    monkeypatch.setattr(conftest, "REPO_ROOT", tmp_path)
    c = FakeContainer()

    def unavailable_auth(argv, **kwargs):
        c.wrapped.calls.append((argv, kwargs))
        return SimpleNamespace(exit_code=22 if unrelated_auth == "corrupt" else 21, output=b"")

    c.wrapped.exec_run = unavailable_auth
    monkeypatch.setattr(container_module, "DockerContainer", lambda _image: c)
    lock_file = None
    if unrelated_auth == "locked":
        lock_file = (tmp_path / "omc-codex-auth-omc-e2e-codex-auth.lock").open("a+b")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fixture = getattr(conftest, fixture_name).__wrapped__
        instance = fixture("disposable-image", provider)
        assert next(instance) is c
        with pytest.raises(StopIteration):
            next(instance)
    finally:
        if lock_file is not None:
            lock_file.close()
    assert not any(target == "/codex-auth" for _, target, _ in c.mounts)
    assert len(c.mounts) == (1 if fixture_name == "container_with_artifacts" else 0)
    assert c.events == ["start", "stop"]
    assert c.wrapped.calls == []


@pytest.mark.parametrize("failure", ["missing", "locked"])
def test_codex_fixture_keeps_account_failures_loud(monkeypatch, tmp_path, failure):
    import testcontainers.core.container as container_module

    monkeypatch.setenv("CODEX_AUTH_VOLUME", "omc-e2e-codex-auth")
    monkeypatch.setenv("OMC_E2E_AUTH_LOCK_DIR", str(tmp_path))
    c = FakeContainer()

    def missing_auth(argv, **kwargs):
        c.wrapped.calls.append((argv, kwargs))
        return SimpleNamespace(exit_code=21, output=b"")

    c.wrapped.exec_run = missing_auth
    monkeypatch.setattr(container_module, "DockerContainer", lambda _image: c)
    lock_file = None
    if failure == "locked":
        lock_file = (tmp_path / "omc-codex-auth-omc-e2e-codex-auth.lock").open("a+b")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        instance = conftest.container.__wrapped__("disposable-image", "codex")
        with pytest.raises(pytest.fail.Exception, match="missing|already in use"):
            next(instance)
    finally:
        if lock_file is not None:
            lock_file.close()
    assert c.events == (["start", "stop"] if failure == "missing" else [])


def test_generic_container_setup_does_not_require_provider_plugins(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = FakeContainer()
    conftest._finish_container_setup(c)
    assert c.wrapped.calls == []


def test_selected_codex_setup_does_not_check_unrelated_claude(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = FakeContainer()
    configure_omc(c, "codex")
    assert c.wrapped.calls[0][0] == ["bash", "/repo/docker/setup-plugins.sh", "codex"]
    assert not any("claude" in str(argv) for argv, _ in c.wrapped.calls)


def test_plugin_setup_exit_is_fatal():
    c = FakeContainer()
    c.wrapped.exec_run = lambda argv, **kwargs: SimpleNamespace(exit_code=9, output=b"plugin error")
    with pytest.raises(pytest.fail.Exception, match="plugin setup failed.*plugin error"):
        configure_omc(c, "codex")


def test_api_login_exit_is_fatal(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-private-value")
    c = FakeContainer()
    calls = []

    def exec_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(exit_code=0 if len(calls) == 1 else 7, output=b"login failed")

    c.wrapped.exec_run = exec_run
    with pytest.raises(pytest.fail.Exception, match="Codex API login failed") as exc:
        configure_omc(c, "codex")
    assert "fake-private-value" not in str(exc.value)


def test_codex_setup_preserves_install_error_and_skips_claude(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    codex = bindir / "codex"
    codex.write_text(
        '#!/bin/sh\nprintf "codex %s\\n" "$*" >> "$OMC_CALLS"\n'
        'case "$*" in\n'
        '  "plugin marketplace add /repo") exit 0 ;;\n'
        '  "plugin add omc@oh-my-clanker --json")\n'
        '    echo "upstream unavailable: $OPENAI_API_KEY" >&2; exit 7 ;;\n'
        "esac\nexit 0\n"
    )
    codex.chmod(0o755)
    claude = bindir / "claude"
    claude.write_text('#!/bin/sh\nprintf "claude\\n" >> "$OMC_CALLS"\nexit 9\n')
    claude.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "OMC_CALLS": str(calls),
            "OPENAI_API_KEY": "private-key",
        }
    )
    script = Path(__file__).resolve().parents[2] / "docker" / "setup-plugins.sh"
    result = subprocess.run(["bash", str(script), "codex"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "codex plugin add omc@oh-my-clanker --json" in result.stderr
    assert "upstream unavailable" in result.stderr
    assert "private-key" not in result.stderr
    assert "[redacted]" in result.stderr
    assert "claude" not in calls.read_text()


def _plugin_payload_fixture(tmp_path, *, omc_version="0.1.7", create_payloads=True):
    home = tmp_path / "codex-home"
    repo = tmp_path / "repo"
    (repo / ".codex-plugin").mkdir(parents=True)
    (repo / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "omc", "version": "0.1.7", "skills": "./skills/"})
    )
    for skill in ("start", "plan", "implement"):
        (repo / "skills" / skill).mkdir(parents=True)
        (repo / "skills" / skill / "SKILL.md").write_text(f"current {skill} skill")
    entries = [
        ("omc@oh-my-clanker", "oh-my-clanker", "omc", omc_version, "start"),
        (
            "superpowers@superpowers-marketplace",
            "superpowers-marketplace",
            "superpowers",
            "6.4.1",
            "brainstorming",
        ),
    ]
    additions = {}
    listed = []
    for plugin_id, marketplace, name, version, skill in entries:
        installed = home / "plugins" / "cache" / marketplace / name / version
        if create_payloads:
            (installed / "skills" / skill).mkdir(parents=True)
            (installed / "skills" / skill / "SKILL.md").write_text(
                f"current {skill} skill" if name == "omc" else "installed skill"
            )
            if name == "omc":
                for extra in ("plan", "implement"):
                    (installed / "skills" / extra).mkdir(parents=True)
                    (installed / "skills" / extra / "SKILL.md").write_text(f"current {extra} skill")
                (installed / ".codex-plugin").mkdir()
                (installed / ".codex-plugin" / "plugin.json").write_text(
                    json.dumps({"name": "omc", "version": version, "skills": "./skills/"})
                )
        additions[plugin_id] = {
            "pluginId": plugin_id,
            "version": version,
            "installedPath": str(installed),
        }
        listed.append(
            {"pluginId": plugin_id, "version": version, "installed": True, "enabled": True}
        )
    return home, repo / ".codex-plugin" / "plugin.json", {"installed": listed}, additions


def test_source_skills_cannot_replace_missing_installed_payload(tmp_path):
    from tests.e2e.codex_plugin_payload import validate_payloads

    home, repo_manifest, listing, additions = _plugin_payload_fixture(
        tmp_path, create_payloads=False
    )
    with pytest.raises(ValueError, match="installed payload"):
        validate_payloads(listing, additions, home, repo_manifest)


def test_stale_omc_payload_is_rejected(tmp_path):
    from tests.e2e.codex_plugin_payload import validate_payloads

    home, repo_manifest, listing, additions = _plugin_payload_fixture(tmp_path, omc_version="0.1.0")
    with pytest.raises(ValueError, match="stale"):
        validate_payloads(listing, additions, home, repo_manifest)


def test_same_version_stale_omc_skill_is_rejected(tmp_path):
    from tests.e2e.codex_plugin_payload import validate_payloads

    home, repo_manifest, listing, additions = _plugin_payload_fixture(tmp_path)
    installed = Path(additions["omc@oh-my-clanker"]["installedPath"])
    (installed / "skills" / "plan" / "SKILL.md").write_text("outdated plan")
    with pytest.raises(ValueError, match="stale"):
        validate_payloads(listing, additions, home, repo_manifest)


def test_correct_installed_payloads_are_accepted(tmp_path):
    from tests.e2e.codex_plugin_payload import validate_payloads

    home, repo_manifest, listing, additions = _plugin_payload_fixture(tmp_path)
    result = validate_payloads(listing, additions, home, repo_manifest)
    assert result == {"omc@oh-my-clanker": "0.1.7", "superpowers@superpowers-marketplace": "6.4.1"}


def test_codex_readiness_requires_installed_enabled_plugins(monkeypatch):
    from tests.e2e.codex_auth import require_codex_ready

    c = FakeContainer()
    replies = iter(
        [
            (0, b"codex-cli 0.156.1"),
            (0, b"Logged in using ChatGPT"),
            (0, json.dumps({"installed": []}).encode()),
        ]
    )
    c.wrapped.exec_run = lambda argv, **kwargs: SimpleNamespace(
        exit_code=(reply := next(replies))[0],
        output=(reply[1], None) if kwargs.get("demux") else reply[1],
    )
    with pytest.raises(pytest.fail.Exception, match="omc@oh-my-clanker"):
        require_codex_ready(c)


def test_codex_readiness_requires_skill_files():
    from tests.e2e.codex_auth import require_codex_ready

    c = FakeContainer()
    plugins = {
        "installed": [
            {"pluginId": "omc@oh-my-clanker", "installed": True, "enabled": True},
            {"pluginId": "superpowers@superpowers-marketplace", "installed": True, "enabled": True},
        ]
    }
    replies = iter(
        [
            (0, b"codex-cli 0.156.1"),
            (0, b"Logged in using ChatGPT"),
            (0, json.dumps(plugins).encode()),
            (1, b""),
        ]
    )
    c.wrapped.exec_run = lambda argv, **kwargs: SimpleNamespace(
        exit_code=(reply := next(replies))[0],
        output=(reply[1], None) if kwargs.get("demux") else reply[1],
    )
    with pytest.raises(pytest.fail.Exception, match="skill files"):
        require_codex_ready(c)


def test_codex_readiness_separates_machine_output_from_stderr():
    from tests.e2e.codex_auth import require_codex_ready

    c = FakeContainer()
    warning = b"WARNING: could not create PATH aliases under temporary CODEX_HOME\n"
    plugins = {
        "installed": [
            {"pluginId": plugin, "installed": True, "enabled": True, "version": "1"}
            for plugin in ("omc@oh-my-clanker", "superpowers@superpowers-marketplace")
        ]
    }
    checked = []

    def exec_run(argv, **kwargs):
        if argv == ["codex", "--version"]:
            stdout = b"codex-cli 0.156.1\n"
        elif argv == ["codex", "plugin", "list", "--json"]:
            stdout = json.dumps(plugins).encode()
        elif argv[0] == "python3":
            checked.append(json.loads(kwargs["environment"]["OMC_E2E_CODEX_PLUGIN_LIST"]))
            return SimpleNamespace(exit_code=0, output=b"")
        else:
            return SimpleNamespace(exit_code=0, output=b"Logged in using ChatGPT")
        output = (stdout, warning) if kwargs.get("demux") else warning + stdout
        return SimpleNamespace(exit_code=0, output=output)

    c.wrapped.exec_run = exec_run
    result = require_codex_ready(c)
    assert result["codex_version"] == "codex-cli 0.156.1"
    assert checked == [plugins]


def test_login_helper_uses_named_volume_and_no_host_home(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$OMC_DOCKER_ARGS"\n')
    docker.chmod(0o755)
    args_file = tmp_path / "argv"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "CODEX_AUTH_VOLUME": "test-codex-volume",
            "OMC_DOCKER_ARGS": str(args_file),
        }
    )
    helper = Path(__file__).resolve().parents[2] / "docker" / "codex-login.sh"
    result = subprocess.run(["bash", str(helper)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    argv = args_file.read_text().splitlines()
    assert argv[:3] == ["volume", "create", "test-codex-volume"]
    assert "type=volume,src=test-codex-volume,dst=/codex-auth" in argv
    assert not any(str(Path.home()) in part for part in argv)
