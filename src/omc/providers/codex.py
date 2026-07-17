from __future__ import annotations

from .base import Provider


class CodexProvider(Provider):
    name = "codex"

    def models(self):
        return []  # free-text entry; codex model ids move fast

    def headless_argv(self, prompt, *, model, allowed_tools=None):
        # `codex exec` is the non-interactive entry point; prompt is the trailing
        # positional; -m is the model flag. allowed_tools has no codex equivalent.
        argv = ["codex", "exec"]
        if model:
            argv += ["-m", model]
        argv.append(prompt)
        return argv

    def session_argv(self, *, session_name, model, seed):
        # No session-name flag exists — codex names sessions internally; omc's
        # terminal title carries the slug instead.
        argv = ["codex"]
        if model:
            argv += ["-m", model]
        argv.append(seed)
        return argv

    def title_env(self):
        return {}  # no suppression env exists; our OSC write happens after codex starts

    def install_hint(self):
        return "npm install -g @openai/codex"
