---
name: grug
description: Internal — used by /omc:spec and /omc:review; not meant for direct invocation. The grug complexity lens - judge a spec section, a whole design record, or the branch diff for complexity that does not pay for itself, cite the grugbrain.dev rule, name the simpler alternative.
---

# omc grug (internal)

The reviewer-relevant half of grugbrain.dev, as a lens. It answers one
question about a design or a diff: is this the simplest thing that delivers
the value? Findings are plain English; the grug quote is the cited rule.

This skill is a leaf. It invokes no other skill, runs no knowledge-graph
query, and never edits a file on its own initiative. It reads files and
runs `git`. Its output is an argument to the caller (`spec` or `review`).

## Arguments

```text
$ARGUMENTS
```

The first whitespace-delimited token selects the payload:

| form | judges |
|---|---|
| `spec <path>` | a whole design record: cross-section findings (total new surface, layer count, mechanisms with one user) |
| `section <text>` | one spec section; the caller appends the explain answer as context so the simpler alternative can name what already exists |
| `diff [<base>]` | the branch diff against `origin/<base>` |

Any other first token, or an empty payload, is a usage error. Print exactly
one line — `grug: usage is spec <path> | section <text> | diff [<base>]` — and
return to the caller. Judge nothing, write nothing.

**Base for `diff`.** When `<base>` is omitted, read `worktree.base_branch`
from the repo's `.omc/config.yaml`; when that is unreadable, use the HEAD
branch reported by `git remote show origin`. That is exactly how `finish`
resolves it. Run `git fetch origin <base>` first, then
`git diff origin/<base>...HEAD`. When the base cannot be resolved or fetched,
print one line — `grug: cannot resolve base <base>; skipping the lens`, with
`(unknown)` in place of `<base>` when no name was ever resolved — and
return with zero findings. The lens never fails a stage over its own plumbing.

**Diff filter.** Skip lockfiles (`*.lock`, `uv.lock`, `package-lock.json`,
`Cargo.lock`, `poetry.lock`), generated directories (`dist/`, `build/`,
`target/`, `node_modules/`, `__pycache__/`), binaries, vendored code
(`vendor/`, `third_party/`), snapshot fixtures (`__snapshots__/`,
`*.snap`, `tests/**/artifacts/`), and the knowledge mirrors `.omc/docs/` and
`.gitnexus/`.

## Step 1 — read the project's conventions first

In the project root (`git rev-parse --show-toplevel`, else the current
directory), read when present:

- `.omc/config/AGENTS.md`
- `.omc/config/coding-convention.md`
- any convention document `.omc/config/AGENTS.md` points at

These are the project's explicit rules. A finding that contradicts one of
them is **dropped, not reported**. A project that mandates newtypes,
discriminant enums, or a function-length ceiling never draws a
`grug:generics` or `grug:locality` finding for following its own rules.

## Step 2 — find the design record

For `diff`, resolve in this order and stop at the first hit:

1. A file under `docs/superpowers/specs/` that the diff itself adds or
   modifies. If the diff touches several, prefer the one matching
   `*-$OMC_SLUG-design.md`, else the first in path order.
2. `docs/superpowers/specs/*-$OMC_SLUG-design.md` when `OMC_SLUG` is set.
3. None. Say `no design record found` in the summary; nothing is suppressed.

For `spec <path>` the record is the file itself. For `section <text>` it is the
record the caller is hardening (the caller names it; when it does not, treat it
as no record).

Read the record's **"Deliberate complexity"** section. Every item there is a
justified waiver: a finding it covers is suppressed, whichever payload is being
judged. A record without that section, or one reading `None.`, counts as
"nothing waived"; a waiver written later replaces the `None.` placeholder or
appends the section (see Step 5).

## Step 3 — the lens

Sixteen rules. Each has a stable id, the quote that is the cited rule, what
it catches, and where it applies (S = spec section or whole spec, D = diff).

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

Two rules carry a caveat:

- `grug:too-complex-for-grug` prescribes no fix. Its `simpler:` line is
  always `none — needs a human answer`.
- `grug:test-level` never overrides the project's own testing policy (Step 1
  wins). A project that forbids skips and distrusts stubs has already chosen
  its test level.

Apply only the rules whose S/D column matches the payload: `section` and
`spec` use the S rules, `diff` uses the D rules.

## Step 4 — findings

One block per finding, plain English, the quote as the cited rule, the
simpler alternative named when there is one:

```text
<§section | file:line> — <grug:id> — <one sentence: what, and why it costs>
  grug: "<quote>"
  simpler: <concrete alternative> | none — needs a human answer
```

**Severity, two levels.**

- **Important** — changes the shape of the design or diff:
  `grug:factor-late`, `grug:cut-point`, `grug:fence`, `grug:small-refactor`,
  `grug:test-level`, `grug:evidence-perf`, `grug:simple-concurrency`,
  `grug:too-complex-for-grug`, `grug:locality`, `grug:generics`,
  `grug:api-common-case` on a public API, and `grug:say-no` / `grug:80-20`
  on a spec.
- **Minor** — local: `grug:debuggable`, `grug:logging`,
  `grug:api-common-case` on internal functions, `grug:simple-repeat`.
  A rule not named in either list is Minor.

Report Important findings first. Do not pad: a payload with nothing worth
saying yields zero findings and a one-line summary. Zero findings means the
working tree is left exactly as found.

## Step 5 — disposition (Important only)

Minor findings are listed and never gate. Every Important finding needs one
of these outcomes, and the caller decides which path applies:

**Under `spec` (`section` / `spec` payloads).** Return the findings; the
caller folds the simpler alternative into the section when it keeps the
converged design, waives it citing a recorded brainstorm decision it
contradicts, or raises it to the user as a numbered CRITICAL question.
Whatever the user waives, the caller records in the design record's
"Deliberate complexity" section with its reason. This skill writes nothing.

**Under `review` (`diff` payload).** Each Important finding is either:

- **fix now** — a behavior-preserving simplification: the code does the
  same thing with less of it. Never change what the code does. After any
  fix, the caller runs `/omc:check` once before the stage may pass.
- **waive** — one line of reason, written into the design record's
  "Deliberate complexity" section (append the section if the record lacks
  it). That edit is a tracked-file change and ships in the commit. When Step 2
  found no record, the waiver is recorded only in the summary.

In both paths the caller applies the fix or writes the waiver; this skill
writes nothing here either.

An Important finding left with neither disposition means the caller's stage
**fails**.

## Step 6 — summary block

End with a plain-text block, no code fence, starting with the literal line
`grug summary:`:

```text
grug summary: <payload> — <N> Important, <M> Minor
  design record: <path> | no design record found
  <grug:id> @ <where> — fixed | waived (<reason>) | UNDISPOSITIONED
  ...one line per Important finding...
```

This block is not the end of your turn. It is the answer the caller
(`spec` or `review`) asked for: return to the caller and continue its next
step.
