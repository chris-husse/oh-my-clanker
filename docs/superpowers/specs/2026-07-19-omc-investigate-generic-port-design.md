# /omc:investigate — generic investigation harness (port of hummingbird's /hb-investigate)

**Date:** 2026-07-19 · **Slug:** omc-investigate-generic-port · **Status:** approved in session

## 1. Goal and shape

One generic, project-agnostic live-system investigation harness ships with omc as
`/omc:investigate <environment> <prompt>`. Everything omc knows is the **HOW**:
env-locked discipline, orchestrator/worker split, evidence-quoted findings,
confidence-driven progression, read-only rules, and fluency with common
observability tooling (Splunk/SPL, Grafana, VictoriaMetrics/PromQL, SQL
databases, MCP servers in general). Everything about the **WHERE** — namespaces,
scopes, connection paths, caveats — comes from a required project skill,
`.omc/skills/investigation-context`, keyed by an environment name omc treats as
opaque.

This introduces a third delegation pattern alongside the existing two:

| Pattern | Example | Missing project skill means |
|---|---|---|
| Stage proxy | build / verify / review | pass — nothing to do |
| Optional context hook | explain → explain-context | skip — degrade gracefully |
| **Required context hook** | **investigate → investigation-context** | **refuse — instruct the user to create it** |

Rejection is deliberate: an environment investigation without a WHERE briefing
cannot safely target anything, and guessing scopes against live systems is
exactly what the env-lock discipline exists to prevent.

## 2. Approaches considered

- **A — prose proxy with a canonical `envs/` layout (CHOSEN).** The generic
  skill reads `.omc/skills/investigation-context/SKILL.md` and follows it; that
  skill maps the environment token to `envs/<env>.md` and returns the briefing.
  Matches the existing build/verify/review and explain-context precedent; no
  Python; projects are free to implement the mapping however they like as long
  as the contract ("given an env, return the briefing") holds.
- **B — machine contract.** The context skill emits a structured
  `OMC_INVESTIGATE_ENV {json}` descriptor the harness parses. Rejected: the
  consumer is the LLM session itself, not a tool — prose briefings are strictly
  more expressive (caveats, execution-model overrides), and machine lines are
  reserved for tool-parsed verdicts.
- **C — per-env project skills (status quo in hummingbird).** Rejected: that is
  the duplication this port removes.

## 3. omc side — `skills/investigate/` (new, user-facing)

**Files:** `SKILL.md` + `worker-mission.md` (companion file, same as the source
skill in hummingbird).

**Argument contract:** first whitespace-delimited token of `$ARGUMENTS` =
environment (opaque string); the remainder = the investigation request. Missing
env or empty request → print usage (`/omc:investigate <environment> <prompt>`)
and stop.

**Step 0 — resolve the context skill (the gate).** Project root via
`git rev-parse --show-toplevel` (cwd fallback), checking the primary worktree
root too when different — the same convention `/omc:explain` uses for
`explain-context`. Look for
`<root>/.omc/skills/investigation-context/SKILL.md`:

- **Missing → REFUSE.** Fixed message: investigation needs the project's
  `investigation-context` skill; explain the canonical layout (`SKILL.md`
  router + `envs/<env>.md` per environment, each answering the briefing
  checklist in §5) and point at `/omc:integrate` to design it interactively.
  No queries, no guessing.
- **Present** → read it and follow it with the environment token. If the
  context skill cannot map the token to a defined environment → stop and
  surface the environments it does define; never fuzzy-match onto a live
  system.

**Step 1 — env-lock echo (always, before any query).** One line naming the
environment and the WHERE the briefing returned — log source + base scope, DB
access, MCP namespace(s), metrics source — so a wrong-env invocation dies in
the first line of output. Generic form of hummingbird's echo, populated
entirely from the briefing.

**Steps 2+ — the ported generic spine**, near-verbatim from `hb-investigate`
with project specifics abstracted out:

- **Intake** — extract a lead (ID, token, or scoped property + time-window);
  if material info is missing, one `AskUserQuestion` at a time; never fabricate
  hypotheses.
- **Pre-flight context (orchestrator only)** — preferably one `/omc:explain`
  pass over the lead (black-box call; it composes the graph, generated docs,
  and the project's explain-context), falling back to the generated GitNexus
  docs under `.omc/docs/gitnexus/docs/` when explain is unavailable, design
  records where the project's explain-context points; workers never do this.
  Mid-loop "how does this code work" questions also go through
  `/omc:explain`.
- **One-sentence plan** before the first dispatch.
- **Investigation loop** — the same digraph: decide next mission → local
  reasoning or worker dispatch → evidence-quoted finding → confident-next-step
  check → answered/pause.
- **Worker dispatch** from `worker-mission.md`; parallel only when missions
  share no inputs; sequential when in doubt.
- **Confidence rules, red flags, common mistakes** — ported as-is (rabbit-hole
  guard, cross-env mixing, three-findings-no-progress, 5+-source-files
  over-fetch, "just one more thing").
- **Read-only discipline** — generic baseline: never mutate; briefings may
  tighten further (e.g. prod's confirm-before-act), and the harness honors
  whatever the briefing adds.
- **Common-tooling fluency** — short statements of what omc already knows how
  to drive once told where: SPL via `mcp__splunk__*` or equivalents,
  PromQL/VictoriaMetrics, Grafana dashboards, SQL over read-only MCP DB tools,
  generic MCP namespaces. Explicitly: the briefing supplies scope/index/
  namespace; the harness supplies query competence.
- **Reporting** — chat-first narrative; on request or long runs, a markdown
  summary to `/tmp/omc-investigations/<lead>-<timestamp>.md` (default;
  briefing may override the location per project).

**Execution-model override:** a briefing may replace the worker-pool model for
an environment (hummingbird's `local` runs as a focused leaf reading on-disk
artifacts). The generic skill states that briefings may do this and defers to
them — that is how the entire local test-run mode stays project-side without a
hole in the design.

## 4. omc side — `worker-mission.md` (generic template)

Same structure as the source: the orchestrator fills `<env>`,
`<allowed tools>`, `<base scope(s)>`, `<mission>`. Allowed tools and scopes
come **from the briefing** instead of hard-coded cops/Splunk lines. Forbidden
list unchanged and generic: other environments' namespaces/tools, any
mutation, own hypotheses, deciding next steps. Output shape unchanged:
finding, verbatim evidence per claim, confidence + reason, incidental
observations, contradictions reported plainly.

**Model tier:** workers are subagents, so the behavior layer's model-tier
policy applies (the hummingbird source predates it): investigation workers
dispatch on the **standard coding tier**; the orchestrator stays on the
session model. Never the cheap/fast tier.

## 5. Project contract — `.omc/skills/investigation-context`

What omc documents (in the investigate skill and in `/omc:integrate`) as the
contract:

- **`SKILL.md` (router):** given an environment name, resolve it (aliases
  allowed) to the env definition and return the briefing — canonically by
  reading `envs/<env>.md` and handing its content back, plus anything global.
  Unknown env → say so and list the defined ones.
- **`envs/<env>.md` (briefing) answers, per environment:**
  - MCP namespace(s) + which tools are read-only-safe
  - log source + base scope to prepend to every query
  - DB access path
  - metrics source
  - env-specific caveats (data sharing between envs, sparse data, stricter
    confirm rules)
  - pointers to additional tools or context skills
  - optional execution-model override
  - optional report-location override
- The layout under `envs/` is canonical-but-not-mandated: the router owns the
  mapping; omc only relies on the router's behavior.

omc's own repo intentionally has no `investigation-context` (no live
environments) — invoking `/omc:investigate` here hits the refusal path, which
doubles as the dogfood test of that path.

## 6. omc repo consistency (same PR)

- `tests/unit/test_plugin_manifests.py`: add `"investigate"` to
  `USER_FACING_SKILLS`; new `test_investigate_skill_contract` with
  load-bearing needles (`.omc/skills/investigation-context`, `$ARGUMENTS`,
  `worker-mission`, `read-only`, the refusal phrasing, the env-lock echo, the
  `/omc:integrate` pointer); extend `test_integrate_skill_contract` needles
  with `.omc/skills/investigation-context`.
- `skills/integrate/SKILL.md`: Phase 1 inventory row + a Phase 2 design
  section for `investigation-context` (investigate what envs exist, what
  observability stack the project has, propose the router + env files;
  absence is correct for projects with no live environments).
- `README.md`: a short paragraph alongside the other skill descriptions.
- `.claude-plugin/marketplace.json` description: cosmetic; leave unless
  trivially touched.

## 7. Hummingbird migration (separate repo, done now in the same session)

Written through `~/Projects/hummingbird-wt/.omc`, which is a symlink to
`~/Projects/chicken-data/hummingbird-omc` (its own git repo; the commit there
is separate from the omc PR).

- Create `skills/investigation-context/SKILL.md` (router: local/dev/uat/prod,
  no aliases needed) and `envs/{local,dev,uat,prod}.md`.
- Each env file absorbs its shim's content **plus** the project-specific parts
  of `hb-investigate/SKILL.md` that belong to it: the env-lock table row (cops
  namespace, Splunk index scope, tsh MariaDB), dev/uat sandbox-org caveats
  into both files, and the **entire local test-run mode** (scenario-folder
  requirement + refusal, files-only log discovery, kept-alive-stub queries,
  parallel-interference hypothesis, focused-leaf execution override) into
  `envs/local.md`.
- Update the two in-repo references to the old entry points:
  `config/AGENTS.md` and `hb-isolated-tests/SKILL.md` (which hands scenario
  paths to `hb-investigate-local`) → point them at `/omc:investigate local …`.
- **Delete** `skills/lib/hb-investigate{,-local,-dev,-uat,-prod}` outright —
  the referrers are updated in the same change, and stale duplicates of
  live-system scopes are worse than a broken muscle-memory invocation.

## 8. Testing

- Contract tests above (pytest, run via the project's build stage
  `just build`).
- No E2E: the skill is prose; its live behavior is exercised by the
  hummingbird dogfood after migration.
- Manual sanity in-session: `/omc:investigate` in the omc repo → must produce
  the refusal message.

## Resolved decisions

1. Report path defaults to `/tmp/omc-investigations/<lead>-<timestamp>.md`;
   the project briefing may override it.
2. The five old hummingbird shim skills are deleted outright (no deprecation
   stubs).
3. The project skill is named `investigation-context` (not
   `investigate-context`).
