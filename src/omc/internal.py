"""`omc internal …` — the skill↔CLI contract (hidden, machine-readable).

Intercepted before argparse; stdout is for machines. Exit codes: 0 ok,
2 usage, 3 bail ("inconclusive — the calling skill falls back to its own
judgment", chicken semantics; rebase conflicts bail rather than error).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import resolve
from .errors import OmcError
from .gitnexus import gitnexus_argv, gitnexus_cli
from .mirror import mirror_snapshot
from .providers.registry import provider_names
from .toolctx import ToolContext
from .wtconfig import WT_TEMPLATE, primary_root, repo_root

_USAGE = (
    "usage: omc internal {rebase-main [--base BRANCH] | wt-template"
    " | notify --provider NAME [--event E] [--message M] [payload]"
    " | gitnexus [--git REF] <query|context|impact|cypher> [args…]"
    " | dependency <ensure|document|list> [args…]"
    " | build-progress LOGFILE}"
)

_GITNEXUS_VERBS = ("query", "context", "impact", "cypher")


def _verdict(payload: dict) -> None:
    print(f"OMC_REBASE_MAIN {json.dumps(payload)}", flush=True)


def _rebase_main(ctx: ToolContext, base_arg: str | None) -> int:
    base = base_arg or resolve.project_config(ctx).worktree.base_branch

    root = repo_root(ctx)
    primary = primary_root(ctx)
    if root is None or primary is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    if Path(root).resolve() == Path(primary).resolve():
        _verdict(
            {
                "ok": True,
                "rebased": "",
                "synced": [],
                "note": "primary checkout — nothing to rebase",
            }
        )
        return 0

    cp = ctx.run([ctx.git_bin, "fetch", "origin", base])
    if cp.returncode != 0:
        print(
            f"error: git fetch origin {base} failed: {(cp.stderr or '').strip()}", file=sys.stderr
        )
        return 1

    old = (ctx.run([ctx.git_bin, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
    cp = ctx.run([ctx.git_bin, "rebase", f"origin/{base}"])
    if cp.returncode != 0:
        conflicts = (
            ctx.run([ctx.git_bin, "diff", "--name-only", "--diff-filter=U"]).stdout or ""
        ).split()
        # The rebase stays PAUSED for the user/skill to resolve — never aborted here.
        _verdict({"ok": False, "conflicts": conflicts})
        return 3

    new = (ctx.run([ctx.git_bin, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
    synced = mirror_snapshot(Path(primary), Path(root))
    if synced and gitnexus_cli(ctx).is_file():
        # Best-effort: register the copied index so gitnexus commands work from
        # this worktree; failure is non-fatal (explain falls back to primary).
        ctx.run(gitnexus_argv(ctx, "index"), cwd=root)
    _verdict({"ok": True, "rebased": f"{old}..{new}", "synced": synced})
    return 0


def _gitnexus(ctx: ToolContext, rest: list[str]) -> int:
    """Scoped GitNexus proxy. GitNexus keys its DEFAULT store to the branch the
    repo was FIRST indexed on (which may since be deleted), while incremental
    analyze writes to .gitnexus/branches/<branch>/ — an unscoped query silently
    reads the frozen default store. So: always run from the PRIMARY root and
    always pin --repo (registry may hold several repos) and --branch (the
    configured base). --repo is the primary root's PATH, not its basename:
    GitNexus registers repos under remote-URL-derived names that need not match
    the directory name, and its resolver also matches canonicalized paths.
    gitnexus 1.6.x resolves --branch <base> against the branch store when one
    exists and falls back to the default store when the base branch IS the
    originally-indexed one — verify on a gitnexus upgrade.

    With --git REF (a URL or manifest key, optional @<hash>), queries scope to
    that dependency checkout pinned to omc-pin — READ-ONLY: unknown/unindexed
    refs error with the ensure hint, never clone.
    """
    dep_ref: str | None = None
    if rest[:1] == ["--git"]:
        if len(rest) < 2:
            print(_USAGE, file=sys.stderr)
            return 2
        dep_ref, rest = rest[1], rest[2:]
    if not rest or rest[0] not in _GITNEXUS_VERBS:
        print(_USAGE, file=sys.stderr)
        return 2
    if not gitnexus_cli(ctx).is_file():
        print(
            "error: GitNexus is not installed — run /omc:index once in a session first",
            file=sys.stderr,
        )
        return 1
    if dep_ref is not None:
        from .dependency import PIN_BRANCH, resolve_ref

        try:
            key, commit, entry = resolve_ref(ctx.home, dep_ref)
        except OmcError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        # A falsy checkout must be treated as not-indexed BEFORE building a Path:
        # Path("") is Path("."), and "./.git" would spuriously pass the guard
        # whenever cwd happens to be a git repo (wrong-repo answers).
        checkout_str = entry.get("checkout")
        if (
            not entry.get("indexed")
            or not checkout_str
            or not (Path(checkout_str) / ".git").exists()
        ):
            print(
                f"error: {key}@{commit[:7]} is not indexed — "
                "run `omc internal dependency ensure --git <url>` first",
                file=sys.stderr,
            )
            return 1
        checkout = Path(checkout_str)
        argv = gitnexus_argv(ctx, *rest, "--repo", str(checkout), "--branch", PIN_BRANCH)
        cp = ctx.run(argv, cwd=checkout, capture=False)
        return cp.returncode
    primary = primary_root(ctx)
    if primary is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    base = resolve.project_config(ctx).worktree.base_branch
    argv = gitnexus_argv(ctx, *rest, "--repo", primary, "--branch", base)
    cp = ctx.run(argv, cwd=primary, capture=False)  # stream JSON straight through
    return cp.returncode


def run_internal(argv: list[str]) -> int:
    if not argv:
        print(_USAGE, file=sys.stderr)
        return 2
    cmd, *rest = argv
    if cmd == "wt-template":
        print(WT_TEMPLATE, end="")
        return 0
    if cmd == "rebase-main":
        parser = argparse.ArgumentParser(prog="omc internal rebase-main", add_help=False)
        parser.add_argument("--base", default=None)
        try:
            args = parser.parse_args(rest)
        except SystemExit:
            print(_USAGE, file=sys.stderr)
            return 2
        return _rebase_main(ToolContext.from_env(), args.base)
    if cmd == "notify":
        parser = argparse.ArgumentParser(prog="omc internal notify", add_help=False)
        parser.add_argument("--provider", required=True, choices=provider_names())
        parser.add_argument("--event", default="")
        parser.add_argument("--message", default="")
        parser.add_argument("payload", nargs="?", default=None)  # codex's single JSON arg
        try:
            args = parser.parse_args(rest)
        except SystemExit:
            print(_USAGE, file=sys.stderr)
            return 2
        from .notify import run_notify

        return run_notify(ToolContext.from_env(), args)
    if cmd == "gitnexus":
        return _gitnexus(ToolContext.from_env(), rest)
    if cmd == "dependency":
        from .dependency import run_document, run_ensure, run_list

        if not rest:
            print(_USAGE, file=sys.stderr)
            return 2
        sub, *dep_rest = rest
        if sub == "list" and not dep_rest:
            return run_list(ToolContext.from_env().home)
        if sub in ("ensure", "document"):
            parser = argparse.ArgumentParser(prog=f"omc internal dependency {sub}", add_help=False)
            parser.add_argument("--git", required=True)
            if sub == "ensure":
                parser.add_argument("--commit", default=None)
            try:
                args = parser.parse_args(dep_rest)
            except SystemExit:
                print(_USAGE, file=sys.stderr)
                return 2
            ctx = ToolContext.from_env()
            if sub == "ensure":
                return run_ensure(ctx, args.git, args.commit)
            return run_document(ctx, args.git)
        print(_USAGE, file=sys.stderr)
        return 2
    if cmd == "build-progress":
        parser = argparse.ArgumentParser(prog="omc internal build-progress", add_help=False)
        parser.add_argument("logfile")
        try:
            args = parser.parse_args(rest)
        except SystemExit:
            print(_USAGE, file=sys.stderr)
            return 2
        from .buildprogress import follow_log

        return follow_log(args.logfile)
    print(_USAGE, file=sys.stderr)
    return 2
