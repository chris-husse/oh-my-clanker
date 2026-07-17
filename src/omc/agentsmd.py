"""The AGENTS.md control chain: root AGENTS.md + CLAUDE.md symlink into
omc-owned `.omc/internal/AGENTS.md`, which defers to the project-owned
`.omc/config/AGENTS.md`.

omc needs deterministic control over how agents behave in omc-managed repos —
across Claude Code (CLAUDE.md), Codex, and OpenCode (AGENTS.md) — without
owning the project's voice. omc regenerates the internal layer; it never
touches the project layer and never replaces existing regular root files.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .toolctx import ToolContext

_INTERNAL_REL = Path(".omc/internal/AGENTS.md")
_PROJECT_REL = Path(".omc/config/AGENTS.md")

INTERNAL_AGENTS_MD = """\
# omc behavior layer (generated — do not edit; `omc configure` regenerates it)

This repo is omc-managed. Root `AGENTS.md`/`CLAUDE.md` resolve here so every
harness (Claude Code, Codex, OpenCode) gets the same ground rules:

- **Worktrees are snapshots of main** — code AND knowledge (`.gitnexus/`,
  `.omc/docs/`). Refresh a worktree with `/omc:rebase-main` (it is also
  `/omc:finish`'s first step). Never hand-copy or hand-delete those dirs;
  the deterministic mirror lives in `omc internal rebase-main`.
- **Finish work through `/omc:finish`** — rebase, squash, project stage gates
  (`/omc:build` → `/omc:verify` → `/omc:review`), described push. Do not
  bypass a failing stage.
- **Ask the graph, not grep**: `/omc:explain <question>` answers from the
  project's GitNexus knowledge graph and docs.
- **Model selection**: the main session runs the model chosen in
  `omc configure` — never second-guess it. When dispatching subagents,
  assess each task and pick the model that fits: the heavyweight model for
  planning/design, reviews, and judging subagent output; efficient models
  for well-specified execution work.
- **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
  `OMC_SQUASH` / `OMC_REBASE_MAIN` verdicts are parsed by tools — emit them
  exactly as their skills specify, never wrapped in markdown.
- Skills marked "not meant for direct invocation" are internal — compose
  them via their user-facing entry points.

## Project instructions

Read `.omc/config/AGENTS.md` next and follow it — that file is the
project's own guidance (omc never edits it) and takes precedence over this
layer wherever they overlap.
"""

PROJECT_STARTER = """\
# Project agent instructions

This file is YOURS — omc seeds it once and never touches it again. Put the
project's real guidance here: build/test commands, architecture ground
rules, review expectations, tribal knowledge. Every agent reads it right
after omc's behavior layer (`.omc/internal/AGENTS.md`).
"""


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def ensure_agents_chain(ctx: ToolContext, root: str | Path) -> str:
    """Verify/create the chain. Returns "created" | "ok" | "blocked".

    - `.omc/internal/AGENTS.md`: ALWAYS regenerated (omc owns it).
    - `.omc/config/AGENTS.md`: seeded only if absent (the project owns it).
    - Root `AGENTS.md`/`CLAUDE.md`: created as symlinks when missing; a
      regular file or foreign symlink is NEVER replaced — the chain is
      reported "blocked" with migration steps, and nothing is changed.
    """
    root = Path(root)
    internal = root / _INTERNAL_REL
    project = root / _PROJECT_REL

    # Check the root files FIRST: a blocked chain must not half-mutate the repo.
    blocked = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        link = root / name
        if not link.exists() and not link.is_symlink():
            continue  # missing -> creatable
        if link.is_symlink() and link.resolve() == internal.resolve():
            continue  # already correct
        blocked.append(name)
    if blocked:
        _say(
            f"→ {', '.join(blocked)} already exist and are not omc's symlinks — "
            "omc will not replace them. To adopt the omc chain: move your content "
            f"into {_PROJECT_REL}, delete the root file(s), and re-run `omc configure`."
        )
        return "blocked"

    created = False
    internal.parent.mkdir(parents=True, exist_ok=True)
    internal.write_text(INTERNAL_AGENTS_MD)  # omc-owned: always regenerated

    if not project.exists():
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(PROJECT_STARTER)
        created = True

    for name in ("AGENTS.md", "CLAUDE.md"):
        link = root / name
        if link.is_symlink():
            continue  # verified correct above
        link.symlink_to(_INTERNAL_REL)
        created = True

    if created:
        _say(
            "→ AGENTS.md/CLAUDE.md now resolve through omc's behavior layer "
            f"({_INTERNAL_REL}); project guidance lives in {_PROJECT_REL} — commit all three"
        )
        return "created"
    return "ok"
