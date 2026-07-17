"""Where was omc installed from? uv's receipt is the authority."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from . import __version__

_REMOTE_SCHEME = ("ssh://", "https://", "http://", "git+", "git://")
_SCP_FORM = re.compile(r"^[^/@]+@[^/:]+:")


def _is_remote_git(source: str) -> bool:
    if not source or source == "unknown":
        return False
    return source.startswith(_REMOTE_SCHEME) or bool(_SCP_FORM.match(source))


def _uv_tool_dir(env: Mapping[str, str]) -> Path:
    if env.get("UV_TOOL_DIR"):
        return Path(env["UV_TOOL_DIR"])
    if env.get("XDG_DATA_HOME"):
        return Path(env["XDG_DATA_HOME"]) / "uv" / "tools"
    return Path(env.get("HOME", "~")).expanduser() / ".local" / "share" / "uv" / "tools"


def install_source(env: Mapping[str, str]) -> tuple[str, bool]:
    """(display_source, is_remote_git) from uv's receipt; ("unknown", False) on any problem."""
    receipt = _uv_tool_dir(env) / "omc" / "uv-receipt.toml"
    try:
        data = tomllib.loads(receipt.read_text())
        reqs = data["tool"]["requirements"]
        req = next((r for r in reqs if r.get("name") == "omc"), None) or reqs[0]
    except (OSError, tomllib.TOMLDecodeError, KeyError, IndexError, TypeError):
        return "unknown", False
    if "directory" in req:
        return str(req["directory"]), False
    if "editable" in req:
        return str(req["editable"]), False
    if "git" in req:
        return str(req["git"]), True
    if "url" in req:
        url = str(req["url"])
        return url, _is_remote_git(url)
    return f"{req.get('name', 'omc')} (PyPI)", False


def version_string(env: Mapping[str, str]) -> str:
    source, _ = install_source(env)
    return f"omc {__version__} from {source}"
