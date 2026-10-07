# Pin the GitNexus analyze label and never escalate on a moved checkout: end the index ownership flap in `omc watch`

**Date:** 2026-10-07
**Slug:** `fix-watch-index-ownership-flap`
**Status:** design, hardened with `/omc:explain` and the grug lens
**Predecessors:** `2026-09-27-stale-knowledge-snapshot-self-heal-design.md` (the
repair ladder this record gates), `2026-08-03-fix-gitnexus-watcher-incremental-sync-design.md`
(the store-inversion heal this record keeps and amends),
`2026-10-07-fix-gitnexus-stale-index-clean-loop-design.md` (sharing off; the
flat `.gitnexus/` store this record pins)

## 1. Problem

`omc watch --enable-documentation --rebase --auto-build` on the primary
checkout of this repository flapped on GitNexus index ownership. One tick:

```
→ refreshing GitNexus index (incremental)
✓ index refreshed
✗ index still stale after analyze (store-inverted) — destroying and rebuilding
· stale docs mirror deleted
✗ rebuilt index is not owned by 'main' — not claiming success
```

The next tick, after `✓ rebased main: 747264a..f9fe2cb`:

```
✗ GitNexus index is owned by 'feature/regenerate-readme', not 'main' — destroying and rebuilding
✓ index rebuilt for main
→ regenerating documentation via …
```

Cost: two full re-indexes, the docs mirror deleted, and a full wiki
regeneration, for a store that was never frozen. Nothing was broken before the
first line; the repair ladder manufactured the inversion it then healed.

## 2. Root cause (confirmed from evidence)

**What moved.** The primary checkout's HEAD reflog shows `git switch -c
feature/regenerate-readme` plus one commit at 11:11:56 local and `switch main`
at 11:12:17 local on 2026-10-07. A manual branch switch in the primary, 21
seconds long, overlapping a watch tick.

**GitNexus side (fork 24af4f60).** `analyze` stamps the flat store with the
branch it sees at analyze time: `src/storage/git.ts:getCurrentBranch` is `git
rev-parse --abbrev-ref HEAD`; `src/core/run-analyze.ts` (~line 1243) sets
`branchLabel = options.branch ?? checkedOutBranch` and writes `branch:
branchLabel ?? existingMeta?.branch` into the metadata. When an explicit
`--branch X` disagrees with the checkout it throws before any write: `--branch
"X" does not match the checked-out branch "Y". Check out "X" before indexing
it, or omit --branch to index the current branch.` (~line 1239). The help text
(`src/cli/index.ts` ~116): "Pin the working tree into a dedicated per-branch
index slot ... Without this flag, analyze always updates the workspace index,
which follows the checked-out working tree."

Placement of an explicit label (`src/storage/branch-index.ts:resolveBranchPlacement`):
label null → flat; flat store absent or unstamped → flat (the label adopts it);
flat owned by the same label → flat; flat owned by another branch →
`branches/<slug>/` sub-slot.

**omc side.** `watch.py:_tick` reads `current_branch` once at tick start and
refuses off-base there (`off-branch:<branch>`). From that point on nothing
re-checks: `gitnexus.py:refresh_knowledge` runs the incremental `analyze`,
GitNexus stamped the branch it saw (`feature/regenerate-readme`), the
recomputed verdict read `store-inverted`, and the ladder escalated to
`_destroy_and_rebuild`: mirror deleted, `clean --force`, full `analyze`, still
inside the 21-second window, so the rebuilt store carried the feature stamp
again and the tick ended with "not owned by 'main'". The next tick found a
genuinely inverted store and healed it the expensive way.

A time-of-check-to-time-of-use race between the repair ladder and anyone
switching branches in the primary. Not a GitNexus bug. Not a shared-store
regression: `GITNEXUS_SHARED_STORE=off` is in effect, there is no omc store
under `~/.gitnexus/stores`, no symlinked `.gitnexus`, no git hooks.

**Why the pin is safe on every omc path.** omc never runs `analyze` while the
flat store is owned by another branch without a preceding `clean --force`: a
`store-inverted` verdict always takes the rebuild path, and `_destroy` judges
`clean --force` by metadata absence before the rebuild `analyze` runs. So with
`--branch <base>` the label always finds the flat store absent, unstamped, or
owned by `<base>`, and placement is always flat. The sub-slot branch of the
placement rule is unreachable from omc.

## 3. Decisions taken during brainstorm

| # | Decision | Why |
|---|----------|-----|
| D1 | Seed "pin + never escalate on a moved checkout" over "re-check only" and "guidance only" | Re-checking alone leaves a window between the check and GitNexus's own stamp; the pin makes GitNexus refuse inside that window. Guidance alone (an AGENTS.md line) would not have helped: the trigger was a manual action |
| D2 | Both callers of `refresh_knowledge` get the behaviour: `omc watch` (`watch.py:_refresh_index` from `_tick`) and `omc internal gitnexus refresh` (`internal.py:_knowledge_refresh`) | One repair function, one contract; the internal verb already refuses off-base before the lock and should refuse mid-repair too |
| D3 | A moved checkout blocks that repair's index update entirely and prints a `✗` warning, not a quiet `·` line | A silent skip hides a 30 s loop that is doing nothing; the user asked for a visible warning |
| D4 | On the synced path (after a rebase or ff-merge) the tick still counts as `synced`; the post-watch hook and auto-build still run | User: "it should block index update altogether and print a warning. It should still build." The code moved; only the knowledge did not |
| D5 | No `.omc/config/AGENTS.md` guidance line | The trigger was a manual switch, not an agent; prose does not fix a race |
| D6 | `OMC_KNOWLEDGE` codes, `store-inverted` semantics and `snapshot_freshness` are unchanged | A moved checkout is a state of this repair, not a freshness fact a consumer should branch on |
| D7 | Upstream `wiki --branch` is a follow-up, not designed here | GitNexus `wiki` has no label; omc's pre-wiki check covers omc's side |
| D8 | Complexity hints for the plan: pin `simple`; detection and callers `medium`; unit tests and stub rework `medium`; E2E step `simple` | Sets the coding tier per task under the model-selection rule |

## 4. Design

### 4.1 Pin the analyze label

`ANALYZE_ARGS` in `src/omc/gitnexus.py` becomes a function:

```python
def analyze_argv(ctx, base) -> list[str]:
    return gitnexus_argv(ctx, "analyze", "--skip-agents-md", "--skip-skills", "--branch", base)
```

Both analyze sites use it: the incremental `analyze` in `refresh_knowledge`
and the rebuild `analyze` in `_destroy_and_rebuild`. No other omc code runs
`analyze` on the project (`dependency.py` indexes dependency checkouts with
`--index-only` and is not touched).

What the pin buys. GitNexus compares the label with the checkout it is about
to index and throws before writing when they differ (§2). With the pin, the
one write omc cannot gate itself, GitNexus's own stamp, is gated by GitNexus.
A plain analyze can only ever be told after the fact; a pinned analyze refuses
up front. The refusal exits non-zero, and omc's existing rule applies:
"exit codes are narrated, never trusted", so the refusal lands as a failed
analyze whose verdict then decides (§4.2: the analyze helper re-checks the
branch after a non-zero exit and raises when it moved).

The verified GitNexus contract (the mismatch throw, the four placement rules,
and why omc only ever reaches the flat placement) lives as a comment at the
`analyze_argv` definition, per the project rule that provider and GitNexus
quirks are comments at the exact code site. The gitnexus-index skill delegates
to the CLI, so no SKILL text gains the literal `--branch`
(`test_plugin_manifests.py` needles, 2026-09-27 record §6).

### 4.2 Moved-checkout detection at the point of use

A new exception in `gitnexus.py`:

```python
class CheckoutMoved(Exception):
    branch: str   # what the primary is on now
    base: str     # what it must be on
```

and one small helper, `_require_base(ctx, root, base)`, which asks
`wtconfig.current_branch` (the same probe `_tick` and `_knowledge_refresh`
already use; `wtconfig` imports only `errors` and `toolctx`, so there is no
cycle) and raises `CheckoutMoved` when the answer is not `base`. The raiser
narrates nothing; the caller owns the message (§4.3). It is a plain
`Exception`, not an `OmcError`: `OmcError` means "omc cannot proceed, exit
1", while a moved checkout is a per-repair condition that `omc watch`
survives and retries.

The check sits on the things that write. `refresh_knowledge` writes in four
places, and each one checks on entry:

- **`_destroy_and_rebuild`**, before `_clear_mirror`. Every `store-inverted`
  verdict and every "still stale after analyze" escalation routes here, so
  this one check covers the two places the incident destroyed things. On
  base, a `store-inverted` verdict is a third-party writer and the store is
  genuinely frozen: today's `clean --force` heal runs unchanged, and the
  existing `✗ rebuilt index is not owned by 'main' — not claiming success`
  line keeps its meaning (with the pin, a wrong stamp after a rebuild can
  only come from a third-party writer, never from a move; §2).
- **the `--reset-gitnexus` path**, before its `_clear_mirror` and `clean`.
- **the pinned `analyze`**: the incremental call and the rebuild call go
  through one small analyze helper that builds the argv with `analyze_argv`
  (§4.1), checks before the run, runs it, and checks again when the run
  exits non-zero. The second check is where the pin's refusal lands. On
  base, a failed analyze keeps today's behaviour (narrate, let the verdict
  decide).
- **`_run_wiki`**, before it starts. `wiki` has no label (D7) and reads
  whatever the flat store says; a wiki built from a moved checkout's graph
  would be mirrored as current.

A raise aborts the repair at once: no `clean --force`, no mirror deletion,
no wiki. `finish()` does not run, so neither `✓ knowledge is current` nor a
mirror restore is claimed for a repair that did not happen. `clean --gc`
after a successful pointer-case heal is not gated: by then the verdict is
fresh and `--gc` touches stores, not the checkout's index.

Why on the writers and not one re-check up front. The incident's window was
21 seconds; the ladder runs two analyzes and a clean inside it. A single
re-check at the top of `refresh_knowledge` leaves every later step exposed,
and a check woven into each branch of the ladder spreads one rule over the
whole function. Putting it on the four writers keeps the rule where the
action is, so a future step that writes inherits it by calling the same
helper. Each check is one `git rev-parse` (milliseconds). When the checkout
is on `base` at every check, behaviour is byte-for-byte today's.

### 4.3 Callers

**`omc watch` (`watch.py`).** `_refresh_index` is already the one thin
wrapper every `_tick` path calls. It gains the single `except CheckoutMoved`
in watch: it prints the warning and returns the moved signal (the caught
exception object, which carries `branch`) instead of a `Freshness`. The
exception never crosses into `_tick`, so there is no call site to forget and
the loop cannot crash on it. The warning:

```
✗ primary moved to '<branch>' mid-tick — knowledge snapshot NOT updated; retrying when it is back on <base>
```

What each path does with the signal:

- Up-to-date paths (`--once` and the heal) return the quiet token
  `off-branch:<branch>`, the same token the tick-start guard returns. On the
  next tick the guard sees the checkout still away, computes the same token,
  and `quiet()` prints nothing because the token did not change. A 30 s loop
  therefore warns once per move, then stays silent until the checkout is
  back on `base`. `--once` returns 0 having warned; no hooks run, because
  nothing synced or refreshed.
- Reset progress is independent of the outcome token. `run_watch` creates
  a `ResetProgress` per tick and passes it through `_tick` / `_refresh_index`
  to the optional `refresh_knowledge(reset_progress=...)` argument. Its
  `attempted` flag becomes true only after the reset's base guard, immediately
  before destructive work. A move before that guard leaves `reset_pending`
  set, including on synced ticks, and prints the existing `· reset pending`
  line (or `· reset not applied` in once mode). A reset that entered destructive
  work is consumed even if a later analyze or wiki guard detects movement:
  attempting reset does not promise successful repair, and must not trigger
  another destructive reset automatically.
- Synced paths (after `rebase --autostash` or `merge --ff-only`) retain their
  outcome: the warning has printed and the tick still returns `synced` (D4).
  The post-watch hook and auto-build run as today. The code on disk did move
  to the new base commit; only the knowledge snapshot lags, and the next
  on-base tick applies any still-pending reset or uses the normal repair ladder.

**2026-10-07 implementation-review amendment:** the original assumption that
unchanged token-based reset consumption sufficed was incorrect. Sync success
and reset entry are separate facts; the explicit progress flag preserves both.

**`omc internal gitnexus refresh` (`internal.py:_knowledge_refresh`).** The
verb already refuses off-base before taking the busy lock. It now also catches
`CheckoutMoved` around `refresh_knowledge`, prints the same existing line,
`error: refresh requires the primary checkout to be on <base> (currently
<branch>)`, and returns 1. The busy lock is released by its context manager;
no `OMC_KNOWLEDGE` line is printed, because no verdict was reached.

### 4.4 Verdict contract unchanged

`OMC_KNOWLEDGE` codes, the `store-inverted` reason and its semantics, and
`snapshot_freshness` are untouched (D6). `CheckoutMoved` is a control-flow
signal between `refresh_knowledge` and its two callers, never a reason code.
Consumers of the verdict (`omc start`, the proxy, `rebase-main`) see nothing
new.

### 4.5 Narration before and after

For the incident, today's five lines in §1 become:

```
→ refreshing GitNexus index (incremental)
✗ primary moved to 'feature/regenerate-readme' mid-tick — knowledge snapshot NOT updated; retrying when it is back on main
```

then silence while the checkout is away (the tick-start guard returns the
same quiet token), then a normal `→ refreshing GitNexus index (incremental)`
/ `✓ index refreshed` once it returns. No mirror deletion, no `clean
--force`, no wiki run.

### 4.6 Documentation

- This record.
- `docs/superpowers/specs/2026-08-03-fix-gitnexus-watcher-incremental-sync-design.md`:
  amendment note under its Heal step 3 ("Full `analyze …` running on `<base>`
  claims the default store and stamps `branch: <base>`") saying that the
  analyze now carries `--branch <base>` (its `ANALYZE_ARGS` is now
  `analyze_argv`) and that a moved checkout aborts the heal, pointing here.
- `docs/superpowers/specs/2026-09-27-stale-knowledge-snapshot-self-heal-design.md`:
  amendment note under §2 ("One repair function") recording that
  `refresh_knowledge` raises `CheckoutMoved` and what its callers do with it,
  and that the record's `_heal_store` name is now the body of
  `_destroy_and_rebuild`.
- The GitNexus contract comment at `analyze_argv` (§4.1).
- No `watch.py` module docstring change and no README change. The README
  recipe reads the watch docstring for its "Keep knowledge fresh" section, so
  a docstring edit would be README drift and a recipe run. The docstring's
  existing sentence, "off-branch checkouts are never touched in any mode",
  is exactly what this record makes true mid-tick as well; the new warning
  line is narration, not a new mode.

### 4.7 Files touched

`src/omc/gitnexus.py` (`analyze_argv`, `CheckoutMoved`, `_require_base`
importing `wtconfig.current_branch`, the `_run_analyze` helper, the four
writer checks in `refresh_knowledge` / `_destroy_and_rebuild` / `_run_wiki`,
and the `ResetProgress` dataclass behind the new optional
`refresh_knowledge(reset_progress=...)` argument), `src/omc/watch.py`
(`_refresh_index` catch and warning; the two up-to-date sites in `_tick` map
the signal to the token; `run_watch` creates a `ResetProgress` per tick and
consumes `reset_pending` from its `attempted` flag instead of the tick
token), `src/omc/internal.py` (`_knowledge_refresh` catch);
`tests/unit/test_watch.py` (stub rework, new cases),
`tests/unit/test_gitnexus_refresh.py`, `tests/unit/test_internal.py` (its
`_HEALING_NODE` stub and the refresh verb's tests),
`tests/e2e/test_e2e_gitnexus.py`; this record and the two amended records.
No fork file, no Dockerfile, no SKILL.md.

## 5. Error handling and edge cases

- **Detached HEAD in the primary.** `current_branch` returns `HEAD`, which is
  not `base`; the tick guard and every check point refuse. GitNexus itself
  would accept an explicit label on a detached checkout (its `checkedOutBranch`
  is null there), so omc's checks are what keep a detached primary from being
  stamped `main`.
- **The move happens between the last check and GitNexus's own `rev-parse`.**
  GitNexus sees the new branch, the label disagrees, it throws. Exit non-zero;
  the analyze helper's post-failure check raises `CheckoutMoved`. Nothing was
  written.
- **The move happens after GitNexus's `rev-parse` but before its write.**
  GitNexus stamps `base` as instructed while indexing the moved tree's
  content. The flat store's stamp is right and its content is one branch off;
  the verdict reads it as fresh. This is the residual window and it is GitNexus's
  to close (its own TOCTOU); omc's next on-base incremental analyze re-indexes
  the working tree and corrects the content. Recorded, not designed around.
- **Analyze fails on base for another reason.** The post-failure check finds
  the checkout on `base` and does nothing; the verdict decides and the ladder
  escalates as today.
- **Third-party writer stamps a foreign branch while the checkout is on
  `base`.** `_destroy_and_rebuild`'s entry check finds `base`; the heal runs
  `clean --force` and the pinned rebuild, which stamps `base`. Exactly today's
  heal; the regression guard in §6 case 5 covers it.
- **`--reset-gitnexus` with a move before the first write.** The reset path's
  entry check raises before `_clear_mirror` and `clean`; nothing is destroyed;
  `reset_pending` stays set even after a successful sync (§4.3). If movement
  happens after reset entered destructive work, it stays consumed; returning
  to base permits ordinary repair without a repeated reset.
- **Move during the wiki run itself.** Not detected; `wiki` has no label (D7).
  The wiki finishes from the flat store's graph, which still carries the
  pre-move content and the `base` stamp. The next documentation tick compares
  the wiki against the index commit as today.
- **`current_branch` returns `None`** (git error, unreadable repo). Today's
  tick guard already treats that as off-branch (`"" != base`); `_require_base`
  does the same, raising with an empty branch name. The warning then reads
  `moved to ''`, which is honest about what git said.

## 6. Testing

Red → green per `.omc/config/AGENTS.md`; no skips. Unit tests run under
`just check` with the `node` stub, no network.

**Stub rework (`tests/unit/test_watch.py::_ctx_with_healing_node_stub`).** The
stub becomes faithful to the verified contract: on `analyze … --branch
<label>` it compares the label with the repo's real checked-out branch
(`/usr/bin/git rev-parse --abbrev-ref HEAD`, the absolute path the stub
already uses), refuses with GitNexus's message and exit 1 when they differ,
and stamps the label otherwise. Knobs: `move_to="feature/x"` makes the stub
switch the repo's branch at the start of its first analyze call (a move
during analyze; with the pin every analyze-time move is the first analyze
the stub sees, because a move during the incremental run aborts before any
rebuild and a seeded inverted store goes straight to the rebuild analyze);
`rogue_stamp` writes a foreign stamp ignoring the label (a third-party
writer). A `git` wrapper installed as `ctx.git_bin` (`OMC_GIT_BIN`, read by
`ToolContext.from_env`) that logs and forwards to `/usr/bin/git` but switches
branch on the first `fetch` simulates a move between the tick guard and the
repair; `tests/unit/test_internal.py::_gitnexus_env_with_config` already has
the forwarding shape.

Every other `node` stub keeps its fixed stamp but must accept the new argv:
the four shell stubs (`_ctx_with_node_stub`, `_ctx_with_healing_node_stub`,
`_ctx_with_shared_store_node_stub` in `test_watch.py`; `_HEALING_NODE` in
`test_internal.py`) match the analyze call with an end-anchored `case`
pattern `*" analyze --skip-agents-md --skip-skills")`, which stops matching
once `--branch main` follows. They gain a trailing `*`; without that change
the stubs silently stop stamping and dozens of tests go red for the wrong
reason. The three exact-list assertions in `test_gitnexus_refresh.py`
(`_commands(calls) == [["analyze", "--skip-agents-md", "--skip-skills"]]`)
gain the two tokens. The two heal-path substring counts in
`test_gitnexus_refresh.py` (`test_stale_index_heals_with_one_incremental_analyze`,
`test_inverted_store_destroys_first_exactly_one_analyze`) became exact-argv
list assertions ending in `--branch main`, the stricter form the project's
testing policy prefers; the remaining `.count("analyze --skip-agents-md
--skip-skills")` assertions keep matching and are left alone.

**Cases.**

1. Both analyze invocations (incremental and rebuild) carry `--branch main`
   (exact argv on the recorded calls).
2. Move between the guard and the repair → no `node` call at all, token
   `off-branch:feature/x`, exactly one `✗ primary moved` line.
3. Move during the incremental analyze → stub refuses; no `clean`; docs mirror
   intact; `meta.json` unchanged; no "destroying and rebuilding" line;
   `--once` exits 0.
4. Move during the rebuild analyze → warning; no `✓ index rebuilt`; no "not
   owned by" line.
5. Rogue foreign stamp while on base → heals exactly as today (`clean
   --force`, pinned rebuild, `✓ index rebuilt for main`).
   `test_heal_wrong_stamp_never_claims_success` is rewritten onto the
   `rogue_stamp` knob (its `analyze_stamps` knob goes away with the faithful
   stub) and keeps asserting the "not owned" line when the rogue stamp
   survives the rebuild.
6. `--reset-gitnexus` with a move before the first write → nothing destroyed,
   `· reset pending` printed, no `clean` call.
7. `omc internal gitnexus refresh` with a move during analyze → exit 1, the
   existing `error: refresh requires the primary checkout to be on main`
   text, busy lock free afterwards.
8. Move before the wiki (documentation on, index fresh, wiki behind) → no
   `wiki` call, warning.
9. Synced path with a move during the repair → warning AND the post-watch
   hook fires (`_seed_hook` style assertion on its side effect).
10. Both synced paths with a move before reset → snapshot intact, hook and
    auto-build artifacts present, exactly one eventual reset after returning
    to base; once mode reports reset not applied. A move after reset's clean
    on either synced path or an up-to-date tick never repeats that reset.

**E2E (`tests/e2e/test_e2e_gitnexus.py::test_index_then_explain_on_real_repo`).**
One step added after the index assertions, in the same container: switch
`/repo` to a throwaway branch, run the real `node <CLI> analyze
--skip-agents-md --skip-skills --branch main`, assert non-zero exit, the
mismatch message, and unchanged metadata (`lastCommit` still HEAD's SHA, no
`branches/` sub-directory under `/repo/.gitnexus`); run `omc internal gitnexus
refresh` there and assert exit 1; switch back to `main`. Seconds, no LLM, no
new container. It pins the GitNexus contract against the real pinned CLI, so
a future fork bump that changes the mismatch rule fails here first.

**Red first.** On current code: case 1 fails (no `--branch` in argv); case 3
shows the stub stamping the moved branch, `store-inverted`, and the
"destroying and rebuilding" line; the E2E step fails because the plain
analyze has no mismatch to refuse. The faithful stub itself is what makes the
reproduction honest: today's stub stamps a fixed label and cannot express the
race.

## 7. Out of scope and follow-ups

- Upstream GitNexus `wiki --branch` (D7). The pre-wiki check covers omc's
  side; a move during the wiki run stays undetected (§5).
- Any change to `OMC_KNOWLEDGE`, `store-inverted` or `snapshot_freshness` (D6).
- An AGENTS.md guidance line about switching branches in the primary (D5).
- Hook and auto-build behaviour on a moved checkout (D4): they keep running on
  a synced tick.
- GitNexus's own window between its `rev-parse` and its write (§5); not omc's
  to close.
- `dependency.py`'s `analyze --index-only` on dependency checkouts: those are
  omc-owned clones nobody switches; not pinned here.

## 8. Hardening notes

The primary's knowledge snapshot was one commit behind `origin/main` with no
generated wiki during hardening (`OMC_KNOWLEDGE` reported `index-behind`,
`wiki-missing`), so the graph was used for structure (callers, call sites,
blast radius) and every claim was checked against the code under `src/omc/`,
the tests, the records under `docs/superpowers/specs/`, and the fork source at
the pinned commit 24af4f60.

**§4.1 (explain + grug).** Explain confirmed `ANALYZE_ARGS` has exactly two
users, both in `gitnexus.py`, that `dependency.py` does not use it, and that
the gitnexus-index skill text is asserted to carry no analyze flags. It found
the test hazard now recorded in §6: four shell stubs match the analyze argv
with an end-anchored `case` pattern and three assertions compare exact argv
lists. Grug found nothing: a function with two users and a comment the
project's own rule asks for.

**§4.2 (explain + grug).** Explain confirmed `refresh_knowledge` has exactly
two callers, that `wtconfig` is a leaf (so `_require_base` can reuse
`current_branch` with no cycle; the brainstorm had sketched a fresh
`_git_out` probe), that `_run_wiki` and `_destroy_and_rebuild` each have one
caller, and that the watch loop has no exception guard. It also established
that, with the pin, "rebuilt store not owned by base" can never be caused by
a move. Grug raised two Important findings, both fixed: `grug:locality` (five
check points woven through the ladder's branches; now the check sits on the
four writers) and `grug:say-no` (the brainstorm's check (d) guarded a case
the pin already prevents; dropped, today's "not owned" line stays as the
third-party-writer path).

**§4.3 (explain + grug).** Explain traced the token handling in `run_watch`
(`reset_pending` survives an `off-branch:*` token; hooks gate on
`synced`/`refreshed`) and corrected the record's first draft: after the
warning, the tick-start guard prints nothing on the following ticks because
`quiet()` sees the same token, so the loop is silent until the checkout
returns. Grug raised one Important `grug:locality` finding, fixed: four
textual `except` blocks in `_tick` became one catch in `_refresh_index`, the
wrapper that already is "the ONE code path", so the exception never reaches
`_tick` and no call site can be forgotten.

**§4.4 to §4.6 (explain + grug).** Explain listed the six `snapshot_freshness`
callers (none sees a change), and found that the README recipe reads the
`watch.py` module docstring; the record now states why the docstring and
README stay untouched. Grug found nothing.

**§6 (explain + grug).** Explain confirmed the forwarding-git-stub shape
already exists in `test_internal.py`, that `OMC_GIT_BIN` is read by
`ToolContext.from_env`, that the E2E container is configured so `omc internal
gitnexus refresh` runs there, and that the project's testing policy fixes the
test level (stub plus one real-tool E2E). Grug raised one Important
`grug:say-no` finding, fixed: the stub's "Nth analyze call" knob had no
reachable N other than 1 once the pin is in.

**Whole record (explain + grug).** Explain found no conflict with the
2026-09-27 ladder, the 2026-08-03 heal, the 2026-10-07 sharing-off rule, or
the `OMC_KNOWLEDGE` contract; the stale check-point letters left in §4.1 and
§5 after the §4.2 rewrite were replaced with the writer names. Grug: zero
cross-section findings; nothing waived.

## Implementation review

**2026-10-07, auditor: Claude (Fable 5.1) via `/omc:audit`.** Review worker
walked the record, the plan and the branch diff against `origin/main`.
Focused unit suites green (169 passed); `just check` 1504 passed; `just
build` clean. No Important findings. Two Minor findings, both `record is
stale`, both amended in this record (no row of §3 is contradicted):

- Minor, record is stale, §4.7: the file inventory omitted `_run_analyze`,
  the `ResetProgress` dataclass behind `refresh_knowledge(reset_progress=...)`
  (`src/omc/gitnexus.py:64-82`, `:443`, `:474-476`) and `run_watch`'s
  reset-consumption rewrite (`src/omc/watch.py:450-465`), the load-bearing
  fix of commit `c1d8f07`. Disposition: §4.7 amended.
- Minor, record is stale, §6: the sentence claiming every `.count("analyze
  --skip-agents-md --skip-skills")` assertion was left alone was wrong for
  the two heal-path tests, which became exact-argv lists
  (`tests/unit/test_gitnexus_refresh.py:66-68`, `:116-118`). Disposition: §6
  amended to describe the stricter assertions.

Noted, not a finding: §6 says the moving `git` wrapper is installed through
`OMC_GIT_BIN`; the test assigns `ctx.git_bin` directly
(`tests/unit/test_watch.py:1427-1437`). Same effect.

Verified conforming: §4.1 pin at both analyze sites with the fork-contract
comment; §4.2 four writer checks and the post-failure re-check; §4.3 single
catch in `_refresh_index`, byte-identical warning, synced ticks keep hook and
auto-build (D4), `internal.py` exits 1 with the existing error and no
`OMC_KNOWLEDGE` line; §4.4 verdict contract untouched (D6); §4.6 both
predecessor amendments present, no docstring, README, SKILL.md or AGENTS.md
change (D5); §5 edge cases and all ten §6 cases plus the real-CLI E2E step
have owning tests; §7 exclusions respected; AGENTS.md invariants hold (no new
`subprocess` import, no `shell=True`, no skips, absolute stub paths).

## Deliberate complexity

None.
