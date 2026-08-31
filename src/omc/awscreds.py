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
close to expiry.

stdout is the JSON contract and nothing else — the subcommand is on the
no-banner list in cli/__init__.py, and every diagnostic goes to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .toolctx import ToolContext

# Reuse a cached session only while it has this much life left: a command
# handed a session with seconds to live dies mid-flight, which is worse
# than one extra STS round trip.
EXPIRY_MARGIN = timedelta(minutes=5)
SESSION_NAME = "omc-aws-credential-process"


def _cache_path(args: argparse.Namespace) -> Path:
    # Keyed by every flag that changes WHO the session is: two profiles or two
    # roles must never share a cache entry. Hash, not join — ARNs hold '/'.
    # args.cache_dir is always set by the time this runs (the entry point
    # fills the ~/.omc default in before anything reads it).
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
    return Path(args.cache_dir) / f"{key}.json"


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


def _store(path: Path, creds: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    # O_CREAT with 0o600 so the file is never readable by others, not even
    # between create and chmod.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(creds, fh)


def _sign_in(ctx: ToolContext, args: argparse.Namespace) -> dict | None:
    op_argv = ["op", "item", "get", args.op_item, "--otp"]
    if args.op_vault:
        op_argv += ["--vault", args.op_vault]
    cp = ctx.run(op_argv)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr or "")
        print(
            f"error: op could not produce a one-time password for item '{args.op_item}'\n"
            "       (is the 1Password app unlocked and its CLI integration on?"
            " try `op signin`)",
            file=sys.stderr,
        )
        return None
    code = (cp.stdout or "").strip()
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
        sys.stderr.write(cp.stderr or "")
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


def run_aws_credential_process(ctx: ToolContext, args: argparse.Namespace) -> int:
    if not args.cache_dir:
        args.cache_dir = str(ctx.home / "aws-credential-cache")
    path = _cache_path(args)
    creds = _load_cached(path)
    if creds is None:
        creds = _sign_in(ctx, args)
        if creds is None:
            return 1
        _store(path, creds)
    json.dump(creds, sys.stdout)
    sys.stdout.write("\n")
    return 0
