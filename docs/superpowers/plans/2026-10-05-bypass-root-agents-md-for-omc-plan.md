# Global harness instructions implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the omc behavior layer through managed sections in global harness instructions while leaving repository root instructions alone.

**Architecture:** `agentsmd.py` owns byte-preserving section writes/removal and project seeding. Providers resolve their global instruction paths; configure/start deliver the section, update invokes the fresh CLI, and uninstall removes it. Watch stops maintaining repo instruction chains.

**Tech Stack:** Python, pathlib/tempfile/os.replace, pytest, existing Docker/provider E2E harness.

**Spec:** `docs/superpowers/specs/2026-10-05-bypass-root-agents-md-for-omc-design.md`

## Global Constraints

- Section body is the installed `distribution/AGENTS.md` inline, byte for byte.
- omc stops creating and repairing the chain; no suppression of root files, no removal of existing symlinks or `.gitignore` lines.
- File present, markers malformed (one marker only, duplicates, end before begin): raise `OmcError` naming the file and the problem. omc never guesses inside someone's instruction file. Callers report the error and continue; the other provider still gets its section.
- Every byte outside the span is preserved.
- Writes go through a temp file in the target directory followed by a rename.
- `ToolContext` is the only subprocess/env/network boundary. Red first, no skips.
- Do not install this checkout into the host or modify the user's real global instructions during tests.

## Review Focus

- Existing user bytes can include CRLF or non-UTF8: byte preservation must survive append, replace, and remove (Task 1).
- Global instruction symlinks must not be silently replaced or redirected; fail clearly and preserve the link and target if unsupported (Task 1).
- Malformed first-provider instructions must not prevent the second provider or project seed (Tasks 1 and 2).
- Plugin failures and early `continue` paths must not skip section refresh (Task 2).
- Live harness tests must establish global loading without any model tool read (disable tools where supported; otherwise assert no tool execution events) (Task 3).

## Pressure-test evidence

`omc:explain` graph queries/context confirmed `ensure_agents_chain` callers at `configure:_ensure_repo_chain`, `start:run_start`, and `watch:_chain_tick`. Remove all callers together with the old API so the first task remains checkable. `installer:post_install` calls `_fresh_cli` and `ToolContext.run_bounded`; reuse that executable resolver for all platforms, outside the macOS-only fish path. The E2E graph identifies `harness:configure_omc`, `run_in`, and Codex account-volume setup; extend those conventions rather than inventing a separate harness. Generated docs still describe the current chain, agreeing with the code; the committed spec intentionally changes it.

## Task 1: Global section engine and configure/start/watch transition

Model: heavy coding tier

**Files:** Modify `src/omc/agentsmd.py`, `src/omc/providers/{base,claude,codex}.py`, `src/omc/{configure,start,watch}.py`, `src/omc/distribution/AGENTS.md`; rewrite/update `tests/unit/test_{agentsmd,providers,configure,start,start_mutex,watch}.py` and any direct old-chain test callers.

**Interfaces:**
- Produces `Provider.instructions_file(self, env) -> Path` using ctx.env HOME and provider-specific overrides.
- Produces `ensure_global_section(ctx: ToolContext, provider_name: str) -> str` (`created`, `updated`, `current`), `remove_global_section(ctx: ToolContext, provider_name: str) -> str | None` (summary note, malformed raises), and `seed_project_agents_md(root: str | Path) -> None`.
- Retains `distribution_agents_md() -> Path`. Fixed UUID chosen once in this task; expose constants for markers so tests avoid duplicate doctrine.

- [x] Write failing unit tests for absent/empty/no-marker/current/replacement files, exact inline body, immutable mtime on current, all malformed marker shapes, ctx.env path resolution, one-time project seed, and uninstall primitive. Representative assertions:
  ```python
  assert ensure_global_section(ctx, "claude") == "created"
  assert distribution_agents_md().read_bytes() in target.read_bytes()
  before, mtime = target.read_bytes(), target.stat().st_mtime_ns
  assert ensure_global_section(ctx, "claude") == "current"
  assert (target.read_bytes(), target.stat().st_mtime_ns) == (before, mtime)
  ```
  Parameterize separator cases (no newline, one newline, two/newline extras), CRLF/non-UTF8 outside bytes, prefix/suffix preservation, malformed untouched bytes, whitespace-only removal, foreign-only files, and symlink refusal without changes. Pin new scope/project wording from the spec.
- [x] Write failing wiring tests: all configure modes and outside-repo configure write each configured provider; malformed Claude still permits Codex and seed; start writes only launching provider and proceeds on write error; existing committed root files and legacy links/gitignore remain byte/link-identical; watch no longer creates/repairs root files. Update mutex fixtures for new boundary.
- [x] Run focused tests and record expected red before implementation: `uv run pytest -q tests/unit/test_agentsmd.py tests/unit/test_providers.py tests/unit/test_configure.py tests/unit/test_start.py tests/unit/test_start_mutex.py tests/unit/test_watch.py`.
- [x] Implement byte-span parsing, atomic writing with cleanup, errors naming path, and removal of span plus one adjacent blank-line separator. Use provider path method and ctx.env expansion. Refuse symlink targets clearly rather than silently replacing personal dotfile links. Preserve mode on replacement. Narrate once on created/updated, silent current. Seed only absent project file. Replace chain calls with best-effort global/seed calls; remove old chain helpers/watch quiet tokens completely. Keep doctrine between scope guard and closing pointer unchanged; update PROJECT_STARTER.
- [x] Run focused tests green, run `just check`, self-review, commit test + implementation. Record actual commands and red/green output in report.

## Task 2: Update refresh through fresh CLI and uninstall removal

Model: heavy coding tier

**Files:** Modify `src/omc/installer.py`, `src/omc/internal.py`, `tests/unit/test_installer.py`, `tests/unit/test_internal.py` (or focused new internal-command tests if that file does not exist).

**Interfaces:**
- Consumes Task 1 `ensure_global_section` and `remove_global_section`.
- Produces `omc internal global-instructions <provider>` invoking the installed writer, no new machine verdict required; success exit 0, normal OmcError exit 1.
- Reuses `_fresh_cli(ctx)` and `ctx.run_bounded`, retains existing fish post-install behavior.

- [x] Write failing tests for new internal dispatch/provider errors, update calling exact `[fresh_exe, "internal", "global-instructions", provider]` once per configured provider on Linux and macOS, and continuation after malformed section/plugin failure/missing fresh CLI. Verify refresh executes independently of plugin `continue` paths and uses fresh CLI rather than current-process packaged body.
- [x] Write failing uninstall tests with two configured providers: strip sections preserving user bytes, remove omc-only files, keep malformed provider untouched/report it while removing other provider, load config before removing OMC_HOME, preserve existing fish and unsafe-home behavior.
- [x] Run focused tests red (`uv run pytest -q tests/unit/test_installer.py` plus command tests).
- [x] Implement internal dispatch, bounded per-provider fresh refresh with reported best-effort errors, and uninstall per-provider cleanup before data removal. Do not widen install behavior or introduce a new persisted setting.
- [x] Run focused tests green, `just check`, self-review, commit test + implementation; report red/green evidence.

## Task 3: Real harness acceptance and delivery documentation

Model: heavy coding tier

**Files:** Replace `tests/e2e/test_e2e_chain.py` with `tests/e2e/test_e2e_global_instructions.py`; modify `README.md`, `skills/integrate/SKILL.md`, `.omc/config/AGENTS.md`; adjust E2E expectations that explicitly require old chains if necessary. Add focused unit contract validation only where it proves a changed requirement.

**Interfaces:** Consumes configured global-section behavior and existing Docker `run_in`, provider auth/model helpers, `codex_gate` convention.

- [x] Write acceptance tests: configure preserves pre-existing user text, markers and exact layer appear, second configure is byte-identical; preconfigured repo with committed root CLAUDE.md stays unchanged except seed; uninstall removes section preserving user text. Assert command exit codes, not `|| true`. Keep unrelated configure config creation out of the root-file snapshot comparison.
- [x] Write one real headless session per provider that quotes the heading of the omc section from preloaded instructions and asserts `omc behavior layer`; do not include the expected answer in the prompt; disable Claude tools and assert Codex JSON events contain no tool execution. Claude joins default E2E; Codex uses existing opt-in gate/account auth. No skips.
- [x] Demonstrate red for these acceptance tests against pre-feature code in a disposable test checkout/image or by temporarily disabling only global delivery in the test environment; restore delivery and demonstrate green. Never reinstall the host CLI. Report infrastructure blockers honestly; controller owns final full E2E gates.
- [x] Update README configure delivery story and exact two-sentence migration note (old symlinks/gitignore stay; users can delete old symlinks to avoid duplicate instructions). Update integrate inventory/remediation to global sections and `omc configure`; reword project guidance opening. Keep historical records and generated docs intact. For documentation edits use focused pre-change assertions/review against old wording instead of fragile prose tests.
- [x] Run focused acceptance tests, `just check`, self-review, commit tests/docs and report evidence. Full final `just e2e-tests` and `just codex-gate` remain the finish milestone.

## Finish (controller)

Model: top tier

- [ ] Task review after each task (spec + quality), `omc:check` after review before next task.
- [ ] Whole-branch review and fix any material findings.
- [ ] `omc:finish`: rebase/mirror, squash, MR description, check/build/verify/review, push, ticket handling and follow-up offer.

## Final-review refinement (2026-10-05)

The original tasks above record the brainstorm and implementation sequence.
Final review expanded section delivery to the effective `llm.default` in
configure and update, even when it is absent from `llm.providers`, with names
deduplicated. `omc implement` now ensures the launch provider after the
committed-design gate and before launch, using start's best-effort reporting.
Uninstall scans all supported provider paths for owned sections without
loading config, so deselection, overrides, and missing or malformed config do
not leave owned sections behind. Regression tests cover each path, plus the
accepted normalization from `b"user"` to `b"user\n"` on round-trip. This is
a delivery refinement only: config schema, plugin selection, root files, and
marker format remain as designed.
