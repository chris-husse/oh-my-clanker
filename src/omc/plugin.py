"""Plugin self-heal: the seeded /omc:* commands must actually resolve.

`omc start` seeds the session with `/omc:start`; if the omc plugin was never
installed for the provider, the session opens on "Unknown command" — the
worst possible first-run. So start ensures the plugin exists, installing it
automatically when missing. No consent prompt: the plugin is omc's own pinned
repo, the same trust model as the CLI install itself (chicken precedent for
pinned-source dependencies).
"""

from __future__ import annotations

import re
import sys

from .config.schema import Config
from .errors import OmcError
from .installsrc import install_source
from .toolctx import ToolContext

PLUGIN_REF = "omc@oh-my-clanker"
_FALLBACK_SOURCE = "chris-husse/oh-my-clanker"


def marketplace_source(env) -> str:
    """Where `claude plugin marketplace add` should pull omc from: the same
    place uv installed the CLI from — a directory checkout stays local, a
    GitHub install uses owner/repo, anything else falls back to the canonical
    public repo."""
    src, is_remote = install_source(env)
    if is_remote:
        m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", src)
        if m:
            return m.group(1)
    elif src != "unknown" and not src.endswith("(PyPI)"):
        return src
    return _FALLBACK_SOURCE


def ensure_plugin(ctx: ToolContext, cfg: Config, *, check_only: bool = False) -> str:
    """Ensure the omc plugin is installed for the configured provider.

    Returns a short status string for the progress/plan output. Only claude
    has a verified scriptable probe + install today; other providers are left
    alone. ``check_only`` (the --dry-run path) never installs anything.
    """
    if cfg.llm.default != "claude":
        return "unverified (no scriptable check for this provider yet)"
    try:
        cp = ctx.run(["claude", "plugin", "list"])
    except OSError as exc:
        raise OmcError(f"could not run `claude plugin list`: {exc}") from exc
    if cp.returncode == 0 and "omc@" in (cp.stdout or ""):
        return "ok"
    if check_only:
        return "missing (omc start will install it)"

    source = marketplace_source(ctx.env)
    print(f"  installing the omc plugin from {source}…", file=sys.stderr, flush=True)
    # The marketplace may already be registered from an earlier attempt —
    # a failed add is fine as long as the install below succeeds.
    ctx.run(["claude", "plugin", "marketplace", "add", source])
    cp = ctx.run(["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"])
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(
            "could not install the omc plugin for claude automatically.\n"
            f"  {detail}\n"
            f"  fix manually: claude plugin marketplace add {source} && "
            f"claude plugin install {PLUGIN_REF}"
        )
    cp = ctx.run(["claude", "plugin", "list"])
    if "omc@" not in (cp.stdout or ""):
        raise OmcError(
            "the omc plugin is still missing after an apparently successful install — "
            "check `claude plugin list` and the plugin's Status line"
        )
    return "installed"
