# E2E Test Infrastructure & Artifacts — tests

# E2E Test Infrastructure & Artifacts — `tests`

## Purpose

`tests/__init__.py` marks the `tests/` directory as a Python package. The file itself is empty — it carries no logic, constants, fixtures, or re-exports. Its only effect is structural: it makes `tests` importable as a package (`import tests`, `from tests.something import ...`) rather than a loose collection of scripts.

## Why it exists

For a package-style test layout, an `__init__.py` at the root of `tests/` is what allows:

- Test modules under `tests/` (and any subpackages, e.g. `tests/e2e/`, `tests/fixtures/`) to use relative imports between each other.
- Test runners and tooling (pytest, coverage, IDEs) to resolve `tests.*` as a stable package path when collecting tests, rather than relying on `rootdir`-relative implicit namespace packages.
- Shared test utilities living elsewhere in the suite to import from `tests` unambiguously.

## What to expect here

Because the file is intentionally empty, there is nothing to configure or extend in `tests/__init__.py` itself:

- Don't add test fixtures, helper functions, or `pytest` hooks here — those belong in `conftest.py` (which pytest auto-discovers independently of `__init__.py`) or in dedicated helper modules within the package.
- Don't add package-level imports or `__all__` — doing so would create import side effects that run every time any test in the package is collected, which is rarely desirable for a test suite.

## Relationship to the rest of the suite

This file has no internal calls, outgoing calls, or incoming calls — it's a marker, not a code unit. Actual E2E test logic, fixtures, and artifact handling live in sibling modules and subpackages under `tests/`. When contributing new E2E tests, add them as new modules/subpackages under this directory; `tests/__init__.py` requires no changes unless the package needs to expose something explicitly at the `tests` level, which should be a deliberate, separately-justified decision rather than a default.