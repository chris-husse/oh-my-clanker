from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PluginEntry:
    """One installed plugin, as the harness reports it.

    ``problems`` is why it won't serve skills; it includes the disabled
    reason so callers check one field instead of also consulting ``enabled``.
    """

    id: str  # "omc@oh-my-clanker"
    enabled: bool
    problems: tuple[str, ...]


@dataclass(frozen=True)
class PluginFacts:
    """What one probe round learned about a harness's plugin state."""

    omc: PluginEntry | None = None
    superpowers: PluginEntry | None = None
    marketplace_source: str | None = None  # registered source; None = unknown


@dataclass(frozen=True)
class RepairStep:
    """One command in a repair plan, bundled with its narration, its failure
    policy, the status it contributes, and its manual-fix hint.

    Per-step metadata follows probe.py's convention (spec tuples of
    (name, argv, hint) -> ProbeResult carries the hint alongside the argv):
    the provider owns the provider-specific text, the caller renders it.
    """

    argv: list[str]
    label: str  # narrated before the step runs
    fatal: bool  # False = best-effort self-heal, failure tolerated
    action: str = ""  # contributes to ensure_plugin's status; last non-empty wins
    manual_fix: str = ""  # rendered as "fix manually: <text>" on fatal failure


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

    def notifies_natively(self) -> bool:
        """True when this harness posts its own desktop notification for
        attention events — omc's macos backend then stays silent to avoid
        duplicates. File backends always log regardless."""
        return False

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

    def model_catalog_argv(self) -> list[str]:
        """Command whose stdout is this harness's model catalog.

        ``[]`` means no scriptable catalog exists — callers fall back to the
        static ``models()`` list. Same split as the plugin seam: this names
        the command, ``parse_model_catalog`` reads its output, and the caller
        does the I/O. Pure.
        """
        return []

    def parse_model_catalog(self, stdout: str) -> list[str]:
        """Selectable model ids from ``model_catalog_argv()``'s stdout, best
        first. Pure; raises on malformed input so the caller can fall back."""
        return []

    def explain_failure(self, output: str) -> str | None:
        """A precise remediation for a known-shape failure in this harness's
        output, or None.

        Providers fail in ways their own output explains badly — a wrong model
        id, for instance, reads as an HTTP 400 about "organization policy".
        The caller shows this INSTEAD of dumping the raw transcript, so the
        signatures live in the adapter that knows them. Pure.
        """
        return None

    @abstractmethod
    def title_env(self) -> dict[str, str]:
        """Env that stops the CLI from clobbering the terminal title ({} if none exists)."""

    @abstractmethod
    def install_hint(self) -> str:
        """One-line install command for this provider's CLI."""

    def docs_model_default(self) -> str:
        """Model for documentation/wiki runs when docs_model is unconfigured.

        The standard-coding-tier floor. "" = pass no model flag and let the
        provider CLI use its own default coding model (codex/opencode ids are
        free-text and move fast — pinning one here would rot)."""
        return ""

    def plugin_probe_argvs(self) -> list[list[str]]:
        """Commands whose stdout carries this harness's plugin state, in the
        order ``parse_plugin_facts`` expects them.

        ``[]`` means no scriptable probe exists for this provider — callers
        report the plugin as unverified and never mutate anything. Pure.

        Probe argvs MUST be side-effect-free: plugin.py runs them BEFORE the
        ``check_only`` early return, so --dry-run's "mutates nothing"
        guarantee rests entirely on this. A read-only listing, never an add,
        an update, or anything that touches a snapshot on disk.
        """
        return []

    def parse_plugin_facts(self, stdouts: list[str]) -> PluginFacts:
        """Turn probe stdout (one entry per ``plugin_probe_argvs()`` command,
        same order) into facts. Default: an all-``None`` ``PluginFacts``
        (nothing known). Pure — no I/O, and it must not raise on
        well-formed-but-empty output.

        "Well-formed-but-empty" means a payload the harness legitimately emits
        when NOTHING is installed (``{}``, an absent key, a ``null`` value, an
        empty array) — a fresh machine is exactly the machine that needs the
        install, and plugin.py:_probe turns any parse exception into a fatal
        error that would block `omc start` there. An empty *string* is NOT
        that case: it means the CLI printed nothing at all, a broken probe
        rather than an empty state, so raising on it stays within contract —
        claude's parser does, matching the pre-seam plugin.py."""
        return PluginFacts()

    def plugin_repair_argvs(
        self, facts: PluginFacts, *, source: str, update: bool
    ) -> list[RepairStep]:
        """Ordered plan that makes ``facts`` healthy. ``[]`` = nothing to do.
        Pure: state in, commands out — the caller executes them."""
        return []
