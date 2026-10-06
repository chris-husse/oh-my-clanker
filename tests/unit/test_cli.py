import json
from pathlib import Path

import pytest

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


def test_internal_omcerror_is_reported_not_traced(tmp_path, capsys, monkeypatch):
    """`internal` is intercepted before argparse, but it must still sit INSIDE
    main()'s OmcError handler: an expected failure is `error: …` + the error's rc,
    never a traceback (a skill parsing stdout would see a stack dump instead)."""
    import subprocess

    repo = tmp_path / "repo"
    (repo / ".omc").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".omc" / "config.yaml").write_text("worktree: [unclosed\n")  # ConfigError on read
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(repo)

    rc = main(["internal", "rebase-main"])
    err = capsys.readouterr().err
    assert rc == 1  # OmcError.rc, the same code every other command gets
    assert err.startswith("error: ") and "invalid YAML" in err
    assert "Traceback" not in err
    assert "Oh My Clanker" not in err  # still bannerless


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


def test_python_dash_m_omc_is_the_cli():
    import subprocess
    import sys

    cp = subprocess.run(
        [sys.executable, "-m", "omc", "--version"], capture_output=True, text=True, timeout=30
    )
    assert cp.returncode == 0
    assert cp.stdout.startswith("omc ")


def test_title_and_shell_integration_are_banner_exempt():
    import inspect

    from omc.cli import _run  # the banner tuple lives in _run; both names must be exempt

    source = inspect.getsource(_run)
    assert '"title"' in source and '"shell-integration"' in source


def test_shell_integration_is_quiet_unconfigured_and_refuses_with_exit_2(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "omc"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert main(["shell-integration", "fish", "enable"]) == 0
    assert capsys.readouterr() == ("", "")  # no banner, nothing on success
    assert main(["shell-integration", "fish", "status"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["owned"] is True and captured.err == ""
    assert main(["shell-integration", "fish", "disable"]) == 0
    hook = tmp_path / "config" / "fish" / "conf.d" / "omc-title.fish"
    assert not hook.exists()
    hook.write_bytes(b"user content\n")
    assert main(["shell-integration", "fish", "enable"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and str(hook) in err and "Traceback" not in err
    assert hook.read_bytes() == b"user content\n"


def test_readme_documents_every_user_facing_title_surface():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    for needle in (
        "omc shell-integration fish reconcile",
        "omc shell-integration fish disable",
        "omc shell-integration fish status",
        "OMC_FISH_TITLE_DISABLE=1",
        "omc title set -- <branch>",
        "conf.d/omc-title.fish",
        "last writer wins",
        "nested shell",
        "just iterm2-tests",
        "cooldown",
        "bypasses the cooldown",
        "do not abort",
        "· ✗ fish integration: <reason>",
        "the next time the decision changes (branch, repository, or leaving Git)",
        "inside tmux or screen are deliberately inert",
        "waits out the cooldown and then applies",
        "Outside iTerm2, or with `OMC_FISH_TITLE_DISABLE=1`, `omc design` keeps today's behavior",
    ):
        assert needle in readme, needle
    assert "(or with an unauthorized API) `omc start` warns" not in readme
    assert "retries once per failure" not in readme
    assert "retried on the next branch or directory change" not in readme
    assert "if that lands inside the cooldown it is dropped" not in readme
    assert "omc title reconcile" not in readme  # the dropped first attempt's command


def test_design_is_canonical_and_start_is_an_alias():
    from omc.cli import build_parser

    design = build_parser().parse_args(["design", "PROJ-1"])
    start = build_parser().parse_args(["start", "PROJ-1"])
    assert design.command == "design" and design.context == "PROJ-1"
    # argparse stores the TYPED token; dispatch must accept both spellings
    assert start.command == "start" and start.context == "PROJ-1"


def test_start_alias_dispatches_to_run_start(monkeypatch):
    import omc.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "_load_cfg_or_bail", lambda ctx: object())
    monkeypatch.setattr(
        cli, "run_start", lambda ctx, cfg, context, **kw: seen.setdefault("ctx", context) and 0
    )
    assert cli.main(["start", "PROJ-9"]) == 0
    assert cli.main(["design", "PROJ-8"]) == 0
    assert seen["ctx"] == "PROJ-9"


def test_design_help_lists_both_spellings():
    from omc.cli import build_parser

    help_text = build_parser().format_help()
    assert "design" in help_text and "start" in help_text


def test_provider_flags_come_from_the_registry_and_exclude_each_other(capsys):
    from omc.cli import build_parser
    from omc.providers.registry import provider_names

    for name in provider_names():
        args = build_parser().parse_args(["design", "ctx", f"--{name}"])
        assert args.provider_override == name
    assert build_parser().parse_args(["design", "ctx"]).provider_override is None
    assert build_parser().parse_args(["start", "ctx", "--codex"]).provider_override == "codex"
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["design", "ctx", "--claude", "--codex"])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_with_provider_is_a_process_local_copy():
    from omc.cli import _with_provider
    from omc.config.schema import Config, SecretsConfig

    cfg = Config(secrets=SecretsConfig(api_keys={"claude": "k"}))
    same = _with_provider(cfg, None)
    assert same is cfg
    over = _with_provider(cfg, "codex")
    assert over.llm.default == "codex" and cfg.llm.default == "claude"
    assert over.llm.providers is cfg.llm.providers  # shallow: nothing on this path mutates it
    assert over.secrets.api_keys == {"claude": "k"}
    assert "api_keys" not in repr(over)  # SecretsConfig repr=False survives the copy


def test_design_dispatch_applies_the_override(monkeypatch):
    import omc.cli as cli
    from omc.config.schema import Config

    seen = {}
    monkeypatch.setattr(cli, "_load_cfg_or_bail", lambda ctx: Config())
    monkeypatch.setattr(
        cli, "run_start", lambda ctx, cfg, context, **kw: seen.setdefault("cfg", cfg) and 0
    )
    assert cli.main(["design", "PROJ-1", "--codex"]) == 0
    assert seen["cfg"].llm.default == "codex"


def test_implement_parser_and_dispatch(monkeypatch):
    import omc.cli as cli
    from omc.config.schema import Config

    args = cli.build_parser().parse_args(["implement", "--codex", "--dry-run"])
    assert args.command == "implement" and args.provider_override == "codex"
    assert args.dry_run is True and args.headless is False
    seen = {}
    monkeypatch.setattr(cli, "_load_cfg_or_bail", lambda ctx: Config())

    def fake_run_implement(ctx, cfg, *, dry_run, headless):
        seen.update(provider=cfg.llm.default, dry_run=dry_run, headless=headless)
        return 0

    monkeypatch.setattr("omc.implement.run_implement", fake_run_implement)
    assert cli.main(["implement", "--codex", "--dry-run"]) == 0
    assert seen == {"provider": "codex", "dry_run": True, "headless": False}


def test_implement_without_config_bails(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["implement"]) == 2
    assert "omc configure" in capsys.readouterr().err


def test_review_parser_flags_are_registry_generated_and_exclusive(capsys):
    from omc.cli import build_parser
    from omc.providers.registry import provider_names

    for name in provider_names():
        args = build_parser().parse_args(["review", f"--{name}"])
        assert args.provider_override == name
        assert args.dry_run is False and args.headless is False
    args = build_parser().parse_args(["review", "--dry-run", "--headless"])
    assert args.provider_override is None
    assert args.dry_run is True and args.headless is True
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["review", "--claude", "--codex"])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_review_dispatch_applies_run_only_provider_override(monkeypatch):
    import omc.cli as cli
    from omc.config.schema import Config

    cfg = Config()
    seen = {}
    monkeypatch.setattr(cli, "_load_cfg_or_bail", lambda ctx: cfg)

    def fake_run_review(ctx, actual, *, dry_run, headless):
        seen.update(provider=actual.llm.default, dry_run=dry_run, headless=headless)
        return 7

    monkeypatch.setattr("omc.review.run_review", fake_run_review)
    assert cli.main(["review", "--codex", "--dry-run"]) == 7
    assert seen == {"provider": "codex", "dry_run": True, "headless": False}
    assert cfg.llm.default == "claude"


def test_review_without_config_bails(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["review"]) == 2
    assert "omc configure" in capsys.readouterr().err
