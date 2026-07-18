from __future__ import annotations

import json
import shlex

from .base import Provider


class ClaudeProvider(Provider):
    name = "claude"

    def models(self):
        return ["claude-fable-5", "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]

    def headless_argv(self, prompt, *, model, allowed_tools=None, session_name=""):
        # Prompt must come RIGHT AFTER -p: --allowed-tools is variadic and would
        # swallow a trailing positional as a tool name. Keep --allowed-tools LAST
        # and omit it entirely when empty (an empty value parses as a bogus tool).
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if session_name:
            argv += ["-n", session_name]  # -p sessions persist; resumable by name
        if model:
            argv += ["--model", model]
        if allowed_tools:
            argv += ["--allowed-tools", *allowed_tools]
        return argv

    def session_argv(self, *, session_name, model, seed, notify_sink_argv=None):
        argv = ["claude"]
        if session_name:
            argv += ["-n", session_name]  # resumable later via `claude --resume <name>`
        if model:
            argv += ["--model", model]
        argv.append(seed)
        return argv

    def notification_setup(self, sink_argv):
        # Notification stays UNFILTERED (all attention events) + Stop for turn
        # end — per the COPS-988 design. settings.local.json is Claude's
        # personal per-checkout settings file (conventionally gitignored).
        group = {"hooks": [{"type": "command", "command": shlex.join(sink_argv)}]}
        settings = {"hooks": {"Notification": [group], "Stop": [group]}}
        return {".claude/settings.local.json": json.dumps(settings, indent=2) + "\n"}

    def title_env(self):
        return {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}

    def install_hint(self):
        return "npm install -g @anthropic-ai/claude-code"

    def plugin_update_argvs(self):
        # Marketplace snapshot first, then the plugin; claude docs: "restart
        # required to apply" — running sessions keep the old plugin.
        return [
            ["claude", "plugin", "marketplace", "update", "oh-my-clanker"],
            ["claude", "plugin", "update", "omc@oh-my-clanker"],
        ]
