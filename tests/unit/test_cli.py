from pathlib import Path

from omc.cli import main


def test_no_command_shows_help(capsys):
    assert main([]) == 2


def test_start_without_config_bails(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["start", "PROJ-1"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "omc configure" in err


def test_version_runs(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OMC_HOME", str(tmp_path))
    assert main(["version"]) == 0
    assert "omc" in capsys.readouterr().out


def test_internal_is_hidden_and_intercepted(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OMC_HOME", str(tmp_path))
    assert main(["internal", "wt-template"]) == 0
    captured = capsys.readouterr()
    assert "copy-ignored" in captured.out
    assert "Oh My Clanker" not in captured.err  # no banner on internal


def test_watch_without_config_bails(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["watch", "--once"]) == 2
    assert "omc configure" in capsys.readouterr().err


def test_watch_default_interval_is_30s():
    from omc.cli import build_parser

    args = build_parser().parse_args(["watch"])
    assert args.interval == 30


def test_print_install_path_is_machine_pure(capsys):
    rc = main(["print-install-path"])
    assert rc == 0
    out = capsys.readouterr()
    assert out.err == ""  # banner-exempt, like version
    lines = out.out.splitlines()
    assert len(lines) == 1  # exactly one line: OMC_PATH=$(omc print-install-path)
    assert (Path(lines[0]) / "distribution" / "AGENTS.md").is_file()
