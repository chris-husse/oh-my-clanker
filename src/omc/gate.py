"""`omc internal gate` — the deterministic half of the MR gate (spec 2026-11-16 §5.1).

`question` answers whether the review or the verify stage must run on the
current `HEAD^{tree}`; `record` remembers the tree a green review or verify
saw. State is one file per gate at `git rev-parse --git-path omc/reviewed-tree`
and `omc/verified-tree`: per worktree, never committed, gone with the worktree.
Keyed by tree, not by commit, so a squash, a rebase onto an unmoved base or a
message-only amend keeps it; a stale file can only make the trees differ and ask again.

The helper runs nothing but git and never runs a hook: when the tree changed
and `.omc/hooks/needs_<gate>/SKILL.md` exists it answers `"ask": true` and the
session puts the question to the hook proxy. The absent-hook defaults differ
per gate (review does not run, verify does) and live here, under test, not in
skill prose.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import resolve
from .errors import OmcError
from .toolctx import ToolContext
from .wtconfig import repo_root

GATES = ("review", "verify")

# gate -> (state file under the git dir's omc/, hook name, `run` when no hook is configured)
_TABLE: dict[str, tuple[str, str, bool]] = {
    "review": ("reviewed-tree", "needs_review", False),
    "verify": ("verified-tree", "needs_verify", True),
}


@dataclass(frozen=True)
class GateVerdict:
    """One gate question's answer: `run` stands unless a hook overrides it; `ask`
    says the session must put the question to the hook with `previous` and `tree`."""

    gate: str
    run: bool
    ask: bool
    reason: str  # unchanged | no-hook | hook | no-record
    tree: str
    previous: str | None
    message: str

    def to_json(self) -> dict:
        return asdict(self)


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _git(ctx: ToolContext, root: str, *args: str) -> str:
    cp = ctx.run([ctx.git_bin, *args], cwd=root)
    if cp.returncode != 0:
        why = (cp.stderr or "").strip() or f"exit {cp.returncode}"
        raise OmcError(f"git {' '.join(args)} failed: {why}")
    return (cp.stdout or "").strip()


def _state_file(ctx: ToolContext, root: str, gate: str) -> Path:
    # `--git-path` is relative to cwd in a primary checkout, absolute in a linked
    # worktree; joining onto root covers both.
    return Path(root) / _git(ctx, root, "rev-parse", "--git-path", f"omc/{_TABLE[gate][0]}")


def _decide(
    gate: str, tree: str, previous: str | None, hooked: bool, recorded: bool
) -> GateVerdict:
    """Spec §5.1's table. `previous` is None only for an unrecorded verify; an
    unrecorded review arrives with the merge-base tree and `recorded` False."""
    _, hook, no_hook_run = _TABLE[gate]
    changed = f"the tree changed since the last recorded {gate}"
    # Unrecorded (review only past no-record): compared with the merge-base tree.
    fresh = f"no {gate} has run on this branch and the tree"
    if previous == tree:
        run, ask, reason = False, False, "unchanged"
        if recorded:
            message = f"the tree is unchanged since the last recorded {gate} — no {gate} runs"
        else:
            message = f"{fresh} is unchanged since its merge-base — no {gate} runs"
    elif previous is None:
        run, ask, reason = True, False, "no-record"
        message = f"no green {gate} is recorded in this worktree — {gate} runs"
    elif hooked:
        run, ask, reason = True, True, "hook"
        since = changed if recorded else f"{fresh} changed since its merge-base"
        message = f"{since} — the `{hook}` hook decides whether {gate} runs"
    else:
        run, ask, reason = no_hook_run, False, "no-hook"
        outcome = f"{gate} runs" if run else f"no {gate} runs"
        if recorded:
            message = f"{changed} and no `{hook}` hook is configured — {outcome}"
        else:
            message = (
                f"no {gate} has run on this branch and no `{hook}` hook is configured — {outcome}"
            )
    return GateVerdict(gate, run, ask, reason, tree, previous, message)


def question(ctx: ToolContext, gate: str) -> int:
    """Print one `OMC_GATE {…}` line for `gate`; 2 outside a repo, 1 on a git failure."""
    root = repo_root(ctx)
    if root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    try:
        tree = _git(ctx, root, "rev-parse", "HEAD^{tree}")
        state = _state_file(ctx, root, gate)
        previous = state.read_text().strip() if state.is_file() else ""
        recorded = bool(previous)
        if not recorded and gate == "review":
            # Never reviewed: compare against the tree the branch started from (D6).
            base = resolve.project_config(ctx).worktree.base_branch
            mb = _git(ctx, root, "merge-base", f"origin/{base}", "HEAD")
            previous = _git(ctx, root, "rev-parse", f"{mb}^{{tree}}")
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hooked = (Path(root) / ".omc" / "hooks" / _TABLE[gate][1] / "SKILL.md").is_file()
    verdict = _decide(gate, tree, previous or None, hooked, recorded)
    print(f"OMC_GATE {json.dumps(verdict.to_json())}", flush=True)
    return 0


def record(ctx: ToolContext, gate: str) -> int:
    """Write `HEAD^{tree}` to the gate's state file; 2 (nothing written) on
    uncommitted changes to tracked files or outside a repo, 1 on a git failure."""
    root = repo_root(ctx)
    if root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    try:
        if _git(ctx, root, "status", "--porcelain", "--untracked-files=no"):
            print(
                f"error: uncommitted changes to tracked files — amend them before"
                f" recording the {gate}",
                file=sys.stderr,
            )
            return 2
        tree = _git(ctx, root, "rev-parse", "HEAD^{tree}")
        state = _state_file(ctx, root, gate)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(tree + "\n")
    _say(f"✓ recorded the {gate} tree {tree[:12]}")
    return 0
