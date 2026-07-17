# CLI & Entry Point

# CLI & Entry Point

The `omc` command-line interface. This module owns argument parsing, top-level command dispatch, and the process exit contract. It is the single front door for every user-facing `omc` subcommand — everything a person types on the command line arrives here first.

Two files make up the module:

- **`src/omc/__init__.py`** — resolves the package version.
- **`src/omc/cli.py`** — the parser, the dispatcher, and `main()`.

## Version resolution (`__init__.py`)

`__version__` is read from installed package metadata via `importlib.metadata.version("omc")`. In a dev checkout where the package isn't installed, `PackageNotFoundError` is caught and the version falls back to `"0.0.0-dev"`. This string is what `--version` and the startup banner print, so it distinguishes an installed build from a working tree at a glance.

## Request lifecycle

`main(argv)` is the entry point (wired as the console script). It follows a fixed sequence: intercept the hidden `internal` command, parse arguments, build the environment context, print the banner, and dispatch.

```mermaid
flowchart TD
    A[main] --> B{argv[0] == internal?}
    B -->|yes| C[run_internal — machine I/O, no banner]
    B -->|no| D[build_parser → parse_args]
    D --> E{command given?}
    E -->|no| F[print help to stderr, return 2]
    E -->|yes| G[ToolContext.from_env]
    G --> H[banner to stderr]
    H --> I[_dispatch]
    I -->|OmcError| J[error to stderr, return exc.rc]
```

### The `internal` fast path

Before argparse ever runs, `main` checks whether the first raw argument is `internal`. If so it hands the remaining args straight to `run_internal` (imported lazily from `.internal`) and returns. This is deliberate: `internal` is the hidden plumbing that skills use to talk to the CLI. Keeping it ahead of `build_parser` means it never appears in `--help`, produces clean machine-readable stdout, and skips the human banner. It is not a registered subparser and should not become one.

### Banner and exit discipline

For every command except `version`, `main` prints `Oh My Clanker! v<version>` to **stderr** — never stdout, so it can't corrupt machine-readable output. Dispatch runs inside a `try` that catches `OmcError` and returns its carried return code (`exc.rc`). This is the one place the error contract is enforced for the whole CLI:

| Exit code | Meaning |
|-----------|---------|
| 0 | success |
| 1 | error (`OmcError`) |
| 2 | refusal / misuse — no command given, or unconfigured |
| 3 | bail (reserved for `omc internal`) |

A bare `omc` with no subcommand prints help to stderr and returns `2`.

## The parser (`build_parser`)

`build_parser` constructs a fresh `ArgumentParser` (prog `omc`) with `--version` and a subparser group. Building it fresh each call keeps it side-effect free — note that `main` calls it a second time to print help when no command is supplied. The registered subcommands:

- **`version`** — print version plus install source.
- **`configure`** — pick your LLM and write `~/.omc/config.json`. Supports `--defaults` (no prompts) and repeatable `--set KEY=VALUE` for non-interactive dotted-key writes.
- **`start CONTEXT`** — begin work on a ticket key, ticket URL, or quoted task description. Flags: `--dry-run` (print the plan, change nothing) and `--headless` (print-mode session, no exec).
- **`watch`** — keep the primary checkout's base branch and knowledge graph fresh. `--interval` seconds between ticks (default 300), `--once` for a single tick, `--enable-documentation` to also regenerate LLM docs (flagged as costly).
- **`install [PATH]`** — (re)install omc from a local checkout (default `.`).
- **`update`** — update from the source omc was installed from.
- **`uninstall`** — remove the binary and `~/.omc`.

## Dispatch (`_dispatch`)

`_dispatch` maps the parsed `command` to a handler. Every handler beyond parsing is **imported lazily inside the branch that needs it** (`run_configure`, `run_install`, `version_string`, etc.). This keeps startup cheap — typing `omc version` never imports the installer, and a broken optional dependency can't stop unrelated commands from running. An unrecognized command raises `OmcError`, which `main` turns into an exit code.

Handlers live in sibling modules and receive the `ToolContext` (and, where relevant, the loaded config):

- `version` → `installsrc.version_string(ctx.env)`
- `start` → `start.run_start`
- `watch` → `watch.run_watch`
- `configure` → `configure.run_configure`
- `install` / `update` / `uninstall` → `installer.run_*`

### The config gate

`start` and `watch` require an existing configuration. Both call `_load_cfg_or_bail(ctx)`, which loads config via `store.load(ctx.home)`. If none exists it prints `error: omc is not configured — run \`omc configure\` first.` to stderr and returns `None`; the caller then returns `2` (refusal). This guarantees no work-performing command runs against an unconfigured environment.

## Connections to the rest of the codebase

The CLI is intentionally thin — a router, not a worker. Its couplings:

- **`ToolContext` (`toolctx.py`)** is the sole subprocess/environment boundary. `main` builds it once via `ToolContext.from_env()` and threads it into every handler. Per the repo's architectural invariants, the CLI itself performs no subprocess or filesystem access beyond this.
- **`config.store`** owns reading `~/.omc/config.json`; the CLI only decides whether the result is usable.
- **`errors.OmcError`** defines the exit-code contract the dispatcher enforces.
- **Command modules** (`start`, `watch`, `configure`, `installer`, `installsrc`, `internal`) do the real work. Each is reachable only through its `_dispatch` branch.

A representative end-to-end flow: `omc configure` runs `main → _dispatch → run_configure`, which walks the user through provider selection by consulting `providers/registry.py` (`provider_names`, `get_provider`). `omc version` runs `main → _dispatch → version_string`, which calls into `installsrc.install_source` to report where omc was installed from (git remote, uv tool dir, redacted for display).

## Extending the CLI

To add a subcommand:

1. Register a subparser in `build_parser` with its help text and arguments.
2. Add a branch to `_dispatch` that lazily imports the handler and passes `ctx` (and config, if the command should require configuration — reuse `_load_cfg_or_bail` and return `2` on `None`).
3. Keep the handler in its own module; the CLI stays a router.

Because everything routes through `main`, honor the existing conventions: banner and diagnostics go to stderr, machine output goes to stdout, and failures raise `OmcError` with the right return code rather than calling `sys.exit` directly. Per the repo's testing policy, a new command needs a test that captures its behavior and fails first — assert on artifacts and exit codes, not on the stderr banner.