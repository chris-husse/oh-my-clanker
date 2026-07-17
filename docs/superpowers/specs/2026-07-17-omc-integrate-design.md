# `/omc:integrate` — the integration concierge

Approved 2026-07-17 (night). One user-facing skill, two auto-detected modes
(explicit intent via `$ARGUMENTS` wins): FRESH guided setup for a project
without omc surfaces; REVIEW for an existing integration (after omc updates,
or when something seems fishy) — every slot evaluated against the project's
current reality AND omc's current expectations.

## Phase 1 — foundation (mechanical)

1. Inventory the full surface, reported as a status table
   (present/missing/suspicious): the AGENTS.md/CLAUDE.md → `.omc/internal/
   AGENTS.md` chain, `.omc/config/AGENTS.md`, `.config/wt.toml`,
   `.gitnexus/`, `.omc/docs/`, each `.omc/skills/{build,verify,review,
   explain-context}`.
2. Fix mechanical gaps with existing machinery: `omc configure` re-run for
   the chain (re-set the CURRENT `llm.default` read from `~/.omc/config.json`
   — never `--defaults`, which would reset config), `/omc:check-wt-config`
   when the sniff flags the wt config, and OFFER `/omc:index` early — the
   graph grounds Phase 2 (declinable on huge repos; fall back to reading
   build/test artifacts directly).

## Phase 2 — per-slot brainstorm (build → verify → review → explain-context → project AGENTS.md)

Per slot: INVESTIGATE the project first (graph queries plus the real
artifacts — justfile/Makefile/package.json/pyproject/CI for build; test
layout/tiers for verify; CONTRIBUTING/CI review norms for review; docs
layout for explain-context) → PROPOSE a concrete draft grounded in findings
(real commands, real pass criteria, never boilerplate) → ITERATE with the
user → WRITE ONLY ON EXPLICIT APPROVAL. Review mode shows the existing file
against what investigation suggests and flags drift ("skill says `make
test`, CI runs `just build`") and omc-convention gaps.

## Non-interactive degradation

Headless runs (and the E2E hook): inventory + grounded draft proposals as
OUTPUT ONLY — zero writes, heavy steps (index) skipped with a note.

## Phase 3 — wrap-up

After-table (created/updated/left), what's now active (finish stage gates,
explain context, watch cadence), suggest the commit.

## Testing (red → green)

Contract units: slot list complete; both modes; approval-gated writes +
headless-zero-writes language; orchestration references (configure,
check-wt-config, index, explain machinery). Two judged E2Es: FRESH (work
repo with a real justfile + tests dir → correct all-missing inventory,
drafts cite the repo's actual build command, NO files written); REVIEW
(deliberately wrong build skill `make test` vs a justfile-only repo → drift
flagged, file untouched).
