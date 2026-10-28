from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .base import Provider


class CodexProvider(Provider):
    name = "codex"

    def families(self) -> list[str]:
        return ["astra", "sol"]

    def effort_levels(self) -> list[str]:
        return ["low", "medium", "high", "xhigh", "max", "ultra"]

    def default_task_model(self, task: str) -> str:
        defaults = {
            "orchestrator": "sol:high",
            "design": "astra",
            "plan": "astra",
            "review": "astra",
            "simple": "sol:medium",
            "medium": "sol:high",
            "high": "astra",
        }
        try:
            return defaults[task]
        except KeyError as exc:
            raise ValueError(f"unknown task: {task}") from exc

    def instructions_file(self, env: Mapping[str, str]) -> Path:
        # Codex 0.158 loads this global AGENTS.md in every session.
        return self._instruction_dir(env, "CODEX_HOME", ".codex") / "AGENTS.md"

    def models(self):
        return []  # free-text entry; codex model ids move fast

    def headless_argv(
        self, prompt, *, model, effort="", allowed_tools=None, session_name="", served_catalog=False
    ):
        # `codex exec` is the non-interactive entry point; prompt is the trailing
        # positional; -m is the model flag. allowed_tools has no codex equivalent;
        # served_catalog has none either (`codex debug models` is already live).
        # --skip-git-repo-check: verified against codex 0.144 — without it, exec
        # refuses to run in a directory the user hasn't interactively trusted,
        # which a headless one-shot call can never satisfy.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        argv.append(prompt)
        return argv

    def session_argv(self, *, session_name, model, seed, notifications=True, effort=""):
        # No session-name flag exists — codex names sessions internally; omc's
        # terminal title carries the slug instead.
        # Codex now updates the terminal title itself. An empty item list
        # disables those writes, preserving omc's slug for this session only.
        # Verified with the real 0.156.1 TUI: default emits OSC 0; [] emits none.
        argv = ["codex", "-c", "tui.terminal_title=[]"]
        if model:
            argv += ["-m", model]
        # Codex 0.158.0: tui.notifications controls native alerts; the native
        # defaults are notification_method=auto and notification_condition=unfocused.
        # This session override leaves the user's global notify command intact.
        argv += ["-c", f"tui.notifications={str(notifications).lower()}"]
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        argv.append(seed)
        return argv

    def title_env(self):
        return {}  # title suppression is a session_argv config override

    def install_hint(self):
        return "npm install -g @openai/codex"

    # Codex 0.158.0 (local CLI, 2026-10-27): marketplace list and plugin list
    # expose JSON; add/remove mutate the named registration only. A repeated
    # `plugin add --json` recopies skill bytes even at the same version and
    # reports installedPath. Marketplace upgrade accepts a specific name and
    # refreshes only Git snapshots; local sources need no marketplace upgrade.
    # A broken local manifest makes both list commands fail, but marketplace
    # remove by known name still succeeds, permitting a supported repair.
    def plugin_marketplace_list_argv(self) -> list[str]:
        return ["codex", "plugin", "marketplace", "list", "--json"]

    def plugin_marketplace_add_argv(self, source: str) -> list[str]:
        return ["codex", "plugin", "marketplace", "add", source, "--json"]

    def plugin_marketplace_remove_argv(self) -> list[str]:
        return ["codex", "plugin", "marketplace", "remove", "oh-my-clanker", "--json"]

    def plugin_marketplace_upgrade_argv(self) -> list[str]:
        return ["codex", "plugin", "marketplace", "upgrade", "oh-my-clanker", "--json"]

    def plugin_list_argv(self) -> list[str]:
        return ["codex", "plugin", "list", "--json"]

    def plugin_add_argv(self) -> list[str]:
        return ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"]

    def plugin_update_argvs(self, marketplace_source: str | None = None) -> list[list[str]]:
        # The generic argv loop cannot verify registration and installed bytes.
        # installer.run_update uses plugin.update_codex_plugin for Codex.
        return []
