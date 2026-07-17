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

from .config import store
from .gitnexus import gitnexus_argv, gitnexus_cli
from .mirror import mirror_snapshot
from .toolctx import ToolContext
from .wtconfig import WT_TEMPLATE, primary_root, repo_root

_USAGE = "usage: omc internal {rebase-main [--base BRANCH] | wt-template}"


def _verdict(payload: dict) -> None:
    print(f"OMC_REBASE_MAIN {json.dumps(payload)}", flush=True)


def _rebase_main(ctx: ToolContext, base_arg: str | None) -> int:
    cfg = store.load(ctx.home)
    base = base_arg or (cfg.worktree.base_branch if cfg else "main")

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
    print(_USAGE, file=sys.stderr)
    return 2
