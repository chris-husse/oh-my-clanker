# Superpowers Planning Docs

# Superpowers Planning Docs

## Overview

`docs/superpowers/plans/` and `docs/superpowers/specs/` hold the omc project's own working documents — the implementation plans and design specs written for features being built *in* this repo, using the `superpowers:writing-plans` / `superpowers:executing-plans` skill workflow. These are not source code and ship no runtime behavior; they are the paper trail an agentic worker follows task-by-task to implement a feature with red→green TDD discipline.

Each plan file is named `YYYY-MM-DD-<slug>-plan.md` and pairs with a design doc `YYYY-MM-DD-<slug>-design.md` in the sibling `specs/` directory. A plan is consumed by dispatching `superpowers:subagent-driven-development` (or `superpowers:executing-plans`) against it — the skill walks the checkboxes in order, and each task's steps are meant to be executed literally: write the failing test, run it, watch it fail for the *expected* reason, implement, run it again, lint, commit.

## Anatomy of a plan

Every plan in this directory follows the same skeleton:

- **Header block** — Goal, Architecture, Tech Stack, and a link to the paired spec in `docs/superpowers/specs/`.
- **Global Constraints** — cross-cutting rules that apply to every task in the plan: how to run tests, lint command and line-length, commit message format (`feat:`/`fix:`/`docs:` + `(red->green)` suffix + `Co-Authored-By` trailer), and doctrine callouts specific to the feature (e.g. "watch narration only narrates on state change," "notifications never break work").
- **Numbered tasks**, each declaring:
  - **Files** — exactly which files are created/modified, with line-number hints into the current source.
  - **Interfaces** — what the task consumes from earlier tasks and produces for later ones, so tasks can be gated and reviewed independently.
  - **Steps as checkboxes** — write failing test → run and confirm the expected failure → implement → run and confirm pass → lint → commit, task by task.
- **A task-order/independence note** at the end, telling the executing agent which tasks are strictly sequential versus parallelizable.

This structure is what makes the plans machine-executable: an agent (or `superpowers:subagent-driven-development` dispatching sub-agents per task) can pick up any task, see its exact inputs/outputs, and know it's done when its own test suite is green and linted.

## The three plans currently on file

| Plan | Repo(s) touched | Shape |
|---|---|---|
| `2026-07-17-cops-987-improve-omc-watch-plan.md` | this repo (`omc`) | 9 tasks: ship the behavior layer as installed package data instead of a per-repo generated file, migrate the old symlink chain, add `omc print-install-path` / `omc update` / `omc internal gitnexus` proxy, rewrite dependent skill docs, then Docker E2E verification |
| `2026-07-17-cops-988-add-slack-ping-on-idle-plan.md` | this repo (`omc`) | 6 tasks: opt-in notification config, a shared `omc internal notify` delivery sink (macOS `osascript` / `file://` log backends), per-provider wiring descriptions (Claude settings hooks, Codex `-c notify=`), and worktree-time wiring executed from `omc start` |
| `2026-07-17-fix-wiki-ladybugdb-not-initialized-plan.md` | **two repos**: `/Users/chriphus/Projects/GitNexus` (TypeScript/vitest) and this omc worktree (Python/pytest) | A bug-fix plan spanning a sibling dependency repo: run-scoped keepalive `setInterval` in GitNexus's `WikiGenerator.run()`, save-on-effective-change for CLI config persistence, then (in omc) a deterministic `update_gitnexus()` wired into `run_update` |

The third plan is architecturally distinct from the other two: it's the only one where "Files" and "Steps" span a second git repository with its own branch, its own build/gate sequence (`npx tsc --noEmit && npm test`), and its own commit/push policy (direct push to `main`, no PR — an explicit user override recorded in the plan's Global Constraints). Anyone executing it needs write access to both checkouts and must not conflate the two repos' commit histories.

## How these connect to the rest of `omc`

These plans aren't read by any omc code at runtime — they're inputs to the *development* process, not the product. But they document (and are the most reliable record of) the intended shape of several load-bearing pieces of the codebase discussed elsewhere in `.omc/docs/`:

- The **behavior-layer chain** (root `AGENTS.md`/`CLAUDE.md` → `.omc/config/AGENTS.md`) — COPS-987 is the plan that moved this from a generated-and-committed file (`INTERNAL_AGENTS_MD` in `agentsmd.py`) to a symlink into installed package data (`src/omc/distribution/AGENTS.md`), which is exactly why the root of *this* repo now works the way `CLAUDE.md`'s own header describes ("ships with the omc install — `omc update` updates it everywhere").
- **`omc update`** — first given real per-provider plugin-update responsibility in COPS-987 Task 6, then extended for the GitNexus dependency in the wiki-fix plan's Task 5 (truncated in the excerpt above, but referenced by its Architecture line: `update_gitnexus()` in `src/omc/gitnexus.py`).
- **`omc internal gitnexus`** — the scoped graph-query proxy (COPS-987 Task 7) that the `omc:explain` / `gitnexus-explain` skill machinery described elsewhere in this doc set depends on for deterministic `--repo`/`--branch` scoping.
- **Notifications** (COPS-988) — a self-contained feature addition (config → delivery core → CLI entry point → provider wiring → worktree materialization → E2E) that doesn't yet appear to have landed in `src/omc/` under its final module name; treat the plan as forward-looking design until `notify.py` shows up in the tree.

If you're trying to understand *why* a piece of `src/omc/` looks the way it does — a symlink where you might expect a generated file, a `--repo`/`--branch` pair on every GitNexus invocation, a `plugin_update_argvs()` method on `Provider` — the matching plan file here is the fastest way to recover the reasoning, including the specific failure mode each change was fixing (e.g. GitNexus's flat-store staleness, or the `LadybugDB not initialized` keepalive bug covered end-to-end in the third plan).