import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from omc.config import store
from omc.config.schema import GlobalConfig, ProviderConfig
from omc.errors import ConfigError, OmcError
from omc.installer import run_install, run_uninstall, run_update, validate_checkout
from omc.toolctx import ToolContext

from ._stubs import HEALTHY_PLUGINS, make_claude_stub, make_stub, stub_env


@pytest.fixture(autouse=True)
def _no_real_gitnexus(monkeypatch):
    # Installer tests exercise require_tools + the plugin loop, not the real
    # clone/build. Keep update_gitnexus a no-op success here. A per-test
    # monkeypatch.setattr overrides this autouse default.
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 0)
    # Legacy cases model uv/dependency behaviour only; the fish cases below opt
    # back into the macOS provisioning path explicitly.
    monkeypatch.setattr("omc.installer._is_macos", lambda: False)


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
    # omc plugin missing and `claude plugin install` fails: narrated once, and
    # the other providers still get their turn.
    ctx, uv_calls, claude_calls, codex_calls = _update_ctx(
        tmp_path, plugins=[{"id": "superpowers@claude-plugins-official"}], install_rc=1
    )
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


def test_update_rejects_unsupported_provider_config(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uv")
    codex_calls = _stub(bindir, "codex")
    home = tmp_path / "omc-home"
    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"}
    )
    home.mkdir()
    (home / "config.yaml").write_text("llm:\n  providers:\n    retired: {}\n    codex: {}\n")
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        run_update(ctx)
    assert not codex_calls.exists()  # no plugin side effects from invalid config


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


def _fresh_ctx(
    tmp_path,
    monkeypatch,
    *,
    bin_link=True,
    reconcile_rc=0,
    reconcile_err="",
    timeout=False,
    version="omc 9.9.9\n",
    version_kwargs=None,
):
    """A ctx whose uv answers `tool dir --bin`/`tool dir` and whose fresh omc is recorded."""
    from omc import installer

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc")})
    bin_dir = tmp_path / "uv bin"
    tool_dir = tmp_path / "uv tools"
    (tool_dir / "omc" / "bin").mkdir(parents=True)
    (tool_dir / "omc" / "bin" / "omc").write_text("#!/bin/sh\n")
    bin_dir.mkdir()
    if bin_link:
        (bin_dir / "omc").write_text("#!/bin/sh\n")
    calls = []

    def run(argv, **kwargs):
        calls.append(("run", list(argv)))
        if argv[:3] == ["uv", "tool", "dir"]:
            out = str(bin_dir) if "--bin" in argv else str(tool_dir)
            return SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")
        if argv[1:] == ["--version"]:
            if version_kwargs is not None:
                version_kwargs.append(kwargs)
            if isinstance(version, BaseException):
                raise version
            return SimpleNamespace(returncode=0, stdout=version, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run_bounded(argv, *, timeout, cwd=None, extra_env=None):
        calls.append(("bounded", list(argv), timeout))
        if timeout_flag[0]:
            raise TimeoutError("command timed out after 30s")
        return subprocess.CompletedProcess(list(argv), reconcile_rc, "", reconcile_err)

    timeout_flag = [timeout]
    monkeypatch.setattr(ctx, "run", run)
    monkeypatch.setattr(ctx, "run_bounded", run_bounded)
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: calls.append(("uv", a)) or 0)
    monkeypatch.setattr(installer, "_is_macos", lambda: True)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: calls.append(("dep",)) or 0)
    expected_exe = bin_dir / "omc" if bin_link else tool_dir / "omc" / "bin" / "omc"
    return ctx, calls, expected_exe


def test_update_runs_fresh_cli_reconcile_between_uv_and_gates(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch)
    assert installer.run_update(ctx) == 0
    assert calls[0] == ("uv", ("tool", "upgrade", "omc"))
    assert calls[1] == ("run", ["uv", "tool", "dir", "--bin"])
    assert calls[2] == ("run", [str(exe), "--version"])
    assert calls[3] == ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30)
    assert ("dep",) in calls and calls.index(("dep",)) > 3
    err = capsys.readouterr().err
    assert err.index("→ provisioning fish integration") < err.index(
        "✓ omc 9.9.9 updated · ✓ fish integration"
    )


def test_version_probe_is_bounded_at_five_seconds(tmp_path, monkeypatch):
    from omc import installer

    seen = []
    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, version_kwargs=seen)
    assert installer.run_update(ctx) == 0
    assert len(seen) == 1 and seen[0].get("timeout") == 5


@pytest.mark.parametrize(
    "version",
    [
        subprocess.TimeoutExpired(["omc", "--version"], 5),
        PermissionError("not executable"),
        "omc \n",
        "omc\n",
    ],
    ids=["timeout", "oserror", "omc-space", "omc-bare"],
)
def test_unusable_version_probe_degrades_to_no_version(tmp_path, monkeypatch, capsys, version):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch, version=version)
    assert installer.run_update(ctx) == 0
    assert ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30) in calls
    assert capsys.readouterr().err.rstrip().splitlines()[-1] == (
        "✓ omc updated · ✓ fish integration"
    )


def test_update_falls_back_to_tool_dir_when_bin_link_missing(tmp_path, monkeypatch):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch, bin_link=False)
    assert installer.run_update(ctx) == 0
    assert ("run", ["uv", "tool", "dir"]) in calls
    assert ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30) in calls


def test_fish_failure_never_aborts_update_and_is_reported_distinctly(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(
        tmp_path,
        monkeypatch,
        reconcile_rc=2,
        reconcile_err="error: fish integration: /x is not omc-owned\n",
    )
    assert installer.run_update(ctx) == 1  # every later gate succeeded → the fish outcome decides
    assert ("dep",) in calls  # the remaining gates ran
    err = capsys.readouterr().err
    assert "error: fish integration: /x is not omc-owned" in err  # relayed verbatim
    assert (
        err.rstrip().splitlines()[-1]
        == "✓ omc 9.9.9 updated · ✗ fish integration: reconcile exit 2"
    )


def test_fish_timeout_is_a_reason_not_an_abort(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, timeout=True)
    assert installer.run_update(ctx) == 1
    assert ("dep",) in calls
    assert "✗ fish integration: timed out after 30s" in capsys.readouterr().err


def test_dependency_failure_outranks_fish_outcome(tmp_path, monkeypatch):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, reconcile_rc=1)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 3)
    assert installer.run_update(ctx) == 3


def test_missing_fresh_cli_is_a_reason(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch, bin_link=False)
    (tmp_path / "uv tools" / "omc" / "bin" / "omc").unlink()
    assert installer.run_update(ctx) == 1
    assert not any(c[0] == "bounded" for c in calls)
    assert "✗ fish integration: installed omc executable not found" in capsys.readouterr().err


def test_non_macos_skips_the_fish_step_entirely(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, _ = _fresh_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "_is_macos", lambda: False)
    assert installer.run_update(ctx) == 0
    assert not any(c[0] in ("run", "bounded") for c in calls)
    err = capsys.readouterr().err
    assert "provisioning fish" not in err and err.rstrip().splitlines()[-1] == "✓ omc updated"


def test_install_runs_post_step_and_reports(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx, calls, exe = _fresh_ctx(tmp_path, monkeypatch, reconcile_rc=1)
    assert installer.run_install(ctx, str(_checkout(tmp_path))) == 1
    assert calls[0][0] == "uv" and calls[0][1][:3] == ("tool", "install", "--reinstall")
    assert ("bounded", [str(exe), "shell-integration", "fish", "reconcile"], 30) in calls
    captured = capsys.readouterr()
    assert "re-rooted future `omc update`s" in captured.out
    assert (
        captured.err.rstrip().splitlines()[-1]
        == "✓ omc 9.9.9 installed · ✗ fish integration: reconcile exit 1"
    )


def test_uninstall_removes_only_an_owned_hook_and_never_blocks(tmp_path, monkeypatch, capsys):
    from omc import installer
    from omc.fish_integration import managed_fish_path, run_fish_integration

    ctx = ToolContext.from_env(
        {"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc"), "SHELL": "/bin/zsh"}
    )
    monkeypatch.setattr(installer, "_is_macos", lambda: True)
    uv_calls = []
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: uv_calls.append(a) or 0)
    ctx.home.mkdir()
    assert run_fish_integration(ctx, "enable") == 0
    hook = managed_fish_path(ctx)
    assert installer.run_uninstall(ctx) == 0
    assert not hook.exists() and not ctx.home.exists()
    assert uv_calls == [("tool", "uninstall", "omc")]
    err = capsys.readouterr().err
    assert "fish shells already running keep the loaded title hook" in err
    # unowned file: left in place with one note; uninstall still completes
    hook.write_bytes(b"user content\n")
    ctx.home.mkdir()
    assert installer.run_uninstall(ctx) == 0
    assert hook.read_bytes() == b"user content\n" and not ctx.home.exists()
    err = capsys.readouterr().err
    assert err.count("· fish integration: left") == 1
    # symlink: same
    hook.unlink()
    hook.symlink_to(tmp_path / "absent")
    assert installer.run_uninstall(ctx) == 0
    assert hook.is_symlink()


def test_uninstall_on_non_macos_removes_an_owned_hook_only(tmp_path, monkeypatch, capsys):
    from omc import installer
    from omc.fish_integration import managed_fish_path, run_fish_integration

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc")})
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: 0)
    assert run_fish_integration(ctx, "enable") == 0  # a Linux user who opted in by hand
    hook = managed_fish_path(ctx)
    assert installer.run_uninstall(ctx) == 0
    assert not hook.exists()
    assert "fish shells already running keep the loaded title hook" in capsys.readouterr().err
    hook.write_bytes(b"user content\n")
    assert installer.run_uninstall(ctx) == 0
    assert hook.read_bytes() == b"user content\n"
    err = capsys.readouterr().err
    assert err.count("· fish integration: left") == 1 and "already running" not in err


def test_uninstall_without_a_hook_says_nothing_about_fish(tmp_path, monkeypatch, capsys):
    from omc import installer

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc")})
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: 0)
    assert installer.run_uninstall(ctx) == 0
    assert "fish" not in capsys.readouterr().err
