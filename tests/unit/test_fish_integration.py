"""Managed fish hook lifecycle: owned by prefix, refuses foreign files, spares config.fish."""

import json
from pathlib import Path

import pytest

from omc.cli import main
from omc.errors import Refusal
from omc.fish_integration import (
    HOOK_PREFIX,
    fish_hook_path,
    is_owned,
    managed_fish_path,
    remove_owned_hook,
    run_fish_integration,
)
from omc.toolctx import ToolContext


def _ctx(tmp_path, **extra):
    return ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "omc"), **extra})


def test_packaged_asset_is_the_hook_and_carries_the_prefix():
    asset = fish_hook_path()
    assert asset.name == "omc-title.fish" and asset.parent.name == "assets"
    assert asset.read_text().splitlines()[0].startswith(HOOK_PREFIX + " v")


def test_managed_hook_lifecycle(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    assert target == tmp_path / ".config" / "fish" / "conf.d" / "omc-title.fish"
    config = target.parent.parent / "config.fish"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"# my fish settings\n")
    assert run_fish_integration(ctx, "enable") == 0
    first = target.read_bytes()
    assert first == fish_hook_path().read_bytes()
    assert run_fish_integration(ctx, "reconcile") == 0
    assert target.read_bytes() == first  # byte-idempotent
    assert config.read_bytes() == b"# my fish settings\n"
    assert run_fish_integration(ctx, "status") == 0
    status = json.loads(capsys.readouterr().out)
    assert status == {"enabled": True, "installed": True, "path": str(target), "owned": True}
    assert run_fish_integration(ctx, "disable") == 0
    assert not target.exists()
    assert (ctx.home / "integrations" / "fish-title.disabled").exists()
    assert run_fish_integration(ctx, "reconcile") == 0
    assert not target.exists()  # reconcile honors the opt-out
    assert run_fish_integration(ctx, "status") == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    assert run_fish_integration(ctx, "enable") == 0
    assert target.read_bytes() == first
    assert not (ctx.home / "integrations" / "fish-title.disabled").exists()


def test_header_bump_never_orphans_an_earlier_copy(tmp_path):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"# omc-managed fish title integration v1\nfunction old; end\n")
    assert is_owned(target)
    assert run_fish_integration(ctx, "reconcile") == 0
    assert target.read_bytes() == fish_hook_path().read_bytes()


def test_xdg_config_home_absolute_only(tmp_path):
    assert managed_fish_path(_ctx(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "my config"))) == (
        tmp_path / "my config" / "fish" / "conf.d" / "omc-title.fish"
    )
    assert managed_fish_path(_ctx(tmp_path, XDG_CONFIG_HOME="relative/config")) == (
        tmp_path / ".config" / "fish" / "conf.d" / "omc-title.fish"
    )
    no_home = ToolContext.from_env({"OMC_HOME": str(tmp_path / "omc")})
    assert (
        managed_fish_path(no_home) == Path.home() / ".config" / "fish" / "conf.d" / "omc-title.fish"
    )


@pytest.mark.parametrize("action", ["enable", "reconcile"])
def test_unowned_file_and_symlink_are_refused_untouched(tmp_path, action):
    ctx = _ctx(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "my config"))
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my hook\n")
    with pytest.raises(Refusal, match=str(target)):
        run_fish_integration(ctx, action)
    assert target.read_bytes() == b"my hook\n"
    target.unlink()
    target.symlink_to(tmp_path / "absent")
    with pytest.raises(Refusal, match="symlink"):
        run_fish_integration(ctx, action)
    assert target.is_symlink()
    assert not is_owned(target)


def _symlinked_fish_dir(tmp_path):
    """~/.config/fish -> a dotfiles checkout, as stow/chezmoi/yadm setups lay it out."""
    fish_dir = tmp_path / ".config" / "fish"
    fish_dir.parent.mkdir()
    real = tmp_path / "dotfiles" / "fish"
    real.mkdir(parents=True)
    fish_dir.symlink_to(real)
    return real


@pytest.mark.parametrize("action", ["enable", "reconcile"])
def test_symlinked_parent_is_written_through(tmp_path, action):
    ctx = _ctx(tmp_path)
    real = _symlinked_fish_dir(tmp_path)
    assert run_fish_integration(ctx, action) == 0
    landed = real / "conf.d" / "omc-title.fish"
    assert landed.read_bytes() == fish_hook_path().read_bytes()
    assert not landed.is_symlink() and is_owned(managed_fish_path(ctx))


def test_disable_under_symlinked_parent_records_opt_out(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    real = _symlinked_fish_dir(tmp_path)
    assert run_fish_integration(ctx, "enable") == 0
    assert run_fish_integration(ctx, "disable") == 0
    assert not (real / "conf.d" / "omc-title.fish").exists()
    assert (ctx.home / "integrations" / "fish-title.disabled").exists()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["unowned", "symlink"])
def test_disable_over_a_foreign_target_records_opt_out_with_one_note(tmp_path, capsys, kind):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    if kind == "unowned":
        target.write_bytes(b"my hook\n")
    else:
        target.symlink_to(tmp_path / "absent")
    assert run_fish_integration(ctx, "disable") == 0
    assert (ctx.home / "integrations" / "fish-title.disabled").exists()
    if kind == "unowned":
        assert target.read_bytes() == b"my hook\n"
    else:
        assert target.is_symlink()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1 and str(target) in captured.err


def test_reconcile_while_opted_out_leaves_a_foreign_file(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    assert run_fish_integration(ctx, "disable") == 0
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my hook\n")
    assert run_fish_integration(ctx, "reconcile") == 0
    assert target.read_bytes() == b"my hook\n"
    assert capsys.readouterr().err.count("\n") <= 1
    target.unlink()
    target.symlink_to(tmp_path / "absent")
    assert run_fish_integration(ctx, "reconcile") == 0
    assert target.is_symlink()


def test_status_never_refuses(tmp_path, capsys):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my hook\n")
    assert run_fish_integration(ctx, "status") == 0
    status = json.loads(capsys.readouterr().out)
    assert status["installed"] is True and status["owned"] is False


def test_remove_owned_hook_is_non_blocking(tmp_path):
    ctx = _ctx(tmp_path)
    target = managed_fish_path(ctx)
    assert remove_owned_hook(ctx) is None  # absent
    assert run_fish_integration(ctx, "enable") == 0
    assert remove_owned_hook(ctx) is None
    assert not target.exists()
    target.write_bytes(b"my hook\n")
    note = remove_owned_hook(ctx)
    assert note is not None and str(target) in note and "not an omc-owned file" in note
    assert target.read_bytes() == b"my hook\n"
    target.unlink()
    target.symlink_to(tmp_path / "absent")
    assert remove_owned_hook(ctx) is not None
    assert target.is_symlink()


def test_owned_hook_removal_permission_error_is_an_error(tmp_path, monkeypatch, capsys):
    ctx = _ctx(tmp_path)
    assert run_fish_integration(ctx, "enable") == 0
    target = managed_fish_path(ctx)
    original = target.read_bytes()
    real_unlink = Path.unlink

    def deny_target(path, *args, **kwargs):
        if path == target:
            raise PermissionError("simulated read-only fish directory")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_target)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(ctx.home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert main(["shell-integration", "fish", "disable"]) == 1
    assert target.read_bytes() == original
    assert "fish integration removal failed" in capsys.readouterr().err
    assert remove_owned_hook(ctx) is not None  # uninstall path: a note, not an exception


def test_enable_flag_removal_permission_error_is_reported(tmp_path, monkeypatch, capsys):
    ctx = _ctx(tmp_path)
    assert run_fish_integration(ctx, "disable") == 0
    flag = ctx.home / "integrations" / "fish-title.disabled"
    real_unlink = Path.unlink

    def deny_flag(path, *args, **kwargs):
        if path == flag:
            raise PermissionError("simulated read-only omc integrations directory")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_flag)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(ctx.home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert main(["shell-integration", "fish", "enable"]) == 1
    assert managed_fish_path(ctx).exists()
    assert flag.exists()
    assert "could not clear fish disable" in capsys.readouterr().err
