from __future__ import annotations

import json

from .base import Provider


class CodexProvider(Provider):
    name = "codex"

    def models(self):
        return []  # free-text entry; codex model ids move fast

    def headless_argv(self, prompt, *, model, allowed_tools=None, session_name=""):
        # `codex exec` is the non-interactive entry point; prompt is the trailing
        # positional; -m is the model flag. allowed_tools has no codex equivalent.
        # --skip-git-repo-check: verified against codex 0.144 — without it, exec
        # refuses to run in a directory the user hasn't interactively trusted,
        # which a headless one-shot call can never satisfy.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        argv.append(prompt)
        return argv

    def session_argv(self, *, session_name, model, seed, notify_sink_argv=None):
        # No session-name flag exists — codex names sessions internally; omc's
        # terminal title carries the slug instead.
        argv = ["codex"]
        if model:
            argv += ["-m", model]
        if notify_sink_argv:
            # -c overrides one config.toml key for THIS session only (the global
            # config is never touched). The value is TOML — a JSON array of
            # strings happens to be valid TOML array syntax. Must precede the
            # seed: the prompt is a trailing positional.
            argv += ["-c", f"notify={json.dumps(notify_sink_argv)}"]
        argv.append(seed)
        return argv

    def title_env(self):
        return {}  # no suppression env exists; our OSC write happens after codex starts

    def install_hint(self):
        return "npm install -g @openai/codex"

    def plugin_update_argvs(self):
        # Refreshes ALL configured git marketplace snapshots (no per-marketplace
        # filter exists); plugins resolve from the refreshed snapshot. Verified
        # empirically in docker/PLUGIN-NOTES.md (Task 9 records the run).
        return [["codex", "plugin", "marketplace", "upgrade"]]
