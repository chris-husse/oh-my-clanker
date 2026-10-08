# Multi-repository worktree support (workspaces)

**Date:** 2026-10-08
**Slug:** `multi-repo-worktree-support`
**Status:** design record, committed by `/omc:design` (converged in chat;
hardened with explain + grug per section and a whole-record pass, two iterations)

## 1. Problem

omc assumes one repository per slug. `omc design` cuts one worktree from the
checkout it is run in (`src/omc/worktree.py:create_worktree`, sole caller
`src/omc/start.py:run_start`), the branch name is derived from that repo's own
committed config (`src/omc/wtconfig.py:branch_for`), the design-record gate
keys on the branch of the checkout containing the current directory
(`src/omc/wtconfig.py:resolve_design_record`), and `finish` removes exactly one
worktree (`skills/finish/SKILL.md`, Step 6). When a feature needs a change in a
second repository — a library the master depends on, a sibling service — there
is no safe path: a session either edits the other repo's primary checkout
directly, or hand-creates a branch there outside every omc gate, and nothing
remembers which repositories belong to the slug once the session ends.

The original ask, condensed: check out missing sibling repos into the parent
folder when it looks like a bag-of-projects folder, otherwise ask; never create
branches directly in those repos — always `wt` worktrees carrying the master's
slug; when a sibling has an `.omc/` directory, proxy `omc design|implement|
review` from the master onto it, and when it has none, tell the user and offer
to retry or drop it; close all of a slug's worktrees in MR order, verifying each
was merged; list MRs across every repo; keep a clear persistent list of the
repos being worked on with their slugs.

The user's seed, verbatim:

> This is part of the existing design stage. I should discover what it needs to
> change as part of the design. But then we need a step after the brainstorm,
> probably when `/omc:design` is invoked, where it goes and fetches these repos
> locally, asks the user to set them up for OMC, and proceeds only once each
> one of them have a `.omc` directory. I think this is the precondition we can
> safely set. Then the new step is basically /omc:dependency-design which takes
> the repo path and uses a subagent rooted there (or some `omc internal`
> command) to run the brainstorming against this dependency. it does it for
> each dependency that popped up as modification target in the brainstorming.
> Then uses the result in the design hardening process and to create a master
> design document for all dependencies. Then it runs `/design` in a subagent or
> via internal command on each of these dependencies with the master design but
> with the instructions to only design this dependency repo. Hopefully we will
> catch the cross section in most of the cases without going further, like
> proper topological order or anything. This might be good enough for now. The
> design documents for just the dependencies should be placed in their
> respective repo. the master document stays in the master repo. Then all
> future commands on the master repo, i.e. `/implement` and `/audit` will need
> to consider the dependencies. I am actually not sure if this is a good
> design. We may actually need to produce a master plan. But whenever you
> operate within a dependency repo, you shouldn't just make the change there
> directly, but rather build a plan there with /implement and execute it with
> `omc` in the same way as if this was a small feature being implemented there
> if that makes sense.

## 2. Goals and non-goals

Goals:

- A slug may span several repositories. Every repository involved gets an
  ordinary omc worktree on an ordinary omc branch, cut by `wt`, never a branch
  in a primary checkout.
- The set of repositories is discovered during the master brainstorm and
  gated at `/omc:design`: no design record is written until every named
  repository is checked out, OMC-aware, and has its worktree.
- One persistent, machine-owned ledger of which repositories belong to which
  slug, where their worktrees are, and what has been committed there.
- Each dependency repository gets its own design record, written there from
  the master record, and is then implemented and audited by that repository's
  own omc lifecycle in a child session — so every existing per-repo verb,
  gate and stage works unchanged inside a dependency.
- `finish` in the master reports every repository's pushed branch and
  compare URL together; the close follow-up closes the whole arrangement in
  order, verifying each branch was merged.

Non-goals (deliberately out of scope):

- Topological ordering of merges or dependencies between dependencies. The
  only ordering is "all dependencies before the master"; catching the
  cross-section is the goal, not a merge scheduler.
- Cross-repo stage orchestration or a combined gate. Stages are per repo.
- A master plan document. The master record's repository table plus the
  ordering rule is the cross-repo plan.
- A "treat as a plain repo" mode for repositories without `.omc/`. Such a
  repository is retried or dropped from scope; the user handles it by hand.
- Forge API calls. `wt list --format=json` exposes the forge URL and the
  upstream counts; omc never lists or creates MRs through an API
  (`skills/create-mr/SKILL.md`).
- A static list of related repositories in project config.
- Making the dependency's own harness instructions (its `CLAUDE.md`, plugin
  settings) load inside a design-phase subagent (section 3.4).
- E2E coverage of the LLM-driven proxy flow (section 3.9).

## 3. Design

### 3.1 Vocabulary and shape

- **Workspace**: one slug spanning several repositories.
- **Master**: the repository the work started in — where `omc design` ran
  and where the master record lives.
- **Dependency**: any other repository the master brainstorm named as a
  modification target.

Every repository in a workspace carries the same slug. Each repository's
branch is its *own* `worktree.branch_prefix` plus the slug, from its own
committed `.omc/config.yaml` (`src/omc/wtconfig.py:branch_for`, read through
`src/omc/config/resolve.py:project_config`), and each worktree is cut from
its own `worktree.base_branch`. Because the slug is shared, the existing
inverse (`src/omc/wtconfig.py:slug_for`) recovers the same slug from any
repository's branch, and `omc internal design-record` works unchanged inside a
dependency worktree. A single-repository slug never touches any of this: the
workspace machinery engages only when the brainstorm names a second
repository.

### 3.2 The ledger

One per-user file, `~/.omc/workspaces.json`, under `ToolContext.home`
(`src/omc/toolctx.py`), following the pattern that `dependencies.json`
already established (`src/omc/dependency.py:update_manifest`,
`src/omc/dependency.py:save_manifest`): a flock on a sibling lock file around
every read-modify-write, a unique temp file plus atomic rename for every save,
readers lock-free. It is written only by the `omc internal workspace` verbs
(section 3.3); no skill edits it by hand.

Entries are keyed by master repository plus slug. Per repository the ledger
holds only what cannot be recomputed from the checkouts: a repository key
(host, owner, name from `wt list --format=json`'s `repo.forge` block, falling
back to the credential-excluded origin URL where no forge is recognised),
the primary checkout path, the worktree path, the branch, the base branch,
and the role (`master` or `dependency`). The fallback is the normal case
for a local bare origin, as in the E2E fixture. It preserves host, port,
repository path, local bare-path identity and SSH username, excluding HTTP
userinfo and SSH passwords. Credentials must not appear in progress,
verdicts, errors or persisted identity.

Explicit URL reuse requires a matching origin before worktree or ledger
mutation. Compare conservatively within the same transport, allowing host
case and optional remote `.git`/trailing slash differences, preserving ports,
SSH accounts and exact local paths. Repositories are stored in registration
order; every ordered walk visits dependencies in that order and the master
last.
Whether a repository's design record is committed is *not* stored: `list`
derives it on demand by running the existing gate against that worktree
(`src/omc/wtconfig.py:find_design_record`), so the ledger never holds a
second copy of a truth git already has.

The ledger is the single machine truth about which repositories belong to a
slug. The master design record carries a human-readable `## Repositories`
table rendered from the ledger once, when the record is written (section
3.4). It is a snapshot for readers, never hand-edited and never regenerated
afterwards: the record is committed, and rewriting it later would trip the
`unclean` refusal of the design-record gate for every subsequent verb.

A workspace is created on the first `workspace add` for a slug: that call
derives the master from the current worktree and registers it alongside the
first dependency. A slug nobody adds a dependency to therefore has no ledger
entry at all.

### 3.3 `omc internal workspace` verbs

Four verbs join `omc internal` (`src/omc/internal.py:run_internal`), all
I/O through `ToolContext`, argv lists only, each printing exactly one
single-line `OMC_WORKSPACE {…}` verdict on stdout and narrating progress on
stderr. Exit codes follow the internal legend: 0 ok, 1 error, 2 refusal,
3 bail ("inconclusive — the calling skill falls back to its own judgment").

**`add <name-or-url>`** — resolve, verify, cut, register.

1. *Resolve.* First an existing sibling checkout in the master's parent
   folder (a directory of that name whose `.git` is a directory — a real
   repository, not one of the master's own worktrees, which carry a `.git`
   *file*). Else derive a URL from the master's `origin` by swapping the last
   path segment and confirm it with `git ls-remote`. Else bail (rc 3) with
   `reason: unresolved`, and the skill asks the user for a URL and re-runs
   `add` with it.
2. *Clone when allowed.* A clone lands in the master's parent folder only
   when that folder looks like a bag of projects: it contains at least two
   real repositories (by the `.git`-is-a-directory test above). Otherwise
   bail (rc 3) with `reason: not-a-projects-folder`; the skill asks the user
   where to put it and re-runs `add` with the path.
3. *Verify OMC-aware.* The checkout must contain `.omc/`. A miss is a
   refusal (rc 2) with `reason: not-omc-aware` and the checkout path; the
   skill shows the two-option prompt — retry (the user integrates the repo,
   e.g. via `/omc:integrate` there) or drop it from scope.
4. *Cut the worktree.* `wt -C <primary> switch --create <branch> --base
   origin/<base> --no-cd --yes --format=json`, with the same retry-without-
   `--create` idempotence `create_worktree` has today, after a best-effort
   `git fetch origin <base>` in that primary (`src/omc/worktree.py:sync_base`
   with a working directory). `create_worktree` grows a working-directory
   argument rather than a sibling function; `wt -C <path>` is a global
   option (verified against `wt` v0.79.0) and `ToolContext.run` already
   accepts `cwd`.
5. *Register.* Locked read-modify-write of the ledger; re-adding an already
   registered repository is a no-op that reports the existing entry.

The verdict carries the repository key, paths, branch and role on success,
and `reason` plus a one-line `message` otherwise.

**`list`** — the workspace the current worktree belongs to, in any role
(slug from the current branch, as `resolve_design_record` derives it;
membership from the ledger), enriched per repository from `wt -C <primary>
list --format=json`: ahead/behind against upstream and, where `repo.forge`
is known, one compare URL per repository; plus the design-record status
derived as described in section 3.2. Dependencies first, master last. No
workspace for this slug is a plain `ok: true` with an empty list, so callers
need no second probe. This *is* "listing MRs across all repos": the skill
layer renders the verdict as a table (role, branch, worktree, record status,
ahead/behind, compare URL). No forge API is called.

**`close`** — the chain. Accepted only from the master worktree: run from
a dependency it refuses (rc 2, `reason: not-master`, naming the master
worktree), so a dependency's own `finish` can never dismantle the workspace.
Dependency worktrees stay in place until this master-owned close removes them
and their ledger entries together; local removal would strand membership.
For each repository in ledger order (dependencies first, master last):
`git fetch origin +refs/heads/<base>:refs/remotes/origin/<base>` in its primary,
explicitly refreshing the ref even under restrictive fetch mappings or remote
rewinds; verify the branch is merged into
`origin/<base>` (`git merge-base --is-ancestor`); refuse (rc 2,
`reason: unmerged`, naming the repository and branch) and stop otherwise;
then `wt -C <primary> remove <branch>` and drop the entry from the ledger.
The master is removed last; only when its entry is dropped is the workspace
itself deleted. A partial failure leaves every remaining entry in
place, so a rerun resumes exactly where it stopped. The merge check detects
an unmerged dependency; it does not prevent one (decision 9).

**`implementation-status <slug>`** — read-only completion check from a dependency
worktree. Emit `{"ok":true,"slug":<slug>,"complete":<boolean>}` with exit 0
for valid complete or incomplete work; failed Git inspection emits `ok:false`
and exits 1. It uses the Git predicate in section 3.5 and never reads or writes
completion state in the ledger. This is an internal lifecycle helper; the
user-facing workspace skill remains `list|close`.

### 3.4 Design phase in the master

**Naming targets.** The `start` and `plan` skills gain one duty: during the
brainstorm, every repository that will be modified is named explicitly as a
modification target as the discussion goes, so the list is on the table
when the user types `/omc:design`.

**Step 0.5 — workspace gate.** The `design` skill
(`skills/design/SKILL.md`) gains a step between its record check and the
Design worker dispatch, before anything is written: for every named target,
run `omc internal workspace add <target>`; on a bail or refusal, ask the
user the question the verdict's `reason` calls for (URL, location, or
retry/drop) and loop until every remaining target is registered or dropped.
A dropped target is noted in the record's `## Repositories` table as
"handled by hand". Only a fully registered set passes the gate; this is the
hard precondition from the seed.

**Step 2b — dependency explain.** A new step of the `design` skill, not a
skill of its own (it has exactly one caller). Once per registered
dependency, the main session dispatches a subagent whose Bash working
directory is that dependency's worktree (Claude's Agent tool has no working-
directory parameter; the worker roots itself with `cd`). Its brief: read
that repository's `.omc/config/AGENTS.md` and `explain-context` skill, run
`/omc:explain` there against the draft design text, and return the
dependency's constraints, affected components and open questions. Accepted
limitation (decision 3): the dependency's own harness instructions and
plugin settings do not load for a `cd`-rooted subagent; the brief compensates
by pointing at the two project-context files. If that repository's knowledge
snapshot is missing, `/omc:explain` relays its "no index" message
(`skills/plan/SKILL.md` already handles that relay for the master) and the
worker answers from the files instead, saying so.

**Master record.** The Design worker folds every dependency's answer into
the master record, which gains two sections: `## Repositories` (rendered
once from `workspace list`, section 3.2) and `## Cross-repo contract` — what
each dependency must expose or change for the master, in prose, as the one
place the cross-section is written down. The master record is then hardened
as today, section by section.

**Per-dependency records.** After the master record is hardened, for each
dependency in ledger order a Design worker rooted (via `cd`) in that worktree
writes `docs/superpowers/specs/<date>-<slug>-design.md` *there*, from the
master record with the instruction "design this repository only". Its
hardening is one whole-record pass in that repository — one `/omc:explain`
over the record and one grug `spec` pass — not the master's per-section
loop: every section was already hardened in the master, and the dependency
record only narrows it. Workers never commit (today's rule); the main
session commits each dependency record on the dependency branch with
`git -C <worktree>`, then commits the master record last, referencing each
dependency record by repository and path. `omc internal design-record`, run
with the dependency worktree as working directory, then answers `ok: true`
there exactly as it does in the master.

### 3.5 Implement in the master

`/omc:implement` (`skills/implement/SKILL.md`) gains **Phase 0.5** after its
record gate: read the ledger (`workspace list`); if the slug has no
workspace, or the current role is dependency, Phase 0.5 is a no-op. Only the
master traverses dependencies, in ledger order, and never launches itself:
run `omc implement --headless` with the dependency worktree as working
directory. That is today's launcher (`src/omc/implement.py:run_implement` →
`src/omc/session.py:run_headless`) unchanged: it validates that repository's
committed record, probes tools and plugin, and runs a full fresh provider
session *in that repository* — with its own harness instructions, plugin and
MCP wiring — through the implement skill's plan, build, milestone gate and
handoff commit. Each dependency is an ordinary omc worktree with its own
committed record, so nothing inside the child knows it is a child.

The child is a long-running foreground process whose output arrives only
at exit: `run_headless` captures, and Claude's print mode with text output
prints nothing but the final message. There is nothing to follow while it
runs, and a real implement outlasts a single foreground tool call, so the
master runs the child in the harness's background mode, announces on its
own line which repository it is waiting on, and waits for the exit. A
streaming variant exists (`src/omc/providers/claude.py:headless_stream_argv`,
used by `src/omc/watch.py` for wiki runs) if live narration is ever wanted;
this record does not require it.

**Deciding what happened.** The project rule is to assert on artifacts, not
transcripts. The master reads the dependency worktree after the child
exits by running `omc internal workspace implementation-status <slug>` there.
The approved completion predicate requires a clean tree, exactly one committed
slug-specific implementation plan in `docs/superpowers/plans`, and a later
commit descending from the reachable commit that first adds that exact plan,
changing a tracked file outside `docs/superpowers/specs` and
`docs/superpowers/plans`. Compare Git ancestry, not timestamps, and not the
plan's last edit: handoff may amend the plan. Local implement Phase 1 commits
the plan before dispatching build tasks, making that chronology achievable.
Design-only, plan-only and dirty states fail. Git inspection failures block;
they are never evidence of completion. There is no ledger completion flag.
The same test is the skip rule on a restarted master, so Phase 0.5 resumes
where it stopped. A failed predicate means the child stopped early and its
final output is the message; an unanswered CRITICAL question must be relayed
and answered before resumption, even when artifacts appear complete. A nonzero
exit also needs an explicit error disposition even when artifacts appear complete. Launch failures do not imply
that a child session exists.

**Relaying a CRITICAL question.** The behavior layer already says that in a
headless run a CRITICAL question is the final output. When a child stopped
early, the master relays its final output to the user verbatim, waits for
the answer, and resumes *the same child session* in print mode with that
answer (`claude -p --resume …`; the launcher already names the session
`<slug>-implement` and Claude's `--resume` by name is live-verified at
`src/omc/implement.py:run_implement`). Start with the named session; if
ambiguous, require the exact session ID or manual resumption, never a fresh
child disguised as a resume. Preserve the dependency cwd, provider environment
and implementation tool grants; pass the answer as one quoted prompt argument.
Codex keeps the manual fallback described below. The loop repeats until the completion test passes. Only after every dependency
passes it does the master run its own Phase 1 plan and Phase 2 build. There
is no master plan (decision 4): each repository's implement writes its own.

### 3.6 Audit and finish in the master

`/omc:audit` (`skills/audit/SKILL.md`) gains the same **Phase 0.5**: per
dependency in ledger order, `omc review --headless` with the dependency
worktree as working directory (`src/omc/review.py:run_review`, unchanged),
which runs that repository's audit and then its finish — rebase, squash, its
own four stages, description, push — with the same background wait,
completion-and-skip test (here: the branch has an upstream and `ahead` is
zero in `workspace list`, i.e. it is pushed) and CRITICAL-relay loop as
section 3.5. Then the master runs its own audit
and finish.

`finish` (`skills/finish/SKILL.md`) changes in two places, and both are
keyed on the role `workspace list` reports for the current worktree, because
`finish` also runs inside every dependency child. Its Step 6 report prints
the rendered `workspace list` whenever the list is non-empty, so every
pushed branch and compare URL appears together, dependencies first, master
last — harmless in a dependency, complete in the master. Its "close the
worktree" follow-up becomes `omc internal workspace close` only in the
master. In a dependency, keep the worktree and ledger entry until the master
closes the workspace; report the master worktree and direct the user to run
`/omc:workspace close` there after every branch is merged. Do not run local
`wt remove`: workspace list and close require the registered worktree to exist.
A single-repository slug retains today's single `wt remove`. This corrects
the original local-dependency-removal design, approved by the user during
implementation review on 2026-10-08. The verb's `not-master` refusal remains
the deterministic backstop if the text is misread. The stacked-branch refusal in Step 0.1
stays per repository. The ticket-sync step runs in every repository's
finish and moves the same ticket each time; the transition is idempotent and
best-effort by design. Stages run only in the master during the
master's finish (decision 5): each dependency's own finish already ran that
repository's stages, and the master's verify exercises linked dependencies
by whatever linking that project's own build setup defines.

### 3.7 Behavior layer and skills

- **Behavior layer** (`src/omc/distribution/AGENTS.md`): one new paragraph.
  In a workspace, the lifecycle words typed in the master proxy to its
  dependencies through child sessions in ledger order; work on a dependency
  happens only inside that dependency's own implement or audit run, never by
  editing it directly from the master session. `OMC_WORKSPACE` joins the
  machine-contract list there, in `.omc/config/AGENTS.md`, the
  explain-context skill and the manifest test that pins those listings.
- **New skill**: `workspace` (user-facing `/omc:workspace list|close`,
  thin: run the verb, render the verdict, on `unmerged` or `not-master` say
  which repository and stop). It exists for the "come back later and close"
  case; `finish`'s follow-up is the common path.
- **Edited skills**: `design` (Step 0.5 gate, Step 2b dependency explain,
  per-dependency records, master commits last); `implement` and `audit`
  (Phase 0.5); `finish` (listing in the report, master-owned workspace close,
  dependency worktree retention, single-repository local close
  follow-up); `start` and `plan` (name modification-target repositories).
  `tests/unit/test_plugin_manifests.py:USER_FACING_SKILLS` gains
  `workspace`.

### 3.8 Python changes

- New `src/omc/workspace.py`: ledger load/save with lock (mirroring
  `dependency.py`), repository-key derivation from `wt list`, the resolution
  order, the bag-of-projects test, clone, `add`/`list`/`close`, the read-only
  `implementation-status` Git predicate, and the verdict printer.
- `src/omc/internal.py`: the `workspace` dispatch and its usage line.
- `src/omc/worktree.py:create_worktree` and `sync_base` gain a working-
  directory argument (passed through `ctx.run(cwd=…)` as `-C`-free git, and
  as `wt -C <primary>` for `wt`, since the primer verified `wt -C` as the
  global option).
- `src/omc/wtconfig.py:repo_root` / `primary_root` and
  `src/omc/config/resolve.py:project_config` gain an optional root argument
  so the dependency's branch prefix and base come from *its* config.
- `src/omc/session.py:run_headless` is reused as is; the master skill
  invokes the `omc` CLI through Bash so the skill layer stays the
  orchestration point. No daemons, no forge API, no new network call
  (`git ls-remote` and `git clone` run through `ToolContext` like every
  other git call).
- `README.md` is generated: the plan regenerates it through
  `/regenerate-readme` after the internal usage line and skill list change;
  nothing here hand-edits it.

### 3.9 Testing

Unit (`just check`, hermetic, stubs on a restricted PATH, exact argv):

- ledger round-trip, lock file taken on write, atomic save, idempotent
  re-add, workspace deleted when the master entry drops;
- resolution order: sibling found; sibling absent → derived URL confirmed by
  a stub `git ls-remote`; derivation refused → bail with `unresolved`;
- bag-of-projects test on tmp trees: two real repos pass, one real repo plus
  the master's worktrees (`.git` files) fails;
- `not-omc-aware` refusal when `.omc/` is absent; `not-master` refusal for
  `close` from a dependency worktree;
- `wt -C <primary> switch …` and `wt -C <primary> remove …` argv exactness,
  including the retry-without-`--create` path;
- chain close: stops at the first unmerged repository with the ledger
  intact; a rerun after merge finishes the chain; master removed last;
- the single `OMC_WORKSPACE` line for every outcome;
- manifest tests: `workspace` joins the user-facing list; `design` names
  the gate and the dependency explain step; `implement` and `audit` name
  Phase 0.5 and `omc implement --headless` / `omc review --headless`;
  `finish` names `workspace close` and retains dependency worktrees until the
  master closes them; the `OMC_WORKSPACE` contract appears in
  every listing that enumerates contracts.

E2E (`tests/e2e`, Docker, no LLM for this record):

- One plain container test (the `container` fixture plus `configure_omc`,
  as `tests/e2e/test_e2e_finish.py` does — no golden snapshot is forked,
  because no model turn is skipped): a fixture of two repositories under one
  parent (`tests/e2e/harness.py:make_work_repo` called twice, each with its
  own bare origin, so the parent holds two real repositories and the
  derived-URL path resolves to the sibling's bare origin through
  `git ls-remote`), exercising `workspace add`, `list` and `close` against
  real `wt` and `git`: asserts the second worktree exists on disk on the
  right branch, the ledger content, the fallback repository key and the
  ahead/behind counts in `list` (a bare origin has no forge, so no compare
  URL is asserted), the `unmerged` refusal, and after a fast-forward merge
  in the bare origins the full chain removal and ledger deletion. Under the
  300 s ceiling; parallel-safe.
- The LLM-driven proxy flow (gate, dependency explain, Phase 0.5 child
  sessions) is not E2E-tested in this record: a child implement alone runs
  about two minutes on the fixture and a real one far longer, which would
  break the five-minute budget. Its skill text is pinned by the manifest
  tests above, and the shared steps it touches (record gate, implement
  handoff, finish follow-ups) stay covered by the existing lifecycle judge.

### 3.10 Milestones

One record, one plan, two milestones (decision 6):

- **M1** — ledger; `workspace add|list|close`; the design skill's gate and
  dependency explain step; per-dependency records; finish listing and chain
  close; the E2E container test.
- **M2** — implement and audit Phase 0.5 with the background wait,
  artifact completion test and CRITICAL-relay loop.

Implement may hand off after M1 if its gates run long; M2 then continues on
the same branch.

## 4. Decisions taken during brainstorm

Each row was presented with a recommendation and settled in chat before
`/omc:design`; hardening notes evidence in the row where it changed one.

| # | Question | Decision |
|---|---|---|
| 1 | Ledger location and ownership | `~/.omc/workspaces.json`, per-user, flock-guarded, atomic writes (the `dependencies.json` pattern); written only by `omc internal workspace` verbs. The master record's `## Repositories` table is derived from it. |
| 2 | Repository without `.omc/` | Two options only: retry, or drop from scope (user handles it by hand). No "treat as a plain repo" mode — it would be a second lifecycle every skill must branch on. |
| 3 | How design-phase subagents reach a dependency | Rooted in the dependency worktree via Bash `cd`; accepted that the dependency's own harness instructions and plugin settings do not load; the brief points at `.omc/config/AGENTS.md` and explain-context there. |
| 4 | Master plan | None. The master record's Repositories table plus the ordering rule (all dependencies before the master, no order among dependencies) is the cross-repo plan; each repo's implement writes its own plan. |
| 5 | Stages | Per repo only. Each dependency's child runs its own check/build/verify/review in its own finish; the master's stages run only in the master. No cross-repo gate. omc's own E2E is plumbing-only, fixture = one master + one dependency; repo count is unbounded in the per-repo loops. |
| 6 | Shape of the work | One record, one plan, two milestones: M1 plumbing + design-phase; M2 implement/audit proxying with the headless resume loop. |
| 7 | How related repos are known | Discovered during the master brainstorm as explicitly named modification targets; hard precondition gate at `/omc:design`. Not a static config list. |
| 8 | Orchestration mechanism | Hybrid: deterministic plumbing in Python; in-session subagents for the design phase (user interaction matters, output is text); child CLI sessions (`omc implement --headless`, `omc review --headless` in the dependency worktree) for implement and audit, so each dependency is an ordinary omc worktree where existing verbs work unchanged. |
| 9 | Merge ordering | No topological ordering; "good enough" is catching the cross-section. The close chain's merge check detects, not prevents, an unmerged dependency. |

## 5. Risks

- **Headless resume on Codex is unverified.** `codex exec` has no session
  name in omc's provider today (`src/omc/providers/codex.py:headless_argv`),
  and its resume semantics are untested. Fallback: the master reports the
  child's CRITICAL question and the user resumes that dependency by hand
  (`omc implement` in its worktree); M2 ships with Claude verified and Codex
  on the fallback until proven.
- **URL derivation across forges.** Swapping the last path segment of the
  master's `origin` assumes the sibling lives beside it under the same owner
  and host. When it does not, `git ls-remote` fails and `add` bails with
  `unresolved`; the skill asks for the URL. Nothing is cloned on a guess.
- **`cd`-rooted subagents lose the dependency's harness context** (decision
  3). The brief's two pointers are the mitigation; a dependency whose
  conventions live only in a `CLAUDE.md` is designed with less context than
  its own sessions have. The implement and audit children do not share this
  limit — they are full sessions in that repository.
- **Child session duration.** A child implement or audit is a full lifecycle
  run; the master waits for it, blind until the child exits. A workspace
  with several dependencies is inherently a long run. The user accepted
  serial per-repo loops; parallel children are a later change if the wait
  hurts.
- **Dependency knowledge snapshot absent.** The dependency explain step and
  the per-dependency Design worker rely on `/omc:explain` in that repository,
  which needs an index the master has no way to build (omc never runs
  `gitnexus index` on another repo's behalf). The workers fall back to the
  files and say so; the design is weaker, not blocked.
- **Not a risk (dropped by the user):** a dependency's build needing the
  master's unpublished branch, or vice versa. Linking is each project's own
  build setup and is covered by its own stages.

## Implementation review

### Codex — 2026-10-08

- **Important — code deviates from the record**, section 3.3, Resolve,
  `src/omc/workspace.py:374`: linked sibling worktrees were accepted as
  primary checkouts. Fixed by requiring a `.git` directory before reuse;
  the regression test demonstrates refusal without worktree or ledger mutation.
- **Minor — code deviates from the record**, section 3.3, Close,
  `src/omc/workspace.py:679`: the `not-master` verdict omitted the master
  worktree. Fixed by including `master_worktree`, with regression coverage.
- **Minor — record is stale**, section 3.3, Close,
  `src/omc/workspace.py:634`: cleanup already fetched an explicit forced
  refspec to refresh the checked remote ref under restrictive mappings and
  rewinds. Amended the record to describe that command; no brainstorm
  decision changed.

The Review worker confirmed all three dispositions. Both code regressions
failed before their fixes and passed afterward. Audit check passed all 1688
unit tests; build passed after one formatting-only repair cycle. Full Docker
E2E validation passed before this audit trace was committed.

## Deliberate complexity

- The master resumes a stopped child session in print mode with the user's
  answer (section 3.5) instead of only reporting the child's question and
  letting the user resume that dependency by hand (lens: `grug:say-no` on
  section 3.5 — a second mechanism with its own provider semantics where
  "report and stop" delivers most of the value). Waived by brainstorm
  decision 6, which names "the headless resume loop" as the content of M2;
  the Codex fallback in section 5 is exactly the simpler alternative, kept
  for the provider where the loop is unverified.
- Two orchestration mechanisms: in-session `cd`-rooted subagents for the
  design phase (section 3.4) and child `omc … --headless` CLI sessions for
  implement and audit (sections 3.5, 3.6) (lens: `grug:say-no` on the whole
  record — one mechanism would be less to learn and test). Waived by
  brainstorm decision 8: a single mechanism either loses the design phase's
  interactivity with the user (child sessions cannot ask) or loses the
  dependency's own harness context in implement and audit (subagents do not
  load it); decision 3 accepts that loss only where the output is text.
