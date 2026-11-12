# Auto-advance the lifecycle: `LLM › Provider Selection Per Step` (off by default)

Date: 2026-11-12 · Branch: `feature/configure-auto-advance-skip-provider-prompt` · Slug: `configure-auto-advance-skip-provider-prompt`

## 1. Problem

The lifecycle stops twice to offer another provider. `skills/design/SKILL.md`
Step 5 ends with three continuations (`/omc:implement` here, `/omc:write-plan`
here, or `omc implement --claude|--codex` from the shell) and waits;
`skills/implement/SKILL.md` Phase 3 ends with three more (`/omc:audit` here,
`omc review --claude|--codex`, or `/omc:finish`) and waits. Both stops exist
so that a second provider can take over (records 2026-10-02 and 2026-10-05).
A user with one provider gets nothing from them: the record is committed, the
build is committed, and twice the session sits idle until the next authority
word is typed.

The original ask, verbatim:

> Build a `omc configure` switch that globally turns on/off the asking for
> using a different provider and just auto advances to the next stage

The seed, verbatim:

> (3) until the end. push the branch. (5) it's in the menu. I think a new
> entry "LLM > Provider Selection Per Step" which is off by default. I.e.
> auto-advance is the default.

## 2. Goals and non-goals

**Goals.**

- One global setting, `LLM › Provider Selection Per Step`
  (`llm.provider_selection_per_step`), default **off**.
- Off: `/omc:design` carries the session through record → plan → build →
  audit → `finish` → push in one run, with no typed command after
  `/omc:design`. The same holds when the run starts later: a directly typed
  `/omc:write-plan` or `/omc:implement`, or a session seeded by
  `omc implement --claude|--codex`, continues from where it is to the push.
- On: exactly today's behaviour — every stop, every continuation statement,
  every authority word.
- The skills learn the setting through a contract they already consume.

**Non-goals.**

- `omc implement` / `omc review` CLIs: unchanged. They remain the manual
  provider switches and know nothing about the setting.
- No automatic per-stage provider routing (for example "always audit on the
  other provider"). The setting removes stops; it never picks a provider.
- No change to `audit` (it already runs `finish`) or to `finish`.
- No migration of existing `config.yaml` files; a file without the field
  hydrates `False` (D7).

**Anti-goals.**

- The pre-`/omc:design` stop going away: the brainstorm still ends in
  discussion, and `/omc:design` stays the one word that starts the run (D4).
- A misread or unreadable setting producing an unwanted push. Anything short
  of a readable `false` stops as today (D5).

## 3. Decisions taken during brainstorm

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | Real chaining: with the setting off, `/omc:design` runs through implement, audit, `finish` and the push with no further typed command. Both provider-offer stops (design → implement, implement → audit) go. | Only skip the first stop; stop before `finish` for a last look (the user: "until the end. push the branch"). |
| D2 | The setting is a global `omc configure` menu entry, `LLM › Provider Selection Per Step`, default off, so auto-advance is the default. | A per-project `.omc/config.yaml` key; a CLI flag on `omc design`; an environment variable. |
| D3 | The skills read the setting as a new field on the existing `OMC_MODELS` verdict (`"provider_selection_per_step": false`). | A new `OMC_LIFECYCLE` verdict and `omc internal` verb (a seventh machine contract for one boolean). |
| D4 | The stop before `/omc:design` remains. `/omc:design` is the one word that runs everything through the push; agreement or `ok` still starts nothing. | Auto-advance from the converged brainstorm into `/omc:design`. |
| D5 | Fail toward a stop. Only a verdict with `"ok": true` and `"provider_selection_per_step": false` auto-advances; `"ok": false`, a missing field (an older CLI under newer skills) or any other value means today's stop-and-offer. | Treat the default as "off" when unreadable (fails toward an unwanted push). |
| D6 | When auto-advancing, print one notice line naming the next command and where to turn the setting on; no statement of continuations. | Silent continuation; the full continuation statement followed by continuing anyway. |
| D7 | Existing users flip to auto-advance on upgrade: a `config.yaml` without the field hydrates `False`; no migration; the README names the setting and the default. | A one-time migration writing `true` into existing files (a new mechanism to keep the old behaviour that nobody asked to keep). |
| D8 | A directly typed `/omc:write-plan` with the setting off continues into `/omc:implement` after its plan commit. Caller mode (`/omc:write-plan implement`) is unchanged. | write-plan always stops (a typed write-plan would be the only way to insert a stop, which is what the setting is for). |
| D9 | The setting governs the stops wherever the session started: a fresh session from `omc implement --claude|--codex` with the setting off also continues through audit to the push. | Auto-advance only inside the design session. |
| D10 | `audit` is unchanged: it always runs `finish`, so there is no third stop to remove. `finish`'s follow-up offers and the CRITICAL stops of every skill remain. | Also chain finish's follow-ups (closing the worktree is a decision, not a stop). |
| D11 | E2E: `tests/e2e/harness.py::configure_omc` sets `llm.provider_selection_per_step=true` for every container, so the golden ladder (`recorded`, `implemented`, `audited`) and every variation keep today's stops; one new expensive variation sets it `false` and proves the chain. | Flip every stop-asserting test individually (the same setting repeated in a dozen setups); no chain E2E (the default behaviour would be unproven). |

D11 is the Design worker's call, stated in the primer and unchallenged.

## 4. Design

### 4.1 The lifecycle after the change

```
omc design <context> → brainstorm → /omc:design (record committed)
                                        │
          provider_selection_per_step   │
        ┌──── on (today) ───────────────┼──────── off (default) ─────┐
        ▼                               │                            ▼
  three continuations stated;           │     notice line, then /omc:implement
  wait for /omc:implement,              │     in the same turn: plan → build →
  /omc:write-plan or omc implement      │     milestone gate → handoff commit
        │                               │                            │
  /omc:implement … handoff commit;      │     notice line, then /omc:audit in
  three continuations stated;           │     the same turn: conformance →
  wait for /omc:audit, omc review       │     fixes → trace → finish → push
  or /omc:finish                        │                            │
        │                               │                            ▼
  /omc:audit → finish → push            │     finish's follow-ups offered
```

Stops that remain in both modes: the discussion stop before `/omc:design`
(D4); the record gate (`omc internal design-record`) in write-plan, implement
and audit; CRITICAL questions returned by any worker; the stage-gate rule's
bounded fix-forward and CRITICAL stop; rebase conflicts (`rebase-main`'s
bail); audit's batched CRITICAL list; `finish`'s final follow-up offers
(D10). A chained run that hits one of these stops exactly as a typed run
would, and a required answer resumes it under the same authorization.

### 4.2 The setting

- **Schema** (`src/omc/config/schema.py`): `LLMConfig.provider_selection_per_step: bool = False`,
  label `Provider Selection Per Step`, help "Stop after each stage to offer
  another provider; off = continue automatically through to the push." It
  is personal (`GlobalConfig`, `~/.omc/config.yaml`), like every other
  `llm.*` leaf; the legacy combined `Config` shape picks it up through the
  shared `LLMConfig`.
- **Menu** (`src/omc/configure.py::_compose_menu`): the `LLM` section is
  built by iterating the scalar `LLMConfig` fields (everything except `docs`
  and `providers`), so the new leaf appears under `LLM` beside
  `Default provider` with no menu code. A bool `SavedTag` renders as the
  same toggle `GitNexus › Embeddings` uses, and `commit()` already lowercases
  a bool into `--set llm.provider_selection_per_step=false` before it reaches
  `store.set_key`.
- **CLI and typing** (`src/omc/config/store.py`): `set_key` needs an explicit
  `true`/`false` branch for this leaf — the generic `LLMConfig` fallback
  would `setattr` the string `"false"`, which is truthy, and the menu's
  commit path goes through `set_key`. `_hydrate` likewise rejects a non-bool
  value (`invalid llm.provider_selection_per_step in <path>: expected
  true/false`), the way it does for `gitnexus.embeddings` and
  `providers.<name>.notifications`. Whether those three bool leaves share
  one branch is the plan's call; the record requires only that both entry
  points accept exactly `true`/`false` and refuse anything else.
- **Upgrade** (D7): no migration. A file without the key hydrates the
  default, `False`; a user who wants the old stops turns the entry on once.

### 4.3 Read path: one more field on `OMC_MODELS`

`src/omc/internal.py::_models` adds
`"provider_selection_per_step": cfg.llm.provider_selection_per_step` to the
`"ok": true` payload, beside `provider` and `tasks`. The `"ok": false`
payload is unchanged (`{"ok": false, "message": …}`).

Why this verdict (D3): `design` (Step 1), `write-plan` (Step 3), `implement`
(Phase 2) and `audit` (Phase 1) already run `omc internal models` and parse
this line; the verdict is loaded from the same `GlobalConfig` the setting
lives in; and `OMC_MODELS` is already "the session's view of its
configuration" (provider, task choices). A seventh contract for one boolean
would add a verb, a parser rule in every skill, a machine-contract listing
in the layer, the README and `test_machine_contract_listings_include_knowledge_and_models`,
for no reader the existing line does not already have.

Reading rule (D5): a skill auto-advances only on `"ok": true` with
`"provider_selection_per_step": false` present. `"ok": false` is already a
blocker in every conductor; a verdict that is `ok` but lacks the field (an
older `omc` CLI under newer skills; `omc update` refreshes both, but a stale
plugin cache or a half-done update can skew them) stops exactly as today.

### 4.4 Skills

Each conductor keeps its gate, phases and completion contract. Its
authority sentence is scoped rather than left alone: design's and
write-plan's intros say the typed word authorizes its own work "and, by
itself, nothing else", then add the chain clause (with the setting off the
committed record carries forward into `/omc:implement`; with it on the
session stays open); implement's intro names the chain (`/omc:design` or a
typed write-plan with the setting off) as a second invoker beside the typed
word and the `omc implement` seed; audit's opening sentence names its third
caller (below). What changes is the **close**: where a skill today states
its continuations and waits, it first reads the flag and, when off, prints
one notice line and invokes the next conductor as a black box in the same
turn — the way implement already invokes `/omc:write-plan implement` and
audit invokes `finish`. Each close (design Step 5, write-plan direct use,
implement Phase 3) also re-reads a lost verdict: "If that verdict is no
longer in context, read it again the same way before closing." This
applies D5 (a skill that cannot see the flag does not guess `off`) and is
pinned by contract tests in `tests/unit/test_plugin_manifests.py`. The
notice (D6), verbatim up to the command name:

> Provider selection per step is off — continuing to `/omc:implement`
> (turn on: `omc configure` → LLM → Provider Selection Per Step).

#### `skills/design/SKILL.md`

- **Step 0.** The last task becomes "close: continue into `/omc:implement`
  when provider selection per step is off, otherwise state the three
  continuations and wait" — one task, resolved by the verdict Step 1 reads.
  The list still ends at the close; the chained phases are externalized by
  the conductor that owns them (implement's Phase -1 writes its own four
  phases), so no list grows to twelve items.
- **Step 1.** Already reads `OMC_MODELS`; the flag comes from that read.
- **Step 5.** After the commit and the short hardening summary: flag off →
  the notice line, then invoke `/omc:implement` (its record gate passes on
  the record just committed). Flag on, or unreadable (D5) → the current
  closing statement, unchanged, then wait. A grug-unavailable stop is still
  a hard stop in both modes: no commit, no continuation.
- **Completion contract.** Complete when the record is committed and either
  the three continuations have been stated (on) or `/omc:implement` has been
  invoked (off). "Waiting for the later implementation handoff is a valid
  stop" stays, qualified by the flag. Agreement or `ok` is still not the
  handoff.

#### `skills/write-plan/SKILL.md`

- **Step 5, direct use** (D8): flag off → the notice line, then invoke
  `/omc:implement`; implement's Phase 1 calls write-plan back in caller
  mode, which finds the plan just committed (Step 2, tracked → return the
  path) and never re-plans. Flag on → today's statement, then wait.
- **Caller mode**: unchanged — the caller continues in the same turn.
- **Reading the flag.** Today Step 3 reads `OMC_MODELS` only when a Plan
  worker is needed, so the tracked-plan and untracked-plan paths never see a
  verdict. The read moves to Step 1, right after the record gate — one
  unconditional read per run, as `design` does — and Step 3 uses that
  verdict for its Plan worker. The contract's order (record gate → models →
  `writing-plans` → plan-file-only commit) is unchanged.
- **Completion contract.** Complete when the plan is committed and, in
  direct use, either the two continuations have been stated (on) or
  `/omc:implement` has been invoked (off).

#### `skills/implement/SKILL.md`

- **Phase -1.** The fourth task becomes "handoff: continue into `/omc:audit`
  when provider selection per step is off, otherwise state the three
  continuations and wait".
- **Phase 2.** Already reads `OMC_MODELS` before the first dispatch; the
  flag comes from that read.
- **Phase 3.** The commit, the drift restore and the clean-tree check are
  unchanged and happen in both modes. Then: flag off → the notice line
  (`… continuing to /omc:audit …`), then invoke `/omc:audit`; its Phase 0
  gate ("nothing to audit" when nothing outside `docs/superpowers/` changed)
  cannot fire after Phase 3's own at-least-one-task-commit check. Flag on,
  or unreadable → today's three-continuation statement, then wait.
- A red milestone gate still blocks Phase 3 entirely, so a chained run never
  reaches audit on a red gate: the stage-gate rule's CRITICAL stop is the
  end of the turn in both modes.
- **Completion contract.** Complete when the branch is committed, the tree
  is clean, and either the three continuations have been stated (on) or
  `/omc:audit` has been invoked (off). This holds in a fresh session seeded
  by `omc implement` too (D9).

#### `skills/audit/SKILL.md`, `skills/finish/SKILL.md`

Unchanged in phases and contract (D10). Audit's "always `finish`" is the
chain's last link in both modes; `finish`'s follow-up offers (close the
worktree, address comments, discuss) are decisions, not provider stops, and
stay. Audit's opening sentence names its third caller (`/omc:implement`
continuing with the setting off) beside the typed word and the `omc review`
seed; nothing else in it moves.

#### Frontmatter descriptions

The harness shows each skill's `description` as its one-line truth, and
three of them become false by default: `design` ("commit it, and stop with
the session open … the first of the two authority words"), `write-plan`
("then stop with the session open") and `implement` ("to an unpublished
implementation"). Each gains the qualifier "with provider selection per step
on; off (the default) continues into the next stage", and `design` drops
the stale "two authority words" (there have been three since record
2026-10-05). `test_skills_have_frontmatter` checks presence only, so this
is text, not a contract change.

### 4.5 Behavior layer: `src/omc/distribution/AGENTS.md`

In the "Lifecycle scope is binding" bullet, after the `omc review` sentence
and before "Agreement or `ok` is none of these invocations":

> With `llm.provider_selection_per_step` off (the default; `omc configure` →
> LLM → Provider Selection Per Step), a committed design record carries the
> implement and audit authority forward: after `/omc:design`, or a directly
> typed `/omc:write-plan` or `/omc:implement`, the session continues through
> `/omc:implement` and `/omc:audit` to publication exactly as if the user had
> typed each word, and the skills say so in one line as they continue. With
> it on, each word must be typed.

"Three direct user invocations carry authority, in order" stays true: the
words and their order do not change; what changes is who supplies the second
and third — the user (on) or the committed record plus the setting (off).
The `omc design <context>` data clause is untouched: a seed that says
"/omc:implement" is still data, because the chain starts only at a typed
`/omc:design` (D4).

In the "Externalize a composed flow" bullet, the per-flow endings gain the
branch: "design and write-plan lists end at the stated continuations, or at
the invoked `/omc:implement` when provider selection per step is off;
implementation lists end at the committed branch and the stated
continuations, or at the invoked `/omc:audit` when it is off".

Three further edits keep the layer from contradicting its own default
chain (otherwise the layer would forbid what the skills now do):

- (a) The design and write-plan grants read "by itself nothing else; with
  provider selection per step on, the session then stays open" (and, for
  write-plan, "a later `/omc:implement` builds from that plan"), instead of
  an unconditional stay-open.
- (b) The stage-gate rule's "No later phase runs on a red gate" sentence
  reads "implement makes no handoff commit, continuation offer or
  `/omc:audit` invocation", so a red gate also stops the chained audit.
- (c) The "verdict is an argument" bullet reads "Waiting for a required user
  answer or (with provider selection per step on) the later implementation
  handoff is a valid stop"; with it off the handoff is not a stop.

The layer reaches other projects through `omc update`, as always; this
repository's root `CLAUDE.md`/`AGENTS.md` are symlinks into the installed
package and follow it.

### 4.6 Unchanged

- `src/omc/implement.py`, `src/omc/review.py`, `src/omc/cli/__init__.py`:
  seeds stay `/omc:implement` and `/omc:audit`; no flag knowledge enters a
  launcher (goal: CLIs unchanged). `IMPLEMENT_ALLOWED_TOOLS` already covers
  what `finish` needs: `omc review --headless` publishes through it today.
- `skills/plan`, `skills/start`, `skills/audit` (phases and contract;
  opening sentence per §4.4), `skills/finish`, `skills/review`, `skills/grug`, `skills/create-mr`, `skills/ticket-sync`.
- Model selection: no new task type; the verdict's `tasks` block is
  untouched.
- The `omc internal` usage line and `_USAGE`: no new verb.

### 4.7 README

`README.md` is generated and never hand-edited; the recipe
`.claude/skills/regenerate-readme/SKILL.md` gains, in `## Configure`, the
new leaf beside `GitNexus → Embeddings` (`LLM → Provider Selection Per Step`,
`llm.provider_selection_per_step`, off by default, also
`--set llm.provider_selection_per_step=true`; off means design runs through
implement, audit and the push in one session; on restores the per-stage
provider stops; existing installs get the new default on upgrade, D7) and,
in the lifecycle section, the flowchart edge and one sentence in the Design
and Implement bullets ("with Provider Selection Per Step off, continues into
implement/audit without a typed command; on, states the continuations and
waits"). Also landed, so no README sentence claims a stop in every mode:
the write-plan flowchart node drops ", stop" and the write-plan paragraph
is conditioned on the setting; the flowchart gains two edges (Record →
Build and Branch → Audit, each labelled `Provider Selection Per Step off`);
the `omc review` subsection says that with the setting off implement
invokes `/omc:audit` itself; the `omc implement` subsection says
"`omc implement` (also with `--headless`) continues through audit and the
push" (D9); the `OMC_MODELS` contract description mentions the flag.
`/regenerate-readme` runs after the skills land. A guard,
`tests/unit/test_cli.py::test_readme_documents_provider_selection_per_step`,
pins the recipe bullet, the README's configure and lifecycle sections and
these scoped statements.

## 5. Tests (red first, per `.omc/config/AGENTS.md`)

Unit, config (`tests/unit/test_config_store.py`, beside the
`gitnexus.embeddings` block): default `False` on `LLMConfig`, `GlobalConfig`
and `Config`; a `config.yaml` without the key hydrates `False` (D7); round
trip of `true`; a non-bool value in the file is a `ConfigError` naming
`llm.provider_selection_per_step`; `set_key` accepts exactly `true`/`false`
and refuses `yes`, `1`, `""` with "expects true or false"; the legacy
`config.json` shape carries the field through `load_legacy`.

Unit, menu (`tests/unit/test_configure_menu.py`): `session.root["LLM"]`
gains `"Provider Selection Per Step"` (the `set(llm)` assertion in
`test_menu_groups_schema_fields_and_registry_providers` grows); a toggle
test like `test_gitnexus_embeddings_toggle_saves_only_the_global_file`:
`tag.val is False`, `update(True)` writes only the global file and copies
back in memory, `update(False)` restores it.

Unit, verdict (`tests/unit/test_taskmodels.py::test_internal_models_verdict_and_refusals`):
the `ok` payload carries `"provider_selection_per_step": False` by default
and `True` after `set_key`; the `ok: false` payload does not carry it.

Unit, skill contracts (`tests/unit/test_plugin_manifests.py`):

- `test_design_skill_contract`: needles `provider_selection_per_step`,
  the notice line prefix "Provider selection per step is off", `Provider
  Selection Per Step`, and the invoked `/omc:implement` in Step 5; the
  unchanged three-continuation statement must still be present (on branch).
  `test_design_is_user_facing_and_owns_the_record` keeps "three
  continuations".
- `test_write_plan_skill_contract`: the direct-use close names both branches
  (`/omc:implement` invoked when off; the two-continuation statement when
  on); `omc internal models` now sits in Step 1 (the existing order check
  record gate → models → `writing-plans` → plan file only still holds);
  caller mode unchanged (`return the plan path`).
- `test_implement_skill_contract`: needles for the notice (`continuing to
  /omc:audit`), the on-branch statement (`/omc:audit`, `omc review`,
  `/omc:finish` all still present), and that the flag read is Phase 2's
  existing `omc internal models` (order check unchanged: record gate →
  write-plan → models → build → milestone gate → handoff).
- Each close that can lose its verdict (design Step 5, write-plan direct
  use, implement Phase 3) pins the re-read sentence "If that verdict is no
  longer in context, read it again the same way before closing." (D5).
- `test_behavior_layer_names_three_authority_words`: also asserts the new
  sentence (`llm.provider_selection_per_step`, "exactly as if the user had
  typed each word", "With it on, each word must be typed") and the
  externalize bullet's off-branch endings.
- A negative assertion in each conductor: the word `audit` is never invoked
  from `design` or `write-plan` directly (the chain goes through implement;
  a shortcut would skip the build).

Unit, README (`tests/unit/test_cli.py::test_readme_documents_provider_selection_per_step`,
§4.7).

E2E (`tests/e2e/`, D11):

- `harness.py::configure_omc` adds `--set llm.provider_selection_per_step=true`
  to its first `omc configure` call. Every golden stage, variation and
  monolithic run keeps its current stops and assertions: `recorded` still
  ends at a committed, unpublished record; `implemented`
  (`omc implement --claude --headless`) still ends with the remote ref
  absent; `test_start_context_waits_for_direct_implement` still waits.
  `test_golden_claude.py::test_stage_recorded`'s judge rubric ("names the
  three ways to continue") stays as written because the stage runs with the
  setting on.
- New expensive variation forked from `agreed` (the stage before
  `/omc:design`), beside `variations/test_from_agreed.py`: run
  `omc configure --set llm.provider_selection_per_step=false` in the
  container, assert `omc internal models` now reports
  `"provider_selection_per_step": false`, send `/omc:design`, wait one long turn (budget from
  `test_e2e_lifecycle_full.py`: design 900 + implement 1800 + audit 1800 s,
  as one turn), then assert artifacts only: the branch ref is present on the
  bare origin (`remote_refs`), exactly one commit over `origin/main` (finish
  squashed), the record exists in it and contains an "Implementation review"
  section, a plan exists under `docs/superpowers/plans/`, product source
  changed to the fixture's expected greeting, the tree is clean, and the
  stage marker file shows `finish`'s stage order (`_assert_finish_stage_order`
  and `_assert_published_fix` are reusable; `_assert_audited_artifacts` is
  not, because it diffs an `implemented` snapshot that already holds the
  record). It also runs `_assert_implement_stage_order(markers)` and
  requires `markers.splitlines().count("verify") >= 2` (the implement
  milestone and `finish` each run verify, so the chain skipped neither).
  No judge: the push is the proof. One variation proves both
  removed stops in one run; no sibling.
- `tests/unit/test_lifecycle_command.py`: `FULL` is an exact set, so the
  `expensive`-marked variation is added to it (or the variation is marked so
  `just lifecycle-full` does not collect it); the default `golden` and
  `e2e-tests` pins are unchanged. As landed, the justfile's `lifecycle-full`
  recipe includes `tests/e2e/variations/test_from_agreed.py`, `FULL` pins the
  chain variation, and
  `test_default_e2e_never_collects_the_auto_advance_chain` asserts the
  default tier does not collect it.

## 6. Risks and side effects

- **One session carries design, plan, build and audit.** Workers are
  subagents and each conductor externalizes its own phases, so the main
  session holds conductor state only; compaction absorbs the rest. A chained
  run is what `test_e2e_lifecycle_full.py` already does with typed words.
- **The audit is no longer cross-provider by default.** Record 2026-10-05
  introduced the separate audit so that a second provider gates publication.
  With the setting off, the implementing session audits itself. Turning the
  setting on, or running `omc review --claude|--codex` after a typed stop,
  restores the second pair of eyes; the README says so beside the setting.
- **Behaviour change on upgrade** (D7). Users who relied on the two stops
  get a push at the end of their next `/omc:design`. The notice line names
  the setting at the moment it matters; the README names the default.
- **A chain can push on a red-looking day.** Every gate that stops a typed
  run stops a chained one (§4.1); `finish`'s stage gates run on the squashed
  commit in both modes. What the chain removes is only the human glance
  between phases.
- **Version skew between CLI and skills** (D5): new skills over an old CLI
  see no field and stop as today; old skills over a new CLI ignore the field.
  Neither direction pushes unexpectedly.
- **Headless `omc implement` runs longer** with the setting off: it now
  continues through audit and the push. E2E pins the setting on, so the
  golden `implemented` stage's budget is unchanged; a user's headless run
  inherits whatever they configured.
- **E2E cost.** One new expensive variation (one `/omc:design` turn running
  three phases, roughly 10–15 minutes of actor time by the monolithic run's
  budget). It is not in the default tier, so `just check` / `just e2e-tests`
  budgets are unchanged.

## 7. Out of scope

Per-stage provider routing; a per-project override of the setting; chaining
`finish`'s follow-ups; a stop between audit and `finish`; changes to the
`omc implement` / `omc review` launchers; regenerating the GitNexus docs on
this branch.

## Implementation review

Auditor: Claude (claude-opus-5-5 orchestrator, Opus review worker),
2026-11-12.

Verdict: the code conforms to the record; there are no "code deviates"
findings. The record was stale in five places and is amended to describe
the implementation.

- F1 (Important, record is stale): §4.5 listed two behavior-layer edits
  and §4.4 said each conductor "keeps its own authority sentence".
  `src/omc/distribution/AGENTS.md:15-21`, `:72-73` and `:101-103` also
  scope the design/write-plan grants, the red-gate sentence and the
  verdict-bullet stop; the design, write-plan and implement intros changed
  (`skills/design/SKILL.md`, `skills/write-plan/SKILL.md`,
  `skills/implement/SKILL.md`, intro paragraphs). Record amended in §4.4
  and §4.5 — no D-row contradicted.
- F2 (Minor, record is stale): §4.4/§5 omitted the "If that verdict is no
  longer in context, read it again the same way before closing." re-read at
  the design, write-plan and implement closes
  (`skills/design/SKILL.md:155`, `skills/write-plan/SKILL.md:130`,
  `skills/implement/SKILL.md:143`; pinned at
  `tests/unit/test_plugin_manifests.py:1331,1430`). Record amended in §4.4
  and §5 — no D-row contradicted (it applies D5).
- F3 (Minor, record is stale): §5's chain variation omitted its models
  precheck, `_assert_implement_stage_order`, the `verify >= 2` count and the
  `lifecycle-full` / `FULL` pins
  (`tests/e2e/variations/test_from_agreed.py:185,222-226`, `justfile:52`,
  `tests/unit/test_lifecycle_command.py:16,72`). Record amended in §5 — no
  D-row contradicted.
- F4 (Minor, record is stale): §4.7 omitted the scoped write-plan stop, the
  two flowchart edges, the Review and `omc implement` sentences and the
  `OMC_MODELS` description (`README.md:156-163,288`; guard
  `tests/unit/test_cli.py:318-369`). Record amended in §4.7 — no D-row
  contradicted.
- F5 (Minor, record is stale): §4.6 listed `skills/audit` as unchanged while
  §4.4 changes its opening sentence (`skills/audit/SKILL.md:8-10`). Record
  amended in §4.6 — no D-row contradicted.

Not run on the auditing host (no Claude token): the E2E golden ladder and the
new chain variation `test_design_auto_advances_to_push`. The unit gate is
baseline-identical to origin/main (22 host-environment failures) and the
token-free E2E smoke passed. A token-bearing `just lifecycle-full` run is
still owed before relying on the chain E2E evidence.

## Deliberate complexity

- A directly typed `/omc:write-plan` continues into `/omc:implement` when
  the setting is off, so "plan here and stop" needs the setting on (lens:
  `grug:80-20` on §3/§4.4). Waived by D8: the user resolved that the setting
  governs every provider stop, write-plan's included; the stop is one menu
  toggle away.
- A lifecycle flag rides on the verdict named `OMC_MODELS` (lens:
  `grug:api-common-case` on §4.3). Waived by D3: every conductor already
  reads that line from the same `GlobalConfig`; a seventh contract for one
  boolean costs a verb, a parser rule per skill and three listings for no
  new reader.
