import os
import stat

import pytest

from omc.config import store
from omc.config.schema import GlobalConfig, ProviderConfig
from omc.errors import OmcError
from omc.installer import run_install, run_uninstall, run_update, validate_checkout
from omc.toolctx import ToolContext

from ._stubs import (
    CODEX_MKT_FALLBACK,
    CODEX_OMC,
    CODEX_SUPERPOWERS,
    HEALTHY_PLUGINS,
    make_claude_stub,
    make_codex_stub,
    make_stub,
    stub_env,
)

# A codex that is already registered and healthy, so `run_update` reaches
# ensure_plugin's UPDATE path rather than its install/repair path. The
# marketplace fixture carries codex's own normalised spelling of the fallback
# repo (measured — see _stubs.CODEX_MKT_FALLBACK); the pretty `owner/repo` form
# would read as a different source and make every run report "repaired".
_HEALTHY_CODEX = {
    "plugins": [CODEX_OMC, CODEX_SUPERPOWERS],
    "marketplaces": [CODEX_MKT_FALLBACK],
}


@pytest.fixture(autouse=True)
def _no_real_gitnexus(monkeypatch):
    # Installer tests exercise require_tools + the plugin loop, not the real
    # clone/build. Keep update_gitnexus a no-op success here. A per-test
    # monkeypatch.setattr overrides this autouse default.
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 0)


def _checkout(tmp_path):
    root = tmp_path / "co"
    (root / ".git").mkdir(parents=True)
    (root / "src" / "omc").mkdir(parents=True)
    (root / "src" / "omc" / "__init__.py").write_text("")
    return root


def test_validate_checkout(tmp_path):
    assert validate_checkout(str(tmp_path)) is not None  # not a checkout
    assert validate_checkout(str(_checkout(tmp_path))) is None


def test_install_bad_path_no_uv_call(tmp_path, capsys):
    bindir = tmp_path / "bin"
    calls_file = bindir / "uv.calls"
    make_stub(bindir, "uv")
    ctx = ToolContext.from_env(stub_env(bindir))
    assert run_install(ctx, str(tmp_path / "nope")) == 1
    assert not calls_file.exists()


def test_install_good_path_calls_uv(tmp_path):
    bindir = tmp_path / "bin"
    make_stub(bindir, "uv", stdout="ok")
    ctx = ToolContext.from_env(stub_env(bindir))
    assert run_install(ctx, str(_checkout(tmp_path))) == 0


def test_update_calls_uv_upgrade(tmp_path):
    bindir = tmp_path / "bin"
    make_stub(bindir, "uv", stdout="ok")
    ctx = ToolContext.from_env(stub_env(bindir))
    assert run_update(ctx) == 0


def test_run_update_combines_uv_and_dependency_refresh(monkeypatch, tmp_path):
    from omc import installer
    from omc.toolctx import ToolContext

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "home")})
    seen = []
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: seen.append(("uv", a)) or 0)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: seen.append(("dep",)) or 0)
    assert installer.run_update(ctx) == 0
    assert ("uv", ("tool", "upgrade", "omc")) in seen and ("dep",) in seen


def test_run_update_fails_if_dependency_refresh_fails(monkeypatch, tmp_path):
    from omc import installer
    from omc.toolctx import ToolContext

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "home")})
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: 0)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 1)
    assert installer.run_update(ctx) == 1


def test_uninstall_removes_home_but_refuses_unsafe(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "uv", stdout="ok")
    home = tmp_path / "omchome"
    home.mkdir()
    (home / "config.yaml").write_text("schema_version: 1\n")
    env = stub_env(bindir, OMC_HOME=str(home))
    assert run_uninstall(ToolContext.from_env(env)) == 0
    assert not home.exists()
    # unsafe home ($HOME itself) is refused but uninstall still proceeds
    env2 = stub_env(bindir, OMC_HOME=str(tmp_path))
    env2["HOME"] = str(tmp_path)
    assert run_uninstall(ToolContext.from_env(env2)) == 0
    assert tmp_path.exists()
    assert "refuse" in capsys.readouterr().err


def _stub(bindir, name, rc=0):
    calls = bindir / f"{name}.calls"
    exe = bindir / name
    # --version always succeeds (require_tools probe); other subcommands use rc.
    exe.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\n'
        f'case "$1" in --version) exit 0 ;; *) exit {rc} ;; esac\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return calls


def _update_ctx(tmp_path, *, plugins=HEALTHY_PLUGINS, install_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv_calls = _stub(bindir, "uv")
    claude_calls = make_claude_stub(bindir, plugins=plugins, install_rc=install_rc)
    codex_calls = make_codex_stub(bindir, **_HEALTHY_CODEX)
    _stub(bindir, "wt")  # require_tools probes git/wt/provider
    _stub(bindir, "git")  # deterministic --version for the probe
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    cfg = GlobalConfig()
    cfg.llm.providers = {"claude": ProviderConfig(), "codex": ProviderConfig()}
    store.save_global(ctx.home, cfg)
    return ctx, uv_calls, claude_calls, codex_calls


def test_update_upgrades_then_updates_each_providers_plugin(tmp_path, capsys):
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(tmp_path)
    assert run_update(ctx) == 0
    assert "tool upgrade omc" in uv_calls.read_text()
    assert "plugin marketplace update oh-my-clanker" in claude_calls.read_text()
    assert "plugin update omc@oh-my-clanker" in claude_calls.read_text()
    # THE USER-VISIBLE FIX: codex is no longer routed down a name fork that
    # only refreshed the marketplace snapshot — it goes through ensure_plugin,
    # which re-adds the plugin from the refreshed snapshot and re-probes it.
    recorded = codex_calls.read_text()
    assert "plugin marketplace upgrade" in recorded
    assert "plugin add omc@oh-my-clanker" in recorded
    assert "✓ codex: omc plugin updated" in capsys.readouterr().err


def test_update_isolates_provider_failures(tmp_path, capsys):
    # omc plugin missing and `claude plugin install` fails: narrated once, and
    # the other providers still get their turn.
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(
        tmp_path, plugins=[{"id": "superpowers@claude-plugins-official"}], install_rc=1
    )
    # Best-effort applies to the LOOP, not the command: the other providers
    # still get their turn, but a left-broken plugin must not report success.
    # `omc update` exiting 0 here is how codex's local-source refresh window
    # (remove, then a failed re-add when the checkout moved) stayed invisible.
    assert run_update(ctx) == 1
    # `marketplace upgrade` only appears in codex's UPDATE branch, so this
    # proves codex reached ensure_plugin(update=True) after claude blew up.
    assert "plugin marketplace upgrade" in codex_calls.read_text()
    err = capsys.readouterr().err
    assert "claude" in err
    # ONE ✗ for the whole failing provider: ensure_plugin raises once, and the
    # best-effort (fatal=False) steps ahead of the failing install never
    # narrate a failure of their own.
    assert err.count("✗") == 1


def test_update_aborts_when_required_tool_missing(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    _stub(bindir, "claude")  # default provider present…
    _stub(bindir, "git")
    # Enforce the ordering invariant: require_tools must gate BEFORE the
    # GitNexus install, so update_gitnexus must never run on the abort path.
    monkeypatch.setattr(
        "omc.gitnexus.update_gitnexus",
        lambda ctx: pytest.fail(
            "update_gitnexus ran despite the missing-tool abort — "
            "require_tools must gate BEFORE the GitNexus install"
        ),
    )
    # …but wt points at a nonexistent binary → require_tools raises. (A bare
    # `wt` would resolve to a real install on a dev machine's PATH.)
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {
            "HOME": str(tmp_path),
            "OMC_HOME": str(home),
            "OMC_WT_BIN": str(bindir / "no-such-wt"),
            "PATH": f"{bindir}:{os.environ['PATH']}",
        }
    )
    cfg = GlobalConfig()
    cfg.llm.providers = {"claude": ProviderConfig()}
    store.save_global(ctx.home, cfg)
    with pytest.raises(OmcError) as exc:
        run_update(ctx)
    assert "wt" in str(exc.value)


def test_update_without_config_skips_plugins(tmp_path, capsys):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    ctx = ToolContext.from_env(
        {
            "HOME": str(tmp_path),
            "OMC_HOME": str(tmp_path / "omc-home"),
            "PATH": f"{bindir}:{os.environ['PATH']}",
        }
    )
    assert run_update(ctx) == 0
    assert "skipping plugin updates" in capsys.readouterr().err


def test_update_registers_marketplace_before_updating(tmp_path):
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(tmp_path)
    assert run_update(ctx) == 0
    recorded = claude_calls.read_text()
    assert "plugin marketplace add" in recorded  # self-heal registration
    assert "plugin marketplace update oh-my-clanker" in recorded
    assert "plugin update omc@oh-my-clanker" in recorded
    # "before updating" is the point of this test — assert order, not just presence.
    assert recorded.index("plugin marketplace add") < recorded.index(
        "plugin update omc@oh-my-clanker"
    )


def test_update_isolates_unknown_provider(tmp_path, capsys):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    codex_calls = make_codex_stub(bindir, **_HEALTHY_CODEX)
    # require_tools probes the DEFAULT provider (claude) + git/wt, not the
    # configured (unknown) providers — stub those so the probe passes and the
    # test still exercises unknown-provider isolation in the plugin loop.
    _stub(bindir, "claude")
    _stub(bindir, "wt")
    _stub(bindir, "git")
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    # Config with unknown provider FIRST, then codex
    cfg = GlobalConfig()
    cfg.llm.providers = {"nonexistent-provider": ProviderConfig(), "codex": ProviderConfig()}
    store.save_global(ctx.home, cfg)
    # rc 1, but the loop is still isolated: a typo'd provider in the config is
    # a real failure the user should see, not something to swallow silently.
    assert run_update(ctx) == 1
    assert "plugin marketplace upgrade" in codex_calls.read_text()  # codex still ran
    err = capsys.readouterr().err
    assert "✗" in err and "nonexistent-provider" in err  # failure narrated


def test_update_installs_a_missing_plugin(tmp_path, capsys):
    # Before: `omc update` only ran `plugin update`, which fails when the
    # plugin was never installed — a fresh machine stayed without /omc:*.
    ctx, _, claude_calls, _ = _update_ctx(tmp_path, plugins=[])
    assert run_update(ctx) == 0
    recorded = claude_calls.read_text().splitlines()
    assert "plugin install superpowers@claude-plugins-official --scope user" in recorded
    assert "plugin install omc@oh-my-clanker --scope user" in recorded
    assert "✓ claude: omc plugin installed" in capsys.readouterr().err


def test_update_reinstalls_a_plugin_that_fails_to_load(tmp_path, capsys):
    broken = [
        {"id": "omc@oh-my-clanker", "errors": ['Dependency "superpowers@x" is not installed']},
        {"id": "superpowers@claude-plugins-official"},
    ]
    ctx, _, claude_calls, _ = _update_ctx(tmp_path, plugins=broken)
    assert run_update(ctx) == 0
    recorded = claude_calls.read_text().splitlines()
    assert (
        recorded.index("plugin marketplace update oh-my-clanker")
        < recorded.index("plugin uninstall omc@oh-my-clanker")
        < recorded.index("plugin install omc@oh-my-clanker --scope user")
    )
    assert "✓ claude: omc plugin repaired" in capsys.readouterr().err


def test_update_uses_ensure_plugin_for_every_provider(monkeypatch, tmp_path, capsys):
    """run_update must not branch on provider name — every configured provider
    goes through ensure_plugin, and a failure in one never aborts the rest."""
    import omc.installer as installer
    from omc.providers.registry import get_provider

    seen = []

    def fake_ensure(ctx, name, *, check_only=False, update=False):
        seen.append((name, update))
        if name == "codex":
            raise OmcError("boom")
        if name == "opencode":
            # What ensure_plugin really returns for a provider with no
            # scriptable probe — the prefix drives the "·" mark.
            return "unverified (no scriptable check for this provider yet)"
        return "updated"

    monkeypatch.setattr(installer, "ensure_plugin", fake_ensure)
    # The provider-name fork is gone, and with it the member it dispatched on.
    assert not hasattr(get_provider("claude"), "plugin_update_argvs")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    _stub(bindir, "claude")  # require_tools probes git/wt/provider
    _stub(bindir, "wt")
    _stub(bindir, "git")
    ctx = ToolContext.from_env(
        {
            "HOME": str(tmp_path),
            "OMC_HOME": str(tmp_path / "omc-home"),
            "PATH": f"{bindir}:{os.environ['PATH']}",
        }
    )
    cfg = GlobalConfig()
    cfg.llm.providers = {
        "claude": ProviderConfig(),
        "codex": ProviderConfig(),
        "opencode": ProviderConfig(),
    }
    store.save_global(ctx.home, cfg)

    # rc 1 because codex was left broken; the loop is unaffected, which is what
    # this test is really about (see `seen` below).
    assert run_update(ctx) == 1
    # update=True for all three, in config order — codex raising did not stop
    # the loop before opencode got its turn.
    assert seen == [("claude", True), ("codex", True), ("opencode", True)]
    err = capsys.readouterr().err
    assert "✗ codex: boom — continuing" in err
    assert "✓ claude: omc plugin updated" in err
    # The display contract: only an "unverified" status renders "·".
    assert "· opencode: omc plugin unverified" in err
    assert err.count("✗") == 1
