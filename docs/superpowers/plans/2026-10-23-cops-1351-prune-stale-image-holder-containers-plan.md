# Prune Stale E2E Image Holders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before every Docker-backed E2E runner mode, remove expired holder containers and the images they no longer pin, while preserving the current tree and recent other trees.

**Architecture:** Stamp base and stage holder containers with an epoch creation label. A `prune_stale` function in `scripts/e2e.sh` removes non-current holders older than 72 hours, then removes E2E image tags with no remaining holder, in stage-before-base order. The manual `prune` mode keeps its immediate behavior.

**Tech Stack:** Bash, Docker CLI, Python 3, pytest, `just`.

**Spec:** `docs/superpowers/specs/2026-10-23-cops-1351-prune-stale-image-holder-containers-design.md`

## Global Constraints

- Follow `.omc/config/AGENTS.md`: write validation first, run it red for the expected reason, then implement and run it green; stub scripts use shell builtins and exact argv assertions.
- Use `holder_ttl=$((72 * 3600))` at the top of `scripts/e2e.sh`; no environment override, lock, daemon, or cron entry.
- `prune_stale` runs before `ensure_image` in `golden`, `rest`, and `all`; it returns without Docker calls when `no_docker=1`.
- Keep the current tree's holders regardless of age, other trees' holders through age 72 hours, and every image whose source still has a holder. Missing or nonnumeric creation labels count as stale for non-current trees.
- The clock is immutable holder creation time, not last use: reusing an old current-tree holder does not refresh its label. This follows the design's detailed mechanism and current-tree exemption, despite its opening "last run" wording.
- Remove stage tags before base tags. Leave `prune` behavior, BuildKit cache, dangling images, volumes, and unrelated image tags untouched.
- `README.md` is generated; do not edit it. Keep user-facing prose free of ticket keys and host or company names.

## Review Focus

These cases sharpen the design's split test and belong to Task 2's tests:

1. A non-current holder exactly 72 hours old remains; only age **greater than** 72 hours expires.
2. A non-current holder with a malformed creation label is removed like one with no label.
3. When one source has both an expired and a fresh holder, removing the expired holder leaves that source's image tags pinned by the fresh one.
4. Tags outside `omc-e2e:*` and `omc-e2e-stage:*` stay untouched even when their suffix resembles an expired source.
5. If Docker cannot inspect a listed holder, the pass fails without deleting holders or images whose safety it cannot establish.

---

### Task 1: Stamp base and snapshot holders

**Complexity: simple**

**Files:**
- Modify: `scripts/e2e.sh:46-57` (base holder label and comment)
- Modify: `tests/e2e/stages.py:13-15,81-112` (clock import, snapshot holder label and docstring)
- Test: `tests/unit/test_e2e_image_holders.py:44-70,89-131`

**Interfaces:**
- Consumes: `source_id` in each creation site; the existing `hold_image()` functions keep their signatures.
- Produces: every newly created holder has `omc.e2e.created=<epoch seconds>` directly after `omc.e2e.source=<sid>`. Task 2 reads that label through Docker inspect.

- [x] **Step 1: Update the two existing holder tests.** In `test_snapshot_holds_the_committed_image`, use `monkeypatch.setattr(stages, "time", SimpleNamespace(time=lambda: 1_700_000_000), raising=False)` so the test is red even before `stages.py` imports `time`; assert the exact `docker run` argv includes `--label omc.e2e.created=1700000000` immediately after the source label. In `test_runner_holds_the_base_image_before_the_golden_path`, require decimal digits in the same argv position and retain the current image-inspect → container-inspect → holder-run ordering until Task 2 changes startup ordering.
- [x] **Step 2: Run the two tests red.** Run `uv run pytest -q tests/unit/test_e2e_image_holders.py::test_snapshot_holds_the_committed_image tests/unit/test_e2e_image_holders.py::test_runner_holds_the_base_image_before_the_golden_path`. Expect both to fail because the created label is absent.
- [x] **Step 3: Add the labels.** Add `--label "omc.e2e.created=$(date +%s)"` to `scripts/e2e.sh:hold_image`. Import `time` at module level in `tests/e2e/stages.py` and add `"--label", f"omc.e2e.created={int(time.time())}"` to its `hold_image`. Rewrite both lifespan comments to mention automatic aging and manual `prune`; preserve the existing reuse behavior.
- [x] **Step 4: Run the focused tests green, then the project quick gate.** Run the Step 2 command, then `just check`. Both must pass.
- [x] **Step 5: Commit Task 1.** Stage only the three files above and commit the labels with their tests.

### Task 2: Prune expired holders and unpinned E2E images at startup

**Complexity: high**

**Files:**
- Modify: `scripts/e2e.sh:17-25,81-98` (TTL, `prune_stale`, mode dispatch, manual-prune comment)
- Modify: `docker/PLUGIN-NOTES.md:64` (automatic and manual cleanup description)
- Test: `tests/unit/test_e2e_image_holders.py:89-131` and new tests below it

**Interfaces:**
- Consumes: `source_id`, `no_docker`, and the two holder labels from Task 1.
- Produces: `prune_stale()` with no arguments, called before `ensure_image` by `golden`, `rest`, and `all`; `prune()` remains the separate manual path.

- [x] **Step 1: Write the startup split test and update the base-holder test.** Stub Docker `ps`, `inspect`, `rm`, `images`, and `rmi` with shell builtins and a log of exact argv. Let the stub read `OMC_E2E_SOURCE_ID` for the current tree; use a fixed `date +%s` stub so ages are deterministic. Return inspect fields separated by a literal `|`, preserving Docker's possible `<no value>` missing-label text. Supply holders for another source aged 73 hours, another source with no creation label, another source aged 1 hour, and the current source aged 73 hours. Supply stage and base tags for those sources plus an orphan source. Assert the first Docker call is the holder listing before image inspection; the exact `rm -f` ids are the two stale other-tree holders; the exact `rmi -f` tags are their stage tags and orphan stage tag before their base tags and orphan base tag; fresh-other and current tags are absent from removals. Update `test_runner_holds_the_base_image_before_the_golden_path` so its no-holder stub expects the initial holder listing and image listing before `image inspect` and still proves the holder starts before the golden path. The new split test and updated ordering must fail red because `prune_stale` does not yet run.
- [x] **Step 2: Write the remaining Task 2 cases.** Parameterize the mode-order test over `rest` and `all` so each starts with holder listing before `image inspect`. Assert both `OMC_E2E_NO_DOCKER=1` and `--collect-only` yield an empty Docker log. Extend the split fixture with a malformed numeric label, a separate source with one expired and one fresh holder, and unrelated image tags; assert the malformed and expired holders are removed while the mixed-age source's tags and unrelated tags remain. Add a boundary test with a holder exactly 72 hours old and assert it remains. Add an inspect-failure test that asserts nonzero runner exit and no `rm` or `rmi`. Assert a no-op pass prints no prune message. The mode-order and split assertions provide the required red evidence; record any other already-green guard tests as guards, not as the red proof.
- [x] **Step 3: Run the focused tests red.** Run `uv run pytest -q tests/unit/test_e2e_image_holders.py`. Expect the startup-order and stale/fresh assertions to fail for missing automatic pruning. The no-Docker guards may already pass.
- [x] **Step 4: Implement `prune_stale()` in `scripts/e2e.sh`.** Set `holder_ttl=$((72 * 3600))` near `source_id`. If `no_docker=1`, return before any Docker call. Read `now` from `date +%s`; list all holders with `docker ps -aq --filter label=omc.e2e=holder`, and inspect each for id, source, and created label using a `|`-delimited Docker format so `<no value>` cannot shift fields. Treat missing or nondecimal created values as stale only if the source differs from `source_id`; otherwise expire on `now - created > holder_ttl`. Make inspection failures fail closed before deletion. Remove stale ids with `docker rm -f` only when nonempty. Build the pin set from **remaining** holders plus `source_id`, then list images with `docker images --format '{{.Repository}}:{{.Tag}}'`; consider only `^omc-e2e(:|-stage:)` tags and derive the 12-hex source suffix after the final `:` or `-`. Remove unpinned stage tags before unpinned base tags. Report one stderr line with holder/image counts and 72 hours only if a deletion occurred. Do not rely on `xargs` running on empty input.
- [x] **Step 5: Wire modes and update prose.** Prepend `prune_stale` to `golden`, `rest`, and `all` in the `case` dispatch; leave the `prune` arm's commands intact and rewrite only its outdated comment. In `docker/PLUGIN-NOTES.md`, describe automatic cleanup of non-current holders older than 72 hours by creation time and immediate `just e2e-prune` cleanup.
- [x] **Step 6: Run focused tests green, then the project quick gate.** Run `uv run pytest -q tests/unit/test_e2e_image_holders.py`, then `just check`. Both must pass. Check `git diff --check` and `git diff` for scope and stage-before-base order.
- [x] **Step 7: Commit Task 2.** Stage only `scripts/e2e.sh`, `tests/unit/test_e2e_image_holders.py`, and `docker/PLUGIN-NOTES.md`; commit the pruner, tests, and docs together.

After both task commits, the implementing lifecycle runs its configured `check` → `build` → `verify` milestone gates. The real `just e2e-tests` verify pass exercises the daemon and its holder backlog; inspect `docker ps -a --filter label=omc.e2e=holder` afterward for fresh or current-tree holders, without manually invoking the aggressive `prune` mode.

## Review disposition

The whole-branch review found that another checkout can prune an old holder
reused by an active run, or an unlabelled holder from a checkout that has not
adopted this change. A later test may then need `just golden` or a full E2E
rerun. The user accepted that failure-and-rerun risk and directed continuation
with the committed no-lock design. The automatic pruner's behavior remains as
specified above.
