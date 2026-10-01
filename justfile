# Load E2E provider tokens from .env (gitignored + dockerignored; see env.example).
set dotenv-load

# Quick gate: build what the unit tests need, then run them. No LLM, no Docker.
# Parallel: every unit test is hermetic (tests/conftest.py) and must stay so.
check:
    uv run pytest -m "not e2e and not local_iterm2" -q -n auto

# macOS native acceptance: a PRIVATE copy of iTerm2 (never your running app), fish, real provider TUIs.
iterm2-tests *args:
    uv run pytest -m local_iterm2 -q tests/local {{args}}

# Build the world: format check + lint + package build. NO tests (see `check`).
build:
    uvx ruff format --check .
    uvx ruff check .
    uv build

# Dockerized E2E suite (real LLMs; token-gated per provider, fails loud, never skips).
# Builds the image once, runs the golden lifecycle stages sequentially
# (snapshotting each), then everything else in parallel. Excludes the
# `expensive` tier and the Codex gate.
e2e-tests *args:
    bash scripts/e2e.sh all {{args}}

# Only the golden lifecycle path (refreshes the stage snapshots).
golden *args:
    bash scripts/e2e.sh golden {{args}}

# The golden path INCLUDING the expensive `implemented` stage (evidence run for
# omc's implement duration; writes the `implemented` snapshot).
golden-full *args:
    bash scripts/e2e.sh golden -m "e2e and golden and not codex_gate" {{args}}

# Housekeeping: remove E2E images and snapshots that do not belong to this tree.
e2e-prune:
    bash scripts/e2e.sh prune

# Everything except the golden path, in parallel (variations need existing snapshots).
e2e-rest *args:
    bash scripts/e2e.sh rest {{args}}

# Codex integration gate: serial on the account volume (needs `just codex-login`).
# Run when a change touches the Codex provider, driver, plugin payload or setup.
codex-gate *args:
    CODEX_AUTH_VOLUME=${CODEX_AUTH_VOLUME:-omc-e2e-codex-auth} bash scripts/e2e.sh rest -m "e2e and codex_gate and not expensive" -n 1 {{args}}

# The old monolithic lifecycle cases (expensive tier): evidence runs, never a gate.
lifecycle-full *args:
    uv run pytest -m "e2e and expensive" -q tests/e2e/test_e2e_lifecycle_full.py {{args}}

# Interactive device login into the dedicated Codex E2E Docker volume.
codex-login:
    bash docker/codex-login.sh

# LLM-heavy E2E (documentation generation). Costs real money - run only with
# explicit user agreement.
expensive-e2e-tests *args:
    uv run pytest -m "e2e and expensive and not golden and not variation" -q {{args}}

# Install omc from this checkout (dev snapshot). Re-run after edits.
install:
    uv tool install --reinstall .
