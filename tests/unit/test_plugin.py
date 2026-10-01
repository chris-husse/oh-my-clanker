import json

import pytest

from omc.errors import OmcError
from omc.plugin import _offered, ensure_plugin, marketplace_source
from omc.toolctx import ToolContext

from ._stubs import HEALTHY_PLUGINS, make_claude_stub, remote_marketplace, stub_env

OMC = {"id": "omc@oh-my-clanker"}
SUPERPOWERS = {"id": "superpowers@claude-plugins-official"}
STALE_MARKETPLACE = {
    "name": "oh-my-clanker",
    "source": "directory",
    "path": "/deleted/worktree",
    "installLocation": "/deleted/worktree",
}
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
    # The 2026-10-01 regression: with omc INSTALLED, Claude's `--available`
    # list no longer contains it, and the live-config proof aborted every
    # `omc update` before `plugin update` ran. The proof must not depend on
    # that list.
    ctx, calls = _ctx(
        tmp_path, plugins=HEALTHY_PLUGINS, marketplaces=[remote_marketplace(tmp_path)]
    )
    assert ensure_plugin(ctx, "claude", update=True) == "updated"
    lines = calls.read_text().splitlines()
    assert "plugin marketplace update oh-my-clanker" in lines
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin install omc@oh-my-clanker --scope user" not in lines


def test_live_proof_reads_the_marketplace_manifest(tmp_path):
    # A marketplace that dropped omc is caught by its manifest — and nothing
    # is updated or removed.
    ctx, calls = _ctx(
        tmp_path,
        plugins=HEALTHY_PLUGINS,
        marketplaces=[remote_marketplace(tmp_path)],
        offers_omc=False,
    )
    with pytest.raises(OmcError, match=r"marketplace at .* does not offer omc@oh-my-clanker"):
        ensure_plugin(ctx, "claude", update=True)
    lines = calls.read_text().splitlines()
    assert "plugin update omc@oh-my-clanker" not in lines
    assert "plugin marketplace remove oh-my-clanker --scope user" not in lines


def test_live_proof_falls_back_to_path(tmp_path):
    # Older registrations carry `path` but no `installLocation` for a
    # directory source; the proof reads the manifest from `path`. Tested on
    # the helper directly: through ensure_plugin this registration would be
    # REPLACED (marketplace_source() falls back to the GitHub source in a
    # stub env), and the stub's fresh registration always has installLocation.
    location = tmp_path / "marketplaces" / "oh-my-clanker"
    registration = {"name": "oh-my-clanker", "source": "directory", "path": str(location)}
    ctx, _ = _ctx(tmp_path, plugins=HEALTHY_PLUGINS, marketplaces=[registration])
    (location / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (location / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "oh-my-clanker", "plugins": [{"name": "omc", "version": "0.1.0"}]})
    )
    assert _offered(ctx) == "0.1.0"


def test_live_proof_without_registration_or_manifest_is_an_error(tmp_path):
    ctx, _ = _ctx(tmp_path, plugins=HEALTHY_PLUGINS)
    with pytest.raises(OmcError, match="not registered"):
        _offered(ctx)
    ctx, _ = _ctx(tmp_path / "second", plugins=HEALTHY_PLUGINS, marketplaces=[STALE_MARKETPLACE])
    with pytest.raises(OmcError, match="cannot read the marketplace manifest"):
        _offered(ctx)
    bare = {"name": "oh-my-clanker", "source": "github", "repo": "chris-husse/oh-my-clanker"}
    ctx, _ = _ctx(tmp_path / "third", plugins=HEALTHY_PLUGINS, marketplaces=[bare])
    with pytest.raises(OmcError, match="no install location"):
        _offered(ctx)


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
    assert "unverified" in ensure_plugin(ctx, "codex")


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


@pytest.mark.parametrize("broken", [False, True])
def test_update_replaces_stale_marketplace_source(tmp_path, broken):
    omc = {**OMC, "errors": ["Marketplace failed to load: cache-miss"]} if broken else OMC
    ctx, calls = _ctx(tmp_path, plugins=[omc, SUPERPOWERS], marketplaces=[STALE_MARKETPLACE])
    assert ensure_plugin(ctx, "claude", update=True) == ("repaired" if broken else "updated")
    entries = json.loads((tmp_path / "bin" / "claude.marketplaces.json").read_text())
    assert entries == [remote_marketplace(tmp_path)]
    assert "errors" not in _state(tmp_path)["omc@oh-my-clanker"]
    lines = calls.read_text().splitlines()
    assert lines.index("isolated plugin install omc@oh-my-clanker --scope user") < lines.index(
        "plugin marketplace remove oh-my-clanker --scope user"
    )
    assert "plugin install omc@oh-my-clanker --scope user" in lines


@pytest.mark.parametrize("failure", ["add", "update"])
def test_marketplace_failure_does_not_uninstall_plugin(tmp_path, failure):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[{**OMC, "errors": [DEP_ERROR]}, SUPERPOWERS],
        marketplaces=[STALE_MARKETPLACE if failure == "add" else remote_marketplace(tmp_path)],
        marketplace_failures={failure: "network unavailable"},
    )
    before = _state(tmp_path)
    with pytest.raises(OmcError, match="network unavailable"):
        ensure_plugin(ctx, "claude", update=True)
    assert _state(tmp_path) == before
    lines = calls.read_text().splitlines()
    assert "plugin uninstall omc@oh-my-clanker" not in lines
    assert "plugin marketplace remove oh-my-clanker --scope user" not in lines


def test_missing_omc_in_replacement_does_not_remove_existing_marketplace(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[OMC, SUPERPOWERS],
        marketplaces=[STALE_MARKETPLACE],
        offers_omc=False,
    )
    with pytest.raises(OmcError, match="omc"):
        ensure_plugin(ctx, "claude", update=True)
    assert (
        "plugin marketplace remove oh-my-clanker --scope user" not in calls.read_text().splitlines()
    )
    assert json.loads((tmp_path / "bin" / "claude.marketplaces.json").read_text()) == [
        STALE_MARKETPLACE
    ]


def test_source_replacement_does_not_refresh_again_after_destructive_remove(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[OMC, SUPERPOWERS],
        marketplaces=[STALE_MARKETPLACE],
        marketplace_failures={"update": "network unavailable"},
    )
    assert ensure_plugin(ctx, "claude", update=True) == "updated"
    assert "omc@oh-my-clanker" in _state(tmp_path)
    assert "plugin marketplace update oh-my-clanker" not in calls.read_text().splitlines()


@pytest.mark.parametrize("settings_name", ["settings.json", "settings.local.json"])
def test_project_marketplace_conflict_preserves_user_registration(tmp_path, settings_name):
    from ._stubs import make_stub

    ctx, calls = _ctx(tmp_path, plugins=[OMC, SUPERPOWERS], marketplaces=[STALE_MARKETPLACE])
    project = tmp_path / "project"
    settings = project / ".claude" / settings_name
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "oh-my-clanker": {
                        "source": {"source": "directory", "path": "/deleted/worktree"}
                    },
                }
            }
        )
    )
    make_stub(tmp_path / "bin", "git", stdout=str(project))
    before = _state(tmp_path)
    with pytest.raises(OmcError, match="project.*marketplace"):
        ensure_plugin(ctx, "claude", update=True)
    assert _state(tmp_path) == before
    assert (
        "plugin marketplace remove oh-my-clanker --scope user" not in calls.read_text().splitlines()
    )
    assert json.loads((tmp_path / "bin" / "claude.marketplaces.json").read_text()) == [
        STALE_MARKETPLACE
    ]


def test_matching_local_declaration_overrides_shared_project_source(tmp_path):
    from ._stubs import make_stub

    ctx, _ = _ctx(tmp_path, plugins=[OMC, SUPERPOWERS], marketplaces=[STALE_MARKETPLACE])
    project = tmp_path / "project"
    config = project / ".claude"
    config.mkdir(parents=True)
    for name, source in (
        ("settings.json", {"source": "directory", "path": "/deleted/worktree"}),
        ("settings.local.json", {"source": "github", "repo": "chris-husse/oh-my-clanker"}),
    ):
        (config / name).write_text(
            json.dumps(
                {
                    "extraKnownMarketplaces": {
                        "oh-my-clanker": {"source": source},
                    }
                }
            )
        )
    make_stub(tmp_path / "bin", "git", stdout=str(project))
    assert ensure_plugin(ctx, "claude", update=True) == "updated"


def test_replacement_plugin_install_failure_preserves_existing_plugin(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[OMC, SUPERPOWERS],
        marketplaces=[STALE_MARKETPLACE],
        install_rc=1,
    )
    before = _state(tmp_path)
    with pytest.raises(OmcError, match="install failed"):
        ensure_plugin(ctx, "claude", update=True)
    assert _state(tmp_path) == before
    assert (
        "plugin marketplace remove oh-my-clanker --scope user" not in calls.read_text().splitlines()
    )


STALE_OMC = {**OMC, "version": "0.1.11"}


def test_update_advances_a_stale_plugin(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[STALE_OMC, SUPERPOWERS],
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude", update=True) == "updated (0.1.11 → 0.1.13)"
    lines = calls.read_text().splitlines()
    assert "plugin marketplace update oh-my-clanker" in lines
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin uninstall omc@oh-my-clanker" not in lines  # stale is never the reinstall path
    assert _state(tmp_path)["omc@oh-my-clanker"]["version"] == "0.1.13"


@pytest.mark.parametrize(("installed", "offered"), [("0.1.11", "0.1.13"), ("0.1.13", "0.1.9")])
def test_update_that_does_not_move_the_version_fails_loud(tmp_path, installed, offered):
    # Claude says ok and changes nothing (the shape `omc update` lied about
    # for three days) — and the downgrade pair: "different" is skew too.
    ctx, calls = _ctx(
        tmp_path,
        plugins=[{**OMC, "version": installed}, SUPERPOWERS],
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version=offered,
        update_moves_version=False,
    )
    with pytest.raises(OmcError) as info:
        ensure_plugin(ctx, "claude", update=True)
    # The update ran and did nothing — not skipped.
    assert "plugin update omc@oh-my-clanker" in calls.read_text().splitlines()
    message = str(info.value)
    assert f"still {installed}" in message
    assert f"{offered} offered" in message
    assert "claude plugin update omc@oh-my-clanker" in message


def test_stale_plugin_is_healed_on_start_path(tmp_path, capsys):
    # No update flag: this is what `omc start` and `omc configure` call.
    ctx, calls = _ctx(
        tmp_path,
        plugins=[STALE_OMC, SUPERPOWERS],
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude") == "updated (0.1.11 → 0.1.13)"
    lines = calls.read_text().splitlines()
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin install omc@oh-my-clanker --scope user" not in lines
    assert "stale" in capsys.readouterr().err


def test_check_only_reports_stale_without_mutating(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[STALE_OMC, SUPERPOWERS],
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version="0.1.13",
    )
    status = ensure_plugin(ctx, "claude", check_only=True)
    assert status.startswith("stale (0.1.11 → 0.1.13 offered")
    recorded = calls.read_text()
    assert "plugin update" not in recorded
    assert "plugin install" not in recorded
    assert "plugin marketplace update" not in recorded


def test_current_plugin_is_ok_without_update_commands(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[{**OMC, "version": "0.1.13"}, SUPERPOWERS],
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin update" not in calls.read_text()


def test_unregistered_marketplace_is_not_skew(tmp_path):
    # omc installed, no oh-my-clanker registration (e.g. installed under
    # another marketplace name): plumbing is never skew, start proceeds.
    ctx, calls = _ctx(tmp_path, plugins=[STALE_OMC, SUPERPOWERS], offered_version="0.1.13")
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin update" not in calls.read_text()


@pytest.mark.parametrize("check_only", [False, True])
def test_fork_installed_omc_is_not_compared_with_the_canonical_marketplace(tmp_path, check_only):
    # omc@<other marketplace> installed AND oh-my-clanker registered: the
    # canonical manifest says nothing about the fork's version, so no skew,
    # no `plugin update omc@oh-my-clanker` (which Claude would reject).
    fork = {"id": "omc@my-fork", "version": "0.1.11"}
    ctx, calls = _ctx(
        tmp_path,
        plugins=[fork, SUPERPOWERS],
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude", check_only=check_only) == "ok"
    assert "plugin update" not in calls.read_text()


def test_unreadable_manifest_is_not_skew_in_dry_run(tmp_path):
    # Registered, but the directory is gone (stale worktree) while the plugin
    # still loads: dry-run says ok rather than crashing over plumbing.
    ctx, _ = _ctx(tmp_path, plugins=[STALE_OMC, SUPERPOWERS], marketplaces=[STALE_MARKETPLACE])
    assert ensure_plugin(ctx, "claude", check_only=True) == "ok"


@pytest.mark.parametrize("side", ["installed", "offered"])
def test_unknown_versions_are_not_skew(tmp_path, side):
    plugins = [{**OMC, "version": ""} if side == "installed" else STALE_OMC, SUPERPOWERS]
    ctx, calls = _ctx(
        tmp_path,
        plugins=plugins,
        marketplaces=[remote_marketplace(tmp_path)],
        offered_version=None if side == "offered" else "0.1.13",
    )
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin update" not in calls.read_text()
