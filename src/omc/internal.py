"""`omc internal …` — the skill↔CLI contract (hidden, machine-readable).

Intercepted before argparse; stdout is for machines. Exit codes: 0 ok,
1 error, 2 usage or refusal (a definite `ok: false` verdict), 3 bail
("inconclusive — the calling skill falls back to its own judgment", chicken
semantics; rebase conflicts bail rather than error).
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path

from .config import resolve, store
from .errors import OmcError
from .gitnexus import (
    ensure_gitnexus,
    gitnexus_argv,
    gitnexus_cli,
    refresh_knowledge,
    snapshot_freshness,
)
from .mirror import mirror_snapshot
from .taskmodels import TASKS, task_choice
from .toolctx import ToolContext
from .watchlock import acquire_busy_narrated, busy_lock
from .wtconfig import (
    WT_TEMPLATE,
    current_branch,
    primary_root,
    repo_root,
    resolve_design_record,
)

_USAGE = (
    "usage: omc internal {rebase-main [--base BRANCH] | wt-template | design-record | models"
    " | global-instructions PROVIDER"
    " | gitnexus [--git REF] <ensure|status|refresh [--enable-documentation]"
    "|query|context|impact|cypher> [args…]"
    " | dependency <ensure|document|list> [args…]"
    " | skills list NAME"
    " | build-progress LOGFILE}"
)

_GITNEXUS_VERBS = ("query", "context", "impact", "cypher")


def _verdict(payload: dict) -> None:
    print(f"OMC_REBASE_MAIN {json.dumps(payload)}", flush=True)


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _knowledge_line(v) -> None:
    print(f"OMC_KNOWLEDGE {json.dumps(v.to_json())}", flush=True)


def _primary_and_base(ctx: ToolContext) -> tuple[str, str] | None:
    primary = primary_root(ctx)
    if primary is None:
        print("error: not inside a git repository", file=sys.stderr)
        return None
    return primary, resolve.project_config(ctx).worktree.base_branch


def _knowledge_status(ctx: ToolContext) -> int:
    try:
        pb = _primary_and_base(ctx)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if pb is None:
        return 2
    primary, base = pb
    _knowledge_line(snapshot_freshness(ctx, Path(primary), base))  # no fetch: basis says so
    return 0


def _knowledge_refresh(ctx: ToolContext, rest: list[str]) -> int:
    """Repair the primary's knowledge snapshot in place (spec §4). Never fetches
    and never takes the INSTANCE lock: the verdict is measured against HEAD, so
    a primary nobody syncs can still become fresh; `omc watch` owns syncing."""
    parser = argparse.ArgumentParser(prog="omc internal gitnexus refresh", add_help=False)
    parser.add_argument("--enable-documentation", action="store_true")
    try:
        args = parser.parse_args(rest)
    except SystemExit:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        cfg = resolve.load_effective(ctx)
        if cfg is None:
            print("error: omc is not configured — run `omc configure` first.", file=sys.stderr)
            return 2
        pb = _primary_and_base(ctx)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if pb is None:
        return 2
    primary, base = pb
    branch = current_branch(ctx, primary) or ""
    if branch != base:
        # analyze off the base would stamp the store with THAT branch and
        # recreate the inversion — refuse before any node call and any lock.
        print(
            f"error: refresh requires the primary checkout to be on {base} (currently {branch})",
            file=sys.stderr,
        )
        return 1
    if ensure_gitnexus(ctx):
        return 1
    lock = busy_lock(ctx, cwd=primary)
    # Held for the WHOLE repair so `omc design` never snapshots a half-written
    # index/wiki; the context manager releases it on exceptions too.
    with acquire_busy_narrated(lock, _say) if lock is not None else nullcontext():
        v = refresh_knowledge(
            ctx, cfg, primary, base, documentation=args.enable_documentation, reset=False, say=_say
        )
    behind = ctx.run([ctx.git_bin, "rev-list", "--count", f"HEAD..origin/{base}"], cwd=primary)
    n = (behind.stdout or "").strip()
    if behind.returncode == 0 and n.isdigit() and int(n) > 0:
        _say(f"· primary is {n} commits behind origin/{base} — omc watch syncs it")
    _knowledge_line(v)
    return 0 if v.fresh else 3


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
                "shared": [],
                "note": "primary checkout — nothing to rebase",
                # No fetch on this path (and none is added for a verdict) — `basis`
                # names the local ref it was measured against.
                "knowledge": snapshot_freshness(ctx, Path(primary), base).to_json(),
            }
        )
        return 0

    cp = ctx.run([ctx.git_bin, "fetch", "origin", base])
    if cp.returncode != 0:
        print(
            f"error: git fetch origin {base} failed: {(cp.stderr or '').strip()}", file=sys.stderr
        )
        return 1

    # One verdict for both remaining shapes, measured against the ref we just fetched.
    knowledge = snapshot_freshness(ctx, Path(primary), base).to_json()

    old = (ctx.run([ctx.git_bin, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
    cp = ctx.run([ctx.git_bin, "rebase", f"origin/{base}"])
    if cp.returncode != 0:
        conflicts = (
            ctx.run([ctx.git_bin, "diff", "--name-only", "--diff-filter=U"]).stdout or ""
        ).split()
        # The rebase stays PAUSED for the user/skill to resolve — never aborted here.
        _verdict({"ok": False, "conflicts": conflicts, "knowledge": knowledge})
        return 3

    new = (ctx.run([ctx.git_bin, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
    result = mirror_snapshot(Path(primary), Path(root))
    # Deliberately NO gitnexus invocation here — indexing is `omc watch`'s
    # exclusive feature. Registering the copied snapshot (`gitnexus index` in
    # the worktree, removed 2026-08-03) minted a same-named registry entry
    # frozen at cut time per worktree, never unregistered; stale-entry lookups
    # then reported "N commits behind" while the primary index was current.
    # Worktree queries don't need registration: the proxy pins --repo <primary>.
    _verdict(
        {
            "ok": True,
            "rebased": f"{old}..{new}",
            "synced": result.synced,
            "shared": result.shared,
            "knowledge": knowledge,
        }
    )
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
    if rest == ["ensure"]:
        return ensure_gitnexus(ctx)
    # Before the --git parse AND the CLI-presence guard: status needs no CLI,
    # refresh installs one itself via ensure_gitnexus.
    if rest == ["status"]:
        return _knowledge_status(ctx)
    if rest[:1] == ["refresh"]:
        return _knowledge_refresh(ctx, rest[1:])
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
            "error: GitNexus is not installed — run `omc update` (or `omc design`/`omc watch`) "
            "to install it",
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
    verdict = snapshot_freshness(ctx, Path(primary), base)  # computed without fetch
    if not verdict.fresh:
        # stderr, flushed BEFORE the child spawns: stdout stays pure GitNexus JSON
        print(f"OMC_KNOWLEDGE {json.dumps(verdict.to_json())}", file=sys.stderr, flush=True)
    argv = gitnexus_argv(ctx, *rest, "--repo", primary, "--branch", base)
    cp = ctx.run(argv, cwd=primary, capture=False)  # stream JSON straight through
    return cp.returncode


def _design_record(ctx: ToolContext) -> int:
    """The design-record gate as a machine contract (spec §3.4). Project config
    alone decides the branch prefix, so the verb works where global config is
    absent. `ok: false` is a definite refusal: verdict line, exit 2. Outside a
    repo it prints the same error line and returns 2 as the other verbs; any
    other OmcError (invalid project config) reaches main()'s shared rc-1
    boundary, so the try stays narrow."""
    cfg = resolve.project_config(ctx)
    try:
        verdict = resolve_design_record(ctx, cfg)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"OMC_DESIGN_RECORD {json.dumps(verdict.to_json())}", flush=True)
    return 0 if verdict.ok else 2


def _skills_list(ctx: ToolContext, name: str) -> int:
    """Print canonical skill paths in project, primary, global run order."""
    part = Path(name)
    if not name or part.is_absolute() or part.parts != (name,) or name in (".", ".."):
        print(_USAGE, file=sys.stderr)
        return 2

    bases: list[Path] = []
    root = repo_root(ctx)
    if root is not None:
        bases.append(Path(root) / ".omc" / "skills")
        primary = primary_root(ctx)
        if primary is not None and Path(primary).resolve() != Path(root).resolve():
            bases.append(Path(primary) / ".omc" / "skills")
    bases.append(ctx.home / "skills")

    found: list[str] = []
    seen: set[Path] = set()

    def add(candidate: Path, skills_root: Path) -> None:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return
        if not resolved.is_file() or not resolved.is_relative_to(skills_root):
            return
        if resolved not in seen:
            seen.add(resolved)
            found.append(str(resolved))

    for base in bases:
        try:
            skills_root = base.resolve()
        except (OSError, RuntimeError):
            continue
        if name != "explain-source":
            add(skills_root / name / "SKILL.md", skills_root)
            continue
        family = skills_root / name
        try:
            resolved_family = family.resolve(strict=True)
            if not resolved_family.is_dir() or not resolved_family.is_relative_to(skills_root):
                continue
            children = sorted(resolved_family.iterdir(), key=lambda path: path.name)
        except (OSError, RuntimeError):
            continue
        for child in children:
            if child.name != ".git":
                add(child / "SKILL.md", skills_root)

    print(json.dumps(found))
    return 0


def _models(ctx: ToolContext) -> int:
    """Expose all resolved task choices as one machine-readable verdict."""
    try:
        cfg = store.load_global(ctx.home)
        if cfg is None:
            raise OmcError("omc is not configured — run omc configure first")
        name = ctx.env.get("OMC_PROVIDER") or cfg.llm.default
        choices = {task: task_choice(ctx, cfg, task, provider=name) for task in TASKS}
        payload = {
            "ok": True,
            "provider": name,
            "tasks": {
                task: {"model": choice.model_arg, "effort": choice.effort}
                for task, choice in choices.items()
            },
        }
    except OmcError as exc:
        payload = {"ok": False, "message": str(exc)}
    print(f"OMC_MODELS {json.dumps(payload)}", flush=True)
    return 0 if payload["ok"] else 2


def run_internal(argv: list[str]) -> int:
    if not argv:
        print(_USAGE, file=sys.stderr)
        return 2
    cmd, *rest = argv
    if cmd == "wt-template":
        print(WT_TEMPLATE, end="")
        return 0
    if cmd == "design-record":
        if rest:
            print(_USAGE, file=sys.stderr)
            return 2
        return _design_record(ToolContext.from_env())
    if cmd == "models":
        if rest:
            print(_USAGE, file=sys.stderr)
            return 2
        return _models(ToolContext.from_env())
    if cmd == "global-instructions":
        if len(rest) != 1:
            print(_USAGE, file=sys.stderr)
            return 2
        from .agentsmd import ensure_global_section

        try:
            ensure_global_section(ToolContext.from_env(), rest[0])
        except OmcError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
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
    if cmd == "gitnexus":
        return _gitnexus(ToolContext.from_env(), rest)
    if cmd == "skills":
        if len(rest) != 2 or rest[0] != "list":
            print(_USAGE, file=sys.stderr)
            return 2
        return _skills_list(ToolContext.from_env(), rest[1])
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
