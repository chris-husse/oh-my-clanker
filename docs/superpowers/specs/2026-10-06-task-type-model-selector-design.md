# Task-type model selector

Date: 2026-10-06 · Branch: `feature/task-type-model-selector` · Status: design record

Supersedes `2026-07-18-brainstorm-plan-model-tier-policy-design.md`. That
record chose "one canonical policy block plus thin pointers" and rejected
"CLI enforcement (per-tier config keys, provider tier→model maps)" as
unnecessary machinery. This record is that machinery, by user decision: the
abstract tiers become configured, per-provider choices. The tier policy's
one surviving rule is that the cheap/fast tier (Haiku-class or its
equivalent) is never used.

## 1. Problem and goal

omc knows one model per provider, `ProviderConfig.model`, labelled "Session
model". Every launch (`omc design`, `omc implement`, `omc review`), the
headless slug call and the watch auto-build run on it. Inside a session the
skills assign subagent models by judgment: the implement skill tells the
planner to label each task with an abstract tier (`Model: top tier`, `heavy
coding tier`, `standard coding tier`) and the build phase resolves the tier
"against the provider's current lineup". Nothing in the config says which
model a tier is, the user cannot change the mapping, and the two providers
are mapped by the model's own guess.

The user's request, verbatim:

> Okay I need you to extend the configuration for each LLM provider. I need
> a model selector for task types. `Review`, `Plan` (Astra/Fable), `Design`
> (Astra/Fable), `Simple Complexity Task` (Sol/Sonnet), `Medium Complexity
> Task` (Sol/Opus), `High Complexity Task` (Astra/Fable). `Orchestrator`
> (Sol/Opus). Where my choices indicate the defaults. So `omc implement`
> should run under the orchestrator model, but then kickoff the plan with
> the specificed model and then implement tasks using the complexity
> settings. But using the orchestrator model for the overall main thread.
> That means `omc` always boots into the orchestrator model and uses agents
> with custom model setting for design, plan, implementation and review.

> Sol is the standard OpenAI model. The model ID is something you need to
> query from the UI. Ask the provider with the default model (don't set any)
> which model ID currently corresponds to `Sol` and `Astra` to get the two
> variants. OpenAI currently just has two models, top-tier and normal-tier.
> And then within Sol there could be Sol (High) and Sol (Medium) effort.
> Same for claude where you would ask for the current most up to date model
> ID for `Fable`, `Opus`, `Sonnet`.

Goal: seven task types per provider — Orchestrator, Design, Plan, Review,
Simple, Medium and High Complexity Task — each a *family* choice (Fable,
Opus, Sonnet on Claude; Astra, Sol on OpenAI) with an optional effort level,
never a model id. The CLI boots every session on the Orchestrator choice.
The skills dispatch a Design agent, a Plan agent, implementers by task
complexity and reviewers on the Review choice, all pinned to the configured
model. Concrete ids are resolved from the provider's own current model list,
so nothing in omc rots when a vendor ships a new generation.

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Config stores **family names plus optional effort**, never model ids | User: ids are queried from the provider; the user picks a family and an effort |
| 2 | **Review** defaults to the top tier (Astra/Fable) | User decision |
| 3 | Skills learn the configured choice through a new **`omc internal models`** verdict verb | Follows the `design-record` → `OMC_DESIGN_RECORD` pattern; works in sessions omc did not launch; keeps the behavior layer byte-identical across machines. Accepted via "continue" |
| 4 | Plan tasks carry a **`Complexity: simple \| medium \| high`** label, not a model | Plans stay valid when the user later changes what a label maps to. Accepted via "continue" |
| 5 | **Orchestrator is the existing `model` key**, relabelled; the slug call and the watch auto-build stay on it | Backward compatible; one main-thread model. Stated assumption, unobjected |
| 6 | Where a harness cannot pin a subagent's model, the **orchestrator model** is used, never a cheaper one | Same fallback as today's tier policy |
| 7 | OpenAI effort defaults: Orchestrator `sol:high`, Medium `sol:high`, Simple `sol:medium`; Claude efforts blank | Matches the user's Codex config (`model_reasoning_effort = "high"`) and "Sol (High) and Sol (Medium)". Recommendation adopted by `/omc:design` without objection |
| 8 | One **`family[:effort]` string** per task type, not two fields | One `--set` pair and one menu leaf per task type. Adopted without objection |
| 9 | On Claude, the Design agent is a **fresh agent fed the brainstorm's converged design text** | A Claude context-inheriting fork always runs on the parent's model, so a Design-model agent cannot inherit context. Adopted without objection |
| 10 | OpenAI family→id resolution reads **Codex's own on-disk model list**; configure refreshes it with one no-model `codex exec` turn | It is exactly what the Codex model picker shows; no model judgment involved. Adopted without objection |
| 11 | Claude resolves the id of a **just-changed family once at configure time** through the existing headless probe, for display and validation | Matches the documentation model's validate-before-save rule. Adopted without objection |

## 3. Configuration shape

One value format everywhere: `family[:effort]`. Examples: `fable`,
`sol:high`, `opus:xhigh`, or a full id typed through "Other"
(`claude-fable-5-1`, `gpt-6-sol:high`). The existing `model` key stays and
becomes the Orchestrator choice; a current `model: fable` or
`model: claude-fable-5-1` keeps its meaning unchanged. Six new string leaves
sit under a `tasks` section per provider:

```yaml
llm:
  providers:
    codex:
      model: sol:high          # Orchestrator — label "Orchestrator model"
      tasks:
        design: astra
        plan: astra
        review: astra
        simple: sol:medium
        medium: sol:high
        high: astra
```

Defaults, per provider:

| Task type | Claude | OpenAI |
|---|---|---|
| Orchestrator (`model`) | `opus` | `sol:high` |
| Design | `fable` | `astra` |
| Plan | `fable` | `astra` |
| Review | `fable` | `astra` |
| Simple Complexity Task | `sonnet` | `sol:medium` |
| Medium Complexity Task | `opus` | `sol:high` |
| High Complexity Task | `fable` | `astra` |

**Blank at rest, resolved on read.** Dataclass defaults are blank, and blank
stays blank in the file. A blank leaf means "the provider's default for this
task type", answered at read time by `Provider.default_task_model(task)`
beside the existing `docs_model_default()` — the rule the documentation
model already follows (`providers/registry.docs_llm_for`). Nothing writes a
concrete default into the file: the schema module must stay import-free and
cannot know a provider, and provider entries are created at four sites (the
menu's configured toggle, `--defaults`, the first `--set` naming a provider,
and `_probe_docs`'s `setdefault`), which the blank rule covers without
touching any of them. The user still sees the defaults: the menu's picker
lists "Provider default (fable)" as the option that stores blank, exactly as
the documentation-provider leaf offers "Follow default provider" for blank.
Files written before this change have no `tasks` section and a blank `model`;
both resolve to the defaults above. The one visible change for such a file is
Claude's Orchestrator: a blank `model` used to let the CLI pick, now it means
`opus`.

**Validation** on load and set, in the store beside `validate_worktree_value`
(the store already imports the registry): the family part is one of
`Provider.families()` or a full id; the effort part, when present, is one of
`Provider.effort_levels()`, a static list per provider — Claude `low, medium,
high, xhigh, max` (`claude --effort`, verified on 2.1.291); OpenAI `low,
medium, high, xhigh, max, ultra` (the union its model list reports today).
Whether a particular model supports a particular effort is checked at
configure time against the live list (§5), never on load: loading config
must not read provider files. The same validation refuses the cheap/fast
families — `haiku` on Claude, `luna` on OpenAI — as a bare family or as a
token of a full id, on load and on set; a pre-existing file naming one fails
to load until it is hand-edited or reset with `--defaults`. A full id may
carry Claude's bracketed long-context suffix (`claude-sonnet-4-5[1m]`).

The store's three allowlists — `set_key`'s provider-leaf tuple, `_hydrate`'s
string checks for `ProviderConfig`, and the menu's picker tuple — are
generalised to read the dataclass fields, and `set_key` learns the one
extra level `llm.providers.<name>.tasks.<task>`. The nested `tasks`
dataclass hydrates through `_hydrate`'s existing nested-dataclass branch.

## 4. Menu

Under LLM › `<provider>`: Configured, Orchestrator model, Native
notifications, Documentation model, API key (where applicable), and a
**Task models** submenu with one leaf per task type. Each leaf is a picker.
Its options are "Provider default (<effective value>)", then the provider's
families, expanded with effort levels where the provider applies per-task
effort: on OpenAI `Astra`, `Astra (high)`, `Sol (medium)`, `Sol (high)`, …
built from the model list when it is present and families only when it is
not (a list that exists but cannot be read also degrades to families only,
reported once on stderr; the configure probe rewrites it before any value is
saved); on Claude `Fable`, `Opus`, `Sonnet` only, because a Claude subagent
cannot take an effort level (§7). Every picker ends with "Other (type a model
id)". The Orchestrator leaf offers effort on both providers because the main
session accepts it (`claude --effort`, `codex -c model_reasoning_effort=`).

Each edit saves through the existing one-pair `_apply_settings` hook. The
probe step gains a sibling of `_probe_docs` for task-model leaves: it runs
only when the edited leaf changed (the same trigger rule), and only the
provider's probe from §5. A failed probe keeps the stored value, as today.

Scripted use: `omc configure --set llm.providers.codex.tasks.medium=sol:high`.
`--set llm.providers.<name>.model=` keeps creating the entry.

## 5. Resolution: how a family becomes a model argument

A new module, `src/omc/taskmodels.py`, owns resolution the way `docsllm.py`
owns the documentation probes: it does I/O only through `ToolContext`, and
the provider adapters stay pure argv builders. Adapters contribute
`families()`, `effort_levels()`, `default_task_model(task)` and, for Codex,
`model_list_path(env)` (the `CODEX_HOME` logic `instructions_file` already
has). Resolution differs per provider, each using what the provider already
exposes.

**OpenAI.** Codex writes the model list its picker shows to
`models_cache.json` under its config directory: per model a `slug`
(`gpt-6-sol`), a `display_name` (`GPT-6-Sol`), a `visibility` (`list` or
`hide`), a `priority` (lower is newer) and `supported_reasoning_levels`.
Resolving `sol` takes the lowest-priority listed model whose display name
carries that family word, case-insensitive: today `gpt-6-sol` over
`gpt-5.6-sol`. The reader relies on those four fields and nothing else, and
fails with a `ConfigError` naming the file on a shape it cannot read.

The configure-time probe for a changed Codex leaf is one `codex exec
--skip-git-repo-check` turn with no `-m` (the existing `cli_model_probe`
with a blank model): it proves the login and makes the CLI fetch and write
the list — "asking the provider on its default model", deterministically.
The leaf is then resolved against the list and the resolved slug shown on
stderr (`✓ sol:high → gpt-6-sol (high)`); a family absent from the list, or
an effort the resolved model does not support, is refused with what the list
offers. Outside configure — the launch and the verb — the list is only read;
when it is missing the error says "run omc configure", the message the
documentation backend already uses for a missing key.

**Claude.** The aliases `fable`, `opus`, `sonnet` are native to `claude
--model` and to the Agent tool's `model` parameter, so dispatch never needs
an id. The id is resolved for display and validation only, at configure
time: when a Claude leaf changes, `cli_model_probe` runs once with `--model
<family>` and the prompt "Reply with only your exact model id" in place of
its fixed "Reply with exactly OK."; the trimmed answer is shown on stderr
(`✓ fable → claude-fable-5-1`) and the probe's success is the save gate.
Nothing resolved is persisted (Decision 1).

**Full ids** typed through "Other" pass through unresolved on both
providers, as `docs_model` does; on Codex a full id needs no list.

## 6. Where each choice is applied

**CLI side.** `taskmodels.orchestrator(ctx, cfg) -> ModelChoice(model_arg,
effort)` replaces the four `pcfg.model if pcfg else ""` sites: the launch
plan (`session.session_plan`, `run_headless`), the slug call and the watch
auto-build. It reads the model list on Codex (a file read, no subprocess:
`session_plan` stays free of process I/O) and returns the alias as-is on
Claude. Claude gets `--model <family> --effort <level>`; Codex gets `-m
<slug> -c model_reasoning_effort=<level>`; a blank effort adds no flag. The
provider argv builders gain an `effort` keyword; `--dry-run` prints the
result.

**Skill side.** A new verb, `omc internal models`, prints one line:

```
OMC_MODELS {"ok":true,"provider":"codex","tasks":{"orchestrator":{"model":"gpt-6-sol","effort":"high"},"design":{"model":"gpt-6-astra","effort":""},"plan":{…},"review":{…},"simple":{…},"medium":{…},"high":{…}}}
```

`model` is the argument the harness's dispatch wants (alias on Claude, slug
on OpenAI); `effort` is the configured level or blank. The provider is the
`OMC_PROVIDER` environment variable, which the launcher sets beside
`OMC_SLUG` in `SessionPlan.env` and the headless `extra_env`, so a `--codex`
session answers for codex; a hand-started session falls back to
`llm.default`. Like `design-record`: exit 0 with the line; `"ok": false`
with a `message` and exit 2 when the global config is missing, the provider
is unknown, or the list is needed and absent. `OMC_MODELS` joins the
machine-contract listings (behavior layer, project `AGENTS.md`,
explain-context) that the manifest tests check.

## 7. Skill changes

- **design**: dispatches a Design-model agent to write the record from the
  converged brainstorm and harden it (the explain and grug passes, the
  fixes). On Claude the agent is fresh and receives the brainstorm's final
  design document, the seed and the answers as input (Decision 9); on Codex
  a model override is accepted only on a fresh or partial-history fork (a
  full-history fork rejects it, verified during implementation), so the
  worker receives the same materialized context. The agent
  cannot talk to the user: it returns the record path and any CRITICAL
  questions; the main session asks them, re-dispatches the agent with the
  answers to finish the waivers, and commits. The conversation, the
  question dialog and the commit stay in the main session.
- **implement**: Phase 1 dispatches a Plan-model agent that invokes
  writing-plans and the per-section explain pass. The directive to
  writing-plans changes to: every task carries a `Complexity: simple |
  medium | high` line (the planner's judgment: multi-file, architecturally
  tricky or ambiguous → high). Phase 2, in the main session, runs `omc
  internal models` once, maps each task's label to its model and effort,
  and pins the implementer; reviewers get the Review choice. Plans without
  labels default to `medium`.
- **audit**: the conformance pass itself is a Review worker dispatched on
  the Review choice, even when the branch looks clean; fix workers are
  labelled with the same vocabulary by the auditor and run on the matching
  complexity choice.
- **review**: the project review run, the grug judgment and the grug "fix
  first" worker are each a worker dispatched on the Review choice; the main
  session keeps disposition and the `OMC_STAGE` verdict.
- **plan**: the brainstorm pointer names the new vocabulary ("every task
  carries a `Complexity:` line; the cheap/fast tier is never used").
- **behavior layer** (`src/omc/distribution/AGENTS.md`, "Model selection"):
  the main session runs the Orchestrator model from `omc configure`;
  subagents run the task-type choice from `omc internal models`; where the
  harness cannot pin a subagent, the orchestrator model; Haiku-class never.
  The E2E tier sentence stays.

Where a harness offers no per-subagent model (an unknown harness, or a
Claude fork that must inherit context), the subagent runs on the orchestrator
model and the skill says so in one line. A harness with no subagent at all
cannot run review on the Review choice; the review stage and the audit
conformance pass then fail closed instead of judging inline in the main
session, because the user asked for review to run as an agent on its own
model (§1). Effort on Claude applies to the
main session only; the Agent tool has no effort parameter, so the verdict's
`effort` is applied where the harness takes it and ignored otherwise.

## 8. Edge cases and live-verify items

- **Codex `spawn_agent` model names.** It accepts `model` and
  `reasoning_effort` overrides, but its V2 allowlist speaks of "presets";
  whether a slug such as `gpt-6-sol` is accepted verbatim is verified against
  the real CLI during implementation and recorded as a comment at the code
  site, per the provider-quirk convention. If only presets are accepted, the
  verdict's `model` for codex carries the preset the list maps the slug to.
  Outcome (implementation, codex 0.158): `gpt-6-sol` / `gpt-6-astra` with
  `reasoning_effort` were accepted verbatim, so the verdict carries the slug;
  the override is accepted only on a fresh or partial-history fork. Recorded
  at `resolve_choice` in `src/omc/taskmodels.py`.
- **Does headless `codex exec` write the model list?** Verified live during
  implementation. If it does not, the configure probe reports "open codex
  once to fetch its model list" instead, and the E2E containers (which only
  ever run codex headless) keep working because their fixture pins full ids
  (§9). Outcome: codex 0.158 writes the list on a no-model `codex exec`; the
  "open codex once" message remains for a list still absent after the probe.
- **Family not in the list** (a new Codex generation renames a family): the
  configure probe refuses the value with the families the list does offer;
  `omc internal models` reports `"ok": false` with the same message.
- **Hand-edited effort** outside the provider's static list: refused on load
  with the levels. An effort the resolved model does not support is caught
  at configure time, and at dispatch Codex's own error surfaces.
- **Old config files** without `tasks`: identical behaviour to a fresh
  `--defaults` file (blank → provider default).
- **`--claude` / `--codex` override**: `_with_provider` swaps `llm.default`;
  `taskmodels.orchestrator` follows it; `OMC_PROVIDER` carries it into the
  session.

## 9. Testing

Red to green for every item, per the project's testing policy. Stubs run on
the restricted PATH with shell builtins; every external integration also
keeps one E2E driving the real tool.

Unit (`tests/unit`):
- `test_config_store.py`: round trip of a pre-change file (no `tasks`); the
  six leaves via `set_key` including the `tasks.<task>` level;
  `family:effort` validation (unknown family, unknown effort, full id
  pass-through); blank stays blank on save.
- `test_configure_menu.py`: the Task models submenu per provider; the
  "Provider default (…)" option storing blank; Claude pickers without
  effort, OpenAI pickers with effort built from a fixture model list and
  families-only without one; the Orchestrator leaf offering effort on both;
  a changed leaf triggers exactly one probe, an unchanged one none (the
  probe-count case lives in `test_configure.py`).
- a new `test_taskmodels.py`: resolution for family, `family:effort` and
  full id on both providers; OpenAI resolution against a fixture
  `models_cache.json` under a temporary `CODEX_HOME` (newest wins, hidden
  models skipped, unreadable shape → `ConfigError` naming the file,
  missing file → "run omc configure"); the configure probe's `codex exec`
  argv (no `-m`) through a stub that writes the fixture list; the Claude
  probe prompt and the displayed id; the `OMC_MODELS` verdict shape and its
  `"ok": false` cases; `OMC_PROVIDER` precedence over `llm.default`.
- `test_session.py`, `test_start.py`, `test_implement.py`, `test_review.py`,
  `test_watch.py` and the launch cases in `test_taskmodels.py` (the slug
  argv included): argv carries `--model`/`--effort` and
  `-m`/`-c model_reasoning_effort=`; `OMC_PROVIDER` in `SessionPlan.env`
  and the headless `extra_env`.
- `test_providers.py`: `families`, `effort_levels`, `default_task_model`
  for both providers; the `effort` keyword on the argv builders.
- `test_plugin_manifests.py`: needles for the new wording in implement,
  design, audit, review, plan and the distribution behavior layer; the old
  `Model:` tier needles removed; `OMC_MODELS` in the contract listings.

E2E (`tests/e2e`): the golden config fixture pins all seven choices to the
E2E model (full ids) in one `omc configure` run, so the Docker stages pay one
validation probe per container (configure de-duplicates identical values and
refreshes the Codex list at most once per run) and never depend on a model
list inside the container; one fast variation runs `omc
internal models` from a seeded Claude session and asserts the verdict; the
opt-in Codex suite gains one case that resolves `sol` through the real
Codex model list.

## 10. Out of scope

- The documentation model, its backend and probes are untouched.
- No complexity inference in omc; the planner labels tasks.
- No persisted resolved ids; no per-task-type documentation.
- Removing or changing `--claude`/`--codex` semantics.

## Implementation review

Auditor: Claude (Fable 5.1) · 2026-10-06 · conformance pass dispatched to a
Review worker on the configured Review choice (`fable`), cross-checked by the
main session.

| # | Severity | Kind | Record | Site | Disposition |
|---|----------|------|--------|------|-------------|
| 1 | Important | record is stale | §7 design bullet, §8 bullets 1–2 | `skills/design/SKILL.md` (Codex fork rule), `src/omc/taskmodels.py` `resolve_choice` / `validate_selection` | Record amended: a Codex model override needs a fresh or partial-history fork; §8 live-verify items closed with their outcomes |
| 2 | Important | record is stale | §7 review and audit bullets, fallback paragraph | `skills/review/SKILL.md`, `skills/audit/SKILL.md` | Record amended: review run, grug judgment and audit conformance pass are dispatched Review workers; a harness with no subagent fails closed. Contradicts no brainstorm decision (Decision 6 covers pinning, not dispatch) and follows the user's request in §1 |
| 3 | Important | code deviates from the record | §5 "relies on those fields and nothing else" | `src/omc/taskmodels.py` `_model_list` | Code fixed: an effort level needs only its `effort`; the `description` requirement dropped. Test `test_reasoning_levels_need_only_an_effort` |
| 4 | Important | code deviates from the record | §4 "families only when it is not" | `src/omc/taskmodels.py` `model_options`, `src/omc/configure.py` `_model_picker` | Code fixed: an unreadable Codex list degrades to the families-only picker with one stderr warning instead of aborting `omc configure` for every provider. Tests `test_codex_picker_falls_back_to_families_on_unreadable_cache`, `test_unreadable_codex_cache_degrades_pickers_and_warns_once` |
| 5 | Minor | record is stale | §3 validation | `src/omc/config/store.py` `_BANNED_FAMILIES` | Record amended: cheap/fast families refused on load and set |
| 6 | Minor | code deviates from the record | §3 "or a full id" | `src/omc/config/store.py` `_MODEL_TOKEN` | Code fixed: Claude's bracketed long-context suffix accepted; record amended |
| 7 | Minor | record is stale | §9 unit test map | `tests/unit/test_taskmodels.py`, `tests/unit/test_configure.py` | Record amended to the actual file placement |

`just check` after the fixes: 1392 passed.

Finish-stage follow-up, same date: the project review stage (Review worker on
`fable`) found one Important item — every `--set` of a task leaf ran its own
live probe, so the E2E fixture's seven pins cost seven serial model turns per
container and the new Codex E2E sat at the 300 s ceiling, contradicting §9
"keep their current cost". Fixed: `_probe_task_models` probes once per
distinct effective value per provider and refreshes the Codex list once per
run; Codex full ids are probed directly without a list; the harness pins all
seven leaves in one configure call after the container policy. The re-review
of that fix caught one more: the "list already refreshed" flag flipped after a
Codex full-id probe, which never refreshes the list; it now flips only on a
family leaf (regression test
`test_codex_full_id_probe_does_not_count_as_a_list_refresh`). Grug lens over
the diff: 1 Important waived (`grug:locality`, see Deliberate complexity),
1 Minor (`grug:debuggable`: `_model_list` rejects a malformed entry with one
compound condition and the message "invalid model fields" rather than naming
the field). Minor items from the project review, listed, not gating: the orchestrator `model` load error now names its key;
the "run omc configure" message for a missing Codex list is only actionable
once a Codex leaf changes; duplicate `CODEX_E2E_MODEL` readers in the E2E
helpers.

## Deliberate complexity

- §5, `grug:say-no`: the Claude configure-time id probe resolves an id that
  nothing persists or dispatches on — display and validation only. Waived by
  Decision 11: the user asked for the current id of each Claude family to be
  queried from the provider, and the probe doubles as the validate-before-save
  gate the documentation model already has.
- Diff, `grug:locality`: adding a task type touches the schema field
  (`config/schema.py`), each provider's default table (`providers/*.py`),
  `taskmodels.TASKS` and the E2E pin list. Waived at review: the seven types
  are the user's fixed vocabulary (§1), and each site is data a new type must
  declare — its label, its default per provider, its E2E pin; deriving one
  list from another would hide the default the new type needs.
