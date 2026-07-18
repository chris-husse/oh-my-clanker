# Build provenance in `omc version` (chicken `omk` port)

Date: 2026-07-18 · Status: approved design · Branch: feature/version-provenance
(stacked on feature/cops-988-add-slack-ping-on-idle until that PR merges)

## 1. Problem

`omc version` prints `omc 0.1.0 from <install source>` — the uv-receipt
install base only. It cannot answer "what code IS this binary": which
branch/commit it was built from, or (for a directory install) which remote
that checkout tracks. A real diagnosis round-trip happened on 2026-07-17:
an installed omc predating the notifications feature rejected
`notifications.enabled` as an unknown config key, and nothing in `omc
version` could show the version skew. The chicken's `omk` solved exactly
this with build-time provenance stamping, which omc v1 explicitly stripped
(`docs/superpowers/specs/2026-07-17-omc-v1-design.md` §11). This ports it
back, adapted.

Reference implementation: `chris.husse/chicken` @ kakarot.chorse.space —
`hatch_build.py`, `src/omk/_buildinfo.py`, `src/omk/buildinfo.py`.

## 2. Approach (alternatives rejected)

**Build-time stamping** (the chicken's): a hatchling build hook resolves
provenance when the wheel is built and bakes it into the artifact. Truthful
about the BINARY even after the source directory moves on.

Rejected:

- *Runtime git probe of the install dir* — shows the directory's CURRENT
  commit, not the binary's; would have actively lied in the 2026-07-17
  incident.
- *uv receipt only* — carries no commit for directory installs (the common
  dev case).

## 3. Components

1. **`hatch_build.py`** (repo root, new) — ported from the chicken:
   - Resolution order per field: `OMC_BUILD_BRANCH` / `OMC_BUILD_COMMIT` /
     `OMC_BUILD_SOURCE` env vars → `.git` probes (`rev-parse
     --abbrev-ref HEAD`, `rev-parse --short HEAD`, `remote get-url
     origin`) → `"unknown"`.
   - Credential redaction before anything lands in the artifact: strip
     `user:password@` from URLs (bare `user@host` ssh forms preserved).
   - Writes the generated module to a temp file and `force_include`s it as
     `omc/_buildinfo.py` — never writes into the source tree. omc's
     existing static skills force-include in pyproject is unrelated and
     unchanged; the chicken's skills-staging part of the hook is NOT
     ported.
   - Guarded hatchling import (chicken-style) so the module's pure helpers
     are unit-testable in the runtime venv where hatchling is absent.
   - Registered via `[tool.hatch.build.targets.wheel.hooks.custom]` (and
     sdist equivalent) in pyproject.toml.
2. **`src/omc/_buildinfo.py`** (new, checked in) — all-`"unknown"`
   fallback; overwritten only inside built artifacts. Source installs and
   editable runs keep it untouched, so `git status` never dirties.
3. **`src/omc/installsrc.py`** grows `provenance() -> dict[str, str]`
   (`{branch, commit, source}` fresh dict per call, from `_buildinfo`).
   No new module: omc already centralizes install-source logic here
   (unlike the chicken's separate buildinfo.py — deliberate divergence).
4. **`version_string` new format**:

   ```
   omc 0.1.0 (feature/version-provenance@ab12cd3) from /Users/x/oh-my-clanker.feature-version-provenance (origin git@github.com:chris-husse/oh-my-clanker.git)
   ```

   - `(branch@commit)` — build provenance: what the binary IS. Omitted
     entirely when BOTH are `"unknown"` (source install; no
     `unknown@unknown` noise).
   - `from <source>` — uv receipt, unchanged: where it was installed from.
   - `(origin <url>)` — provenance source-remote, shown ONLY when the
     receipt source is a directory AND the provenance source is a known
     remote: for a local-checkout install this answers "which remote does
     that checkout come from". For remote-git installs the `from` URL
     already IS the remote — suffix omitted.
   - Every displayed URL passes the existing `installsrc._redact`
     (`[REDACTED]` style).

## 4. Testing

- **Unit** (new `tests/unit/test_buildinfo_hook.py` for the hook's pure
  helpers + extensions to the existing `tests/unit/test_installsrc.py`):
  hook resolution order (env overrides git, git fallback, unknown);
  build-time redaction (token URL → stripped; ssh `git@host` preserved);
  `provenance()` fallback shape; `version_string` composition for the four
  shapes (provenance known/unknown × directory/remote-git source).
- **E2E**: extend the existing smoke `test_install_reroot` (or
  `test_configure_and_gate`, whichever asserts `omc version`) with a regex
  assertion that a real containerized `uv tool install` produced a stamped
  `(<branch>@<hex>)` — verifying the hook fires in a genuine build.
- README `## Commands` row for `omc version` updated to "Print version +
  build provenance + install source".

## 5. Out of scope

- `omc update` / `omc install` behavior (already receipt-based).
- Using provenance anywhere beyond display (no gating, no update checks).
- The chicken's skills-catalog staging (omc ships skills statically).
