# Oh My Clanker!

> Turn "I have a ticket" into "I'm in a prepared worktree with an LLM session that already knows the ticket" — for Claude Code, Codex, and OpenCode.

`omc` is one repo, two things. A small, deterministic CLI does what a computer's good at: probing your tools, naming a branch, creating a worktree, launching and naming a session. A skills plugin — installed straight from this repo into your harness — does what only an LLM can: read the ticket, decide if there's enough to go on, kick off a brainstorm. There's no skill-sync step and no copying files into provider config directories; each harness's own plugin manager pulls skills from here directly. ("Clanker": what you call a robot after it's done all of that for you.)

## Install

1. Install the CLI:

   ```bash
   uv tool install git+https://github.com/chris-husse/oh-my-clanker
   ```

2. Configure it — pick a default provider and, optionally, a model per provider:

   ```bash
   omc configure
   ```

   Non-interactive equivalents exist for scripting: `omc configure --defaults` or `omc configure --set llm.default=claude`. Run inside a repo, configure also establishes the **AGENTS.md control chain**: root `AGENTS.md` and `CLAUDE.md` become symlinks straight into the omc install's own generated behavior layer (`distribution/AGENTS.md`, found via `omc print-install-path`), which defers to the project's own instructions in `.omc/config/AGENTS.md` (yours — seeded once, never touched; existing regular files are never replaced, you get migration steps instead). Those symlinks point at a machine-specific path, so omc gitignores them — `omc start` and `omc configure` recreate them on any machine. Commit `.omc/config/AGENTS.md` plus the `.gitignore` entries; updating omc (`omc update`) updates the behavior layer everywhere instantly, no re-commit needed.

3. Install the skills plugin for each harness you use (then, inside your project, run `/omc:integrate` in a session — it inventories every omc surface and brainstorms your project's build/verify/review/explain-context skills with you, grounded in your actual codebase; re-run it after omc updates or whenever the integration feels off):

   | Harness | Install |
   |---|---|
   | Claude Code | `/plugin marketplace add chris-husse/oh-my-clanker` then `/plugin install omc@oh-my-clanker` |
   | Codex | `codex plugin marketplace add chris-husse/oh-my-clanker`, then install `omc` from `/plugins` |
   | OpenCode | add `"plugin": ["omc@git+https://github.com/chris-husse/oh-my-clanker.git"]` to `opencode.json` |

   `omc`'s session skill hands off to [superpowers](https://github.com/obra/superpowers)'s brainstorming skill, and declares it as a marketplace-qualified plugin dependency (`superpowers@superpowers-marketplace`) — but install superpowers explicitly yourself for every harness; Claude Code resolves the dependency once you have, it doesn't fetch it for you.

   | Harness | Install superpowers |
   |---|---|
   | Claude Code | `/plugin marketplace add obra/superpowers-marketplace` then `/plugin install superpowers@superpowers-marketplace` |
   | Codex / OpenCode | Install from [obra/superpowers](https://github.com/obra/superpowers) |

   Full write-up (including the cross-marketplace dependency pitfall this manifest shape avoids): [`docker/PLUGIN-NOTES.md`](docker/PLUGIN-NOTES.md).

## Usage

`omc start` takes exactly one positional argument, in any of three shapes:

```bash
omc start PROJ-123
omc start https://yourteam.atlassian.net/browse/PROJ-123
omc start "add rate limiting to the public API"
```

A ticket key or URL is resolved through whatever tracker tool your session already has configured (Jira MCP, a GitHub/GitLab MCP or CLI, …); free text is used as-is. Either way, here's what happens end to end:

1. **Gate** — refuses to run until `omc configure` has been done once.
2. **Probe** — real `--version` calls (never file-exists checks) against `git`, `wt`, and your configured provider CLI. Anything missing fails loud with an install hint, before anything else happens.
3. **Slug** — one headless call to your provider turns the context into a short branch slug (`proj-123-fix-login-timeout`) — or a precise, actionable refusal if it can't (no tracker tool configured, tracker tool not authenticated, ticket not found, or free text too thin to name work after).
4. **Worktree** — fetches the base branch and hands the slug to `wt` to create a fresh worktree on `{branch_prefix}{slug}` (`feature/proj-123-fix-login-timeout` by default). Re-running `omc start` for the same ticket re-enters that same worktree instead of erroring.
5. **Handoff** — sets your terminal tab's title to the slug and launches your provider's interactive session *inside* the worktree, seeded with `/omc:start <context>`. On Claude Code the session is also *named* after the slug (`-n <slug>`), so you can walk away and pick it back up later with `claude --resume <slug>` — Codex and OpenCode have no session-naming flag, so for those the tab title is the only breadcrumb.

From there, `/omc:start` takes over inside the session itself: it gathers the ticket's context (parent/epic, linked docs — each summarized, or reported as "couldn't fetch" rather than failing outright), verifies the base branch is still fresh (rebasing, or stopping cleanly on conflicts — it never brainstorms on a stale base), and then hands off to `/omc:plan`, which runs one `/omc:explain` pass over the ticket ("which parts of this codebase are relevant to this?"), bundles the answer with pointers to prior design records into a project primer, asks for your own seed thinking, and starts a `superpowers:brainstorming` session that already knows the codebase. When the brainstorm converges, type `/omc:implement`: it writes the spec and hardens it section-by-section through `/omc:explain`, walks the implementation plan through the same scrutiny, builds via subagents, and ends by invoking `/omc:finish`.

When the work is done, run `/omc:finish` inside the session: it rebases onto a fresh base, squashes the branch to a single commit whose message *is* the MR/PR description (generated from the real diff), pushes with `--force-with-lease`, and prints where to open the MR — it never creates one for you. Worktrees are snapshots of main — code AND knowledge: `wt` copies every gitignored file (`.env`, caches, the `.gitnexus`/`.omc/docs` graph+docs) into new worktrees, and `/omc:rebase-main` refreshes both later (rebase onto the fresh base + a deterministic Python re-mirror of the knowledge dirs; it is also `/omc:finish`'s first step). omc seeds a starter `.config/wt.toml` when a project has none, and `/omc:check-wt-config` reviews an existing one against the faithful-worktree expectations.

If the repo defines project stages (`.omc/skills/{build,verify,review}/SKILL.md` — each a skill saying what that stage means for *this* project), finish runs them in that order between squash and push, stopping before the push if one fails; `/omc:build`, `/omc:verify`, and `/omc:review` run them standalone and are no-ops when unconfigured. It ends by offering to close the worktree (`wt remove` — the branch survives until merged), iterate on review comments (amend + re-push), or just talk through the change.

Two flags change the shape of the run: `--dry-run` prints the full plan (branch name, `wt` argv, title sequence, session argv) and stops before touching anything; `--headless` runs the seeded session in the provider's print mode instead of an interactive shell.

## Understanding a codebase

`/omc:index` builds (incrementally refreshes) a [GitNexus](https://github.com/chris-husse/GitNexus) knowledge graph of the repo; `/omc:document` generates LLM-written architecture docs from that graph into `.omc/docs/gitnexus/docs/`; `/omc:explain <question>` answers "how does X work / what breaks if I change Y" with file-and-symbol citations, grounded in the graph, the generated docs, and — if the project defines one — its own `.omc/skills/explain-context` skill (where the project says where its truth lives). GitNexus installs itself on first use into `~/.omc/dependencies/gitnexus`, cloned only from its approved source. `omc watch` automates the cadence: run it in the main checkout (foreground; `--interval`; `--once` runs a single tick AND forces an index/docs refresh even with nothing new — the "refresh now" button) and it ff-syncs the base branch as commits land, refreshing the index directly (no LLM cost) — add `--enable-documentation` to also regenerate the docs (LLM-heavy, so it's opt-in). Manually, that cadence is: run `index` + `document` in the main checkout as the base branch moves; `explain` from any worktree reads the primary checkout's graph, so it stays current. If the project commits a script at `.omc/hooks/post-watch.sh`, watch runs it (via `bash`, from the repo root) after every cycle that did real work — a sync or a forced `--once` refresh, `$OMC_WATCH_OUTCOME` says which (`synced`/`refreshed`) — and a failing or hung hook never stops the loop: watch warns and links the captured output log. `--auto-build` goes one step further: after the hook, watch runs the project's build stage (`.omc/skills/build`) via the default LLM (LLM-heavy, hence the flag; skipped instantly when no build stage exists), again linking the transcript log on failure.

## Notifications

Opt in during `omc configure` (or `omc configure --set notifications.enabled=true`)
and every omc-launched session pings you the moment it needs attention — a
question, a permission prompt, a finished turn — instead of idling unseen in
its tab. Delivery is per-harness under the hood (Claude Code hooks, codex's
`notify` program, an OpenCode plugin), all funneling into
`omc internal notify`.

Two backends (`notifications.backend`):

- `macos` (default) — native notification via `osascript`; silently does
  nothing on other platforms.
- `file:///absolute/path.log` — appends one tab-separated line per event
  (`time  slug  provider  event  message`), handy headless or over ssh:
  `tail -f` it in a spare terminal to see which sessions are ready.

Disabling (`--set notifications.enabled=false`) silences everything at once —
already-wired worktrees included.

## Prerequisites

- `git`
- [`wt`](https://github.com/worktrunk) (Worktrunk) — creates the worktree
- [`uv`](https://astral.sh/uv) — installs and updates `omc` itself
- At least one provider CLI: `claude`, `codex`, or `opencode`
- The [superpowers](https://github.com/obra/superpowers) plugin, for whichever harness(es) you use — `/omc:start` reaches it through `/omc:plan`

`omc start` probes for `git`, `wt`, and your configured provider before doing anything else, and refuses with an install hint for whatever's missing rather than guessing.

## Commands

| Command | Does |
|---|---|
| `omc configure` | Pick your LLM (and worktree branch naming); writes `~/.omc/config.json` |
| `omc start <context>` | Ticket key, ticket URL, or quoted task description → worktree → seeded session |
| `omc watch` | Keep the main checkout's base branch + knowledge graph fresh (`--once`, `--interval`, `--enable-documentation`, `--auto-build`); runs the project's `.omc/hooks/post-watch.sh` (and with `--auto-build` its build stage) after action ticks |
| `omc version` | Print version + build provenance + install source |
| `omc install [path]` | (Re)install omc from a local checkout (default `.`) |
| `omc update` | Update omc from the source it was installed from |
| `omc uninstall` | Remove omc (binary + `~/.omc`) |

## Development

Fast tier — format check, lint, unit tests; no LLM, no network, no Docker:

```bash
just build
```

E2E tier — Dockerized, real provider CLIs, a fresh container per test:

```bash
just e2e-tests                                # everything
just e2e-tests tests/e2e/test_e2e_smoke.py    # container-harness smoke test only, no tokens needed
just e2e-tests -k claude                      # just the claude column of the matrix
just expensive-e2e-tests                      # LLM-heavy docs-generation tests - real money, run deliberately
```

Live scenarios need a token per provider. Put them in a `.env` file at the repo
root — `cp env.example .env` and fill in what you have. `.env` is gitignored and
dockerignored (tokens never land in a commit or an image layer), and `just` loads
it automatically for every recipe, so no shell exports are needed:

| Provider | Env var | Where to get it |
|---|---|---|
| `claude` | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | `claude setup-token` / console.anthropic.com |
| `codex` | `OPENAI_API_KEY` | platform.openai.com |
| `opencode` | `ANTHROPIC_API_KEY` | console.anthropic.com |

This is by design, not an oversight: a selected test **runs or fails loud — it never skips**. If a provider's token is missing, that provider's live E2E tests fail with the exact command needed to fix it (e.g. `claude setup-token`) instead of silently passing.

## Security note

The Slug step runs your configured provider headlessly while it reads the ticket's title and description. On Claude Code the call is granted only conventional tracker MCP servers (`jira`, `atlassian`, `linear`, `github`, `gitlab`) — never your other MCP tools; on Codex and OpenCode no per-call tool scoping exists, so the session's own tool config applies. Either way the ticket text is — text written by whoever filed the ticket, not by you. Treat tickets from untrusted or external reporters accordingly; a crafted ticket title is untrusted input to that headless call, the same as any other prompt-injection surface. A per-MCP-server allowlist for the headless call is a tracked hardening item, not yet implemented.

## License

MIT — see [LICENSE](LICENSE).
