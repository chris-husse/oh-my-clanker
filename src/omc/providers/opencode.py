from __future__ import annotations

from .base import Provider


class OpencodeProvider(Provider):
    name = "opencode"

    def models(self):
        return []  # free-text `provider/model` entry

    def headless_argv(self, prompt, *, model, allowed_tools=None, session_name=""):
        argv = ["opencode", "run"]
        if model:
            argv += ["-m", model]
        argv.append(prompt)
        return argv

    def session_argv(self, *, session_name, model, seed):
        # Interactive `opencode [project]` — the positional is a DIRECTORY, not a
        # prompt; the seed rides on --prompt. No session-name flag exists.
        argv = ["opencode"]
        if model:
            argv += ["-m", model]
        argv += ["--prompt", seed]
        return argv

    def title_env(self):
        return {"OPENCODE_DISABLE_TERMINAL_TITLE": "1"}

    def install_hint(self):
        return "npm install -g opencode-ai"
