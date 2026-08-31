import argparse
import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from omc import awscreds
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


def _path(args):
    """The session file for these args — cache_dir is the caller's, as in run()."""
    return _cache_path(args, args.cache_dir)


def _ctx(bindir):
    return ToolContext.from_env(stub_env(bindir))


def _fresh(**over):
    creds = {
        "Version": 1,
        "AccessKeyId": "CACHED",
        "SecretAccessKey": "s",
        "SessionToken": "t",
        "Expiration": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    }
    creds.update(over)
    return creds


def test_fresh_signin_emits_contract_json_and_caches(tmp_path, capsys):
    bindir = tmp_path / "bin"
    aws_log = tmp_path / "aws-argv.txt"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON, argv_log=aws_log)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["Version"] == 1
    assert out["AccessKeyId"] == "ASIAEXAMPLE"
    assert out["Expiration"] == "2099-01-01T00:00:00+00:00"
    session = _path(_args(tmp_path))
    # Exactly the session and its lock: no half-written .tmp survives a store.
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == [
        session.name,
        session.with_suffix(".lock").name,
    ]
    assert stat.S_IMODE(session.stat().st_mode) == 0o600
    # The MFA identity has to reach STS, or the role is assumed without it.
    argv = aws_log.read_text()
    assert "sts assume-role" in argv
    assert "--profile base" in argv
    assert "--role-arn arn:aws:iam::123456789012:role/Dev" in argv
    assert "--serial-number arn:aws:iam::123456789012:mfa/cli" in argv
    assert "--token-code 123456" in argv
    assert "--duration-seconds 43200" in argv


def test_default_cache_dir_is_under_omc_home(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path, cache_dir=None))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "ASIAEXAMPLE"
    # stub_env pins HOME to bindir.parent, so ~/.omc resolves inside tmp_path.
    cache = tmp_path / ".omc" / "aws-credential-cache"
    assert sorted(p.suffix for p in cache.iterdir()) == [".json", ".lock"]


def test_valid_cache_short_circuits_op_and_aws(tmp_path, capsys):
    bindir = tmp_path / "bin"
    # Both stubs FAIL: a cache hit must never invoke them.
    make_stub(bindir, "op", rc=1)
    make_stub(bindir, "aws", rc=1)
    args = _args(tmp_path)
    _store(_path(args), _fresh())
    rc = run_aws_credential_process(_ctx(bindir), args)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "CACHED"


def test_near_expiry_cache_is_a_miss(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    args = _args(tmp_path)
    _store(
        _path(args),
        _fresh(
            AccessKeyId="STALE",
            Expiration=(datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
        ),
    )
    rc = run_aws_credential_process(_ctx(bindir), args)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "ASIAEXAMPLE"


def test_corrupt_cache_is_a_miss_not_an_error(tmp_path, capsys):
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", stdout="123456")
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    args = _args(tmp_path)
    path = _path(args)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert run_aws_credential_process(_ctx(bindir), args) == 0
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "ASIAEXAMPLE"


def test_store_is_atomic_and_leaves_no_temp_file(tmp_path):
    args = _args(tmp_path)
    path = _path(args)
    _store(path, _fresh(AccessKeyId="FIRST"))
    _store(path, _fresh(AccessKeyId="SECOND"))  # overwrite must not truncate in place
    assert json.loads(path.read_text())["AccessKeyId"] == "SECOND"
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_store_failure_leaves_the_previous_session_intact(tmp_path, monkeypatch):
    """A write that dies mid-flight must not destroy the session already on disk.

    This is the property truncate-in-place cannot offer: there the old bytes are
    gone the instant the file is opened, so a crash mid-write leaves every later
    caller with a corrupt cache — a miss, and another TOTP code spent.
    """
    path = _path(_args(tmp_path))
    _store(path, _fresh(AccessKeyId="FIRST"))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(awscreds.json, "dump", boom)
    with pytest.raises(OSError, match="disk full"):
        _store(path, _fresh(AccessKeyId="SECOND"))
    monkeypatch.undo()
    assert json.loads(path.read_text())["AccessKeyId"] == "FIRST"
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # temp cleaned up


def test_lock_double_check_reuses_the_winners_session(tmp_path, capsys, monkeypatch):
    """A racer that loses the lock must read the winner's session, not sign in again.

    Two real processes cannot be raced deterministically here, so this drives the
    interleaving directly: _load_cached misses on the check before the lock and
    hits on the double-check under it, which is exactly what a loser observes when
    the winner stores while it blocks on flock. Both CLI stubs exit non-zero, so
    any attempt to sign in anyway fails the run — and the code the winner already
    spent could not be spent twice regardless (STS rejects a reused MFA code).
    """
    bindir = tmp_path / "bin"
    make_stub(bindir, "op", rc=1)
    make_stub(bindir, "aws", rc=1)
    winner = _fresh(AccessKeyId="WINNER")
    calls = []

    def fake_load(path):
        calls.append(path)
        return None if len(calls) == 1 else winner

    monkeypatch.setattr(awscreds, "_load_cached", fake_load)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path))
    assert rc == 0
    assert len(calls) == 2  # once before the lock, once under it
    assert json.loads(capsys.readouterr().out)["AccessKeyId"] == "WINNER"


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


def test_empty_otp_is_an_error_and_never_calls_aws(tmp_path, capsys):
    bindir = tmp_path / "bin"
    aws_log = tmp_path / "aws-argv.txt"
    make_stub(bindir, "op", stdout="   ")  # exit 0, no code: the item has no TOTP field
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON, argv_log=aws_log)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "one-time password" in captured.err
    assert not aws_log.exists()  # no STS round trip on a known-bad code


@pytest.mark.parametrize("text", ["unterminated", "terminated\n", "two\nlines"])
def test_child_stderr_is_always_newline_terminated(text, capsys):
    # Driven directly, not through a stub: make_stub's heredoc always appends a
    # newline, so an unterminated child stderr is unreachable from a stub script.
    awscreds._echo_child_stderr(text)
    err = capsys.readouterr().err
    assert err.endswith("\n")  # our error: line must start at column 0
    assert err.rstrip("\n") == text.rstrip("\n")  # and nothing else is altered


def test_child_stderr_says_nothing_when_the_child_said_nothing(capsys):
    awscreds._echo_child_stderr(None)
    awscreds._echo_child_stderr("")
    assert capsys.readouterr().err == ""  # no blank line padding a silent child


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
    assert captured.err.index(aws_error) < captured.err.index("error: sts assume-role failed")


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
    base = _path(_args(tmp_path))
    assert _path(_args(tmp_path, **{flag: value})) != base


def test_op_vault_flag_reaches_op(tmp_path, capsys):
    bindir = tmp_path / "bin"
    argv_log = tmp_path / "op-argv.txt"
    make_stub(bindir, "op", stdout="123456", argv_log=argv_log)
    make_stub(bindir, "aws", stdout=_ASSUME_ROLE_JSON)
    rc = run_aws_credential_process(_ctx(bindir), _args(tmp_path, op_vault="Private"))
    assert rc == 0
    assert "--vault Private" in argv_log.read_text()
