"""install / update / uninstall — thin uv wrappers. Gate-exempt by design."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import store
from .errors import OmcError
from .fish_integration import is_owned, managed_fish_path, remove_owned_hook
from .plugin import ensure_plugin, marketplace_source
from .probe import require_tools
from .providers.registry import get_provider
from .toolctx import ToolContext, tool_version

_UV_MISSING = (
    "error: uv not found; install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
)

_PLUGIN_REMOVAL = """\
The omc plugin (if installed) is removed per harness:
  Claude Code:  /plugin uninstall omc
  Codex:        remove 'omc' via /plugins
"""


def _is_macos() -> bool:
    return sys.platform == "darwin"


@dataclass(frozen=True)
class PostInstall:
    version: str | None  # the FRESH on-disk omc's version, when its executable was found
    fish_failure: str | None  # None = provisioned (or the step was skipped off macOS)


def _fresh_cli(ctx: ToolContext) -> Path | None:
    """The just-installed on-disk omc: <uv tool dir --bin>/omc, else <uv tool dir>/omc/bin/omc.

    Resolved through ctx.uv_argv so UV_TOOL_BIN_DIR/UV_TOOL_DIR are honored. The running
    (old) process never imports the new package's fish modules.
    """
    for args, tail in (
        (("tool", "dir", "--bin"), ("omc",)),
        (("tool", "dir"), ("omc", "bin", "omc")),
    ):
        try:
            cp = ctx.run(ctx.uv_argv(*args))
        except OSError:
            return None
        if cp.returncode == 0 and cp.stdout.strip():
            candidate = Path(cp.stdout.strip()).joinpath(*tail)
            if candidate.is_file():
                return candidate
    return None


def post_install(ctx: ToolContext) -> PostInstall:
    """macOS-only: run the fresh CLI's `shell-integration fish reconcile`. Never raises."""
    if not _is_macos():
        return PostInstall(None, None)
    print("→ provisioning fish integration", file=sys.stderr)
    exe = _fresh_cli(ctx)
    if exe is None:
        return PostInstall(None, "installed omc executable not found via `uv tool dir`")
    # Bounded and never raising (tool_version): a hung or broken fresh CLI only costs the
    # version in the summary line; the bounded reconcile below still runs.
    version = None
    ok, detail = tool_version(ctx, [str(exe), "--version"], timeout=5)
    parts = detail.split()
    if ok and len(parts) > 1 and parts[0] == "omc":
        version = parts[1]
    try:
        cp = ctx.run_bounded([str(exe), "shell-integration", "fish", "reconcile"], timeout=30)
    except TimeoutError:
        return PostInstall(version, "timed out after 30s")
    except OSError as exc:
        return PostInstall(version, f"{exe} not runnable ({type(exc).__name__})")
    if cp.stderr:
        sys.stderr.write(cp.stderr if cp.stderr.endswith("\n") else cp.stderr + "\n")
    if cp.returncode != 0:
        return PostInstall(version, f"reconcile exit {cp.returncode}")
    return PostInstall(version, None)


def _report(verb: str, post: PostInstall) -> str:
    omc = f"✓ omc {post.version} {verb}" if post.version else f"✓ omc {verb}"
    if not _is_macos():
        return omc
    if post.fish_failure is None:
        return f"{omc} · ✓ fish integration"
    return f"{omc} · ✗ fish integration: {post.fish_failure}"


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
    if rc != 0:
        return rc
    post = post_install(ctx)
    print(f"Installed omc (re-rooted future `omc update`s at {abspath}).")
    print(_report("installed", post), file=sys.stderr)
    return 1 if post.fish_failure else 0


def _finish_update(post: PostInstall, dep_rc: int) -> int:
    print(_report("updated", post), file=sys.stderr)
    if dep_rc:
        return dep_rc
    return 1 if post.fish_failure else 0


def run_update(ctx: ToolContext) -> int:
    print("Updating omc via uv…", file=sys.stderr)
    rc = _uv(ctx, "tool", "upgrade", "omc")
    if rc != 0:
        return rc
    # Runs the FRESH on-disk CLI; a fish failure is recorded, never raised, so the
    # gates below still run and _finish_update reports both outcomes.
    post = post_install(ctx)
    # Load config up front. When configured, the tool probe is a FATAL gate
    # (git/wt/provider) that runs BEFORE the GitNexus install — a machine
    # without `wt` aborts the update before anything is cloned/built.
    cfg = store.load_global(ctx.home)
    if cfg is not None:
        require_tools(ctx, cfg)  # git/wt/provider — raises OmcError on a miss
    # Managed dependencies (GitNexus). Module-attribute import so tests can
    # monkeypatch omc.gitnexus.update_gitnexus; a failure here must fail the
    # command (unlike the best-effort plugin loop below).
    from . import gitnexus

    dep_rc = gitnexus.update_gitnexus(ctx)
    if cfg is None:
        print("· no config — skipping plugin updates (run `omc configure`)", file=sys.stderr)
        return _finish_update(post, dep_rc)
    source = marketplace_source(ctx.env)
    for name in cfg.llm.providers:
        if name == "claude":
            # Install when missing, reinstall when Claude refuses to load it,
            # refresh when healthy — a plain `plugin update` fails on a plugin
            # that was never installed, which is how first-runs stayed broken.
            try:
                status = ensure_plugin(ctx, name, update=True)
            except OmcError as exc:
                print(f"✗ {name}: {exc} — continuing", file=sys.stderr)
                continue
            print(f"✓ {name}: omc plugin {status}", file=sys.stderr)
            continue
        try:
            argvs = get_provider(name).plugin_update_argvs(source)
        except OmcError as exc:
            print(f"✗ {name}: {exc} — continuing", file=sys.stderr)
            continue
        if not argvs:
            print(f"· {name}: no scriptable plugin update yet — update it in-app", file=sys.stderr)
            continue
        ok = True
        for i, argv in enumerate(argvs):
            is_last = i == len(argvs) - 1
            try:
                cp = ctx.run(argv)
            except OSError as exc:
                print(f"✗ {name}: {argv[0]} not runnable ({exc}) — continuing", file=sys.stderr)
                ok = False
                break
            if cp.returncode != 0 and is_last:
                detail = (cp.stderr or cp.stdout or "").strip()[:200]
                print(f"✗ {name}: {' '.join(argv)} failed: {detail} — continuing", file=sys.stderr)
                ok = False
                break
            # non-last (marketplace add/update) failures are benign self-heal
            # steps — never abort the sequence or mark failure.
        if ok:
            print(f"✓ {name}: plugin updated", file=sys.stderr)
    return _finish_update(post, dep_rc)


def _is_unsafe_home(home: Path, env) -> bool:
    """Never recursively delete / or the user's $HOME. Uses ctx.env's HOME (not the
    process env) so the guard is testable and honors sandboxed contexts."""
    resolved = home.resolve()
    user_home = Path(env.get("HOME", "~")).expanduser().resolve()
    return str(resolved) == resolved.anchor or resolved == user_home


def run_uninstall(ctx: ToolContext) -> int:
    # Every platform: `shell-integration fish enable` works anywhere, so ownership (not the
    # OS) decides. Owned file only; anything else stays, with a note.
    had_hook = is_owned(managed_fish_path(ctx))
    note = remove_owned_hook(ctx)
    if note:
        print(f"· fish integration: {note}", file=sys.stderr)
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
    if had_hook and note is None:
        print(
            "· fish shells already running keep the loaded title hook until they exit",
            file=sys.stderr,
        )
    return 0 if rc == 0 else 1
