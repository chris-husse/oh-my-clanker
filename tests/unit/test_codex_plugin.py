"""Codex reconciliation against the JSON schema observed on Codex 0.158.0."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from omc import plugin
from omc.errors import OmcError
from omc.toolctx import ToolContext

from ._codex_plugin import CodexState, _source


def _update(state: CodexState, source: Path):
    return plugin.update_codex_plugin(state.ctx, str(source))


def test_matching_source_refreshes_same_version_and_repeats(tmp_path):
    source = _source(tmp_path / "A", "old")
    state = CodexState(tmp_path, source)
    (source / "skills/start/SKILL.md").write_text("new")
    assert _update(state, source)
    assert (state.payload / "skills/start/SKILL.md").read_text() == "new"
    assert _update(state, source)
    assert (
        state.calls
        == [
            ["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"],
            ["codex", "plugin", "list", "--json"],
        ]
        * 2
    )


@pytest.mark.parametrize("old,unlistable", [(False, False), (True, False), (True, True)])
def test_registration_is_reconciled_without_touching_other_marketplace(tmp_path, old, unlistable):
    prior = _source(tmp_path / "A", "old") if old else None
    desired = _source(tmp_path / "B", "new")
    state = CodexState(tmp_path, prior)
    state.fail_initial_list = unlistable
    _update(state, desired)
    assert state.current == desired
    assert (state.payload / "skills/start/SKILL.md").read_text() == "new"
    assert state.other_marketplace["name"] == "other"
    assert state.other_plugin in state._installed()
    expected = [["codex", "plugin", "marketplace", "list", "--json"]]
    if old:
        expected.append(["codex", "plugin", "marketplace", "remove", "oh-my-clanker", "--json"])
    expected.append(["codex", "plugin", "marketplace", "add", str(desired), "--json"])
    expected.extend(
        [
            ["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"],
            ["codex", "plugin", "list", "--json"],
        ]
    )
    assert state.calls == expected


@pytest.mark.parametrize(
    "failing",
    [
        "plugin marketplace remove",
        "plugin marketplace add",
        "plugin add",
    ],
)
def test_required_cli_failure_stops_before_success(tmp_path, failing):
    prior = _source(tmp_path / "A", "old")
    desired = _source(tmp_path / "B", "new")
    state = CodexState(tmp_path, prior)
    state.fail = [failing]
    with pytest.raises(OmcError, match="synthetic CLI failure"):
        _update(state, desired)
    if failing != "plugin add":
        assert ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"] not in state.calls


def test_invalid_desired_source_preserves_old_registration(tmp_path):
    prior = _source(tmp_path / "A", "old")
    desired = _source(tmp_path / "B", "new")
    (desired / ".claude-plugin/marketplace.json").unlink()
    state = CodexState(tmp_path, prior)
    with pytest.raises(OmcError, match="marketplace"):
        _update(state, desired)
    assert state.current == prior
    assert not any(c[:4] == ["codex", "plugin", "marketplace", "remove"] for c in state.calls)


def test_unrelated_listing_failure_does_not_remove_omc(tmp_path):
    prior = _source(tmp_path / "A", "old")
    desired = _source(tmp_path / "B", "new")
    state = CodexState(tmp_path, prior)
    state.fail = ["plugin marketplace list"]
    with pytest.raises(OmcError, match="synthetic CLI failure"):
        _update(state, desired)
    assert state.current == prior
    assert not any(c[:4] == ["codex", "plugin", "marketplace", "remove"] for c in state.calls)


def test_installed_payload_mismatch_is_rejected(tmp_path):
    source = _source(tmp_path / "A", "offered")
    state = CodexState(tmp_path, source)
    copy = state._copy

    def stale_copy(path):
        copy(path)
        (state.payload / "skills/start/SKILL.md").write_text("stale")

    state._copy = stale_copy
    with pytest.raises(OmcError, match="stale|mismatch"):
        _update(state, source)


def test_local_registration_rejects_source_root_mismatch(tmp_path):
    stale = _source(tmp_path / "A", "old skill")
    desired = _source(tmp_path / "B", "new skill")
    state = CodexState(tmp_path, desired)
    state.reported_root = stale
    with pytest.raises(OmcError, match="root"):
        _update(state, desired)
    assert state.current == desired
    assert state.reported_root == stale


@pytest.mark.parametrize(
    ("field", "value"),
    [("installed", "true"), ("installed", "false"), ("enabled", "true"), ("enabled", "false")],
)
def test_nonboolean_installation_flags_are_rejected(tmp_path, field, value):
    source = _source(tmp_path / "A", "skill")
    state = CodexState(tmp_path, source)
    setattr(state, f"{field}_flag", value)
    with pytest.raises(OmcError, match="installed and enabled"):
        _update(state, source)


@pytest.mark.parametrize(
    "case",
    [
        "missing canonical entry",
        "disabled",
        "wrong plugin id",
        "malformed plugin JSON",
        "malformed plugin shape",
        "missing installed path",
        "installed path is file",
        "unreadable installed skill",
        "installed path names source",
        "absent installed manifest",
        "malformed installed manifest",
        "installed manifest wrong skills path",
        "stale support bytes",
        "missing new skill",
        "extra old skill",
    ],
)
def test_update_rejects_unverifiable_installed_artifact(tmp_path, case):
    source = _source(tmp_path / "A", "offered")
    support = source / "skills/start/support.txt"
    support.write_text("current support")
    if case == "missing new skill":
        new_skill = source / "skills/new/SKILL.md"
        new_skill.parent.mkdir()
        new_skill.write_text("new skill")
    state = CodexState(tmp_path, source)
    copy = state._copy

    def tampered_copy(path):
        copy(path)
        if case == "unreadable installed skill":
            skill = state.payload / "skills/start/SKILL.md"
            skill.unlink()
            skill.mkdir()
        elif case == "installed path is file":
            shutil.rmtree(state.payload)
            state.payload.write_text("not a plugin directory")
        elif case == "absent installed manifest":
            (state.payload / ".codex-plugin/plugin.json").unlink()
        elif case == "malformed installed manifest":
            (state.payload / ".codex-plugin/plugin.json").write_text("{bad json")
        elif case == "installed manifest wrong skills path":
            manifest_path = state.payload / ".codex-plugin/plugin.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["skills"] = "./other/"
            manifest_path.write_text(json.dumps(manifest))
        elif case == "stale support bytes":
            (state.payload / "skills/start/support.txt").write_text("old support")
        elif case == "missing new skill":
            (state.payload / "skills/new/SKILL.md").unlink()
        elif case == "extra old skill":
            stale = state.payload / "skills/old/SKILL.md"
            stale.parent.mkdir()
            stale.write_text("old skill")

    state._copy = tampered_copy
    if case == "missing canonical entry":
        state.plugin_list_override = {"installed": [state.other_plugin], "available": []}
    elif case == "disabled":
        state.enabled_flag = False
    elif case == "wrong plugin id":
        state.plugin_list_override = {
            "installed": [{**state._installed()[-1], "pluginId": "other@oh-my-clanker"}],
            "available": [],
        }
    elif case == "malformed plugin JSON":
        state.plugin_list_raw = "{bad json"
    elif case == "malformed plugin shape":
        state.plugin_list_override = {"installed": {"pluginId": "omc@oh-my-clanker"}}
    elif case == "missing installed path":
        state.add_installed_path = str(tmp_path / "missing")
    elif case == "installed path names source":
        state.add_installed_path = str(source)
    with pytest.raises(OmcError, match="Codex plugin update from"):
        _update(state, source)


@pytest.mark.parametrize(
    "case", ["missing skill", "malformed manifest", "wrong skills path", "symlinked skill"]
)
def test_update_rejects_unverifiable_offered_artifact(tmp_path, case):
    source = _source(tmp_path / "A", "offered")
    state = CodexState(tmp_path, source)
    if case == "missing skill":
        (source / "skills/start/SKILL.md").unlink()
    elif case == "malformed manifest":
        (source / ".codex-plugin/plugin.json").write_text("{bad json")
    elif case == "wrong skills path":
        manifest_path = source / ".codex-plugin/plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["skills"] = "./other/"
        manifest_path.write_text(json.dumps(manifest))
    else:
        skill = source / "skills/start/SKILL.md"
        skill.unlink()
        skill.symlink_to(tmp_path / "nonexistent")
    with pytest.raises(OmcError, match="Codex plugin update from"):
        _update(state, source)


@pytest.mark.parametrize("failure", ["scratch create", "codex home mkdir", "scratch cleanup"])
def test_remote_preflight_filesystem_error_is_source_contextual(tmp_path, monkeypatch, failure):
    offered = _source(tmp_path / "offered", "skill")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "codex")})

    class BrokenScratch:
        def __enter__(self):
            if failure == "scratch create":
                raise PermissionError("scratch create denied")
            return str(scratch)

        def __exit__(self, exc_type, exc, traceback):
            if failure == "scratch cleanup":
                raise PermissionError("scratch cleanup denied")
            return False

    monkeypatch.setattr(plugin, "TemporaryDirectory", lambda **kwargs: BrokenScratch())
    original_mkdir = Path.mkdir

    def deny_codex_home(path, *args, **kwargs):
        if failure == "codex home mkdir" and path == scratch / "codex":
            raise PermissionError("codex home mkdir denied")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_codex_home)
    monkeypatch.setattr(
        plugin, "_codex_json", lambda ctx, argv: {"marketplaceName": "oh-my-clanker"}
    )
    monkeypatch.setattr(
        plugin, "_codex_registration", lambda ctx, provider, source: {"root": str(offered)}
    )
    with pytest.raises(OmcError, match=f"Codex plugin update from example/omc: .*{failure} denied"):
        plugin.update_codex_plugin(ctx, "example/omc")


def test_remote_preflight_keeps_git_config_from_normal_home(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    (home / ".gitconfig").write_text(
        '[url "file:///fixture-origin/"]\n\tinsteadOf = https://github.com/example/\n'
    )
    offered = _source(tmp_path / "offered", "skill")
    ctx = ToolContext.from_env({"HOME": str(home), "CODEX_HOME": str(home / ".codex")})
    probed_homes = []

    def add_with_git_probe(probe, argv):
        assert argv == ["codex", "plugin", "marketplace", "add", "example/omc", "--json"]
        config = probe.run(["git", "config", "--global", "--get-regexp", r"^url\..*\.insteadof$"])
        assert config.returncode == 0, config.stderr
        assert "https://github.com/example/" in config.stdout
        probed_homes.append(probe.env["CODEX_HOME"])
        return {"marketplaceName": "oh-my-clanker"}

    monkeypatch.setattr(plugin, "_codex_json", add_with_git_probe)
    monkeypatch.setattr(
        plugin, "_codex_registration", lambda probe, provider, source: {"root": str(offered)}
    )
    plugin._codex_preflight(ctx, plugin.CodexProvider(), "example/omc")
    assert len(probed_homes) == 1
    assert Path(probed_homes[0]) != home / ".codex"


def test_offered_plugin_symlink_loop_reports_codex_source_error(tmp_path):
    source = _source(tmp_path / "source", "skill")
    loop = source / "loop"
    loop.symlink_to("loop")
    marketplace = source / ".claude-plugin/marketplace.json"
    data = json.loads(marketplace.read_text())
    data["plugins"][0]["source"] = "./loop"
    marketplace.write_text(json.dumps(data))
    state = CodexState(tmp_path)

    with pytest.raises(OmcError, match="Codex plugin update from .*plugin root.*loop"):
        _update(state, source)


def test_offered_plugin_null_path_reports_codex_source_error(tmp_path):
    prior = _source(tmp_path / "prior", "old skill")
    desired = _source(tmp_path / "desired", "new skill")
    marketplace = desired / ".claude-plugin/marketplace.json"
    data = json.loads(marketplace.read_text())
    data["plugins"][0]["source"] = "./bad\x00path"
    marketplace.write_text(json.dumps(data))
    state = CodexState(tmp_path, prior)

    with pytest.raises(OmcError) as failure:
        _update(state, desired)

    assert f"Codex plugin update from {desired}: cannot resolve Codex plugin root at" in str(
        failure.value
    )
    assert state.current == prior
    assert state.calls == []


def test_marketplace_source_symlink_loop_reports_codex_source_error(tmp_path):
    source = _source(tmp_path / "source", "skill")
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    state = CodexState(tmp_path, source)
    registration = state._registration

    def looping_registration():
        entries = registration()
        entries[0]["marketplaceSource"]["source"] = str(loop)
        return entries

    state._registration = looping_registration
    with pytest.raises(OmcError, match="Codex plugin update from .*marketplace source.*loop"):
        _update(state, source)


def test_marketplace_root_symlink_loop_reports_codex_source_error(tmp_path):
    source = _source(tmp_path / "source", "skill")
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    state = CodexState(tmp_path, source)
    state.reported_root = loop

    with pytest.raises(OmcError, match="Codex plugin update from .*marketplace root.*loop"):
        _update(state, source)


def test_installed_path_symlink_loop_reports_codex_source_error(tmp_path):
    source = _source(tmp_path / "source", "skill")
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    state = CodexState(tmp_path, source)
    state.add_installed_path = str(loop)

    with pytest.raises(OmcError, match="Codex plugin update from .*installed artifact.*loop"):
        _update(state, source)
