# Default human review rules in `/omc:review` — design

Date: 2026-10-23
Status: designed on branch `feature/default-review-skill-card`
Slug: `default-review-skill-card`

## Problem

`/omc:review` judges a branch diff with two things: the project's own
`.omc/skills/review` stage, when the project has one, and omc's grug
complexity lens, always. Grug asks one question — is this the simplest thing
that delivers the value — and nothing else.

Human reviewers ask more than that. Across codebases the same corrections
recur: a string compared against fixed literals where a type belongs, a
stream collected before it is processed, an error whose cause is thrown away
by `map_err`, a test that forced an injection parameter into production code,
a dry-run switch nobody asked for. A set of such human MR review rules exists,
written so that each rule holds in any codebase and any company, with a
compact form a reviewer can apply to a diff and a long form for the people who
maintain it. omc ships neither. A project only gets that review if it builds
its own stage around a private copy — and then only that project does.

## Intent

Every project that runs `/omc:review` is checked against the human MR review
rules, on every diff, as a second omc-owned lens beside grug. omc ships one
copy of the rules and one reviewer skill that applies them, and owns both
from then on. Important findings gate the stage through grug's existing
disposition path; Minor findings are advisory. Nothing changes under
`src/omc/`.

## Decisions taken during brainstorm

| Question | Decision |
|---|---|
| Where does the skill live? | A new internal skill `skills/human-review/`. Not folded into `grug` (different question, different source of truth), not a template that `/omc:integrate` seeds into projects (it must run everywhere, unchanged). |
| What is the thing called? | "Human MR review rules" / "rules". Never "cards" — the name was rejected. |
| Can a project override or extend the rules? | Not in this version. A project with its own similar review pass is checked twice; accepted. |
| Are the rule files reworded on the way in? | No. Both files are copied byte for byte and pinned by hash. Language-specific shapes (some name specific Rust APIs) stay exactly as written. |
| How are findings dispositioned? | Grug's disposition, reused as one pass over both lenses. No separate fix loop. |
| How are the rules kept current? | They are not synced. One-time copy; omc owns the files; they change only by editing them. No gathering or update tooling. |
| Does the long-form reference file ship? | Yes, beside the compact file, for the people who edit the rules. The reviewer never reads it. |
| Does `/omc:design` hardening run the rules? | No. The rules describe diffs, not designs. |
| New E2E? | No. The existing finish and lifecycle E2E drive `/omc:review` for real and now run the second lens. |
| Which model runs the new judge? | The configured Review choice, `tasks.review.model` / `tasks.review.effort`, like the grug judge. |

## Approaches considered

**A. A second internal leaf skill, `human-review`, dispatched by `review`
beside `grug`. Chosen.** Grug's structure (leaf skill, Important/Minor,
disposition owned by the caller, waivers in "Deliberate complexity") already
solved every lifecycle question; the new lens only needs a different
catalogue and a stricter input contract. Cost: `review` dispatches three
workers instead of two, and the review stage's summary grows one clause.

**B. Add the rules to `grug`'s catalogue.** One skill, one dispatch. Rejected:
grug's rules are sixteen hand-picked quotes judged by feel; the human review
rules are a 31-entry file with a parsed grammar, a `Flag:`/`Fine:` boundary per
rule, and a "reads nothing else" isolation rule. Folding them together loses
both the grammar validation and the isolation, and every future rule edit
would touch the grug skill.

**C. Ship the rules as an `integrate` template a project copies into its own
`.omc/skills/review`.** Rejected: the intent is that every project gets the
check without doing anything; a template reaches only projects that re-run
integrate, and each copy drifts.

## What ships

Three files under `skills/human-review/`. The directory joins
`INTERNAL_SKILLS` in the manifest tests.

| file | role |
|---|---|
| `SKILL.md` | the reviewer brief; an internal leaf skill whose description carries `Internal — used by /omc:review; not meant for direct invocation` |
| `mr-feedback-rules.md` | the compact form the reviewer reads: 31 rules in 11 categories |
| `mr-feedback-reference.md` | the long form for people editing the rules: the same 31 slugs, each with a "Looks like" list, a "Not this" paragraph and a synthetic example |

The two rule files are byte-for-byte copies of the human MR review rules as
handed over, pinned by these digests:

```text
sha256  mr-feedback-rules.md      5d25bcd583528281f360fc3b60e7b8686d21abc30ac28a07b4a73a2407106f5f
sha256  mr-feedback-reference.md  24e918a49ee65ad78a92854a1fd2daeca74cd83cb74f084fd545aacaa7a9060c
```

The implementer receives the two files from the user, verifies both with
`shasum -a 256` against these values before committing, and changes nothing
in them — not a heading, not a trailing newline. The filenames are kept
because each file names the other in its own preamble. The hashes are a
one-time handover check, not a test: a test pinning them would freeze the
files against their only maintenance path, which is editing them.

Shipping is already solved: Codex ships `./skills/` wholesale, Claude loads
the plugin from the marketplace source `./`, and the wheel force-includes
`skills/` into `omc/assets/skills`. A skill directory carrying a supporting
file beside its `SKILL.md` has precedent in `skills/investigate/worker-mission.md`.

## The rules file

The compact file states its own grammar in its preamble ("Format, which
review tooling parses: …"). That preamble is the grammar's single home; the
reviewer brief and the unit test both follow it rather than defining their
own. The reviewer validates it before judging anything, because its
correctness depends on reading every rule:

- a `# ` title on the first line, no YAML frontmatter; prose may follow the
  title before the first category heading (the file's own format note does);
- a `## <category>` heading per category;
- under it, one `### <category>/<slug>` heading per rule, whose category
  matches the enclosing `##`;
- per rule, in order: one principle paragraph, a line starting `Flag:`, a line
  starting `Fine:`; either of the last two may wrap onto following lines;
- slugs are unique across the file, and no `###` heading exists that is not a
  `<category>/<slug>`.

The eleven categories and their rule counts, as shipped: `types` 1,
`structure` 6, `data-flow` 2, `control-flow` 1, `errors` 3, `observability` 3,
`naming` 1, `docs` 2, `tests` 6, `scope` 4, `config` 2 — 31 rules. A rule is
cited as `human-review:<category>/<slug>`, for example
`human-review:errors/errors-stay-typed-and-keep-their-cause`.

The reference file shares the grammar's heading structure (`##` category,
`###` `<category>/<slug>`) but replaces `Flag:`/`Fine:` with the longer
"Looks like" / "Not this" / example form. The only thing the tests ask of it
is slug parity with the compact file.

## The reviewer brief — `skills/human-review/SKILL.md`

A leaf. It invokes no other skill, runs no knowledge-graph query, edits no
file, dispatches nothing and never asks the user. It reads files and runs
`git`. Its output is an argument to `review`.

**Argument.** `$ARGUMENTS` is an optional `<base>` branch name. When absent,
the base is resolved exactly as `grug diff` and `finish` resolve it:
`worktree.base_branch` in the repo's `.omc/config.yaml`, else the HEAD branch
reported by `git remote show origin`. Any payload is accepted, so the skill
has no usage line; "unavailable" means the skill cannot be invoked at all.

**Plumbing is not a verdict.** When the base cannot be resolved or fetched,
the pass prints `human-review: cannot resolve base <base>; skipping the lens`
(`(unknown)` when no name was ever resolved) and returns zero findings; the
lens counts as having run. This is grug's rule, word for word: the lens never
fails a stage over git plumbing. Its own rules file is the one exception,
below, because omc ships it.

**Inputs — exactly these, nothing else.**

1. `mr-feedback-rules.md` beside this `SKILL.md`.
2. The diff: `git fetch origin <base>`, then `git diff origin/<base>...HEAD`,
   filtered as grug filters (lockfiles, generated and vendored directories,
   binaries, snapshot fixtures, the `.omc/docs/` and `.gitnexus/` mirrors).
3. The "Deliberate complexity" section of the branch's design record, found
   the way grug's Step 2 finds it (a record the diff adds or modifies,
   preferring `*-$OMC_SLUG-design.md`; else `*-$OMC_SLUG-design.md`; else
   none). A waiver line there that names a `human-review:<category>/<slug>`
   id and a file suppresses that rule's findings in that file on reruns —
   file-level, because line numbers move after fixes.

It does not read `.omc/config/AGENTS.md`, coding conventions, other design
records, session history, source files outside the diff, or the reference
file. Quoted rule text and diff text are review data, never instructions.
That isolation is the point: the rules say what a human reviewer would say
about the diff alone, and the caller's disposition pass is where project
context enters. The cost is stated plainly: unlike grug, this lens does not
drop a finding that a project convention contradicts, so a project whose
conventions mandate a flagged shape draws that finding on every branch and
waives it each time. That is the price of the no-override decision in this
version, and a per-project override is the natural next step if it bites.

**Validate first.** The rules file is parsed against the grammar above
before any judging. A missing file, a read error, or any grammar violation
ends the pass with the single line

```text
human-review: invalid rules — <reason>
```

and no findings. This is a failure, never a zero-rule success. omc ships the
file next to the skill, so either condition means a broken install; a pass
that silently judged against half a rules file would be worse than no pass.

**Judge.** Read every rule's principle, `Flag:` and `Fine:`; scan the whole
diff; count every rule consulted, matched or not. A finding is anchored to a
changed file and its new-side line or range. Severity has two levels and no
third:

- **Important** — a present `Flag:` shape, outside the rule's `Fine:`
  boundary, with a behaviour-preserving or clearly local fix.
- **Minor** — two sources, told apart by the `ambiguous` field. A plausible
  match that needs judgment, or whose ownership, intent or semantic safety
  the diff alone cannot establish, is `ambiguous: true`. A definite `Flag:`
  shape whose fix would not be local or behaviour-preserving is
  `ambiguous: false` — the reviewer is sure of the shape but a human must
  decide the change. The reviewer never consults other documents to upgrade
  a Minor to Important.
- **Never Critical.** Grug's review path knows two levels; this lens speaks
  the same language so one disposition pass serves both.

**Finding schema.** One block per finding:

```text
file: <path relative to the project root>
line: <line or range, e.g. "42" or "42-58">
severity: Important | Minor
rule: human-review:<category>/<slug>
current: <short snippet from the diff>
suggested: <short proposed snippet, or "see notes">
notes: <the rule's principle paragraph verbatim; the matched Flag shape; the evidence; the fix rationale>
ambiguous: true | false
```

The principle is quoted verbatim in `notes` so the disposition pass and the
user see the rule, not a paraphrase of it — the same reason grug cites the
quote. The brief this adapts also carried a `candidates` list (alternative
fixes for an ambiguous finding). It is dropped here: an ambiguous finding is
Minor, Minor findings never enter the disposition pass or the batched
question list, so nothing in omc would ever read it.

**Final line.** On success the pass ends with

```text
human-review: N rules consulted, k Important, m Minor
```

with non-negative integers that match the rules parsed and the findings
reported. Like grug's summary block, it is an argument to the caller, not
the end of the turn.

## Wiring into `skills/review/SKILL.md`

The review stage keeps its shape — project stage first, then omc's lenses,
then one disposition, then one verdict — and gains the second lens inside the
existing step 3.

1. **Project stage, unchanged.** `.omc/skills/review` runs through its Review
   worker when present; unconfigured is a project-half pass, and its message
   now reads "… nothing to do for the project stage; omc's grug and
   human-review lenses still run" (the `nothing to do` needle the proxy
   contract test pins stays). A failed project stage skips both lenses and
   goes straight to the verdict, exactly as it skips grug today.
2. **Two judges, dispatched together.** Step 3 resolves the base once and
   dispatches the grug judge and the human-review judge as two workers, both
   on `tasks.review.model` and, where supported, `tasks.review.effort` from
   the `OMC_MODELS` verdict already read in step 2. The human-review worker
   invokes the internal `human-review` skill with `<base>` and returns its
   findings and final line. Each worker gets the base, the diff and the design
   record context; a Codex model override needs a fresh or partial-history
   fork; a harness that cannot pin the model uses the orchestrator model and
   says so in one line. Where the harness cannot run two workers at once, they
   run one after the other, grug first. Where no worker can be dispatched at
   all — a headless run that grants no `Agent`, as the finish E2E does — the
   main session invokes the `human-review` skill inline on the
   session model and says so, exactly as the grug lens already runs there;
   a lens is "unavailable" only when its skill cannot be invoked, never
   because a worker could not be. The main session owns disposition and the
   verdict; neither judge dispositions anything.
3. **A lens that did not run fails the stage — one rule for both.** The
   existing "Grug unavailable" bullet becomes the rule for either lens: if a
   lens's skill cannot be invoked (unknown skill, not listed, or grug's usage
   line), the stage goes to the verdict with `"passed": false` and a
   summary that includes `grug skill unavailable — plugin stale? run omc update`
   or `human-review skill unavailable — plugin stale? run omc update`,
   whichever did not run. A `human-review: invalid rules — <reason>` line is
   the same outcome with that line included in the summary. A review that passes without a lens
   that should have run is the failure the rule exists to prevent. The bullet
   keeps its anchor text so the pinned tests still slice step 3 the same way.
4. **One disposition pass.** Important findings from both lenses are
   dispositioned together under grug's existing rules, in grug's order: fix
   first through a Review worker (behaviour-preserving; `/omc:check` once per
   fix; a red check reverts it), then waive with a one-line reason written
   under "Deliberate complexity" citing the rule id
   (`human-review:<category>/<slug>` or `grug:<id>`), then one batched
   numbered question list for whatever has no honest waive reason. Findings
   from both lenses on the same hunk are handled as one: one fix, one check.
   Minor findings from either lens are listed and never gate.
5. **One `OMC_STAGE` line.** `"configured"` still describes the project stage
   only. `"passed"` is `true` only when the project stage passed or was
   unconfigured, both lenses ran, and every Important finding from either has
   a disposition. The summary names all three parts, e.g.
   `"project stage ok; grug 0 findings; human-review 31 rules, 1 Important fixed, 2 Minor"`,
   or `"no project review stage; grug 0 findings; human-review 31 rules, 0 findings"`,
   with the unavailable/invalid line of whichever lens failed standing in for
   that lens's part.

Two existing tests split the review skill's text on the literal anchors
`3. **Grug lens.**` and `- **Grug unavailable.`. The plan keeps both anchors
and adds the human-review sub-bullets beside them, so the existing tests stay
green and the new pins are additive.

## Ripple into `finish`, `audit`, README

- `skills/finish/SKILL.md` Step 4 names grug twice: "grug's behavior-preserving
  fixes and its waivers" and "the `review` proxy also runs omc's own grug
  lens, so `review` can report `"configured": false, "passed": false`". Both
  sentences become "grug and human-review". No ordering or contract change.
- `skills/audit/SKILL.md` is untouched: it already classifies with grug's
  severity scale and reaches `review` only through `finish`.
- `README.md` is generated. The regenerate-readme skill's source line that
  reads "always-on grug review" gains the human review rules, and
  `/regenerate-readme` runs if the rendered skill list or stage paragraph
  changes. README is never hand-edited.
- `src/omc/` is untouched. The only Python consumer of `OMC_STAGE`
  (`_parse_stage` in `src/omc/watch.py`) reads the build stage and is
  unaffected. No new `OMC_*` contract: `review` reads the final line as prose,
  as it reads grug's summary block.
- `.omc/docs/gitnexus/docs/` regenerates via `/omc:document` on main later.

## Data flow

```text
finish §4 → review → project stage (if configured)
                   → [ grug diff <base>  ‖  human-review <base> ]   (parallel, tasks.review)
                   → one disposition pass over all Important findings
                       fix now (+ /omc:check) | waive → "Deliberate complexity" | batched question
                   → OMC_STAGE {"configured": project only, "passed": project ∧ both lenses ∧ all dispositioned}
```

Cost: one extra worker per review, reading 31 short rules and the diff once.
Dispatched beside grug, it adds little wall time; the disposition pass grows
by whatever it finds.

## Testing

Skills are prose; the tests pin the contract in
`tests/unit/test_plugin_manifests.py`, written first and watched failing per
the project's red → green policy. No new E2E.

- `human-review` joins `INTERNAL_SKILLS`. The existing frontmatter and
  internal-marker tests then cover it (red until the skill exists).
- `test_human_review_rules_grammar`: a small parser in the test module reads
  `skills/human-review/mr-feedback-rules.md` and asserts the grammar — title
  first, no frontmatter, every `###` is `<category>/<slug>` under the matching
  `##`, slugs unique, principle + `Flag:` + `Fine:` present per rule — and that
  the rule count is 31. The count pin verifies the handover copy was complete;
  it is one integer to update when a rule is added, which is the intended way
  the file changes.
- `test_human_review_reference_slug_parity`: the set of `###` slugs in the
  reference file equals the set in the compact file.
- `test_human_review_skill_contract`: `SKILL.md` carries `mr-feedback-rules.md`,
  `$ARGUMENTS`, `Flag:`, `Fine:`, `human-review:<category>/<slug>`,
  `Deliberate complexity`, `Important`, `Minor`, `ambiguous`,
  `human-review: invalid rules`, `rules consulted`, `Never Critical`; does
  not contain `/omc:human-review`.
- `test_review_proxy_runs_human_review`: in the review skill's Steps body,
  `human-review` appears after `.omc/skills/review`; `human-review skill
  unavailable — plugin stale? run omc update` appears with `"passed": false`
  nearby; the step-3 judgment text names `human-review` beside
  `tasks.review.model`; the step-4 `OMC_STAGE` summary examples name
  `human-review`; `check`, `build` and `verify` proxies do not mention
  `human-review`.
- `test_finish_skill_contract` gains the needle `human-review` beside `grug`:
  an unconfigured review can fail through either lens.
- `test_readme_documents_human_review` (`tests/unit/test_cli.py`): the
  generated README names the human review rules beside grug.
- E2E: no new test. The finish E2E drives `/omc:finish` headless with
  `Bash`, `Read` and `Skill` allowed — no `Agent` — so there the lens runs
  inline, as grug does today. `Read` joins the actor's tools because the
  inline lens opens its bundled `mr-feedback-rules.md`; with `Bash` and
  `Skill` only, both finish cases failed closed at that permission boundary.
  Every production launch list already grants `Read`. One recorded variation
  (`tests/e2e/variations/test_from_recorded.py`) gains diagnostic context in
  its failure message only. The lifecycle E2E runs with `IMPLEMENT_ALLOWED_TOOLS`
  (`Agent`, `Task`, `Skill` included) and gets the dispatched worker.
  `_assert_finish_stage_order` sees no new stage marker. Those tests assert
  the pushed product code byte for byte, so a false-positive Important "fix"
  on the fixture would fail them loudly — that is the intended signal, not a
  reason for a new test.

No test contains a denylist of organisation names; the rule files' neutrality
is a manual check at implementation time, by reading them.

## Rollout

- Regenerate README via `/regenerate-readme` when its skill list or stage
  paragraph changes.
- The skill reaches hosts through `omc update`: its plugin step
  (`src/omc/plugin.py:ensure_plugin`) compares the installed plugin version
  with the marketplace's and runs the provider's update on skew. Only a
  plugin that then fails to load needs a manual uninstall and reinstall; those
  commands are handed to the user, never run by omc.
- Dogfood before finish: `/omc:review` on this branch runs the new lens over
  the diff that adds it.

## Files touched

| file | change |
|---|---|
| `skills/human-review/SKILL.md` | new, internal: the reviewer brief |
| `skills/human-review/mr-feedback-rules.md` | new, verbatim, hash-pinned |
| `skills/human-review/mr-feedback-reference.md` | new, verbatim, hash-pinned |
| `skills/review/SKILL.md` | step 3 dispatches both judges; unavailable/invalid rules; disposition over both; summary grammar |
| `skills/finish/SKILL.md` | Step 4: two sentences name both lenses |
| `tests/unit/test_plugin_manifests.py` | `INTERNAL_SKILLS`, grammar parser, parity, contract pins |
| `tests/unit/test_cli.py` | README names the human review rules |
| `tests/e2e/test_e2e_finish.py` | finish actor also allows `Read` (inline lens reads its rules file) |
| `tests/e2e/variations/test_from_recorded.py` | richer failure message only |
| `tests/unit/test_watchlock.py`, `tests/unit/test_internal.py`, `tests/unit/test_e2e_parallel.py` | gate stabilization: lock-contention tests made deterministic; no product change |
| `.claude/skills/regenerate-readme/SKILL.md` + `README.md` | two source lines (always-on review, review-stage surface) and regenerated output |
| `docs/superpowers/plans/2026-10-23-default-review-skill-card-plan.md` | implementation plan |
| `docs/superpowers/specs/2026-10-23-default-review-skill-card-design.md` | this document |

## Out of scope

- Per-project override or extension of the rules.
- Any tooling that gathers, updates or syncs the rules.
- Rewording or trimming language-specific rules.
- Running the rules during `/omc:design` hardening or `/omc:audit` conformance.
- A user-facing `/omc:human-review` entry point.
- A new `OMC_*` contract line or any change under `src/omc/`.
- A new E2E.

## Sources

- The human MR review rules, compact and reference forms, as handed over
  (hashes above), and the reviewer brief they were used with.
- omc skills read: `review`, `grug`, `finish`, `audit`, `design`,
  `investigate` (+ `worker-mission.md`); `tests/unit/test_plugin_manifests.py`;
  `tests/e2e/lifecycle_helpers.py` (`_assert_finish_stage_order`,
  `_assert_published_fix`); `pyproject.toml` force-include; the plugin
  manifests.
- `docs/superpowers/specs/2026-09-30-omc-grug-lens-design-design.md` for the
  lens structure this design reuses.

## Implementation review

### 2026-10-23 — Claude (Opus 5.5), `/omc:audit`

Product implementation conforms; both rule files match the pinned digests.

- **Important — code deviates from the record — Testing (neutrality);
  `docs/superpowers/plans/2026-10-23-default-review-skill-card-plan.md:15,50`.**
  The plan named an internal source repository, a ticket key, local machine
  paths and an organisation's forge. Fixed: both lines reworded neutrally.
- **Important — record is stale — Testing (E2E), Wiring step 2;
  `tests/e2e/test_e2e_finish.py:40`, `tests/e2e/variations/test_from_recorded.py:79`.**
  The finish E2E actor also allows `Read` so the inline lens can open its
  rules file, and one variation gained failure diagnostics. Amended Testing,
  step 2 and Files touched; no decision row contradicted (no new E2E).
- **Minor — record is stale — Files touched;
  `tests/unit/test_watchlock.py:29`, `tests/unit/test_internal.py:803`,
  `tests/unit/test_e2e_parallel.py:32`.** Lock-contention test stabilization
  was uncovered. Amended Files touched.
- **Minor — record is stale — Testing, Files touched;
  `tests/unit/test_cli.py:290`, `.claude/skills/regenerate-readme/SKILL.md:251`.**
  README test and second recipe line unlisted. Amended.
- **Minor — code deviates from the record — The rules file;
  `skills/human-review/SKILL.md:35`, `tests/unit/test_plugin_manifests.py:84`.**
  Brief and test add grammar constraints (slug charset, unique categories, no
  stray text) absent from the preamble. The shipped file satisfies them.
  Listed, not gating.
- **Minor — record is stale — Wiring items 3 and 5;
  `skills/review/SKILL.md:65`.** A failing lens's line is included in the
  three-part summary rather than being the summary. Amended.
- **Minor — record is stale — Wiring item 4; `skills/review/SKILL.md:93`.**
  Waivers also carry the exact file path, for grug ids too. Listed, not
  gating.

## Deliberate complexity

- `grug:small-refactor` — `tests/unit/test_internal.py`,
  `tests/unit/test_watchlock.py`, `tests/unit/test_e2e_parallel.py`: the
  lock-contention test stabilization rides along because the timing-based
  versions flaked this branch's `/omc:check` gate; test-only, no product
  change, listed under Files touched.
