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

    def headless_stream_argv(self, prompt, *, model, allowed_tools=None):
        # stream-json + --verbose emits one JSON event per line AS IT HAPPENS
        # (verified 2026-07-19: tool_use/tool_result arrive live; plain
        # `--output-format text` prints only at exit). Same flag-ordering
        # constraint as headless_argv: --allowed-tools stays LAST.
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if model:
            argv += ["--model", model]
        if allowed_tools:
            argv += ["--allowed-tools", *allowed_tools]
        return argv

    def decode_stream_line(self, line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return [line] if line.strip() else []
        if not isinstance(event, dict):
            return [line]
        out: list[str] = []
        kind = event.get("type")
        if kind == "assistant":
            for block in event.get("message", {}).get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    out.extend(str(block["text"]).splitlines())
                elif block.get("type") == "tool_use":
                    command = (block.get("input") or {}).get("command")
                    out.append(f"$ {command}" if command else f"[{block.get('name', 'tool')}]")
        elif kind == "user":
            for block in event.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, str):
                        out.extend(content.splitlines())
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                out.extend(str(part.get("text", "")).splitlines())
        elif kind == "result":
            result = event.get("result")
            if isinstance(result, str):
                out.extend(result.splitlines())
        # system / thinking / rate_limit events decode to nothing
        return out

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
