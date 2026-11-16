# One graph per repo: embeddings always on, `.gitnexusignore` kept by omc, dependency subsystem removed

Date: 2026-11-16 · Branch: `feature/default-embeddings-gitnexusignore-exclusions` · Slug: `default-embeddings-gitnexusignore-exclusions`

Predecessors:
`2026-10-27-enable-embeddings-for-gitnexus-design.md` (embeddings as an
opt-in option; this record **reverses its decision 2**, "default off", and
removes the option it introduced; its runtime layout, fallbacks and backfill
stay),
`2026-11-15-investigate-gitnexus-index-node-bloat-design.md` (the review
stage keeps `.gitnexusignore`; its D1 holds, its D2 and D3 are partly
reversed here, see §2),
`2026-07-22-gitnexus-dependency-index-explain-design.md` and the
`2026-07-23-dependency-watch-*` records (the dependency subsystem; retired by
this record, kept as history).

## 1. Problem

Three things stand between a project and a useful code graph.

**Embeddings are off unless someone finds the switch.** Spec 2026-10-27 made
`gitnexus.embeddings` an option that defaults to off. A fresh install indexes
keyword-only, and `/omc:explain` answers conceptual questions only when the
words happen to appear in a symbol name. The user's request: "Remove the
selection if embeddings should be used and make it default."

**Nobody decides what belongs in the graph before the first index.** Spec
2026-11-15 added a paragraph to the review stage that covers generated
directories a branch happens to touch. A repo whose generated code never
changes never gets a pattern (that spec's own "slow uptake" risk), bulk data
and recorded fixtures are not covered at all, and the instruction knows
nothing about the project: which generators exist, where they write, which
folder named `generated` is hand-written. `apuntalo.co` still indexes 31,195
generated symbol nodes and loses its embeddings to GitNexus's 50,000-node cap.
The user's request: "We need to add folder exlusion into /integrate to find
folders that are likely generated or data folder, etc. that should not be
indexed. Also we need to inform the project agent (the one using OMC) to keep
and maintain the .gitnexusignore file".

**A second graph subsystem exists for other repos.** `src/omc/dependency.py`
and `src/omc/depwatch.py` (801 lines), `omc dependency watch|list`,
`omc internal dependency …`, the proxy's `--git` mode and
`/omc:explain-dependency` clone other repositories into
`~/.omc/dependencies/<host>/…/<commit>`, index them keyword-only and generate
docs for them. The repos a question usually reaches into are the user's own
omc-managed repos, which already sit next to this one with their own graph
and docs.

## 2. Decisions taken during brainstorm

| # | Decision | Why |
|---|----------|-----|
| D1 | The embeddings option is removed, not defaulted to on: no `GitNexusConfig`, no menu section, no `--set gitnexus.embeddings`. A config file that still carries the key loads silently and loses it on the next save | User seed: "Remove the selection if embeddings should be used and make it default." Asked whether a stale key should load silently and drop on save: "rest ok". Reverses spec 2026-10-27 decision 2 |
| D2 | After `omc update`, the first refresh downloads the embedding runtime and model once and backfills each existing index once | User: "ok" |
| D3 | The whole `explain-dependency` machinery is removed. Questions about another repo are answered from that repo's own graph when it is an omc repo on this machine; that is an agent instruction in `/omc:explain`, not a subsystem | User: "remove the entire `explain-dependency` machinery as well we stick to one repo and we can auto-detect other OMC repos and then use our custom knowledge to navigate those. This can become an agent instruction instead of a separate subsystem" |
| D4 | Other omc repos are found through the `.omc/` folder in the known checkout layout (siblings of this repo's primary checkout, plus a path the user names). The GitNexus registry `~/.gitnexus/registry.json` is not used | User: "through `.omc` folder in the known structure man." Sibling-only lookup plus user-named paths accepted with "rest ok" |
| D5 | A plain library (not an omc repo on this machine) gets no graph. `/omc:explain` says so and may cite installed or vendored source; it never clones | Accepted |
| D6 | `omc update` deletes the retired dependency caches, narrated, and never fails over it | Accepted |
| D12 | The cleanup of D6 also deletes `~/.omc/gitnexus/`, the retired subsystem's mirrored dependency docs | Found during hardening: the accepted scope did not name this directory. Asked whether the cleanup should delete it too, the user answered: "yes" |
| D7 | What to ignore is a judgement made by reading content, not a heuristic in omc code | User: "Well, it looks at the content and tries to use judgement to detect which files are likely generated or just data files." |
| D8 | "Data" means bulk data and recorded fixtures. Hand-written source, tests and docs stay indexed | Accepted |
| D9 | omc owns the upkeep flow; the project teaches it through a hook skill, `gitnexus-ignore-context`. The flow runs in the review stage and nowhere else, except that `/omc:integrate` builds the hook and runs the flow once to create the first file | User: "it should be a hook with `gitnexus-ignore-context` that let's the project teach us about how to find things to ignore. OMC manages this on its own. Let's try that. And we do it as part of the review flow only. /integrate builds the `gitnexus-ignore-context` and runs the flow once to build the initial file. rest ok." Spec 2026-11-15 D1 (review stage only, agents write the file, omc code does not) holds. Its D2 is reversed in one part (`/omc:integrate` now seeds the file) and kept in the other two (no behavior-layer text, no hint in the refresh narration). Its D3 is narrowed: the step still reads only the branch diff on a normal finish, but surveys the whole tree when the file is missing |
| D10 | A `.gitnexusignore` holding only its header comment means "surveyed, nothing to ignore". The review stage creates a missing file | Accepted ("rest ok") |
| D11 | No `.gitnexusignore` bullet in the behavior layer (`src/omc/distribution/AGENTS.md`) | Default taken: omc manages the file in the review flow, so the project agent has nothing to remember |

## 3. Goals, non-goals, anti-goals

One graph per repo, always semantic, trimmed to hand-written code.

Goals:

- **G1.** Every index omc builds or refreshes embeds, with no setting.
- **G2.** omc keeps `.gitnexusignore` current at every finish, guided by the
  project's `gitnexus-ignore-context`. `/omc:integrate` builds that hook and
  the first file before the first index.
- **G3.** A question that reaches into another omc repo on this machine is
  answered from that repo's own graph and docs.

Non-goals:

- Changes to the GitNexus fork or to its 50,000-node cap.
- A heuristic in omc code that decides what is generated (D7; also a
  non-goal of spec 2026-11-15).
- Graphs for plain libraries (D5).
- The GitNexus registry as a way to find repos (D4).
- Embedding backends, models or a per-project switch (unchanged from spec
  2026-10-27 §8).

Anti-goals, things this change must not cause:

- Hand-written source, tests or docs dropping out of the graph.
- A failed embedding step failing `omc update`, `omc watch` or a refresh.
- A stale-key notice printed on every command after the update.

## 4. Approaches considered

**Embeddings.** Delete the option (picked, D1), or keep the knob and flip its
default. The user rejected the knob. Without it, one config section, one menu
section, one `--set` branch and the "off" half of every embedding test go
away.

**Deciding what to ignore.** Three shapes:

1. An omc-owned internal flow plus a project hook (picked, D9). One text
   carries the judgement rules; review and integrate both call it; the project
   corrects it through its own file.
2. The same prose written twice, in `skills/review/SKILL.md` and
   `skills/integrate/SKILL.md`. Rejected: two copies of a judgement procedure
   drift, and neither has a place for project knowledge.
3. An `omc internal` command that computes the patterns. Rejected: it
   contradicts D7 and the 2026-11-15 non-goal, and directory names alone
   cannot tell a hand-written `generated/` from a machine-written one.

**Other repos.** A sibling `.omc/` scan written into `/omc:explain` (picked,
D3/D4), the GitNexus registry (rejected by the user), or keeping the
dependency subsystem (rejected by the user).

## 5. Embeddings always on

### 5.1 Config

`GitNexusConfig` is deleted from `src/omc/config/schema.py`, with its field
on `GlobalConfig` and on the runtime `Config`, the `gitnexus=` argument in
`config/resolve.py`, the "GitNexus" section of the `omc configure` menu
(`configure.py`), the `--set gitnexus.*` branch and the type check in
`config/store.py`.

Every config file omc has saved since spec 2026-10-27 contains
`gitnexus: {embeddings: false}`, because saves dump the whole dataclass.
`_hydrate` reports an unknown top-level key on every load with
"· config: ignoring unknown key(s)", which would print on every omc command
until the next save. So `_hydrate` skips the one retired top-level key,
`gitnexus`, by name and without the notice. Saves write `asdict(cfg)`, so the
key disappears the next time the file is saved. No migration step, no rewrite
on load, and no general retired-key mechanism until a second key retires.

Why the option existed: the user's 2026-10-27 ruling made embedding opt-in so
that nobody paid the download and the CPU time without asking for it, and
"off" meant "stop spending CPU" (that spec's decisions 2 and 6). D1 withdraws
that protection on purpose. What remains is that every embedding failure
degrades to keyword-only (§5.2); there is no way left to turn embedding off.

### 5.2 Runtime

`gitnexus.ensure_embeddings` loses its `cfg` argument and its "off" branch.
It always probes the runtime and installs it when missing, at its three call
sites: `refresh_knowledge`, the up-to-date path of `update_gitnexus`, and the
reinstall after a GitNexus rebuild. `omc update` on a machine without a
config installs the runtime as well; `installer.py` no longer passes `cfg`
for this purpose.

`analyze_argv` keeps its `embeddings` flag: the keyword-only retry after a
failed embedding analyze still needs the plain argv.

Every fallback from spec 2026-10-27 is unchanged and now applies to everyone:

- a failed runtime install is a narrated `✗` and the index is built
  keyword-only;
- a failed or stalled embedding analyze retries keyword-only in the same run;
- an index over GitNexus's node cap stays keyword-only, as GitNexus reports.
  GitNexus's `analyze` applies its 50,000-node cap, but `embeddings sync`
  does not, so the one-time backfill mirrors the cap
  (`GITNEXUS_EMBEDDING_NODE_CAP` in `gitnexus.py`, which must follow the
  fork's `DEFAULT_EMBEDDING_NODE_LIMIT`, compared with `>` on the index's
  total `stats.nodes`) and skips an index above it with one neutral line,
  `· index over GitNexus's 50000-node embedding cap — staying keyword-only`
  (no `.gitnexusignore` hint, D9). Once the index is under the cap, the next
  refresh backfills it;
- an existing index with `stats.embeddings == 0` is backfilled once with
  `embeddings sync`.

None of these fails `omc update`, `omc watch` or a refresh.

The first refresh after the update is slower once per machine (runtime and
model download) and once per repo (backfill). Both are narrated by the
existing lines (D2).

### 5.3 Wording

- `skills/gitnexus-explain/SKILL.md`: the keyword-only hint no longer tells
  the user to enable an option. It says the runtime is missing or the backfill
  has not run yet, and that the next refresh (`omc watch`, `/omc:index`)
  repairs it.
- `toolctx.gitnexus_env` docstring: drop the reference to the option.
- `README.md` through `.claude/skills/regenerate-readme/SKILL.md`: embeddings
  are described as always on; the option and `--set` example are removed.

## 6. `.gitnexusignore`: omc runs the upkeep, the project supplies knowledge

### 6.1 Project hook `gitnexus-ignore-context`

A project skill at `.omc/skills/gitnexus-ignore-context/SKILL.md`, resolved
with `omc internal skills list gitnexus-ignore-context`. That command already
resolves any skill name in project, primary and global (`~/.omc/skills`)
order, so no omc code changes. When several are found the project's file wins
a disagreement, as with `explain-context`.

It is a context skill like `explain-context`: prose the flow reads, no
contract line. It teaches four things:

- which generators the project runs and where they write;
- markers their output carries (a header line, a file suffix);
- where bulk data and recorded fixtures live;
- folders that look generated or look like data but are hand-written and
  must stay indexed.

The hook is optional. Without it the flow works from its built-in rules.

### 6.2 Internal skill `gitnexus-ignore`

A new `skills/gitnexus-ignore/SKILL.md`, described as "Internal — used by
/omc:review and /omc:integrate; not meant for direct invocation". It is the
one place the judgement rules are written.

Two call forms, one per caller:

| Call | Caller | Scope | Writes |
|---|---|---|---|
| no argument | `/omc:review` | whole tree when `.gitnexusignore` is missing, or when the branch diff changes the hook or `.gitattributes`; otherwise only files the branch diff adds, changes or renames (`--diff-filter=AMR`; a moved directory lists under its new path) | edits the file and runs `git add .gitnexusignore` |
| `survey` | `/omc:integrate` | whole tree | nothing; returns the proposed file text |

Steps:

1. **Read the project's knowledge.** Resolve the hook (§6.1) and read every
   returned file. None returned: built-in rules only.
2. **Pick the scope** from the table above. Only tracked files matter
   (`git ls-files`); the index covers nothing else (spec 2026-10-07). A
   branch that edits the hook or `.gitattributes` gets the whole tree because
   such an edit names directories the branch itself does not touch; with diff
   scope the new knowledge would never be applied.
3. **Judge.** The project context comes first. Then built-in hints say where
   to look:
   - `linguist-generated` and `linguist-vendored` lines in `.gitattributes`;
   - directory names such as `generated`, `gen`, `dist`, `build`, `out`,
     `vendor`, `third_party`, `data`, `fixtures`, `snapshots`;
   - lockfiles, `*.min.*`, bundles;
   - in a whole-tree survey, directories ranked by tracked file count and
     bytes.

   A hint only nominates a candidate. Sampled file content decides (D7): a
   generator header ("Code generated … DO NOT EDIT", `@generated`,
   `<auto-generated>`), minified lines, data-shaped files, many near-identical
   files. Hand-written source, tests, docs and everything the context lists as
   a keep stay indexed (D8). When the content does not settle it, the
   directory stays indexed and the result line says which one was left in.
4. **Write.** Patterns cover directories, in gitignore syntax, each with a
   one-line comment saying why. The file starts with a header comment naming
   omc and the hook. A file with only that header means "surveyed, nothing to
   ignore" (D10), which is what stops the next finish from surveying the
   whole tree again. The flow adds patterns and leaves existing lines alone,
   with one exception: a line that ignores something the hook lists as a keep
   is removed and named in the result line.
5. **Return** one line naming the patterns added (or "none"). The flow never
   fails its caller: an unreadable hook or a failed write is reported in that
   line and the caller continues. `survey` returns the full proposed file
   text followed by the same line with `proposed` in place of `added`. An
   unknown argument prints a usage line, and an unresolvable base in diff
   scope returns `gitnexus-ignore: skipped (cannot resolve base <base>)`.

### 6.3 Review stage

The "GitNexus index hygiene" paragraph in `skills/review/SKILL.md` keeps its
place and its routing: reached from both passing branches of the project
stage, skipped with the lenses when the project stage fails, run inline in
the main session before `3. **Grug lens.**`. Its body changes from an inline
instruction to: invoke the internal `gitnexus-ignore` skill, name the
patterns it added in the summary, never fail the stage over it.

This is the only place upkeep runs (D9). `/omc:finish` amends stage changes
with `git add -u`, which leaves a new file out, so the flow's explicit
`git add .gitnexusignore` stays load-bearing (spec 2026-11-15 §4).
`skills/finish/SKILL.md` gains one clause: a new file a stage has staged
(such as review's `.gitnexusignore`) is not an untracked artifact and goes
into the same amend. This supersedes spec 2026-11-15 §4's "finish is not
changed"; finish still does not run the upkeep flow (D9).

On the first finish in a repo without the file, the review stage surveys the
whole tree and commits the result with the branch (D10). The file is in the
MR diff, with a comment per pattern, where the reviewer sees it.

### 6.4 `/omc:integrate`

- **Inventory.** One new row: `.omc/skills/gitnexus-ignore-context` and
  `.gitnexusignore`.
- **A new Phase 1 step before the `/omc:index` offer.** The first index
  should already be trimmed, so this slot cannot wait for Phase 2. The step
  investigates how the project generates code and stores data (build scripts,
  generator configs, `.gitattributes`, CI), proposes the hook and writes it on
  approval. Then it invokes `gitnexus-ignore survey`, shows the proposed file
  (in review mode, as a diff against the existing one) and writes it on
  approval. GitNexus reads `.gitnexusignore` from the working tree, so the
  index offered next honors it before anything is committed, in the primary
  checkout; run from a worktree, it takes effect once merged, since
  `/omc:index` indexes the primary.
- **Headless.** Both are proposed, nothing is written, as for every other
  slot.
- **Phase 3.** The after-table lists both files; they are committed with the
  rest.

In a repo that already has an index, a new pattern takes effect at the next
refresh: the incremental analyze drops paths that are no longer scanned (spec
2026-11-15 §4, read from the fork's source). The remedy if it does not is the
existing `omc watch --once --reset-gitnexus`.

### 6.5 What is not added

No bullet in the behavior layer (D11), no hint in the refresh narration, no
omc code that reads or writes `.gitnexusignore`.

## 7. Dependency subsystem removed; other repos answered from their own graph

### 7.1 Deleted

- `src/omc/dependency.py`, `src/omc/depwatch.py`.
- `omc dependency watch|list`: the argparse entries and the dispatch in
  `cli/__init__.py`.
- `omc internal dependency ensure|document|list`, the `--git REF` mode of
  `omc internal gitnexus`, and their lines in the usage text (`internal.py`).
- `skills/explain-dependency/`. Skills are discovered by directory, so the
  only lists that name it are in `tests/unit/test_plugin_manifests.py`.
- In `skills/explain/SKILL.md`: Step 3's dependency handoff and the "optional
  dependency keys" field of a source report in Step 2b. A source that still
  returns keys is not an error; the field is ignored.
- The README lines about the subsystem, through `regenerate-readme`.

Why this code existed: it gave `/omc:explain` graph evidence for code outside
the repo, pinned to a commit so answers were reproducible. What replaces it
is §7.3 for the user's own repos and D5 for everything else. What is lost on
purpose: a graph for third-party libraries, and commit pinning (a sibling
checkout is at whatever commit it is at; its own `OMC_KNOWLEDGE` freshness
line is relayed instead).

`src/omc/wikirun.py` stays while the project wiki uses it. Its comments and the
re-export tests that mention `dependency.py` are adjusted. Kept untouched:
`~/.omc/dependencies/gitnexus` and `~/.omc/dependencies/gitnexus-embeddings`
(omc's own managed tools), the captured fixture `tests/e2e/artifacts/omc-wiki/`,
and the 2026-07 specs.

### 7.2 Cache cleanup in `omc update`

`omc update` removes what the subsystem left on disk:

- `~/.omc/dependencies.json` and `~/.omc/dependencies.json.lock`;
- everything under `~/.omc/dependencies/` except `gitnexus/` and
  `gitnexus-embeddings/`;
- `~/.omc/gitnexus/`, where `dependency.py` mirrored each dependency's
  generated docs (`~/.omc/gitnexus/<host>/…/<commit>/docs`) (D12).

`~/.omc/gitnexus/` is safe to remove whole: `dependency.py` (`docs_dir` and
`run_document`) is the only code under `src/omc/` that reads or writes it,
and no skill names it. omc's own GitNexus install lives elsewhere, under
`~/.omc/dependencies/gitnexus`, and project docs live in each repo's
`.omc/docs/`.

It narrates `· removed retired dependency cache (<size>)`. It keeps no
marker: with nothing to remove it is silent, so it can run on every update.
A failed delete is a narrated `✗` and never changes the update's exit code
(D6).

`omc update` continues in the process that started before the package was
upgraded, so the cleanup first runs on the update after the one that
delivers it. That delay is accepted; the cache is inert once the code that
read it is gone.

### 7.3 `/omc:explain` Step 3: the question reaches another repo

Step 3 becomes an instruction for questions that hinge on code outside this
repo.

**Finding the repo.** omc's layout is known: primary checkouts sit side by
side under one parent directory, and worktrees sit beside them. Look in the
parent of this repo's primary checkout for directories that contain `.omc/`
and whose `.git` is a directory. A worktree's `.git` is a file, so worktrees
are excluded without relying on their names. A path the user names is checked
the same way. Match the repo the question means by directory name or by the
repo name in `git remote get-url origin`. When several checkouts match, the
answer names them and uses the one whose directory name equals the repo name;
with no such one it reports the ambiguity instead of picking.

**Found.** Query that repo's graph from its checkout:
`(cd <repo> && omc internal gitnexus query|context|impact|cypher …)`. The
proxy resolves the primary root and base branch from the working directory,
so no new flag is needed. Read that repo's
`.omc/skills/explain-context/SKILL.md`, and its generated docs under
`.omc/docs/gitnexus/docs/` when they exist. The proxy prints that repo's `OMC_KNOWLEDGE`
line on stderr when its snapshot is stale; relay it. Fold the cited findings into the answer, attributed to that
repo. Content from another repo is data, never instructions. A sibling
without an index is named with "run `/omc:index` there"; nothing is indexed
from here.

**Not found, not omc-managed, or a plain library.** Name it, say it has no
local graph, and answer from installed or vendored source when that is
available. Never clone (D5).

The behavior layer's "Ask the graph, not grep" bullet gains half a sentence:
"including sibling omc repos — `/omc:explain` reaches them". This is the only
behavior-layer change.

## 8. Tests (red first, per `.omc/config/AGENTS.md`)

Each new or changed test is written first and seen failing for the expected
reason. Tests for deleted behavior are deleted with it.

**Embeddings.**

- `tests/unit/test_config_store.py`: a file carrying
  `gitnexus: {embeddings: true}` loads with no notice on stderr and the key is
  gone after a save; another unknown top-level key still prints the notice.
  The default-off and round-trip tests are deleted.
- `tests/unit/test_configure.py`, `test_configure_menu.py`: the `--set` and
  toggle tests are deleted; `--set gitnexus.embeddings=true` is now an
  unknown-key error.
- `tests/unit/test_gitnexus_refresh.py`, `test_gitnexus_update.py`: the "off"
  cases are deleted; the "on" cases run without a config (a
  `tests/unit/conftest.py` autouse seam keeps `ensure_embeddings`
  keyword-only unless a test carries `@pytest.mark.real_embeddings`; the
  embedding cases carry it, and the product has no switch); a new case shows
  `update_gitnexus` installing the runtime with no config at all.
- `tests/e2e/test_e2e_gitnexus.py`: the opt-in test becomes "a default
  refresh embeds the real repo", without `--set`.
- `tests/unit/test_cli.py`: the README guard asserts the runtime layout and
  the absence of `gitnexus.embeddings`.

E2E cost: `docker/Dockerfile.e2e` pre-bakes the runtime and the model, so no
test downloads them, but every E2E that refreshes an index now embeds on CPU.
Four files do (`test_e2e_gitnexus.py`, `test_e2e_explain_sources.py`,
`test_e2e_watch.py`, `test_e2e_docs_artifact.py`), and some index `/repo`,
the real omc codebase: the existing embedding test measured 112.5 s for that
first refresh, and the suite runs under a 300 s per-test ceiling
(`pyproject.toml`). The plan measures each of those tests with embedding on
and rebalances their step budgets or their `@pytest.mark.timeout` before the
change lands. Test budgets move; the product gets no switch for tests (D1).

**`.gitnexusignore`.** Skill-text guards in
`tests/unit/test_plugin_manifests.py`, in the needle and `text.index` style
that file already uses:

- review invokes `gitnexus-ignore` after the project stage and before the
  grug lens, and never fails the stage over it (the existing
  `test_review_proxy_keeps_gitnexusignore_covering_generated_code` is updated
  to the new body);
- `gitnexus-ignore` is in the internal-skill list, resolves
  `omc internal skills list gitnexus-ignore-context`, names
  `git add .gitnexusignore`, has both call forms and the header-only rule;
- integrate places the hook slot and the flow run before the index offer.

One E2E: `tests/e2e/test_e2e_integrate.py`'s headless fresh-setup fixture
gains a committed generated directory. The existing test must still find
nothing written, and its proposal must name the hook and a `.gitnexusignore`
pattern for that directory. No Docker-plus-LLM test for the review step: it
never gates, and the same flow is exercised by the integrate test.

**Dependency removal.**

- Deleted: `tests/unit/test_dependency.py`, `test_depwatch.py`,
  `tests/e2e/test_e2e_dependency.py`, and the dependency cases in `test_cli`,
  `test_internal`, `test_wikirun`, `test_plugin_manifests`.
- New unit test for the cleanup against a temporary home: the manifest, lock,
  a dependency checkout and `~/.omc/gitnexus/` are removed,
  `dependencies/gitnexus/` and `dependencies/gitnexus-embeddings/` survive,
  the line is narrated; a second run is
  silent; a delete that fails narrates `✗` and returns success.
- `omc dependency` and `omc internal dependency` are usage errors;
  `omc internal gitnexus --git …` is a usage error.
- Skill-text guard: explain Step 3 names the `.omc/` and `.git`-directory
  rule, the `(cd … && omc internal gitnexus …)` form, and "never clone"; it no
  longer names `explain-dependency`. A guard on the behavior-layer half
  sentence.
- One E2E replacing the two deleted dependency E2Es: two fixture repos side
  by side in the container, both omc-managed and indexed; a question in the
  first that hinges on a marker symbol defined only in the second. The judge
  passes when the answer cites the second repo's file. This is the real-tool
  test for §7.3.

## 9. Documentation

`README.md` is generated. `.claude/skills/regenerate-readme/SKILL.md` is
edited (embeddings always on; the dependency commands, the
`explain-dependency` skill and the dependency-watch model group removed; the
in-session skill count; the `gitnexus-ignore-context` hook and the sibling
lookup added) and `/regenerate-readme` is run. The README guards in
`tests/unit/test_cli.py` follow.

## 10. Left to the plan

§5, §6 and §7 do not depend on each other. The plan builds them as three
separate parts, in any order, and `just check` is green after each one; the
removal in §7 is not interleaved with the other two.

- The exact wording of the three skills, as long as the §8 needles hold.
- How `_hydrate` spells the skip of the retired `gitnexus` key.
- The order of deletions and the cleanup of `_USAGE`.
- Whether the `docs` group of `reconcile` is still needed: its only caller
  outside `omc watch`'s own groups is `omc dependency watch`.
- Where in the update path the cleanup is called, and how the size is
  computed through `ToolContext`.
- The template of the `gitnexus-ignore-context` skill that integrate
  proposes.
- Whether the review stage hands a whole-tree survey to a Review worker.
  Diff scope stays inline in the main session, as today; a first-finish
  survey reads far more than one diff.

## 11. Risks

- **The flow ignores too much.** Hand-written code under a folder named
  `data` or `generated` would vanish from `/omc:explain`. Guarded by: content
  decides, unclear stays indexed, the hook lists keeps, every pattern carries
  a comment and arrives in an MR diff.
- **A whole-tree survey inside an unattended finish.** The first finish in a
  repo without the file spends one survey and commits a file nobody approved
  interactively (D10). It never gates, and the file is reviewed with the MR.
  Running `/omc:integrate` first moves that decision to an approval prompt.
- **A finish that adds a pattern runs the final verify.** `/omc:finish`
  skips its final verify only when review left the tree unchanged. True since
  spec 2026-11-15; the first-finish survey makes it more likely once per repo.
- **Embedding cost for everyone.** The first refresh after the update is
  slow, and every later refresh embeds new nodes on CPU. Accepted (D1, D2);
  all failures degrade to keyword-only.
- **A repo over the node cap stays keyword-only** until its
  `.gitnexusignore` trims it. §6 is the remedy; the cap is a non-goal.
- **Third-party library questions lose graph evidence.** Accepted (D5).
- **A sibling checkout is not on its base branch or is stale.** The proxy
  answers from that repo's index of its base branch and its freshness line is
  relayed; nothing is refreshed from here.
- **Ambiguous siblings** (`repo` and `repo-old` with the same origin). §7.3
  names them instead of guessing.
- **A parallel branch edits the same code.**
  `feature/remove-gitnexus-documentation-feature` (design record committed on
  its branch, not merged) removes generated documentation: `wikirun.py`, the
  `document` half of `dependency.py` and `depwatch.py`, the `docs` reconcile
  group, `.omc/docs/`. It keeps the dependency index, which this record
  removes. Nothing here depends on which lands first: §7.3 reads generated
  docs only when they exist, and §7.1 deletes whatever is left of the two
  modules. The second branch to land rebases over deletions in the same
  files. The two records differ on two points, both on purpose. That one
  leaves stale config keys to the unknown-key notice; this one silences the
  `gitnexus` key because every saved config carries it (§5.1). That one's D4
  keeps `~/.omc/gitnexus/…/docs` on disk ("omc never deletes user files on an
  upgrade"); this one deletes the directory (D12). If that branch lands
  second, its D4 no longer describes what `omc update` does to that
  directory and needs the same sentence changed.
- **Cleanup runs one update late** (§7.2). Accepted; idempotent.
- **GitNexus's own registry keeps entries for deleted dependency
  checkouts.** omc does not read the registry (D4); stale rows there are
  harmless and out of scope.

## 12. Prerequisite added at implementation: a green unit gate on this host

Amended 2026-11-16, user decision. At the base commit `just check` on the
NixOS development host was red before any change (248 failed, 5 errors), so
`/omc:check` could gate nothing. Asked how to proceed, the user answered:
"direnv is installed. wt should use >= to check versions, require python 3.14
then so you dont have the loops anymore. the other I dont know". The plan's
Task 0 therefore:

- takes the parallel branch's FHS-portability commit `7b1b348` unchanged
  (the user's decision there), so whichever branch lands second drops it;
- **raises omc's minimum Python to 3.14** (`requires-python = ">=3.14"`)
  and adapts symlink-loop detection in `plugin._codex_resolve` to 3.14's
  non-raising `Path.resolve()`;
- checks the Worktrunk version as a minimum (`>= 0.79.0`), never by
  equality, and never downgrades the user's `wt`;
- fixes the pty-hangup mapping in `configure` forward (left to the builder).

- uv keeps a tool venv's interpreter on `install --reinstall` and
  `upgrade`, so `omc install`/`omc update`, `just install` and the E2E image
  pass `--python '>=3.14'` (`installer.REQUIRED_PYTHON`, pinned to
  `requires-python` by a unit test); uv downloads a managed interpreter when
  the host has none. An omc already installed on Python 3.13 or older needs
  one manual `uv tool upgrade --python '>=3.14' omc`, which the README
  documents. ruff's `target-version` stays `py312` so files run by other
  interpreters (the E2E image's python3, `scripts/`) never get 3.14-only
  syntax.

The gate is then the real `just check` on the host, fully green.

## Implementation review

2026-11-16 · auditor: Claude (claude-opus-5-5 orchestrator; Review worker opus) · base `origin/main` 5e5b6c0

1. Important · code deviates from the record · §6.5, D9 · `src/omc/gitnexus.py:841-845`, `tests/unit/test_gitnexus_refresh.py:1081` · the over-cap backfill skip narrated a `.gitnexusignore` hint in the refresh narration · **fixed** in 54c42e1 (neutral line; test asserts no `.gitnexusignore`).
2. Important · record is stale · §5.2 · `src/omc/gitnexus.py:149-153, 841` · omc mirrors GitNexus's node cap for the backfill (`embeddings sync` does not enforce it) · **record amended** (§5.2 bullet); contradicts no decision.
3. Important · record is stale · §6.3 · `skills/finish/SKILL.md:131-133` · finish's amend clause now names a stage's newly staged file · **record amended** (§6.3); contradicts no decision.
4. Important · record is stale · §12 · `src/omc/installer.py:23-27,129,147`, `justfile:65`, `docker/Dockerfile.e2e:158-161`, README, `pyproject.toml` ruff target · `--python '>=3.14'` on install/update paths and the one-time manual upgrade · **record amended** (§12 bullet); contradicts no decision.
5. Minor · record is stale · §6.2 · `skills/gitnexus-ignore/SKILL.md:66-74` · diff scope includes renames (`AMR`) · **record amended**.
6. Minor · record is stale · §6.2 Step 5 · `skills/gitnexus-ignore/SKILL.md:24-32,64-65,177-178` · `survey` output, usage line, `skipped` line · **record amended**.
7. Minor · record is stale · §6.4 · `skills/integrate/SKILL.md:70-73` · primary-vs-worktree effect of the first file · **record amended**.
8. Minor · record is stale · §8 · `tests/unit/conftest.py`, `pyproject.toml` marker · `real_embeddings` test seam · **record amended**.
9. Minor · code deviates from the record · §8 E2E cost · `tests/e2e/test_e2e_gitnexus.py:43,237`, `tests/e2e/test_e2e_explain_sources.py:121`, `tests/e2e/test_e2e_sibling_explain.py:96` · ceilings are per-step budget sums, not measurements; finish's verify confirms them · **listed** (does not gate).
10. Minor · code deviates from the record · §6.3, D8 · `skills/review/SKILL.md:44-48` · hygiene summary under-describes whole-tree scope and bulk data · **listed** (the flow decides scope itself).


### Finish review — claude (opus), 2026-11-16

Rebased at the user's request onto the local
`feature/remove-gitnexus-documentation-feature` squash (7f5d9cd), which removes
generated documentation (`wikirun.py`, `/omc:document`, `.omc/docs/`). Where this
record says `wikirun.py`, the `docs` reconcile group or `.omc/docs/` stay, that
parent now removes them; sibling-repo explain reads only the sibling's graph and
`explain-context`. Verify was skipped at the user's request.

Rebased again onto `main` after PR #83 introduced project hooks
(`.omc/hooks/<name>/`, answered by `OMC_HOOK`). To keep the two apart, the
shipped text calls `gitnexus-ignore-context` a *context skill* (like
`explain-context`); where this record says "hook" for it, it means that skill.

- Important · `src/omc/internal.py` `_USAGE` lost the whole `gitnexus` entry in
  the rebase · **fixed** (entry restored without `[--git REF]`, presence asserted).
- Important · project rule "No slow tests" · six E2E ceilings above 300 s outside
  the `expensive` tier, with `pyproject.toml`'s policy comment rewritten to exempt
  them · **fixed** (the six tests are marked `expensive`; the comment is reverted).
- Important · grug:small-refactor · `pyproject.toml:5` Python 3.14 floor bundled
  into this branch · **waived** (Deliberate complexity).
- Minor (listed): `refresh_knowledge`'s `cfg` parameter is now unused
  (`src/omc/gitnexus.py`); silent return on an unsafe home in
  `remove_retired_dependency_cache` (`src/omc/installer.py`); duplicated awk
  ranking pipeline and git ≥ 2.38 `ls-files --format` in
  `skills/gitnexus-ignore/SKILL.md`; `REQUIRED_PYTHON` restates
  `requires-python`; `test_dependency_subsystem_is_gone` checks file absence
  only; two `real_embeddings` marks not needed.

## Deliberate complexity

- `grug:small-refactor` · `pyproject.toml` — the Python 3.14 floor rides in this
  branch because the user decided it here (§12: "require python 3.14 then") to
  unblock this branch's own check on the host.
