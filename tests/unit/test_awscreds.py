import argparse
import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from omc.awscreds import _cache_path, _store, run_aws_credential_process
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env

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


def _args(tmp_path, **over):
    base = dict(
        source_profile="base",
        role_arn="arn:aws:iam::123456789012:role/Dev",
        mfa_serial="arn:aws:iam::123456789012:mfa/cli",
        op_item="item123",
        op_vault=None,
        duration=43200,
        cache_dir=str(tmp_path / "cache"),
    )
    base.update(over)
    return argparse.Namespace(**base)


def _ctx(bindir):
    return ToolContext.from_env(stub_env(bindir))


def test_fresh_signin_emits_contract_json_and_caches(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["Version"] == 1
    assert out["AccessKeyId"] == "ASIAEXAMPLE"
    assert out["Expiration"] == "2099-01-01T00:00:00+00:00"
    files = list((tmp_path / "cache").iterdir())
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_valid_cache_short_circuits_op_and_aws(tmp_path, capsys):
    bindir = tmp_path / "bin"
    # Both stubs FAIL: a cache hit must never invoke them.
    make_stub(bindir, "op", rc=1)
    make_stub(bindir, "aws", rc=1)
    args = _args(tmp_path)
    cached = {
        "Version": 1,
        "AccessKeyId": "CACHED",
        "SecretAccessKey": "s",
        "SessionToken": "t",
        "Expiration": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    }
    _store(_cache_path(args), cached)
    rc = run_aws_credential_process(_ctx(bindir), args)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "CACHED"


def test_near_expiry_cache_is_a_miss(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    args = _args(tmp_path)
    _store(
        _cache_path(args),
        {
            "Version": 1,
            "AccessKeyId": "STALE",
            "SecretAccessKey": "s",
            "SessionToken": "t",
            "Expiration": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
        },
    )
    rc = run_aws_credential_process(_ctx(bindir), args)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "ASIAEXAMPLE"


def test_corrupt_cache_is_a_miss_not_an_error(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    args = _args(tmp_path)
    path = _cache_path(args)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert run_aws_credential_process(_ctx(bindir), args) == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "ASIAEXAMPLE"


def test_op_failure_names_remedy_and_emits_nothing(tmp_path, capsys):
    bindir = tmp_path / "bin"
    # op writes its diagnosis to stderr; ours must carry it through, not swallow it.
    make_stub(bindir, "op", stderr="[ERROR] account is not signed in", rc=1)
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""  # stdout is the JSON contract: nothing on failure
    assert "[ERROR] account is not signed in" in captured.err
    assert "one-time password" in captured.err
    assert "op signin" in captured.err


def test_aws_failure_reemits_stderr_and_emits_nothing(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    aws_error = "An error occurred (AccessDenied) when calling the AssumeRole operation"
    make_stub(bindir, "aws", stderr=aws_error, rc=254)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    # "see the aws error above" is only true if the aws error is actually above it.
    assert aws_error in captured.err
    assert "assume-role failed" in captured.err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("source_profile", "other-profile"),
        ("role_arn", "arn:aws:iam::123456789012:role/Other"),
        ("mfa_serial", "arn:aws:iam::123456789012:mfa/other"),
        ("op_item", "other-item"),
        ("op_vault", "Private"),
        ("duration", 3600),
    ],
)
def test_cache_key_depends_on_flags(tmp_path, flag, value):
    # Every flag that changes WHO the session is must change the cache key —
    # a collision would hand one role's caller another role's credentials.
    base = _cache_path(_args(tmp_path))
    assert _cache_path(_args(tmp_path, **{flag: value})) != base


def test_op_vault_flag_reaches_op(tmp_path, capsys):
    bindir = tmp_path / "bin"
    # The op stub records its argv so the test can assert --vault went through.
    argv_log = tmp_path / "op-argv.txt"
    bindir.mkdir(parents=True, exist_ok=True)
    op = bindir / "op"
    op.write_text(f'#!/bin/sh\necho "$@" > {argv_log}\necho 123456\n')
    op.chmod(op.stat().st_mode | stat.S_IXUSR)
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path, op_vault="Private"))
    assert rc == 0
    assert "--vault Private" in argv_log.read_text()
