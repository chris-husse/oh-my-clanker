from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import store
from .errors import OmcError
from .start import run_start
from .toolctx import ToolContext

_CONFIGURE_HINT = "run `omc configure` first"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omc", description="Oh My Clanker!")
    parser.add_argument("--version", action="version", version=f"omc {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="Print version + install source")

    p_conf = sub.add_parser("configure", help="Pick your LLM; writes ~/.omc/config.json")
    p_conf.add_argument("--defaults", action="store_true", help="Write defaults, no prompts")
    p_conf.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a dotted key non-interactively (repeatable)",
    )

    p_start = sub.add_parser("start", help="Begin work on a ticket / task description")
    p_start.add_argument("context", help="Ticket key, ticket URL, or quoted task description")
    p_start.add_argument("--dry-run", action="store_true", help="Print the plan, change nothing")
    p_start.add_argument("--headless", action="store_true", help="Print-mode session (no exec)")

    p_install = sub.add_parser("install", help="(Re)install omc from a local checkout")
    p_install.add_argument("path", nargs="?", default=".", help="Checkout path (default: .)")

    sub.add_parser("update", help="Update omc from the source it was installed from")
    sub.add_parser("uninstall", help="Remove omc (binary + ~/.omc)")

    return parser


def _load_cfg_or_bail(ctx: ToolContext):
    cfg = store.load(ctx.home)
    if cfg is None:
        print(f"error: omc is not configured — {_CONFIGURE_HINT}.", file=sys.stderr)
        return None
    return cfg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help(sys.stderr)
        return 2
    ctx = ToolContext.from_env()
    if args.command != "version":
        print(f"Oh My Clanker! v{__version__}", file=sys.stderr)
    try:
        return _dispatch(ctx, args)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.rc


def _dispatch(ctx: ToolContext, args: argparse.Namespace) -> int:
    if args.command == "version":
        from .installsrc import version_string

        print(version_string(ctx.env))
        return 0
    if args.command == "start":
        cfg = _load_cfg_or_bail(ctx)
        if cfg is None:
            return 2
        return run_start(ctx, cfg, args.context, dry_run=args.dry_run, headless=args.headless)
    if args.command == "configure":
        from .configure import run_configure

        return run_configure(ctx, defaults=args.defaults, sets=args.set)
    if args.command == "install":
        from .installer import run_install

        return run_install(ctx, args.path)
    if args.command == "update":
        from .installer import run_update

        return run_update(ctx)
    if args.command == "uninstall":
        from .installer import run_uninstall

        return run_uninstall(ctx)
    raise OmcError(f"unknown command {args.command!r}")
