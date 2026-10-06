# Add `omc review`: the audit handoff that publishes

**Date:** 2026-10-05
**Slug:** `add-omc-review-provider-handoff`
**Status:** design, approved in chat; hardened (explain + grug per section,
whole-spec pass)

## 1. Problem

The lifecycle hands a committed design record from one provider to another
for implementation (`omc implement --claude|--codex`, spec 2026-10-02), but
nothing hands the finished branch back. `/omc:implement` plans, builds and
runs `finish`, so the implementing provider grades its own work and the
branch is published before anyone else has looked at it. There is no seam
where a second provider checks the implementation against the record and
corrects it.

The user's seed, verbatim:

> we also need to add `omc review --codex|claude` so that at the very end,
> the other provider can take over again to check that the implementation
> vibes with the original design and if needed fixes what needs fixing.

And the reorder the brainstorm settled on, in the user's words:

> So this will effectively replace the current /omc:finish? I think that
> actually makes sense.

The answer is a reorder, not a replacement. `finish` stays the publish
machinery and stays typable. What changes is who calls it last on the omc
path: implement no longer does; the audit does.

## 2. Goals and non-goals

Goals:

- A third CLI entry point, `omc review [--claude|--codex]`, launches a fresh
  session in the existing worktree, on the configured or overridden
  provider, seeded to audit the branch against its design record.
- An in-session conductor skill, `audit`, finds drift between the record
  and the implementation, fixes it, writes what it found into the record,
  and publishes through `finish`.
- `/omc:implement` ends at a built, checked, committed and unpublished
  branch and states the continuations, the way `/omc:design` already does.
- The audit skill is also typable in the implement session, so a
  same-provider audit needs no new session.

Non-goals (deliberately out of scope):

- Detecting which provider implemented. Spec 2026-10-02 made persisting the
  override a non-goal; the flag is the user's statement.
- Making the audit mandatory. `/omc:finish` stays typable and never refuses
  for lack of an audit.
- Renaming the `review` stage proxy (`skills/review`). The CLI command and
  the seeded skill carry different names; see "Deliberate complexity".
- Judging code quality or simplicity again. The project review stage and
  the grug lens run inside `finish`, which the audit calls.
- Reviewing MR comments. `finish` Step 6 owns that loop.
- Any refactor of the launchers: no shared exec-or-headless tail (spec
  2026-10-02 §3.6 left the fork in each command) and no re-homing of the
  write-capable allow-list. A third copy is cheaper than a refactor shipped
  with a feature.
- Hand-editing generated docs (`.omc/docs/gitnexus/docs/`,
  `tests/e2e/artifacts/omc-wiki/`); they regenerate via `/omc:document`.

## 3. Design

### 3.1 The lifecycle after the change

| Step | Who runs it | What it does | Where it stops |
|---|---|---|---|
| `omc design <context> [--claude\|--codex]` | CLI | Probe, slug, worktree, seed `start` | Session open |
| `/omc:design` | user types it | Writes, hardens, commits the record | Record committed; two implement continuations stated |
| `/omc:implement`, or `omc implement [--claude\|--codex]` | user, or CLI seed | Record gate, plan, subagent build, `/omc:check` per task | Task commits on the branch, clean tree, nothing pushed; three continuations stated |
| `/omc:audit`, or `omc review [--claude\|--codex]` | user, or CLI seed | Record gate, conformance pass, fixes, trace in the record, then `finish` | `finish`'s own end: one squashed, described, pushed commit; ticket in review; follow-ups offered |
| `/omc:finish` | user types it | Unchanged | Unchanged |

What the reorder buys: nothing is published before the second provider has
looked; `finish` squashes the task commits, the audit's fixes and the trace
into one commit in one pass, so there is no amend-and-force-push of an
already-described commit; the audit has exactly one publish route; and the
expensive `implemented` E2E stage loses its slowest part.

What it gives up, and where that survives: today one typed command carries
a committed record to a pushed branch (spec 2026-10-02 §3.1, "Pushed
branch"). That walk-away property moves one command away, to `/omc:finish`
as implement's stated third continuation. The reorder trades it for a
publication that a second pair of eyes has gated.

**Landing order.** The launcher and the `audit` skill land first. Audit's
"always `finish`" is correct on today's already-published one-commit branch
too (finish re-squashes and re-pushes with the lease), so the existing
`implemented` stage stays green while they land. Implement's ending then
flips in the same commit as the moved E2E assertions (§3.10), so no
intermediate commit has a suite asserting a publish that implement no
longer performs.

### 3.2 Authority words (behavior layer)

The behavior layer (`src/omc/distribution/AGENTS.md`) names three direct
invocations, in order:

- `/omc:design` (`$omc:design` on Codex): write, harden and commit the
  record; nothing else.
- `/omc:implement` (`$omc:implement` on Codex): requires a committed record;
  plan, subagent build, per-task check, through the committed and unpublished
  branch. No longer through the push.
- `/omc:audit` (`$omc:audit` on Codex): requires a committed record;
  conformance review, conformance fixes, record amendments, and publication
  through `finish`. Assigned fix workers inherit it.
- `omc implement` and `omc review` launch fresh seeded sessions for the
  second and third words. Agreement, including `ok`, authorizes none of
  them.

Each skill names only its own precondition and the next handoff it states:
`design` ends with the implement continuations, `implement` ends with the
audit continuations, `audit` ends where `finish` ends. Only the behavior
layer and the README carry the complete three-word rule. The "Externalize a
composed flow" bullet lists `/omc:audit` among the conductors and says that
implementation lists end at the committed branch and the stated
continuations, and audit lists run through finish.

### 3.3 The `omc review` launcher

A new `src/omc/review.py` with `run_review`, shaped like `run_implement`:

- The record gate first, `wtconfig.resolve_design_record`, read-only and
  answered in milliseconds; `Refusal` with the verdict's message on
  `ok: false`, the same messages implement prints. The gate is the record
  only. "Is there anything to audit" needs a fetch, which belongs to the
  skill.
- Probe and plugin health exactly as implement (`probe.require_tools`,
  `plugin.ensure_plugin`, `check_only` under `--dry-run`), so the override's
  "installed" claim holds and the seeded command cannot open on "Unknown
  command".
- Session name `<slug>-audit`, for the reason settled in spec 2026-10-02
  §3.6: a second `claude -n <name>` forks and makes `--resume` ambiguous.
  One spelling, `audit`, names the skill, the session and the E2E stage;
  `review` is the CLI verb only. `OMC_SLUG` stays the slug. Codex has no
  session name.
- Seed: the bare one-liner `/omc:audit`. The launcher has validated the
  record; the skill recovers it through `omc internal design-record`.
- `session_plan` with the branch as title; notification wiring through
  `notify.wire_worktree` when enabled; dry-run printer headed
  `omc review — plan` with the rows `branch`, `record`, `session`,
  `session argv`, `shell argv`, `notify`; `--headless` through
  `run_headless` with implement's write-capable allow-list, imported from
  `implement.py`. Nothing moves: the constant stays where two unit tests
  import it by name.
- `review.py` imports `run_headless`, `detect_shell` and `os` into its own
  namespace, as `implement.py` does, so a mirrored `test_review.py` can
  intercept by module attribute; the CLI dispatch lazy-imports `run_review`
  at call time, as the CLI tests' monkeypatch path requires.

The CLI (`src/omc/cli/__init__.py`) gains a `review` subparser with
`--dry-run`, `--headless` and the registry-generated provider flags
(`_add_provider_flags`), and a lazy dispatch branch shaped like implement's
(`_with_provider` on the loaded config). No provider changes.

### 3.4 The `audit` skill (conductor)

Invoked directly (`/omc:audit`, `$omc:audit`) in the implement session, or
by the `omc review` seed in a fresh one. The command IS the user's approval
to review, fix conformance, amend the record and publish through `finish`;
it does not ask permission between phases.

- **Phase -1, externalize.** Write the phases into the task list first:
  gates, conformance, disposition, trace and commit, finish.
- **Phase 0, gates.** `omc internal design-record` must be `ok`; otherwise
  refuse with its message (a missing record points at `/omc:design`). Then
  `git fetch origin <base>` (base resolved as `finish` does; a fourth
  skill-side derivation is the existing convention) and refuse with
  "nothing to audit — run `/omc:implement` first" when `origin/<base>...HEAD`
  changes nothing outside `docs/superpowers/`. An empty-diff check would
  never fire, because `/omc:design` has already committed the record on the
  branch. Read the record in full; it is the truth. Read the plan at
  `docs/superpowers/plans/*-$OMC_SLUG-plan.md` when present; it is context,
  and its absence is reported, never refused.
- **Phase 1, conformance.** Walk the record and the diff together. Two
  finding kinds, named by what gets fixed: **code deviates from the record**
  (something promised is missing, something decided is done differently,
  something unasked or excluded was built) and **record is stale** (the
  implementation is right and the record is wrong). Each finding cites the
  record section and `file:line`. Severity is Important or Minor, as in
  grug. `/omc:explain` is available for "where does X live" questions and is
  not required per finding.
- **Phase 2, disposition.** Important findings, in this order: fix the code
  to the record; or amend the record to the code when "record is stale" is
  the honest reading and the change contradicts no row of the record's
  "Decisions taken during brainstorm"; or ask, batched into one numbered
  CRITICAL list at the end of the pass. Fixes are conformance only, never
  taste: the skill says so in words, because a second provider rewriting a
  working implementation to its own preferences is the failure this feature
  could introduce. After any code fix, `/omc:check` once. Minor findings are
  listed and never gate.
- **Phase 2b, trace and commit.** Insert an "Implementation review" section
  into the design record immediately before "## Deliberate complexity", so
  the record still ends with that section, which `design`, `grug` and
  `review` rely on. Content: provider, date, each finding with its
  disposition, or "conforms, no findings". A re-audit adds a dated entry
  below the previous one. Then commit the trace and any fixes on the branch
  before Phase 3: the design-record gate requires a committed, clean record,
  and a re-typed `/omc:audit` after a failed finish stage must pass it.
- **Phase 3, publish.** Invoke `finish`. It rebases the N task commits
  (more conflict surface than today's one commit; its rc 3 bail hands
  control to the user as before), squashes the task commits, the fixes and
  the trace into one commit, runs check, build, verify and review,
  describes, pushes with the lease, moves the ticket and offers the
  follow-ups. A failing stage stops there, exactly as today, and the branch
  stays unpublished.
- **Completion contract.** Complete when `finish` has run to its own
  contract. A batched CRITICAL list awaiting answers is a valid stop; once
  answered, the remaining phases resume without a new command.

### 3.5 Implement's new ending

`/omc:implement` keeps its record gate, plan and build phases unchanged. Its
third phase becomes **hand off**: commit the plan file and whatever a
subagent left behind (tracked product and docs changes; known drift such as
a `uv run`-dirtied lockfile is restored, never committed), so that every
task's work is committed and the tree is clean, then post a short summary
and this statement, a statement and not a question:

> The implementation is committed on this branch and not yet published. To
> audit and publish on this provider, type `/omc:audit` here (`$omc:audit`
> on Codex). To have another provider audit it, exit this session and run
> `omc review --claude` or `omc review --codex` in this worktree. To publish
> without an audit, type `/omc:finish`.

The clean-tree check is new behavior, not a restatement: today a dirty tree
is tolerated only because `finish`'s squash folds it in with notice.

Its completion contract becomes: complete when the branch is committed,
clean, and the three continuations have been stated. Waiting for the audit
handoff is a valid stop.

The manifest pin on implement's phase order is rewritten, red first: it
orders record gate, plan, build, handoff statement; its needles gain
`/omc:audit` and `omc review`; and it asserts that implement no longer
invokes the `finish` skill. The old `` `finish` `` needle would not have
matched the closing statement's `/omc:finish` anyway, so a silent pass was
never available.

### 3.6 `finish`, `create-mr`, `ticket-sync`: unchanged

`finish` is still the one place that squashes, runs the stages, describes,
pushes and moves the ticket. It is a self-contained conductor that does not
know its caller, so the audit calling it changes no line. It gains no audit
awareness and never refuses for lack of a trace. The `review` stage proxy
and the grug lens keep running inside it, so the audit never duplicates
them. `ticket-sync`'s review phase is still invoked only from `finish`, so
the ticket moves exactly once per publish.

### 3.7 Provider default

No flag means the configured default, mirroring `omc implement`. The known
limit stays: omc cannot warn when the auditor is the implementer, because it
does not record who implemented.

### 3.8 Skill changes

- **New `audit`** (user-facing, conductor): as in §3.4, with a completion
  contract and the externalize-first step before its first sub-skill
  invocation. Joins the user-facing list, the conductor list, and
  `tests/e2e/codex_plugin_payload.py`'s installed-skill list.
- **`implement`**: third phase becomes hand off (§3.5); its completion
  contract changes; the "ship" wording goes.
- **`design`**: unchanged; its closing statement already names only the
  implement continuations, and its manifest pin names only those.
- **Behavior layer and README**: three authority words; the conductor list
  names `/omc:audit`; the lifecycle paragraph and the command table gain
  `omc review`; the E2E paragraph names the `audited` stage.
- `finish`, `review`, `grug`, `create-mr`, `ticket-sync`: untouched.

### 3.9 Python changes

- `cli/__init__.py`: `review` subparser and dispatch branch.
- `review.py`: `run_review`, dry-run printer, seed constant.
- `implement.py`, `session.py`, `internal.py`, `wtconfig.py`, providers: no
  changes.

### 3.10 Testing

Unit tests (`just check`, hermetic, parallel):

- the `review` flags and their mutual exclusion; `--dry-run` rows including
  the `<slug>-audit` session name and the `/omc:audit` seed; refusals for
  no record and unclean record in throwaway repositories, same messages as
  implement; the headless argv carries the session name and the write-capable
  list; dry-run writes no notification files.
- manifest tests: `audit` has frontmatter, externalizes first, declares a
  completion contract, names `omc internal design-record`, `/omc:design`,
  `/omc:check` and `` `finish` ``, and orders gate, conformance, finish;
  `implement`'s pin as rewritten in §3.5; the behavior layer names three
  authority words and lists `/omc:audit` in the conductor bullet; the Codex
  payload lists `audit`.
- a pure snapshot-diff helper for "exactly the record changed among the
  specs", unit-tested with dict fixtures like `_assert_recorded`.

E2E (`tests/e2e`):

- **`implemented` changes meaning.** This definition supersedes spec
  2026-10-02 §3.10. It still runs `omc implement --claude --headless` from
  `recorded`, and asserts: product source changed, a plan is new, the record
  is unchanged, the tree is clean, at least one commit over `origin/main`,
  and the remote branch ref is absent (`snapshot_repo` records the bare
  origin's refs). It no longer asserts the finish stage block or the
  published fix. Its duration is measured again; under 240 seconds it moves
  into the default golden tier, otherwise it stays expensive. The decision
  waits for the number, as `recorded`'s did.
- **New stage `audited`** (expensive by construction: it forks
  `implemented`): `omc review --claude --headless` through the harness with
  the implement turn budget. Asserts: the record gained an "Implementation
  review" section and no other spec changed, exactly one commit over
  `origin/main`, the finish stage block in order in the marker file (leading
  `check` lines from implement are tolerated by the existing helper), and
  the published fix against the bare origin. Nothing forks `audited`; its
  snapshot is the stage-pass mechanism only.
- **In-session variations type into the resumed design session.** The
  harness resumes by slug; the implement-session path differs only in which
  named session is resumed, so no harness change is made and the
  same-provider claim rests on that identity.
- **Moved variations.** `test_from_recorded.py::test_implement_publishes`
  becomes "implement commits and does not publish".
  `failing_build_blocks_publication` forks `implemented`, applies the
  existing sabotage (the sentinel in `.omc/stage-scripts/build.sh` plus its
  marker file), types `/omc:audit`, and asserts nothing was published. All
  three monolithic `test_e2e_lifecycle_full.py` runs, the failing-build one
  included, gain an `/omc:audit` turn through a `_direct_audit` helper
  beside `_direct_design` and `_direct_implement`, because a sabotaged build
  stage is now first reached inside the audit's finish.
- **Drift variation** from `implemented`: commit a deviation on the branch
  (the greeting wrong again), type `/omc:audit`, assert the published source
  is the correct greeting and the record gained the "Implementation review"
  section. Artifact-based; no assertion on the finding's prose.
- **Fast, model-free variation** from `agreed`, beside the existing
  implement dry run: write and commit a fixture record, run
  `omc review --claude --dry-run` through the harness, assert the `record`
  and `session` rows and the seed.
- **Codex evidence.** `test_codex_handoff.py` adapts to the new
  `implemented` meaning (the artifact helper splits into an implemented
  half and an audited half). A sibling builds from the Claude `implemented`
  image, applies the Codex account volume and a config in which
  `llm.default` stays `claude`, runs `omc review --codex --headless`, and
  asserts the `audited` artifacts: the whole tail, audit plus finish, on
  Codex from a Claude-built branch. Both are expensive, Codex-marked, and
  collected by `just lifecycle-full`; `test_lifecycle_command.py` pins that
  collection exactly.

## 4. Decisions taken during brainstorm

| Question | Decision |
|---|---|
| Replace `finish`? | No. Reorder: implement stops unpublished; the audit is the last word and calls `finish`. `finish` stays typable. |
| Skill name | `audit`; the CLI command is `omc review`. `/omc:review` stays the stage proxy. |
| Session and stage spelling | `<slug>-audit` and `audited`; one spelling for everything the skill owns. |
| Provider default | Configured default; the flag overrides for this run. |
| What the audit reads | The record is truth; the plan is context; the diff against `origin/<base>`. |
| Written trace | Always, into the record before "Deliberate complexity", committed, squashed in by `finish`. |
| Publish route | Always `finish`, from the audit. No amend-and-repush path. |
| Is the audit mandatory | No. `/omc:finish` is the stated third continuation and never refuses. |
| Same-provider path | `/omc:audit` is typable in the implement session. |
| Launcher shape | `review.py` mirrors `implement.py`; nothing shared beyond what spec 2026-10-02 already shared. |
| Landing order | Launcher and `audit` first; implement's ending flips with the moved E2E assertions in one commit. |
| E2E depth | `audited` stage, moved variations, drift variation, Codex evidence test. |

## 5. Risks

- **The auditor over-fixes.** Constrained by prose (conformance only, never
  taste) and by the stage gates in `finish`. The drift variation is where a
  taste rewrite would show as a wrong published source.
- **Implemented but unpublished branches.** A user who walks away after
  implement leaves work on a branch nobody pushed. The closing statement
  names three continuations; `omc review` on a branch with no product
  change refuses loudly rather than publishing nothing.
- **Duration.** The audit turn has never been timed. `audited` is expensive
  by construction, so no gate depends on the number; `implemented` is
  re-measured and may move down a tier.
- **Three authority words.** A wider surface for a session to misread its
  licence. Each skill naming only its own handoff, the task-list rule, and
  the manifest pins are the defence, as before.
- **Codex seed trigger.** Evidence exists for `/omc:start` and
  `/omc:implement` seed positionals on Codex 0.156.1; `/omc:audit` rests on
  the same mechanism and is first proven by the Codex evidence test.
- **Rebase surface.** `finish` now rebases N task commits instead of one;
  `rebase-main`'s rc 3 bail hands conflicts to the user as before.
- **Headless subagent permissions.** Still not established (spec 2026-10-02
  §5). The audit's fixes are small and may need no subagent; the fallback,
  as before, is typing the command from the forked stage.
- **Snapshot images may embed tokens.** Pre-existing, unchanged, still worth
  one `docker image inspect` in a separate check.

## Deliberate complexity

- `omc review` seeds `/omc:audit` while design and implement keep one name
  on both sides (lens: `grug:api-common-case` on §2, §3.1 and §3.3). Waived
  by the brainstorm decision "Skill name": the user's seed names the command
  `omc review`, and `/omc:review` is already the stage proxy that `finish`
  consumes. The session and stage take the skill's spelling so the user
  holds two names, not three.
- The common lifecycle needs one more typed command to publish than before
  (lens: `grug:api-common-case` on §3.5). Waived by the decisions "Replace
  `finish`? No, reorder" and "Is the audit mandatory? No": publication gated
  by the audit is the point, and `/omc:finish` is the stated escape.
