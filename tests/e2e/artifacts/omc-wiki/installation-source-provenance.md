# Installation & Source Provenance

# Installation & Source Provenance

Everything covering how `omc` gets onto a machine, how it self-heals its editor plugin, how it knows what it's supposed to be probing for, and how it reports where it actually came from. Four modules do the work:

- **`src/omc/installer.py`** — `install` / `update` / `uninstall`, thin wrappers around `uv`.
- **`src/omc/installsrc.py`** — reads `uv`'s install receipt to answer "where did this binary come from?"
- **`src/omc/plugin.py`** — self-heals the Claude Code plugin so `/omc:*` slash commands resolve.
- **`src/omc/probe.py`** — parallel `--version` checks that gate `omc update` and `omc start`.

`src/omc/errors.py` (`OmcError`, `Refusal`) and `hatch_build.py` / `src/omc/_buildinfo.py` (build-time provenance stamping) support all four.

## Install: `run_install`

`run_install(ctx, path)` re-roots `omc` at a local checkout: it resolves `path` to an absolute path, runs it through `validate_checkout` (must contain `.git` and `src/omc/__init__.py`), and — if valid — shells out to `uv tool install --reinstall <abspath>`. A bad path never touches `uv` at all; `validate_checkout` is checked and printed to stderr before any subprocess runs. This is how `chris-husse/oh-my-clanker` gets treated identically to any other dev checkout: `uv`'s own receipt (see below) becomes the new source of truth for every subsequent `omc update`.

## Update: `run_update`

`run_update(ctx)` is a strict two-stage pipeline, and the ordering is load-bearing:

```mermaid
flowchart LR
    A[uv tool upgrade omc] --> B[require_tools<br/>git/wt/provider]
    B -->|miss| X[raise OmcError<br/>abort, nothing cloned]
    B -->|ok| C[gitnexus.update_gitnexus]
    C --> D[per-provider plugin update]
    D -->|failure| D
```

1. `_uv(ctx, "tool", "upgrade", "omc")` — if this fails, return immediately.
2. If a global config exists, `require_tools(ctx, cfg)` runs as a **fatal gate** before anything else — a machine missing `wt` aborts before GitNexus is touched. `test_update_aborts_when_required_tool_missing` pins this ordering explicitly (it fails the test if `update_gitnexus` runs at all).
3. `gitnexus.update_gitnexus(ctx)` refreshes the managed GitNexus dependency. Imported as a module attribute (`from . import gitnexus`) specifically so tests can monkeypatch `omc.gitnexus.update_gitnexus`. Its return code becomes `run_update`'s return code — a GitNexus failure fails the whole update.
4. For each configured provider, `get_provider(name).plugin_update_argvs(marketplace_source(ctx.env))` yields a sequence of argvs to run. Only the **last** argv's exit code decides pass/fail for that provider — earlier steps (typically `marketplace add`/`marketplace update`, which self-heal a missing registration) are allowed to fail silently. Any provider's failure is caught, printed with a `✗` prefix, and the loop continues to the next provider; it never aborts the whole update. Missing/unknown providers raise `OmcError` from `get_provider`, which is caught the same way.

No config at all short-circuits straight past the plugin loop with a `no config — skipping plugin updates` message — `run_update` still returns whatever GitNexus reported.

## Uninstall: `run_uninstall`

Two independent halves, both best-effort:

- **Data removal**: `_is_unsafe_home(home, env)` refuses to `shutil.rmtree` if `ctx.home` resolves to either the filesystem root or the user's actual `$HOME` (read from `ctx.env`, not the process env, so it's testable and honors sandboxed contexts). Otherwise `ctx.home` is removed with `ignore_errors=True`.
- **Tool removal**: `uv tool uninstall omc` via `_uv`.

It always prints `_PLUGIN_REMOVAL` (per-harness manual plugin-removal instructions) and returns `0` unless the `uv` step failed — a refused home deletion does **not** fail the command, since the intent (get `omc` off the machine) still succeeded.

## Provenance: `installsrc.py`

Two distinct notions of "where omc is from," both feeding `version_string`:

- **Build provenance** (`provenance()`) — `branch`/`commit`/`source` baked into the artifact at build time by `hatch_build.py`. It reads from `_buildinfo.py`, whose checked-in copy is all `"unknown"`; a wheel/sdist build overwrites that file via `force_include` (see below). `provenance()` returns a fresh dict every call so callers can't accidentally mutate shared state.
- **Install provenance** (`install_source(env)`) — where `uv` actually installed *this* binary from, read live from `uv`'s own receipt (`<uv-tool-dir>/omc/uv-receipt.toml`). `_uv_tool_dir` resolves the receipt location via `UV_TOOL_DIR` → `XDG_DATA_HOME` → `~/.local/share/uv/tools`. The receipt's `requirements[0]` (or the entry named `"omc"`) is inspected for `directory`, `editable`, `git`, or `url` keys, in that order; anything unparseable — missing file, non-UTF-8 bytes, malformed TOML, wrong shape — collapses to `("unknown", False)` rather than raising.

`version_string(env)` composes both into one line: `omc <version> [(branch@commit)] from <source> [(origin <remote>)]`. The `(origin …)` suffix only appears for a **directory** install whose build provenance points at a remote git origin — a remote-git install's own `from <source>` already *is* that remote, so repeating it would be noise.

Every URL that reaches display goes through `_redact`, which strips `userinfo@` credentials (`git+https://oauth2:TOKEN@host` → `git+https://[REDACTED]@host`) before it's ever printed — this is the same protection `hatch_build._redact` applies at build time (see below), just applied again at read time as belt-and-braces.

`package_root()` returns the installed package's directory via `importlib.resources`, which resolves identically for a wheel-installed `uv` tool venv and an editable dev checkout; `agentsmd.py`'s `distribution_agents_md` uses it to locate the shipped `AGENTS.md`.

## Build-time stamping: `hatch_build.py`

`BuildInfoHook` is a Hatchling build hook (`initialize`) that runs during `uv tool install`/`uv build`. It resolves `(branch, commit, source)` via `_resolve`, preferring `OMC_BUILD_BRANCH`/`OMC_BUILD_COMMIT`/`OMC_BUILD_SOURCE` env vars, falling back to `git rev-parse`/`git remote get-url origin` against the source tree, and finally `"unknown"` if there's no `.git` at all. Deliberately, it never writes into the source tree — `_render`'s output is written to a temp file and injected via `build_data["force_include"]`, replacing `src/omc/_buildinfo.py` **only inside the built artifact**. This keeps `uv sync`/`uv run` in an editable checkout from ever dirtying the working tree.

`_redact` here mirrors `installsrc._redact` but is stricter: it strips any `userinfo@` unless it's exactly the identity-free `git@` SSH login, and — unlike a naive colon-based check — also treats colonless tokens (`https://ghp_xxx@host`) as credentials.

## Plugin self-heal: `plugin.py`

`omc start` seeds a session with `/omc:start`; if the Claude Code plugin was never installed, that's an "Unknown command" on the very first interaction. `ensure_plugin(ctx, cfg, check_only=False)` prevents that:

1. Only `claude` has a scriptable check today — any other configured provider returns `"unverified (no scriptable check for this provider yet)"` and is left alone.
2. Runs `claude plugin list`; if `"omc@"` appears in stdout, returns `"ok"`.
3. If missing and `check_only` is set (the dry-run path used by `omc configure`'s planning output), returns `"missing (omc start will install it)"` without installing anything.
4. Otherwise it resolves `marketplace_source(ctx.env)` and runs `claude plugin marketplace add <source>` (failure here is a benign self-heal — the marketplace may already be registered) followed by `claude plugin install omc@oh-my-clanker --scope user`. A failing install raises `OmcError` with the exact manual fix-it commands. A successful-looking install is re-verified with another `plugin list` call, since it "could still be missing after an apparently successful install."

`marketplace_source(env)` picks where the marketplace should pull from, using the same install receipt as `installsrc`: a remote GitHub install (`git+https://github.com/x/y` or scp-form) maps to `owner/repo`; a directory/editable install reuses that path directly (local dev loop); anything else — including a bare PyPI install — falls back to the canonical `chris-husse/oh-my-clanker`. This is the same pinned-source trust model as the CLI install itself: no consent prompt, because the plugin is omc's own repo.

## Tool probing: `probe.py`

`run_probes(ctx, specs)` runs a list of `(name, argv, hint)` specs through `ThreadPoolExecutor` concurrently, each calling `tool_version` (a *real* subprocess invocation — never a file-existence check) and returning a `ProbeResult(name, present, detail, hint)`.

`require_tools(ctx, cfg)` is the fatal variant used by both `run_update` and `run_start`/`run_watch`: it probes `git`, `wt`, and whichever provider is configured (`get_provider(cfg.llm.default)`), collects every miss (not just the first), and raises a single `OmcError` listing all of them with their install hints. This "list every miss, not just the first" behavior is deliberate — `test_require_tools_lists_all_misses` pins it — so a developer missing two tools doesn't have to run the probe twice to find out about the second.

## How it fits together

```mermaid
flowchart TD
    CLI["_dispatch (omc/cli)"] --> run_install
    CLI --> run_update
    CLI --> run_uninstall
    CLI -->|omc version| version_string
    run_update --> require_tools
    run_update --> marketplace_source
    run_start --> require_tools
    run_start --> ensure_plugin
    ensure_plugin --> marketplace_source
    marketplace_source --> install_source
    version_string --> install_source
    version_string --> provenance
```

`install_source`/`marketplace_source` are the connective tissue: `omc version`, `omc update`'s plugin loop, and `ensure_plugin`'s self-heal all resolve "where did this install come from" through the exact same `uv` receipt, so a directory-checkout install, a GitHub install, and a PyPI install are each handled consistently everywhere that question comes up. `errors.py`'s `OmcError`/`Refusal` are the uniform failure vocabulary across all four modules — `OmcError` prints without a traceback and exits 1 (`Refusal` exits 2 for deliberate precondition refusals), so callers in `omc/cli/__init__.py` can treat every module's failures the same way.