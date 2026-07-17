# Load E2E provider tokens from .env (gitignored + dockerignored; see env.example).
set dotenv-load

# Fast gate: lint + format check + unit tests. No LLM, no network, no Docker.
build:
    uvx ruff format --check .
    uvx ruff check .
    uv run pytest -m "not e2e" -q

# Dockerized E2E suite (real LLMs; token-gated per provider, fails loud, never skips).
e2e-tests *args:
    uv run pytest -m e2e -q {{args}}

# Install omc from this checkout (dev snapshot). Re-run after edits.
install:
    uv tool install --reinstall .
