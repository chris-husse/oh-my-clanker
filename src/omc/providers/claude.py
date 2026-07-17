from __future__ import annotations

from .base import Provider


class ClaudeProvider(Provider):
    name = "claude"

    def models(self):
        return ["claude-fable-5", "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]

    def headless_argv(self, prompt, *, model, allowed_tools=None):
        # Prompt must come RIGHT AFTER -p: --allowed-tools is variadic and would
        # swallow a trailing positional as a tool name. Keep --allowed-tools LAST
        # and omit it entirely when empty (an empty value parses as a bogus tool).
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            argv += ["--model", model]
        if allowed_tools:
            argv += ["--allowed-tools", *allowed_tools]
        return argv

    def session_argv(self, *, session_name, model, seed):
        argv = ["claude"]
        if session_name:
            argv += ["-n", session_name]  # resumable later via `claude --resume <name>`
        if model:
            argv += ["--model", model]
        argv.append(seed)
        return argv

    def title_env(self):
        return {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}

    def install_hint(self):
        return "npm install -g @anthropic-ai/claude-code"
