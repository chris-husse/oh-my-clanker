# Project stages (`.omc/skills/`) + `/omc:squash` — design

Approved 2026-07-17. The chicken's `.omk` stage system, ported minimal: no
doctor repair loop, no bounded retries, no pre-commit stage.

## Convention

A repo opts in by creating `.omc/skills/<stage>/SKILL.md` at its root, for
stage ∈ `build`, `verify`, `review`. The project defines what each stage
means; omc defines only when they run and how their outcome is reported.

## Skills

| Skill | Layer | Does |
|---|---|---|
| `build` / `verify` / `review` | user-facing proxies | resolve project root (`git rev-parse --show-toplevel`, else cwd); `.omc/skills/<stage>/SKILL.md` missing → "nothing to do", pass; present → follow it from the project root. Always end with one machine line: `OMC_STAGE {"stage": "<stage>", "configured": bool, "passed": bool, "summary": "..."}` — unconfigured is `configured: false, passed: true`. |
| `squash` | internal | fold uncommitted changes (with notice), `git reset --soft origin/<base>`, one temp commit; ends `OMC_SQUASH {"ok": true, "commits_folded": N}`. Extracted from finish for future control over the process. |

## `/omc:finish` (reordered)

gate → floor → rebase → **squash** (via the internal skill) → **build →
verify → review** in that order → create-mr → follow-up offers.

- Unconfigured stages are noted and skipped (they pass).
- A FAILED stage stops before push; the branch is left squashed.
- Changes a stage makes to TRACKED files (formatters, autofixes) are amended
  into the squash commit (`git add -u`); untracked artifacts are left alone.

## Dogfooding

This repo defines `.omc/skills/build/SKILL.md` → `just build`. verify/review
stay unconfigured, exercising the no-op path in real use.

## Testing

- Unit: manifest/frontmatter for the four new skills; proxy contract needles
  (`.omc/skills/`, `OMC_STAGE`, unconfigured-passes); squash internal marker +
  `OMC_SQUASH`; finish ordering (build < verify < review < create-mr in the
  skill text).
- E2E (claude, hermetic): existing no-stages finish scenario, plus a repo
  whose build stage PASSES (marker in /tmp proves it ran; push happens) and
  one whose build stage FAILS (origin must NOT receive the branch).
