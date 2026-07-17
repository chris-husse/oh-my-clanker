import stat

import pytest

from omc.config.schema import Config
from omc.errors import OmcError
from omc.plugin import ensure_plugin, marketplace_source
from omc.toolctx import ToolContext

from ._stubs import stub_env


def make_plugin_stub(bindir, *, installed_initially: bool, install_rc: int = 0):
    """A stateful claude stub: `plugin list` reports omc@ only after `plugin
    install` ran (or from the start); records every argv line."""
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "claude.calls"
    marker = bindir / "installed.marker"
    if installed_initially:
        marker.write_text("")
    script = f"""#!/bin/sh
echo "$@" >> "{calls}"
case "$1 $2" in
  "plugin list")
    echo "Installed plugins:"
    if [ -f "{marker}" ]; then echo "  omc@oh-my-clanker"; fi
    ;;
  "plugin marketplace") echo "added" ;;
  "plugin install")
    if [ "{install_rc}" -eq 0 ]; then : > "{marker}"; echo "installed"; fi
    exit {install_rc}
    ;;
esac
exit 0
"""
    path = bindir / "claude"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return calls


def test_present_plugin_is_left_alone(tmp_path):
    calls = make_plugin_stub(tmp_path / "bin", installed_initially=True)
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    assert ensure_plugin(ctx, Config()) == "ok"
    assert "plugin install" not in calls.read_text()


def test_missing_plugin_self_heals(tmp_path, capsys):
    calls = make_plugin_stub(tmp_path / "bin", installed_initially=False)
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    assert ensure_plugin(ctx, Config()) == "installed"
    lines = calls.read_text().splitlines()
    add_at = next(i for i, ln in enumerate(lines) if ln.startswith("plugin marketplace add"))
    install_at = next(i for i, ln in enumerate(lines) if ln.startswith("plugin install"))
    assert add_at < install_at
    assert "plugin install omc@oh-my-clanker --scope user" in lines[install_at]
    assert "installing the omc plugin" in capsys.readouterr().err


def test_check_only_never_installs(tmp_path):
    calls = make_plugin_stub(tmp_path / "bin", installed_initially=False)
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    status = ensure_plugin(ctx, Config(), check_only=True)
    assert "missing" in status
    assert "plugin install" not in calls.read_text()


def test_install_failure_carries_manual_commands(tmp_path):
    make_plugin_stub(tmp_path / "bin", installed_initially=False, install_rc=1)
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    with pytest.raises(OmcError, match="fix manually: claude plugin marketplace add"):
        ensure_plugin(ctx, Config())


def test_non_claude_provider_unverified(tmp_path):
    cfg = Config()
    cfg.llm.default = "opencode"
    ctx = ToolContext.from_env(stub_env(tmp_path / "bin"))
    assert "unverified" in ensure_plugin(ctx, cfg)


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
