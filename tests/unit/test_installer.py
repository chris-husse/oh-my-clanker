import os
import stat

import pytest

from omc.config import store
from omc.config.schema import GlobalConfig, ProviderConfig
from omc.errors import OmcError
from omc.installer import run_install, run_uninstall, run_update, validate_checkout
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env


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


def _update_ctx(tmp_path, *, claude_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv_calls = _stub(bindir, "uv")
    claude_calls = _stub(bindir, "claude", rc=claude_rc)
    codex_calls = _stub(bindir, "codex")
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
    assert "plugin marketplace upgrade" in codex_calls.read_text()


def test_update_isolates_provider_failures(tmp_path, capsys):
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(tmp_path, claude_rc=1)
    assert run_update(ctx) == 0  # a broken provider never fails the update
    assert "plugin marketplace upgrade" in codex_calls.read_text()  # codex still ran
    err = capsys.readouterr().err
    assert "claude" in err
    assert err.count("✗") == 1  # only the FINAL argv decides pass/fail — benign
    # marketplace add/update failures must not each print their own ✗


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
    codex_calls = _stub(bindir, "codex")
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
    assert run_update(ctx) == 0  # Must succeed despite unknown provider
    assert "plugin marketplace upgrade" in codex_calls.read_text()  # codex still ran
    err = capsys.readouterr().err
    assert "✗" in err and "nonexistent-provider" in err  # failure narrated
