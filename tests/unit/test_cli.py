import json
from pathlib import Path

from omc.cli import main

from ._stubs import make_stub

_ASSUME_ROLE_JSON = json.dumps(
    {
        "Credentials": {
            "AccessKeyId": "ASIAEXAMPLE",
            "SecretAccessKey": "secret/Example+Key",
            "SessionToken": "token/Example+Tok==",
            "Expiration": "2099-01-01T00:00:00+00:00",
        }
    }
)


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


def test_watch_rebase_flag_default_off():
    from omc.cli import build_parser

    assert build_parser().parse_args(["watch"]).rebase is False
    assert build_parser().parse_args(["watch", "--rebase"]).rebase is True


def test_gate_hints_legacy_migration(tmp_path, monkeypatch, capsys):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home.mkdir(parents=True)
    (home / "config.json").write_text('{"schema_version": 1}')
    assert main(["start", "PROJ-1"]) == 2
    err = capsys.readouterr().err
    assert "legacy" in err and "config.json" in err


def test_print_install_path_is_machine_pure(capsys):
    rc = main(["print-install-path"])
    assert rc == 0
    out = capsys.readouterr()
    assert out.err == ""  # banner-exempt, like version
    lines = out.out.splitlines()
    assert len(lines) == 1  # exactly one line: OMC_PATH=$(omc print-install-path)
    assert (Path(lines[0]) / "distribution" / "AGENTS.md").is_file()


def test_aws_credential_process_is_registered():
    from omc.cli import build_parser

    assert "aws-credential-process" in build_parser().format_help()


def test_aws_credential_process_runs_bannerless_and_needs_no_config(tmp_path, capsys, monkeypatch):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(bindir))
    rc = main(
        [
            "aws-credential-process",
            "--source-profile",
            "base",
            "--role-arn",
            "arn:aws:iam::123456789012:role/Dev",
            "--mfa-serial",
            "arn:aws:iam::123456789012:mfa/cli",
            "--op-item",
            "item123",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    out = json.loads(captured.out)
    assert out["Version"] == 1
    assert "Oh My Clanker!" not in captured.err  # bannerless: stdout is the JSON contract


def test_service_account_token_flag_is_optional_and_defaults_to_none():
    """The flag goes into an operator's ~/.aws/config line — its spelling is a contract.

    Optional in both directions: omitting it must keep parsing (every existing
    credential_process line stays valid), and its dest is what awscreds reads.
    """
    from omc.cli import build_parser

    base = [
        "aws-credential-process",
        "--source-profile",
        "base",
        "--role-arn",
        "arn:aws:iam::123456789012:role/Dev",
        "--mfa-serial",
        "arn:aws:iam::123456789012:mfa/cli",
        "--op-item",
        "item123",
    ]
    parser = build_parser()
    assert parser.parse_args(base).with_service_account_token is None
    args = parser.parse_args([*base, "--with-service-account-token", "/etc/op/token"])
    assert args.with_service_account_token == "/etc/op/token"
