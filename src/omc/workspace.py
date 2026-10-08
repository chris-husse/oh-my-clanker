"""Workspace membership persisted under the caller's ToolContext.home.

Writers use update_ledger to serialize read-modify-write across processes.
Readers remain lock-free; atomic replacement gives them a complete snapshot.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config.resolve import project_config
from .errors import OmcError
from .gitnexus import redact_userinfo
from .toolctx import ToolContext
from .worktree import create_worktree, sync_base
from .wtconfig import (
    branch_for,
    current_branch,
    find_design_record,
    primary_root,
    repo_root,
    slug_for,
)


def workspace_id(master_key: str, slug: str) -> str:
    """Encode both identity components without ambiguous separators."""
    return json.dumps([master_key, slug], separators=(",", ":"))


def _origin_identity(origin: str) -> str:
    """Exclude URL credentials, retaining location and SSH login identity.

    Local paths and scp-style SSH names are already credential-free. HTTP
    userinfo is authentication only; SSH usernames select a remote account.
    """
    match = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/]*)(.*)", origin)
    if not match:
        return origin
    scheme, authority, path = match.groups()
    if "@" in authority:
        userinfo, host = authority.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        authority = f"{user}@{host}" if scheme.lower() == "ssh://" else host
    return scheme + authority + path


def _comparable_origin(origin: str) -> str:
    # Conservative equivalence: same transport, host (case insensitive), port,
    # SSH user and path; optional .git/trailing slash. Do not conflate scp's
    # home-relative path with ssh:// absolute paths or distinct transports.
    origin = _origin_identity(origin)
    match = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/]*)(.*)", origin)
    if match:
        scheme, authority, path = match.groups()
        if scheme.lower() == "file://":
            return origin
        user, at, host = authority.rpartition("@")
        authority = user + at + host.lower()
        return scheme.lower() + authority + path.rstrip("/").removesuffix(".git")
    if not origin.startswith(("/", ".", "~")) and ":" in origin:
        host, path = origin.split(":", 1)
        user, at, hostname = host.rpartition("@")
        return user + at + hostname.lower() + ":" + path.rstrip("/").removesuffix(".git")
    return origin


def _safe_output(value):
    """Sanitize every nested verdict string, including subprocess diagnostics."""
    if isinstance(value, str):
        return redact_userinfo(value)
    if isinstance(value, dict):
        return {_safe_output(key): _safe_output(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_output(item) for item in value]
    return value


def _say(message: str) -> None:
    print(redact_userinfo(message), file=sys.stderr, flush=True)


def repository_key(wt_data: dict, origin: str) -> str:
    """Prefer complete forge identity, using credential-free origin as fallback."""
    repo = wt_data.get("repo")
    forge = repo.get("forge") if isinstance(repo, dict) else None
    if isinstance(forge, dict):
        fields = [forge.get(field) for field in ("host", "owner", "name")]
        if all(isinstance(field, str) and field.strip() for field in fields):
            return redact_userinfo("/".join(fields))
    return _origin_identity(origin)


def ordered_repositories(workspace: dict) -> list[dict]:
    """Visit dependencies in registration order, then the master."""
    return sorted(workspace["repositories"], key=lambda repo: repo["role"] == "master")


def ledger_path(home: Path) -> Path:
    return home / "workspaces.json"


def _validate_ledger(data: object, path: Path) -> None:
    def require(condition: bool, detail: str) -> None:
        if not condition:
            raise OmcError(f"corrupt workspace ledger {path}: {detail}")

    def nonempty(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    require(isinstance(data, dict), "expected a JSON object")
    require(type(data.get("version")) is int and data["version"] == 1, "unsupported version")
    require(isinstance(data.get("workspaces"), dict), "workspaces must be an object")
    for identity, workspace in data["workspaces"].items():
        require(isinstance(workspace, dict), "workspace must be an object")
        require(nonempty(workspace.get("master")), "workspace master must be a nonempty key")
        require(nonempty(workspace.get("slug")), "workspace slug must be a nonempty string")
        require(
            identity == workspace_id(workspace["master"], workspace["slug"]),
            "workspace identity does not match master and slug",
        )
        repositories = workspace.get("repositories")
        require(
            isinstance(repositories, list) and bool(repositories), "repositories must be a list"
        )
        keys = set()
        masters = []
        for repository in repositories:
            require(isinstance(repository, dict), "repository must be an object")
            for field in ("key", "primary", "worktree", "branch", "base", "role"):
                require(nonempty(repository.get(field)), f"repository {field} must be a string")
            for field in ("primary", "worktree"):
                require(
                    Path(repository[field]).is_absolute(), f"repository {field} must be absolute"
                )
            require(repository["role"] in ("master", "dependency"), "invalid repository role")
            require(repository["key"] not in keys, "duplicate repository key")
            keys.add(repository["key"])
            if repository["role"] == "master":
                masters.append(repository["key"])
        require(
            masters == [workspace["master"]], "workspace must have its named master exactly once"
        )


def load_ledger(home: Path) -> dict:
    """Read a complete snapshot, refusing corruption rather than resetting it."""
    path = ledger_path(home)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"version": 1, "workspaces": {}}
    except (OSError, ValueError) as exc:
        raise OmcError(f"corrupt workspace ledger {path}: {exc}") from exc
    _validate_ledger(data, path)
    return data


def save_ledger(home: Path, data: dict) -> None:
    """Atomically replace a snapshot; read-modify-write callers use update_ledger."""
    path = ledger_path(home)
    _validate_ledger(data, path)
    home.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=home, prefix=".workspaces.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def update_ledger(home: Path, mutate: Callable[[dict], None]) -> dict:
    """Hold the sibling lock through a fresh read, mutation, and atomic save."""
    home.mkdir(parents=True, exist_ok=True)
    with (home / "workspaces.json.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = load_ledger(home)
            mutate(data)
            save_ledger(home, data)
            return data
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class _WorkspaceFailure(Exception):
    def __init__(self, code: int, reason: str, message: str, **fields):
        self.code = code
        self.payload = {"ok": False, "reason": reason, "message": message, **fields}
        super().__init__(message)


def _emit(payload: dict, code: int = 0) -> int:
    print(f"OMC_WORKSPACE {json.dumps(_safe_output(payload))}", flush=True)
    return code


def _run(action: Callable[[], dict]) -> int:
    """One stdout boundary for usage, refusal, bail, and operational errors."""
    try:
        return _emit(action())
    except _WorkspaceFailure as exc:
        return _emit(exc.payload, exc.code)
    except (OmcError, OSError, ValueError) as exc:
        return _emit({"ok": False, "reason": "error", "message": str(exc)}, 1)


def _inspect(ctx: ToolContext, primary: str) -> dict:
    # Worktrunk 0.68 defaults to schema 1 (an array). Pin the schema whose
    # repo/items, forge and upstream fields this boundary consumes.
    cp = ctx.run(
        [ctx.wt_bin, "-C", primary, "--config-set", "list.json-schema=2", "list", "--format=json"]
    )
    if cp.returncode:
        raise _WorkspaceFailure(
            1,
            "inspection-failed",
            (cp.stderr or cp.stdout or "wt list failed").strip(),
            primary=primary,
        )
    try:
        data = json.loads(cp.stdout)
    except (ValueError, TypeError) as exc:
        raise _WorkspaceFailure(
            1, "inspection-failed", f"invalid wt list JSON: {exc}", primary=primary
        ) from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("repo"), dict)
        or not isinstance(data.get("items"), list)
        or any(not isinstance(item, dict) for item in data["items"])
    ):
        raise _WorkspaceFailure(
            1, "inspection-failed", "invalid wt list structure", primary=primary
        )
    forge = data["repo"].get("forge")
    if forge is not None and (
        not isinstance(forge, dict)
        or any(value is not None and not isinstance(value, str) for value in forge.values())
    ):
        raise _WorkspaceFailure(1, "inspection-failed", "invalid forge inspection", primary=primary)
    return data


def _origin(ctx: ToolContext, primary: str) -> str:
    cp = ctx.run([ctx.git_bin, "remote", "get-url", "origin"], cwd=primary)
    if cp.returncode or not (cp.stdout or "").strip():
        raise _WorkspaceFailure(1, "missing-origin", "repository has no origin", primary=primary)
    return cp.stdout.strip()


def _current(ctx: ToolContext, *, adding: bool = False) -> tuple[dict, str]:
    root = repo_root(ctx)
    primary = primary_root(ctx)
    if root is None or primary is None:
        raise _WorkspaceFailure(2, "not-a-repository", "run inside an omc worktree")
    root, primary = str(Path(root).resolve()), str(Path(primary).resolve())
    if adding and root == primary:
        raise _WorkspaceFailure(
            2, "primary-checkout", "register from the feature worktree, not the primary checkout"
        )
    cfg = project_config(ctx, root)
    branch = current_branch(ctx, root) or "HEAD"
    slug = slug_for(cfg, branch)
    if branch == "HEAD" or slug is None:
        raise _WorkspaceFailure(
            2, "no-prefix", "current branch is not an omc feature branch", branch=branch
        )
    data = _inspect(ctx, primary)
    entry = {
        "key": repository_key(data, _origin(ctx, primary)),
        "primary": primary,
        "worktree": root,
        "branch": branch,
        "base": cfg.worktree.base_branch,
        "role": "master",
    }
    return entry, slug


def _membership(data: dict, entry: dict, slug: str) -> tuple[str, dict, dict] | None:
    matches = []
    for identity, workspace in data["workspaces"].items():
        if workspace["slug"] != slug:
            continue
        for repository in workspace["repositories"]:
            if (
                repository["key"] == entry["key"]
                and repository["branch"] == entry["branch"]
                and Path(repository["worktree"]).resolve() == Path(entry["worktree"]).resolve()
            ):
                matches.append((identity, workspace, repository))
    if len(matches) > 1:
        raise _WorkspaceFailure(
            2, "membership-collision", "worktree belongs to more than one workspace"
        )
    return matches[0] if matches else None


def _check_registration(data: dict, identity: str, master: dict, dependency: dict) -> dict | None:
    """Read-only check reused before cutting and inside the final ledger lock."""
    existing = None
    for other_id, workspace in data["workspaces"].items():
        for entry in workspace["repositories"]:
            for candidate in (master, dependency):
                same_branch = (
                    entry["key"] == candidate["key"] and entry["branch"] == candidate["branch"]
                )
                same_tree = (
                    candidate.get("worktree")
                    and Path(entry["worktree"]).resolve() == Path(candidate["worktree"]).resolve()
                )
                if other_id != identity and (same_branch or same_tree):
                    raise _WorkspaceFailure(
                        2,
                        "membership-collision",
                        "repository branch already belongs to another workspace",
                        repository=entry,
                    )
            if other_id == identity and entry["key"] == dependency["key"]:
                if (
                    entry["primary"] != dependency["primary"]
                    or entry["branch"] != dependency["branch"]
                ):
                    raise _WorkspaceFailure(
                        2,
                        "membership-collision",
                        "repository is already registered with a different checkout or branch",
                        repository=entry,
                    )
                existing = entry
            if other_id == identity and entry["role"] == "master" and entry != master:
                raise _WorkspaceFailure(
                    2,
                    "membership-collision",
                    "workspace master checkout has changed",
                    repository=entry,
                )
    return existing


def _resolve_target(ctx: ToolContext, master: dict, target: str, path: str | None) -> str:
    if not target.strip() or target.startswith("-"):
        raise _WorkspaceFailure(2, "usage", "target must name a repository")
    explicit_url = "://" in target or (":" in target and not target.startswith("/"))
    projects = Path(master["primary"]).parent
    name = target.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    sibling = projects / name
    direct = Path(target).expanduser()
    # An existing path is explicit; an ordinary repository name prefers its
    # sibling primary. Nonexistent destinations require the explicit --path.
    if path is not None:
        destination = Path(path).expanduser().resolve()
    elif "/" not in target and ":" not in target and sibling.exists():
        destination = sibling.resolve()
    elif direct.exists():
        destination = direct.resolve()
    else:
        if not name or name in (".", "..") or target.startswith("-"):
            raise _WorkspaceFailure(2, "usage", "target must name a repository")
        destination = projects / name
    if destination.exists():
        if not (destination / ".git").is_dir():
            raise _WorkspaceFailure(
                2,
                "occupied",
                "destination is not a primary repository checkout",
                checkout=str(destination),
            )
        root = repo_root(ctx, destination)
        primary = primary_root(ctx, destination)
        if root is None or primary is None or Path(root).resolve() != destination:
            raise _WorkspaceFailure(
                2, "occupied", "destination is not a repository checkout", checkout=str(destination)
            )
        if explicit_url and _comparable_origin(_origin(ctx, str(primary))) != _comparable_origin(
            target
        ):
            raise _WorkspaceFailure(
                2,
                "origin-mismatch",
                "destination origin differs from the requested repository; "
                "use --path with a different destination",
                checkout=str(destination),
                origin=_origin_identity(target),
            )
        return str(Path(primary).resolve())
    if path is None and not explicit_url and ("/" in target or target in (".", "..")):
        raise _WorkspaceFailure(2, "usage", "use --path for a new clone destination")
    if (
        path is None
        and sum((child / ".git").is_dir() for child in projects.iterdir() if child.is_dir()) < 2
    ):
        raise _WorkspaceFailure(
            3,
            "not-a-projects-folder",
            "clone destination needs two sibling primary repositories or an explicit --path",
            checkout=str(destination),
        )
    origin = _origin(ctx, master["primary"])
    if explicit_url:
        url = target
    else:
        suffix = ".git" if origin.endswith(".git") else ""
        if "/" in origin:
            url = origin.rsplit("/", 1)[0] + "/" + name + suffix
        elif ":" in origin:
            url = origin.rsplit(":", 1)[0] + ":" + name + suffix
        else:
            raise _WorkspaceFailure(
                3, "unresolved", "cannot derive a sibling origin from the master origin"
            )
    _say(f"→ checking {url}")
    cp = ctx.run([ctx.git_bin, "ls-remote", url])
    if cp.returncode:
        raise _WorkspaceFailure(3, "unresolved", "could not resolve repository origin", origin=url)
    _say(f"→ cloning into {destination}")
    cp = ctx.run([ctx.git_bin, "clone", "--", url, str(destination)])
    if cp.returncode:
        raise _WorkspaceFailure(
            1,
            "clone-failed",
            (cp.stderr or cp.stdout or "git clone failed").strip(),
            checkout=str(destination),
        )
    primary = primary_root(ctx, destination)
    if primary is None:
        raise _WorkspaceFailure(
            1, "clone-failed", "clone did not produce a repository", checkout=str(destination)
        )
    return str(Path(primary).resolve())


def _payload(workspace: dict | None, slug: str, current: dict | None) -> dict:
    master = (
        next((entry for entry in workspace["repositories"] if entry["role"] == "master"), None)
        if workspace
        else None
    )
    return {
        "ok": True,
        "slug": slug,
        "current_role": current["role"] if current else None,
        "master_worktree": master["worktree"] if master else None,
        "repositories": ordered_repositories(workspace) if workspace else [],
    }


def _add(ctx: ToolContext, target: str, path: str | None) -> dict:
    master, slug = _current(ctx, adding=True)
    data = load_ledger(ctx.home)
    member = _membership(data, master, slug)
    if member and member[2]["role"] != "master":
        raise _WorkspaceFailure(
            2, "membership-collision", "register repositories from the master worktree"
        )
    identity = workspace_id(master["key"], slug)
    primary = _resolve_target(ctx, master, target, path)
    if primary == master["primary"]:
        raise _WorkspaceFailure(2, "master-self", "cannot add the master repository to itself")
    if not (Path(primary) / ".omc").is_dir():
        raise _WorkspaceFailure(
            2,
            "not-omc-aware",
            "repository needs omc integration; retry or handle it by hand",
            checkout=primary,
        )
    cfg = project_config(ctx, primary)
    dependency = {
        "key": repository_key(_inspect(ctx, primary), _origin(ctx, primary)),
        "primary": primary,
        "branch": branch_for(cfg, slug),
        "base": cfg.worktree.base_branch,
        "role": "dependency",
    }
    if dependency["key"] == master["key"]:
        raise _WorkspaceFailure(
            2, "master-self", "cannot add another checkout of the master repository"
        )
    existing = _check_registration(data, identity, master, dependency)
    if existing:
        return {**_payload(data["workspaces"][identity], slug, master), "repository": existing}
    _say(f"→ preparing {primary} ({dependency['branch']})")
    sync_base(ctx, dependency["base"], cwd=primary, say=_say)
    worktree = create_worktree(
        ctx, dependency["branch"], f"origin/{dependency['base']}", cwd=primary, say=_say
    )
    if worktree is None:
        raise _WorkspaceFailure(
            1, "worktree-failed", "could not create or enter dependency worktree", checkout=primary
        )
    dependency["worktree"] = str(Path(worktree).resolve())

    def register(fresh: dict) -> None:
        # Fetch/clone/cut stay outside this lock. Recheck ownership against
        # the fresh snapshot so concurrent registrations never share a branch.
        previous = _check_registration(fresh, identity, master, dependency)
        if previous:
            if previous["worktree"] != dependency["worktree"]:
                raise _WorkspaceFailure(
                    2, "membership-collision", "repository was registered with a different worktree"
                )
            return
        workspace = fresh["workspaces"].setdefault(
            identity, {"master": master["key"], "slug": slug, "repositories": [master]}
        )
        workspace["repositories"].append(dependency)

    updated = update_ledger(ctx.home, register)
    return {**_payload(updated["workspaces"][identity], slug, master), "repository": dependency}


def run_add(ctx: ToolContext, target: str, *, path: str | None = None) -> int:
    return _run(lambda: _add(ctx, target, path))


def _url_segment(value: str) -> str:
    # Percent-encode path data without introducing a network-library boundary.
    safe = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(chr(byte) if byte in safe else f"%{byte:02X}" for byte in value.encode())


def _compare_url(data: dict, entry: dict) -> str | None:
    forge = data["repo"].get("forge")
    if not isinstance(forge, dict):
        return None
    url, provider = forge.get("url"), forge.get("provider")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None
    base, branch = _url_segment(entry["base"]), _url_segment(entry["branch"])
    suffix = {
        "github": f"/compare/{base}...{branch}",
        "gitlab": f"/-/compare/{base}...{branch}",
        "bitbucket": f"/branches/compare/{branch}..{base}",
    }.get(provider)
    return _origin_identity(url).rstrip("/").removesuffix(".git") + suffix if suffix else None


def _worktree_item(ctx: ToolContext, entry: dict) -> tuple[dict, dict]:
    if not Path(entry["worktree"]).is_dir():
        raise _WorkspaceFailure(
            1, "missing-worktree", "registered worktree is missing", worktree=entry["worktree"]
        )
    data = _inspect(ctx, entry["primary"])
    matches = [
        item
        for item in data["items"]
        if item.get("branch") == entry["branch"]
        and isinstance(item.get("worktree"), dict)
        and isinstance(item["worktree"].get("path"), str)
        and Path(item["worktree"]["path"]).resolve() == Path(entry["worktree"]).resolve()
    ]
    if len(matches) != 1:
        raise _WorkspaceFailure(
            1,
            "missing-worktree",
            "wt list did not identify the registered branch and worktree",
            worktree=entry["worktree"],
        )
    return data, matches[0]


def _enrich(ctx: ToolContext, entry: dict, slug: str) -> dict:
    data, item = _worktree_item(ctx, entry)
    upstream = item.get("upstream")
    if upstream is not None and not isinstance(upstream, dict):
        raise _WorkspaceFailure(
            1, "inspection-failed", "invalid upstream inspection", worktree=entry["worktree"]
        )

    def count(name: str) -> int | None:
        value = upstream.get(name) if upstream else None
        if value is not None and (type(value) is not int or value < 0):
            raise _WorkspaceFailure(
                1, "inspection-failed", f"invalid upstream {name}", worktree=entry["worktree"]
            )
        return value

    return {
        **entry,
        "record": find_design_record(ctx, entry["worktree"], slug).to_json(),
        "upstream": upstream,
        "ahead": count("ahead"),
        "behind": count("behind"),
        "compare_url": _compare_url(data, entry),
    }


def _list(ctx: ToolContext) -> dict:
    current, slug = _current(ctx)
    member = _membership(load_ledger(ctx.home), current, slug)
    if member is None:
        return _payload(None, slug, None)
    _, workspace, entry = member
    payload = _payload(workspace, slug, entry)
    payload["repositories"] = [_enrich(ctx, repo, slug) for repo in ordered_repositories(workspace)]
    return payload


def run_list(ctx: ToolContext) -> int:
    return _run(lambda: _list(ctx))


def _close_entry(data: dict, identity: str, entry: dict) -> dict:
    """Fail closed if membership or close order changed since inspection."""
    workspace = data["workspaces"].get(identity)
    if workspace is None or ordered_repositories(workspace)[0] != entry:
        raise _WorkspaceFailure(
            2,
            "workspace-changed",
            "workspace membership changed during closure; inspect it before retrying",
            repository=entry,
        )
    return workspace


def _close_worktree(ctx: ToolContext, identity: str, entry: dict) -> None:
    primary, branch, base = entry["primary"], entry["branch"], entry["base"]
    _worktree_item(ctx, entry)
    _say(f"→ checking merge of {primary} ({branch})")
    # Closure cannot use sync_base's best-effort policy: stale merge evidence
    # must never authorize removal after a failed fetch.
    # Explicitly refresh the ref checked below, even if remote.origin.fetch
    # excludes this base or the remote base has moved backwards.
    commands = [
        (
            [ctx.git_bin, "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"],
            "fetch-failed",
        ),
        (
            [ctx.git_bin, "merge-base", "--is-ancestor", branch, f"origin/{base}"],
            "merge-check-failed",
        ),
        ([ctx.wt_bin, "-C", primary, "remove", branch], "remove-failed"),
    ]
    for argv, reason in commands:
        if reason == "remove-failed":
            _close_entry(load_ledger(ctx.home), identity, entry)
            _say(f"→ removing {entry['worktree']}")
        try:
            cp = ctx.run(argv, cwd=primary)
        except OSError as exc:
            raise _WorkspaceFailure(1, reason, str(exc), repository=entry) from exc
        if reason == "merge-check-failed" and cp.returncode == 1:
            raise _WorkspaceFailure(
                2,
                "unmerged",
                f"{branch} is not merged into origin/{base}",
                repository=entry,
            )
        if cp.returncode:
            raise _WorkspaceFailure(
                1,
                reason,
                (cp.stderr or cp.stdout or f"{reason}: exit {cp.returncode}").strip(),
                repository=entry,
            )


def _close(ctx: ToolContext) -> dict:
    current, slug = _current(ctx)
    member = _membership(load_ledger(ctx.home), current, slug)
    if member is None:
        raise _WorkspaceFailure(2, "no-workspace", "current worktree has no registered workspace")
    identity, workspace, registered = member
    if registered["role"] != "master":
        raise _WorkspaceFailure(
            2,
            "not-master",
            "close the workspace from its master worktree",
            repository=registered,
            master_worktree=next(
                entry["worktree"]
                for entry in workspace["repositories"]
                if entry["role"] == "master"
            ),
        )
    removed = []
    for entry in ordered_repositories(workspace):
        _close_entry(load_ledger(ctx.home), identity, entry)
        _close_worktree(ctx, identity, entry)

        def drop(fresh: dict, entry: dict = entry) -> None:
            remaining = _close_entry(fresh, identity, entry)
            if entry["role"] == "master":
                del fresh["workspaces"][identity]
            else:
                remaining["repositories"].remove(entry)

        # Only successful removal earns a ledger update. A fresh locked read
        # preserves other writers and refuses to erase a replacement entry.
        update_ledger(ctx.home, drop)
        removed.append(entry)
    return {**_payload(None, slug, None), "removed": removed}


def run_close(ctx: ToolContext) -> int:
    return _run(lambda: _close(ctx))


def _implementation_status(ctx: ToolContext, slug: str) -> dict:
    """Read Git evidence only; a design/plan commit is not a build handoff."""

    def git(*args: str, cwd: str | None = None) -> str:
        result = ctx.run([ctx.git_bin, *args], cwd=cwd)
        if result.returncode:
            raise OmcError(f"implementation inspection failed: {result.stderr.strip()}")
        return result.stdout

    root = git("rev-parse", "--show-toplevel").strip()
    incomplete = {"ok": True, "slug": slug, "complete": False}
    if git("status", "--porcelain", "--untracked-files=all", cwd=root).strip():
        return incomplete
    paths = git(
        "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", "docs/superpowers/plans", cwd=root
    ).split("\0")
    plans = [
        path
        for path in paths
        if Path(path).parent.as_posix() == "docs/superpowers/plans"
        and Path(path).name.endswith(f"-{slug}-plan.md")
    ]
    if len(plans) != 1:
        return incomplete
    # The first addition, not the latest edit: handoff may amend the plan.
    additions = git(
        "log", "--reverse", "--diff-filter=A", "--format=%H", "HEAD", "--", plans[0], cwd=root
    ).splitlines()
    if not additions:
        return incomplete
    # Ancestry, not timestamps. The plan-add commit itself cannot count.
    changes = git(
        "log",
        "--ancestry-path",
        "--format=",
        "--name-only",
        "-z",
        "-m",
        f"{additions[0]}..HEAD",
        "--",
        ".",
        ":(exclude)docs/superpowers/specs",
        ":(exclude)docs/superpowers/plans",
        cwd=root,
    )
    return {"ok": True, "slug": slug, "complete": bool(changes.strip("\0\n"))}


class _WorkspaceParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally prints raw arguments to stderr before SystemExit.
        raise ValueError(message)


def run_workspace(ctx: ToolContext, argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "implementation-status":
        return _run(lambda: _implementation_status(ctx, argv[1]))
    if argv == ["list"]:
        return run_list(ctx)
    if argv == ["close"]:
        return run_close(ctx)
    if argv[:1] == ["add"]:
        parser = _WorkspaceParser(prog="omc internal workspace add", add_help=False)
        parser.add_argument("target")
        parser.add_argument("--path")
        try:
            args = parser.parse_args(argv[1:])
        except ValueError as exc:
            return _emit({"ok": False, "reason": "usage", "message": str(exc)}, 2)
        else:
            return run_add(ctx, args.target, path=args.path)
    return _emit(
        {
            "ok": False,
            "reason": "usage",
            "message": (
                "usage: omc internal workspace {add TARGET [--path PATH] | list | close"
                " | implementation-status SLUG}"
            ),
        },
        2,
    )
