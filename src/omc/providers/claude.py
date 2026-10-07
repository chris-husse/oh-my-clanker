from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .base import Provider

# Anthropic's OpenAI-compatible endpoint (docs read 2026-10-01): /chat/completions
# under this base accepts `Authorization: Bearer <key>`, `max_completion_tokens`,
# system messages (hoisted) and streaming — exactly what GitNexus's llm-client
# sends for `--provider custom`. Trailing slash kept: GitNexus strips it itself.
API_BASE_URL = "https://api.anthropic.com/v1/"

# CLI family aliases -> API id prefixes. The newest id with the prefix is picked
# from the live /v1/models list at configure time (docsllm.resolve_model).
_API_FAMILIES = {"fable": "claude-fable-", "opus": "claude-opus-", "sonnet": "claude-sonnet-"}


class ClaudeProvider(Provider):
    name = "claude"

    def families(self) -> list[str]:
        return ["fable", "opus", "sonnet"]

    def effort_levels(self) -> list[str]:
        return ["low", "medium", "high", "xhigh", "max"]

    def default_task_model(self, task: str) -> str:
        defaults = {
            "orchestrator": "opus",
            "design": "fable",
            "plan": "fable",
            "review": "fable",
            "simple": "sonnet",
            "medium": "opus",
            "high": "fable",
        }
        try:
            return defaults[task]
        except KeyError as exc:
            raise ValueError(f"unknown task: {task}") from exc

    def instructions_file(self, env: Mapping[str, str]) -> Path:
        # Claude Code loads user CLAUDE.md in every session, including projects
        # whose own instruction file is named AGENTS.md.
        return self._instruction_dir(env, "CLAUDE_CONFIG_DIR", ".claude") / "CLAUDE.md"

    def models(self):
        # CLI aliases — resolved to the latest model in each family by the
        # claude binary; haiku excluded per tier policy (never used).
        return ["fable", "opus", "sonnet"]

    def docs_model_default(self) -> str:
        return "sonnet"  # standard coding tier — the docs floor

    def api_base_url(self) -> str:
        return API_BASE_URL

    def api_model_family(self, alias):
        return _API_FAMILIES.get(alias, "")

    def auth_status_argv(self):
        # `claude auth status` prints a JSON object with loggedIn/authMethod/
        # apiProvider (verified live on 2.1.286, 2026-10-01).
        return ["claude", "auth", "status"]

    def headless_argv(self, prompt, *, model, effort="", allowed_tools=None, session_name=""):
        # Prompt must come RIGHT AFTER -p: --allowed-tools is variadic and would
        # swallow a trailing positional as a tool name. Keep --allowed-tools LAST
        # and omit it entirely when empty (an empty value parses as a bogus tool).
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if session_name:
            argv += ["-n", session_name]  # -p sessions persist; resumable by name
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        if allowed_tools:
            argv += ["--allowed-tools", *allowed_tools]
        return argv

    def headless_stream_argv(self, prompt, *, model, effort="", allowed_tools=None):
        # stream-json + --verbose emits one JSON event per line AS IT HAPPENS
        # (verified 2026-07-19: tool_use/tool_result arrive live; plain
        # `--output-format text` prints only at exit). Same flag-ordering
        # constraint as headless_argv: --allowed-tools stays LAST.
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
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

    def session_argv(self, *, session_name, model, seed, notifications=True, effort=""):
        argv = ["claude"]
        if session_name:
            argv += ["-n", session_name]  # resumable later via `claude --resume <name>`
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        argv.append(seed)
        return argv

    def notification_setup(self, notifications: bool):
        # Claude Code 2.1.291: preferredNotifChannel accepts auto and
        # notifications_disabled in local settings (verified with installed CLI).
        channel = "auto" if notifications else "notifications_disabled"
        return {
            ".claude/settings.local.json": json.dumps({"preferredNotifChannel": channel}, indent=2)
            + "\n"
        }

    def title_env(self):
        return {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}

    def install_hint(self):
        return "npm install -g @anthropic-ai/claude-code"

    def plugin_update_argvs(self, marketplace_source: str | None = None):
        # Self-heal the marketplace registration first (best-effort — a re-add
        # of an existing marketplace is benign), then snapshot + update. Claude
        # docs: "restart required to apply" — running sessions keep the old plugin.
        argvs = []
        if marketplace_source:
            argvs.append(["claude", "plugin", "marketplace", "add", marketplace_source])
        argvs += [
            ["claude", "plugin", "marketplace", "update", "oh-my-clanker"],
            ["claude", "plugin", "update", "omc@oh-my-clanker"],
        ]
        return argvs
