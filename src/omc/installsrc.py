"""Where was omc installed from? uv's receipt is the authority."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from . import __version__, _buildinfo

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


def _redact(source: str) -> str:
    """Strip embedded credentials (e.g. git+https://oauth2:TOKEN@host) before display."""
    return re.sub(r"://[^/@]+@", "://[REDACTED]@", source)


def package_root() -> Path:
    """Absolute directory of the installed omc package (contains distribution/).

    importlib.resources over __file__ math: works identically for wheel
    installs (uv tool venv) and the dev checkout (src/omc). uv tool venvs are
    real directories, so the result is a valid symlink target.
    """
    from importlib import resources

    return Path(str(resources.files("omc")))


def provenance() -> dict[str, str]:
    """Build provenance as a fresh dict: ``{branch, commit, source}``.

    All ``"unknown"`` for a source install where the build hook never fired
    (the checked-in ``_buildinfo`` fallback). A new dict each call so callers
    can mutate without affecting later reads.
    """
    return {
        "branch": _buildinfo.BRANCH,
        "commit": _buildinfo.COMMIT,
        "source": _buildinfo.SOURCE,
    }


def install_source(env: Mapping[str, str]) -> tuple[str, bool]:
    """(display_source, is_remote_git) from uv's receipt; ("unknown", False) on any problem."""
    receipt = _uv_tool_dir(env) / "omc" / "uv-receipt.toml"
    try:
        data = tomllib.loads(receipt.read_text())
        reqs = data["tool"]["requirements"]
        req = next((r for r in reqs if r.get("name") == "omc"), None) or reqs[0]
    except (
        OSError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        AttributeError,
    ):
        return "unknown", False
    if "directory" in req:
        return _redact(str(req["directory"])), False
    if "editable" in req:
        return _redact(str(req["editable"])), False
    if "git" in req:
        return _redact(str(req["git"])), True
    if "url" in req:
        url = str(req["url"])
        return _redact(url), _is_remote_git(url)
    return _redact(f"{req.get('name', 'omc')} (PyPI)"), False


def version_string(env: Mapping[str, str]) -> str:
    """``omc <v> [(branch@commit)] from <source> [(origin <remote>)]``.

    ``(branch@commit)`` is build provenance — what the binary IS; omitted for
    source installs where the hook never fired. ``from <source>`` is uv's
    receipt — where it was installed from. ``(origin <remote>)`` names the
    checkout's remote for directory installs; a remote-git install's from-URL
    already IS the remote, so the suffix would be noise there.
    """
    source, is_remote = install_source(env)
    prov = provenance()
    parts = [f"omc {__version__}"]
    if not (prov["branch"] == "unknown" and prov["commit"] == "unknown"):
        parts.append(f"({prov['branch']}@{prov['commit']})")
    parts.append(f"from {source}")
    if not is_remote and _is_remote_git(prov["source"]):
        parts.append(f"(origin {_redact(prov['source'])})")
    return " ".join(parts)
