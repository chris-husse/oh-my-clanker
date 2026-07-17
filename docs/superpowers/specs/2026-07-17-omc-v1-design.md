# Oh My Clanker! (`omc`) v1 — design

The open-source successor to an internal tool ("the chicken"). omc turns "I have a
ticket" into "I'm in a prepared worktree with an LLM session that already knows the
ticket" — for **Claude Code, Codex, and OpenCode** from day one. This spec is the
validated v1 design; the code, once it exists, is the source of truth.

## 1. Product shape — one repo, two artifacts

- **The `omc` CLI**: a uv-installed Python package
  (`uv tool install git+https://github.com/chris-husse/oh-my-clanker`). Owns
  everything that must happen at launch time: prerequisite probes, slug
  resolution, worktree creation, terminal title, session naming, provider launch.
- **The skills plugin**: the same repo is installable as a plugin in all three
  harnesses, delivering the in-session skills as `/omc:slug` and `/omc:start`:
  - Claude Code: `.claude-plugin/plugin.json` (name `omc`,
    `dependencies: ["superpowers"]`) + `.claude-plugin/marketplace.json` so the
    repo is its own marketplace. Install:
    `/plugin marketplace add chris-husse/oh-my-clanker` →
    `/plugin install omc@oh-my-clanker`. Scriptable:
    `claude plugin install omc@oh-my-clanker --scope user`.
  - Codex: `.codex-plugin/plugin.json` (only the manifest lives in
    `.codex-plugin/`; `skills/` stays at repo root per the Codex plugin spec).
    Install: `codex plugin marketplace add chris-husse/oh-my-clanker` → install.
  - OpenCode: no marketplace exists; a JS plugin entry (`.opencode/`, modeled on
    superpowers') registers the skills. Install: add
    `"plugin": ["omc@git+https://github.com/chris-husse/oh-my-clanker.git"]` to
    `opencode.json`.

There is **no skill-sync machinery**: each harness's plugin manager pulls skills
from the repo. The CLI never copies skill files into provider config dirs.

**Superpowers dependency**: native on Claude Code (`dependencies`, auto-installs).
Codex and OpenCode have no plugin-dependency mechanism → documented prerequisite
plus a runtime presence check in the start skill with an install pointer.

## 2. CLI surface

Entry point `omc = "omc.cli:main"`. Exit codes: 0 success, 1 expected error
(`OmcError`, no traceback), 2 usage error / refusal.

| Command | Does |
|---|---|
| `omc configure` | Interactive picker (questionary): default provider (claude/codex/opencode) + optional per-provider model. Writes `~/.omc/config.json`. `--defaults` writes defaults, `--set KEY=VALUE` (repeatable, dotted keys) is non-interactive. Ends by printing the three per-harness plugin install one-liners. Needs a TTY in interactive mode (rc 2 otherwise). |
| `omc start <context> [--dry-run] [--headless]` | The centerpiece — §3. |
| `omc version` | Version (importlib.metadata) + install source from uv's receipt (`<UV_TOOL_DIR>/omc/uv-receipt.toml`): git URL (credentials redacted) or local dir. |
| `omc install [path]` | Validates `path` (default `.`) is an omc checkout (`.git` + `src/omc/__init__.py`), then `uv tool install --reinstall <path>` — re-roots future `omc update`s at that checkout. |
| `omc update` | `uv tool upgrade omc`; uv honors the recorded source (git → pull latest, dir → rebuild). |
| `omc uninstall` | `uv tool uninstall omc` + delete `~/.omc` (refuse if `OMC_HOME` points at `/` or `$HOME`). Plugin uninstall is printed as manual per-harness steps, not performed. |

**Gating**: `start` requires config — without it, bail: `run \`omc configure\`
first` (rc 2). `configure`, `version`, `install`, `update`, `uninstall` are
gate-exempt (install and repair must work from a broken state).

Dropped from the chicken: `omk llm`, `omk doctor`, `omk internal`, the `.omk`
stage system, and all skills except the two below.

## 3. `omc start <context>`

`<context>` is one positional: a ticket key (`PROJ-123`), a ticket URL, or a
free-text task description (quoted). There is no Python-side ticket-format
parsing — classification is the slug skill's job.

1. **Gate**: config exists, else rc 2 with configure guidance.
2. **Probe**: parallel `--version` runs on `git`, `wt`, and the configured
   provider CLI (~one round-trip total). Any miss → list every missing tool with
   its install hint, rc 1. Probes are real subprocess runs, never file-exists
   checks. No auto-install of anything.
3. **Slug**: one headless provider call whose prompt is the packaged
   `skills/slug/SKILL.md` content with `<context>` substituted (§4). Parse the
   final `OMC_SLUG` line:
   - `ok: true` → use `slug`.
   - `ok: false` → print `message` verbatim to stderr, rc 2.
   - No parseable verdict line → rc 1 with the raw output (a broken provider is
     an error, not a refusal).
4. **Worktree**: `git fetch origin <base>` (best-effort; the session skill's
   freshness gate is the backstop), then
   `wt switch --create <branch> --base origin/<base> --no-cd --yes --format=json`
   with `<branch> = {branch_prefix}{slug}`. If the branch already exists, retry
   without `--create` — re-running `omc start` for the same ticket idempotently
   re-enters the same worktree. Parse `.path` from the JSON.
5. **Handoff**: detect shell (fish/zsh/bash, `sh` fallback) and terminal (iTerm2,
   generic OSC fallback); set the tab title to `<slug>`; export the provider's
   title-suppression env plus `OMC_SLUG=<slug>` (the marker the session skill
   uses to detect the prepared path); `os.execvp` an interactive shell in the
   worktree that starts the provider session **named `<slug>` where supported**
   (Claude: `-n <slug>`; Codex/OpenCode: no naming — title only) and **seeded
   with `/omc:start <context>`**.
6. `--dry-run`: print the full plan (branch, wt argv, title sequence, session
   argv, shell argv) after the slug step, then stop — no worktree, no exec.
   `--headless`: skip the interactive shell; run the seeded session in the
   provider's print mode under the worktree and echo the transcript (the E2E
   hook).

## 4. The slug skill (`skills/slug/` → `/omc:slug <context>`)

Staged resolution, all intelligence in the skill (no Python-side MCP detection):

1. Classify `<context>`: ticket key / ticket URL / free-text description.
2. Key or URL → resolve it with whatever configured tool the session has (Jira
   MCP, GitHub/GitLab MCP or CLI, etc.). Description → use it directly.
3. Derive a slug: lowercase, hyphenated, `[a-z0-9-]`, ≤50 chars, ≤6 words,
   ticket key baked in when one exists (`proj-123-fix-login-timeout`). The CLI
   re-sanitizes whatever comes back (authoritative, since it names the branch) —
   the skill carries the format contract, the CLI enforces it. No bundled
   scripts: in the headless call the model may have no Bash access, and the CLI
   is already Python.
4. **Always** end with exactly one line, machine-readable:

```
OMC_SLUG {"ok": true, "slug": "proj-123-fix-login-timeout"}
OMC_SLUG {"ok": false, "reason": "<reason>", "message": "<actionable text>"}
```

Reason codes (the diagnostic contract the E2E matrix asserts):

| reason | meaning | message must include |
|---|---|---|
| `mcp-missing` | ref needs a tracker but no matching MCP/tool is configured | which server to add and how |
| `mcp-unauthenticated` | tracker tool exists but auth fails | the exact re-auth step (e.g. `/mcp` → authenticate) |
| `ticket-not-found` | tracker reachable, ticket isn't | "create it first" |
| `context-insufficient` | free text too thin to name work | what to add |

The CLI invokes this skill by **inlining the packaged SKILL.md** into the
headless prompt — single source of truth, and it works even when the plugin
isn't installed in the harness yet. The same file, delivered via the plugin, is
user-invocable as `/omc:slug`. The headless call must be able to invoke the
user's MCP read tools non-interactively (per-provider mechanism — Claude
`--allowed-tools` patterns vs permission mode — is a verify-at-implementation
item, §10).

## 5. The session skill (`skills/start/` → `/omc:start <context>`)

Seeded by the CLI at launch; dual-path:

- **Prepared path** (`OMC_SLUG` is set in the environment and the current
  branch is `{branch_prefix}{OMC_SLUG}` — the normal case after `omc start`):
  1. Gather ticket context via available tools: the ticket itself, its
     parent/epic/links where the tracker exposes them, linked docs — each doc
     summarized (title + few sentences + link), per-doc failures reported as
     "couldn't fetch — reason", never fatal.
  2. Context gate: enough to brainstorm from (clear problem + goal)? If not,
     tell the user what's missing and loop until improved or they exit.
  3. Base-freshness hard gate: `git fetch origin <base>`; verify
     `git merge-base --is-ancestor origin/<base> HEAD`, rebase if stale, STOP on
     conflicts/dirty tree. Never brainstorm on a stale base.
  4. Invoke `superpowers:brainstorming` primed with the user's seed, the
     gathered context, and a doc-naming directive: specs/plans named with
     `<slug>`.
- **Cold path** (invoked manually, no prepared worktree): guard — explain that
  setup starts from the shell (`omc start <context>`; a skill cannot set the
  session name or reliably own the tab title) and stop.
- Runtime check on every invocation: superpowers installed? If not, point at its
  install command for this harness and stop before step 4.

## 6. Config — `~/.omc/config.json` (schema v1)

```json
{
  "schema_version": 1,
  "llm": {
    "default": "claude",
    "providers": { "claude": { "model": "" } }
  },
  "worktree": { "branch_prefix": "feature/", "base_branch": "main" }
}
```

Model blank = provider default. No jira section (the skill discovers tools), no
MCP catalog, no difficulty knob. `OMC_HOME` env overrides `~/.omc` (the test
seam). Unknown keys are rejected on load with the file path in the error.

## 7. Providers

Three adapters (claude, codex, opencode — cursor dropped) behind one interface:
`models()`, `headless_argv(prompt, model)`, `session_argv(session_name, model,
seed)`, `title_env()`. The chicken's doctor surface (auth status, MCP presence,
guides) is deleted — auth/MCP problems surface through the slug skill's
structured diagnostics instead.

Verified launch facts carried from the chicken (re-verify versions at
implementation, §10):

| | claude | codex | opencode |
|---|---|---|---|
| headless | `claude -p <prompt> --output-format text` (`--allowed-tools` variadic — keep LAST, omit when empty) | `codex exec [-m model] <prompt>` | `opencode run [-m provider/model] <prompt>` |
| interactive seed | trailing positional | trailing positional | `--prompt <seed>` (positional is a DIRECTORY) |
| session name | `-n <name>` (resumable via `--resume <name>`) | none | none |
| title suppression | `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` | none exists | `OPENCODE_DISABLE_TERMINAL_TITLE=1` |
| config isolation (tests) | `CLAUDE_CONFIG_DIR` | `CODEX_HOME` | `XDG_DATA_HOME` (auth) |

`ToolContext` (trimmed from the chicken) remains the **only external-tool
boundary**: argv-list-only `run()` (never a shell string), explicit env merge,
`OMC_HOME`/binary overrides from env. Nothing invokes `git`/`wt`/`uv`/providers
directly — this is what keeps the fast tier hermetic. Shell adapters
(fish/zsh/bash/sh) and terminal adapters (iTerm2, generic OSC) carry over
trimmed; interactive `execvp` seams are E2E-verified, `pragma: no cover` in unit.

## 8. Testing

Policy carried verbatim from the chicken: **a selected test RUNS or FAILS loud —
never skips.** No `pytest.skip`/`skipif` anywhere; a missing prerequisite is
`pytest.fail` with the exact command that satisfies it. Tier selection is
allowed; skipping within a tier is not. Python tests only. At least one E2E per
external integration drives the REAL tool and asserts the on-disk effect —
argv-stub tests never count as coverage of an effectful path.

### Fast tier — `just build` (no LLM, no network, no Docker)

ruff format-check + lint + `pytest -m "not e2e"`: adapter argv contracts, config
round-trip + `--set` parsing, `OMC_SLUG` verdict parsing + slug sanitization,
probe logic against stub binaries on an isolated PATH, wt JSON parsing,
shell/terminal sequence assertions, walkthrough via pipe input.

### E2E tier — `just e2e-tests [args]` (Docker, real LLMs, opt-in)

Host-side pytest + **testcontainers-python**; every test gets a **fresh
container** (that container is the whole sandbox — the chicken's macOS
Keychain model, env relocation, and fake-origin machinery are all deleted).

**Image** (one `docker/Dockerfile.e2e`, built once per run, layer-cached):
`debian-slim` + python3 + uv + node + git + `wt` (worktrunk release binary) +
the three provider CLIs + superpowers + this repo baked in with `omc` installed
via `uv tool install /repo`, the omc plugin registered in each harness, and the
**stub Jira MCP server** available.

**Auth**: tokens enter as container env — `CLAUDE_CODE_OAUTH_TOKEN`,
`OPENAI_API_KEY`, and the opencode provider key. Live scenarios for a provider
whose token is absent **FAIL with setup guidance** (`claude setup-token`, …) —
never skip.

**Stub Jira MCP** (`docker/stub-jira-mcp/`): a small stdio JSON-RPC MCP server
(Python, stdlib) exposing `getIssue` over fixture tickets, with three modes
wired per scenario into the harness's MCP config: `ok` (serves fixtures),
`auth-error` (server present, returns auth failures), absent (not configured).
The LLM is real; the tracker is hermetic — "authenticated" is reproducible in CI
without an Atlassian OAuth dance.

**LLM judge** (carried from the chicken): live transcripts scored by a headless
no-tools judge call — run on the same provider under test, so each provider's
token suffices for its own scenarios — returning strict JSON
`{"passed": bool, "reasons": […]}`; unparseable judge output raises, never
silently passes.

**Matrix** — for each provider × {ok, auth-error, absent}:
`omc start PROJ-1 --dry-run` asserts the slug lands in the branch (ok) or the
exact reason code's message appears on stderr with rc 2 (auth-error →
`mcp-unauthenticated`, absent → `mcp-missing`). Plus per provider:
full `omc start PROJ-1 --headless` (ok mode) asserting the worktree exists on
disk with the right branch and a judge-scored transcript showing context
gathering + brainstorming kickoff; `context-insufficient` and free-text-description
scenarios; configure via `--set`/`--defaults`; `omc install /repo` re-rooting +
`omc version` source reporting. Judge-failure repair-loop policy: fix the side
(test vs src) only when the spec clearly determines which is wrong; ambiguous →
stop and surface.

### CI

GitHub Actions: fast tier on every push; E2E workflow token-gated via repo
secrets (providers whose secret is configured), manually triggerable.

## 9. Repo layout

```
oh-my-clanker/
├── .claude-plugin/{plugin.json, marketplace.json}
├── .codex-plugin/plugin.json
├── .opencode/                  # JS entry + INSTALL.md (superpowers' shape)
├── src/omc/                    # cli, errors, config/, start, probe, slugcall,
│                               # providers/, shells/, terminals/, toolctx,
│                               # worktree, install/update/uninstall, assets/
├── skills/
│   ├── slug/SKILL.md
│   └── start/SKILL.md
├── docker/{Dockerfile.e2e, stub-jira-mcp/}
├── tests/{unit/, e2e/}
├── justfile  pyproject.toml  README.md  LICENSE
```

Packaging: hatchling; repo-root `skills/` force-included into the wheel at
`omc/assets/skills/` (plain `[tool.hatch.build.targets.wheel.force-include]`,
no custom build hook — the chicken's provenance stamping is dropped);
`importlib.resources` resolution with a dev-checkout fallback to repo-root
`skills/`. Python ≥3.12; runtime dep: `questionary` only; dev deps: pytest,
testcontainers, ruff.

## 10. Verify-at-implementation risks (task #1 territory)

Ordered by how much of the design leans on them:

1. **Headless plugin/skill invocation**: `claude -p "/omc:start …"` must execute
   plugin skills; equivalents for `codex exec` / `opencode run`. The E2E harness
   and the seeded handoff both lean on this. (The slug call does NOT — it
   inlines the skill text.)
2. **Headless MCP tool access**: the slug call must be able to use the session's
   MCP read tools non-interactively per provider (Claude `--allowed-tools`
   pattern support vs permission modes; codex/opencode equivalents).
3. **Provider flag drift**: the §7 table was verified against June-2026 CLI
   versions; re-verify `-n`, `--prompt`, `codex exec`, title envs.
4. **Codex/OpenCode plugin install from a bare repo**: superpowers' layout is
   the template; confirm `codex plugin marketplace add <owner>/<repo>` accepts
   the repo shape, and the OpenCode JS entry registers skills from `skills/`.
5. **wt in Docker**: worktrunk Linux release binary (fallback: cargo install).

## 11. Explicitly stripped (vs the chicken)

Kraken-specific: krapi, kakarot.chorse.space trust gates, `seabound` cloud_id,
GitLab MR URL builder, Slack/owner references. Architecture: doctor/deps
registries + heavy-dep auto-install (GitNexus, krapi), MCP catalog + credential
presence layer, tools layer, `omk internal` contract, `.omk` stage system +
end-work/verify/commit/doctor/discover/document-project skills, skill-sync +
`env.py`, hatch provenance stamping, cursor adapter, difficulty knob, Jira-only
ticket-key parsing, mac-specific E2E sandbox (Keychain auth inheritance, env
stripping, fake origins, PTY reconfigure driver).

Deferred, not rejected: end-work/commit/verify skill family, `omc llm`,
worktree cleanup, difficulty mapping (adapters keep the seam).
