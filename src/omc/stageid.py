"""Stage identity: the project's `.omc/project.id` and each worktree's `.cache/worktree.id`."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import wtconfig
from .config import resolve
from .errors import OmcError, Refusal
from .fsutil import create_exclusive, write_atomic
from .toolctx import ToolContext

PROJECT_ID = ".omc/project.id"  # relative to the primary checkout; committed by the project


@dataclass(frozen=True)
class StageIds:
    project: str
    worktree: str
    primary: Path
    root: Path


def ensure_cache(root: Path) -> Path:
    """`<root>/.cache`, created with a `.gitignore` of `*` so git never shows it."""
    cache = root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    ignore = cache / ".gitignore"
    if not ignore.exists():
        create_exclusive(ignore, "*\n")
    return cache


def _committed_project_id(ctx: ToolContext, primary: Path) -> str | None:
    """The project ID committed at HEAD, else on origin's base branch (another clone may
    have committed it), else at origin's HEAD; None when none of them has a valid one."""
    base = resolve.project_config(ctx).worktree.base_branch
    for ref in ("HEAD", f"origin/{base}", "origin/HEAD"):
        cp = ctx.run([ctx.git_bin, "show", f"{ref}:{PROJECT_ID}"], cwd=primary)
        if cp.returncode != 0:
            continue
        try:
            return str(uuid.UUID((cp.stdout or "").strip()))
        except ValueError:
            continue
    return None


def project_id(ctx: ToolContext, primary: Path) -> str:
    """The UUID in `<primary>/.omc/project.id`. A missing file is created with the ID
    committed upstream when there is one (so every clone agrees on the project's ID),
    else a new one; an empty file gets a new one. The created file is untracked, so a
    later sync that brings the committed one still refuses to overwrite it: watch's
    `yield_to_committed_id` repairs that."""
    path = primary / PROJECT_ID
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        create_exclusive(path, f"{_committed_project_id(ctx, primary) or uuid.uuid4()}\n")
        raw = path.read_bytes()
    else:
        if not raw.strip():
            write_atomic(path, f"{uuid.uuid4()}\n")
            raw = path.read_bytes()
    try:
        return str(uuid.UUID(raw.decode("utf-8", "replace").strip()))
    except ValueError:
        raise OmcError(f"{path}: not a UUID — fix or delete it") from None


def yield_to_committed_id(ctx: ToolContext, root: str, base: str, stderr: str) -> bool:
    """After git refused to ff-merge or rebase onto origin/<base>: when the one path it
    would overwrite is an untracked `.omc/project.id` that origin/<base> commits, remove
    it (the committed ID wins) and say so on stderr; True means retry once.

    `project_id` adopts a committed ID, but one made before another clone committed its
    own stays untracked here, and git's overwrite refusal would block every sync."""
    blocking = [line.strip() for line in stderr.splitlines() if line.startswith("\t")]
    if blocking != [PROJECT_ID]:
        return False
    tracked = ctx.run([ctx.git_bin, "ls-files", "--", PROJECT_ID], cwd=root)
    if tracked.returncode != 0 or (tracked.stdout or "").strip():
        return False  # tracked here (or unknowable): not the collision this repairs
    committed = ctx.run([ctx.git_bin, "cat-file", "-e", f"origin/{base}:{PROJECT_ID}"], cwd=root)
    if committed.returncode != 0:
        return False
    print(
        f"→ removing the local untracked {PROJECT_ID}: origin/{base} commits one, which wins",
        file=sys.stderr,
        flush=True,
    )
    (Path(root) / PROJECT_ID).unlink(missing_ok=True)
    return True


def _own_id(raw: bytes, here: str) -> str | None:
    """The ID in a worktree.id payload when it names `here`; None when unusable."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("path") == here:
            return str(uuid.UUID(data["id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def worktree_id(root: Path) -> str:
    """The UUID in `<root>/.cache/worktree.id`; a file naming another path is replaced."""
    path = root / ".cache" / "worktree.id"
    here = str(root.resolve())
    payload = json.dumps({"id": str(uuid.uuid4()), "path": here}) + "\n"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        ensure_cache(root)
        create_exclusive(path, payload)
    else:
        found = _own_id(raw, here)
        if found is not None:
            return found
        # Copied in from another worktree, or unparseable: replace it outright.
        write_atomic(path, payload)
    found = _own_id(path.read_bytes(), here)
    if found is None:
        raise OmcError(f"{path}: rewritten but still not this worktree's ID")
    return found


def resolve_ids(ctx: ToolContext) -> StageIds:
    """Both IDs for the repo around the process cwd."""
    root = wtconfig.repo_root(ctx)
    primary = wtconfig.primary_root(ctx)
    if root is None or primary is None:
        raise Refusal("not inside a git repository")
    root_path, primary_path = Path(root), Path(primary)
    return StageIds(
        project=project_id(ctx, primary_path),
        worktree=worktree_id(root_path),
        primary=primary_path,
        root=root_path,
    )
