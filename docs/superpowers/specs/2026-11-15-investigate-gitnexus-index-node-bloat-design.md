# GitNexus index hygiene: the review stage keeps `.gitnexusignore` covering committed generated code

Date: 2026-11-15 · Branch: `feature/investigate-gitnexus-index-node-bloat` · Slug: `investigate-gitnexus-index-node-bloat`

Predecessors: `2026-10-07-scope-wiki-generation-to-tracked-files-design.md`
(the index covers tracked files; its D3/D4 name `.gitnexusignore` as the
per-project tool for tracked vendored and generated code),
`2026-10-27-enable-embeddings-for-gitnexus-design.md` (§6: bare
`--embeddings`, GitNexus's 50,000-node cap kept).

## 1. Problem

A GitNexus refresh of the omc-managed repo `apuntalo.co` (GitNexus 1.6.12,
2,190 tracked files) produced 51,609 nodes and 117,171 edges and reported
"Embeddings skipped: 51,609 nodes exceeds the 50,000-node safety cap". The
user asked whether it was indexing `node_modules`.

It is not. The fork's walker enumerates `git ls-files` (spec 2026-10-07) and
`apuntalo.co` tracks no `node_modules` path. A read-only cypher pass over
that index shows where the nodes come from:

| Source | Nodes |
|---|---|
| 282 tracked files under `src/**/generated/` (objects-generator output, duplicated in the `domain/` and `store/` packages; Property 16,289, Method 14,177, Class 5,898) | 31,195 of about 47,500 symbol nodes |
| Markdown Section nodes | 4,600 |
| `test/` | about 2,100 |
| Everything else | about 9,600 |

Without the generated code the graph is about 20,000 nodes, well under the
cap. The repo's `.gitattributes` already marks
`src/**/generated/** linguist-generated`; GitNexus does not read it.

GitNexus has the tool for this: a repo-root `.gitnexusignore` in gitignore
syntax (`gitnexus/src/config/ignore-service.ts` in the pinned fork), and its
own out-of-memory hint says "Add large vendored/generated directories to
.gitnexusignore". Nobody maintains that file. No omc skill and nothing under
`src/omc/` mentions it. The behavior layer tells an agent that `/omc:explain`
answers from a GitNexus graph, but not that the repo's tracked files feed
that graph or that an ignore file controls what goes in. So committed
generated code lands in the graph, crowds `/omc:explain` answers with
machine-written symbols, and on a repo this size costs the embeddings.

## 2. Decisions taken during brainstorm

| # | Decision | Why |
|---|----------|-----|
| D1 | Agents maintain `.gitnexusignore`; omc code does not write it. The instruction lives in omc's review stage, `skills/review/SKILL.md`, and nowhere else | User seed: "You need to provide agent instructions so they automatically maintain that file for you for every project and add generated folders and output folders, etc." and "you need to make agents aware that gitnexus even exists and they should maintain this file, so just put a short instruction into the review task. That should be enough". `/omc:finish` runs the review stage on every project, configured or not. This moves past a non-goal of 2026-10-07 ("any omc-side … `.gitnexusignore` writing") on purpose: omc code still filters and writes nothing, but an omc skill now has the agent write the file |
| D2 | Not in the behavior layer (`src/omc/distribution/AGENTS.md`), not seeded by `/omc:integrate`, no hint in the refresh narration when the cap trips | User chose the review stage only |
| D3 | The step acts only when the branch diff adds or changes generated, build/output or vendored tracked files; the pattern it writes covers the directory, not the touched files | Accepted at its default. The review stage judges the branch diff, so it has no business auditing the whole tree on every finish; a directory pattern means the first branch that touches generated code in `apuntalo.co` covers all 282 files |
| D4 | The step never fails the stage; the `OMC_STAGE` line is unchanged in shape and the summary may name added patterns | Accepted at its default. Index upkeep is not a reason to block publication; machine contracts are sacred |
| D5 | Not a rule in `skills/human-review/mr-feedback-rules.md` | That lens may not read files outside the diff, so it cannot tell whether an existing pattern already covers a directory; its rules are human MR-review principles, not tool upkeep; the bundled rule count is guarded by tests |
| D6 | Out of scope, follow-ups only: what the cap counts (the fork counts every node, including File, Folder, Section, Community and Process), backfill of a partially embedded index (omc backfills only when `stats.embeddings == 0`; `apuntalo.co` still holds 41,267 preserved vectors), any fork change, a configurable cap | The user asked for agent instructions; these are separate defects with separate designs |

## 3. Goals and non-goals

Goal: an agent finishing a branch in any omc-managed repo knows GitNexus
indexes the repo and keeps committed generated, build/output and vendored
directories out of the graph through `.gitnexusignore`, without being asked.

Non-goals:

- Any change to `src/omc/` or to the GitNexus fork. `analyze_argv` keeps
  passing bare `--embeddings`.
- A sweep of existing repos. A repo gets its first pattern when a branch
  next touches such files, or when its owner writes the file by hand. That
  includes `apuntalo.co`: this change does not fix it on its own.
- Detection by heuristic in code. The agent judges what is generated; a
  `linguist-generated` line in `.gitattributes` is a hint, not a parser input.
- Everything in D6.

## 4. Design

One short step is added to `skills/review/SKILL.md`. The main session runs
it inline, with no worker, once the project half is a pass (configured and
passed, or not configured) and before the grug and human-review lenses are
dispatched. The existing step numbers and the `3. **Grug lens.**` heading
stay as they are: the skill refers to "step 3" and "step 4" throughout and
the review guards in `tests/unit/test_plugin_manifests.py` anchor on that
heading. Where exactly the paragraph sits between them is the plan's call,
and the plan may tighten the wording as long as the facts §5 guards hold.
Proposed wording:

> **GitNexus index hygiene.** omc indexes this repo with GitNexus, the code
> knowledge graph behind `/omc:explain`. Committed generated code,
> build/output and vendored files bloat it. If the branch diff adds or
> changes tracked files of that kind, make sure the repo-root
> `.gitnexusignore` (gitignore syntax) covers their *directory* (e.g.
> `src/**/generated/`; a `linguist-generated` line in `.gitattributes` is a
> strong hint), then `git add .gitnexusignore`. Hand-written source, tests
> and docs stay indexed. This never fails the stage; name any added patterns
> in the summary.

Why each part is there:

- **Inline, before the lenses.** It is one diff read and at most one small
  file edit, which does not justify a worker. It does not depend on what the
  lenses find, and placed before them it is not skipped by the paths that
  send an unavailable lens straight to the verdict. The lenses judge
  `origin/<base>...HEAD`, committed content only, so they do not see the
  staged edit; nothing in it needs their judgment.
- **Skipped when the project stage fails.** Step 2 already sends a failed
  project stage straight to the verdict. Hygiene is skipped with the lenses;
  `/omc:finish` stops on the failed stage and the next run does it.
- **Explicit `git add`.** `/omc:finish` amends what a stage changed with
  `git add -u && git commit --amend --no-edit` and leaves untracked files
  alone. A newly created `.gitnexusignore` is untracked, so without staging
  it would be dropped silently. Once staged it is in the index and the amend
  commits it. `skills/finish/SKILL.md` is not changed.
- **Never gates.** No finding, no disposition, no question to the user. The
  `OMC_STAGE` contract and the meaning of `"passed"` stay as they are; the
  summary sentence may name the added patterns.

The pattern reaches the index when the branch merges. The index lives in
the primary checkout on the base branch; the merge puts the index behind,
and the next refresh runs the incremental analyze there. The fork's scan is
filtered by the ignore service, and `storage/file-hash.ts:diffFileHashes`
treats a path that was indexed but is no longer scanned as deleted and drops
its rows. This chain was read from the fork's source, not run (§7).

## 5. Tests (red first, per `.omc/config/AGENTS.md`)

A skill-text guard in `tests/unit/test_plugin_manifests.py`, written first
and seen failing, asserts that `skills/review/SKILL.md`:

- names `.gitnexusignore` and `git add .gitnexusignore`;
- states the step never fails the stage;
- places the step after the project stage and before the grug lens.

Needle lists and `text.index` ordering are how this file already guards the
review skill (`test_review_proxy_runs_grug`,
`test_review_proxy_runs_human_review`); those guards keep passing unchanged.

No new E2E. The change is skill prose with no machine contract, and the
fixtures of `tests/e2e/test_e2e_finish.py` commit no generated code, so the
step is a no-op there. A Docker-plus-LLM test proving an agent writes the
file would cost more than an instruction that never gates is worth.

## 6. Documentation

None beyond the skill itself. The step is an agent instruction that never
gates; a user meets it as a `.gitnexusignore` hunk in the MR diff and as the
pattern named in the review summary.

`README.md` is generated and never hand-edited. Its review-stage paragraph
describes what gates publication, which does not change, and the README
guards (`tests/unit/test_cli.py`, the `readme` tests) do not read
`skills/review/SKILL.md`. They run in the check gate as always; only if one
fails is `/regenerate-readme` run.

## 7. Risks

- **An agent ignores too much.** Hand-written code under a directory named
  `generated` or `vendor` would vanish from `/omc:explain`. The wording says
  what stays indexed, the pattern is named in the review summary, and the
  file is committed and reviewed with the MR.
- **The graph goes blind to generated code, by design.** `/omc:explain` and
  `impact` no longer see generated classes or edges into them, and the wiki
  shares the index's scope (spec 2026-10-07 §3), so the docs stop describing
  them too. Accepted: that is the trade the user asked for; a project that
  wants a generated directory indexed removes the line or negates it.
- **Ignored directories linger in an existing index.** If the incremental
  analyze did not drop newly ignored files as §4 expects, the owner's
  existing remedy is `omc watch --once --reset-gitnexus` in the primary
  checkout. omc gains no code for this.
- **Standalone `/omc:review`.** Outside `/omc:finish` the staged file stays
  staged; the next finish's squash stages everything (`git add -A`) and
  folds it in.
- **Slow uptake.** A repo whose generated code never changes never gets a
  pattern from this step. Accepted under D3; the owner can write the file.

## Implementation review

2026-11-15 · auditor: Claude (opus, `/omc:audit`) — conforms, no findings.
The §4 facts are present in `skills/review/SKILL.md:43-53` and the routes
(Missing, passed, failed) behave as recorded. The final wording adds "so
finish's amend includes it" after `git add .gitnexusignore`, a tightening
§4 allows. D1–D6 hold; the §5 guard is
`tests/unit/test_plugin_manifests.py:test_review_proxy_keeps_gitnexusignore_covering_generated_code`,
and the existing guards are unchanged.

## Deliberate complexity

None.
