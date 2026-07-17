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
