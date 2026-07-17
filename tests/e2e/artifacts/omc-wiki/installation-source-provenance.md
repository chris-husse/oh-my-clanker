# Installation & Source Provenance

# Installation & Source Provenance

This module manages omc's own lifecycle: installing it from a checkout, upgrading it, removing it cleanly, and answering the question "where did this omc come from?" It spans three small files, all of which delegate the actual work to `uv` (Astral's Python tool manager) and to omc's own resources.

| File | Responsibility |
|------|---------------|
| `installer.py` | Install / update / uninstall commands — thin `uv tool` wrappers |
| `installsrc.py` | Read `uv`'s install receipt to report provenance and version |
| `skills_source.py` | Locate bundled skill text (wheel asset or dev checkout) |

All three are reached from `_dispatch` in `src/omc/cli.py`, which wires the `omc install`, `omc update`, `omc uninstall`, and `omc --version` commands. `skill_text` is reached from a different path — `build_prompt` in `src/omc/slug.py` — but shares the "find omc's own files on disk" concern, so it lives alongside the provenance code.

## Design stance: gate-exempt, uv-authoritative

Two principles run through the module:

- **These commands are gate-exempt by design.** Unlike most omc behavior, install/update/uninstall don't route through the project's build/verify/review stages. They are bootstrap operations — you may be running them precisely because omc isn't installed yet.
- **`uv` is the source of truth.** omc never maintains its own record of where it was installed from or what version is live. `run_install` hands a path to `uv tool install`; `install_source` reads back the receipt `uv` wrote. There is no second bookkeeping system to drift out of sync.

## Installation lifecycle (`installer.py`)

Every command funnels its subprocess work through `_uv`, which is the module's single point of contact with `uv`:

```python
def _uv(ctx: ToolContext, *args: str) -> int:
    try:
        cp = ctx.run(ctx.uv_argv(*args), capture=False)
    except FileNotFoundError:
        print(_UV_MISSING, file=sys.stderr)
        return 1
    return cp.returncode
```

`_uv` builds the argv via `ctx.uv_argv(...)` and runs it through `ctx.run(...)` — both methods of `ToolContext`, the module's only subprocess/env boundary (see `src/omc/toolctx.py`). `capture=False` lets `uv`'s own progress stream straight to the terminal. The one error `_uv` handles itself is a missing `uv` binary: rather than leak a `FileNotFoundError`, it prints the canonical install-uv instructions (`_UV_MISSING`) and returns exit code 1.

### `run_install(ctx, path)`

Installs omc from a local checkout. It resolves `path` to an absolute path, validates it, then runs `uv tool install --reinstall <abspath>`:

- **`validate_checkout(path)`** is the gate. It returns an error string (or `None` if valid). A valid checkout must be a directory containing both `.git` and `src/omc/__init__.py` — the minimal signature of an omc source tree. This is also exported for callers that want to check a path without triggering an install.
- The **absolute path matters**: on success, the printed message notes that future `omc update`s are now "re-rooted" at that path. `uv` records the directory in its receipt, so `uv tool upgrade` later knows to pull from that same checkout.
- `--reinstall` forces a clean rebuild even if the version number hasn't changed — important during development when the code changes but `__version__` doesn't.

### `run_update(ctx)`

The simplest command: `uv tool upgrade omc`. Because `uv` remembers the install source from the receipt, this re-pulls from wherever the original `run_install` pointed — a local directory, a git URL, or PyPI.

### `run_uninstall(ctx)`

Removal happens in three steps, ordered so a refusal on the first step still leaves the tool itself removable:

```mermaid
flowchart TD
    A[run_uninstall] --> B{_is_unsafe_home?}
    B -- yes --> C[refuse: skip data removal]
    B -- no --> D{ctx.home exists?}
    D -- yes --> E[shutil.rmtree ctx.home]
    C --> F[uv tool uninstall omc]
    D -- no --> F
    E --> F
    F --> G[print plugin-removal instructions]
```

1. **Data directory.** `ctx.home` (omc's data dir, typically `~/.omc`) is recursively deleted — but only after `_is_unsafe_home` clears it.
2. **The tool itself** via `uv tool uninstall omc`.
3. **Plugin instructions.** omc can't uninstall its plugin from Claude Code / Codex / OpenCode on the user's behalf, so it prints per-harness removal steps (`_PLUGIN_REMOVAL`).

The safety guard is worth reading closely:

```python
def _is_unsafe_home(home: Path, env) -> bool:
    resolved = home.resolve()
    user_home = Path(env.get("HOME", "~")).expanduser().resolve()
    return str(resolved) == resolved.anchor or resolved == user_home
```

It refuses to `rmtree` two things: a filesystem anchor (`/`, or a drive root — `resolved.anchor`) and the user's literal `$HOME`. Critically, it reads `HOME` from `ctx.env` rather than the process environment. That indirection is what makes the guard testable and makes it honor sandboxed contexts where `HOME` has been redirected. The final exit code reflects only the `uv tool uninstall` result; a refused data removal prints a warning but does not, by itself, fail the command.

## Source provenance (`installsrc.py`)

This file answers `omc --version` with something more useful than a bare version number — it reports *where* the running omc was installed from. The public entry points are `install_source` and `version_string`.

### Reading the receipt

`uv` writes an install receipt when it installs a tool. `install_source` locates and parses it:

```python
receipt = _uv_tool_dir(env) / "omc" / "uv-receipt.toml"
```

**`_uv_tool_dir(env)`** reproduces `uv`'s own directory-resolution precedence, reading from the passed-in env mapping:

1. `UV_TOOL_DIR` if set (explicit override)
2. `$XDG_DATA_HOME/uv/tools`
3. `$HOME/.local/share/uv/tools` (the default)

`install_source` then parses `uv-receipt.toml`, pulls `tool.requirements`, and finds the requirement named `omc` (falling back to the first requirement if none is named). The requirement dict's *key* tells you the install kind, and each is handled in priority order:

| Key present | Meaning | Returned `is_remote` |
|-------------|---------|----------------------|
| `directory` | Installed from a local directory | `False` |
| `editable`  | Editable/dev install | `False` |
| `git`       | Installed from a git ref | `True` |
| `url`       | Installed from a URL | `_is_remote_git(url)` |
| *(none)*    | PyPI package | `False` |

The whole parse is wrapped in a broad `except` catching `OSError`, `KeyError`, `IndexError`, `TypeError`, `ValueError`, `AttributeError`. **Any** problem — no receipt, malformed TOML, unexpected shape — collapses to the sentinel `("unknown", False)`. Provenance reporting must never crash `omc --version`.

### Two classification helpers

- **`_is_remote_git(source)`** decides whether a bare `url` is actually a remote git source. It matches known schemes (`ssh://`, `https://`, `http://`, `git+`, `git://`) or the SCP short form (`user@host:path`, via the `_SCP_FORM` regex). Empty or `"unknown"` sources are never remote.
- **`_redact(source)`** strips embedded credentials before anything is displayed — a URL like `git+https://oauth2:TOKEN@host` becomes `git+https://[REDACTED]@host`. This runs on **every** returned source string, so tokens baked into an install URL never surface in `--version` output or logs.

### `version_string(env)`

The thin display wrapper `_dispatch` actually calls:

```python
def version_string(env):
    source, _ = install_source(env)
    return f"omc {__version__} from {source}"
```

It pairs `__version__` (from `omc/__init__.py`) with the redacted source — e.g. `omc 1.2.3 from /home/dev/oh-my-clanker` or `omc 1.2.3 from unknown`.

## Bundled skill resolution (`skills_source.py`)

`skill_text(name)` returns the raw text of a bundled `SKILL.md`, trying two locations in order:

1. **Wheel asset** — `importlib.resources.files("omc") / "assets" / "skills" / <name> / "SKILL.md"`. This is where skills live in an installed wheel.
2. **Dev-checkout fallback** — `<repo-root>/skills/<name>/SKILL.md`, computed as `parents[2]` of this file. This is where skills live when running from source.

If neither exists, it raises `OmcError(f"bundled skill {name!r} not found (broken install?)")`. The resource lookup tolerates `ModuleNotFoundError`, `FileNotFoundError`, and `NotADirectoryError` so a missing wheel asset falls through to the dev path rather than raising. This two-location strategy mirrors the module's broader theme: the same code must behave whether omc is a packaged install or a live checkout.

## How it connects

- **`cli.py` `_dispatch`** is the primary caller — it maps CLI verbs to `run_install`, `run_update`, `run_uninstall`, and `version_string`.
- **`toolctx.py` `ToolContext`** is the sole subprocess and environment gateway. `_uv` never touches `subprocess` directly; it uses `ctx.uv_argv` and `ctx.run`, whose `child_env` shapes the environment passed to `uv`. `run_uninstall` and `_is_unsafe_home` similarly read `ctx.home` and `ctx.env` rather than the process globals.
- **`slug.py` `build_prompt`** consumes `skill_text` to embed bundled skill instructions into generated prompts.
- **`errors.py`** supplies `OmcError`, the exit-code-1 error type raised on a broken skill install.

The design keeps this whole surface stateless with respect to omc: `uv` owns the install records, the resource loader finds files by convention, and every function that touches the environment or filesystem does so through `ToolContext` so the behavior stays testable and sandbox-aware.