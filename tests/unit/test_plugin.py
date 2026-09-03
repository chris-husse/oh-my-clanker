import json

import pytest

from omc.errors import OmcError
from omc.plugin import ensure_plugin, marketplace_source
from omc.toolctx import ToolContext

from ._stubs import HEALTHY_PLUGINS, make_claude_stub, stub_env

OMC = {"id": "omc@oh-my-clanker"}
SUPERPOWERS = {"id": "superpowers@claude-plugins-official"}
DEP_ERROR = (
    'Dependency "superpowers@superpowers-marketplace" is not installed — run '
    "`claude plugin install superpowers@superpowers-marketplace`, or check that "
    "its marketplace is added"
)


def _ctx(tmp_path, **kw):
    calls = make_claude_stub(tmp_path / "bin", **kw)
    return ToolContext.from_env(stub_env(tmp_path / "bin")), calls


def _state(tmp_path):
    return {e["id"]: e for e in json.loads((tmp_path / "bin" / "claude.plugins.json").read_text())}


def test_healthy_plugin_is_left_alone(tmp_path):
    ctx, calls = _ctx(tmp_path, plugins=HEALTHY_PLUGINS)
    assert ensure_plugin(ctx, "claude") == "ok"
    recorded = calls.read_text()
    assert "plugin list --json" in recorded  # the JSON probe, not the human listing
    assert "plugin install" not in recorded
    assert "plugin update" not in recorded


def test_missing_plugin_self_heals(tmp_path, capsys):
    ctx, calls = _ctx(tmp_path, plugins=[SUPERPOWERS])
    assert ensure_plugin(ctx, "claude") == "installed"
    lines = calls.read_text().splitlines()
    add_at = next(i for i, ln in enumerate(lines) if ln.startswith("plugin marketplace add"))
    install_at = next(i for i, ln in enumerate(lines) if ln.startswith("plugin install omc@"))
    assert add_at < install_at
    assert lines[install_at] == "plugin install omc@oh-my-clanker --scope user"
    assert "installing the omc plugin" in capsys.readouterr().err
    assert "omc@oh-my-clanker" in _state(tmp_path)


def test_failed_to_load_plugin_is_reinstalled(tmp_path, capsys):
    # The canonical first-run bug: the plugin is INSTALLED but Claude refuses to
    # load it (stale manifest declared a dependency on a marketplace the user
    # doesn't have). A substring probe calls this "ok"; the JSON probe must
    # see the error and reinstall from a refreshed marketplace snapshot.
    ctx, calls = _ctx(tmp_path, plugins=[{**OMC, "errors": [DEP_ERROR]}, SUPERPOWERS])
    assert ensure_plugin(ctx, "claude") == "repaired"
    lines = calls.read_text().splitlines()
    update_at = lines.index("plugin marketplace update oh-my-clanker")
    uninstall_at = lines.index("plugin uninstall omc@oh-my-clanker")
    install_at = lines.index("plugin install omc@oh-my-clanker --scope user")
    assert update_at < uninstall_at < install_at
    assert "errors" not in _state(tmp_path)["omc@oh-my-clanker"]
    assert "failed to load" in capsys.readouterr().err


def test_disabled_plugin_counts_as_broken(tmp_path):
    ctx, _ = _ctx(tmp_path, plugins=[{**OMC, "enabled": False}, SUPERPOWERS])
    assert ensure_plugin(ctx, "claude") == "repaired"
    assert _state(tmp_path)["omc@oh-my-clanker"]["enabled"] is True


def test_still_broken_after_reinstall_fails_loud(tmp_path):
    ctx, _ = _ctx(
        tmp_path,
        plugins=[{**OMC, "errors": [DEP_ERROR]}, SUPERPOWERS],
        install_errors=[DEP_ERROR],
    )
    with pytest.raises(OmcError, match="still fails to load") as info:
        ensure_plugin(ctx, "claude")
    assert "superpowers@superpowers-marketplace" in str(info.value)  # Claude's own error text


def test_superpowers_is_installed_when_missing(tmp_path, capsys):
    # omc's start skill hands off to superpowers; the manifest no longer
    # declares it (Claude never auto-installs dependencies and rejects the
    # plugin outright when superpowers came from another marketplace), so
    # ensure_plugin installs it from the official marketplace.
    ctx, calls = _ctx(tmp_path, plugins=[OMC])
    assert ensure_plugin(ctx, "claude") == "installed superpowers"
    lines = calls.read_text().splitlines()
    assert "plugin marketplace add anthropics/claude-plugins-official" in lines
    assert "plugin install superpowers@claude-plugins-official --scope user" in lines
    assert "installing superpowers" in capsys.readouterr().err


def test_superpowers_from_any_marketplace_satisfies(tmp_path):
    ctx, calls = _ctx(tmp_path, plugins=[OMC, {"id": "superpowers@superpowers-marketplace"}])
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin install" not in calls.read_text()


def test_update_refreshes_a_healthy_plugin(tmp_path):
    ctx, calls = _ctx(tmp_path, plugins=HEALTHY_PLUGINS)
    assert ensure_plugin(ctx, "claude", update=True) == "updated"
    lines = calls.read_text().splitlines()
    assert "plugin marketplace update oh-my-clanker" in lines
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin install omc@oh-my-clanker --scope user" not in lines


def test_update_installs_when_missing(tmp_path):
    # `omc update` used to run only `plugin update`, which fails on a plugin
    # that was never installed — update must install, not just refresh.
    ctx, _ = _ctx(tmp_path, plugins=[SUPERPOWERS])
    assert ensure_plugin(ctx, "claude", update=True) == "installed"
    assert "omc@oh-my-clanker" in _state(tmp_path)


def test_check_only_never_installs(tmp_path):
    ctx, calls = _ctx(tmp_path, plugins=[SUPERPOWERS])
    status = ensure_plugin(ctx, "claude", check_only=True)
    assert "missing" in status
    assert "plugin install" not in calls.read_text()


def test_check_only_reports_broken(tmp_path):
    ctx, calls = _ctx(tmp_path, plugins=[{**OMC, "errors": [DEP_ERROR]}, SUPERPOWERS])
    status = ensure_plugin(ctx, "claude", check_only=True)
    assert "failed to load" in status
    assert "plugin install" not in calls.read_text()


def test_install_failure_carries_manual_commands(tmp_path):
    ctx, _ = _ctx(tmp_path, plugins=[SUPERPOWERS], install_rc=1)
    with pytest.raises(OmcError, match="fix manually: claude plugin marketplace add"):
        ensure_plugin(ctx, "claude")


def test_unparseable_plugin_list_is_an_error(tmp_path):
    from ._stubs import make_stub

    make_stub(tmp_path / "bin", "claude", stdout="not json")
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    with pytest.raises(OmcError, match="plugin list --json"):
        ensure_plugin(ctx, "claude")


def test_non_claude_provider_unverified(tmp_path):
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    assert "unverified" in ensure_plugin(ctx, "opencode")


def test_marketplace_source_forms(tmp_path):
    d = tmp_path / "uvt" / "omc"
    d.mkdir(parents=True)
    base = {"UV_TOOL_DIR": str(tmp_path / "uvt"), "HOME": str(tmp_path)}

    (d / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "omc", directory = "/checkout/omc" }]\n'
    )
    assert marketplace_source(base) == "/checkout/omc"

    (d / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "omc", git = "https://github.com/x/omc.git" }]\n'
    )
    assert marketplace_source(base) == "x/omc"

    assert marketplace_source({"HOME": str(tmp_path)}) == "chris-husse/oh-my-clanker"
