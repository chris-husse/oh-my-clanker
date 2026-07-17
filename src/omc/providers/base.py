from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    """An agentic CLI omc can drive. Argv builders are pure (no I/O)."""

    name: str

    @abstractmethod
    def models(self) -> list[str]:
        """Known model ids for the config picker; [] means free-text entry."""

    @abstractmethod
    def headless_argv(
        self, prompt: str, *, model: str, allowed_tools: list[str] | None = None
    ) -> list[str]:
        """One-shot print-mode run against the user's system config."""

    @abstractmethod
    def session_argv(self, *, session_name: str, model: str, seed: str) -> list[str]:
        """Interactive session seeded with ``seed``; named where the CLI supports it."""

    @abstractmethod
    def title_env(self) -> dict[str, str]:
        """Env that stops the CLI from clobbering the terminal title ({} if none exists)."""

    @abstractmethod
    def install_hint(self) -> str:
        """One-line install command for this provider's CLI."""
