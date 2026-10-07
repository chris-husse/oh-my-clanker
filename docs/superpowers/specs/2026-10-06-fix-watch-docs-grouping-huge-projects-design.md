# Fix documentation generation on huge projects: output-aware grouping in GitNexus, a truthful failure excerpt in omc

Date: 2026-10-06. Slug: `fix-watch-docs-grouping-huge-projects`.
Predecessor: `2026-10-01-fix-doc-false-stall-gitnexus-progress-design.md`
(the false-stall fix; this record fixes the failure that fix uncovered).

## 1. Problem

`omc watch --enable-documentation --rebase --auto-build --once` on funds-rs
(2726 source files in the graph, 43,772 nodes, 1,597 communities) fails in
GitNexus's grouping phase every time, and the wiki has never completed there.
The user sees:

```
→ regenerating documentation via claude api (claude-sonnet-5)
✗ wiki failed: phase":"grouping","percent":15,"detail":"Grouping files into modules (LLM)..."}
GITNEXUS_PROGRESS {"phase":"heartbeat","percent":15,"detail":"Grouping files into modules (LLM)... (90s)"}
✗ documentation still behind after regeneration
```

Two defects compound here. GitNexus dies on a failure mode that only huge
projects hit, and omc reports the wrong 400 characters, so the real error is
invisible and the predecessor fix looked "still broken".

## 2. Root cause

Reproduced twice on 2026-10-06 against the current fork HEAD `e890baff`
(fork main, rebased onto upstream that day): once plainly, once through a
loopback tee proxy that recorded the request and the raw SSE stream.

**GitNexus side.** `WikiGenerator.buildModuleTree`
(`gitnexus/src/core/wiki/generator.ts`) formats one grouping prompt for all
files. For funds-rs that prompt is 332,529 characters, about 83k tokens, under
`GROUPING_TOKEN_BUDGET` (100,000), so the single-prompt branch runs: one
`invokeLLM` call with no try/catch. The request is a streamed
`chat/completions` with `max_completion_tokens: 16384`. The endpoint answered
HTTP 200 and a stream of exactly three events over 125 seconds: a role delta,
`finish_reason: "length"`, `[DONE]`. Zero content. The model spent the whole
output cap before emitting visible text (Sonnet 5 reasons by default; the
compatibility endpoint streams no reasoning deltas). The cap could not have
held the answer anyway: a JSON object mapping 2726 paths is about 30k output
tokens. `readSSEStream` (`llm-client.ts`) throws `LLM returned empty streaming
response`; the throw climbs `buildModuleTree → fullGeneration → run()` to the
CLI's top-level catch in `cli/wiki.ts`, which prints `Error: …` with
`console.log` (stdout) and sets exit code 1. Only `batchedGrouping` catches
LLM failures and falls back to directory grouping; `parseGroupingResponse`
never throws. The batching decision considers input size only
(`batchFilesForGrouping`, `estimateGroupingPromptTokens`), never the size of
the answer. Upstream GitNexus has the identical structure; this is not fork
drift.

**omc side.** `_run_wiki` (`src/omc/gitnexus.py`) and `run_document`
(`src/omc/dependency.py`) build the failure excerpt as
`redact((cp.stderr or cp.stdout or "").strip())[-400:]`. Since fork PR 5,
stderr always carries `GITNEXUS_PROGRESS` lines, so it is never empty and the
stdout error is never shown; the exit code is never printed either. Two unit
tests (`test_wiki_failure_excerpt_is_the_tail_where_the_error_is` and the
redaction tests in `tests/unit/test_gitnexus_refresh.py` and
`tests/unit/test_dependency.py`) write the error to stderr in their node stub,
so they pass while the real path is broken. The predecessor record's claim
that "a failed grouping call degrades and exits 0" was true only for the
batched path.

Not a cause: `omc watch` already refuses to retry the same `knowledge-stale`
codes every tick (`watch._tick`), so there was no cost runaway.

**Second failure, found by the live run (2026-10-06, after fork PR 6 merged).**
With 4.1 in place the run reached the batched path (17 batches) and fell back on
batch 2 with `Grouping failed (invalid grouping JSON)`; zero pages were written
before the run was stopped. Re-running through the tee proxy reproduced it at
the same batch: 252 files, estimated answer 4,090 tokens (at the quarter-of-cap
budget), real prompt 14,053 tokens against the 8,389 the chars/4 heuristic
estimated. The stream carried 14,989 characters of well-formed JSON (15
modules, 214 of 252 paths) cut inside a string, with `finish_reason: "length"`.
Replaying that exact request: `reasoning_effort: low` and `none` were accepted
and changed nothing (16,384 completion tokens, truncated again); a 32,768 cap
finished with `stop` and valid JSON after 14,317 completion tokens, of which
about 6,000 were the visible answer. So reasoning tokens are two to three times
the visible answer on this model and endpoint, the output estimate undercounts
path-heavy text by about 1.6×, and the "three quarters absorb reasoning"
assumption behind decision 4 was wrong. `parseGroupingResponse` saw the
truncated text as malformed JSON and fell back for the batch, and 4.1.3's
diagnostic only covers the empty-stream case, so the narration could not say
why.

## 3. Goals and non-goals

Goals:

- A funds-rs-sized repository gets an LLM-grouped wiki, not a directory
  fallback, through the batched grouping path that already works on large
  repositories.
- A grouping LLM failure never kills the run; the single-prompt path gets the
  same safety net as the batched path, and the degradation is visible.
- When a wiki run does fail, omc's narration shows the real error and the exit
  code, from whichever stream GitNexus wrote it to.
- The empty-stream-at-cap case names itself, so the next diagnosis takes
  minutes.

Non-goals:

- Taming reasoning on the compatibility endpoint (`reasoning_effort`):
  verified 2026-10-06 to be accepted and ignored for `claude-sonnet-5` there, so
  it is not available; smaller batches and 4.1.5 remove the need.
- Retrying a 200 with an empty stream: a smaller batch is the fix, not a
  retry.
- Exposing the output cap as a GitNexus flag or an omc setting: nothing needs
  it.
- Persisting a full log of the child's output: the excerpt now carries the
  error; a log file would need a home and a retention rule nobody asked for.
- Any change to omc's `gitnexus wiki` argv: fork defaults carry the fix.

## 4. Design

### 4.1 GitNexus side (fork `chris-husse/GitNexus`, separate PR, lands first)

**4.1.1 Output-aware grouping batches.** Grouping is batched when EITHER the
prompt exceeds the input budget OR the estimated answer exceeds an output
budget. The estimate is the sum over files of the tokens in `"<path>",`, computed
with the existing `estimateTokens`, and nothing else. The output budget is
one named constant beside `GROUPING_TOKEN_BUDGET`, one quarter of the LLM
config's `maxTokens` (already on the generator as `this.llmConfig`; 16,384
today, so about 4,096 tokens): the other three quarters absorb module keys,
braces, and reasoning, so no second allowance is needed.
`batchFilesForGrouping` keeps its partition loop (top-level directories,
sub-batching, symbol trimming); only its "fits" predicate changes from "input
fits" to "input fits AND estimated output fits". `buildModuleTree` uses the
same predicate to choose between the single call and `batchedGrouping`. For
funds-rs this yields roughly seven batches of 350 to 450 files, each answering
in about 5k tokens, on the code path hummingbird already exercises.

**4.1.2 The single-prompt path gets the batched safety net.** The bare
`invokeLLM` call in `buildModuleTree` is wrapped like the one in
`batchedGrouping`: on failure it reports
`onProgress('grouping', 15, 'Grouping failed (<reason>), falling back to
directory grouping')` and uses `fallbackGrouping`. The CLI's end-of-run
summary prints one line naming the fallback and its reason whenever grouping
fell back, in either path: `WikiRunResult` gains a `groupingFallback` reason
string, printed at both summary sites in `cli/wiki.ts` (normal run and
`--review` continuation). omc users see the progress line as a `·` narration
off a TTY; the summary line is for TTY users, whose bar overwrites progress
details. No flag is written to the wiki's meta: omc would then need a policy
for fresh-but-degraded wikis, which nobody has asked for.

**4.1.3 A specific error for the length case.** `readSSEStream` records the
stream's `finish_reason`. Empty content with `finish_reason: "length"` throws
an error that names `max_completion_tokens` and states that the model
produced no visible text before reaching it. Other empty streams keep the
existing message. The error text is what omc's new excerpt (4.2.1) shows.

**4.1.5 Truncated answers regroup as halves; the budget is an eighth.** (Fork
PR 7, after the live run above.) `LLMResponse` carries `finishReason` from both
the SSE and JSON paths. `buildModuleTree` runs one loop over
`fits ? [files] : partition(files)`, so the single-prompt and batched paths
share one helper per batch: call the LLM; if `finishReason` is `length` and the
batch holds more than one file, split it in half and group each half the same
way, narrating `<label>: answer for N files hit max_completion_tokens (16384),
regrouping as two halves`; a single file that still truncates falls back for
itself only, with a reason naming the cap. Thrown LLM errors keep the whole-run
directory fallback. `GROUPING_OUTPUT_BUDGET_DIVISOR` goes from 4 to 8 so that
expected use (visible answer plus two to three times that in reasoning) sits
near half the cap and splits are the exception: funds-rs partitions into 28
batches instead of 17. The malformed-JSON fallback reason gains the finish
reason and response size (`invalid grouping JSON (finish_reason=stop, 7
chars)`). No request parameter changes, so other providers see only smaller
batches.

**4.1.4 Tests (fork).** In `test/unit/wiki-grouping-batch.test.ts` and
siblings: files under the input budget but over the output budget take the
batched path (more than one LLM call); a rejecting single-prompt call yields
directory grouping plus the progress line and no throw; the SSE reader with
`length` and no content throws the specific message, and with content behaves
as before; the output estimator is checked on a handful of paths. For 4.1.5: a
fake LLM that truncates above N files yields every file LLM-grouped, more calls
than batches, no fallback line and no `Other` module; a truncated single-prompt
answer recovers the same way; a single file that still truncates falls back
with the cap-naming reason; the malformed-JSON reason carries finish reason and
size; a 160-token estimate no longer fits a 1,000-token cap; `finishReason` is
populated on both response paths.

### 4.2 omc side (this branch)

**4.2.1 A truthful failure excerpt.** One helper in `src/omc/wikirun.py`,
shared by `_run_wiki` and `run_document`, builds the excerpt from both
streams: redact first (the caller's `DocsRun.redact`, plus `dependency._redact`
where it applies today), drop `GITNEXUS_PROGRESS` lines and blank lines, then
take the last 400 characters of stderr's remaining lines followed by stdout's
lines. Ordering stderr before stdout puts GitNexus's final `Error: …` at the
end, where the tail keeps it. The helper takes the completed
process and a redact callable, so `gitnexus.py` passes the exact-key
redactor and `dependency.py` passes its existing composition (exact key
first, then its userinfo heuristic), and `wikirun.py` keeps its
standard-library-only imports. Both call sites print the exit code:
`✗ wiki failed (exit 1): Error: LLM returned no content before reaching
max_completion_tokens (16384)…` and the `error: gitnexus wiki failed (exit
1): …` twin in `run_document`. `run_document` also fails when the exit code
is zero but no wiki directory exists; that message names the missing
directory rather than presenting "exit 0" as the error. Stall handling, the
`return True` ("the recomputed verdict, not the exit code, decides") and the
narration lines around it are unchanged.

**4.2.2 Tests encode the real contract.** The two existing excerpt tests move
the error to stdout and put progress noise on stderr, which is what GitNexus
does. New `tests/unit/test_wikirun.py` cases cover the helper: progress lines
filtered, stdout error visible, exit code present, a key appearing on stdout
redacted before truncation. No new E2E: the existing api-backend E2E on a
two-file repo keeps exercising the single-prompt path, and the failing-run
contract is fully observable at the stub level.

**4.2.3 The fork pin.** `docker/Dockerfile.e2e` bumps `ARG GITNEXUS_REF` to
the merge commit of the fork PR. The pin (`f858d50b…`) is already stale
against fork main (`e890baff`); the bump catches up as well. The comment
above the pin names this record.

### 4.3 Sequencing and acceptance

1. Fork PR opened against `chris-husse/GitNexus` main with 4.1; merged
   (PR 6, `d194fc1b`).
2. Live run failed on batch 2 of 17 (see section 2); diagnosed through the tee
   proxy; fork PR 7 with 4.1.5 opened and merged (`24af4f60`).
3. Grouping-only live proof, user decision 2026-10-06: the same tee-proxy
   capture against the fork build, stopped at `Created N modules`. Pass means
   every batch line appears and no `Grouping failed` line does. Passed
   2026-10-06 on PR 7's commit `baccf011`: 28 of 28 batches ended with
   `finish_reason: stop` and valid JSON, no fallback or regrouping line,
   `Created 277 modules` after 953 s; the snapshot
   `first_module_tree.json` now sits in funds-rs's `.gitnexus/wiki`, so the
   next full run resumes from it instead of regrouping.
4. `omc update` on the host fast-forwards and rebuilds the clone
   (`update_gitnexus`).
5. omc PR with 4.2, pin bumped to the second fork merge commit; merged.
6. Live acceptance, user decision: a FULL `omc watch --enable-documentation
   --once` on funds-rs in the primary checkout. Expected narration: `·
   Grouping batch 1/N (LLM)...` through `· Wiki generation complete`, then
   `✓ documentation refreshed → .omc/docs/gitnexus/docs`, with markdown pages
   under that path. The grouping outcome is fully visible through that
   narration: batch lines appear, and a `Grouping failed … falling back`
   line must not. Individual page failures are not: GitNexus exits 0 and
   lists them only in its own stdout summary, which omc does not show on
   success; they are page-generation issues outside this record. This run is
   long and costs real tokens; it is the gate the user chose over a bounded
   grouping-only check.

## 5. Decisions taken during brainstorm

| # | Decision | Why |
|---|---|---|
| 1 | Fix in both GitNexus and omc, one design | GitNexus owns the grouping failure; omc owns the invisible error. Diagnostics alone leaves funds-rs without docs. |
| 2 | Reproduce before designing the fork change | Done by the design session, twice, with a tee proxy; the design targets the actual failure (200 + empty stream at the cap), not the class of LLM errors. |
| 3 | Output-aware batching is the fix for huge repos | The answer, not the prompt, is what does not fit; raising the cap keeps one slow fragile 30k-token answer; fallback alone makes funds-rs permanently directory-grouped. |
| 4 | Output budget = one eighth of `maxTokens` (was one quarter), one constant, no flag | The quarter assumed reasoning fits in three quarters; the live run measured reasoning at two to three times the visible answer. An eighth puts expected use near half the cap. |
| 5 | Single-prompt failure falls back to directory grouping, narrated and summarized; no degraded flag in wiki meta | The user's starting position, kept visible for free through the existing progress narration; a meta flag would force omc to invent a policy for fresh-but-degraded wikis. |
| 6 | Empty stream at `finish_reason: length` is its own error | Names the real cause; costs one tracked field. |
| 7 | omc excerpt reads both streams, filters progress lines, prints the exit code; no log file | The excerpt then carries the error; persistence needs a home and retention nobody asked for. |
| 8 | No omc argv change; fork defaults carry the fix | One moving part fewer; the pin bump is the only omc-side dependency on the fork. |
| 9 | Full funds-rs wiki run is the acceptance gate | User decision 2026-10-06, over a bounded grouping-only check. |
| 10 | A truncated grouping answer splits the batch in half and regroups, instead of falling back | Fixes the class: the estimate cannot see reasoning tokens, so the run must self-correct. Chosen over a grouping-only 32k cap (works for Sonnet 5 in one line but leaves the budget blind and regresses providers that reject a larger cap) and over splitting alone (most batches would truncate first and waste a full call each). User decision 2026-10-06. |
| 11 | A grouping-only live proof precedes the next full generation | The first live run cost a full attempt to learn one batch's failure; the proof costs about 25 minutes and stops at the module tree. User decision 2026-10-06. |

## 6. Risks and open points

- **Estimate drift.** The output estimate is a heuristic (path tokens alone)
  and undercounts path-heavy text by about 1.6× on Sonnet 5. If a batch still
  hits the cap, 4.1.5 regroups it; tightening the eighth is a one-constant
  change.
- **Reasoning still eats the cap on a batch.** Happened on the first live run
  (section 2). Now the batch is regrouped as two halves (4.1.5); the cost is
  one wasted call per truncation, and the run stays LLM-grouped. Only a single
  file that truncates still falls back, for itself.
- **Two repos, one behaviour.** Until `omc update` runs on a host, an omc
  with 4.2 and a GitNexus without 4.1 shows the real error (good) but still
  fails on huge repos. The E2E pin makes the suite test the new pair.
- **The pin is a fork merge commit.** Pointing `GITNEXUS_REF` at a branch head
  is not acceptable on main; the omc PR waits for the fork merge.
- **Acceptance cost.** The full funds-rs run generates many module pages. It
  is the user's chosen gate; a failing page surfaces as `Failed: <name>` and
  is not a grouping regression.

## 7. Testing

Fork: see 4.1.4; all unit, fake LLM, no network.

omc: see 4.2.2. `just check` runs them; the node stub keeps running on a
restricted PATH (shell builtins and absolute paths only). The api-backend E2E
(`tests/e2e/test_e2e_gitnexus.py::test_document_api_backend_generates_wiki_docs`)
is unchanged and keeps the suite under the five-minute ceiling.

Live: section 4.3 steps 3 and 6.

## 8. Hardening notes

**§4.1 (explain + grug).** Explain, from the fork graph re-indexed at
`e890baff`, confirmed the chain `fullGeneration → buildModuleTree →
batchedGrouping → batchFilesForGrouping` has no other callers, that
incremental mode and the `--review` flow never regroup, that
`first_module_tree.json` is written only after grouping succeeds, and that
`mergeGroupings` canonicalizes names by slug so more batches cannot
duplicate modules. It found the end-of-run summary printed at two CLI sites
(normal and review continuation), so the fallback line needs a
`WikiRunResult` field; the record now says so. Grug raised two Important
`say-no` findings: the per-module allowance in the output estimate (fixed:
dropped, the three-quarters margin absorbs it) and the summary line itself
(waived by decision 5, see Deliberate complexity).

**§4.2 (explain + grug).** Explain confirmed `wikirun.py` has
standard-library-only top-level imports and both call sites already import
it, so a helper there creates no cycle; that `run_document` composes the
exact-key redactor with its userinfo heuristic today and the mask carries no
`@`, so the order is safe; and that `run_document` also fails on exit 0 with
no wiki directory, which the message must not present as "exit 0". Both
points are folded in (redact callable parameter; missing-directory wording).
Grug found nothing: the helper has two users at birth and the regression
comes from existing tests moving to the real stream layout.

**§4.3 (explain + grug).** Explain confirmed `update_gitnexus` is a no-op
until fork main moves and then fast-forwards and rebuilds, that the `ARG`
precedes the build `RUN` so the E2E layer cache rebuilds, that `watch
--once` bypasses the knowledge-stale suppression, and that the mirror runs
only after a clean verdict. It flagged that failed pages are invisible
through omc on success while grouping fallback is visible; the acceptance
text now names both. Grug raised one Important `80-20` finding on the full
run as the gate, waived by decision 9.

**Whole record (explain + grug).** No conflict with the predecessor's
decisions (no `--timeout`; the only parsed GitNexus line is still the
structured progress line), the narration contract, `run_document`'s
`OMC_PROGRESS` stdout contract, or the ToolContext boundary. The old
`✗ wiki failed: ` prefix is matched only by the two unit tests this record
rewrites. Grug: zero cross-section findings beyond the two recorded waivers.

## Deliberate complexity

- §4.1.2 end-of-run summary line for a grouping fallback (`grug:say-no`):
  a second surface for a fact the progress line already carries, costing a
  `WikiRunResult` field and two CLI print sites. Waived by brainstorm
  decision 5, where the user chose "fall back, narrate, summarize" over
  "narrate only".
- §4.3 step 4 full funds-rs wiki run as the acceptance gate (`grug:80-20`):
  the grouping change is observable from the first batch lines and the
  module tree, so a bounded grouping-only run would verify it at a fraction
  of the cost. Waived by brainstorm decision 9, where the user chose the full
  run.
