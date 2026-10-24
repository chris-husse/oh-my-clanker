# MR review: review one merge request written by someone else

Date: 2026-10-23 · Branch: `feature/add-mr-review-skill` · Status: design record,
amended 2026-10-23 after the second live run (redesign of passes, posting and output)

A user-facing plugin skill, `/omc:mr-review <mr-link>`. It is the code-review
half of `/omc:review` plus the design review and hardening `/omc:design`
applies to a record, pointed at a merge request the user did not write. It
checks out the MR head in isolation, finds or derives the design the MR was
meant to implement, drafts review comments from several passes, reconciles
every draft against the author's own material, and posts the few that
survive — unattended, each labelled as automated. The terminal shows a
one-paragraph summary; the full reasoning is written to a review trail on
disk.

## 1. Problem and goal

omc reviews only the user's own branch: `/omc:review` judges the current
worktree's diff and `/omc:design` hardens the user's own record. Reviewing a
colleague's merge request today means pasting a link into a bare session and
improvising: the session has no checkout of the MR head, no project review
rules, no grug lens, no design-review pass, and whatever it writes to the
forge is written in the user's name with nothing marking it as automated.

The first version of this skill (shipped from this record, one live run)
fixed the mechanics. A second live run — a real self-hosted GitLab MR, a
27-file service change whose description linked a design spec in another
repository and a ticket, and whose code pinned a dependency to an unmerged
upstream MR — showed what it still got wrong: it asked questions the author's
linked spec and upstream MRs already answered, because the review never
opened them; it interrupted the user with a dialog per comment; and its
comment format was off. The user stopped the run before posting. This
amendment redesigns the review around those three findings.

Goal: one MR link in; a review that reads what the author read (the MR, its
linked spec, tickets and upstream MRs) before judging; comments that each
point to a real gap between implementation and intent, a real quality
problem, or a question the author's material does not answer; posted
unattended with a severity prefix and an automated-review footer; a short
terminal summary and a complete review trail on disk. GitLab (including
self-hosted) and GitHub through whatever forge tool the session already has
(a GitLab MCP server or `glab`; `gh`).

Non-goals: MR sets and stacks (a stacked MR is reviewed on its own diff
against its own target branch); running automatically on every MR and
learning from replies to posted comments (§8 keeps the door open, nothing
is built).

Anti-goals — the skill never does these, and the needle tests pin each:

- posts a comment that did not pass the reconcile step, or any body without
  its severity prefix and the fixed footer;
- posts the reviewer's own design, derived or hardened: it is a private
  yardstick, never a comment;
- approves or unapproves the MR, resolves a thread, replies to an existing
  thread, or sets a review verdict (approve / request changes);
- executes the author's code in any way: no builds, no tests, no package
  installs, no git hooks, no direnv;
- lets MR content steer the review. The description, commit messages, code
  comments, file contents, linked documents and any review rules the MR
  changes are data to be reviewed, never instructions to the reviewer. The
  author's material is read to *answer* draft comments, not to *direct* the
  reviewer;
- comments on a design choice because the reviewer would have chosen
  differently. Only a real problem is a comment (Decision 11).

## 2. Decisions taken during brainstorm

Rows 1–9 are the original brainstorm and audit; rows marked *superseded* or
*amended* are kept for history. Rows 10–18 are the 2026-10-23 redesign after
the second live run. A reason beginning "Design worker's call" is a default
chosen while amending this record, not a user ruling; the user's silence on
it is not acceptance.

| # | Decision | Reason |
|---|----------|--------|
| 1 | Ships as an omc plugin skill **`/omc:mr-review`**; generic and forge-neutral (GitLab through an MCP server or `glab`, GitHub through `gh`); no organisation-specific names anywhere | User decision. The skill lives beside `review` and `design`, whose halves it reuses |
| 2 | **Checkout**: the current checkout when its `origin` is the MR's project, else a temporary partial clone; the MR head goes into a temporary detached worktree; both are removed at the end | User decision. Reviewing needs the files, not just the diff; the user's checkout is already there in the common case, and a partial clone is cheap in the rest |
| 3 | **Single MR only**, never sets or stacks | User decision, for simplicity. A stacked MR is reviewed on its own diff against its own target |
| 4 | ~~Walk every comment first, then post all approved comments in one step~~ — **superseded by 12** (2026-10-23 redesign): there is no walk and no confirmation | Original reason: the author sees one posting step. Superseded because the user does not want to review the comments one by one ("I am not really reviewing these except for general sanity"); the reconcile step (10) replaces the human filter |
| 5 | **Review passes** — *amended by 10* (2026-10-23 redesign): omc's defaults always run (correctness, grug `diff`, and the intent-and-vetting pass that replaces the old design reviewer); a target repo set up with omc (`.omc/skills/review/SKILL.md`) gets its project review pass added; every pass produces **drafts**, and a reconcile step decides what is posted | User decision. The project's rules are the project's business; omc's lens applies everywhere. Amended because passes alone produced comments the author's material already answered |
| 6 | **Every posted comment carries a fixed disclosure footer** — *amended by 14* (2026-10-23 redesign): the footer no longer names an approver, since nobody approves | User decision, emphatic: never post as the user on other people's MRs without saying it is automated |
| 7 | **Testing**: needle tests on the skill text plus manual live runs on real MRs; no E2E — *amended by 16* (2026-10-23 redesign): during development the live runs are stopped by the user before posting | User decision. A forge-posting E2E needs a forge in the container; the live run is the real-tool check and is recorded in this record |
| 8 | **Future direction, out of scope**: run on every MR automatically. The design must not preclude it — *extended by 17* | User decision. §3–§5 need no human; the review trail is the hand-off point |
| 9 | **Posting ladder** — *amended by 13* (2026-10-23 redesign): rung 1 is the forge's all-in-one post (on GitHub its body is exactly the footer line); when that fails with a permission or scope error the comments are posted individually without asking; when that fails too, the run stops and records what was not posted with scope guidance; `glab`, which has no all-in-one method, posts individually from the start (audit ruling, 2026-10-23, kept) | User decision (the original answer to the GitHub review-body question; the audit ruling that the skill does not argue with the user's tool choice). Amended because the ladder's questions were the only human step left after 12, and the user wants none |
| 10 | **Pipeline** (2026-10-23 redesign): (a) the review passes produce **draft** comments; (b) an **intent** step finds the design — a design document in the MR's own diff, or one reached through the links in the MR description, commits and diff (a spec in another repository, upstream MRs, the tickets and their references), read-only through the session's tools — and **hardens** it with the hardening half of `/omc:design` (per-section `/omc:explain` + `grug section`, `grug spec` on the record); with no design document it **derives** one from the material; with no material either, that absence is itself a comment; (c) the implementation is **vetted** against the found-and-hardened or derived design; (d) a **reconcile** step takes every draft from (a)–(c) with the design and the author's material and keeps, sharpens or drops each | User decision. The second live run's comments asked what the linked spec and upstream MRs already answered; a reviewer who has not read the author's material cannot tell a gap from an already-made choice |
| 11 | **The design is a private yardstick, and the implementation is the baseline** (2026-10-23 redesign): the hardened or derived design is never posted; a design comment is raised only for a real problem — the implementation breaks or misses the intent, is incorrect or unsafe, fights a stated repo convention, or carries complexity that clearly does not pay for itself at grug's Important bar — never because the reviewer would do it differently; before raising one, look for the author's reasoning (spec, ticket, threads, code comments) and drop the point when the reasoning holds; "consider X" alternatives without a concrete cost are dropped; style and craft only for clear defects, as Minor | User decision, emphatic: "you are not doing the work for them; you are commenting on the work, like a human reviewer" — form an opinion, compare, inspect style and complexity even when the result is technically right, but lean towards what was built: choices were made to end up here |
| 12 | **No human in the loop** (2026-10-23 redesign): no per-comment walk, no batch confirmation; posting is unattended; no dry-run flag or mode is added | User decision for the walk and confirmation. The absent flag is the Design worker's call, consistent with Decision 8's "no flag or mode": a development run is stopped by the user before §7 (Decision 16) |
| 13 | **Unattended defaults** (2026-10-23 redesign): the forge head moved since checkout → post nothing, report the run as stale, and ask for a fresh run; the all-in-one post fails with a permission or scope error → post individually without asking; individual posting fails too → stop, and record every unposted comment with scope guidance in the trail and one output line; nothing survives reconcile → post nothing, no "looks good" note | Design worker's call, each the only safe choice once no question may be asked: a stale head makes inline anchors lie; individual posting is the ladder's existing next rung; a note with no finding is noise |
| 14 | **Comment format** (2026-10-23 redesign): every body starts with its severity — `Critical: `, `Important: `, `Minor: ` or `Question: ` — and ends with exactly `— 🤖 Automated review comment (omc mr-review)` | User decision. The approver clause of the old footer is gone with the walk; the forge's current user is no longer read, nothing else needed it |
| 15 | **Output** (2026-10-23 redesign): the terminal shows a short summary — comments posted, split by kind (`code` / `design`) and by severity, the MR link, the trail path, and one line when the run stopped; everything else goes to the **review trail**, a directory under `$OMC_HOME/mr-reviews/<host>/<project-path>/<iid>-<head-sha7>/` holding the material followed and not followed, the design found or derived with its hardening, the vetting, every draft with its reconcile verdict and reason, and the posted bodies with their note ids and links | User decision for the summary and the trail's contents ("if something is really fishy, I can figure out why you made the comments"). The directory in place of the old single file is the Design worker's call: five artifacts written by three different steps do not belong in one appended file |
| 16 | **Development runs** (2026-10-23 redesign): the live runs that develop and verify this amendment are real MRs the user already knows; the user instructs the session to stop before §7 and reads the trail and the would-be comments; the skill gains no mode for this | User decision ("we stop before posting manually"). It keeps Decision 7's "no E2E" and Decision 12's "no flag" |
| 17 | **Future, out of scope, not precluded** (2026-10-23 redesign): a later skill will tune this reviewer from real-world feedback on posted comments (replies, possibly from other agents); the trail therefore keeps for every posted comment its note id and link, severity, kind, rationale, reconcile verdict and the design context it was judged against | User decision. Nothing is built; the trail's per-comment fields are fixed so that a later reader finds them |
| 18 | **Who reads the author's material** (2026-10-23 redesign): the main session collects it read-only through its own tools, bounded (two hops from the MR, a document cap), and saves it into the trail; workers read it from there and keep no forge or tracker access | Design worker's call, extending the audit ruling that the main session reads the ticket and hands it over as data: the trust boundary "workers never touch the forge" stays intact, and material on disk is what makes the trail — and a later tuning skill — able to show what the reviewer saw |
| 19 | **Reconcile is its own worker** (2026-10-23 redesign): a fresh Review worker (R, §5) judges every draft; it is never the intent-and-vetting worker continued | User decision (hardening question on grug's `80-20` finding): an independent filter, not anchored on drafts it wrote, is the point of the step, and the trail must show an independent reason for each drop. At the same `/omc:design`, the user accepted the Design worker's calls in rows 12 (no flag), 13, 15 (directory) and 18 as presented |

Assumptions accepted with the design: the session has `omc` on `PATH`
(`omc internal models` is how the Review choice is read, as in `/omc:review`);
a harness that cannot dispatch a worker fails the skill closed, as the review
stage does (the user asked for review to run as an agent on its own model —
`2026-10-06-task-type-model-selector-design.md` §7); the forge tool the
session has is the one used, in the order MCP server, then CLI, and the skill
refuses with the name of what it needs when neither is present; a tracker
tool or a documents tool is used when the session has one and silently
absent otherwise — their absence is recorded in the trail, never a stop.

## 3. Getting the MR onto disk

Six steps, all plain `git` plus one forge read. No new Python: link parsing
and forge I/O are the harness's tools, and git does the checkout.

1. **Parse the link.** A path containing `/-/merge_requests/<iid>` is a
   GitLab MR (any host, any subgroup depth); `/pull/<n>` is a GitHub PR.
   Anything else prints the usage line — `Usage: /omc:mr-review <mr-link>` —
   and stops.
2. **Read the metadata** through the session's forge tool: title,
   description, author, source and target branches, base and head SHAs, the
   clone URL, the commit list with messages, and the existing discussions;
   on GitLab through `glab` also the diff version's `base_sha`, `start_sha`
   and `head_sha`, which its inline positions need (§5) — the MCP posting
   methods anchor by path and line and need none. The forge's current user
   is not read: the footer no longer names it (Decision 14) and nothing else
   uses it. No forge tool → refuse, naming what is needed for that forge (a
   GitLab MCP server or `glab`; `gh`).
3. **Choose the checkout.** If the current directory is inside a git repo
   whose `origin` is the MR's project (same host and project path, scheme
   and `.git` suffix ignored), use it. Otherwise make a temporary partial
   clone (`--filter=blob:none`, no checkout) of the clone URL the forge
   reports; the user's own git credentials apply, and a clone failure stops
   the skill with git's error (userinfo redacted before anything is printed,
   as `create-mr` and `dependency.py` already do).
4. **Fetch the MR head and the target branch.** The forge-side ref —
   `refs/merge-requests/<iid>/head` on GitLab, `refs/pull/<n>/head` on
   GitHub — exists on the target project for fork MRs too, so no second
   remote is ever added. The fetched SHA is captured at once
   (`git rev-parse FETCH_HEAD`, before anything else can fetch) and is **the
   reviewed head** for the rest of the run: the trail names it, and
   §7 compares the forge against it before posting. The head SHA the forge
   reported in step 2 is informational; on GitLab the forge head is read
   again after the capture (through `glab`, the diff version's `head_sha`;
   through the MCP server, which exposes no versions, the MR's newest
   commit) and must equal the reviewed head, or the run aborts rather than
   pair a newer version with the fetched tree. No local ref or branch is created.
5. **Check out.** A detached temporary worktree is created at that SHA with
   plain `git worktree add --detach` — never `wt switch`, whose project hooks
   (`.config/wt.toml`) would run against the author's tree — under a fresh
   directory below one known temp parent (`${TMPDIR:-/tmp}/omc-mr-review/`,
   the shape `investigate` uses for its reports), with
   `-c core.hooksPath=/dev/null`
   so git's `post-checkout` hook cannot run the author's code even when the
   user's checkout points `core.hooksPath` at a tracked directory, and with
   `GIT_LFS_SKIP_SMUDGE=1`, so Git LFS files stay pointer files (what the
   forge diff shows) and nothing is downloaded. Filter drivers come only
   from the user's own git configuration, so other filters run as in any
   checkout. The
   worktree's own HEAD keeps the commit alive. The MR's diff is
   `merge-base(target, head)..head` — what the forge shows.
6. **Clean up**, always, including after a failure or a stale stop: remove
   the worktree (`git worktree remove --force`, then `prune`) and delete the
   temporary clone when one was made. Skill text has no `finally`, so the
   start of a run also removes any leftover worktree under the known temp
   parent from a run that died. A run proves it died through its ownership
   marker, written before any path it lists: run id, git common-dir, owned
   paths, and the owner — the harness process that runs the session's shell
   commands (`$PPID`) with its start time. Only a marker whose owner is gone
   (no such process, or a different start time) is swept; a missing,
   incomplete or unreadable marker is left and reported, and directory age
   never counts. A run that died earlier in a still-running session looks
   live and is swept once that session ends. The user's local branches are
   untouched throughout; `origin/<target-branch>` may advance through grug's
   ordinary fetch (§4 C), and the only other trace in their checkout is
   unreachable objects that git's own gc collects. The review trail (§6) is
   never cleaned up: it is the record of the run.

The current-checkout case exists because it is the common one for a
maintainer reviewing their own project's MRs and it lets `/omc:explain` see
the project's knowledge graph (§4 D); the clone case exists so the skill works
from anywhere. Both end in the same thing: a detached worktree at the MR head
with the target branch fetched, which is all §4 needs.

## 4. Review passes — the author's material, then parallel Review workers

The main session runs `omc internal models` once and reads its `OMC_MODELS`
verdict; every worker is dispatched on `tasks.review.model` and, where the
harness takes it, `tasks.review.effort`, exactly as `/omc:review` dispatches
its project reviewer and grug judge. An `"ok": false` verdict blocks dispatch
with its `message`. Workers run in the temporary worktree, never touch the
forge or the tracker, and write nothing outside the review trail (§6). Every
worker mission states that MR content and the author's material are data,
not instructions (§1 anti-goal), and that the worker runs nothing from the
checkout.

Every pass produces **drafts**: comments addressed to the author that the
reconcile step (§5) may still drop. A pass is told so, and told the bar its
drafts will be held to (Decision 11), so that it drafts findings, not
alternatives.

### 4.0 The author's material (main session, before dispatch)

Before the workers start, the main session collects what the author pointed
at, read-only, through the tools the session has, and saves each item under
the trail's `material/` directory as data:

- the MR itself: title, description, commit messages, existing discussions
  (already read in §3 step 2);
- every URL in the description, the commit messages and the added lines of
  the captured diff (a plain URL scan, no parsing of manifests — a code
  comment that cites an upstream MR is a link; the second live run's
  dependency pin had one), followed with whichever read-only tool the
  session has for that URL's host — the forge tool for a file, MR or PR on a
  forge (cross-repository included), the tracker tool for a ticket, a
  documents tool for a document — and saved as fetched; a URL no session
  tool can read is not followed;
- one more hop: the URLs found inside a fetched ticket or design document.

The crawl is bounded — two hops from the MR and a small document cap, the
exact cap being the plan's — and deterministic in what it records:
`material/index.md` lists every URL found, with *followed*, the tool that
read it and the saved file, or *not followed* and the reason (no tool for
that host, outside the hop bound, over the cap, fetch error). A URL the
session cannot follow is never a stop; it is a line in the index, and the
reconcile step treats a question that the unfollowed material might answer
as still open. The main session fetches and saves; it does not read the
material. The material is what the D and R workers judge against, and the
index is how the trail shows what the reviewer did and did not see.

Workers keep no forge or tracker tools (Decision 18): a worker that reads the
author's material — which may contain instructions — must have no way to
act on them against the forge.

### 4.1 Draft passes

- **A. Correctness reviewer** (always): bugs, edge cases, error handling,
  tests, security, over the diff with the full tree available for context.
- **B. Project review pass** (only when the target repo has
  `.omc/skills/review/SKILL.md`): runs that skill's instructions. The skill
  and `.omc/config/AGENTS.md` are read **from the target branch**
  (`git show <target>:<path>`), never from the MR head, so an MR cannot
  rewrite the rules it is judged by. (An MR that touches `.omc/` at all is
  flagged mechanically in §5, whatever passes ran.)
- **C. Grug judge** (always): invokes the existing internal `grug` skill with
  `grug diff <target-branch>` from inside the temporary worktree, with the
  hook-disabled git configuration in its environment. grug's own
  `git fetch origin <target-branch>` may advance `origin/<target-branch>` in
  a reused checkout, as any fetch would. grug diffs from
  `merge-base(origin/<target-branch>, HEAD)`, so right after it returns the
  main session checks that merge-base against the run's diff base; if the
  target moved under the MR's merge-base, grug judged a different diff and
  the run aborts and asks for a fresh one (a target that merely advanced
  keeps the merge-base and does not abort). grug already
  reads conventions and the design record from the repo root and the diff, so
  it needs no change: a design record the MR adds is found (grug's Step 2,
  first rule) and waivers in the author's own "Deliberate complexity" still
  suppress findings. The mission tells this worker to treat `OMC_SLUG` as
  unset (a worker inherits the session's environment and cannot drop it), so
  a record from the user's own worktree is never picked up. grug's own
  "unavailable" rule
  applies: if it cannot be invoked, answers with its usage line, or skips its
  lens because it cannot resolve the base, the skill stops with `grug skill unavailable — plugin stale? run omc update`.
- **D. Intent and vetting** (always): the `/omc:design` hardening pass
  turned outward, now reading what the author read. One worker, two ordered
  steps, both written into the trail (§6: `design.md`, `vetting.md`):

  1. **Find or derive the design.** First the MR's own diff: a design
     record under `docs/superpowers/specs/`, or any added or changed
     document that reads as a design — ADR, RFC, spec, design note, by path
     or by title; the convention is a hint, not a requirement. Then the
     material of §4.0: a spec in another repository, the tickets, upstream
     MRs. **Found** → harden it exactly as `/omc:design` hardens a record:
     per section, `/omc:explain` from the checkout ("does this make
     architectural sense here, what does it touch, what might break" — no
     graph → explain says so and the worker reads the code instead) and
     `grug section` over the section plus explain's answer; then `grug spec`
     over the document's file — in the checkout, or as saved under
     `material/`. A hardening finding becomes a draft only when the
     implementation inherited the design's problem; a weakness of the
     document that the code does not carry is noted in `design.md` and goes
     no further. **Not found** → derive the design from the material: what
     should have been built, as the worker would build it, with the ticket's
     and the threads' constraints respected and every derived point tagged
     with the material it rests on. **No material either** (no design doc,
     no followable link, an empty description) → one Question draft saying
     that the MR's intent cannot be established from what it links — a human
     reviewer could not tell either — and the vetting step runs against the
     MR description alone.
  2. **Vet the implementation against the design** — found-and-hardened or
     derived: promises the design makes that the diff does not keep; scope
     the diff adds that the design does not ask for; choices that contradict
     it. Each is a draft with the design point it rests on, so the reconcile
     step and the trail can show the comparison.

  The design is **private** (Decision 11): the worker never drafts "here is
  how I would build this"; it uses its design as a yardstick for real
  problems and leans towards what was built. The mission carries Decision
  11's bar verbatim — real problem only; look for the author's reasoning
  first and drop when it holds; no "consider X" without a concrete cost;
  style and craft only for clear defects, Minor. explain runs with whatever
  evidence sources the user has configured globally; the worker hands it the
  design's section and its question, never the diff. Two limits of explain
  here, named rather than engineered around: it reads the project's context
  map (`.omc/skills/explain-context/`) from the checkout, which is the MR
  head, so an MR that edits that map steers its own design review — the
  mechanical `.omc/` draft of §5 is what makes such an MR visible; and a
  globally configured evidence source may send the section text it is given
  off the machine, as it does for any `/omc:explain` question.

A and B may be one worker or two — the plan's call; the record fixes what
each pass judges and where its rules come from. D is one worker because its
two steps share one context — the design it found or derived — and its
output is the context the reconcile step judges against; splitting it would
hand a derived design across a worker boundary for no reader. `omc internal
models` reads only the global config and `OMC_PROVIDER`, so it answers the
same from a temporary clone as from the user's project.

## 5. Compile, reconcile, finalize

**Compile (main session, mechanical).** The main session merges the draft
sets. It drops duplicates (two passes finding the same thing yields one
draft, the stronger body kept) and exact overlaps with an existing discussion
on the MR (same anchor, same point — every judgement call about what a thread
answers belongs to R), then ranks Critical → Important → Minor → Question. One draft
is added mechanically, from the diff's path list rather than any worker's
judgment: an MR that touches `.omc/` (the project's review rules, conventions
and context maps) gets an Important draft saying so, because it changes what
every later review is judged by. Every draft gets an id, its severity, its
kind (`code` for A, B, C and the mechanical draft; `design` for D), its
source pass, an anchor (`file:line` on a line inside the diff, or *general*),
the author-facing body, one private rationale line, and — for D's drafts —
the design point it rests on. The compiled drafts are written to the trail
(`comments.md`).

**Reconcile (one Review worker, R).** R reads the compiled drafts, the
design (`design.md`), the vetting (`vetting.md`), the author's material
(`material/`), the existing discussions, and the diff with the tree. For
each draft it returns one verdict with a reason:

- **drop** — the author's material already answers it (the spec, the
  ticket, an upstream MR, a thread, a code comment), it is a "consider X"
  with no concrete cost, it is a design preference rather than a problem, or
  it is style below a clear defect;
- **sharpen** — real, but the body asks or asserts more than the evidence
  carries; R rewrites it to the gap or defect actually shown, citing the
  author's own material where that makes the point ("the spec says X; the
  diff does Y");
- **keep** — as drafted.

A surviving draft must satisfy one of Decision 10's three tests — a real gap
between implementation and intent, a real quality problem, or a question the
author's material does not answer — and R names which. A question the
*unfollowed* material (index: not followed) might answer stays a Question but
says what it could not read. R may lower a severity (the draft keeps both
severities in the trail); it never raises one, and it never adds a comment
of its own. R is a separate worker, not the main
session and not D: a fresh judge is not anchored on drafts it wrote, its
inputs are the trail files alone so a human can rerun the judgement from the
trail, and it needs the whole material in context, which the main session
(whose terminal shows one paragraph) should never hold. R returns its
verdicts — per draft id: keep, sharpen with the new body, or drop; the
reason; which of the three tests a survivor meets — and the main session
writes them beside each draft in `comments.md`, of which it is the only
writer.

**Finalize (main session, mechanical).** For every kept or sharpened draft:
the body is put in its posted form — the severity prefix (`Critical: `,
`Important: `, `Minor: `, `Question: `), the body, the fixed footer

> — 🤖 Automated review comment (omc mr-review)

— and no body line starts with `/` (forges read such a line as a quick
action and reject or execute it). The body is written to the author in a
reviewer's voice, with the finding and what would close it, not the tool's
internal vocabulary (`grug:cut-point` is the rationale, not the comment). An
inline comment gets a private position derived from the captured unified
diff (`old_path`, `new_path`, `old_line`, `new_line`, `side`), which is what
posting sends; the public `file:line` alone is never a forge position. An
anchor outside the diff, ambiguous, or one the forge cannot represent
becomes a general comment that cites the `file:line` in its text — decided
here, never at posting time. Positions are derived only for survivors, so no
work is spent on what R dropped.

## 6. Review trail and terminal output

Everything the run reasons with goes to disk; the terminal gets a summary
(Decision 15). The trail is a directory under omc's home (`$OMC_HOME`,
default `~/.omc`):

```text
mr-reviews/<host>/<project-path>/<iid>-<head-sha7>/
  review.md      the run: MR link, reviewed head, target, passes run (and why
                 one did not), checkout used, the posting report (§7), and
                 the stop line when the run stopped
  material/      the author's material (§4.0): mr.md, one file per followed
                 link, and index.md — every link found, followed or not, why
  design.md      the design found (with its source) and its hardening, or
                 the design derived (each point tagged with its material), or
                 "intent not discoverable" and what was looked at
  vetting.md     implementation vs design: promises missed, scope beyond,
                 contradictions, each with the design point it rests on
  comments.md    every draft: id, kind, source pass, severity, anchor, body,
                 rationale, design point (D), private position (survivors),
                 R's verdict and reason, and after posting the note id and
                 link or the reason it was not posted
```

Five entries, one writer each: the main session writes `review.md`,
`material/` and `comments.md` (R's verdicts included — R returns them, §5);
D writes `design.md` and `vetting.md`, the only files any worker writes, and
nothing in the checkout. The trail is created before the first worker starts
(so a run that dies leaves what it had) and is never removed by the §3
cleanup; it is omc-owned state under `$OMC_HOME` like everything else there,
so `omc uninstall` removes it with the home — it is a record of the run, not
an archive. A second run on a moved MR gets a new directory (new
`<head-sha7>`), and the old one stays.

The per-comment fields in `comments.md` are fixed by Decision 17 — id, kind,
severity, rationale, design point, verdict with reason, posted note id and
link — because a later tuning skill reads them; the rest of the trail is
prose for a human asking "why did you say that".

The terminal shows, as the skill's final message:

```text
Posted 3 of 7 drafted comments to <MR link>: 2 code, 1 design — 0 Critical,
2 Important, 0 Minor, 1 Question. Trail: <path>
```

plus exactly one line when the run stopped — stale head, posting failure,
grug unavailable, target moved under the merge-base — naming the trail where
the detail is. The session's ordinary one line per step as it works
(checkout, material, passes, reconcile, post) is not output in this sense
and stays; what never reaches the terminal is the content — comments,
design, vetting, material, verdict reasons. A run with zero survivors prints
the same shape with zero posted and posts nothing (Decision 13).

## 7. Post — unattended

No confirmation and no question (Decision 12). Before posting, the forge's
head SHA is read again (on the GitLab MCP path, the MR's newest commit) and
compared with the reviewed head from §3. If it moved, nothing is posted: the
run writes its stop line — the MR moved since checkout, inline anchors would
not match, run again — into `review.md` and the terminal, and ends after
cleanup. The survivors stay in the trail; a fresh run reviews afresh
(resuming from a trail is not built, §12).

Posting is a ladder of three rungs (Decision 9 as amended by 13). Each rung
is entered on the failure of the one above, without asking, and the trail
records exactly which comments went up at each rung so nothing is
double-posted on a retry.

**Rung 1 — the all-in-one post**, the forge's own batching, so the author
sees one review event. It has one shape per forge, with no branch on what
the batch happens to contain:

- **GitLab** (MCP server): inline comments through the review-submission call
  (`submit_review`) in chunks of its limit (twenty today) with `summary` and
  `verdict` omitted — the tool then posts only the diff comments; a comment
  on a renamed file goes through `create_diff_note`, which carries both
  paths; general comments as plain notes (`create_note`). `glab` has no
  all-in-one method in v1: with `glab` this rung is unavailable, nothing is
  written, and the run goes straight to rung 2's individual posting; that a
  review method would have made one review event is written once to the
  trail as a suggestion, not printed as a choice.
- **GitHub**: one review (`gh api …/pulls/<n>/reviews`, event `COMMENT`)
  carrying the inline comments. GitHub requires a body on such a review, so
  the body is exactly the footer line — never a drafted summary. General
  comments are issue comments on the PR.

**Rung 2 — individually.** When the session's tool has no all-in-one method
(always `glab`), or when rung 1 fails with a permission or scope error, the
still-unposted comments are posted one by one: GitLab `create_diff_note` /
`create_note` per comment through the MCP server, or the MR `discussions`
endpoint (with the stored diff version's SHAs and the private position) and
the `notes` endpoint through `glab api`; GitHub `…/pulls/<n>/comments` per
inline comment with the reviewed SHA as `commit_id`, `…/issues/<n>/comments`
per general one. The forge's error from rung 1 is quoted in the trail. On
GitLab both rungs use the same tool and the same `api` scope, so a true
scope error recurs here and the run reaches rung 3 one call later; the rung
is still tried because a rung-1 failure may be the review method itself, not
the scope, and the ladder keeps one shape on every forge.

**Rung 3 — stop.** When individual posting fails too, the run stops: the
trail lists every surviving comment that was **not** posted with the forge's
error verbatim and the scope guidance from the forges' own documentation — a
GitLab personal access token needs the `api` scope (`read_api` reads the MR
but cannot write notes) and the user a project role that may comment; a
GitLab MCP server is re-authorised with a grant or token that carries `api`;
a `gh` token needs the `repo` scope (classic) or "Pull requests: Read and
write" (fine-grained), added with `gh auth refresh -s repo` or in the token's
settings page — and the terminal gets the one stop line pointing at the
trail. Nothing is retried on its own. The quoted error is what lets the user
tell a scope problem from a role problem when they read the trail.

A failure part-way through any rung is recorded per comment: posted ids with
their links, unposted ids with the reason. An inline position the forge
rejects leaves that comment unposted and recorded; it is never converted to a
general comment at posting time. No summary note is posted unless it is a
surviving comment. The forge tools expose `approve`, `unapprove`,
`resolve_discussion`, `post_duo_review` and `verdict` (GitLab) and `APPROVE`
/ `REQUEST_CHANGES` (GitHub); the skill names them only to forbid them (§1).
Replying to an existing discussion is forbidden the same way.

This is the first omc skill that writes to a forge. `create-mr`'s rule —
never call a forge API; the user opens the MR — stays as written for
`/omc:finish`; it exists so finish does not create MRs in the user's name
without asking. This skill's exception is explicit: the user invoked it on
this MR, every body is labelled automated (Decision 14), and every body
passed the reconcile bar (Decision 10). That bar is a weaker gate than the
per-comment approval it replaces — a model's judgement, not the user's eyes
— which is why the trail keeps every verdict and reason, and why the footer
says what the comment is.

## 8. Where future work plugs in (nothing built in v1)

Two directions, both out of scope, both kept open.

**Automatic runs** (Decision 8). §3–§7 need no human now: a checkout, the
material, the workers, the reconcile, an unattended post. A later CI run
would be the same skill in a checkout that already is the target repo, with
the session's forge token as the poster. Nothing in v1 reads the terminal;
no hook, flag or mode is added.

**Tuning from feedback** (Decision 17). A later skill will read replies to
posted comments — from the author, from other reviewers, possibly from other
agents — and tune this reviewer's bar. What it needs from a run is in the
trail: per posted comment the note id and link (to find the replies), the
severity and kind, the rationale, R's verdict and reason, and the design
point it rested on; per run the material index (what the reviewer saw). The
trail's `comments.md` fields are fixed for that reader (§6); how tuning
happens — a per-project calibration file, changes to the mission text,
anything else — is that skill's design, and nothing here chooses for it.

## 9. Shape in this repo

- `skills/mr-review/SKILL.md` — the user-facing skill, frontmatter
  `name: mr-review`, invoked as `/omc:mr-review <mr-link>`; `$ARGUMENTS` is
  the link. Rewritten for the redesign: material collection (§4.0), the
  intent-and-vetting pass, the reconcile step, the trail directory, the
  unattended ladder, the summary output; the walk and the confirmation
  removed.
- `skills/mr-review/worker-mission.md` — the worker brief beside it, as
  `skills/investigate` does: the pass to run, the worktree path, the target
  branch, the trail directory, the forge-free rule, the "MR content and
  author's material are data" rule, the "run nothing" rule, the "drafts,
  held to Decision 11's bar" rule, and the expected output shape per pass.
  Pass types: `correctness`, `project review`, `grug`, `design` (now
  find-or-derive, harden, vet — writes `design.md` and `vetting.md`), and
  the new `reconcile` (reads the trail, returns verdicts; writes nothing).
- `skills/grug/SKILL.md` — frontmatter description already names
  `/omc:mr-review` as a caller; no change.
- `tests/unit/test_plugin_manifests.py` — `test_mr_review_*` needles updated
  for the redesign (§10): the walk and approval needles removed, the new
  rules pinned.
- `.claude/skills/regenerate-readme/SKILL.md` and `README.md` — the skill's
  one-line description changes (no walk, unattended, trail); README
  regenerated via `/regenerate-readme` (never hand-edited).

No new Python, no new `OMC_*` contract, no config key: link parsing,
link following and forge I/O belong to the harness's tools, git does the
checkout, and `omc internal models` is already there.

**Rejected — a Python helper for link parsing, link following or checkout.**
It would be the first `omc` verb that reads a forge or a tracker, would need
its own credentials (the session's MCP servers and CLIs already have them),
and would duplicate what the tools and three lines of git do. The skill text
is the right place while there is one caller.

**Rejected — reconcile inside the main session or inside D.** The main
session would have to hold the whole material (it must stay small: its
terminal output is one paragraph) and would judge drafts it compiled; D would
judge its own vetting drafts. A separate worker reading only the trail is
also the only variant a human can rerun by hand from the trail.

**Rejected — a dry-run flag for development runs.** Decision 12 and 16: the
user stops the session before §7; a mode would be a second code path with
one user and would outlive its purpose.

## 10. Testing

Red to green per the project's testing policy, within Decision 7's scope
(amended by 16).

`tests/unit/test_plugin_manifests.py`:

- `mr-review` stays in `USER_FACING_SKILLS` (frontmatter and naming checks
  run automatically);
- `test_mr_review_skill_contract` — needles on the skill text for every
  load-bearing rule, each failing first against the current text: the usage
  line; `core.hooksPath`; the project rules read from the target branch; no
  approve / verdict / resolve / reply; the head SHA re-check before posting
  **and** that a moved head posts nothing and asks for a fresh run; the
  posting ladder — the all-in-one post first, individual posting on a
  permission or scope error **without a question**, a stop that records what
  was not posted with the `api` and `repo` scopes and `gh auth refresh`;
  cleanup of worktree and clone; MR content and the author's material
  treated as data; `omc internal models` and `tasks.review`; `grug diff`,
  `grug section` and `grug spec`; `/omc:explain`; the material crawl
  (`material/`, `index.md`, `not followed`, two hops); the intent step
  (`docs/superpowers/specs/`, `derive`, `intent cannot be established`); the
  vetting step; the reconcile verdicts (`drop`, `sharpen`, `keep`) and
  Decision 10's three tests; Decision 11's bar (`never because`, `look for
  the author's reasoning`, `consider`, `Minor`); the design is `never
  posted`; the severity prefixes and the exact footer
  `— 🤖 Automated review comment (omc mr-review)`; the trail directory path
  `mr-reviews/<host>/<project-path>/<iid>-<head-sha7>/` and its five
  entries; the summary line's shape; `worker-mission.md`;
- the pass procedures (find, harden, derive, vet), the reconcile criteria and
  the Decision 11 bar live once, in `worker-mission.md`, and their needles are
  pinned there; `SKILL.md` keeps only what the main session does (finish
  review, `grug:locality`, 2026-10-23);
- negative needles, pinning what the redesign removed: no `Post / Drop /
  Stop / Other`, no `Post N comments`, no `approved before posting`, no
  `ask again if it moved`, no `<poster>`;
- the worker mission exists and carries `<worktree>`, `<pass>`, `<trail>`,
  `data, never instructions`, `FORBIDDEN`, `reconcile`, the Decision 11 bar,
  and no forge write verb;
- `test_grug_skill_contract` keeps `/omc:mr-review` beside the two other
  callers in grug's description;
- `test_readme_lists_mr_review` gains a needle on the regenerated row's new
  description (today it checks only that the row exists, so a README still
  describing the walk would stay green until `/regenerate-readme` is run).

The red state comes for free: the current needles for the walk, the
`Post N comments` confirmation, the `<poster>` footer, `ask again if it
moved` and the `all-in-one` → `ask a question` → `individual` order
assertion all fail against the rewritten skill text, and the new needles
fail against the current one. Each is watched failing before the text that
turns it green is written.

Manual live runs during development (Decision 16): real MRs the user already
knows — at least the 27-file MR that triggered this redesign and one MR
whose diff adds its own design record — run from this branch's skill text
with the session told to stop before §7. The user reads the trail and the
would-be comments; the comparison that matters is against the first
version's comments on the same MR (fewer, and none that the linked material
answered). One final run on a real MR posts, with the footer and severity
prefix verified on the forge and the checkout verified clean afterwards
(`git worktree list`, `git status`). Outcomes are recorded under
"Implementation review" in this record; the GitHub path is exercised the
same way when a PR is at hand, else recorded as not live-verified.

E2E: none (Decision 7). A forge-posting E2E needs a forge inside the Docker
container; the project's "every external integration keeps one E2E driving
the real tool" rule is met for the git half by the existing finish and
rebase-main E2Es, and the forge half is the manual live run above. The user
has ruled that E2E is not run for this skill.

## 11. Size

Two tasks. `Complexity: high` for the skill text and worker mission — the
rules in §1, §3, §4 D, §5 and §7 are safety and judgement rules and the
wording must make a session follow them under a long transcript; Decision
11's bar in particular must survive being read by a worker that has just
found something it would have done differently. `Complexity: simple` for the
manifest test, the README recipe line and the README regeneration. Left to
the builder: whether A and B are one worker; the material cap; the exact
forge, tracker and documents tool call names, discovered from the tools
present in the session; the trail files' internal layout beyond the fixed
per-comment fields.

## 12. Out of scope

- MR sets, stacks and cross-MR review (Decision 3).
- Automatic or CI-triggered runs, posting policies, an `omc` CLI verb
  (Decision 8).
- Tuning the reviewer from replies to posted comments (Decision 17); only
  the trail fields it will read are fixed here.
- Approving, requesting changes, resolving or replying to existing threads.
- Resuming an interrupted or stale run from its trail; the trail keeps
  everything so nothing is lost, but a later run reviews afresh.
- A dry-run flag or mode (Decisions 12, 16).
- Running the MR's tests or build, or any tool from the MR's tree.
- Following links the session has no tool for; they are listed in the
  material index as not followed.
- Changes to `/omc:review`, `/omc:design`, `create-mr` or `grug`.
- A forge abstraction in Python; credentials handling of any kind.

## Implementation review

### 2026-10-23 — audit (Claude, Opus 5.5; Review worker on `tasks.review`)

| # | Severity | Kind | Record section | Where | Disposition |
|---|----------|------|----------------|-------|-------------|
| 1 | Important | code deviates (promise missing) | Decision 7, §10 | record had no live-run outcome | Live run done; outcome below |
| 2 | Important | record is stale | Decision 9, §7 rung 2 | `skills/mr-review/SKILL.md` §6 | User ruling: a missing batch method (always `glab`) posts individually under the batch confirmation, with the batch method printed once as a suggestion; Decision 9 and rung 2 amended |
| 3 | Important | record is stale | §7 rung 1, Decision 1 | `skills/mr-review/SKILL.md` §6 (`glab` bullet) | Record amended: `glab` has no rung 1 in v1 |
| 4 | Important | code deviates | §3 step 6 | `skills/mr-review/SKILL.md` §2 (sweep) | Code fixed: OS advisory lock (unholdable across harness steps, so the sweep never ran) replaced by an owner marker (`$PPID` + start time); record amended to describe it |
| 5 | Important | record is stale + code deviates | §3 step 6, §4 C | `skills/mr-review/SKILL.md` §3 item 3 | Code fixed: private `--shared` clone and abort-on-any-target-move removed; grug runs in the run's worktree; abort only when the merge-base moved. Record amended: `origin/<target>` may advance through grug's fetch |
| 6 | Important | code deviates (excluded refusal added) | §3 step 5 | `skills/mr-review/SKILL.md` §2 (checkout) | Code fixed: stop-on-filter refusal (blocked every Git LFS repo) replaced by `GIT_LFS_SKIP_SMUDGE=1`; record amended |
| 7 | Minor | code deviates | §7 head re-check | `skills/mr-review/SKILL.md` §6 | Listed: "post anyway" on a moved MR cannot express a historical position through MCP methods that take only path and line; such inline comments stay unposted |
| 8 | Minor | record is stale | §3, §5, §8 | `skills/mr-review/SKILL.md` §1, §2, §4 | Record amended: diff-version SHAs, private positions |
| 9 | Minor | code deviates | §7, §12 | `skills/mr-review/SKILL.md` opening and final rules | Code fixed: `post_duo_review` and replying to existing discussions named in the forbid list |
| 10 | Minor | code deviates | §4 D | `skills/mr-review/SKILL.md` §3 item 4 | Code fixed: the main session reads the ticket and hands it to the design worker as data |
| 11 | Minor | record is stale | §9 | `.claude/skills/regenerate-readme/SKILL.md` | Record amended: recipe file listed |
| 12 | Minor | test coverage | §10 | `tests/unit/test_plugin_manifests.py` | Rung-3 `permissions are unavailable` needle added; vague needles left as is |

**Live run (Decision 7, §10).** One real self-hosted GitLab MR (a colleague's one-file
schema change), run end to end from this branch's skill text through the GitLab MCP
server. Target project ≠ current checkout, so the partial-clone path ran: an ownership marker,
`--filter=blob:none` clone, forge-side head ref fetched and captured, detached hook-disabled
worktree. Correctness, grug and design passes ran on the Review model, with no project pass
(no `.omc/skills/review/SKILL.md` on the target). grug's merge-base guard held (the target
had advanced and the merge-base was unchanged). Two existing-discussion duplicates were dropped,
and six comments were compiled into the review file and printed. The walk gave five Post and one
Drop, followed by one batch confirmation. The head re-check passed, and rung 1 posted four inline
comments through `submit_review` (no summary, no verdict) and one general comment through
`create_note`. On the forge all five carry the footer, and the inline ones are anchored at the
reviewed head on the intended new lines. Afterwards the temp parent was empty and the user's
checkout was unchanged (`git worktree list`, `git status`). Found by the run and fixed in this
audit: the MCP read exposes no head or diff-version SHAs, so the head check now uses the MR's
newest commit on the MCP path (version SHAs are needed only for `glab` posting). Not
established: whether `submit_review` shows up on the forge as one review event (its response lists
one `create_diff_note` per comment). GitHub: not live-verified (no PR at hand).

### 2026-10-23 — second live run, stopped before posting (trigger of the redesign)

A second real self-hosted GitLab MR, after the first version was published: a
27-file service change whose description linked a design spec in another
repository and a ticket, and whose code pinned a dependency to a commit of an
unmerged upstream MR. Checkout, passes and compile ran as designed. The user
stopped the walk before anything was posted. Observed: several comments asked
what the linked spec and the upstream MRs likely already answered — the
review had opened neither, nor the ticket's references; the per-comment
dialogs were unwanted; the footer and the severity placement were not the
wanted format. Dispositions: Decisions 10–18 and the rewritten §4–§8, §10.
Nothing posted; checkout verified clean.

## Deliberate complexity

- §10, `grug:test-level`: the automated tests are needle checks on the skill
  text; the only test that drives the real tools is the manual live run.
  grug's simpler alternative — one E2E running §3–§5 against a local bare
  repo and asserting the trail, forge half still manual — was declined
  by Decision 7 (needle tests plus live runs, no E2E). Waived citing that
  decision; the live runs' outcomes are recorded in this record.
- §3 step 6, `grug:simple-concurrency` (finish review, 2026-10-23): the startup sweep's
  owner marker (`$PPID` + start time) is kept over "list leftovers and report them"
  because §3 step 6 promises automatic removal of a dead run's leftovers, and the marker is
  the least state that tells a dead run from a concurrent live one, so the sweep never
  deletes another review's worktree.
- §5, `grug:80-20` (redesign hardening, 2026-10-23): reconcile runs as a fifth Review
  worker that re-reads the material, design and diff the intent worker already held,
  instead of the intent worker reconciling its own drafts. Waived by the user (Decision 19):
  the over-asking design questions that triggered the redesign need an independent filter.
