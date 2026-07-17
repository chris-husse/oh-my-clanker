"""install / update / uninstall — thin uv wrappers. Gate-exempt by design."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .toolctx import ToolContext

_UV_MISSING = (
    "error: uv not found; install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
)

_PLUGIN_REMOVAL = """\
The omc plugin (if installed) is removed per harness:
  Claude Code:  /plugin uninstall omc
  Codex:        remove 'omc' via /plugins
  OpenCode:     drop the omc entry from opencode.json's "plugin" array
"""


def validate_checkout(path: str) -> str | None:
    """Error message when `path` isn't an omc checkout, else None."""
    root = Path(path)
    if not root.is_dir():
        return f"{path} is not a directory"
    if not (root / ".git").exists() or not (root / "src" / "omc" / "__init__.py").is_file():
        return f"{path} doesn't look like an omc checkout (need .git and src/omc/)"
    return None


def _uv(ctx: ToolContext, *args: str) -> int:
    try:
        cp = ctx.run(ctx.uv_argv(*args), capture=False)
    except FileNotFoundError:
        print(_UV_MISSING, file=sys.stderr)
        return 1
    return cp.returncode


def run_install(ctx: ToolContext, path: str) -> int:
    abspath = str(Path(path).resolve())
    err = validate_checkout(abspath)
    if err is not None:
        print(err, file=sys.stderr)
        return 1
    rc = _uv(ctx, "tool", "install", "--reinstall", abspath)
    if rc == 0:
        print(f"Installed omc (re-rooted future `omc update`s at {abspath}).")
    return rc


def run_update(ctx: ToolContext) -> int:
    print("Updating omc via uv…", file=sys.stderr)
    return _uv(ctx, "tool", "upgrade", "omc")


def _is_unsafe_home(home: Path, env) -> bool:
    """Never recursively delete / or the user's $HOME. Uses ctx.env's HOME (not the
    process env) so the guard is testable and honors sandboxed contexts."""
    resolved = home.resolve()
    user_home = Path(env.get("HOME", "~")).expanduser().resolve()
    return str(resolved) == resolved.anchor or resolved == user_home


def run_uninstall(ctx: ToolContext) -> int:
    if _is_unsafe_home(ctx.home, ctx.env):
        print(
            f"refuse: OMC_HOME ({ctx.home}) is unsafe to delete; skipping data removal",
            file=sys.stderr,
        )
    elif ctx.home.exists():
        shutil.rmtree(ctx.home, ignore_errors=True)
        print(f"Removed {ctx.home}")
    rc = _uv(ctx, "tool", "uninstall", "omc")
    print(_PLUGIN_REMOVAL)
    return 0 if rc == 0 else 1
