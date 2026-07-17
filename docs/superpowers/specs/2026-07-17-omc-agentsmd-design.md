# The AGENTS.md control chain

Approved 2026-07-17 (evening). omc needs deterministic control over how agents
behave in omc-managed repos, across all three harnesses, without owning the
project's voice:

```
AGENTS.md   ──symlink──▶  .omc/internal/AGENTS.md   (omc-OWNED, regenerated)
CLAUDE.md   ──symlink──▶        │  omc's behavior layer: snapshot model,
                                │  skills, stage gates, machine contracts
                                ▼
                         .omc/config/AGENTS.md      (PROJECT-owned, never
                                                     touched after seeding)
```

Codex and OpenCode read `AGENTS.md`, Claude Code reads `CLAUDE.md` — both
resolve through the symlink to omc's layer, which ends by directing agents to
read the project's own instructions.

## `ensure_agents_chain(ctx, root)` — called by `omc configure`

Only when cwd is inside a git repo (a global `omc configure` outside any repo
skips this silently). Idempotent:

1. `.omc/internal/AGENTS.md` — ALWAYS (re)written: omc owns it (content
   versioned with the CLI; user edits don't survive, that's the point).
2. `.omc/config/AGENTS.md` — seeded with a starter ONLY if absent; never
   overwritten (the project's file).
3. Root `AGENTS.md` and `CLAUDE.md`:
   - missing → created as relative symlinks to `.omc/internal/AGENTS.md`;
   - already the correct symlink → silent ok;
   - a REGULAR file or a wrong symlink → NEVER replaced. Warn with exact
     migration steps ("move your content into `.omc/config/AGENTS.md`, delete
     the file, re-run `omc configure`").

All three artifacts are meant to be committed (symlinks + the internal file
must exist in every checkout for Codex/OpenCode to read).

## The internal file's content

Short omc behavior layer: the worktree-snapshot model (`/omc:rebase-main`
before finishing), the stage gates (`/omc:build|verify|review` via
`/omc:finish`), `/omc:explain` for code questions, machine contracts
(`OMC_*` lines) are sacred, and — last, explicitly — "read
`.omc/config/AGENTS.md` and follow it; it is the project's own guidance and
takes precedence where they overlap."

## This repo (dogfood)

The existing doctrine content moves from root `AGENTS.md` to
`.omc/config/AGENTS.md`; root `AGENTS.md`/`CLAUDE.md` become the symlinks.

## Testing

Unit (red first): chain created from nothing; correct chain → silent; regular
root file → warned, byte-identical afterwards; wrong symlink → warned,
untouched; internal file regenerated (edit is overwritten); config starter
never overwritten; configure wiring (in-repo creates, outside-repo skips).
E2E: configure in the container work repo → both root files are symlinks
resolving to the internal file.
