"""`omc internal …` — the skill↔CLI contract (hidden, machine-readable).

Intercepted before argparse; stdout is for machines. Exit codes: 0 ok,
1 error, 2 usage or refusal (a definite `ok: false` verdict, or an `error:`
line on stderr), 3 bail ("inconclusive — the calling skill falls back to its
own judgment", chicken semantics; rebase conflicts bail rather than error).
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
    CheckoutMoved,
    ensure_gitnexus,
    gitnexus_argv,
    gitnexus_cli,
    refresh_knowledge,
    snapshot_freshness,
)
from .taskmodels import TASKS, task_choice
from .toolctx import ToolContext
from .watchlock import acquire_busy_narrated, busy_lock
from .wtconfig import (
    WT_TEMPLATE,
    current_branch,
    primary_root,
    rebase_in_progress,
    repo_root,
    resolve_design_record,
)

_USAGE = (
    "usage: omc internal {rebase-main [--base BRANCH] [--squash] | wt-template | design-record"
    " | gate [record] review|verify | models | global-instructions PROVIDER"
    " | gitnexus <ensure|status|refresh|query|context|impact|cypher> [args…]"
    " | skills list NAME"
    " | stage <start STAGE [-- ARGS…]|wait STAGE|kill STAGE|template>"
    " | build-progress LOGFILE | herdr-cwd}"
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
    if rest:  # refresh takes no arguments; argparse would echo the rejected one
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
    # index; the context manager releases it on exceptions too.
    try:
        with acquire_busy_narrated(lock, _say) if lock is not None else nullcontext():
            v = refresh_knowledge(
                ctx,
                cfg,
                primary,
                base,
                reset=False,
                say=_say,
            )
    except CheckoutMoved as moved:
        print(
            f"error: refresh requires the primary checkout to be on {moved.base} "
            f"(currently {moved.branch})",
            file=sys.stderr,
        )
        return 1
    behind = ctx.run([ctx.git_bin, "rev-list", "--count", f"HEAD..origin/{base}"], cwd=primary)
    n = (behind.stdout or "").strip()
    if behind.returncode == 0 and n.isdigit() and int(n) > 0:
        _say(f"· primary is {n} commits behind origin/{base} — omc watch syncs it")
    _knowledge_line(v)
    return 0 if v.fresh else 3


def _squash(ctx: ToolContext, root: str, base: str, old: str) -> int | None:
    """Fold the branch, uncommitted changes included, into ONE `wip:` commit over
    `merge-base origin/<base> HEAD` — never over origin/<base> itself, which would
    revert every commit the base gained since the cut. Returns the number of
    branch commits folded, or None after printing why; a rejected commit (hooks
    run, no --no-verify) is rolled back to `old`, leaving the commits as found."""
    git = ctx.git_bin
    mb_cp = ctx.run([git, "merge-base", f"origin/{base}", "HEAD"])
    mb = (mb_cp.stdout or "").strip()
    if mb_cp.returncode != 0 or not mb:
        why = (mb_cp.stderr or "").strip()
        print(f"error: no merge-base between origin/{base} and HEAD: {why}", file=sys.stderr)
        return None
    count = ctx.run([git, "rev-list", "--count", f"{mb}..HEAD"])
    if count.returncode != 0:
        print(f"error: git rev-list failed: {(count.stderr or '').strip()}", file=sys.stderr)
        return None
    n = int((count.stdout or "").strip() or 0)
    status = ctx.run([git, "status", "--porcelain"])
    if status.returncode != 0:
        print(f"error: git status failed: {(status.stderr or '').strip()}", file=sys.stderr)
        return None
    dirty = bool((status.stdout or "").strip())
    if dirty:
        # Untracked files fold too; ignored ones (.gitnexus) never do.
        _say("→ folding uncommitted changes into the squash")
        add = ctx.run([git, "add", "-A"])
        if add.returncode != 0:
            print(f"error: git add -A failed: {(add.stderr or '').strip()}", file=sys.stderr)
            return None
    if n == 0 and not dirty:
        _say("· nothing to squash")
        return 0
    # Commits that cancel out leave nothing to commit. Checked against the index
    # before the reset, so HEAD never moves: the end state is the failed-commit
    # rollback's (commits as found, any folded changes left staged).
    if ctx.run([git, "diff", "--cached", "--quiet", mb]).returncode == 0:
        print(
            f"error: the branch changes nothing over the merge-base {mb}"
            f" (pre-squash tip {old}) — nothing to squash or publish",
            file=sys.stderr,
        )
        return None
    reset = ctx.run([git, "reset", "--soft", mb])
    if reset.returncode != 0:
        why = (reset.stderr or "").strip()
        print(f"error: git reset --soft {mb} failed: {why}", file=sys.stderr)
        return None
    branch = current_branch(ctx, root) or "HEAD"
    commit = ctx.run([git, "commit", "-m", f"wip: squash of {branch}"])
    if commit.returncode != 0:
        parts = ((commit.stderr or "").strip(), (commit.stdout or "").strip())
        why = "\n".join(p for p in parts if p)
        print(f"error: squash commit failed (pre-squash tip {old}): {why}", file=sys.stderr)
        # The original commits come back; the index stays staged (at worst the
        # user's own dirty changes, now staged).
        back = ctx.run([git, "reset", "--soft", old])
        if back.returncode != 0:
            print(
                f"error: could not restore the original commits:"
                f" {(back.stderr or '').strip()}\n"
                f"HEAD is at the merge-base {mb} with all changes staged;"
                f" restore with: git reset --soft {old}",
                file=sys.stderr,
            )
        return None
    undo = f"git reset --hard {old}"
    if dirty:
        # That reset drops the folded edits from the tree; this commit (rebased
        # away next, still reachable by sha) is where they survive.
        wip = (ctx.run([git, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
        undo += f"; folded uncommitted changes stay in {wip}"
    _say(f"· pre-squash tip {old} (undo: {undo})")
    return n


def _rebase_main(ctx: ToolContext, base_arg: str | None, squash: bool) -> int:
    base = base_arg or resolve.project_config(ctx).worktree.base_branch

    root = repo_root(ctx)
    primary = primary_root(ctx)
    if root is None or primary is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    in_primary = Path(root).resolve() == Path(primary).resolve()
    # The primary on its base (or detached) belongs to `omc watch`; only finish's
    # --squash on a feature branch there — a plain clone — squashes and rebases.
    squash_here = squash and current_branch(ctx, root) not in (None, "HEAD", base)
    if in_primary and not squash_here:
        _verdict(
            {
                "ok": True,
                "rebased": "",
                "note": "primary checkout — nothing to rebase",
                # No fetch on this path (and none is added for a verdict) — `basis`
                # names the local ref it was measured against.
                "knowledge": snapshot_freshness(ctx, Path(primary), base).to_json(),
            }
        )
        return 0

    # Refuse before the fetch: a paused rebase or unmerged paths are the user's to
    # finish, and a squash's `git add -A` would stage conflict markers as resolved.
    # A merge/cherry-pick/revert with its conflicts already resolved has no
    # unmerged paths left, but is just as unfinished.
    unmerged = (ctx.run([ctx.git_bin, "ls-files", "-u"]).stdout or "").strip()
    pending = any(
        ctx.run([ctx.git_bin, "rev-parse", "-q", "--verify", ref]).returncode == 0
        for ref in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
    )
    if rebase_in_progress(ctx, root) or unmerged or pending:
        print(
            "error: a rebase or merge is in progress — finish it (git rebase --continue"
            " / git merge --continue) or back out (git rebase --abort / git merge --abort)"
            " first; a cherry-pick or revert takes its own --continue / --abort",
            file=sys.stderr,
        )
        return 2

    cp = ctx.run([ctx.git_bin, "fetch", "origin", base])
    if cp.returncode != 0:
        print(
            f"error: git fetch origin {base} failed: {(cp.stderr or '').strip()}", file=sys.stderr
        )
        return 1

    # One verdict for both remaining shapes, measured against the ref we just fetched.
    knowledge = snapshot_freshness(ctx, Path(primary), base).to_json()

    old = (ctx.run([ctx.git_bin, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
    squashed = None
    if squash:
        squashed = _squash(ctx, root, base, old)
        if squashed is None:
            return 1
    cp = ctx.run([ctx.git_bin, "rebase", f"origin/{base}"])
    if cp.returncode != 0:
        if not rebase_in_progress(ctx, root):
            # git refused before starting (canonical: a dirty tree) — nothing is
            # paused and there are no conflicts to report.
            print(
                f"error: git rebase origin/{base} failed: {(cp.stderr or '').strip()}",
                file=sys.stderr,
            )
            return 1
        conflicts = (
            ctx.run([ctx.git_bin, "diff", "--name-only", "--diff-filter=U"]).stdout or ""
        ).split()
        # The rebase stays PAUSED for the user/skill to resolve — never aborted here.
        _verdict({"ok": False, "conflicts": conflicts, "knowledge": knowledge})
        return 3

    new = (ctx.run([ctx.git_bin, "rev-parse", "--short", "HEAD"]).stdout or "").strip()
    # Deliberately NO gitnexus invocation here — indexing is `omc watch`'s
    # exclusive feature. Registering the copied snapshot (`gitnexus index` in
    # the worktree, removed 2026-08-03) minted a same-named registry entry
    # frozen at cut time per worktree, never unregistered; stale-entry lookups
    # then reported "N commits behind" while the primary index was current.
    # Worktree queries don't need registration: the proxy pins --repo <primary>.
    payload = {"ok": True, "rebased": f"{old}..{new}"}
    if squashed is not None:
        payload["squashed"] = squashed  # only with --squash; finish reports it as N→1
    payload["knowledge"] = knowledge
    _verdict(payload)
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

    GitNexus 1.6.12 routes linked-worktree checkouts to $GITNEXUS_HOME/stores
    by default. omc forces GITNEXUS_SHARED_STORE=off in ToolContext.child_env,
    overriding inherited sharing so .gitnexus/ remains a local snapshot.
    GITNEXUS_STORAGE_PATH and GITNEXUS_STORAGE_ROOT overrides are unsupported
    with omc and remain untouched — reverify storage routing on upgrades too.
    """
    if rest == ["ensure"]:
        return ensure_gitnexus(ctx)
    # Before the CLI-presence guard: status needs no CLI, refresh installs one
    # itself via ensure_gitnexus.
    if rest == ["status"]:
        return _knowledge_status(ctx)
    if rest[:1] == ["refresh"]:
        return _knowledge_refresh(ctx, rest[1:])
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
        except OSError:
            return
        if not resolved.is_file() or not resolved.is_relative_to(skills_root):
            return
        if resolved not in seen:
            seen.add(resolved)
            found.append(str(resolved))

    for base in bases:
        try:
            skills_root = base.resolve()
        except OSError:
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
        except OSError:
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
            # Lifecycle conductors read this (spec 2026-11-12 §4.3): only an
            # explicit false auto-advances; a missing key stops as before.
            "provider_selection_per_step": cfg.llm.provider_selection_per_step,
        }
    except OmcError as exc:
        payload = {"ok": False, "message": str(exc)}
    print(f"OMC_MODELS {json.dumps(payload)}", flush=True)
    return 0 if payload["ok"] else 2


def _stage(ctx: ToolContext, rest: list[str]) -> int:
    """`stage start|wait|kill|template`: one OMC_STAGE_RUN line per answer (design 6.4).

    rc 0 for every answer but `refused` (2). Any OmcError, a Refusal included, or OSError
    prints `error:`, no verdict, and exits 1: rc 2 belongs to the `refused` verdict line alone.
    The hook inherits this process's environment as-is: the supervisor's spawn passes
    ctx.env on (no GitNexus overlay), and the supervisor passes its own on to the hook."""
    from .stagedef import STAGE_TEMPLATE, STAGES
    from .stagerun import kill_run, start_run, wait_run

    verb, *args = rest or [""]
    if verb == "template" and not args:
        print(STAGE_TEMPLATE, end="")
        return 0
    if verb not in ("start", "wait", "kill") or not args or args[0] not in STAGES:
        print(_USAGE, file=sys.stderr)
        return 2
    stage, *tail = args
    if verb == "start" and tail[:1] == ["--"]:
        tail = tail[1:]
    elif tail:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        if verb == "start":
            answer = start_run(ctx, stage, tail)
        elif verb == "wait":
            answer = wait_run(ctx, stage)
        else:
            answer = kill_run(ctx, stage)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:  # a stage root or worktree file it cannot write, …
        print(f"error: {exc.strerror} ({exc.filename})", file=sys.stderr)
        return 1
    print(f"OMC_STAGE_RUN {json.dumps(answer.to_json())}", flush=True)
    return 2 if answer.state == "refused" else 0


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
    if cmd == "gate":
        from .gate import GATES, question, record

        if len(rest) == 1 and rest[0] in GATES:
            return question(ToolContext.from_env(), rest[0])
        if len(rest) == 2 and rest[0] == "record" and rest[1] in GATES:
            return record(ToolContext.from_env(), rest[1])
        print(_USAGE, file=sys.stderr)
        return 2
    if cmd == "models":
        if rest:
            print(_USAGE, file=sys.stderr)
            return 2
        return _models(ToolContext.from_env())
    if cmd == "herdr-cwd":
        if rest:
            print(_USAGE, file=sys.stderr)
            return 2
        from .herdr import run_herdr_cwd

        return run_herdr_cwd(ToolContext.from_env(), sys.stdin)
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
        parser.add_argument("--squash", action="store_true")
        try:
            args = parser.parse_args(rest)
        except SystemExit:
            print(_USAGE, file=sys.stderr)
            return 2
        return _rebase_main(ToolContext.from_env(), args.base, args.squash)
    if cmd == "gitnexus":
        return _gitnexus(ToolContext.from_env(), rest)
    if cmd == "skills":
        if len(rest) != 2 or rest[0] != "list":
            print(_USAGE, file=sys.stderr)
            return 2
        return _skills_list(ToolContext.from_env(), rest[1])
    if cmd == "stage":
        return _stage(ToolContext.from_env(), rest)
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
