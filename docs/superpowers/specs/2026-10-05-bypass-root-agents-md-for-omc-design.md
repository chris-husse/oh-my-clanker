# Deliver the omc behavior layer through global harness instructions

Approved in brainstorm 2026-10-05. Supersedes the delivery mechanism of
`2026-07-17-omc-agentsmd-design.md` (the root-symlink control chain); that
record stays as history.

## Problem

omc's behavior layer reaches agents today through root `AGENTS.md` and
`CLAUDE.md` in each managed repo: machine-local, gitignored symlinks into the
installed package's `distribution/AGENTS.md`, which ends by sending the agent
to the project-owned `.omc/config/AGENTS.md`. The design only works while a
project never commits a root instruction file. A committed root file
"blocks" the chain, omc refuses to touch it, and the layer never arrives.
Every managed repo also carries omc-added `.gitignore` lines for files the
project never asked for.

## Goal

The behavior layer and the project's `.omc/config/AGENTS.md` reach every
Claude Code and Codex session opened inside an omc-managed repo, regardless
of what sits at the repo root. omc writes nothing into the repo except the
one-time seed of `.omc/config/AGENTS.md`. Root instruction files that exist
are the project's business: omc neither replaces, suppresses, nor cleans
them up.

## Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Delivery through the harnesses' **global** instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`), not plugin hooks and not launch argv | User's seed. Both files load in every session on the machine; plugin hooks depend on Claude re-caching the plugin and are unverified on Codex; argv only covers sessions omc launches |
| 2 | One **fenced section** per file, marked by a fixed UUID, replaced in place by `omc configure` and `omc update` | User's seed: search-and-replace with the current preamble |
| 3 | Section body is the installed `distribution/AGENTS.md` **inline**, byte for byte | Codex has no import syntax; an absolute-path import goes stale when uv relocates the venv; inline is one mechanism for both harnesses and deterministic |
| 4 | omc **stops** creating and repairing the chain; no suppression of root files, no removal of existing symlinks or `.gitignore` lines | User: "just stop doing what it is doing now" |
| 5 | The layer's size in non-omc repos is accepted now; trimming it is separate work | Keeps this change about delivery, not content |
| 6 | `omc update` refreshes the section through the **fresh on-disk CLI**, as `post_install` already does for fish | The body must come from the just-upgraded package |
| 7 | `omc design` (start) also ensures the section for the launching provider | Cheap, idempotent, matches how start self-heals the plugin so the first run on a fresh machine works |
| 8 | No one-time "legacy symlinks present" hint in configure | Documentation only; keeping `is_omc_link` alive for a hint is complexity without a second user |

## The section

A fixed UUID constant, chosen once and never changed, fences the block:

```
<!-- omc:begin <UUID> — managed by omc; edits inside are overwritten by `omc configure` / `omc update` -->
<contents of the installed src/omc/distribution/AGENTS.md>
<!-- omc:end <UUID> -->
```

- HTML comment markers: Claude Code strips block-level HTML comments before
  injecting a memory file, so the markers cost no context there. Codex keeps
  them; they are harmless.
- `distribution/AGENTS.md` remains the single source of the layer. Nothing
  else carries a copy of the doctrine.

## Wording of the layer

`distribution/AGENTS.md` loses both sentences that assume symlink delivery.

- Opening scope guard: "This section is installed into your global
  instructions by omc. It applies only inside a repository that contains an
  `.omc/` directory (an omc-managed repo). In any other repository, ignore
  everything in this section." The guard keys on the directory, not on
  `.omc/config/AGENTS.md`, so a configured repo whose project file was never
  seeded still gets the layer.
- Closing project pointer: "Project instructions: when the repository you
  are working in contains `.omc/config/AGENTS.md`, read it now and follow it.
  It is the project's own guidance, omc never edits it, and it takes
  precedence over this layer wherever they overlap. A root `AGENTS.md` or
  `CLAUDE.md` in that repository is the project's business; it does not
  replace this step."
- Everything between stays as is.

`PROJECT_STARTER` drops its sentence about the root symlinks. This repo's own
`.omc/config/AGENTS.md` first line ("Reached via omc's AGENTS.md control
chain") is reworded as dogfood.

## `ensure_global_section(ctx, provider_name)`

Lives in `src/omc/agentsmd.py`, which keeps its name (it is still about how
AGENTS.md content reaches agents). Returns `"created" | "updated" |
"current"`. Rules, in order:

1. Resolve the target path from the provider (next section). `~` expands
   from `ctx.env` HOME; `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are honored when
   set.
2. File absent: create the parent directory if needed, write the section
   alone with a trailing newline. `created`.
3. File present, begin and end markers each found exactly once, begin before
   end: replace the span including the markers with the fresh rendering.
   Identical bytes mean `current` and no write; otherwise write and return
   `updated`. Every byte outside the span is preserved.
4. File present, no markers: append one blank-line separator (only when the
   file does not already end in one) plus the section. `created`.
5. File present, markers malformed (one marker only, duplicates, end before
   begin): raise `OmcError` naming the file and the problem. omc never
   guesses inside someone's instruction file. Callers report the error and
   continue; the other provider still gets its section.

Writes go through a temp file in the target directory followed by a rename,
the same atomic pattern `fish_integration._install` already uses, so a
crash mid-write never leaves a half-written instructions file. No locking:
configure and update do not run concurrently in practice.

### Removal: `remove_global_section(ctx, provider_name)`

`omc uninstall` already removes the omc-owned fish hook; the section is the
only other omc artifact outside `~/.omc`, so uninstall removes it too. Rules:
file absent or no markers, nothing to do; well-formed markers, remove the
span including the markers and one adjacent blank-line separator; if only
whitespace remains the file was effectively omc's and is deleted, otherwise
it is rewritten with the remaining bytes untouched. Malformed markers are
reported and left alone, as in the writer. Returns a one-line note for the
uninstall summary, mirroring `remove_owned_hook`.

## Provider surface

One new method on the provider base, beside `plugin_update_argvs`:

```
def instructions_file(self, env) -> Path
```

- claude: `$CLAUDE_CONFIG_DIR` or `~/.claude`, file `CLAUDE.md`. Claude Code
  loads the user `CLAUDE.md` in every session, including sessions where the
  project uses `AGENTS.md`.
- codex: `$CODEX_HOME` or `~/.codex`, file `AGENTS.md`. Codex's global
  instructions file, loaded for every session (verified against codex
  0.158).

Each fact is recorded as a comment at the code site, per repo convention.

## Where it runs

| Command | Today | After |
|---|---|---|
| `omc configure` (interactive and `--defaults`/`--set`) | `_ensure_repo_chain` | for each provider in `llm.providers` plus the effective `llm.default`, deduplicated: `ensure_global_section`; inside a repo, seed `.omc/config/AGENTS.md` when absent |
| `omc update` | per-provider plugin refresh loop | refresh the section for each provider in `llm.providers` plus the effective `llm.default`, deduplicated, through the fresh on-disk CLI (new `omc internal` entry point), so the body comes from the upgraded package |
| `omc design` / start | `ensure_agents_chain`, warn-but-proceed | `ensure_global_section` for the launching provider plus the project seed when inside a repo, same best-effort stance, silent when `current` |
| `omc implement` | no global-section ensure | after the committed-design gate, `ensure_global_section` for the launching provider before session launch, best effort as in start |
| `omc watch` | `_chain_tick` | removed; global config is not repo state, and watch only observes repos |
| `omc uninstall` | removes the owned fish hook | also `remove_global_section` for every supported provider, independently of global config validity or selection |

Narration: one stderr line per provider on `created` or `updated`
(`→ omc section written to ~/.claude/CLAUDE.md`), nothing on `current`.

### Final-review refinement (2026-10-05)

The initial table followed `llm.providers` for configure, update, and
uninstall and listed only start as a session self-heal path. Review found that
an accepted `llm.default=codex` configuration can omit Codex from that map;
implement can also hand a committed Claude design to Codex. Both entry paths
would start without Codex's global layer after root-chain removal. The
effective default therefore counts as selected for section delivery, and
implement ensures its launching provider after the record gate. This changes
only delivery selection, not config schema or plugin selection.

An owned section may remain after a provider is deselected or used through an
override. Uninstall now scans the provider registry and removes only marked
sections, without loading global config. This also permits cleanup when that
config is malformed or missing. These changes add no marker format, persisted
setting, root-file cleanup, or concurrency mechanism.

## What goes away

- `agentsmd.py`: `ensure_agents_chain`, `chain_healthy`, `is_omc_link`,
  `_ensure_gitignore`, the v1 migration branch, `_ROOT_NAMES`,
  `_GITIGNORE_ENTRIES`, `_V1_INTERNAL_REL`. Kept: `distribution_agents_md`,
  `PROJECT_STARTER` (reworded), and project seeding as
  `seed_project_agents_md(root)`.
- `watch.py`: `_chain_tick` and its quiet tokens.
- `configure.py`: `_ensure_repo_chain`.
- `start.py`: the chain call.

Existing root symlinks and the two `.gitignore` lines in already-managed
repos stay untouched. A machine that still has the symlinks sees the layer
twice (global plus root) until the user deletes them. The README migration
note says so in two sentences.

## Testing

Red first, no skips, per the project's testing policy.

Unit, `tests/unit/test_agentsmd.py` rewritten:

- creates the file when absent, with markers and the distribution content
- appends to an existing file without markers, preserving existing bytes and
  adding exactly one separator
- replaces the fenced span in place, preserving bytes before and after
- returns `current` and leaves mtime untouched when content is already
  current
- raises on malformed markers (begin only, end only, duplicated, reversed),
  file byte-identical afterwards
- honors `CLAUDE_CONFIG_DIR` and `CODEX_HOME` from `ctx.env`
- the distribution file resolves and carries the scope guard and project
  pointer (existing test, extended)
- the project starter is seeded only when absent

Wiring: `test_configure.py`, `test_start.py`, `test_start_mutex.py`,
`test_watch.py` lose their chain assertions and gain: configure writes a
section per configured provider; start writes one for the launching
provider; watch has no chain tick. `test_providers.py` covers
`instructions_file` per provider. The installer test covers update calling
the fresh CLI for the refresh.

Also unit: `remove_global_section` removes the span and separator, deletes a
whitespace-only remainder, leaves a foreign file alone; uninstall calls it per
provider.

Docker E2E: `tests/e2e/test_e2e_chain.py` is replaced by a global-section
scenario. Configure in a container whose `~/.claude/CLAUDE.md` already holds
user text: markers present afterwards, user text intact, a second run is
byte-identical. Then configure in a repo with a committed root `CLAUDE.md`:
the command succeeds and nothing in the repo changes except the
`.omc/config/AGENTS.md` seed. Finally uninstall: the section is gone and the
user text remains.

Live harness check (the project's "stub is not tested" rule: the external
integration here is the harness reading the file): one short headless
session per provider in the existing token-gated E2E tier asks the model to
quote the heading of the omc section from its instructions and asserts the
substring `omc behavior layer` in the reply. Claude runs in the default E2E
set; Codex follows the suite's existing opt-in convention.

## Documentation

- README: the configure paragraph's chain story becomes the global-section
  story plus the two-sentence migration note.
- `skills/integrate/SKILL.md`: the inventory item becomes "does each
  configured harness's global instructions file carry the current omc
  section"; the fix is `omc configure`.
- `distribution/AGENTS.md`, `PROJECT_STARTER`, this repo's
  `.omc/config/AGENTS.md`: wording above.
- Generated GitNexus docs regenerate on their own.

## Risks

- **Prose-gated scoping.** "Ignore this outside omc repos" depends on the
  model following it. Both harnesses treat instruction files as context, not
  configuration, so this is the same strength as every other line in the
  layer.
- **Shared line budget.** Claude Code recommends keeping instruction files
  under about 200 lines. omc's 84 lines plus the user's own content fits, but
  the budget is now shared.
- **Duplicate layer on legacy machines** until the old symlinks are deleted.
  Harmless; noted in the README.

## Deliberate complexity

- `grug:80-20` on the section body: the full layer (84 lines) is written
  inline into every global instructions file instead of a short pointer to
  the installed file. Waived by Decisions 3 and 5: Codex has no import
  syntax, an absolute path goes stale when uv relocates the venv, and
  trimming the layer is separate work.
- `grug:factor-late` on the new `omc internal` entry point that `omc update`
  calls through the fresh on-disk CLI, a mechanism with one caller. Waived
  by Decision 6: the section body must come from the just-upgraded package,
  and the old process's package path can be gone after a Python upgrade.
