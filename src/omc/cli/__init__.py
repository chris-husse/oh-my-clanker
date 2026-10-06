from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .. import __version__
from ..config import resolve, store
from ..errors import OmcError
from ..providers.registry import provider_names
from ..start import run_start
from ..toolctx import ToolContext

_CONFIGURE_HINT = "run `omc configure` first"


def _add_provider_flags(parser: argparse.ArgumentParser) -> None:
    """One boolean flag per registered provider (--claude, --codex, ...), mutually
    exclusive: use that provider for THIS run. Registry-generated so a new
    provider gets its flag for free; the set is validated by construction."""
    group = parser.add_mutually_exclusive_group()
    for name in provider_names():
        group.add_argument(
            f"--{name}",
            dest="provider_override",
            action="store_const",
            const=name,
            help=f"Use {name} for this run only (the saved default is untouched)",
        )
    parser.set_defaults(provider_override=None)


def _with_provider(cfg, override: str | None):
    """The effective config with llm.default swapped for this process. A
    dataclasses.replace copy: it SHARES llm.providers and secrets with the
    original (nothing on the design/implement path mutates them) and is never
    persisted - Config is a runtime composite that no save path accepts."""
    if not override:
        return cfg
    return replace(cfg, llm=replace(cfg.llm, default=override))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omc", description="Oh My Clanker!")
    parser.add_argument("--version", action="version", version=f"omc {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="Print version + install source")

    sub.add_parser(
        "print-install-path", help="Print the installed omc package directory (one line, no banner)"
    )

    p_conf = sub.add_parser(
        "configure",
        help="Pick your LLM (~/.omc/config.yaml) and the repo's worktree naming (.omc/config.yaml)",
    )
    p_conf.add_argument("--defaults", action="store_true", help="Write defaults, no prompts")
    p_conf.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a dotted key non-interactively (repeatable)",
    )

    p_design = sub.add_parser(
        "design",
        aliases=["start"],
        help="Begin work on a ticket / task description (alias: start)",
    )
    p_design.add_argument("context", help="Ticket key, ticket URL, or quoted task description")
    p_design.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan; no worktree/session created (still ensures the agents chain)",
    )
    p_design.add_argument("--headless", action="store_true", help="Print-mode session (no exec)")
    p_design.add_argument(
        "--no-mutex",
        action="store_true",
        help="Do not wait for an in-flight `omc watch` update before creating the worktree",
    )
    _add_provider_flags(p_design)

    p_impl = sub.add_parser(
        "implement",
        help=(
            "Hand this worktree's committed design record to a fresh session (plan, build, finish)"
        ),
    )
    p_impl.add_argument(
        "--dry-run", action="store_true", help="Print the plan; no session launched"
    )
    p_impl.add_argument("--headless", action="store_true", help="Print-mode session (no exec)")
    _add_provider_flags(p_impl)

    p_review = sub.add_parser(
        "review", help="Audit this worktree's committed design record in a fresh session"
    )
    p_review.add_argument(
        "--dry-run", action="store_true", help="Print the plan; no session launched"
    )
    p_review.add_argument("--headless", action="store_true", help="Print-mode session (no exec)")
    _add_provider_flags(p_review)

    p_watch = sub.add_parser(
        "watch", help="Keep the primary checkout's base branch + knowledge graph fresh"
    )
    p_watch.add_argument("--interval", type=int, default=30, help="Seconds between ticks")
    p_watch.add_argument("--once", action="store_true", help="Run a single tick and exit")
    p_watch.add_argument(
        "--enable-documentation",
        action="store_true",
        help="Also regenerate the LLM documentation on changes (costly)",
    )
    p_watch.add_argument(
        "--auto-build",
        action="store_true",
        help="After each action tick, run the project's build stage via the default LLM",
    )
    p_watch.add_argument(
        "--rebase",
        action="store_true",
        help="Sync via 'git rebase --autostash' — syncs even dirty or diverged "
        "checkouts (opt-out of warn-and-skip)",
    )
    p_watch.add_argument(
        "--clear-mutex",
        action="store_true",
        help="Remove a leftover watch mutex and run anyway (bypasses the single-instance guard)",
    )
    p_watch.add_argument(
        "--reset-gitnexus",
        action="store_true",
        help="Force-clear the GitNexus index, wiki and docs mirror and rebuild them "
        "(primary must be on the base branch)",
    )

    p_dep = sub.add_parser(
        "dependency", help="External dependency knowledge cache (~/.omc): watch, list"
    )
    dep_sub = p_dep.add_subparsers(dest="dep_command")
    p_depw = dep_sub.add_parser(
        "watch", help="Keep dependency checkouts indexed and their LLM docs generated"
    )
    p_depw.add_argument("--interval", type=int, default=30, help="Seconds between ticks")
    p_depw.add_argument(
        "--once", action="store_true", help="Reconcile everything once, announce, exit"
    )
    dep_sub.add_parser("list", help="Show cached dependencies: repo, commit, index/doc status")

    p_awscp = sub.add_parser(
        "aws-credential-process",
        help="AWS credential_process provider: assume a role with a 1Password-served"
        " TOTP, headless",
    )
    p_awscp.add_argument(
        "--source-profile", required=True, help="AWS profile holding the long-lived keys"
    )
    p_awscp.add_argument("--role-arn", required=True, help="IAM role to assume")
    p_awscp.add_argument("--mfa-serial", required=True, help="TOTP MFA device ARN")
    p_awscp.add_argument(
        "--op-item", required=True, help="1Password item with the one-time password field"
    )
    p_awscp.add_argument("--op-vault", default=None, help="1Password vault (default: any)")
    p_awscp.add_argument(
        "--with-service-account-token",
        default=None,
        metavar="FILE",
        help="File holding a 1Password service account token; passed to the `op` call"
        " only, and only when OP_SERVICE_ACCOUNT_TOKEN is not already set",
    )
    p_awscp.add_argument(
        "--duration", type=int, default=43200, help="Session seconds (default 12h)"
    )
    p_awscp.add_argument(
        "--cache-dir", default=None, help="Session cache dir (default ~/.omc/aws-credential-cache)"
    )

    p_title = sub.add_parser(
        "title", help="Pin or release this iTerm2 tab's title (quiet; used by the fish hook)"
    )
    title_sub = p_title.add_subparsers(dest="title_command", required=True)
    p_title_set = title_sub.add_parser(
        "set", help="Pin the caller's tab title: omc title set -- <title>"
    )
    p_title_set.add_argument("title")
    title_sub.add_parser("release", help="Restore iTerm2's live default title for the caller's tab")
    p_title_apply = title_sub.add_parser(
        "apply", help="Apply a hook-written request file (serialized)"
    )
    p_title_apply.add_argument("request_file")

    p_shell = sub.add_parser(
        "shell-integration", help="Manage the fish tab-title hook (enable|disable|status|reconcile)"
    )
    shell_sub = p_shell.add_subparsers(dest="shell", required=True)
    p_fish = shell_sub.add_parser("fish")
    p_fish.add_argument("action", choices=("enable", "disable", "status", "reconcile"))

    p_install = sub.add_parser("install", help="(Re)install omc from a local checkout")
    p_install.add_argument("path", nargs="?", default=".", help="Checkout path (default: .)")

    sub.add_parser("update", help="Update omc, its plugins, and managed dependencies (GitNexus)")
    sub.add_parser("uninstall", help="Remove omc (binary + ~/.omc)")

    return parser


def _load_cfg_or_bail(ctx: ToolContext):
    cfg = resolve.load_effective(ctx)
    if cfg is None:
        hint = _CONFIGURE_HINT
        if store.legacy_config_path(ctx.home).exists():
            hint += (
                f" (found legacy {store.legacy_config_path(ctx.home)} — "
                "`omc configure` migrates it)"
            )
        print(f"error: omc is not configured — {hint}.", file=sys.stderr)
        return None
    return cfg


def main(argv: list[str] | None = None) -> int:
    # ONE OmcError boundary for every command, `internal` verbs included: an
    # expected failure prints `error: …` and returns the error's rc, never a
    # traceback into a skill's stdout parser.
    try:
        return _run(sys.argv[1:] if argv is None else argv)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.rc


def _run(raw: list[str]) -> int:
    # `internal` is hidden skill<->CLI plumbing: intercepted before argparse so it
    # never appears in --help; machine-readable stdout, no banner.
    if raw and raw[0] == "internal":
        from ..internal import run_internal

        return run_internal(raw[1:])
    args = build_parser().parse_args(raw)
    if not args.command:
        build_parser().print_help(sys.stderr)
        return 2
    ctx = ToolContext.from_env()
    # version/print-install-path: stdout is a one-line machine contract.
    # aws-credential-process: stdout is the credential_process JSON contract, and it
    # must work on a machine that never ran `omc configure`.
    # title/shell-integration: quiet helpers driven by the fish hook and the installer.
    if args.command not in (
        "version",
        "print-install-path",
        "aws-credential-process",
        "title",
        "shell-integration",
    ):
        print(f"Oh My Clanker! v{__version__}", file=sys.stderr)
    return _dispatch(ctx, args)


def _dispatch(ctx: ToolContext, args: argparse.Namespace) -> int:
    if args.command == "title":
        from ..title import run_title_command  # lazy, never loads configuration

        return run_title_command(ctx, args)
    if args.command == "shell-integration":
        from ..fish_integration import run_fish_integration  # lazy, never loads configuration

        return run_fish_integration(ctx, args.action)
    if args.command == "version":
        from ..installsrc import version_string

        print(version_string(ctx.env))
        return 0
    if args.command == "print-install-path":
        from ..installsrc import package_root

        print(package_root())
        return 0
    if args.command in ("design", "start"):  # argparse stores the typed token for an alias
        cfg = _load_cfg_or_bail(ctx)
        if cfg is None:
            return 2
        cfg = _with_provider(cfg, args.provider_override)
        return run_start(
            ctx,
            cfg,
            args.context,
            dry_run=args.dry_run,
            headless=args.headless,
            no_mutex=args.no_mutex,
        )
    if args.command == "implement":
        cfg = _load_cfg_or_bail(ctx)
        if cfg is None:
            return 2
        from ..implement import run_implement  # lazy, like every newer command

        return run_implement(
            ctx,
            _with_provider(cfg, args.provider_override),
            dry_run=args.dry_run,
            headless=args.headless,
        )
    if args.command == "review":
        cfg = _load_cfg_or_bail(ctx)
        if cfg is None:
            return 2
        from ..review import run_review

        return run_review(
            ctx,
            _with_provider(cfg, args.provider_override),
            dry_run=args.dry_run,
            headless=args.headless,
        )
    if args.command == "watch":
        cfg = _load_cfg_or_bail(ctx)
        if cfg is None:
            return 2
        from ..watch import run_watch

        return run_watch(
            ctx,
            cfg,
            interval=args.interval,
            once=args.once,
            enable_documentation=args.enable_documentation,
            auto_build=args.auto_build,
            rebase=args.rebase,
            clear_mutex=args.clear_mutex,
            reset_gitnexus=args.reset_gitnexus,
        )
    if args.command == "dependency":
        if args.dep_command == "watch":
            cfg = _load_cfg_or_bail(ctx)
            if cfg is None:
                return 2
            from ..depwatch import run_dependency_watch

            return run_dependency_watch(ctx, interval=args.interval, once=args.once)
        if args.dep_command == "list":
            from ..depwatch import run_dependency_list

            return run_dependency_list(ctx.home)
        print("usage: omc dependency {watch|list}", file=sys.stderr)
        return 2
    if args.command == "configure":
        from ..configure import run_configure

        return run_configure(ctx, defaults=args.defaults, sets=args.set)
    if args.command == "aws-credential-process":
        from ..awscreds import run_aws_credential_process

        return run_aws_credential_process(ctx, args)
    if args.command == "install":
        from ..installer import run_install

        return run_install(ctx, args.path)
    if args.command == "update":
        from ..installer import run_update

        return run_update(ctx)
    if args.command == "uninstall":
        from ..installer import run_uninstall

        return run_uninstall(ctx)
    raise OmcError(f"unknown command {args.command!r}")
