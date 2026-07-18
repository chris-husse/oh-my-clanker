# Model-Tier Policy for the Plan/Implement Flow — Design

**Date:** 2026-07-18
**Slug:** `brainstorm-plan-model-tier-policy`
**Status:** approved (brainstorm converged in-session; user pre-approved through finish)

## Problem

Plans produced through omc's brainstorm → plan → implement lifecycle carry no
model assignments, and the behavior layer's current "Model selection" bullet
("efficient models for well-specified execution work") actively invites
dropping to Haiku-class models for coding tasks. The user's policy: spec &
review work gets the top tier, coding never drops below Sonnet-class, bigger
coding tasks get Opus-class, and the cheap/fast tier is never used — for
Claude *and* for OpenAI-family providers.

The policy must be **tier-abstract and self-adapting**: baselined on Claude's
Fable/Opus/Sonnet stack but resolved against the provider's *current* lineup
at dispatch time, so it survives model releases without edits, and maps
itself onto other providers' hierarchies in realtime.

## Approach (chosen: A — canonical block + thin pointers)

The full policy lives in exactly one place: `src/omc/distribution/AGENTS.md`,
the behavior layer symlinked into every omc-managed repo and refreshed by
`omc update`. Skills that hand off to external superpowers skills carry only
short directives that *apply* the policy. One source of truth, no drift; the
plan document is the durable artifact that carries per-task assignments
downstream.

Rejected: (B) duplicating the full policy text into each handoff — three
copies drift, and the plan doc already propagates the assignments; (C) CLI
enforcement (per-tier config keys, provider tier→model maps) — the harnesses'
Agent tools accept tier names directly, so the machinery is unnecessary
(explicit user scope decision).

Deployment note: root `AGENTS.md`/`CLAUDE.md` in omc-managed repos symlink to
the *installed* package's `distribution/AGENTS.md` (`src/omc/agentsmd.py:43`,
chain v2). Editing the repo copy therefore takes effect everywhere on the
next `omc update`/reinstall — no per-repo migration needed, and `watch.py`'s
chain-health probe is unaffected (the file continues to exist).

## The policy text (canonical)

Replaces the **Model selection** bullet in `src/omc/distribution/AGENTS.md`.
The old "efficient models for well-specified execution work" phrasing is
dropped — it contradicts the Sonnet-class floor.

> - **Model selection**: the main session runs the model chosen in
>   `omc configure` — never second-guess it. When dispatching subagents or
>   assigning models to plan tasks, apply the **model-tier policy**. Tiers
>   are abstract, baselined on Claude's stack, and resolved at dispatch time
>   against the provider's *current* lineup: pin the **top tier** to whatever
>   is the latest & best model available (Fable-class today), then
>   reinterpret the **heavy coding tier** (Opus-class) and the **standard
>   coding tier** (Sonnet-class) down the current hierarchy. On other
>   providers (OpenAI, …), map the tiers to that provider's current
>   equivalents the same way.
>   - Spec, review, and judging tasks → **top tier**.
>   - Coding tasks → never below the **standard coding tier**; bigger coding
>     tasks (multi-file, architecturally tricky, or ambiguous) → **heavy
>     coding tier**.
>   - The cheap/fast tier (Haiku-class or its equivalent on any provider) is
>     **never used**, for anything.

"Bigger coding task" is deliberately a planner judgment call via the
"(multi-file, architecturally tricky, or ambiguous)" heuristic — no hard
rule (user decision, Q2).

## Touch points

1. **`src/omc/distribution/AGENTS.md`** — the canonical policy block above.
2. **`skills/implement/SKILL.md`**
   - *Phase 2 (writing-plans)*: directive that every task in the plan
     carries an explicit **`Model:` line naming its tier** per the behavior
     layer's model-tier policy (e.g. `Model: standard coding tier`,
     `Model: top tier`). Tier names only — never pinned model ids — so plans
     stay self-adapting (user decision, Q3).
   - *Phase 3 (subagent-driven-development)*: directive that each task's
     subagent is dispatched with its `Model:` tier resolved against the
     current lineup via the Agent tool's model parameter, and that
     reviewer/judge subagents always get the top tier.
3. **`skills/plan/SKILL.md`** — the Step 4 brainstorm handoff gains a
   one-line policy pointer, because `superpowers:brainstorming`'s own
   checklist can invoke `writing-plans` directly (without `/omc:implement`
   conducting); plans born that way must also carry Model lines.
4. **`skills/spec/SKILL.md`** — unchanged: the spec is written by the main
   session, whose model is sacred. "Spec tasks → top tier" applies only when
   spec/review work is *delegated*, which the behavior-layer block covers.

## Data flow

Brainstorm/plan handoffs carry the policy pointer → `writing-plans` writes
per-task `Model:` tier lines into `docs/superpowers/plans/*.md` →
`subagent-driven-development` resolves each tier against the provider's
current lineup at dispatch and passes it to the Agent tool → reviewer
subagents get top tier regardless of the task under review.

## Edge cases

- **Unknown lineup** (new provider, renamed models): tier definitions are
  relative ("latest & best", "one step down"), so the dispatching model
  resolves them against whatever it knows; no hard-coded id goes stale.
- **Harness without per-subagent model switching** (Codex/OpenCode not
  established): honor tiers where the harness supports them; otherwise
  proceed on the session model — never silently substitute a cheaper tier.
- **Plan missing Model lines** (hand-written/legacy plans): executors fall
  back to the behavior-layer policy directly (review → top tier, coding →
  standard-or-better).

## Testing (red→green contract tests, per plan/implement precedent)

Needle tests in `tests/unit/test_plugin_manifests.py`, written first and
watched to fail:

- New `test_distribution_agents_model_tier_policy`: `src/omc/distribution/
  AGENTS.md` contains tier-policy needles ("model-tier policy", "top tier",
  "never used") AND the old "efficient models" phrasing is gone. (No
  existing test asserts the old phrasing — verified — so replacing it breaks
  nothing.)
- Extend `test_implement_skill_contract` with the Phase 2 `Model:`-line
  directive needle and the Phase 3 dispatch-resolution needle (existing
  needles and the spec→plan→build→ship order assertion keep passing).
- Extend `test_plan_skill_contract` with the Step 4 policy-pointer needle.

Live-E2E (a real plan produced in Docker asserting per-task Model lines) is
**deferred to the ledger**, matching how the plan/implement feature itself
was validated.

## Out of scope

- Removing `claude-haiku-4-5` from `src/omc/providers/claude.py:models()` —
  that list feeds `omc configure`'s *main-session* picker, a different
  concern from subagent/task dispatch (explicit user scope decision).
- Any per-tier config keys or provider mapping code.
