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
        self,
        prompt: str,
        *,
        model: str,
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
        notify_sink_argv: list[str] | None = None,
    ) -> list[str]:
        """Interactive session seeded with ``seed``; named where the CLI supports it.

        ``notify_sink_argv``, when set, is the notification sink command; the
        provider that wires notifications via argv (codex) places it itself —
        flag ordering is provider-specific. File-wired providers ignore it.
        """

    def notification_setup(self, sink_argv: list[str]) -> dict[str, str]:
        """Worktree-relative path -> file content wiring this provider's
        "needs attention" events to ``sink_argv``. {} = no file wiring.
        Pure like everything here — the caller writes the files."""
        return {}

    def headless_stream_argv(
        self,
        prompt: str,
        *,
        model: str,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        """Like headless_argv, but for LIVE streaming consumption. Default:
        same argv — codex/opencode already emit incremental text. Providers
        that buffer their print mode (claude) override with a streaming
        output format."""
        return self.headless_argv(prompt, model=model, allowed_tools=allowed_tools)

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

    @abstractmethod
    def plugin_update_argvs(self) -> list[list[str]]:
        """Commands that update this provider's installed omc plugin, in order.

        [] means no scriptable update path is known yet — `omc update` says so
        and moves on. Builders stay pure (no I/O)."""
