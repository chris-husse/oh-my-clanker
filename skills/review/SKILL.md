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
     `/omc:check` once; a failing check means the fix is wrong: revert it and
     waive with the reason `simplification broke check; left as-is`.
   - **waive** — one line of reason, written into the branch's design
     record under "Deliberate complexity" (grug's summary names the record;
     append the section if missing; replace a `None.` placeholder). With no design record on the branch,
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
