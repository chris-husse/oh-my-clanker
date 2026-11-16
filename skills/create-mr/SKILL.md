---
name: create-mr
description: Internal — used by /omc:finish; not meant for direct invocation. Given a squashed feature branch, run the MR gate (a review only when the tree changed and `needs_review` asks; a verify whenever the tree changed since the last green verify, unless `needs_verify` says no), generate the MR description, amend it into the commit, run the project's pre-push hook, and push with --force-with-lease. Never calls a forge API — the user creates the MR/PR.
---

# omc create-mr (internal)

Precondition: the current branch holds exactly the work to publish — typically
one squashed commit ahead of `origin/<base>` (that's what `/omc:finish`
guarantees) — and the working tree is clean.

Every push passes through this skill, finish's Step 5 re-push included, so
the gate below runs before every push. Its stages run by finish's
stage-running rules (finish's Step 3: tool drift restored after each stage
run; fixes and stage-made changes amended before a rerun, files a fix
created staged by path, so the pushed tree is the one judged). A red stage or
hook follows the behavior layer's **stage-gate rule — bounded fix-forward,
then a CRITICAL stop**.

## Step 0 — the MR gate

Both questions are answered by `omc internal gate`, which prints one
`OMC_GATE {"gate", "run", "ask", "reason", "tree", "previous", "message"}`
line; relay the gate's `message` in one line each time. A question that
prints no `OMC_GATE` line, or an `omc internal gate record …` that does not
exit 0, has decided nothing: push nothing, report the error and stop.

1. **Review question.** Run `omc internal gate review`. When `"ask": true`,
   invoke the `hook` skill with
   `needs_review previous=<previous> tree=<tree>` and read its `OMC_HOOK`
   line: its `run` replaces the gate's, and an `error` verdict leaves `run`
   true. When `run` is true, note the tree as finish's rule for a review
   that changed the tree says, invoke `/omc:review` and amend its changes, fix-created files
   included, into the squashed commit, whatever its verdict; on a passing
   verdict run `omc internal gate record review`. A red review follows the
   stage-gate rule's general clause (fix, `/omc:build`, `/omc:check`, then
   the review again). When `run` is false, no review runs; say why (the
   gate's `message`, or the hook's `reason`).

   Then apply finish's rule for a review that changed the tree: a passing
   review whose amend changed the tree gets `/omc:build` → `/omc:check`
   before the verify question, which follows even after a red one is
   repaired; it never returns to the review question.
2. **Verify question**, asked after the review so that it sees the review's
   changes. Run `omc internal gate verify`. When `"ask": true`, invoke the
   `hook` skill with `needs_verify previous=<previous> tree=<tree>` the same
   way: its `run` replaces the gate's, and an `error` verdict leaves `run`
   true. When `run` is true, say on its own line that the project's `verify`
   stage is starting (it can run for minutes, and a silent minute is a bug),
   invoke `/omc:verify`, restore known tool drift, amend, and on green run
   `omc internal gate record verify`. A red verify follows the stage-gate
   rule's verify clause and never returns to the review question. When
   `run` is false, say why: unchanged since the last green verify, or
   suppressed by `needs_verify` and its `reason`.

## Step 1 — the description

Invoke `get-mr-description` with `base = origin/<base>` and `extent = HEAD`.

## Step 2 — amend it into the commit

`git commit --amend` so the squashed commit's message IS the description
(title line + body). Forges auto-fill the MR/PR title and text from this
commit — that is why no MR is created here. The amend changes the message,
not the tree.

## Step 3 — `pre_push`

Run it after the describe amend, so the hook judges the final commit:
invoke the `hook` skill with `pre_push base=<base> branch=<branch>` and read its
`OMC_HOOK` line.

- `"configured": false` → no hook; continue to Step 4.
- `"ok": true` → continue to Step 4.
- `"ok": false`, or an `error` verdict → a red gate under the stage-gate
  rule: a fix subagent briefed with the hook's `reason` (or its `error`),
  then `/omc:build`, then `/omc:check`, with the fix amended. Then the gate
  resumes at the verify question (Step 0, item 2), never the review
  question: `omc internal gate verify`, `needs_verify` when asked, verify
  when it runs, then the describe amend and `pre_push` again. When the fix
  left the tree unchanged the gate answers `unchanged` and the flow goes
  straight on to `pre_push`. A fix repairs the code, never the hook's own
  file.

This is the gate's one way back. Nothing sends the flow back to the review
question: a verify the gate ran, a verify fix and a `pre_push` fix never
re-ask it. Nothing is pushed while a review, a verify or `pre_push` is red.

**Bound.** Inside finish the gate shares finish's fix-cycle budget; a
finish Step 5 re-push is its own conductor run. `pre_push` fixes and the
verify fixes that follow them draw on that one budget, so the way back is
finite.

## Step 4 — push

```sh
git push --force-with-lease origin <branch>
```

Force because `/omc:finish` rewrote history; the lease guards against
clobbering someone else's push. First push of a new branch: plain
`git push -u origin <branch>` is fine. If the push is REJECTED, surface the
error and stop — never retry blindly, never `--force`.

## Step 5 — report

State: branch pushed, one commit, its title, and what the gate did: whether
a review ran and why, whether a verify ran, was suppressed by
`needs_verify`, or was skipped as unchanged, and the `pre_push` outcome. Then
a convenience pointer, derived from `git config --get remote.origin.url`
(redact any credentials in the URL before printing):

- GitHub host → `https://github.com/<owner>/<repo>/compare/<base>...<branch>?expand=1`
- GitLab host → `https://<host>/<owner>/<repo>/-/merge_requests/new?merge_request[source_branch]=<branch>&merge_request[target_branch]=<base>`
- Anything else (or no recognizable forge) → "pushed `<branch>` — open the
  MR/PR from your forge's UI".

**Do not create the MR/PR. Do not call `gh`, `glab`, or any forge API.**
