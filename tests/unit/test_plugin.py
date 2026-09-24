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


# --- codex: the same self-heal, driven through codex's own CLI -----------------


def _codex_ctx(tmp_path, **kw):
    from ._stubs import make_codex_stub

    calls = make_codex_stub(tmp_path / "bin", **kw)
    return ToolContext.from_env(stub_env(tmp_path / "bin")), calls


def _codex_state(tmp_path):
    return {
        e["pluginId"]: e for e in json.loads((tmp_path / "bin" / "codex.plugins.json").read_text())
    }


def test_codex_fresh_install_registers_omc_and_superpowers(tmp_path, capsys):
    ctx, calls = _codex_ctx(tmp_path)
    # "installed", not "installed superpowers": codex plans superpowers FIRST
    # (as claude does) so ensure_plugin's last-action-wins status names omc's
    # own plugin — a fresh machine reporting only superpowers read as though
    # omc's plugin never installed.
    assert ensure_plugin(ctx, "codex") == "installed"
    recorded = calls.read_text()
    assert "plugin marketplace add" in recorded
    assert "plugin add omc@oh-my-clanker" in recorded
    assert "plugin add superpowers@superpowers-marketplace" in recorded
    state = _codex_state(tmp_path)
    assert "omc@oh-my-clanker" in state
    assert "superpowers@superpowers-marketplace" in state
    assert "installing the omc plugin" in capsys.readouterr().err


def test_codex_healthy_is_left_alone(tmp_path):
    from ._stubs import CODEX_MKT_FALLBACK, CODEX_OMC, CODEX_SUPERPOWERS

    # CODEX_MKT_FALLBACK's source is what marketplace_source(env) resolves to
    # under stub_env (no uv receipt -> the fallback repo), so this is the true
    # no-op case rather than a different-source conflict.
    ctx, calls = _codex_ctx(
        tmp_path, plugins=[CODEX_OMC, CODEX_SUPERPOWERS], marketplaces=[CODEX_MKT_FALLBACK]
    )
    assert ensure_plugin(ctx, "codex") == "ok"
    recorded = calls.read_text()
    assert "plugin add" not in recorded
    assert "marketplace remove" not in recorded


def test_codex_conflicting_marketplace_source_is_reregistered(tmp_path, capsys):
    from ._stubs import CODEX_MKT_LOCAL, CODEX_OMC, CODEX_SUPERPOWERS

    # Registered from /old/checkout while omc resolves the fallback repo.
    ctx, calls = _codex_ctx(
        tmp_path,
        plugins=[CODEX_OMC, CODEX_SUPERPOWERS],
        marketplaces=[CODEX_MKT_LOCAL],
    )
    assert ensure_plugin(ctx, "codex") == "repaired"
    lines = calls.read_text().splitlines()
    remove_at = lines.index("plugin marketplace remove oh-my-clanker")
    add_at = next(i for i, ln in enumerate(lines) if ln.startswith("plugin marketplace add"))
    omc_at = lines.index("plugin add omc@oh-my-clanker")
    assert remove_at < add_at < omc_at
    # THE REGRESSION THIS GUARDS: the marketplace removal wipes the plugin, so
    # without the `or conflict` clause codex would end up with no omc plugin.
    assert "omc@oh-my-clanker" in _codex_state(tmp_path)
    assert "re-registering" in capsys.readouterr().err


def test_codex_superpowers_from_another_marketplace_satisfies(tmp_path):
    from ._stubs import CODEX_MKT_FALLBACK, CODEX_OMC

    curated = {"pluginId": "superpowers@openai-curated-remote", "name": "superpowers",
               "marketplaceName": "openai-curated-remote"}  # fmt: skip
    ctx, calls = _codex_ctx(
        tmp_path, plugins=[CODEX_OMC, curated], marketplaces=[CODEX_MKT_FALLBACK]
    )
    assert ensure_plugin(ctx, "codex") == "ok"
    assert "plugin add" not in calls.read_text()


def test_codex_check_only_never_mutates(tmp_path):
    ctx, calls = _codex_ctx(tmp_path)
    status = ensure_plugin(ctx, "codex", check_only=True)
    assert "missing" in status
    recorded = calls.read_text()
    assert "plugin add" not in recorded
    assert "marketplace add" not in recorded


def test_codex_check_only_reports_a_marketplace_conflict(tmp_path):
    from ._stubs import CODEX_MKT_LOCAL, CODEX_OMC, CODEX_SUPERPOWERS

    # Everything the facts-only description looked at is healthy: omc loads,
    # superpowers is present. Only the REGISTERED SOURCE disagrees (/old/checkout
    # while omc resolves the fallback repo) — and a real `omc start` therefore
    # runs marketplace remove -> add -> plugin add and reports "repaired".
    # --dry-run promises "what omc start WOULD do", so it must not say "ok".
    ctx, calls = _codex_ctx(
        tmp_path,
        plugins=[CODEX_OMC, CODEX_SUPERPOWERS],
        marketplaces=[CODEX_MKT_LOCAL],
    )
    status = ensure_plugin(ctx, "codex", check_only=True)
    assert status != "ok"
    assert "/old/checkout" in status and "re-register" in status
    # ...while still mutating nothing: only the two read-only probes ran.
    assert calls.read_text().splitlines() == [
        "plugin marketplace list --json",
        "plugin list --json",
    ]


def test_codex_disabled_plugin_is_repaired(tmp_path):
    from ._stubs import CODEX_MKT_FALLBACK, CODEX_OMC, CODEX_SUPERPOWERS

    ctx, _ = _codex_ctx(
        tmp_path,
        plugins=[{**CODEX_OMC, "enabled": False}, CODEX_SUPERPOWERS],
        marketplaces=[CODEX_MKT_FALLBACK],
    )
    assert ensure_plugin(ctx, "codex") == "repaired"
    assert _codex_state(tmp_path)["omc@oh-my-clanker"]["enabled"] is True


def test_codex_add_failure_carries_manual_commands(tmp_path):
    ctx, _ = _codex_ctx(tmp_path, add_rc=1)
    with pytest.raises(OmcError, match="fix manually: codex plugin marketplace add"):
        ensure_plugin(ctx, "codex")


def test_codex_empty_probe_output_raises_before_mutating_anything(tmp_path):
    from ._stubs import CODEX_MKT_FALLBACK, make_codex_stub

    # A codex whose `plugin list --json` prints NOTHING is a broken probe, not
    # an empty state. Masking it (reading "" as "nothing installed") made omc
    # run four mutating commands and only then fail with the misleading "still
    # missing after an apparently successful install"; the parser now raises, so
    # _probe reports it and omc touches nothing.
    calls = make_codex_stub(
        tmp_path / "bin", marketplaces=[CODEX_MKT_FALLBACK], blank_plugin_list=True
    )
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    match = r"could not parse the output of .*`codex plugin list --json`"
    with pytest.raises(OmcError, match=match) as info:
        ensure_plugin(ctx, "codex")
    # The diagnostic names EVERY probe and quotes EACH output, because
    # parse_plugin_facts consumed them all at once and cannot say which one
    # broke. Blaming argvs[0] named the marketplace probe and quoted its
    # HEALTHY payload — the empty output must be attributable to its command.
    message = str(info.value)
    assert "`codex plugin list --json` printed: ''" in message
    assert '`codex plugin marketplace list --json` printed: \'{"marketplaces"' in message
    # THE POINT: an unreadable probe must never mutate the harness.
    recorded = calls.read_text()
    assert "plugin add" not in recorded
    assert "marketplace add" not in recorded
    assert "marketplace remove" not in recorded
    # Only the two read-only probes ran.
    assert recorded.splitlines() == ["plugin marketplace list --json", "plugin list --json"]
