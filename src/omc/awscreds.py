"""`omc aws-credential-process` — a headless AWS credential_process provider.

AWS SDKs invoke the configured command and read credential JSON from stdout
(the `credential_process` contract). This one satisfies an assume-role MFA
requirement with a TOTP code served by 1Password's `op` CLI, so a laptop
signs in with no prompt on any path — ToolContext.run gives children a
DEVNULL stdin, so a tool that would prompt fails fast instead of hanging.

THE CACHE IS OURS BECAUSE AWS DOES NOT CACHE credential_process OUTPUT: the
SDKs spawn the command per client, and without a local session cache every
aws invocation would cost an `op` call and an STS round trip. Sessions land
in ~/.omc/aws-credential-cache/<flag-hash>.json, mode 0600, reused until
close to expiry. Writes are atomic and sign-in is serialized per cache key,
because concurrent SDK clients are the normal case, not the edge case.

stdout is the JSON contract and nothing else — the subcommand is on the
no-banner list in cli/__init__.py, and every diagnostic goes to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .toolctx import ToolContext

# Reuse a cached session only while it has this much life left: a command
# handed a session with seconds to live dies mid-flight, which is worse
# than one extra STS round trip.
EXPIRY_MARGIN = timedelta(minutes=5)
SESSION_NAME = "omc-aws-credential-process"


def _cache_path(args: argparse.Namespace, cache_dir: str | Path) -> Path:
    # Keyed by every flag that changes WHO the session is: two profiles or two
    # roles must never share a cache entry. Hash, not join — ARNs hold '/'.
    # cache_dir is passed in rather than read off args: the default lives on
    # ToolContext.home, and resolving it here would make the key depend on a
    # field the caller may not have filled in yet.
    flags = "\0".join(
        (
            args.source_profile,
            args.role_arn,
            args.mfa_serial,
            args.op_item,
            args.op_vault or "",
            str(args.duration),
        )
    )
    key = hashlib.sha256(flags.encode()).hexdigest()[:16]
    return Path(cache_dir) / f"{key}.json"


def _load_cached(path: Path) -> dict | None:
    """A readable, well-formed, not-near-expiry session — anything else is a miss."""
    try:
        creds = json.loads(path.read_text())
        expiration = datetime.fromisoformat(creds["Expiration"])
        if expiration - EXPIRY_MARGIN <= datetime.now(UTC):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None  # corrupt or unreadable cache is a miss, never an error
    return creds


def _prepare_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)


def _store(path: Path, creds: dict) -> None:
    """Publish a session atomically: a reader sees the old file or the new one.

    An in-place truncate-and-write leaves a window where a concurrent reader
    gets half a JSON object — which _load_cached would charitably treat as a
    miss, sending that caller off to spend another TOTP code. mkstemp lands
    0600 in the destination directory (same filesystem, so os.replace is a
    rename, not a copy) and the rename is what makes the session visible.
    """
    _prepare_dir(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(creds, fh)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)  # never leave a partial session lying in the cache
        raise


def _echo_child_stderr(text: str | None) -> None:
    """Re-emit a child's stderr, newline-terminated.

    A child whose last line is unterminated would otherwise splice with our
    own `error:` line, hiding the word `error` mid-sentence.
    """
    text = text or ""
    if text and not text.endswith("\n"):
        text += "\n"
    sys.stderr.write(text)


def _no_otp(args: argparse.Namespace) -> None:
    print(
        f"error: op could not produce a one-time password for item '{args.op_item}'\n"
        "       (is the 1Password app unlocked and its CLI integration on?"
        " try `op signin`)",
        file=sys.stderr,
    )


def _sign_in(ctx: ToolContext, args: argparse.Namespace) -> dict | None:
    op_argv = ["op", "item", "get", args.op_item, "--otp"]
    if args.op_vault:
        op_argv += ["--vault", args.op_vault]
    cp = ctx.run(op_argv)
    if cp.returncode != 0:
        _echo_child_stderr(cp.stderr)
        _no_otp(args)
        return None
    code = (cp.stdout or "").strip()
    if not code:
        # op exits 0 and prints nothing when the item has no TOTP field. Sending
        # an empty --token-code buys a guaranteed AccessDenied, and that aws
        # error would misdirect the reader to IAM policy instead of the item.
        _no_otp(args)
        return None
    cp = ctx.run(
        [
            "aws",
            "sts",
            "assume-role",
            "--profile",
            args.source_profile,
            "--role-arn",
            args.role_arn,
            "--role-session-name",
            SESSION_NAME,
            "--serial-number",
            args.mfa_serial,
            "--token-code",
            code,
            "--duration-seconds",
            str(args.duration),
            "--output",
            "json",
        ]
    )
    if cp.returncode != 0:
        _echo_child_stderr(cp.stderr)
        print(
            f"error: sts assume-role failed for '{args.role_arn}'\n"
            "       (see the aws error above)",
            file=sys.stderr,
        )
        return None
    try:
        c = json.loads(cp.stdout or "")["Credentials"]
        return {
            "Version": 1,
            "AccessKeyId": c["AccessKeyId"],
            "SecretAccessKey": c["SecretAccessKey"],
            "SessionToken": c["SessionToken"],
            "Expiration": c["Expiration"],
        }
    except (ValueError, KeyError, TypeError):
        print("error: unexpected assume-role output (no Credentials object)", file=sys.stderr)
        return None


def _sign_in_locked(ctx: ToolContext, args: argparse.Namespace, path: Path) -> dict | None:
    """Sign in under an exclusive per-key lock, re-checking the cache first.

    A TOTP code is valid for one 30-second window and STS REJECTS A REUSED MFA
    CODE. Two cold callers racing — the normal case, since SDKs spawn this
    command once per client and a shell can start several at once — would both
    read the same code from op, and whichever assume-role landed second would
    fail. Serializing the whole miss→sign-in→store span means the loser waits
    and then reads the winner's session, spending one code instead of two.

    The lock is only ever held across one op call and one STS call, and flock
    is released by the kernel if the holder dies, so a crash cannot wedge the
    next caller — only a live-but-stuck child can, which is the same exposure
    the un-serialized path already had.
    """
    _prepare_dir(path)
    lock = path.with_suffix(".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Double-check: the winner stored a session while we blocked above, and
        # reading it is the entire point of having waited.
        creds = _load_cached(path)
        if creds is not None:
            return creds
        creds = _sign_in(ctx, args)
        if creds is not None:
            _store(path, creds)
        return creds
    finally:
        os.close(fd)  # closing the descriptor releases the flock


def run_aws_credential_process(ctx: ToolContext, args: argparse.Namespace) -> int:
    cache_dir = args.cache_dir or (ctx.home / "aws-credential-cache")
    path = _cache_path(args, cache_dir)
    # Fast path: a valid session needs no lock and no child process at all.
    creds = _load_cached(path)
    if creds is None:
        creds = _sign_in_locked(ctx, args, path)
        if creds is None:
            return 1
    json.dump(creds, sys.stdout)
    sys.stdout.write("\n")
    return 0
