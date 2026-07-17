"""Resolve packaged skill files: wheel asset first, dev-checkout skills/ fallback."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from .errors import OmcError


def skill_text(name: str) -> str:
    try:
        ref = resources.files("omc") / "assets" / "skills" / name / "SKILL.md"
        if ref.is_file():
            return ref.read_text()
    except (ModuleNotFoundError, FileNotFoundError, NotADirectoryError):
        pass
    dev = Path(__file__).resolve().parents[2] / "skills" / name / "SKILL.md"
    if dev.is_file():
        return dev.read_text()
    raise OmcError(f"bundled skill {name!r} not found (broken install?)")
