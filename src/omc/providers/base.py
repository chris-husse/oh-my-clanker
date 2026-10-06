from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

from ..errors import OmcError


class Provider(ABC):
    """An agentic CLI omc can drive. Argv builders are pure (no I/O)."""

    name: str

    @abstractmethod
    def models(self) -> list[str]:
        """Known model ids for the config picker; [] means free-text entry."""

    @abstractmethod
    def families(self) -> list[str]:
        """Model families available as task choices."""

    @abstractmethod
    def effort_levels(self) -> list[str]:
        """Statically valid effort names for this provider."""

    @abstractmethod
    def default_task_model(self, task: str) -> str:
        """Default family[:effort] for a task, including orchestrator."""

    @abstractmethod
    def headless_argv(
        self,
        prompt: str,
        *,
        model: str,
        effort: str = "",
        allowed_tools: list[str] | None = None,
        session_name: str = "",
    ) -> list[str]:
        """One-shot print-mode run against the user's system config.

        ``session_name`` names the resulting session where the CLI supports it
        (claude: ``-n``, resumable via ``--resume <name>`` — verified live);
        providers without session naming ignore it.
        """

    @abstractmethod
    def session_argv(
        self,
        *,
        session_name: str,
        model: str,
        seed: str,
        notifications: bool = True,
        effort: str = "",
    ) -> list[str]:
        """Interactive session with explicit native notification state."""

    def notification_setup(self, notifications: bool) -> dict[str, str]:
        """Worktree-relative native settings fragment; {} means argv-only."""
        return {}

    def headless_stream_argv(
        self,
        prompt: str,
        *,
        model: str,
        effort: str = "",
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        """Like headless_argv, but for LIVE streaming consumption. Default:
        same argv — codex already emits incremental text. Providers
        that buffer their print mode (claude) override with a streaming
        output format."""
        return self.headless_argv(prompt, model=model, effort=effort, allowed_tools=allowed_tools)

    def decode_stream_line(self, line: str) -> list[str]:
        """Decode ONE raw child output line into human-readable text lines.
        Default: identity. Providers whose stream is an event protocol
        (claude stream-json) override to unwrap events; multi-line texts
        must be split so line-anchored contracts (OMC_STAGE) survive."""
        return [line]

    @abstractmethod
    def title_env(self) -> dict[str, str]:
        """Env that stops the CLI from clobbering the terminal title ({} if none exists)."""

    @abstractmethod
    def install_hint(self) -> str:
        """One-line install command for this provider's CLI."""

    def docs_model_default(self) -> str:
        """Model for documentation/wiki runs when docs_model is unconfigured.

        The standard-coding-tier floor. "" = pass no model flag and let the
        provider CLI use its own default coding model (codex ids are
        free-text and move fast — pinning one here would rot)."""
        return ""

    def api_base_url(self) -> str:
        """HTTP API base URL for the `api` documentation backend, WITH a trailing
        slash. "" = this provider has no API backend: configure refuses
        `llm.docs.backend=api` for it (spec 2026-10-01 §2.1/§5)."""
        return ""

    def api_model_family(self, alias: str) -> str:
        """Model-id prefix for a CLI family alias ("sonnet" -> "claude-sonnet-").
        "" = not a family alias, treat the value as a full model id. Full ids are
        resolved from the provider's LIVE model list at configure time — there is
        deliberately no static alias→id table anywhere, so nothing rots."""
        return ""

    def auth_status_argv(self) -> list[str]:
        """Argv that reports the CLI's login state as JSON with a `loggedIn`
        boolean; [] = no such command (the headless model probe is the only
        login check). Pure like everything here."""
        return []

    @abstractmethod
    def instructions_file(self, env: Mapping[str, str]) -> Path:
        """Global instruction file path derived solely from the supplied environment."""

    @staticmethod
    def _instruction_dir(env: Mapping[str, str], override: str, default: str) -> Path:
        raw = env.get(override)
        home = env.get("HOME")
        if not raw:
            if not home:
                raise OmcError(f"HOME is required to locate global instructions for {override}")
            raw = str(Path(home) / default)
        if raw == "~" or raw.startswith("~/"):
            if not home:
                raise OmcError(f"HOME is required to expand {override}")
            return Path(home) if raw == "~" else Path(home) / raw[2:]
        return Path(raw)

    @abstractmethod
    def plugin_update_argvs(self, marketplace_source: str | None = None) -> list[list[str]]:
        """Commands that update this provider's installed omc plugin, in order.

        ``marketplace_source`` (owner/repo or a local path) lets a provider
        self-heal a missing marketplace registration; providers that don't need
        it ignore the argument. [] means no scriptable update path is known yet.
        Builders stay pure (no I/O)."""
