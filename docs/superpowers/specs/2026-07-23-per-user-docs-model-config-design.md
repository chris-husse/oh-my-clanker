# per-user docs model — wiki runs never inherit the session model

Approved 2026-07-23. Root cause (investigated live): every GitNexus wiki run
(`omc watch --enable-documentation`, `omc dependency watch` → `omc internal
dependency document`, the `gitnexus-document` skill) passed the SESSION model
(`llm.providers.<name>.model`, e.g. claude-fable-5) to `gitnexus wiki`. A
top-tier thinking model on dozens of per-module calls, with no timeout and a
TTY-only progress bar, is indistinguishable from a hang — hummingbird-bridge
never finished documenting. Docs generation is grounded bulk summarization;
its floor is the STANDARD CODING TIER, not the session model.

## The setting (per-user, global config)

`ProviderConfig` gains `docs_model: str = ""` — persisted under
`llm.providers.<name>.docs_model` in `~/.omc/config.yaml` (GlobalConfig;
never project config). Blank = the provider's docs default:

- **claude** → `claude-sonnet-5` (the standard-coding-tier floor, pinnable
  because the claude provider already enumerates model ids).
- **codex / opencode** → `""` = pass no `--model`, letting the CLI use its
  own default coding model (this repo deliberately keeps their model ids
  free-text — "ids move fast" — so pinning one would rot).

Resolution helper `docs_model_for(cfg, name)` in `providers/registry.py`:
`cfg.llm.providers[name].docs_model` or `get_provider(name).docs_model_default()`.
`Provider.docs_model_default()` lives on the base class (returns `""`);
claude overrides it.

## Call sites (the fix proper)

- `watch.py:_refresh_index` and `dependency.py:run_document`: wiki argv is
  `wiki --provider <name>` plus `--model <docs_model_for(...)>` when
  non-empty. The session `pcfg.model` is NEVER consulted for wiki runs.
  For claude the flag is therefore ALWAYS passed — which also overrides the
  stale model gitnexus caches in its own `~/.gitnexus/config.json`.
- `store.set_key`: `llm.providers.<name>.docs_model` becomes settable
  (`omc configure --set`); interactive configure stays silent about it.
- `skills/gitnexus-document/SKILL.md`: prose updated — use the docs model,
  never the session model; name the defaults.
- README: one line documenting the setting.

## Testing

- Registry: claude `docs_model_default() == "claude-sonnet-5"`;
  codex/opencode `== ""`; `docs_model_for` override + fallback.
- `run_document`: session model set (e.g. `opus-x`) + docs_model unset →
  wiki argv contains `--model claude-sonnet-5` and NOT `opus-x`;
  docs_model set → it wins.
- `watch._refresh_index`: default config → wiki argv carries
  `--model claude-sonnet-5`.
- `set_key` accepts `llm.providers.claude.docs_model`, still rejects
  unknown provider leaves.

Out of scope (follow-ups already discussed, not this ticket): streaming
wiki progress into `dependency watch`, a wiki `--timeout`, and the
`omc update` plugin force-reinstall branch.
