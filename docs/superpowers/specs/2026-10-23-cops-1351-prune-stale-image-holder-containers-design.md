# E2E runner: prune stale image holders automatically

Date: 2026-10-23 · Status: design record (the branch and this file's name
carry the ticket slug the `design-record` gate matches; the body does not)

Amends the housekeeping half of `scripts/e2e.sh`: holder containers and the
E2E images they pin now expire 72 h after the holder was created, once its
tree is no longer the one being run, and every runner mode prunes the
expired ones before it starts. The
manual `prune` mode (`just e2e-prune`) is unchanged. Everything the
2026-10-01 plan (`docs/superpowers/plans/2026-10-01-fast-e2e-and-review-gates-plan.md`)
says about images keyed by tree content, snapshots and Ryuk still stands.

## 1. Problem and goal

The E2E runner pins each image it needs with an idle `sleep infinity`
"holder" container (labels `omc.e2e=holder`, `omc.e2e.source=<sid>`), so
Docker Desktop's image GC — kubelet's eviction manager under disk pressure
deletes every container-less image every ~10 s, observed 2026-10-06 — cannot
remove an image mid-run. Two sites create holders: `scripts/e2e.sh:hold_image`
for the base image (`omc-e2e-holder-base-<sid>`, pinning `omc-e2e:<sid>`) and
`tests/e2e/stages.py:hold_image`, called by `stages.snapshot` right after
`docker commit`, one per golden-stage snapshot
(`omc-e2e-holder-<provider>-<stage>-<sid>`, pinning
`omc-e2e-stage:<provider>-<stage>-<sid>`). `<sid>` is a 12-character tree
hash of the working tree's content, so every edit produces a new id, a new
image set and a new holder set.

Holders live until someone runs `scripts/e2e.sh prune` (`just e2e-prune`),
and nobody does: over 17 days one host accumulated 229 holders pinning about
21 GB of images that no checkout could use again. The 2026-10-01 plan pruned
stale snapshots automatically at every run (plan line 250: "Snapshots from
other sources are stale: prune them"); that became the manual `prune` because
"a concurrent run on another checkout may be using an image this checkout
considers stale". An age threshold restores what the automatic version
lacked: a holder a concurrent run has just created is always fresh, so the
pruner cannot take it. Two routes remain where it can take a live run's
holder — a run that reuses its tree's holders after more than 72 h, and a
checkout that predates the created label — and both are accepted races
(§4).

Goal: steady-state disk holds roughly 72 h of trees, with zero human steps —
every `just e2e-tests`, `just golden`, `just e2e-rest` (and the recipes built
on them) prunes what has expired before it starts.

Non-goals: BuildKit's build cache, dangling `<none>` images, volumes (the
`omc-e2e-codex-auth` account volume is never touched), and anything that
runs outside a runner invocation — cron entries and daemons are forbidden by
project policy, and a pruner that only runs when the runner runs is enough
for a host that runs E2E at all.

Anti-goal: any run — this checkout's or a concurrent one on another checkout
— losing its base image or a stage snapshot mid-run. The 2026-10-06 incident
is exactly that failure, and this change must not reintroduce it by another
route.

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Keep the ticket's design: a **`omc.e2e.created` label** on every holder, a **72 h** threshold, **unlabelled holders count as stale**, pruning at the **start of `golden`, `rest` and `all`** | The age threshold is what makes automatic pruning safe for concurrent runs; unlabelled holders are all pre-change leftovers whose age is unknown and whose trees are long gone, so treating them as stale clears the backlog on the first run |
| 2 | The **current tree's holders are never pruned**, whatever their age | A `rest` run reuses the stage snapshots an earlier `golden` run of the same tree produced; aging them out between the two would unpin exactly the images `rest` is about to fork from |
| 3 | **Manual `prune` is unchanged**: still "everything except this tree", immediately | User decision. It is the recovery tool when the disk is full now; the automatic path is the steady state, not a replacement |
| 4 | The threshold is a **constant at the top of the script**, `holder_ttl=$((72 * 3600))`, with **no environment override** | User decision. One knob nobody will turn is one knob nobody has to document or test |
| 5 | **No locking** around the build → hold and commit → hold windows | Design call, accepted with the design. The window is milliseconds, the exposure is the same the external GC already has, and the victim fails loud (§4) |
| 6 | **Silent when nothing was pruned**; one stderr line when something was | Design call, accepted with the design. The pass takes well under a second when there is nothing to do, and the runner's narration already says what it is doing |
| 7 | The record's filename carries the branch slug (the `omc internal design-record` gate matches `<date>-<slug>-design.md`); the record body, commit and PR text stay free of ticket keys, company and host names | omc is a public, company-neutral repository |

## 3. Mechanism

### 3.1 The created label

Both holder-creation sites add one label right after the source label:

- `scripts/e2e.sh:hold_image` — `--label "omc.e2e.created=$(date +%s)"`.
  `date +%s` is the same on BSD and GNU `date`.
- `tests/e2e/stages.py:hold_image` — `"--label",
  f"omc.e2e.created={int(time.time())}"`, with a module-level `import time`
  (the module has none today; the test pins `stages.time.time`, the way
  `tests/unit/test_e2e_conversation.py` pins its module's clock).

Why a label when Docker already records a container's `.Created`: that
field is an RFC 3339 string, and turning it into seconds differs between
BSD and GNU `date` (`-j -f` versus `-d`) — the very portability seam
`date +%s` avoids. An epoch label compares with `$(( now - created ))` and
nothing else. The label's absence is also the cheapest possible marker for
the pre-change backlog (Decision 1), with no date parsing at all.

Container labels are immutable, so an existing holder is reused exactly as
today (the `container inspect` check in the script, the "already in use"
tolerance in Python) and keeps the label it was born with. That is enough:
the current tree's holders are never pruned (Decision 2), so a reused
holder's age only matters once the tree is no longer current, and from that
moment its age is measured from its real creation — which is the right
clock.

The one-line comments at both sites that say holders live until `prune` are
rewritten to say `prune_stale` removes a holder 72 h after its creation once
its tree is not the current one, and `prune` removes it immediately.

### 3.2 `prune_stale` in `scripts/e2e.sh`

A new function, run **first** in the `golden`, `rest` and `all` modes —
before `ensure_image`, so a build that is about to happen finds the disk it
needs. `codex-gate`, `lifecycle-full`, `e2e-tests`, `golden-full` and
`e2e-rest` are `just` recipes over these modes and inherit it; the `prune`
mode does not call it. Under `no_docker=1` (`OMC_E2E_NO_DOCKER=1` or
`--collect-only`) it returns immediately, like `ensure_image`, `hold_image`
and `sweep`.

```
holder_ttl=$((72 * 3600))        # top of the script, next to source_id

prune_stale:
  now = date +%s
  (a) for every container with label omc.e2e=holder:
        docker inspect → id, omc.e2e.source, omc.e2e.created
  (b) stale  ⇔  source ≠ $source_id
             AND (created is empty OR not all digits OR now − created > holder_ttl)
  (c) docker rm -f  <stale holder ids>
  (d) pinned = { source of every remaining holder } ∪ { $source_id }
  (e) for every image tag matching ^omc-e2e(:|-stage:):
        sid = text after the last ':' or '-'      (a sid is 12 hex characters,
                                                   so it contains neither)
        skip the tag when sid is not 12 lowercase hex characters
        docker rmi -f the tag when sid ∉ pinned
  (f) if anything was removed:
        >&2 "pruned N stale holders and M images (older than 72 h)"
```

Why each step is what it is:

- **(a)/(b) decide on holders, not images.** A holder carries the age; an
  image does not (its `Created` is the build time, which for a reused tree
  is unrelated to when it was last used). Holders are also the reason an
  image cannot be removed, so they go first — a running holder blocks even
  `rmi -f`, as the existing `prune` already notes.
- **(b) "missing, non-numeric or older than TTL".** The first two cases are
  the pre-change holders (no label at all — `docker inspect`'s `index` on a
  missing label prints an empty string, or `<no value>` on some versions,
  and both fall into this branch) and defence against a corrupted value;
  both are treated as expired (Decision 1). The current tree is
  exempt outright (Decision 2).
- **(d)/(e) prune images by pin set, not by age.** An image whose tree still
  has a holder is in use or about to be; an `omc-e2e*` tag whose tree has no
  holder at all is an orphan (its holder was pruned just now, by a manual
  `prune` from another checkout, or by a crash before its holder was
  created) and goes too. The current `source_id` is added to the set
  because at this point in the run its own holder may not exist yet. Stage
  tags are removed **before** base tags: under the classic graph driver,
  `rmi -f` of a base tag whose stage snapshots still exist only untags it
  and its layers stay behind as a dangling `<none>` parent — the bulk of the
  disk this change is meant to free. Children first, the base goes in the
  same pass; under the containerd image store the order is harmless.
- **(f) one line, only when it did something** (Decision 6). The exact
  wording is the builder's; the counts and the threshold are the content.
- **Failures fail closed and loud.** A holder whose `docker inspect` fails
  or returns no id stops the pass before anything is deleted, prints which
  holder could not be inspected, and the runner exits non-zero before
  `ensure_image`. A failing `docker rm -f` or `docker rmi -f` exits the
  runner the same way under `set -e`. Neither is downgraded to a warning:
  a pass that cannot account for a holder must not guess.

What it never removes: a holder of the current tree; a holder of another
tree younger than 72 h (a concurrent run, or a tree the user switched away
from this morning and will come back to); any image whose tree still has a
holder; anything not tagged `omc-e2e:*` or `omc-e2e-stage:*`; a tag whose
suffix is not a 12-hex sid (such as `omc-e2e:latest`); the Codex account
volume; BuildKit cache; dangling images.

### 3.2a Snapshot check under `pipefail`

`run_golden`'s "golden path left no stage snapshots" check reads the whole
`docker images` listing (`grep … >/dev/null`, not `grep -q`). Under
`pipefail`, `grep -q` exits on the first match, `docker images` can die of
SIGPIPE, and the pipeline then reports a false "no stage snapshots". The
fix rides with this change because the prune pass makes the listing longer
on a host with a backlog.

### 3.3 Manual `prune`

Unchanged in behaviour. Its comment — "Explicit housekeeping, never
automatic: a concurrent run on another checkout may be using an image this
checkout considers stale" — is no longer true as stated and is rewritten:
automatic runs call `prune_stale`, which respects the 72 h threshold; `prune`
is the immediate, aggressive version that keeps only this tree's images, for
when the disk is full now.

### 3.4 Documentation truth

- `docker/PLUGIN-NOTES.md` line 64, "`just e2e-prune` is the only thing that
  deletes images", is rewritten: at the start of every run, the runner
  removes other trees' holders created more than 72 h ago (or missing a
  valid creation label), then other-tree E2E images without a remaining
  holder; `just e2e-prune` removes everything except the current tree's at
  once.
- The two `hold_image` comments (§3.1) and the `prune` comment (§3.3).
- `README.md` does not mention holders or `e2e-prune`; no regeneration.

## 4. Accepted race

Two windows exist where an image has no holder yet: between `docker build`
tagging `omc-e2e:<sid>` and `hold_image` starting its holder, and between a
snapshot's `docker commit` and `stages.hold_image` starting its holder. A
`prune_stale` running concurrently from another checkout at that instant
sees a tag whose tree has no holder and removes it.

This is accepted without locking (Decision 5). The windows are milliseconds
wide and already exist against the external image GC that motivated the
holders — this change adds a second, far rarer, pruner to the same exposure,
not a new class of failure. The victim fails loud, never silently: a
variation finds its snapshot missing and `stages.stage_image` calls
`pytest.fail` naming the tag and the `just golden` that recreates it; a
golden run whose snapshots vanished trips the "golden path left no stage
snapshots" check in `run_golden` and exits 1; a missing base image fails the
container start. Rerunning rebuilds what was lost.

Three further routes, found in the implementation review and accepted by
the user with the same fail-and-rerun answer (no locking, no re-stamping):

- **Old reused holders.** The clock is the immutable creation label, not
  the last run. A checkout whose tree has not changed for more than 72 h
  reuses holders older than the threshold; a concurrent run on another
  checkout, to which that tree is not current, prunes them and their
  images mid-run.
- **Checkouts without the label.** Until every checkout carries this
  change, an older checkout creates unlabelled holders, and every updated
  checkout's run treats them as expired at once — including while the
  older checkout's run is still using them.
- **The pruning run can fail at startup.** A holder that a concurrent
  pruner or `sweep` removes between the listing and its `docker inspect`,
  or an image still used by a running container when `rmi -f` reaches it,
  makes `prune_stale` fail closed (§3.2) and the starting run exits 1
  before any test. Rerunning starts a fresh pass.

Every route ends in a loud failure and a rerun, never a silent wrong
result.

## 5. Tests

All in `tests/unit/test_e2e_image_holders.py`, run by `just check` (unit tier,
no Docker), written first and watched fail. Stubs follow the project rules:
shell builtins only, exact-argv assertions.

Updated:

- `test_snapshot_holds_the_committed_image` pins `stages.time.time` (a
  monkeypatched callable returning a fixed value) and expects the
  `docker run` argv to carry `--label omc.e2e.created=<that value>`
  directly after `--label omc.e2e.source=<sid>`. Red: the label is absent.
- `test_runner_holds_the_base_image_before_the_golden_path` expects the base
  holder line to carry `--label omc.e2e.created=<digits>` after the source
  label (the value is the wall clock, matched by shape), and its ordering
  assertions (`image inspect` first, `container inspect` second, the holder
  `run` third) move to "after the prune calls": the stub's `ps` returns no
  holders, so the prune pass is `ps` plus the `images` listing and removes
  nothing. Red: no label, and `ps` is not the first call.

New:

- **Stale/fresh split.** `e2e.sh golden` against a docker stub whose `ps`
  returns five holder ids and whose `inspect` returns the canned
  `<id> <source> <created>` lines for them: another tree stale by age
  (created now − 73 h), another tree with no created label, another tree
  fresh (now − 1 h), the current tree stale by age, the current tree fresh.
  Its `images` listing returns `omc-e2e:<sid>` and one
  `omc-e2e-stage:claude-start-<sid>` for each of those sources plus one
  orphan tag whose source has no holder at all. Asserts the exact `rm -f`
  call (only the two stale other-tree holders), the exact `rmi -f` calls
  (only the tags of those two trees plus the orphan — nothing of the fresh
  other tree, nothing of the current tree), and that the first docker call
  is the holder listing, before `image inspect`. Red: `prune_stale` does not
  exist, so no `rm`/`rmi` calls are made.
- **`rest` prunes too.** The same stub under `e2e.sh rest`; asserts the
  holder listing precedes `image inspect`.
- **Fail closed.** A holder whose `inspect` fails makes the runner exit
  non-zero with no `rm` or `rmi` call (§3.2).
- **No Docker, no calls.** `OMC_E2E_NO_DOCKER=1 e2e.sh golden` and
  `e2e.sh golden --collect-only` against the same stub produce an empty
  docker log.

The threshold itself is pinned by the split test's 73 h / 1 h values; there
is no separate test of the constant.

E2E: none added. `just e2e-tests` after the implementation is the
post-implementation milestone — it exercises the real `prune_stale` against
the real daemon and the real holder backlog on the host, and its pass plus
`docker ps --filter label=omc.e2e=holder` showing only fresh holders
afterwards is the live evidence. A pruner that only acts on what is older
than 72 h cannot be driven to its interesting branch inside one E2E run;
the unit split test is where those branches are proven.

## 6. Size

Two tasks.

1. **Labels and the updated tests** — `Complexity: simple`. One `--label`
   argument at each creation site, two comment rewrites, two test updates.
2. **`prune_stale` and the split test** — `Complexity: medium`. The function,
   its three call sites in the `case`, the `prune` comment, the
   `PLUGIN-NOTES.md` sentence, and the three new tests. The docs ride along
   here.

Left to the builder: the exact stderr wording and its singular/plural
handling; whether holder inspection is one `docker inspect` over all ids or a
loop; whether `xargs` is used at all (macOS `xargs` does not run the command
on empty input, GNU `xargs` does unless given `-r` — a `bash` loop over the
collected ids avoids the difference); and test names. Not the builder's:
stage tags are removed before base tags (§3.2).

## 7. Out of scope

- Any change to what `prune` removes or when (Decision 3).
- An environment override or flag for the threshold (Decision 4).
- Locking or a hold-before-tag protocol for the build → hold windows
  (Decision 5).
- Pruning BuildKit cache, dangling images or volumes; `docker system prune`
  is a different tool with a different blast radius.
- Observed while reading `sweep`, left as is: `sweep` removes exited
  containers whose image matches `^omc-e2e(:|-stage:)`, and holders have no
  restart policy, so after a Docker daemon restart every holder is exited —
  `hold_image` then reuses the current tree's exited base holder and `sweep`
  removes it a moment later, leaving the base image unpinned for that run.
  `prune_stale` neither causes nor fixes this (it handles exited holders
  like running ones); it is a separate, smaller change (restart or recreate
  a non-running holder in `hold_image`, or exclude the holder label in
  `sweep`).

## Implementation review

### 2026-10-23 — Claude (Opus 5.5)

1. Important · record is stale · §1, §3.1, §3.4, §4 ·
   `scripts/e2e.sh:60` · The record described expiry as "72 h after the
   tree was last run" and claimed a live run's holders are always fresh;
   the clock is the immutable creation label, so a reused old holder or an
   unlabelled holder from an older checkout can be pruned under a live run.
   The user had accepted that fail-and-rerun risk at implementation review
   (plan, "Review disposition"). **Amended**: §1, §3.1 and §3.4 now say
   creation time; §4 lists both routes as accepted races.
2. Important · record is stale · §3.2, §4 · `scripts/e2e.sh:50-59`,
   `:93-96` · `prune_stale` fails closed: an inspect failure or a failing
   `rm -f`/`rmi -f` exits the starting run non-zero, which the record did
   not describe. Fail-closed was the plan's explicit choice and is tested
   (`test_runner_inspect_failure_stops_before_deletion`); downgrading it to
   a warning would be a silent skip. **Amended**: §3.2 states the failure
   behaviour, §4 accepts the startup failure as a third race, §5 lists the
   test.
3. Minor · record is stale · §3 · `scripts/e2e.sh:138-139` · The `run_golden`
   snapshot check moved from `grep -q` to a full read under `pipefail`.
   **Amended**: new §3.2a.
4. Minor · record is stale · §3.2 · `scripts/e2e.sh:82` · Tags whose
   suffix is not a 12-hex sid are skipped. **Amended**: step (e) and the
   never-removes list.
5. Minor · code deviates · §3.1 · `scripts/e2e.sh:115-116`,
   `tests/e2e/stages.py:89-92` · The holder comments implied the clock
   starts when the tree stops being current. **Fixed**: both comments now
   name `prune_stale`, creation time and 72 h.

Gate after disposition: `just check` 1704 passed; `just build` clean;
`just e2e-tests` 65 passed. Its live `prune_stale` removed 6 unlabelled
holders left by a just-finished run on another checkout and kept the
labelled fresh ones.

## Deliberate complexity

None.
