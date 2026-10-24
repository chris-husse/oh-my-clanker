---
name: review
description: Run the project's review stage if configured (.omc/skills/review), then omc's grug complexity and human MR review rules lenses over the branch diff.
---

# omc review (project stage + grug and human-review lenses)

Run the PROJECT's review stage first, if the project defines one. Then run
omc's internal `grug` complexity and `human-review` human MR review rules
lenses over the branch diff — on every project, configured or not. The main
session combines their findings into one disposition and one verdict.

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory.
2. Look for `<project-root>/.omc/skills/review/SKILL.md`.
   Before any review pass, run `omc internal models` once
   and read its `OMC_MODELS` verdict. Use `tasks.review` (Review) for reviewer
   workers, including the project's reviewers; an `"ok": false` verdict
   blocks dispatch with its `message`.
   - **Missing** → report "no project `review` stage configured —
     nothing to do for the project stage; omc's grug and human-review lenses
     still run". The project half is a PASS. Continue to step 3.
   - **Present** → read it. Dispatch a Review worker with
     `tasks.review.model` and, where supported, `tasks.review.effort` to run
     its instructions and judge the project stage from the project root.
     Give a fresh worker the full project skill, project root, branch diff and
     relevant design record context; Codex model overrides require a fresh or
     partial-history fork. Dispatch even when the diff appears clean and no
     fix worker will be needed. Do not perform the project review judgment
     inline in the main session. If the harness cannot pin the worker model,
     use the orchestrator model and say so in one line; ignore effort where
     unsupported. If no worker can be dispatched, the project stage fails.
     The project skill decides what passing means; the worker returns its
     findings and outcome to the main session, which owns disposition and
     the final stage verdict.
     - Project stage **failed** → skip both lenses in step 3 (the stage is
       already failing) and go to step 4.
     - Project stage **passed** → continue to step 3.
3. **Grug lens.** Resolve one base branch the way `finish` does
   (`worktree.base_branch` in `.omc/config.yaml`, else the remote's HEAD
   branch). Dispatch a grug judge worker for the complexity lens. Dispatch a
   human-review judge worker for the human MR review rules. Run them in
   parallel when the harness supports it. Both use
   `tasks.review.model` and, where supported, `tasks.review.effort` from the
   `OMC_MODELS` verdict in step 2. The grug worker invokes the internal
   `grug` skill with `grug diff <base>` and returns its findings and
   `grug summary:` block. The human-review worker invokes the internal
   `human-review` skill with `human-review <base>` and returns its finding
   blocks and `human-review: N rules consulted, k Important, m Minor` line.
   Both report invocation failures. Give each worker the base, branch diff
   and selected design record context. A Codex model override requires a
   fresh or partial-history fork. If the harness can dispatch workers but
   cannot run them in parallel, run them serially, grug first. If no worker
   can be dispatched, invoke both internal skills inline on the session
   model and say so in one line. If the harness cannot pin a worker model,
   use the orchestrator model and say so in one line; ignore effort where
   the worker harness cannot set it. The main session owns disposition and
   the final `OMC_STAGE` verdict; neither judge fixes or waives findings.
   - **Grug unavailable.** If the `grug` skill cannot be invoked (unknown
     skill, not listed, or it answers a well-formed payload with its usage
     line), the lens did NOT run. Go straight to step 4 with
     `"passed": false` and include
     `grug skill unavailable — plugin stale? run omc update` in the summary.
     If the `human-review` skill cannot be invoked (unknown skill or not
     listed), it too did NOT run: go to step 4 with `"passed": false` and
     include `human-review skill unavailable — plugin stale? run omc update`
     in the summary. If the human-review worker returns
     `human-review: invalid rules — <reason>`, go to step 4 with
     `"passed": false` and include `human-review: invalid rules — <reason>`
     in the summary. A failed fetch or base resolution inside a running lens
     does not fail the stage; the lens ran and reports zero findings. Never
     pass the stage when a required skill could not be invoked or its bundled
     human rules are invalid.

   Otherwise read both lens results and disposition all Important findings
   from both lenses once, in the following shared order:
   - **Fix first, Review** (the "fix now" path). Before any waive, a worker
     using the configured Review choice attempts a behavior-preserving,
     local fix: the code does the same thing with less of it, or fixes the
     definite human-review shape locally. Use `tasks.review.model` and, where
     supported, `tasks.review.effort`. Codex model overrides need a fresh or
     partial-history fork with the findings and diff supplied as context. If
     the harness cannot pin a worker model, use the orchestrator model and
     say so in one line; ignore effort where the worker harness cannot set
     it. Treat findings from both lenses on the same overlapping hunk as one
     change: one fix and one `/omc:check` for that hunk, not one per finding.
     After each fix, invoke `/omc:check` once; a failing check means the fix
     is wrong: revert it. Only findings whose fix failed check, or whose fix
     would change behavior or require a nonlocal decision, proceed.
   - **waive** — one line of honest reason for each surviving finding, with
     its rule id and exact file path (`human-review:<category>/<slug>` or
     `grug:<id>`), written into the branch's design record under
     "Deliberate complexity" (grug's summary names the record; append the
     section if missing; replace a `None.` placeholder). The exact file and
     rule id make human-review waivers effective on a rerun. With no design
     record on the branch, the reason lives only in the summary. A fix that
     broke check is waived as `simplification broke check; left as-is`.
   - A surviving finding is waived whenever an honest one-line reason exists;
     it reaches the user only when no honest waive reason exists. A finding
     neither fixed nor waived becomes a question for the user; until it is
     answered it has no disposition, and any Important finding left without
     a disposition fails this stage. Questions for the user, if any remain,
     are **batched** into one numbered list at the end of the stage — never
     one dialog per finding.
   Minor findings from both lenses are listed in the summary and never gate.
4. **Always** end with exactly one machine-readable line (plain text, no
   backticks or code fences around it):

   `OMC_STAGE {"stage": "review", "configured": true|false, "passed": true|false, "summary": "<one sentence>"}`

   - `"configured"` describes the PROJECT stage only: `false` when
     `.omc/skills/review/SKILL.md` is missing.
   - `"passed"` is `true` only when the project stage passed (or was
     unconfigured) AND both lenses ran, human-review's rules were valid, and
     every Important finding from either lens has a disposition. It can
     therefore be `false` while `"configured"` is `false` — callers stop on
     `"passed": false` regardless of `"configured"`.
   - `"summary"` names project, grug and human-review, including skipped or
     unavailable lenses, and lists waived findings by id and file, e.g.
     `"project stage ok; grug 0 findings; human-review 31 rules, 1 Important fixed, 2 Minor"`
     or `"no project review stage; grug 0 findings; human-review 31 rules, 0 findings"`
     or `"project stage ok; grug 0 findings; human-review skill unavailable — plugin stale? run omc update"`.
