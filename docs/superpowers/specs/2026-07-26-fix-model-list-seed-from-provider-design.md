# Fix model list: seed from provider aliases

**Date:** 2026-07-26
**Slug:** fix-model-list-seed-from-provider

## Problem

`ClaudeProvider.models()` returns a hardcoded list of version-pinned model IDs:
`["claude-fable-5", "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]`.
This list is already stale — `claude-opus-5` is missing — and will go stale
again with every new Anthropic release. The `omc configure` picker inherits
whatever `models()` returns, so users don't see current models without a code
change.

## Solution

Replace version-pinned IDs with the CLI aliases that the `claude` CLI resolves
to the latest model in each family on the provider side: `fable`, `opus`,
`sonnet`. This makes the picker self-updating without network calls, API keys,
or new dependencies.

Drop `haiku` from the default choices: the behavior-layer model-tier policy
says the cheap/fast tier is "never used, for anything." Users who want it can
still type any model ID via the existing "Other" escape hatch.

## Changes

### `src/omc/providers/claude.py`

- **`models()`** — return `["fable", "opus", "sonnet"]` instead of the
  version-pinned list.
- **`docs_model_default()`** — return `"sonnet"` instead of `"claude-sonnet-5"`.
  Same tier (standard coding floor), alias form so it tracks the latest Sonnet
  automatically.

### `src/omc/configure.py`

No changes. `_walkthrough_global()` already calls `get_provider(name).models()`
and presents whatever comes back. The "Other (type a model id)…" option already
covers users who want a specific version-pinned ID.

### `src/omc/providers/base.py`

No changes. The `Provider.models()` contract ("known model ids for the config
picker; `[]` means free-text entry") is unchanged — aliases are valid model
identifiers accepted by the CLI.

### Tests

Update assertions that reference the old hardcoded model IDs or
`docs_model_default()` return value:

- `tests/unit/test_providers.py` — no model-list-specific assertions exist,
  but verify no new ones were added.
- `tests/unit/test_docs_model.py` — lines 19, 26, 28 assert
  `"claude-sonnet-5"` as the docs default; change to `"sonnet"`.
- `tests/unit/test_watch.py` — line 122 asserts `"--model claude-sonnet-5"`
  in the recorded wiki command; change to `"--model sonnet"`.
- `tests/unit/test_dependency.py` — line 465 asserts
  `"--model claude-sonnet-5"` in the docs command log; change to
  `"--model sonnet"`.

Tests that use version-pinned IDs as user config values (e.g.
`test_config_store.py` setting `"claude-fable-5"` via `set_key`,
`test_configure.py` setting `"anthropic/claude-sonnet-5"` for OpenCode) are
unaffected — they test the config plumbing, not the default model list.

## Backwards compatibility

`ProviderConfig.model` stores whatever string the user picks as a plain string.
Existing configs with version-pinned IDs like `claude-opus-4-8` continue to
work — they pass through to `--model` unchanged. They just won't appear in the
picker's default choices; the "Other" path covers them if the user wants to
re-select one.

## What this does NOT do

- No network calls, no API-key requirement, no `/v1/models` query.
- No changes to Codex or OpenCode providers (they already return `[]` for
  free-text entry).
- No changes to the `Provider` ABC contract or `configure.py` consumer logic.
