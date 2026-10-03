# Split design from implement, with a provider handoff

**Date:** 2026-10-02
**Slug:** `split-design-implement-provider-handoff`
**Status:** design, approved in chat; `/omc:implement` invoked with the
recommendations adopted; hardened (explain + grug per section, whole-spec
pass)

## 1. Problem

The lifecycle is bound to one provider session. `omc start` opens a session
on the configured provider and model, the brainstorm converges there, and
`/omc:implement` carries the design through spec, plan, build and finish in
that same session. There is no seam at which a different provider can take
over, although the spec phase wants the best model available and the build
phase is where another provider may be cheaper, faster, or simply preferred.

The user's seed, verbatim:

> I need a way to let the `omc start` phase be done by the set model, like
> Fable, but then switch to another provider for anything after the spec has
> been written. Currently this is wedged into /implement, which writes the
> spec and then the plan. I think we need to split this into `/design`
> (writing & hardening the spec from the brainstorming session) and
> `/implement` (writing the plan from the design and implementing it). Then
> we add a `omc implement` command with override flags `omc implement
> --claude` and `omc implement --codex` which will require the design file
> to be present (using the branch based slug, ideally deterministic) and
> then use the selected provider to continue with the plan implementation
> using the design file. This is basically a handoff.

> Yeah I think we should maybe rename `omc start` to `omc design` and also
> allow the provider overrides `--claude` and `--codex` there. Maybe this
> can just be a general override for all sorts of commands that use the
> providers under the hood. Then `omc design` would not terminate after the
> design, but it would be up to the user to enter `/implement` to kickoff
> implement with the same provider or close the provider and run `omc
> implement --codex` etc. to kickoff `/implement` on another provider.

## 2. Goals and non-goals

Goals:

- The in-session work splits at the design record. `/omc:design` writes,
  hardens and commits the record. `/omc:implement` plans from it, builds and
  finishes.
- The design record is the handoff artifact: deterministic to find from the
  branch, committed, exactly one per slug, validated by one implementation
  whichever side asks.
- A second CLI entry point, `omc implement`, launches a fresh session in the
  existing worktree, on the configured provider or an overridden one, seeded
  to continue from the record.
- `omc start` becomes `omc design`, and both session-launching commands
  accept the same provider override.
- The design session does not end after the record is committed. The user
  decides whether to continue in place or hand off.

Non-goals (deliberately out of scope):

- Resuming the design session by name instead of launching a fresh one.
- Persisting the override anywhere.
- Switching providers per plan task.
- Overrides on the documentation commands (`watch`, `dependency watch`).
  Those read the docs provider for wiki runs and the session provider for
  the slug, so an override there has two meanings; and with
  `llm.docs.backend=api` an override to a provider without an API backend
  would make the docs run raise. A separate change, if ever.
- Fixing the implement duration; it stays an open product finding
  (`tests/e2e/golden/test_golden_claude.py`, the `implemented` stage).
- Hand-editing generated docs (`.omc/docs/gitnexus/docs/`,
  `tests/e2e/artifacts/omc-wiki/`); they regenerate via `/omc:document`.

## 3. Design

### 3.1 The lifecycle after the change

| Step | Who runs it | What it does | Where it stops |
|---|---|---|---|
| `omc design <context> [--claude\|--codex]` | CLI | Probe, slug, worktree, seed the session (today's `omc start`) | Session open, seeded skill running |
| seeded skill `start` | session | Gates, ticket sync, base freshness, primer, brainstorm | Waits for the user's design agreement |
| `/omc:design` (`$omc:design` on Codex) | user types it | Writes, hardens and commits the design record | Session stays open and states the two ways to continue |
| `/omc:implement` in the same session | user types it | Validates the design record via the CLI, then plan, build, finish | Pushed branch |
| `omc implement [--claude\|--codex]` in the worktree | CLI | Derives the slug from the branch, validates the design record, launches a fresh session seeded with `/omc:implement` | Session open, implement skill running |

The seeded skill keeps the name `start`. The user never types it; the CLI
seeds it, so the CLI command and the seeded skill may carry different names
without anyone noticing. The user-typed spec writer is `design`, as the seed
asks. `omc design` and `/omc:design` sharing a name while doing different
things was raised by the lens and is waived by the brainstorm decision on
the rename (section 4).

### 3.2 Authority words (behavior layer)

The behavior layer (`src/omc/distribution/AGENTS.md`, reached through the
root `AGENTS.md`/`CLAUDE.md` symlinks) today names one authorizing word.
After this change it names two:

- `/omc:design` (`$omc:design` on Codex) authorizes writing, hardening and
  committing the design record, and nothing else.
- `/omc:implement` (`$omc:implement` on Codex) authorizes plan, subagent
  build and finish through the described push, and requires a committed
  design record.
- Agreement, including `ok`, authorizes neither.

Only the behavior layer and README carry the complete two-word rule. Each
skill names only the next handoff it waits for: `start` and `plan` say "wait
for `/omc:design`"; `design` ends by stating the two continuations;
`implement` states its own precondition, a committed record. The "Externalize
a composed flow" bullet of the behavior layer lists `/omc:design` among the
conductors and gains a third clause: design lists end at the committed record
and the stated continuations. The E2E helper that types the handoff
(`tests/e2e/lifecycle_helpers.py:_direct_implement`) gets a sibling for the
design command.

### 3.3 The provider override

Two private helpers in the CLI module (`src/omc/cli/__init__.py`), next to
the parser and the dispatcher. One adds the flags to a subparser; one
resolves them.

- The flags are boolean, generated from the provider registry
  (`providers/registry.py:provider_names()`), and mutually exclusive. Today
  that yields `--claude` and `--codex`; a third registered provider gets its
  flag for free. The registry already feeds `omc internal notify --provider`
  the same way.
- Resolution returns a copy of the effective config with `llm.default`
  replaced for this process: `dataclasses.replace` on the config and its
  `llm` field, which shares the providers map and the secrets with the
  original and is not a deep copy. Nothing is persisted; the runtime
  `Config` is never written back by construction. Every downstream call
  site keeps reading the default provider unchanged, so tool probing
  (`probe.require_tools`), slug generation (`slug.fetch_slug`), plugin
  health (`plugin.ensure_plugin`), notification wiring (`notify`) and the
  session launch follow the override without being touched.
- The overridden provider must be installed; the existing probe verifies
  that with a real version call. It need not appear in `cfg.llm.providers`:
  a missing entry means the provider's own default model, which is how the
  config already behaves (`start.py`, `model = pcfg.model if pcfg else ""`).
  Config validation runs on load and save, not on the runtime copy; the
  override cannot name an unknown provider because its flags come from the
  same registry.
- The override is attached to `design` and `implement` in this change.
- Known limit, unchanged by this change: plugin health is verified for
  Claude only; `--codex` launches without a plugin check, exactly as a Codex
  default does today.

### 3.4 The design record as the handoff artifact

The `design` skill writes `docs/superpowers/specs/YYYY-MM-DD-<slug>-design.md`
(today's `spec` behavior, unchanged). The date is the writing date and is not
derivable from the slug, so lookup globs on the `-<slug>-design.md` suffix,
exactly as the implement skill and the grug skill already do.

The branch-to-slug mapping gets one owner. `wtconfig.py`, which already owns
the repo roots and sits beside `WorktreeConfig`, gains both directions:
`branch_for(cfg, slug)` and `slug_for(cfg, branch)`. `start.py` calls the
former instead of formatting the branch itself. The latter strips the
committed `worktree.branch_prefix` (default `feature/`; the store allows an
empty prefix, which is always present) and refuses a branch that does not
start with it or whose remainder is not a sanitized slug.
`sanitize_slug` and its regex move into `wtconfig.py` as the leaf they are,
and `slug.py` imports them from there: `wtconfig` is the lowest-level git
module, imported by config resolution, plugin, configure, internal, watch and
start, so it must not pull in `slug.py` and with it both provider adapters.
The config parameter is typed under `TYPE_CHECKING` only, as `probe` does.
`find_design_record(ctx, root, slug)` lives next to them and anchors its
match on `^\d{4}-\d{2}-\d{2}-<slug>-design\.md$`, since a loose suffix glob
would call a slug that is a suffix of another slug ambiguous. All three
operate on `repo_root(ctx)`, the checkout containing the current directory,
never `primary_root`, where the record does not exist yet; outside a
repository they refuse. The prose globs in the implement and grug skills can
stay loose.

The gate is strict: exactly one match, committed in HEAD and clean.

- Zero matches refuses and says to type `/omc:design` in a design session.
- Several matches refuses and lists them. Two dated records for one slug is
  a refusal by design, and the message says so.
- A record missing from HEAD (untracked or only staged) or with pending
  changes refuses. `design` commits in its last step, so a dirty record is
  the sign the design session died before that step, and a half-hardened
  spec must not seed an implementation. The check is `git cat-file -e
  HEAD:<rel>` plus an empty `git status --porcelain -- <rel>`, run through
  `ToolContext`.

**One rule, one implementation.** The gate is exposed as a hidden internal
verb, `omc internal design-record`, printing a single-line
`OMC_DESIGN_RECORD {…}` verdict: `ok: true` with `slug` and `path`, or
`ok: false` with a one-line `message` and a `reason` in
`no-prefix | missing | ambiguous | unclean`. Untracked, staged-only and
modified collapse into `unclean` because every consumer treats them alike;
`design` branches on `missing` (write), `unclean` or `ok` (continue
hardening the returned path) and `ambiguous` (refuse), so the vocabulary is
exactly what is consumed. The verb prints its verdict itself and exits 2 on
`ok: false`, the refusal code; the `internal.py` docstring widens its exit
legend to "2 usage or refusal", and the verb joins the internal usage line.
`omc implement` calls the same Python functions and raises
`errors.Refusal` (exit 2) with the same message, which the CLI boundary
prints as `error: …`. The in-session `/omc:implement` runs the verb and
relays its verdict. The dry run, the real run and the in-session path
therefore cannot disagree about what a valid record is.

`OMC_DESIGN_RECORD` joins the machine-contract listings that the behavior
layer, the project's `.omc/config/AGENTS.md`, the explain-context skill and
the review stage enumerate, and the manifest test that pins those listings.

### 3.5 The implement seed

One line: `/omc:implement`. The launcher has already validated the record
before the session starts, and the implement skill recovers it through the
internal verb, so the seed carries no data. The plugin notes record both full
Codex lifecycle runs green with the `/omc:start` seed positional
(`docker/PLUGIN-NOTES.md`, 2026-09-24 row), so a `/omc:` seed drives the
plugin skill on Codex; only a `/omc:` command typed at the Codex prompt is
rejected, and the CLI never types. No per-provider syntax method is added,
and `build_start_seed` is untouched.

### 3.6 Session launch

The shared part is pure. A plan builder, `session_plan(ctx, cfg, *, seed,
session_name, title, cwd)`, returns what both commands and both dry-run
printers compute identically today: the interactive session argv (provider
and model lookup, the notification sink argv when enabled), the child
environment (`title_env` plus `OMC_SLUG`), the title sequence and title
argv, and the shell invocation. The exec-or-headless fork stays in each
command, and `_run_headless` keeps its name, module and five positional
parameters, gaining keyword-only `session_name` and `allowed_tools` whose
defaults are today's values, so the tests that monkeypatch it with a
five-argument lambda, and the ones that monkeypatch shell detection and
`os` on the start module, keep intercepting. Everything else stays with its
command: worktree creation, base fetch and the knowledge alert in `design`;
record validation in `implement`. `start.py:_print_plan` keeps its
test-asserted row labels; `implement`'s plan prints `branch`, `record`,
`session argv`, `shell argv` and `notify`.

`omc implement` probes the tools and checks plugin health exactly as
`design` does (`probe.require_tools`, `plugin.ensure_plugin`, with
`check_only` under `--dry-run`); that is what makes the override's
"installed" claim true and what prevents a seeded command from opening on
"Unknown command". It runs in the current worktree (`repo_root`), with the
branch as tab title and `OMC_SLUG=<slug>` in the environment even though
the session is named differently, so the implement skill's own glob and the
verb agree. If notifications are enabled it writes the chosen provider's
wiring into the worktree (`notify.wire_worktree`, idempotent for Claude's
settings file; Codex wires through argv, so a Claude-to-Codex handoff
writes nothing) and passes the sink argv into the interactive session argv
as `design` does; the headless argv has no notification parameter today.

`--dry-run` prints the plan and changes nothing. `--headless` runs the
provider's print mode; its user is the E2E tier (section 3.10), where
`omc start --headless` is already the proven launch pattern. The headless
allow-list for implement is its own explicit list: the investigation list
used by `design` (tracker MCP patterns, Bash, Read, Glob, Grep) would stall
an implement run at the first write. Codex ignores allow-lists.

**Session name.** Settled live during hardening: a second `claude -n <slug>`
in the same worktree creates a separate session without error, and
`claude --resume <slug>` then fails as ambiguous between the two. The
implement session is therefore named `<slug>-implement`, so both
breadcrumbs stay resumable by name. Codex has no session name either way.

### 3.7 The rename

`omc design` is the canonical command. `start` stays as an argparse alias
with no deprecation output. Argparse stores the typed token in
`args.command`, so the dispatcher matches both names; `--help` lists both.
The reasons are muscle memory and the E2E inventory: every E2E launches
`["omc", "start", …]` and the Claude conversation driver hard-requires that
argv prefix. Decision: E2E argv and that guard stay on `omc start` in this
change; the alias makes them legal.

The rename is the first task of the plan, green on its own before any
feature code: parser alias, dispatcher, the Python user-facing strings
(`plugin.py` status strings, `watchlock.py`'s `--no-mutex` hint and its two
literal test assertions, `configure.py`'s prompt label, `start.py`'s plan
header, `dependency.py` and `internal.py` hints), README (including the
sentence `tests/unit/test_cli.py` asserts and the plugin status it quotes),
the plugin notes, and the `start`, `slug`, `finish` and `ticket-sync` skills.
`test_start_skill_contract` pins the literal `omc start` in the start skill;
the cold-path message names `omc design` and mentions the alias, so the pin
is updated deliberately, not by accident.

### 3.8 Skill changes

- **`spec` becomes `design`, user-facing.** The skill directory is renamed.
  It keeps every hardening step, gains a `$ARGUMENTS` block (an optional
  detail the user passes with the command, as the E2E critical-question
  variation does), and gains three conductor lines. First, it writes its
  steps into the task list before invoking anything. Second, it runs
  `omc internal design-record` and branches on the verdict: `missing`
  writes the record; `unclean` or `ok` continues hardening the returned
  path, never writing a second dated file; `ambiguous` refuses. Third, it
  ends by committing, posting the hardening summary, and stating
  the two ways to continue as a statement, not a question: type the
  implement command here, or exit and run `omc implement --<provider>`. Its
  last task is literally to wait for the handoff, using the behavior layer's
  sentence that waiting for the implementation handoff is a valid stop. The
  "when run under `/omc:implement`, keep going" branch is gone. It declares
  a completion contract and joins the conductor list in the manifest tests.
  An internal `spec` returns only when a second caller appears.
- **`implement`** loses its spec phase and its glob. Its first phase runs
  `omc internal design-record` and refuses with the verdict's message,
  pointing at `/omc:design`, when it is not ok. It keeps the existing "a plan
  for this slug already exists, continue from it" check. Phases become plan,
  build, ship. The manifest test that pins the spec-first order changes with
  it.
- **`start`** and **`plan`** point their cold-path message and caller
  contract at `omc design` and "wait for `/omc:design`".
- **`grug`** names `/omc:design` as its spec-side owner in its frontmatter
  (`/omc:spec` was never a real entry point; the test pin moves with it).
- The behavior layer and README carry the two-word authority rule and the
  conductor list.

### 3.9 Python changes

- CLI parser (`cli/__init__.py`): `design` subparser with the `start`
  alias, a new `implement` subparser with `--dry-run` and `--headless`, the
  two override helpers applied to both, lazy dispatch branches that load
  config or bail with exit code 2.
- `wtconfig.py`: `sanitize_slug` moves in; `branch_for`, `slug_for`,
  `find_design_record`, and the pure `session_plan` builder (or a sibling
  module beside it if `wtconfig` would otherwise import providers).
- `start.py`: calls `branch_for` and `session_plan`; `_print_plan` and
  `_run_headless`'s shape unchanged, the latter gaining two keyword-only
  parameters with today's defaults.
- A new `implement.py` with `run_implement`: probe and plugin health,
  resolve root, slug and record, dry-run printer, notification wiring,
  `session_plan`, then exec or headless.
- `internal.py`: the `design-record` verb and its usage line.
- `tests/e2e/codex_plugin_payload.py`: `design` joins the installed-skill
  list it verifies.
- No provider changes.

### 3.10 Testing

Unit tests (`just check`, hermetic, parallel):

- the generated flags and their mutual exclusion; an unknown provider is a
  parser error;
- override resolution returns a copy and leaves the original and the saved
  config untouched;
- `branch_for` / `slug_for` round trip, the empty prefix, the prefix
  mismatch and the unsanitized remainder refusals;
- record lookup for zero, one, many, untracked, staged-only and modified, in
  throwaway repositories;
- the `design-record` verdict line for each outcome;
- `session_plan` output per provider for both commands, the implement
  session name, `OMC_SLUG` in its environment, and the implement headless
  allow-list;
- the `omc implement --dry-run` output and the unchanged start plan rows;
- the start seed is byte-identical to today;
- manifest tests: `design` joins the user-facing list and the conductor
  list and inherits the former spec contract test plus the three conductor
  needles; `spec` leaves the internal list; `implement` orders plan, build,
  ship and names `omc internal design-record` and `/omc:design`; `start` and
  `plan` name `/omc:design`; `grug` names `/omc:design`.

E2E (`tests/e2e`):

- **New golden stage `recorded`** after `agreed`: the test types the design
  command; a new boundary helper next to the repo snapshot asserts the
  artifact facts (product source and remote refs unchanged, HEAD advanced,
  a `specs/` key gained under `docs/superpowers/`); the judge rubric covers
  only the reply stating both continuations. The name is deliberately not a
  tense of the existing `design` stage.
- **Tier placement by measurement.** The `/omc:design` turn (explain and
  grug per section, commit) has never been timed on the fixture. The plan
  measures it first. Under 240 seconds `recorded` sits in the default
  golden tier; otherwise it is an expensive stage, and nothing in the
  default tier depends on it.
- **Fast variations fork `agreed`**, not `recorded`, so they never depend
  on a model-produced record. One is model-free: it writes and commits a
  fixture record itself, then runs `omc implement --claude --dry-run`
  through the harness and asserts the plan's `record` row and seed. One is
  a single fast model turn: it types `/omc:implement` with no record and
  asserts the refusal names `/omc:design` with the boundary snapshot
  unchanged.
- **Moved variations.** `test_from_agreed.py`'s `implement_publishes` and
  `failing_build_blocks_publication` fork `recorded` instead, since
  `/omc:implement` from `agreed` now refuses. `critical_question_waits`
  becomes a design-time variation from `agreed`: the conflicting detail now
  surfaces as a critical question during `/omc:design`. The monolithic
  `test_e2e_lifecycle_full.py` runs gain a `/omc:design` turn before the
  implement command.
- **`implemented`** forks `recorded` and runs `omc implement --claude
  --headless` through the harness with the implement turn budget, which
  covers the CLI handoff on Claude at no extra cost; the in-session
  `/omc:implement` path is covered by the moved variations. A harness run
  yields no driver events, so the child-dispatch event assertion goes; the
  artifact checks stay: finish stage order from its marker file, the
  published fix against the bare origin, and a stage-baseline comparison in
  which a `plans/` key is new and the `specs/` key is unchanged. The stage
  manifest records the implement session name so a later fork resumes the
  right session. If a headless `claude -p` under an explicit allow-list
  stalls on permissions (not established; the conversation driver's turns
  skip permissions), the stage falls back to typing the implement command
  from `recorded`, and the CLI handoff on Claude is covered by the dry run
  alone.
- **Codex handoff, standalone evidence.** One test builds its container
  from the Claude `recorded` image directly, applies the Codex account
  volume and a config in which `llm.default` stays `claude` and only the
  Codex model is set, so it proves the override and not a default swap,
  then runs `omc implement --codex --headless` through the harness. It does
  not generalize the stage fixture; that waits for a second cross-provider
  test. It is marked for the Codex provider, so collection adds the Codex
  gate marker and serialization group, and `expensive`, so the Codex gate
  recipe excludes it; its id contains `codex` as the recipe test requires.
- **Recipe home.** `just lifecycle-full` already means "expensive evidence,
  never a gate"; it gains the Codex account volume and the handoff test's
  path, and `tests/unit/test_lifecycle_command.py` pins that collection so
  the test cannot fall out of every recipe silently.

## 4. Decisions taken during brainstorm

Each row was presented as an open question with a recommendation; the user
invoked `/omc:implement` without changing any, so the recommendation stands
unless hardening produced evidence, noted in the row.

| Question | Decision |
|---|---|
| Seeded skill name | Keep `start`; the user never types it. |
| Flag shape | Boolean flags generated from the provider registry, mutually exclusive. |
| Which commands get the override | `design` and `implement` now; watch commands later, if ever. |
| Gate strictness | Exactly one record, committed in HEAD and clean; otherwise refuse. One implementation serves CLI and session. |
| Native command syntax | Revised during hardening: no provider method. The plugin notes record the `/omc:start` seed driving the skill on Codex, so both seeds stay `/omc:`. |
| Second Claude session name | Revised during hardening: settled live; a reused name creates a separate session and breaks `--resume`, so the implement session is `<slug>-implement`. |
| Rename | `omc design` canonical, `start` a silent argparse alias; E2E argv stays on `start`. |
| Implement without a record | Refuse and point at `/omc:design`. |

## 5. Risks

- **Codex seed trigger.** The evidence is a `/omc:start` seed on Codex
  0.156.1. A later Codex release could change how a seed positional is
  treated; the standalone handoff evidence run is where that shows up.
- **Behavior-layer drift.** Two authority words instead of one means two
  places for a session to misread its licence. Each skill naming only its
  next handoff halves the prose surface; the task-list rule and the
  manifest tests that pin one word per skill are the defence; the renamed
  `design` conductor is held to the same anti-stall contract.
- **The design stop.** `design` is the first conductor whose task list
  deliberately ends before the push. Phrased as a question it fails the E2E
  rubric that forbids routine approval requests; phrased as a statement and
  a wait it passes. The rubric is the guard.
- **Rename blast radius.** The alias keeps the command working; the sweep
  is its own first task with its own green run.
- **`recorded` turn duration.** Unknown until measured; the tier decision
  waits for the number, and the default tier never depends on the stage. If
  it lands in the expensive tier, the moved variations need `just
  golden-full` and the stage-image failure hint should say so.
- **Headless implement permissions.** Settled in part during planning: a
  one-shot `claude -p` under the implement allow-list wrote a file headless,
  and an unknown tool token is ignored rather than rejected. Whether
  subagents dispatched from that run inherit the grant is not established;
  the fallback in section 3.10 keeps the stage green either way.
- **Pre-existing, unchanged here.** Stage snapshots are `docker commit`s of
  containers that received provider tokens in their environment, so the
  images may embed those tokens. Not verified; worth one `docker image
  inspect` in a separate check.

## Deliberate complexity

- `omc design` and `/omc:design` share a name while the CLI command starts
  the session and the skill writes the record (lens: `grug:api-common-case`
  on section 3.1). Waived by the brainstorm decision "Rename: `omc design`
  canonical"; the user's seed asks for this name on both sides.
- The prose sweep to `omc design` ships in this change rather than as a
  follow-up with `design` as a mere alias (lens: `grug:small-refactor` on
  section 3.7). Waived by the same decision; the sweep is isolated as the
  plan's first task with its own green run.
