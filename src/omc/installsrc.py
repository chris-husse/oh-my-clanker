from __future__ import annotations

from collections.abc import Mapping

from . import __version__


def version_string(env: Mapping[str, str]) -> str:
    return f"omc {__version__}"  # extended with install source in the installer task
