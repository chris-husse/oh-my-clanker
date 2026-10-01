# omc grug lens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an internal `grug` skill, a complexity lens distilled from grugbrain.dev, and wire it into `spec` hardening and the `review` stage proxy, with contract tests pinning every load-bearing phrase.

**Architecture:** omc skills are prose (`skills/<name>/SKILL.md`) that ship automatically to Claude (marketplace source `./`), Codex (`./skills/`), and the wheel (force-include). Behavior is pinned by substring and ordering "needle" tests in `tests/unit/test_plugin_manifests.py`. This change adds one new prose file, edits three existing skills and the README, and adds or extends five tests. No Python changes. No new `OMC_*` machine contract.

**Tech Stack:** Markdown skills, pytest (`just check`), git.

**Spec:** `docs/superpowers/specs/2026-09-30-omc-grug-lens-design-design.md`

## Global Constraints

- Red → green for every change: write the test, run `just check`, watch it fail for the expected reason, implement until green, commit test + implementation together. Never `pytest.skip`.
- `just check` is the only test command (`uv run pytest -m "not e2e and not local_iterm2" -q`). Do not run E2E.
- No file under `src/omc/` changes. `src/omc/distribution/AGENTS.md` and `.omc/docs/` are untouched.
- No new `OMC_*` machine contract line. `OMC_STAGE` keeps its four keys: `stage`, `configured`, `passed`, `summary`.
- The string `/omc:grug` must not appear anywhere in `skills/grug/SKILL.md` (the skill is internal).
- The substring `nothing to do` must remain in `skills/review/SKILL.md` (existing proxy contract test).
- Never run `omc install` or `uv tool install` from this worktree.
- Ruff excludes `*.md`, so skill prose has no lint constraints; the test file must pass `uvx ruff format --check` and `uvx ruff check` (line length 100).
- Model tiers: every task carries a `Model:` line. Tier names only, never model ids. The cheap/fast tier is never used.

## Review Focus

Failure modes the spec implies that no unit test can exercise (skills are prose, executed by an LLM). Each line names the input and the expected behavior; the owning task's prose must state the rule explicitly so a reviewer can check it.

1. **Trivial diff, no findings** → grug leaves the working tree untouched and reports zero findings. The lifecycle E2E asserts pushed product code byte-for-byte. Owner: Task 1 (skill text states it).
2. **Review unconfigured but grug finds an undispositioned Important** → `OMC_STAGE` carries `"configured": false, "passed": false` and finish stops before push. Owner: Task 3 (review emits it) and Task 4 (finish honors `passed: false` regardless of `configured`).
3. **Branch with a hand-written spec lacking "Deliberate complexity"** → treated as nothing waived; a review waiver appends the section. Owner: Task 1.
4. **`diff` with no `origin/<base>` fetched or no remote** → grug reports one line ("cannot resolve base") and the review stage does not fail because of grug. Owner: Task 1.
5. **Unknown first token in `$ARGUMENTS`** → one usage line, no judging, no file writes. Owner: Task 1.

---

### Task 1: The `grug` skill and its contract test

**Model:** heavy coding tier

**Files:**
- Create: `skills/grug/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (the `INTERNAL_SKILLS` tuple near line 76, and a new test after `test_spec_skill_contract`)

**Interfaces:**
- Consumes: nothing.
- Produces: the internal skill name `grug`, invoked by later tasks as `grug section <text>`, `grug spec <path>`, `grug diff [<base>]`. Its output ends in a plain-text block that starts with the line `grug summary:`.

- [ ] **Step 1: Add `grug` and `ticket-sync` to `INTERNAL_SKILLS`**

In `tests/unit/test_plugin_manifests.py`, change the tuple to:

```python
INTERNAL_SKILLS = (
    "create-mr",
    "get-mr-description",
    "squash",
    "spec",
    "gitnexus-ensure",
    "gitnexus-index",
    "gitnexus-document",
    "gitnexus-explain",
    "ticket-sync",
    "grug",
)
```

`ticket-sync` is a coverage addition (its frontmatter already carries "Internal" and "not meant for direct invocation"). `grug` is red until the file exists.

- [ ] **Step 2: Write the failing contract test**

Append to `tests/unit/test_plugin_manifests.py`, directly after `test_spec_skill_contract`:

```python
GRUG_RULE_IDS = (
    "grug:say-no",
    "grug:80-20",
    "grug:factor-late",
    "grug:cut-point",
    "grug:simple-repeat",
    "grug:locality",
    "grug:debuggable",
    "grug:fence",
    "grug:small-refactor",
    "grug:generics",
    "grug:test-level",
    "grug:evidence-perf",
    "grug:api-common-case",
    "grug:logging",
    "grug:simple-concurrency",
    "grug:too-complex-for-grug",
)


def test_grug_skill_contract():
    text = (ROOT / "skills" / "grug" / "SKILL.md").read_text()
    for rule in GRUG_RULE_IDS:
        assert rule in text, f"grug skill missing rule {rule!r}"
    for needle in (
        "$ARGUMENTS",
        "spec <path>",
        "section <text>",
        "diff [<base>]",
        'grug: "',
        "simpler:",
        "none — needs a human answer",
        "Deliberate complexity",
        "Important",
        "Minor",
        "fix now",
        "waive",
        ".omc/config/AGENTS.md",
        ".omc/config/coding-convention.md",
        "worktree.base_branch",
        "no design record found",
        "behavior-preserving",
        "grug summary:",
        "not the end of your turn",
        "A rule not named in either list is Minor",
    ):
        assert needle in text, f"grug skill missing {needle!r}"
    # internal leaf: no user entry point, no graph, no explain
    assert "/omc:grug" not in text
    assert "omc internal gitnexus" not in text
    assert "/omc:explain" not in text
    # the two owners are named in the frontmatter description
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert "/omc:spec" in m.group(1) and "/omc:review" in m.group(1)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `just check`
Expected: `test_skills_have_frontmatter`, `test_internal_skills_marked_internal`, and `test_grug_skill_contract` FAIL with `FileNotFoundError: .../skills/grug/SKILL.md`. Every other test passes.

- [ ] **Step 4: Create `skills/grug/SKILL.md`**

Write this file exactly:

````markdown
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

## Step 2 — for `diff`, find the branch's design record

Resolve in this order and stop at the first hit:

1. A file under `docs/superpowers/specs/` that the diff itself adds or
   modifies. If the diff touches several, prefer the one matching
   `*-$OMC_SLUG-design.md`, else the first in path order.
2. `docs/superpowers/specs/*-$OMC_SLUG-design.md` when `OMC_SLUG` is set.
3. None. Say `no design record found` in the summary; nothing is suppressed.

Read the record's **"Deliberate complexity"** section. Every item there is a
justified waiver: a finding it covers is suppressed. A record without that
section counts as "nothing waived"; a waiver written during review appends the
section (see Step 5).

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
converged design, or raises it to the user as a numbered CRITICAL question.
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
````

- [ ] **Step 5: Run the tests to verify they pass**

Run: `just check`
Expected: all tests pass, including `test_grug_skill_contract`, `test_skills_have_frontmatter`, `test_internal_skills_marked_internal`.

- [ ] **Step 6: Lint the test file**

Run: `uvx ruff format --check tests/unit/test_plugin_manifests.py && uvx ruff check tests/unit/test_plugin_manifests.py`
Expected: no output, exit 0. If format fails, run `uvx ruff format tests/unit/test_plugin_manifests.py` and re-run `just check`.

- [ ] **Step 7: Commit**

```bash
git add skills/grug/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat(skills): add the internal grug complexity lens

Sixteen grugbrain.dev rules with stable ids, Important/Minor severity,
positional spec|section|diff payloads, project-convention precedence,
design-record waiver suppression. Contract test pins every rule id and
disposition word; ticket-sync joins INTERNAL_SKILLS for coverage.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Wire grug into `spec` hardening

**Model:** standard coding tier

**Files:**
- Modify: `skills/spec/SKILL.md` (Steps 1–4)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_spec_skill_contract`)

**Interfaces:**
- Consumes: `grug section <text>` and `grug spec <path>` from Task 1; the `grug summary:` block.
- Produces: every design record written by `spec` ends with a `## Deliberate complexity` section (content `None.` when nothing was waived). Task 1's Step 2 and Task 3 rely on that section existing.

- [ ] **Step 1: Extend the failing contract test**

Replace `test_spec_skill_contract` in `tests/unit/test_plugin_manifests.py` with:

```python
def test_spec_skill_contract():
    text = (ROOT / "skills" / "spec" / "SKILL.md").read_text()
    for needle in (
        "/omc:explain",
        "EACH section",
        "whole-spec",
        "architectural",
        "follow-up",
        "review",
        "grug section",
        "grug spec",
        "Deliberate complexity",
        "None.",
    ):
        assert needle in text, f"spec skill missing {needle!r}"
    # spec-phase emphasis is architecture; implementation choices are plan-phase
    assert "plan phase" in text
    # grug judges each section AFTER explain has answered, with that answer as context
    # (anchor on Step 2: the frontmatter already mentions /omc:explain)
    step2 = text.index("## Step 2")
    assert text.index("/omc:explain", step2) < text.index("grug section", step2)
    # the waiver section is unconditional so review can rely on it
    assert text.index("Deliberate complexity") < text.index("## Step 2")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `just check`
Expected: `test_spec_skill_contract` FAILS with `spec skill missing 'grug section'`. Everything else passes.

- [ ] **Step 3: Rewrite `skills/spec/SKILL.md` Steps 1–4**

Replace the body from `## Step 1 — write` through the end of `## Step 4 — iterate` with:

````markdown
## Step 1 — write

Write the design doc per repo conventions:
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` (topic = `$OMC_SLUG`
when set, else a short feature slug).

Every design doc ends with an unconditional final section:

```markdown
## Deliberate complexity

None.
```

It lists every Important grug finding the user waived during hardening,
each with its one-line reason. It reads `None.` when nothing was waived. Its
presence is guaranteed so that `review` can rely on it later.

## Step 2 — per-section hardening

For EACH section of the spec, two calls:

1. Invoke `/omc:explain` with:

   > Does this proposed change make architectural sense in this codebase:
   > <section summary>? What existing components does it touch, and what
   > problems might occur?

2. Invoke the internal `grug` skill with `grug section <section text,
   followed by explain's answer as context>`. With explain's answer in hand,
   grug can say "reuse the existing X" instead of guessing.

Refine the section with both answers. Emphasis here is architecture,
purpose, general function, and whether each mechanism pays for itself —
implementation-level choices (enums, parameters, reuse) belong to the
plan phase, not here.

## Step 3 — whole-spec pass

Run `/omc:explain` once more over the complete spec: does it cohere at a
high level, and does anything conflict with how the codebase already works?
Then invoke `grug spec <path>` over the committed-to-be file for
cross-section findings: total new surface, layer count, mechanisms with a
single user.

## Step 4 — iterate

Repeat steps 2–3 until neither explain nor grug surfaces real issues. Every
Important grug finding is dispositioned: fold the simpler alternative into
the section when it keeps the converged design; otherwise surface it to the
user as an explicit numbered CRITICAL follow-up question — never make silent
choices on their behalf. What the user waives goes into "Deliberate complexity" with
its reason.
````

Leave Step 5 unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just check`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format --check tests/unit/test_plugin_manifests.py && uvx ruff check tests/unit/test_plugin_manifests.py
git add skills/spec/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat(spec): harden each section and the whole spec with the grug lens

Per-section grug pass after explain, whole-spec grug pass, and an
unconditional \"Deliberate complexity\" section that records waivers.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Run grug from the `review` stage proxy

**Model:** standard coding tier

**Files:**
- Modify: `skills/review/SKILL.md`
- Modify: `tests/unit/test_plugin_manifests.py` (new test after `test_stage_proxy_contract`)

**Interfaces:**
- Consumes: `grug diff <base>` from Task 1; `/omc:check` (existing proxy).
- Produces: `OMC_STAGE {"stage": "review", "configured": <project stage present>, "passed": <project stage ok AND every Important grug finding dispositioned>, "summary": "<project outcome>; grug <N> Important fixed, <W> waived (<ids>), <M> Minor"}`. Task 4 relies on `configured: false, passed: false` being a possible combination.

- [ ] **Step 1: Write the failing test**

Add directly after `test_stage_proxy_contract`:

```python
def test_review_proxy_runs_grug():
    text = (ROOT / "skills" / "review" / "SKILL.md").read_text()
    for needle in (
        "grug diff",
        "fix now",
        "waive",
        "/omc:check",
        "Deliberate complexity",
        'regardless of `"configured"`',
    ):
        assert needle in text, f"review proxy missing {needle!r}"
    # grug runs after the project stage; the proxy contract needles still hold
    assert text.index(".omc/skills/review") < text.index("grug diff")
    assert "nothing to do" in text and '"configured"' in text and "OMC_STAGE" in text
    # the other three proxies stay pure pass-throughs
    for stage in ("check", "build", "verify"):
        assert "grug" not in (ROOT / "skills" / stage / "SKILL.md").read_text()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `just check`
Expected: `test_review_proxy_runs_grug` FAILS with `review proxy missing 'grug diff'`.

- [ ] **Step 3: Rewrite `skills/review/SKILL.md`**

Replace the whole file with:

````markdown
---
name: review
description: Run the project's review stage if one is configured (.omc/skills/review in the repo omc is invoked from), then omc's own grug complexity lens over the branch diff. The project defines what "review" means; grug adds "is this the simplest thing that delivers the value".
---

# omc review (project-stage proxy + grug lens)

Two halves. First the PROJECT's review stage, if the project defines one.
Then omc's own complexity lens, the internal `grug` skill, over the branch
diff — on every project, configured or not.

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory.
2. Look for `<project-root>/.omc/skills/review/SKILL.md`.
   - **Missing** → report "no project `review` stage configured —
     nothing to do for the project stage; the omc grug lens still runs". The project
     half is a PASS. Continue to step 3.
   - **Present** → read it and follow its instructions, running commands from
     the project root. The project skill decides what passing means; take its
     instructions at face value and judge the outcome honestly.
     - Project stage **failed** → skip step 3 (the stage is already failing)
       and go to step 4.
     - Project stage **passed** → continue to step 3.
3. **Grug lens.** Determine the base branch the way `finish` does
   (`worktree.base_branch` in `.omc/config.yaml`, else the remote's HEAD
   branch) and invoke the internal `grug` skill with `grug diff <base>`.
   Read its `grug summary:` block and disposition every Important finding:
   - **fix now** — apply the behavior-preserving simplification, then invoke
     `/omc:check` once; a failing check means the fix is wrong, revert it and
     waive instead.
   - **waive** — one line of reason, written into the branch's design
     record under "Deliberate complexity" (grug's summary names the record;
     append the section if missing). With no design record on the branch,
     the reason lives only in the summary.
   - Any Important finding left without a disposition fails this stage.
   Minor findings are listed in the summary and never gate.
4. **Always** end with exactly one machine-readable line (plain text, no
   backticks or code fences around it):

   `OMC_STAGE {"stage": "review", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

   - `"configured"` describes the PROJECT stage only: `false` when
     `.omc/skills/review/SKILL.md` is missing.
   - `"passed"` is `true` only when the project stage passed (or was
     unconfigured) AND every Important grug finding has a disposition. It
     can therefore be `false` while `"configured"` is `false` — callers stop
     on `"passed": false` regardless of `"configured"`.
   - `"summary"` names both halves and lists waived findings by id, e.g.
     `"project stage ok; grug 1 Important fixed, 1 waived (grug:factor-late), 3 Minor"`
     or `"no project review stage; grug 0 findings"`.
````

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just check`
Expected: all pass, including `test_stage_proxy_contract` (the `nothing to do`, `"configured"`, `OMC_STAGE`, `.omc/skills/review` needles) and `test_review_proxy_runs_grug`.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format --check tests/unit/test_plugin_manifests.py && uvx ruff check tests/unit/test_plugin_manifests.py
git add skills/review/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat(review): run the grug lens after the project review stage

grug diff runs on every project once the project stage passes (or is
unconfigured); Important findings are fixed or waived into the design
record; OMC_STAGE folds the outcome into passed and summary while
configured stays about the project stage.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `finish` honors `passed: false` regardless of `configured`; README sentence

**Model:** standard coding tier

**Files:**
- Modify: `skills/finish/SKILL.md:65-71` (Step 4 bullets)
- Modify: `README.md:67` (the stages paragraph)
- Modify: `tests/unit/test_plugin_manifests.py` (`test_finish_skill_contract`)

**Interfaces:**
- Consumes: the `OMC_STAGE` shape from Task 3, including `configured: false, passed: false`.
- Produces: nothing downstream.

- [ ] **Step 1: Extend the failing test**

In `test_finish_skill_contract`, extend the needle tuple:

```python
    for needle in (
        "merge-base",
        "rebase-main",  # the inline rebase was replaced by the rebase-main skill
        "create-mr",
        "Close the worktree",
        "review comments",
        "Chat about this",
        "grug",  # review runs omc's own lens, so a review failure may come from it
        'regardless of `"configured"`',  # unconfigured review can still fail via grug
    ):
        assert needle in text, f"finish skill missing {needle!r}"
```

Leave the rest of the test as is.

- [ ] **Step 2: Run the test to verify it fails**

Run: `just check`
Expected: `test_finish_skill_contract` FAILS with `finish skill missing 'grug'`.

- [ ] **Step 3: Edit `skills/finish/SKILL.md` Step 4**

Replace the three bullets under Step 4 (currently "Unconfigured…", "A stage that changed TRACKED files…", "A stage that FAILED…") with:

```markdown
- Unconfigured (`"configured": false`) → the project half was skipped; note
  it and move on — but still read `"passed"`. The `review` proxy also runs
  omc's own grug lens on every project, so `review` can report
  `"configured": false, "passed": false`.
- A stage that changed TRACKED files (formatters, autofixes, grug's
  behavior-preserving fixes and its waivers written into the design record)
  → amend those changes into the squashed commit (`git add -u && git commit
  --amend --no-edit`); leave untracked artifacts alone.
- A stage that FAILED (`"passed": false`, regardless of `"configured"`) →
  **stop at that stage** (do not run the remaining stages, do not push):
  report which stage failed and why, and leave the branch squashed so the
  user can fix and re-run `finish`.
```

Do not touch anything else in the file. The ordering assertions in `test_finish_skill_contract` and `test_finish_starts_with_rebase_main` use first occurrences of backticked names, which this edit does not move.

- [ ] **Step 4: Edit `README.md` line 67**

In the sentence beginning `If the repo defines project stages`, after the clause `stopping before the push if one fails.`, insert:

```text
The `review` stage also applies omc's own complexity lens (the internal `grug` skill, sixteen rules distilled from grugbrain.dev) to the branch diff on every project, configured review stage or not; Important findings are fixed or waived into the design record's "Deliberate complexity" section, and an undispositioned Important fails the stage.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `just check`
Expected: all pass, including `test_finish_skill_contract`, `test_finish_starts_with_rebase_main`, and the anti-stall `CONDUCTORS` tests.

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff format --check tests/unit/test_plugin_manifests.py && uvx ruff check tests/unit/test_plugin_manifests.py
git add skills/finish/SKILL.md README.md tests/unit/test_plugin_manifests.py
git commit -m "fix(finish): stop on a failed review even when unconfigured

review now carries the grug lens, so it can report configured:false
with passed:false; finish reads passed regardless of configured and
amends grug's fixes and waivers like any stage's tracked changes.
README names the lens.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Dogfood the lens on its own branch

**Model:** top tier

**Files:**
- Read: everything on the branch; possibly modify `docs/superpowers/specs/2026-09-30-omc-grug-lens-design-design.md` ("Deliberate complexity") and any skill file grug flags.

**Interfaces:**
- Consumes: the finished `grug`, `spec`, `review` skills from Tasks 1–3.
- Produces: a branch whose own review stage passes with every Important finding dispositioned.

- [ ] **Step 1: Run grug over the design record**

Invoke the internal `grug` skill with `grug spec docs/superpowers/specs/2026-09-30-omc-grug-lens-design-design.md`. Read the `grug summary:` block.

- [ ] **Step 2: Disposition spec findings**

For each Important finding: fold the simpler alternative into the spec when it keeps the approved design; otherwise append it to the spec's "Deliberate complexity" section as `- <grug:id> @ <§section> — waived: <reason>` and replace `None.` if present. If a finding invalidates part of the approved design, STOP and ask the user — that is a CRITICAL finding under the implement conductor.

- [ ] **Step 3: Run the review stage over the branch**

Invoke `/omc:review`. This runs the repo's own `.omc/skills/review` stage (ToolContext, no-skip, argv-only, machine contracts intact, no secrets) and then `grug diff main` over the branch. Read the `OMC_STAGE` line.

- [ ] **Step 4: Disposition diff findings**

Fix now (behavior-preserving only, then `just check` via `/omc:check`) or waive into the spec's "Deliberate complexity" section. `OMC_STAGE` must end with `"passed": true`.

- [ ] **Step 5: Run the quick gate and commit**

Run: `just check`
Expected: all pass.

```bash
git add -A docs/superpowers/specs skills tests README.md
git commit -m "chore: dogfood the grug lens on its own branch

Dispositions recorded in the design record's Deliberate complexity section.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

If there is nothing to commit (zero findings), say so and skip the commit.

---

## Self-review

**Spec coverage.** Problem/Intent/Approaches: covered by Task 1's skill text. The lens table, precedence, finding format, severity: Task 1. Disposition under spec: Task 2 Step 4 text; under review: Task 3 step 3 text. Skill surface (three forms, usage error, base resolution, diff filter, no graph, summary block not end of turn): Task 1. Changes to spec: Task 2. Changes to review: Task 3. Ripple into finish and README: Task 4. Testing (INTERNAL_SKILLS incl. ticket-sync, four tests): Tasks 1–4. Dogfood: Task 5. Out of scope items appear nowhere.

**Placeholder scan.** None. Every step carries its content. Needles that must survive line wrapping (`plan phase`, `nothing to do`, `regardless of`) sit on single lines in the replacement text; implementers must not re-wrap them.

**Type consistency.** The summary block starts with `grug summary:` in Task 1 and is read by that name in Tasks 2, 3, 5. Argument forms are `spec <path>`, `section <text>`, `diff [<base>]` everywhere. The section title is `Deliberate complexity` (capital D, lowercase c) everywhere. The phrase `regardless of` is followed by a backticked `"configured"` in both the review skill and the finish skill, and both tests use that exact spelling.

**Review Focus.** Items 1, 3, 4, 5 are stated as rules in Task 1's skill text (zero findings leaves the tree untouched; record without the section counts as nothing waived; unresolvable base skips the lens; unknown token prints usage). Item 2 is stated in Task 3 (`passed` can be false while `configured` is false) and enforced in Task 4 (finish reads `passed` regardless). Prose rules cannot be unit-tested; the needle tests pin the phrases `behavior-preserving`, `no design record found`, and `regardless of` so a later edit that drops them fails loudly.
