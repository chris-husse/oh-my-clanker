# Rewrite README for humans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Give newcomers a readable install-to-review guide, generated through a repo-local skill and guarded against CLI/outline drift.

**Architecture:** Move the existing shell-title detail verbatim into its own document, then create the canonical README regeneration skill and follow it to produce the README. Parser-derived tests protect the option table; structural tests protect navigation and generation provenance. Runtime code is unchanged.

**Tech Stack:** Markdown, Mermaid, Python/pytest, argparse.

**Spec:** `docs/superpowers/specs/2026-10-06-rewrite-readme-for-humans-design.md`

## Global Constraints

- The fish/iTerm2 tab-title text moves **verbatim** to `docs/shell-integration.md`.
- The skill lives at `.claude/skills/regenerate-readme/SKILL.md`, one canonical file, no symlink.
- No `docs/development.md`; no changes to CLI help strings; no edits to generated docs under `.omc/docs/`.
- Nothing under `src/`, `skills/` or the stage skills in `.omc/skills/` changes.
- `docker/PLUGIN-NOTES.md` is untouched; `README.md#development` stays valid.
- The skill never commits; the lifecycle does that.
- Use `uv run --frozen` when rendering help; parser source is canonical because the copied `.venv` may reference the primary checkout.
- Red → green validation comes before each product change; no prose-quality test.
- Per the behavior layer's model-tier policy (AGENTS.md, Model selection), every task in the plan carries a `Model:` line naming its tier — `top tier` for spec, review, and judging tasks; `standard coding tier` as the floor for coding tasks; `heavy coding tier` for bigger coding tasks (multi-file, architecturally tricky, or ambiguous). Tier names only, never pinned model ids.

## Review Focus

1. Shell-title users must retain all seventeen required phrases and none of the five forbidden phrases (Task 1, existing guard retargeted).
2. A newly added watch option must fail documentation validation until it has an option-table row (Task 2, derive flags from argparse, exclude built-in help).
3. A newcomer must reach the shell detail through a real relative Markdown link (Task 1, link test).
4. The generated banner must lead to an existing canonical skill (Task 2, banner/path guard).
5. A README section added later must be represented in the skill, including `## Development` (Task 2, heading guard; human review checks the full fixed order).

## Pressure-test findings

- `/omc:explain` for shell/navigation: graph results identify `tests/unit/test_cli.py:test_readme_documents_every_user_facing_title_surface`, `src/omc/fish_integration.py` and title flows. The current test contains seventeen positive and five negative assertions, so retarget it rather than duplicate its contract. Source text is two whole paragraphs; preserve exact bytes within those paragraphs.
- `/omc:explain` for regeneration/architecture: the graph identifies `src/omc/cli/__init__.py:build_parser`, `src/omc/internal.py:run_internal`, GitNexus refresh flows and provider validation. Use the existing parser to derive watch flags, with no new parser wrapper or runtime API. Read generated shell/CLI architecture docs only as secondary evidence.
- Graph search returned partial results with schema/FTS warnings; it did not prove absence of other flows. Source reads confirm the parser, watch defaults and project configuration; do not repair the primary index as part of this documentation task.
- Built-in `-h`/`--help` is outside the seven domain flags listed in the design. Exclude `argparse._HelpAction` from the option guard rather than hardcoding the seven flags, so later real options are caught.
- The overall 150–200-line aspiration and section budgets conflict arithmetically. Keep the mandatory content and short paragraphs, using section budgets as targets rather than cramming it into long lines.
- Architecture wording must describe observed boundaries accurately: `ToolContext` centralizes process/network effects, and skill flows combine judgment with deterministic `omc internal` operations. Avoid absolute claims contradicted by direct tool calls in existing skills.

## Fact-carryover checklist

Task 2 checks these against the old README and canonical source before completion:

- [x] Installation URL, both harness plugin paths, automatic Claude plugin/superpowers repair, Codex superpowers prerequisite, marketplace pitfall link, and `/omc:integrate` entry point.
- [x] Update's five effects, version/provenance, local-install re-rooting and uninstall.
- [x] Menu navigation and reopen-dependent-choices note; global/project/secrets file roles; provider/session/docs/native-notification/worktree settings; defaults and repeatable `--set`; live validation before save, masked 0600 keys, CLI/API backend behavior and no silent fallback.
- [x] Global fenced guidance preserves outside text and honors harness config directories; project guidance is seeded once; legacy root symlink migration.
- [x] Watch foreground-in-primary rule, all seven domain flags, 30-second interval, opt-in docs/build cost, faithful worktree artifacts, default warn-and-skip, autostash conflict recovery, single-instance/crash behavior, stale snapshot guidance, hook outcomes and live logs, no build timeout, external dependency watch.
- [x] Lifecycle context shapes, tool probe and alias, slug/worktree/title/seed, primer and user-seed gate, explicit design/implement/audit authority, committed-record gates, provider handoff/session names/resume, shared flags and design-only mutex flag, Codex syntax, squash/gates/force-with-lease/user-opened MR.
- [x] In-session explain/index/document/dependency/investigate/rebase/config-review/finish/integrate skills, env briefing requirement, optional stage semantics and grug review.
- [x] CLI/plugin/behavior/project components, ToolContext/provider adapters, knowledge snapshot and primary graph query distinction, exact machine verdict names and 0/1/2/3 meanings.
- [x] Prerequisites, native-notification settings and old-hook migration, other-command table, all ten development surfaces, no post-watch hook here, E2E evidence link.
- [x] Security note and license retain their substance. Only the explicit exclusions in spec §3 leave the README.

### Task 1: Extract shell integration detail and preserve navigation

**Model:** standard coding tier

**Files:**
- Create: `docs/shell-integration.md`
- Modify: `README.md`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: the two existing README paragraphs beginning `Tab titles in iTerm2 (fish)` and `To verify the iTerm2 behavior natively`.
- Produces: `docs/shell-integration.md` containing those exact paragraphs; a README relative Markdown link to it; renamed `test_shell_integration_documents_every_user_facing_title_surface` and new `test_readme_links_to_shell_integration`.

- [x] **Step 1:** Retarget and rename the existing title guard without altering its seventeen positive or five negative assertions. Add the link guard, asserting an actual Markdown destination `docs/shell-integration.md` in README.
- [x] **Step 2:** Run `uv run --frozen pytest tests/unit/test_cli.py -q -k 'shell_integration_documents or readme_links_to_shell'`; expect two failures for missing destination file and link.
- [x] **Step 3:** Create the shell document with a title and one intro line, then the two paragraphs copied verbatim; replace their README location with a short link. Do not otherwise rewrite the README yet.
- [x] **Step 4:** Re-run the focused tests and compare both moved paragraphs with `git show` of the pre-task README; expect both tests and exact paragraph comparison to pass.
- [x] **Step 5:** Self-review and commit only these three files as `docs: move shell integration detail out of README`. Controller reviews the task and runs `/omc:check` before Task 2.

### Task 2: Add the regeneration skill and generate the human README

**Model:** top tier

**Files:**
- Create: `.claude/skills/regenerate-readme/SKILL.md`
- Modify: `README.md`, `.omc/config/AGENTS.md`, `.omc/skills/explain-context/SKILL.md`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Task 1's shell document and link guard; `omc.cli.build_parser()`; spec §3 outline and §5 source map.
- Produces: canonical skill, README first generated by following it, three additional structural guards, agent guidance directing regeneration.

- [x] **Step 1:** Add `test_readme_documents_every_watch_option`: find argparse's subparser action, take its `watch` parser, collect every non-help option string, and assert each starts a README option-table row (`| \`--flag\``). Do not hardcode the option list.
- [x] **Step 2:** Add `test_readme_starts_with_regeneration_banner`: first line is an HTML comment mentioning generation; a visible blockquote before the title links `/regenerate-readme` to `.claude/skills/regenerate-readme/SKILL.md`, says not to edit by hand, and its destination exists as a regular file. Add `test_readme_headings_match_regeneration_skill`: extract every README `## ` heading, assert nonempty, and require each in the skill text.
- [x] **Step 3:** Run `uv run --frozen pytest tests/unit/test_cli.py -q -k readme`; expect new guards to fail for absent table/banner/skill while Task 1's navigation test stays green. Record this baseline before authoring the skill.
- [x] **Step 4:** Write the canonical skill as a reference recipe with valid name/description frontmatter. Transfer the full fixed outline, source map, section budgets, four Mermaid diagrams (one lifecycle plus three architecture), carryover constraints and generation steps from spec §§3/5. Keep commands to frozen help rendering, guard tests and diff display; no installation or commit side effects. Codex can follow this file directly; do not add a second entry point unless its path is verified and duplication avoided.
- [x] **Step 5:** Follow the new skill, read every listed source, render help with `uv run --frozen omc <cmd> --help`, then write the whole README. Preserve all fixed headings, accurate options, short prose, the banner, shell link and development anchor. Check off the carryover checklist above in the task report. This real generation is the skill's application test.
- [x] **Step 6:** Add the one-line generated-README rule to `.omc/config/AGENTS.md` and two truth-map lines to `.omc/skills/explain-context/SKILL.md` identifying the generator and shell document.
- [x] **Step 7:** Run focused CLI tests; expect all to pass. Check Markdown links resolve, count four Mermaid blocks, review diagram syntax, check moved shell paragraphs still match their originals, and inspect `git diff --check`. Inspect the full generated diff against the outline/carryover checklist; update the skill and regenerate for defects rather than patching the output alone.
- [x] **Step 8:** Self-review and commit task files as `docs: generate a human-readable README from a repo skill`. Report RED/GREEN evidence, source reads, factual corrections, carryover checklist and any concerns. Controller performs task review, `/omc:check`, final branch review, commits the plan, and verifies a clean unpublished branch.

## Execution evidence

- Task 1 committed as `81b4402`: two expected RED failures, then 2 focused and 29 affected-file tests passed; both shell paragraphs matched the original exactly. Independent task review approved.
- Task 2 committed as `4343790`: three expected RED failures, then 32 CLI tests and 5 documentation guards passed. The canonical skill was authored before its first README generation, with the source map and carryover checklist followed. Independent task review approved.
- Final whole-branch review found no Critical or Important issues. Its one minor code-span formatting finding was fixed in `584f106`; 5 guards passed and scoped re-review confirmed both spans fixed with no new breakage.
- Final `/omc:check` (`just check`): exit 0, **1308 passed**, 7 pre-existing Python fork/forkpty DeprecationWarnings, 17.66 seconds.
- `uvx ruff check` and `uvx ruff format --check` passed for `tests/unit/test_cli.py`; Ruff is supplied by uvx in this repository, not its project environment.
- All four diagrams passed Mermaid 11.17.2 grammar parsing (Node syntax check with identity label sanitization, not a browser render). Relative links and local anchors resolved; external URLs were not network-probed.
- Source-based architecture corrections preserve primary graph routing and actual CLI/skill process boundaries. The detailed outline takes priority over its contradictory global line target: the README is 304 lines and 2,933 words.
- No runtime, stage implementation, dependency, publication or host-install changes. The known uv-generated root-package version drift in `uv.lock` is restored before handoff.
