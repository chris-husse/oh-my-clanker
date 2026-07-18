"""The AGENTS.md control chain, v2: root AGENTS.md + CLAUDE.md are
machine-local, gitignored symlinks into the INSTALLED omc package's
distribution/AGENTS.md, which defers to the project-owned
.omc/config/AGENTS.md.

`uv tool upgrade omc` replacing the venv is the whole propagation story —
every managed repo serves the new behavior layer instantly. The v1 chain
(root symlinks -> committed .omc/internal/AGENTS.md stamped from a constant)
is migrated automatically; omc never touches the project layer and never
replaces files it does not own.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .errors import OmcError
from .installsrc import package_root
from .toolctx import ToolContext

_V1_INTERNAL_REL = Path(".omc/internal/AGENTS.md")
_PROJECT_REL = Path(".omc/config/AGENTS.md")
_DISTRIBUTION_REL = Path("distribution/AGENTS.md")
_ROOT_NAMES = ("AGENTS.md", "CLAUDE.md")
_GITIGNORE_ENTRIES = ("/AGENTS.md", "/CLAUDE.md")

PROJECT_STARTER = """\
# Project agent instructions

This file is YOURS — omc seeds it once and never touches it again. Put the
project's real guidance here: build/test commands, architecture ground
rules, review expectations, tribal knowledge. Every agent reads it right
after omc's behavior layer (the root AGENTS.md/CLAUDE.md symlinks).
"""


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def distribution_agents_md() -> Path:
    """The installed behavior-layer file — the chain's symlink target."""
    target = package_root() / _DISTRIBUTION_REL
    if not target.is_file():
        raise OmcError(f"broken install: {target} is missing")
    return target


def is_omc_link(link: Path) -> bool:
    """True when `link` is a symlink omc owns (v1, v2, or a stale v2 from a
    previous install location) and may therefore repair or migrate."""
    if not link.is_symlink():
        return False
    raw = os.readlink(link)
    if raw.endswith(str(_V1_INTERNAL_REL)):
        return True  # v1 relative link
    return raw.endswith(str(_DISTRIBUTION_REL))  # v2, current or stale


def chain_healthy(root: str | Path) -> bool:
    """Cheap read-only probe: both root links exist and hit the live target."""
    root = Path(root)
    target = distribution_agents_md().resolve()
    return all(
        (root / name).is_symlink() and (root / name).resolve() == target for name in _ROOT_NAMES
    )


def _ensure_gitignore(root: Path) -> bool:
    """Append-only: add missing root-anchored entries, never rewrite content."""
    gi = root / ".gitignore"
    text = gi.read_text() if gi.is_file() else ""
    missing = [e for e in _GITIGNORE_ENTRIES if e not in text.splitlines()]
    if not missing:
        return False
    chunk = "" if not text or text.endswith("\n") else "\n"
    chunk += "# machine-local omc chain symlinks (targets differ per machine)\n"
    chunk += "".join(f"{e}\n" for e in missing)
    gi.write_text(text + chunk)
    return True


def ensure_agents_chain(ctx: ToolContext, root: str | Path) -> str:
    """Verify/create the v2 chain. Returns "created" | "ok" | "blocked".

    - Root AGENTS.md/CLAUDE.md: absolute symlinks to the installed
      distribution/AGENTS.md; gitignored (entries ensured, append-only).
    - v1 chain artifacts (omc's own relative symlinks + the stamped
      .omc/internal/AGENTS.md) migrate automatically.
    - Foreign regular files or unknown symlinks: NEVER replaced — chain is
      "blocked" with migration steps and NOTHING is mutated.
    - .omc/config/AGENTS.md: seeded only if absent (the project owns it).
    """
    root = Path(root)
    target = distribution_agents_md()
    resolved_target = target.resolve()

    # Check the root files FIRST: a blocked chain must not half-mutate the repo.
    blocked = []
    for name in _ROOT_NAMES:
        link = root / name
        if not link.exists() and not link.is_symlink():
            continue  # missing -> creatable
        if not is_omc_link(link):
            blocked.append(name)
    if blocked:
        _say(
            f"→ {', '.join(blocked)} already exist and are not omc's symlinks — "
            "omc will not replace them. To adopt the omc chain: move your content "
            f"into {_PROJECT_REL}, delete the root file(s), and re-run `omc configure`."
        )
        return "blocked"

    created = False
    for name in _ROOT_NAMES:
        link = root / name
        if link.is_symlink():
            if link.resolve() == resolved_target:
                continue  # already correct
            link.unlink()  # v1 or stale v2 — replace
        link.symlink_to(target)
        created = True

    internal = root / _V1_INTERNAL_REL
    if internal.is_file():
        internal.unlink()  # v1 stamped layer retired; content now ships installed
        if internal.parent.is_dir() and not any(internal.parent.iterdir()):
            internal.parent.rmdir()
        created = True

    project = root / _PROJECT_REL
    if not project.exists():
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(PROJECT_STARTER)
        created = True

    if _ensure_gitignore(root):
        created = True

    if created:
        _say(
            "→ AGENTS.md/CLAUDE.md now symlink into the omc install "
            f"({target}); they are machine-local (gitignored) — project guidance "
            f"lives in {_PROJECT_REL}, commit that one"
        )
        return "created"
    return "ok"
