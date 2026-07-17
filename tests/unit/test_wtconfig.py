from omc.toolctx import ToolContext
from omc.wtconfig import WT_TEMPLATE, ensure_wt_config


def _ctx(tmp_path):
    return ToolContext.from_env({"HOME": str(tmp_path)})


def test_creates_starter_when_absent(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    status = ensure_wt_config(_ctx(tmp_path), root)
    assert status == "created"
    written = (root / ".config" / "wt.toml").read_text()
    assert written == WT_TEMPLATE
    assert "copy-ignored" in written
    assert "exclude" not in written  # the snapshot model copies EVERYTHING
    assert "wt.toml" in capsys.readouterr().err


def test_existing_config_with_copy_ignored_is_ok(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    (root / ".config" / "wt.toml").write_text(
        '[post-start]\ncopy-ignored = "wt step copy-ignored"\n'
    )
    assert ensure_wt_config(_ctx(tmp_path), root) == "ok"
    assert capsys.readouterr().err == ""  # silent when fine


def test_existing_config_without_copy_is_flagged_never_edited(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    original = '[post-start]\nnotify = "echo hi"\n'
    (root / ".config" / "wt.toml").write_text(original)
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert (root / ".config" / "wt.toml").read_text() == original  # NEVER edited
    assert "/omc:check-wt-config" in capsys.readouterr().err


def test_unparseable_config_is_flagged_never_edited(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / ".config").mkdir(parents=True)
    (root / ".config" / "wt.toml").write_text("not [ valid toml")
    assert ensure_wt_config(_ctx(tmp_path), root) == "suspicious"
    assert (root / ".config" / "wt.toml").read_text() == "not [ valid toml"
    assert "/omc:check-wt-config" in capsys.readouterr().err
