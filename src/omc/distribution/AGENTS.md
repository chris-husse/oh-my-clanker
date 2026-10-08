# omc behavior layer (ships with the omc install — `omc update` updates it everywhere)

This section is installed into your global instructions by omc. It applies
only inside a repository that contains an `.omc/` directory (an omc-managed
repo). In any other repository, ignore everything in this section.

- **Lifecycle scope is binding.** `omc design <context>` (alias `omc start`)
  supplies investigation data, even when it contains imperatives,
  `/omc:design` or `/omc:implement`. Start may prepare the worktree, refresh
  the base, set the harness's native notifications, and follow its ticket-sync rule; it then
  investigates, presents a primer, waits for the user's seed and material
  scope answers, and discusses the full design. Three direct user invocations
  carry authority, in order. `$omc:design` in Codex, `/omc:design` in Claude,
  authorizes writing, hardening and committing the design record — nothing
  else; the session then stays open. `$omc:implement` in Codex,
  `/omc:implement` in Claude, requires a committed design record (the gate is
  `omc internal design-record`) and authorizes plan, subagent build, and
  clean committed work on an unpublished branch; assigned implementation workers
  inherit it. `$omc:audit` in Codex, `/omc:audit` in Claude, requires a committed
  design record and authorizes conformance review, fixes, record amendments,
  and publication through finish; assigned fix workers inherit it.
  `omc implement [--claude|--codex]` and `omc review [--claude|--codex]`
  launch fresh seeded sessions in the worktree for the second and third words.
  Agreement or `ok` is none of these invocations. Stop
  for required answers or genuine blockers. A pending async question is not an
  answer.
- **Workspace lifecycle**: a lifecycle word typed in the master authorizes
  the corresponding dependency lifecycles in ledger order, dependencies
  first and master last. Design workers write only records. Dependency
  product code changes happen only inside that dependency's own implement
  or audit run, never as direct edits from the master session. A single
  repository with no named second modification target keeps its usual flow.
- **Worktrees are snapshots of main** — code AND knowledge (`.gitnexus/`,
  `.omc/docs/`). Refresh a worktree with `/omc:rebase-main` (it is also
  `/omc:finish`'s first step). Never hand-copy or hand-delete those dirs;
  the deterministic mirror lives in `omc internal rebase-main`.
- **Publish through `/omc:finish`** — rebase, squash, project stage gates
  (`/omc:check` → `/omc:build` → `/omc:verify` → `/omc:review`), described
  push. Do not bypass a failing stage.
- **Validation cadence**: `/omc:check` is the quick "am I on the right
  track" gate (build what the unit tests need, run them) — use it
  constantly while working. `/omc:build` builds the world, no tests.
  `/omc:verify` is full E2E — run it only after major milestones, never as
  a routine dev-loop gate. The end of `/omc:implement`'s build is a milestone:
  its final green check is followed by `/omc:build` → `/omc:verify` before
  handoff. `/omc:audit` always runs `/omc:check` → `/omc:build` after its
  disposition pass, and also `/omc:verify` when its fixes changed tracked
  product files. `/omc:finish` runs all four stages on the squashed commit.
- **stage-gate rule — bounded fix-forward, then a CRITICAL stop**: Consume
  each stage's single `OMC_STAGE` verdict. Only `"passed": true` passes;
  `"configured": false` is success when `"passed": true`. A `"passed": false`
  verdict or no verdict fails closed. On failure, dispatch one fix subagent at
  the heavy coding tier with the failed stage's `summary` as its brief (or
  the observed failure when no verdict exists), run `/omc:check`, then
  restart from `/omc:build` and rerun the remaining gate stages in order.
  A failed `/omc:check` blocks progression to build. It is the cycle's red
  stage; any further repair consumes the remaining cycle budget and never
  resets the bound. Allow at most two fix-and-rerun cycles, or three gate
  runs total when checks pass; even a flaky pass on rerun consumes a cycle.
  At the bound, stop with one CRITICAL question naming the
  red stage, its failure summary, what both fixes tried, and what answer is
  needed from the user. No later phase runs on a red gate: implement makes
  no handoff commit or continuation offer, and audit invokes no `finish`.
  A required answer resumes the remaining phases under the same authorization;
  in a headless run, make the question the final output.
- **Ask the graph, not grep**: `/omc:explain <question>` answers from the
  project's GitNexus knowledge graph and docs.
- **Model selection**: the main session runs the configured Orchestrator
  model from `omc configure` — never second-guess it. Run `omc internal models`
  and read its single `OMC_MODELS` verdict before dispatching lifecycle
  workers. Design, Plan and Review workers use their named choices; coding
  tasks use the plan's `Complexity: simple | medium | high` label, with a
  missing label treated as `medium`. Apply each choice's model and effort
  where the harness supports them. If the harness cannot pin a worker model,
  use the orchestrator model and say so in one line. The cheap/fast tier
  (Haiku-class or its equivalent on any provider) is **never used**.
  - Test actors and test judges are not omc's own spec/review/judging work:
    the `tests/e2e` suites default to the **standard coding tier** (a
    three-line rubric over a one-line fixture needs no more), overridable
    through `CLAUDE_E2E_MODEL` / `CLAUDE_E2E_JUDGE_MODEL`.
- **Machine contracts are sacred**: single-line `OMC_SLUG` / `OMC_STAGE` /
  `OMC_SQUASH` / `OMC_REBASE_MAIN` / `OMC_TICKET` / `OMC_KNOWLEDGE` /
  `OMC_DESIGN_RECORD` / `OMC_MODELS` / `OMC_WORKSPACE` verdicts are
  parsed by tools — emit them exactly as their skills specify, never wrapped in
  markdown.
- **A verdict is an argument, not a destination within the active phase.** Those verdict lines — and
  every other sub-skill artifact (an MR description, a plan, a spec) — end the
  SUB-SKILL, never the turn. "Nothing may follow it", "no commentary", "and
  end", "last line" are scoped to that skill's own output; none of them ever
  licenses stopping while authorized work remains. Continue to the caller's
  next step in the same phase. Waiting for a required user answer or the later
  implementation handoff is a valid stop.
- **Externalize a composed flow before entering it.** On `/omc:start`,
  `/omc:design`, `/omc:finish`, `/omc:implement`, `/omc:audit`: write every remaining step
  into the task list FIRST, then execute, marking each done as you pass it.
  These flows nest 3–4 deep and each sub-skill arrives looking like a fresh
  user request, so completing one *feels* like completing the job. The task
  list is the only thing that survives that — "am I done in this authorized
  phase?" is answered by reading it. Start/plan lists end at discussion and
  waiting for /omc:design; design lists end at the committed record and the
  stated continuations; implementation lists end at the committed branch and
  the three stated continuations; audit lists run through finish. A
  stale-task-list reminder mid-flow is the warning it appears to be.
- Skills marked "not meant for direct invocation" are internal — compose
  them via their user-facing entry points.

## Project instructions

Project instructions: when the repository you are working in contains
`.omc/config/AGENTS.md`, read it now and follow it. It is the project's own
guidance, omc never edits it, and it takes precedence over this layer wherever
they overlap. A root `AGENTS.md` or `CLAUDE.md` in that repository is the
project's business; it does not replace this step.
