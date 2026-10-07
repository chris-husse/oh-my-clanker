# Scope GitNexus indexing to tracked files: the fork walker enumerates `git ls-files`, omc bumps the pin

**Date:** 2026-10-07
**Slug:** `scope-wiki-generation-to-tracked-files`
**Status:** design, hardened with `/omc:explain` and the grug lens
**Predecessors:** `2026-10-06-fix-watch-docs-grouping-huge-projects-design.md`
(the fork-PR-then-pin-bump sequence this record repeats),
`2026-10-07-fix-gitnexus-stale-index-clean-loop-design.md` (sets
`GITNEXUS_SHARED_STORE=off` in `child_env()`; the reset path this record's
migration relies on), `2026-07-17-omc-gitnexus-design.md` (the repo-local
snapshot model: index in the primary checkout's `.gitnexus/`, mirrored into
worktrees)

## 1. Problem

GitNexus indexes, and therefore documents, files that are not the project's
own. The wiki of hummingbird's primary checkout
(`/Users/chriphus/Projects/hummingbird-wt/.gitnexus/wiki/meta.json`,
generated 2026-10-06 from commit `eb4c0f3` with `claude-sonnet-5`) lists
18,504 `moduleFiles` entries over 9,272 unique paths. Against
`git ls-tree -r eb4c0f3` those 9,272 paths are:

| Paths | What they are | Should be in scope? |
|---|---|---|
| 2,679 | hummingbird's own tracked files | yes |
| 6,571 | the three git submodules under `git/` (`git/event-schemas`, `git/monitoring-config`, `git/ktls-rs`; all in `.gitmodules`, mode `160000` gitlinks in `git ls-files --stage`) | no |
| 22 | untracked files that happened to be on disk (`crates/compliance-ops-agent-evals/resources/datasets/forta/…` yaml, `scratchpad_trace_dump.txt`, `gitlab-ci-revert-hakari.patch`, `wiki-overview.md`) | no |
| 624 of the 2,679 | tracked third-party crates under `crates/vendored/` (656 tracked there) | yes, tracked is tracked (D3) |

Hummingbird has 2,983 tracked files and 10,451 with `--recurse-submodules`;
the submodules alone more than triple the wiki's input and the LLM grouping
and page generation pay for every one of them. Gitignored output (`target/`,
`kgrpc_modules/`, `.gitnexus/`, `.idea/`, `.secrets/`) was correctly
excluded, so `.gitignore` handling is not the defect. The 307 tracked files
absent from the meta are absent for legitimate reasons (`.snap`, `.zip`,
`.lock`, `.json` and images through GitNexus's `IGNORED_EXTENSIONS`;
dotfiles because the walker's glob runs with `dot: false`; files added after
`eb4c0f3`). The two-to-one ratio of entries to unique paths is by design:
`generator.ts:extractModuleFiles` writes a parent module as the union of its
children. It is out of scope here.

The index and the wiki share one scope (section 3), so every consumer of the
graph sees the same pollution: `/omc:explain` answers from submodule code
and scratch files, `impact` walks edges into vendored trees of other
repositories, and the docs mirror under `.omc/docs/gitnexus/docs` describes
modules the project does not own.

## 2. Goal and non-goals

Goal: a GitNexus index, and the wiki built from it, cover exactly the files
git tracks in the repository being analyzed, minus what the ignore rules
already remove. Nothing else changes: the ignore pipeline, the size cap, the
dotfile rule, the omc narration and freshness verdicts all stay as they are.

Non-goals:

- A switch, flag, config key or environment variable that widens scope back
  to the filesystem (D2). The only non-tracked behaviour is the fallback for
  a root that is not a git work tree.
- Dropping tracked vendored code (D3) or detecting tracked generated files
  (D4). `.gitnexusignore` is the per-project tool for both.
- An upstream GitNexus PR (D5).
- Reconciling an existing over-scoped wiki inside GitNexus (D6). The owner
  resets once with omc.
- Any omc-side scope filtering, `.gitnexusignore` writing, or a new
  `OMC_KNOWLEDGE` freshness reason (D1).

## 3. Root cause

Fork source at pin `24af4f60`, checked out at
`~/.omc/dependencies/gitnexus/gitnexus` (the same tree as
`~/.omc/dependencies/github.com/chris-husse/GitNexus/24af4f60…/gitnexus`).

**The walker never asks git.**
`src/core/ingestion/filesystem-walker.ts:walkRepositoryPaths` enumerates with
`glob('**/*', { cwd: repoPath, nodir: true, dot: false, ignore: ignoreFilter })`,
then runs a batched `fs.stat` loop (`READ_CONCURRENCY` = 32, `Promise.allSettled`,
so a failed stat is dropped silently; files over the size cap are skipped and
narrated), deduplicates `.d.ts` beside its implementation, and sorts. Four
callers depend on that enumeration: `pipeline-phases/scan.ts:scanPhase.execute`
(the index), `core/index-content-drift.ts:detectIndexContentDrift` (the
coverage set `status` compares against disk, documented as "the same
`walkRepositoryPaths` scan"), `languages/dart/package-config.ts` (Dart
package discovery), and, by copy rather than by call,
`core/group/extractors/include-extractor.ts:discoverIndexableFiles`, which
repeats the glob verbatim under a MAINTENANCE note that says the two "MUST
agree on which files are reachable" so `File:<rel>` UIDs in cross-links match
graph File nodes. The glob is there for a reason that still holds: GitNexus
accepts roots that are not git repositories at all (`analyze` on a plain
directory, `--skip-git`), and for those the filesystem is the only source of
paths. This record keeps that use as the fallback (D2) and takes the glob
away only where git can answer.

**The ignore filter is the only scope rule.**
`src/config/ignore-service.ts:createIgnoreFilter` returns a glob `Ignore`
object `{ ignored(p), childrenIgnored(p) }` combining root and nested
`.gitignore` (#2675), `.gitnexusignore` (negations win, #771), the global
excludes file, and the hardcoded `DEFAULT_IGNORE_LIST` / `IGNORED_FILES` /
`IGNORED_EXTENSIONS` / `ROOT_ARTIFACT_DIRECTORIES` through `shouldIgnorePath`.
Both methods read only `p.relative()`. `GITNEXUS_NO_GITIGNORE` and
`GITNEXUS_NO_GLOBAL_IGNORE` only widen scope. A submodule's working tree is
an ordinary directory to this filter: nothing in `.gitignore` names it, and
git itself never needed to, because git knows it is a gitlink. The same holds
for untracked files, which are by definition not ignored. `--skip-git` only
disables git-root discovery; `.gitnexusrc` (`cli/analyze-config.ts`) has no
path-scope key.

**Git is already a dependency of this layer.** `src/storage/git.ts` shells
out with `execFileSync`: `listWorkingTreeDirtyPaths` runs
`git ls-files -v -z --`, and `isWorkingTreePristine` runs
`git ls-files --stage -z --`, parses `<mode> <object> <stage>\t<path>` records
and recognises mode `160000` as a gitlink (`git.ts:131-158`). The helper this
record adds is a sibling of those two.

**Wiki scope is index scope.** `src/core/wiki/generator.ts:WikiGenerator.fullGeneration`
reads `graph-queries.ts:getAllFiles` (`MATCH (f:File) RETURN f.filePath`),
filters with `shouldIgnorePath`, groups with the LLM, and
`extractModuleFiles` writes `meta.json`. `incrementalUpdate` diffs
`git diff fromCommit..currentCommit` only, so a file that leaves the graph
while unchanged in git is invisible to it, and `fullGeneration` skips
existing pages unless `clearForRegrouping` ran. `gitnexus wiki -f` forces a
full regeneration.

**Why migration is free on the index side.** `src/core/run-analyze.ts`
(`key: 'runner-identity'`, line 2221) forces a full rebuild when
`existingMeta.runnerIdentity` differs from the current analyzer build, so any
new fork build rebuilds every existing index in full on its next analyze.
Incremental analyze hashes the scan result
(`run-analyze.ts:2955`, `computeFileHashes(repoPath, allFilePaths)`) and
diffs it against the stored hashes (`:3007`, `diffFileHashes`), so a path
that leaves the scan lands in `hashDiff.deleted` and is purged on later
incremental runs too. `gitnexus clean --force`
(`storage/shared-store-lifecycle.ts:removeCheckoutStorage`) removes the
whole `.gitnexus/` directory including `wiki/` when sharing is off, which omc
guarantees since the 2026-10-07 record. omc's
`omc watch --once --enable-documentation --reset-gitnexus`
(`src/omc/watch.py` → `gitnexus.py:refresh_knowledge` reset branch:
`_clear_mirror`, `_destroy` (`clean --force`), full analyze, wiki) therefore
rebuilds index, wiki and docs mirror from nothing.

## 4. Decisions taken during brainstorm

| # | Decision | Why |
|---|----------|-----|
| D1 | Fix the walker in the GitNexus fork. omc does not filter paths, does not write `.gitnexusignore` files, does not add a freshness reason. The root analyze runs in is the main repository (the primary checkout omc creates worktrees from), not a linked worktree; omc indexes there and mirrors into worktrees | The scope defect lives where enumeration lives. Any omc-side filter would have to be reproduced for the wiki, the drift check and the dependency indexer, and would still leave `gitnexus` run by hand over-scoped. User seed |
| D2 | Tracked-only is unconditional: no `GITNEXUS_SCAN_SCOPE` environment variable, no `.gitnexusrc` key, no CLI flag, no omc `GITNEXUS_ENV` entry. The one non-toggle behaviour: a root that is not inside a git work tree, or where `git ls-files` fails, has no checked-in files, so the walker keeps today's glob there and logs why once. That is a fallback for non-repositories, not an opt-out | User: "no default. you only ever index checked in files, period." A switch would need a default, documentation, a test matrix and an omc opinion; none of that buys anything a repository owner cannot get from `git add` |
| D3 | Tracked vendored code (`crates/vendored/`) stays in scope; projects drop it with `.gitnexusignore` | Tracked is the rule. A vendoring heuristic would guess at directory names and be wrong somewhere |
| D4 | No new heuristic for tracked generated files | Gitignored output is already excluded; `IGNORED_EXTENSIONS` and `.gitnexusignore` cover the rest |
| D5 | Fork only, no upstream PR | Upstream's walker is filesystem-first by design and carries flags omc does not want; the fork already diverges in the wiki generator |
| D6 | No wiki reconciliation in the fork. Existing over-scoped wikis are refreshed by the owner running `omc watch --once --enable-documentation --reset-gitnexus` once; new indexes are right from the first run because a new fork build forces a full index rebuild (runner-identity) | User: "leave it, I will run omc watch once with the clean flag. we can't regenerate all docs every time a file disappears." Teaching `incrementalUpdate` about graph membership would make every file removal a wiki event |
| D7 | Dotfile parity: paths with a dot-segment stay excluded exactly as glob `dot: false` excludes them today | The change only narrows scope; widening it to `.github/`, `.gitlab-ci.yml` and friends is a different discussion. Assumed during the brainstorm, not objected to |
| D8 | Submodule gitlinks (mode `160000`) are skipped, never recursed; untracked files are never scanned; staged-but-uncommitted new files are in `git ls-files` and therefore in scope | A brand-new file is invisible until `git add`, which matches omc's base-branch model: the index describes what the branch carries |

## 5. Design

### 5.1 Fork walker: the glob stays, its output is intersected with `git ls-files`

**One helper in `src/storage/git.ts`**, beside `isWorkingTreePristine`:
`listTrackedFiles(repoPath): Set<string> | null`. It runs
`execFileSync('git', ['ls-files', '--stage', '-z', '--'], { cwd: repoPath, windowsHide: true, ...gitPathListExec })`
exactly as the two existing calls do, splits on NUL, parses each
`<mode> <object> <stage>\t<path>` record, drops records whose mode is
`160000` (D8), and returns the remaining paths as a set of POSIX strings.
`ls-files` prints paths relative to its cwd, so running it from the index
root yields root-relative paths even when `--skip-git` chose a subdirectory
of a larger repository. Any failure (no `git` binary, not inside a work
tree, non-zero exit) returns `null`. An empty but successful listing is an
empty set: under D2 a repository with nothing added has nothing to index,
and the scope line below says so.

**The two glob calls stay exactly as they are.** `walkRepositoryPaths` and
`include-extractor.ts:discoverIndexableFiles` each keep their
`glob('**/*', { nodir: true, dot: false, ignore })`, so `.gitignore`
precedence (#2675), `.gitnexusignore` negation (#771), the hardcoded lists,
the dotfile rule (D7), `nodir` and glob's symlink behaviour all apply as
today, by construction rather than by re-implementation. Each site then
does one thing more: when `listTrackedFiles` returned a set, it keeps only
the glob results that are in the set; when it returned `null`, it keeps
them all (the non-repository fallback, D2). Submodule working trees and
untracked files are walked by the glob as they are today and dropped before
the stat loop, so the change removes work and adds none. The MAINTENANCE
note in `include-extractor.ts` gains one bullet for the intersection; its
invariant (cross-link `File:<rel>` UIDs correspond to graph File nodes)
holds because both sites apply the same set.

The stat/size loop that follows is unchanged, and so are `onPathsDiscovered`
(now called with the intersected list), `.d.ts` deduplication, sorting and
the large-file narration. A tracked path absent on disk is never produced
by the glob, so it needs no handling; a tracked symlink is whatever glob
makes of it today.

**One scope line per walk**, through the walker's existing narration path
(`warnLargeFileSkip`, which respects the analyze progress bar):

```
  Scope: 2676 of 10451 walked files are tracked (git ls-files); the rest were skipped
```

or, in the fallback,

```
  Scope: filesystem walk (git ls-files unavailable); all walked files kept
```

The fallback names no finer reason: the helper's `Set | null` contract and
`gitPathListExec`'s ignored stderr carry none, and "not a git work tree"
would be a false claim when the `git` binary is missing. Read-only callers
pass `quiet: true` already and stay silent.

`detectIndexContentDrift` and the Dart `package-config.ts` follow because
they call `walkRepositoryPaths`. The drift check already walks with
`quiet: true`; Dart package discovery now does too, so the scan phase's one
scope line is not repeated by the auxiliary walk (found by the fork's Dart
test, which counts log records). The drift check's
"recovered" rule (a recorded path missing from the scan but readable on disk
stays in the coverage set, `index-content-drift.ts:113-125`) means that
`gitnexus status` on a pre-change index does not report the scope change as
deletions; the runner-identity rebuild makes the question moot (5.4).

### 5.2 omc side: the pin, nothing else

Exactly two omc product files change (the test half is the two E2E edits
section 7 names, `tests/e2e/test_e2e_gitnexus.py` and
`tests/e2e/test_e2e_docs_artifact.py`):

- `docker/Dockerfile.e2e`: `ARG GITNEXUS_REF` moves from
  `24af4f6006f5ee05ebbeeb187bba4c6186d2d5ce` to the fork's merge commit for
  this change, and the comment block above it (lines 50-53) gains the
  tracked-only walker and this record's file name.
- This record.

Untouched, deliberately: `src/omc/toolctx.py` (`GITNEXUS_ENV` stays
`{"GITNEXUS_SHARED_STORE": "off"}`), `gitnexus.py:ANALYZE_ARGS`
(`analyze --skip-agents-md --skip-skills`), `providers/registry.py`
`DocsRun.wiki_args`, `snapshot_freshness` and its `OMC_KNOWLEDGE` reasons,
`mirror_dir`, `watch.py`. The host picks up the fork through `omc update`
(`gitnexus.py:update_gitnexus`: force-checkout of fork `origin/main`, rebuild),
as it did for the two previous fork changes. `ToolContext` stays the only
subprocess boundary; no new process, environment key or argv is introduced.

### 5.3 Dependency docs: same walker, nil effect

`src/omc/dependency.py` indexes a dependency with
`analyze --index-only --name <key>@<commit>` in a pinned checkout
(`git clone --no-checkout` + `checkout -b omc-pin <commit>`; submodules are
never initialised) and `run_document` reuses `wiki_args`. The new walker
applies there too. Practical effect: none. Every file in such a checkout is
tracked, and the gitlink directories are empty today, so the glob yields
nothing under them either. `test_e2e_dependency.py` is unaffected.

### 5.4 Rollout and migration

1. Fork PR against `chris-husse/GitNexus` main with 5.1 and its tests;
   vitest green, formatting green; merged.
2. omc PR with 5.2, pin set to that merge commit; the E2E suite builds the
   image at the new pin and the tests in section 7 run against it; merged.
3. Host: `omc update` fast-forwards and rebuilds `~/.omc/dependencies/gitnexus`.
4. Owner of each over-scoped project, once, in the primary checkout with the
   base branch checked out (`watch.py:run_watch` refuses `--reset-gitnexus`
   anywhere else, before any node call):
   `omc watch --once --enable-documentation --reset-gitnexus`. The reset
   branch of `gitnexus.py:refresh_knowledge` runs `_clear_mirror`, `_destroy`
   (`clean --force`), the ordinary `ANALYZE_ARGS` analyze (full, because no
   index exists), then `_run_wiki`, and `finish` mirrors the new wiki. For
   hummingbird the expected result is: no File node under `git/`, none for
   the 22 untracked paths, `moduleFiles` ⊆ `git ls-files`, about 2,680 unique
   paths in `wiki/meta.json`, and `.omc/docs/gitnexus/docs` mirrored from
   the new wiki.

Why the index does not need step 4: the next plain `omc watch` tick or
`/omc:index` after step 3 runs `analyze`, which sees a changed
`runnerIdentity` and rebuilds in full from the tracked list. Why the wiki
does need it (D6): `incrementalUpdate` cannot see files that left the graph,
and `fullGeneration` keeps existing pages, so only a reset (which removes
`.gitnexus/wiki` with the rest of the directory) produces a wiki whose
modules match the new graph. Projects whose wiki was never over-scoped (no
submodules, clean working trees at generation time) need nothing.

## 6. Error handling and edge cases

- **`git` missing, root not a work tree, `ls-files` non-zero.**
  `listTrackedFiles` returns `null`; every glob result is kept and the scope
  line says `git ls-files unavailable` once. `analyze` never fails because
  of scope.
- **Successful but empty listing** (a repository before its first `git add`,
  or `--skip-git` pointed at a subdirectory with no tracked files). The
  walker yields nothing and the scope line says `0 tracked files`. This is
  D2 applied, not a fallback case; the fix is `git add`.
- **Uninitialised submodule.** Its gitlink record is skipped like any other;
  whether `<dir>/.git` exists is irrelevant to scope (it matters only to
  `isWorkingTreePristine`, which is unchanged).
- **Tracked path missing on disk** (deleted without `git rm`, sparse
  checkout, skip-worktree). The glob never produces it; nothing to handle.
- **Tracked symlink to a directory.** glob 13 follows one level of symlinked
  directory under `**` and yields the files inside; git lists only the
  symlink. The files inside are not in the tracked set and are dropped. A
  narrowing, consistent with D2; today they are indexed under the symlink's
  path.
- **Paths with unusual bytes.** `-z` output is neither quoted nor escaped,
  so `core.quotepath` cannot alter it; the existing `gitPathListExec`
  encoding handling applies.
- **A nested repository inside the project** (a clone sitting in an
  untracked directory). git resolves the nearest work tree for the index
  root, so the project's `ls-files` does not list the nested clone's files;
  they are out of scope, as they should be.
- **The fallback is reachable from a repository only by breaking git.** A
  user cannot opt out by deleting `.git`'s index or renaming `git`; both
  break `analyze`'s other git calls first (`getCurrentCommit`, dirty-path
  capture).

## 7. Testing

Red before green in both repositories; every test runs or fails loudly, none
skips.

**Fork (vitest).**

- `test/unit/git.test.ts` or a sibling: `listTrackedFiles` on a fixture
  built with `git init` in a temp directory returns the regular files, omits
  a staged gitlink record, splits NUL-separated records with a tab in a path,
  and returns `null` for a non-repository directory and for a missing `git`
  (PATH emptied for the call).
- `test/integration/filesystem-walker.test.ts`, new `describe`: a real
  temporary git repository containing a committed file, an untracked file, a
  gitignored file, a tracked dotfile, a staged-but-uncommitted file, and a
  submodule added with `git -c protocol.file.allow=always submodule add` of
  a second temporary repository. Red assertion on today's walker: it yields
  the untracked file and the submodule's files. Green: it yields the
  committed file and the staged file only, and `discoverIndexableFiles`
  agrees. A second case asserts that a non-repository directory (today's
  `mkdtemp` fixtures, which is why every existing walker test stays green
  unchanged) yields the glob set.
- `test/unit/index-content-drift.test.ts`: the existing cases stay green;
  one new case shows an untracked file on disk is neither "added" nor part
  of the coverage set.
- `test/unit/filesystem-walker-order.test.ts` and
  `test/integration/ignore-and-skip-e2e.test.ts` stay green unchanged.

**omc (pytest, conventions from `.omc/config/AGENTS.md`: assert on artifacts,
Docker-per-test E2E, no skips).**

- `tests/e2e/test_e2e_gitnexus.py::test_index_then_explain_on_real_repo`
  gains, before its `/omc:index` step and beside the worktree it already
  adds: one untracked file written into `/repo`, and one submodule added to
  `/repo` from a `make_work_repo` repository with
  `git -c protocol.file.allow=always submodule add` (staged, not committed,
  so the test does not move `HEAD` that the existing `lastCommit` assertion
  compares). After indexing, `omc internal gitnexus cypher` asserts the
  graph holds no `File` node whose `filePath` is the untracked file or lies
  under the submodule directory, and, as the positive control, does hold a
  `File` node for `src/omc/gitnexus.py`. Red on the current pin: both
  negative assertions fail. No new container, no new LLM call; the suite's
  five-minute ceiling is kept.
- `tests/e2e/test_e2e_docs_artifact.py` (expensive tier, run only by user
  decision): after `/omc:document`, every path in the refreshed
  `wiki/meta.json` `moduleFiles` is in `git -C /repo ls-files`. This is a
  one-line artifact assertion on a run that already happens. Precondition,
  from D6: it holds only when `/omc:document` takes the full-generation
  path, because `incrementalUpdate` carries `moduleFiles` forward unchanged
  and never removes a path. The committed artifact's `fromCommit` is far
  enough behind `/repo` HEAD (more than five new files) that the run is
  full today; if the artifact is ever refreshed to within five new files of
  HEAD, the run goes incremental and this assertion must be restricted to
  full runs.
- Unit: nothing new. No unit test asserts the pin value
  (`tests/unit/test_e2e_provider_support.py` reads the Dockerfile for other
  ARGs and is unaffected by the hash).

Plan note: every task in the implementation plan carries a `Complexity:`
line (`simple | medium | high`). The helper, the two filter lines and the
pin bump are `simple`; the fixture repositories in both test suites are
`medium`.

## 8. Risks

- **A new file is invisible until staged** (D8). Narrated by the scope line
  only; an author who forgets `git add` sees the file missing from
  `/omc:explain` and from the wiki. Under omc this reaches only files left
  untracked in the primary checkout on the base branch (`watch.py:run_watch`
  indexes nowhere else; worktrees receive the snapshot through
  `mirror.py:SNAPSHOT_DIRS`), which is the hummingbird case itself.
  Accepted: it matches what the branch carries.
- **The glob still walks what git does not track.** Submodule trees and
  untracked directories are enumerated and then dropped, so a repository
  with a huge untracked, non-ignored tree pays the same walk it pays today.
  No regression, no improvement; an `ls-files`-first enumeration would be
  the optimisation if a measurement ever asks for it.
- **Fork delta across upstream rebases** (D5). The change is one helper in
  `git.ts` and one filter line at each of the two glob sites, all beside
  code the fork already carries.
- **One long wiki regeneration per reset project** (D6). The owner pays it
  once, knowingly, with the reset flag; omc's existing stall handling and
  progress narration apply.
- **Repositories whose tests ran on untracked fixtures.** Any project that
  indexed a scratch directory inside its work tree loses those files from
  the graph. That is the intended behaviour, and the scope line makes it
  visible on the first run.

## 9. Out of scope

- `extractModuleFiles` double-listing of parent modules (the 18,504 versus
  9,272 ratio).
- Vendored or generated-file heuristics (D3, D4).
- An upstream GitNexus PR (D5).
- Shared-store adoption in omc (Direction B from the 2026-10-07 record).
- An `OMC_KNOWLEDGE` reason for wiki scope, or any omc inspection of
  `meta.json` beyond `fromCommit` (D1).
- Widening the dotfile rule (D7).
- Wiki `incrementalUpdate` learning about graph membership (D6).

## 10. Hardening notes

The primary's GitNexus snapshot was one commit behind `origin/main` with no
wiki during hardening, so graph facts were checked against the working tree;
fork facts come from the fork checkout at `24af4f60` and from its dependency
graph (`github.com/chris-husse/GitNexus@24af4f60`, indexed under `~/.omc`).
Spectrum was unavailable for every pass (this repository's origin is GitHub,
not the Kraken GitLab).

**§1 (explain + grug).** Explain confirmed omc has no file-scoping code of
its own, that `read_wiki_meta` is consumed only by `snapshot_freshness`
(`fromCommit`) and `refresh_knowledge.finish` (presence), and that every
graph consumer (`internal.py:_gitnexus`, the mirror) reads the same
primary-rooted index. Grug: nothing.

**§2, §3 (explain + grug).** Explain confirmed the reset path
(`watch.py:run_watch` → `gitnexus.py:refresh_knowledge(reset=True)`:
`_clear_mirror`, `_destroy`, the ordinary analyze, `_run_wiki`, `finish`)
and that `--reset-gitnexus` is refused off the base branch; §5.4 step 4 now
says so. Grug raised one Important `grug:fence` finding: §3 removed the
filesystem walk as the scope source without saying what it was for. Fixed:
§3 states that the glob serves non-git roots and that D2's fallback keeps
that use.

**§4, §5.1 (explain + grug).** Explain, from the fork graph, confirmed the
walker's three callers and the one caller of `discoverIndexableFiles`, that
`computeFileHashes`/`diffFileHashes` are used only by
`runFullAnalysisInner` (so out-of-scope paths are purged on incremental
runs), that the ignore filter reads only `p.relative()`, and that glob 13
follows one level of symlinked directory, which git does not. Grug raised
one Important `grug:80-20` finding: the design re-implemented glob's pruning
(dot rule, ancestor `childrenIgnored`, `ignored`, `nodir`, symlink
following, a path shim, a memo) over the tracked list, which was also the
record's own parity risk. Fixed: the two glob calls stay as they are and
their output is intersected with the tracked set, so every rule applies by
construction; the exported enumeration function, the shim, the memo and the
parity risk are gone, and §6, §7 and §8 were updated to match.

**§5.2–§5.4 (explain + grug).** Explain confirmed `docker/Dockerfile.e2e`
is the only pin, that the `ARG` precedes the bake `RUN` (layer rebuilds),
that `update_gitnexus` rebuilds whenever `HEAD != origin/main`, that
`dependency.py` never touches submodules, and that no test or manifest
asserts the hash. Grug: nothing.

**§6, §7 (explain + grug).** Explain confirmed the `container` fixture is
per test (no leak into the other `/repo` tests), the image's git (2.39,
bookworm) needs `protocol.file.allow=always` for a local submodule, the
cypher proxy prints parseable JSON (`row_count`), and a dirty `/repo`
changes none of the existing assertions. Grug: nothing; the project's
testing policy fixes the test level.

**§8, §9 (explain + grug).** Explain bounded risk 1 to untracked files in
the primary on the base branch (omc indexes nowhere else); folded into §8.
Grug: nothing.

## Implementation review

**Auditor:** claude (Fable 5.1 orchestrator; Review worker on `fable`, fix
workers on `opus`/`fable` per `OMC_MODELS`), 2026-10-07.

Branch under review: two omc commits over `origin/main` (this record, the
two §7 E2E edits) plus the fork branch
`chris-husse/GitNexus` `feature/scope-wiki-generation-to-tracked-files`
(PR #8). Findings and dispositions:

1. **Important, code deviates from the record.** §5.2, §5.4 step 2;
   `docker/Dockerfile.e2e:54`. `GITNEXUS_REF` still at `24af4f60` because
   §5.4 step 1 (fork PR merged) had not happened; at that pin the new
   `test_index_then_explain_on_real_repo` assertion is red by design.
   Disposition: fork PR #8 opened from the implementation branch; its CI
   exposed five test regressions, all fixed in the fork as conformance
   repairs (below); pin bumped to the PR's merge commit after merge, comment
   block extended. `/omc:verify` rebuilt the image at the new pin.
2. **Minor, record stale.** §5.1 scope line, §6 first bullet; fork
   `src/core/ingestion/filesystem-walker.ts:133-139`. Fallback text is
   `Scope: filesystem walk (git ls-files unavailable); all walked files kept`
   and counts carry no thousands separators. Disposition: record amended.
3. **Minor, record stale.** §5.2 said "exactly two omc files" while §7
   requires the two E2E edits. Disposition: §5.2 amended to "two product
   files" and names the tests.
4. **Minor, record stale.** §7 docs-artifact bullet; `moduleFiles ⊆ ls-files`
   holds only on a full wiki generation (D6: `incrementalUpdate` never
   removes a path). Disposition: precondition written into §7; the committed
   artifact's `fromCommit` is far enough behind for the run to be full.
5. **Minor, housekeeping.** The implementation plan was untracked in the
   worktree. Disposition: committed with the audit.
6. **Minor, validation gap.** Fork `npm test` never completed locally; CI
   was the arbiter and found what the focused runs missed:
   - `test/unit/ignore-service.test.ts` mocked `storage/git` without the
     new export (8 failures): mock gained `listTrackedFiles: () => null`.
   - `test/unit/dart-package-imports.test.ts` counted the fallback scope
     line as an extra log record: Dart package discovery now walks with
     `quiet: true` (§5.1 amended; the scan phase owns the one scope line).
   - `test/integration/cli-e2e.test.ts` `analyze --watch` and
     `test/integration/shared-store-seed.test.ts` R9 wrote new files without
     `git add` and expected them indexed: D8 applied, the tests stage them.
   - `include-extractor.ts:discoverIndexableFiles` never normalized
     backslashes, so on Windows the intersection with git's POSIX paths was
     empty: normalization added before the intersection (the walker already
     had it).
   - `run-analyze-fts-crash-marker` (identity-guard race on the coverage
     shard) and `manifest-synthetic-impact-lbug` (`truncated` budget) failed
     once in CI, pass locally, touch no walker code: unrelated.
   Fork CI baseline (PR #7, the previous pin) already failed `quality /
   format` (two web files), `quality / typecheck` (eight errors in two
   untouched tests) and `review` (dependency graph disabled on the repo);
   those remain and are not this change's.

## Deliberate complexity

None.
