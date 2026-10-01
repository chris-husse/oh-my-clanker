# omc grug lens — design

Date: 2026-09-30
Status: implemented on branch `feature/omc-grug-lens-design`; dogfood waivers recorded below
Slug: `omc-grug-lens-design`

## Problem

Two gaps in the omc lifecycle.

`omc:spec` hardens each section of a design record only through `/omc:explain`,
which answers "does this fit the architecture that exists" and never "is this
more than the value needs". Over-engineering that is architecturally consistent
passes hardening untouched.

`omc:review` is a pure proxy to the project's `.omc/skills/review`. A project
without a review stage gets no review at all, and a project with one gets only
its own rules. omc has no opinion of its own about complexity at the point
where the diff is final.

## Intent

Extract the reviewer-relevant half of grugbrain.dev into a complexity lens and
apply it in both places from one catalogue: during spec hardening ("is this the
simplest thing that delivers the value") and during the review stage over the
branch diff. Findings are plain English that cite the grug quote as the rule.
Important findings must be dispositioned and gate the stage. Minor findings are
advisory. Project conventions and the spec's own recorded waivers suppress
noise.

## Decisions taken during brainstorm

| Question | Decision |
|---|---|
| Where does the lens hook in? | omc-level: the `review` proxy on every project, plus the `spec` hardener. |
| Weight of findings | Dispositioned, then gate. Important needs an explicit outcome; an undispositioned Important fails the review stage. Minor is advisory. |
| Voice | Plain-English finding; the grug quote is the cited rule. No grug-speak output mode. |
| Exposure | Internal skill. No `/omc:grug` entry point for users. Called only by `spec` and `review`. |
| Callers | Both `spec` and `review`. |
| Bare-text argument form | Dropped; it served the user-facing use that no longer exists. |
| Who waives during review | The session may waive, but the waiver must be written into the branch's design record so it ships in the commit. |
| Design doc name | Slug-derived, `2026-09-30-omc-grug-lens-design-design.md`, so `implement`'s resume check matches by slug. |
| `ticket-sync` manifest gap | Fixed in the same change: it joins `INTERNAL_SKILLS`. |

## Approaches considered

**A. One internal skill, `grug`, called from `spec` and `review`. Chosen.**
One catalogue, two callers, no drift. Cost: the review proxy is no longer a
pure pass-through, and spec hardening roughly doubles its per-section calls.

**B. Inline the lens into `spec` and `review`.** Two copies drift, or a
cross-skill file path that behaves differently under Claude and Codex.
Rejected.

**C. A complexity flag on `/omc:explain`.** Explain answers "what exists and
how it works" from the graph; complexity judgement is a different job with a
different output. Rejected.

## The lens

Sixteen rules, kept only where they judge a design or a diff. Each has a
stable id, the grug quote that becomes the cited rule, what it catches, and
where it applies (S = spec section, D = diff).

| id | grug says | catches | S/D |
|---|---|---|---|
| `grug:say-no` | "best weapon against complexity spirit demon is magic word: no" | a feature, option, or abstraction with no stated need in the spec | S |
| `grug:80-20` | "80 want with 20 code" | the full bells-and-whistles version where a smaller one delivers most of the value | S |
| `grug:factor-late` | "not factor your application too early" | a trait, layer, or generic mechanism introduced before it has two concrete users | S D |
| `grug:cut-point` | "good cut point has narrow interface with rest of system" | a proposed boundary whose interface is wide or leaks internals | S D |
| `grug:simple-repeat` | "repeat code sometimes often better than complex DRY solution" | a helper, closure, or generic introduced to fold two simple, obvious copies | D |
| `grug:locality` | "put code on the thing that do the thing" | one behaviour spread across many files so a small change touches N places | S D |
| `grug:debuggable` | "EASIER DEBUG!" | dense compound conditions or combinator chains that hide intermediate values | D |
| `grug:fence` | "if you don't see the use of it, I certainly won't let you clear it away" | removing code or behaviour without stating why it existed | S D |
| `grug:small-refactor` | "not be too far out from shore" | a refactor bundled with the feature, or a sweep where the system is broken between steps | S D |
| `grug:generics` | "grug try limit generics to container classes" | type-system gymnastics, generic params beyond containers, trait hierarchies with one impl; the Visitor pattern is the named example | D |
| `grug:test-level` | "integration test sweet spot", "first reproduce bug with regression test" | a test plan built on fine unit tests plus mocks; a bugfix without a regression test; new mocks | S D |
| `grug:evidence-perf` | "concrete, real world perf profile before begin optimizing" | caching, batching, or optimisation without a measurement behind it | S D |
| `grug:api-common-case` | "good apis not make grug think too much" | a new API where the common call needs ceremony, or the common op is not on the thing | S D |
| `grug:logging` | "log all major logical branches", "include request ID in all" | a new branch or failure path with no log line; logs missing the correlation id | D |
| `grug:simple-concurrency` | "grug, like all sane developer, fear concurrency" | new shared mutable state, locks, or background tasks where a stateless handler or queue would do | S D |
| `grug:too-complex-for-grug` | "hmmm, this too complex for grug" | the reviewer cannot follow a section or function after one honest read; no fix prescribed | S D |

Two clarifications carried into the skill text:

- `grug:too-complex-for-grug` prescribes no fix. Its `simpler:` line is always
  `none — needs a human answer`.
- `grug:test-level` never overrides a project's own testing policy. The
  precedence rule below handles that generally; this repo's "tests must run,
  never skip; stub ≠ tested" policy is the motivating case.

Dropped from grugbrain.dev because they do not judge a spec or a diff:
microservices, front end, agile, parsing, tools, type-system-for-autocomplete,
fads (kept only implicitly through `say-no` for a new dependency), FOLD as
career advice, impostor syndrome.

## Precedence — so grug is not noise

Before judging, grug reads, when present, in the project root:

- `.omc/config/AGENTS.md`
- `.omc/config/coding-convention.md`
- any convention document `.omc/config/AGENTS.md` points at

A finding that contradicts an explicit project convention is **dropped, not
reported**. A project whose conventions mandate newtypes, discriminant enums,
or a function-length ceiling therefore never draws a `grug:generics` or
`grug:locality` finding for following them.

For a diff, grug also reads the design record for the branch. Resolution
order:

1. A file under `docs/superpowers/specs/` that the diff itself adds or
   modifies. In the omc lifecycle the spec is committed on the branch, so this
   is the normal case. If the diff touches several, prefer the one matching
   `*-$OMC_SLUG-design.md`, else the first in path order.
2. Else `docs/superpowers/specs/*-$OMC_SLUG-design.md` when `OMC_SLUG` is set.
3. Else none. Nothing is suppressed and the summary says "no design record
   found".

Anything the record justifies in its **"Deliberate complexity"** section is
suppressed. The same applies to the `spec` and `section` payloads: for
`spec <path>` the record is the file itself, for `section` it is the record
the caller is hardening, so a waiver recorded in one hardening pass holds in
the next. A record that exists but has no such section (a hand-written spec
from before this change) counts as "nothing waived"; a waiver written later
replaces the `None.` placeholder or appends the section.

## Finding format

Plain English, the quote as the cited rule, the simpler alternative named when
there is one.

```
<§section | file:line> — <grug:id> — <one sentence: what, and why it costs>
  grug: "<quote>"
  simpler: <concrete alternative> | none — needs a human answer
```

**Severity, two levels.** This mirrors the scheme omc's own dogfood
`.omc/skills/review` already uses, where Important fails and Minor lists.

- **Important** — changes the shape of the design or diff: `factor-late`,
  `cut-point`, `fence`, `small-refactor`, `test-level`, `evidence-perf`,
  `simple-concurrency`, `too-complex-for-grug`, `locality`, `generics`,
  `api-common-case` on a public API, and `say-no` / `80-20` on a spec.
- **Minor** — local: `debuggable`, `logging`, `api-common-case` on internal
  functions, `simple-repeat`. A rule not named in either list is Minor.

## Disposition

Only Important findings need one.

**Spec hardening.** Three outcomes. Fold the simpler alternative into the
section when it keeps the converged design. A finding that contradicts a
decision recorded in the record's "Decisions taken during brainstorm" table
is waived into "Deliberate complexity" citing that decision; the brainstorm
already settled it, so it is not a CRITICAL question (this matches
`implement`'s definition of CRITICAL: an issue the brainstorm never settled).
Anything else becomes a numbered CRITICAL user question through the path
`omc:spec` Step 4 already has, and what the user waives lands in "Deliberate
complexity" with its reason.

**Review.** *Fix now*, or *waive* with a one-line reason.

- A fix changes tracked files. `finish` Step 4 already amends tracked-file
  changes made by a stage into the squashed commit, so no new mechanism is
  needed. After any fix, `/omc:check` runs once before the stage may pass.
- A fix is a behavior-preserving simplification only. Grug never changes what
  the code does, only how much of it there is. A diff with no Important
  finding leaves the tree untouched. The lifecycle E2E asserts the pushed
  product code byte-for-byte, so this is load-bearing, not a nicety.
- A waiver must be written into the branch's design record under "Deliberate
  complexity". That is itself a tracked-file change, amended into the commit
  and visible in the MR, so the session's self-waivers are auditable rather
  than lost with the transcript. When no design record exists on the branch,
  the waiver is recorded only in the `OMC_STAGE` summary.
- Any Important finding left without a disposition **fails the stage**. Minor
  findings are listed and never gate.

Full `/omc:verify` is not re-run after a review-stage fix. `check` is the quick
gate and `verify` belongs to milestones. This is a stated, accepted risk.

## Skill surface

New `skills/grug/SKILL.md`. Frontmatter description begins `Internal — used by
/omc:spec and /omc:review; not meant for direct invocation.` It joins
`INTERNAL_SKILLS` in the manifest tests.

`$ARGUMENTS` follows the `investigate` precedent of a positional first token:

| form | judges |
|---|---|
| `spec <path>` | a whole design record: cross-section findings such as total new surface or layer count |
| `section <text>` | one spec section, with the explain answer appended as context so grug can say "reuse the existing X" instead of guessing |
| `diff [<base>]` | the branch diff against `origin/<base>`; default base from `.omc/config.yaml` `worktree.base_branch`, else the remote HEAD branch, exactly as `finish` resolves it |

Anything else is a usage error reported in one line; there is no bare-text
form. When the base cannot be resolved at all, the one-line report says
`(unknown)` in place of the base name.

Grug itself writes nothing in either path. Under `spec` the caller folds or
records; under `review` the caller applies the fix or writes the waiver.

Diff filtering is spelled out in the skill: skip lockfiles, generated
directories, binaries, vendored code, snapshot fixtures, and the `.omc/docs`
and `.gitnexus` knowledge mirrors.

The skill invokes no other skill and runs no graph query: no `/omc:explain`,
no GitNexus. It reads files and runs `git diff`. That keeps it cheap relative
to explain, which matters because it runs once per spec section and once per
review inside the same session budget. It is a leaf, not a conductor, so it
carries no task-list or completion-contract boilerplate. It ends with a plain-text summary block: counts per severity and
the disposition of every Important finding. The block is an argument to the
caller, never an end of turn, and the skill says so.

**No new `OMC_*` machine contract.** The callers are skills, not tools; they
read the summary block. Nothing under `src/omc/` changes.

Grug runs inline in the caller's session, on the session model; nothing
dispatches it as a subagent.

Composition: the load-bearing rule from the plan/implement design says an
internal skill is invoked only by its designated owner. Grug has two designated
owners, `spec` and `review`, both named in its description. `ticket-sync`
already set that precedent with `start` and `finish`. Review invoking
`/omc:check` after a fix is a user-facing skill called as a black box, which is
the rule itself.

## Changes to `spec`

- **Step 1** template gains an unconditional final section, **"Deliberate
  complexity"**: every Important finding waived during hardening, with its
  reason. Written as "None." when nothing was waived, so its presence is
  guaranteed and review can rely on it.
- **Step 2** gains a second call per section: `grug section <text + explain's
  answer>`. Refine the section with both answers.
- **Step 3** gains `grug spec <path>` after the whole-spec explain pass.
- **Step 4** iterate loop includes grug: repeat until neither explain nor grug
  surfaces real issues.

## Changes to `review`

- After the project stage runs and **passes**, invoke `grug diff <base>`.
- Project stage **failed** → skip grug; the stage is already failing.
- Project stage **unconfigured** → grug still runs. The message becomes "no
  project `review` stage configured — nothing to do for the project stage; the
  omc grug lens still runs", keeping the `nothing to do` needle the proxy
  contract test asserts.
- `OMC_STAGE` keeps `"configured"` about the project stage only. `"passed"` is
  the conjunction of the project stage outcome and grug's disposition gate.
  `"summary"` names both and lists any waived finding by id, e.g. `"project
  stage ok; grug 1 Important fixed, 1 waived (grug:factor-late), 3 Minor"`.
- The `check`, `build`, and `verify` proxies are untouched.

The only Python consumer of `OMC_STAGE`, `_parse_stage` in `src/omc/watch.py`,
reads the build stage for `--auto-build` and is unaffected. `finish` reads the
line as prose; its amend-tracked-changes and stop-on-`passed: false` rules
cover grug's outcomes once the unconfigured-means-skipped wording is fixed (see
Ripple below).

## Ripple into `finish` and docs

- `skills/finish/SKILL.md` Step 4 changes two sentences. First: the review
  proxy also runs omc's grug lens, so a review failure may come from either,
  and grug's fixes and waivers are tracked-file changes amended like any
  other stage's. Second, a genuine defect hardening found: today Step 4 says an
  unconfigured stage is "skipped", but review can now report
  `"configured": false, "passed": false`. The rule becomes: `"passed": false`
  stops the flow regardless of `"configured"`. No ordering or contract change,
  so `test_finish_skill_contract` is unaffected.
- `README.md`'s Usage paragraph on stages gains one sentence: review also
  applies omc's complexity lens; its "no-ops when unconfigured" clause
  excepts `/omc:review`, whose grug half always runs.
- `.omc/docs/gitnexus/docs/` is never hand-edited; it regenerates via
  `/omc:document` on main later.
- `src/omc/distribution/AGENTS.md` and all Python are untouched.

## Data flow

```
finish §4 → review → project stage (if configured) → grug diff <base>
          → fix now (+ /omc:check) | waive (→ "Deliberate complexity") → OMC_STAGE
implement Ph1 → spec → per section: explain → grug section
                    → whole spec:  explain → grug spec
                    → fold or CRITICAL question → "Deliberate complexity" → commit
```

## Testing

Skills are prose. Tests live in `tests/unit/test_plugin_manifests.py` beside
the existing contract tests, written first and watched failing per the
project's red → green policy.

- Add `grug` and `ticket-sync` to `INTERNAL_SKILLS`. The existing frontmatter
  and internal-marking tests then cover both. For `grug` this is red → green
  (the file does not exist yet). For `ticket-sync` it is a coverage addition
  that is green immediately: its frontmatter already carries "Internal" and
  "not meant for direct invocation".
- `test_grug_skill_contract`: `skills/grug/SKILL.md` carries all sixteen rule
  ids, the three argument forms, the `grug:` and `simpler:` finding lines,
  `Deliberate complexity`, `Important`, `Minor`, `fix now`, `waive`,
  `.omc/config/AGENTS.md`, `$ARGUMENTS`, and does not contain `/omc:grug`.
- Extend `test_spec_skill_contract`: requires `grug section`, `grug spec`,
  `Deliberate complexity`, and that `grug section` appears after
  `/omc:explain` within Step 2 (anchored past the frontmatter, which also
  names explain).
- `test_review_proxy_runs_grug`: `skills/review/SKILL.md` contains `grug diff`
  after `.omc/skills/review` within the Steps body (same anchoring), and still
  satisfies the stage-proxy needles `"configured"`, `OMC_STAGE`,
  `.omc/skills/review`, and `nothing to do` (the last pins the project half
  only; the grug half always runs).
- `test_finish_skill_contract` and `_assert_finish_stage_order` (E2E) are
  unchanged: grug adds no stage markers, and a trailing `check` after a review
  fix is already tolerated (the assertion dedupes adjacent markers and searches
  for the ordered window). `test_finish_skill_contract` gains one needle for
  the new finish rule: `regardless of`.
- Existing E2E finish scenarios with an unconfigured review stage now also run
  grug over the diff. They run headless with `Bash` and `Skill` allowed, which
  is all grug needs. Added latency is one lightweight pass.

No new E2E. Dogfood before finish: `/omc:review` on the branch that adds the
lens, and `grug spec` on this design record.

## Files touched

| file | change |
|---|---|
| `skills/grug/SKILL.md` | new, internal |
| `skills/spec/SKILL.md` | steps 1–4, "Deliberate complexity" |
| `skills/review/SKILL.md` | grug pass after project stage, `OMC_STAGE` summary, wording |
| `skills/finish/SKILL.md` | Step 4: grug mention; `passed: false` stops regardless of `configured` |
| `tests/unit/test_plugin_manifests.py` | `INTERNAL_SKILLS`, contract tests |
| `README.md` | one sentence |
| `docs/superpowers/specs/2026-09-30-omc-grug-lens-design-design.md` | this document |

Codex needs nothing extra: it ships `./skills/`. Claude loads the skill from
the marketplace source `./`. The wheel force-includes `skills/`.

## Out of scope

- No grug pass over the plan phase of `implement`.
- No grug-speak output mode.
- No user-facing entry point.
- No new `OMC_*` contract line.
- No re-run of `verify` after a review-stage fix.

## Sources

- https://grugbrain.dev/ (fetched 2026-09-30)
- Sterling's `grug` SKILL.md v1.0.0, MIT, shared on Slack 2026-09-30
- omc skills read: `spec`, `review`, `explain`, `implement`, `plan`,
  `finish`, `investigate`; dogfood `.omc/skills/review`;
  `tests/unit/test_plugin_manifests.py`; `src/omc/watch.py` `_parse_stage`

## Deliberate complexity

Important grug findings waived, with reasons. Recorded by the dogfood pass on
2026-09-30 (`grug spec` over this record, `grug diff main` over the branch).

- `grug:say-no` @ §Precedence — waived: hummingbird keeps its conventions in
  `.omc/config/coding-convention.md` without `AGENTS.md` pointing at it; naming
  the file costs one line and is harmless where it does not exist.
- `grug:cut-point` @ §Changes to review — waived: a separate `grug` key in
  `OMC_STAGE` would change a sacred machine contract for one consumer; the
  finish wording fix ("passed: false regardless of configured") closes the
  leak the overloaded `passed` opened.
- `grug:locality` @ `skills/review/SKILL.md` step 3 — waived: the review
  executor needs to see the fix-now/waive gate without opening the grug skill,
  and the contract test pins those two verbs in review.
- `grug:small-refactor` @ `tests/unit/test_plugin_manifests.py`
  `INTERNAL_SKILLS` — waived: adding `ticket-sync` is one token with zero
  behavior change, recorded in the decisions table above.
