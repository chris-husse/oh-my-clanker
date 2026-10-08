"""Faithful-worktree wt configuration: create the starter when absent, sniff
existing configs and point at /omc:check-wt-config when they look off.

The starter has NO excludes on copy-ignored: cutting a worktree snapshots
main — .env, caches, AND the .gitnexus/.omc/docs knowledge dirs — refreshed
later by /omc:rebase-main.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import OmcError
from .toolctx import ToolContext

if TYPE_CHECKING:  # no runtime import: wtconfig is a leaf below config/
    from .config.schema import Config, ProjectConfig

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 50

WT_TEMPLATE = """\
# Worktrunk project config, seeded by omc — faithful worktrees out of the box.
# Every gitignored file (.env, caches, the .gitnexus/.omc knowledge snapshot)
# is reflink-copied into new worktrees; refresh a worktree's snapshot later
# with /omc:rebase-main. Docs: https://worktrunk.dev

# Blocking setup so the worktree is usable the moment you land in it.
pre-start = "{ ! test -f .gitmodules || git submodule update --init --recursive; } && { ! command -v direnv >/dev/null || direnv allow .; }"

[post-start]
copy-ignored = "wt step copy-ignored"
"""


def sanitize_slug(s: str) -> str:
    out = _NON_SLUG_RE.sub("-", s.replace("\n", " ").lower()).strip("-")
    return out[:_SLUG_MAX].rstrip("-")


def branch_for(cfg: Config | ProjectConfig, slug: str) -> str:
    """The branch `omc design` creates for a slug; the ONE place the prefix is applied."""
    return f"{cfg.worktree.branch_prefix}{slug}"


def slug_for(cfg: Config | ProjectConfig, branch: str) -> str | None:
    """Inverse of branch_for. None when the branch is not an omc branch: wrong
    (or missing) prefix, or a remainder that is not a sanitized slug. An empty
    configured prefix (allowed by the store) is always present — so then every
    sanitized name (even "main") is a slug, and a non-None result does NOT prove
    omc created the branch; the design-record lookup is what decides."""
    prefix = cfg.worktree.branch_prefix
    if not branch.startswith(prefix):
        return None
    rest = branch[len(prefix) :]
    if not rest or sanitize_slug(rest) != rest:
        return None
    return rest


def repo_root(ctx: ToolContext, root: str | Path | None = None) -> str | None:
    """The toplevel of the repo containing root (default cwd), or None outside a repo."""
    try:
        cp = ctx.run(
            [ctx.git_bin, "rev-parse", "--show-toplevel"],
            cwd=str(root) if root is not None else None,
        )
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    return (cp.stdout or "").strip() or None


def primary_root(ctx: ToolContext, root: str | Path | None = None) -> str | None:
    """First entry of `git worktree list --porcelain` = the primary checkout."""
    try:
        cp = ctx.run(
            [ctx.git_bin, "worktree", "list", "--porcelain"],
            cwd=str(root) if root is not None else None,
        )
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    for line in (cp.stdout or "").splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return None


def _has_copy_ignored(text: str) -> bool:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise
    post_start = data.get("post-start", {})
    if isinstance(post_start, dict):
        return any("copy-ignored" in str(v) for v in post_start.values())
    return False


def ensure_wt_config(ctx: ToolContext, root: str | Path) -> str:
    """Create-if-absent, sniff-if-present; NEVER edits an existing file.

    Returns "created" | "ok" | "suspicious". Notices go to stderr.
    """
    path = Path(root) / ".config" / "wt.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(WT_TEMPLATE)
        print(
            f"→ wrote {path} (faithful-worktree copy rules) — review and commit it",
            file=sys.stderr,
            flush=True,
        )
        return "created"
    try:
        if _has_copy_ignored(path.read_text()):
            return "ok"
        reason = "doesn't copy ignored files into worktrees"
    except tomllib.TOMLDecodeError:
        reason = "did not parse as TOML"
    print(
        f"→ existing {path} {reason} — run /omc:check-wt-config for an analysis",
        file=sys.stderr,
        flush=True,
    )
    return "suspicious"


SPECS_DIR = "docs/superpowers/specs"


@dataclass(frozen=True)
class RecordVerdict:
    """The design-record gate's answer — one rule for the CLI launcher, the
    internal verb and the in-session skill (spec §3.4)."""

    ok: bool
    slug: str = ""
    path: str = ""  # worktree-relative
    reason: str = ""  # one of no-prefix | missing | ambiguous | unclean when not ok
    message: str = ""

    def to_json(self) -> dict:
        if self.ok:
            return {"ok": True, "slug": self.slug, "path": self.path}
        return {"ok": False, "slug": self.slug, "reason": self.reason, "message": self.message}


def current_branch(ctx: ToolContext, root: str) -> str | None:
    try:
        cp = ctx.run([ctx.git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    return (cp.stdout or "").strip() or None


def _record_name_re(slug: str) -> re.Pattern[str]:
    # Anchored: a loose `*-<slug>-design.md` glob also matches a slug that
    # merely ENDS with this one (…-bar-<slug>-design.md). The date is fixed-width.
    return re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(slug)}-design\.md$")


def find_design_record(ctx: ToolContext, root: str, slug: str) -> RecordVerdict:
    """Exactly one `<date>-<slug>-design.md`, committed in HEAD and clean."""
    specs = Path(root) / SPECS_DIR
    pattern = _record_name_re(slug)
    names = sorted(p.name for p in specs.glob("*-design.md") if pattern.match(p.name))
    if not names:
        return RecordVerdict(
            False,
            slug,
            "",
            "missing",
            f"no design record for {slug} under {SPECS_DIR}/ — type /omc:design in a "
            "design session first",
        )
    if len(names) > 1:
        return RecordVerdict(
            False,
            slug,
            "",
            "ambiguous",
            f"{len(names)} design records for {slug}: {', '.join(names)} — keep exactly one",
        )
    rel = f"{SPECS_DIR}/{names[0]}"
    in_head = ctx.run([ctx.git_bin, "cat-file", "-e", f"HEAD:{rel}"], cwd=root)
    status = ctx.run([ctx.git_bin, "status", "--porcelain", "--", rel], cwd=root)
    if in_head.returncode != 0 or (status.stdout or "").strip():
        why = "is not committed in HEAD" if in_head.returncode != 0 else "has uncommitted changes"
        return RecordVerdict(
            False,
            slug,
            rel,
            "unclean",
            f"design record {rel} {why} — finish /omc:design "
            "(it commits the record) before implementing",
        )
    return RecordVerdict(True, slug, rel)


def resolve_design_record(ctx: ToolContext, cfg: Config | ProjectConfig) -> RecordVerdict:
    """Branch -> slug -> record, from the checkout containing cwd (never the
    primary: the record is committed on the feature branch)."""
    root = repo_root(ctx)
    if root is None:
        raise OmcError("not inside a git repository")
    branch = current_branch(ctx, root) or "HEAD"
    slug = slug_for(cfg, branch)
    if slug is None:
        if branch == "HEAD":
            message = (
                "detached HEAD (mid-rebase?) is not an omc branch — "
                "check out the feature branch and retry"
            )
        else:
            message = (
                f"branch {branch!r} is not an omc branch (expected "
                f"{cfg.worktree.branch_prefix}<slug>) — run this inside an omc worktree"
            )
        return RecordVerdict(False, "", "", "no-prefix", message)
    return find_design_record(ctx, root, slug)
