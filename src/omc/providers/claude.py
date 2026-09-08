from __future__ import annotations

import json
import shlex

from .base import PluginEntry, PluginFacts, Provider, RepairStep

PLUGIN_REF = "omc@oh-my-clanker"
MARKETPLACE_NAME = "oh-my-clanker"
SUPERPOWERS_REF = "superpowers@claude-plugins-official"
OFFICIAL_MARKETPLACE = "anthropics/claude-plugins-official"


def _entry(entries: list[dict], name: str) -> PluginEntry | None:
    """The installed plugin named ``name`` from ANY marketplace (ids are
    ``name@marketplace``). Cross-marketplace matching is deliberate: a
    manifest pinned to one marketplace made Claude refuse to load omc when
    superpowers came from another — see docker/PLUGIN-NOTES.md "Resolution 2"."""
    raw = next((e for e in entries if str(e.get("id", "")).startswith(name + "@")), None)
    if raw is None:
        return None
    problems = [str(e) for e in (raw.get("errors") or [])]
    # Absent `enabled` counts as enabled: only an explicit False disables.
    enabled = raw.get("enabled") is not False
    if not enabled:
        problems.append("the plugin is disabled")
    return PluginEntry(id=str(raw.get("id", "")), enabled=enabled, problems=tuple(problems))


class ClaudeProvider(Provider):
    name = "claude"

    def models(self):
        # CLI aliases — resolved to the latest model in each family by the
        # claude binary; haiku excluded per tier policy (never used).
        return ["fable", "opus", "sonnet"]

    def docs_model_default(self) -> str:
        return "sonnet"  # standard coding tier — the docs floor

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

    def notifies_natively(self):
        # Claude Code posts its own clickable, session-focusing notification
        # for permission prompts / idle / turn end (observed live 2026-07-23);
        # omc's osascript ping would duplicate it as a dead "omc: <slug>"
        # alert, so the macos backend suppresses itself for claude.
        return True

    def title_env(self):
        return {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}

    def install_hint(self):
        return "npm install -g @anthropic-ai/claude-code"

    def plugin_probe_argvs(self):
        # `claude plugin list --json`: one entry per installed plugin with
        # `id`, `enabled` and — for a plugin Claude refused to load — an
        # `errors` list. The human listing carries the same facts as prose;
        # the JSON is the contract (verified 2026-09-02, claude 2.1.x).
        return [["claude", "plugin", "list", "--json"]]

    def parse_plugin_facts(self, stdouts):
        # ValueError covers both failure modes for the caller: json's own
        # JSONDecodeError is a ValueError subclass.
        data = json.loads(stdouts[0] or "")
        if not isinstance(data, list):
            raise ValueError("expected a JSON array")
        entries = [e for e in data if isinstance(e, dict)]
        # marketplace_source stays None: claude exposes no source probe, and
        # re-adding an existing marketplace is benign.
        return PluginFacts(omc=_entry(entries, "omc"), superpowers=_entry(entries, "superpowers"))

    def plugin_repair_argvs(self, facts, *, source, update):
        omc_fix = f"claude plugin marketplace add {source} && claude plugin install {PLUGIN_REF}"
        steps: list[RepairStep] = []
        if facts.superpowers is None:
            steps += [
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", OFFICIAL_MARKETPLACE],
                    "installing superpowers (omc's start skill hands off to it)…",
                    # Best-effort: the official marketplace is usually pre-registered.
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "install", SUPERPOWERS_REF, "--scope", "user"],
                    "",
                    fatal=True,
                    action="installed superpowers",
                    manual_fix=(
                        f"claude plugin marketplace add {OFFICIAL_MARKETPLACE} && "
                        f"claude plugin install {SUPERPOWERS_REF}"
                    ),
                ),
            ]
        elif facts.superpowers.problems:
            # Present but not serving skills. omc's start skill hands off to
            # superpowers, so reporting "ok" here would promise a handoff that
            # fails mid-session. Repair in place by its OWN id — a superpowers
            # from any marketplace satisfies the check, so re-installing
            # OFFICIAL_MARKETPLACE's copy could orphan the user's.
            steps.append(
                RepairStep(
                    ["claude", "plugin", "enable", facts.superpowers.id],
                    f"superpowers is installed but not serving skills "
                    f"({facts.superpowers.problems[0]}) — re-enabling…",
                    fatal=True,
                    action="enabled superpowers",
                    manual_fix=f"claude plugin enable {facts.superpowers.id}",
                )
            )
        if facts.omc is None:
            steps += [
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", source],
                    f"installing the omc plugin from {source}…",
                    # The marketplace may already be registered from an earlier
                    # attempt — a failed add is fine if the install below succeeds.
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"],
                    "",
                    fatal=True,
                    action="installed",
                    manual_fix=omc_fix,
                ),
            ]
        elif facts.omc.problems:
            steps += [
                # Refresh the marketplace snapshot first so the reinstall picks
                # up a fixed manifest; add/update/uninstall are best-effort.
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", source],
                    f"the omc plugin is installed but failed to load "
                    f"({facts.omc.problems[0]}) — reinstalling from {source}…",
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "marketplace", "update", MARKETPLACE_NAME],
                    "",
                    fatal=False,
                ),
                RepairStep(["claude", "plugin", "uninstall", PLUGIN_REF], "", fatal=False),
                RepairStep(
                    ["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"],
                    "",
                    fatal=True,
                    action="repaired",
                    manual_fix=omc_fix,
                ),
            ]
        elif update:
            # Claude's docs note a restart is required to apply — running
            # sessions keep the old plugin — per Claude's docs, not verified
            # live; whether a RUNNING session picks the update up is an
            # explicitly open question (a non-goal of the codex-registration
            # design, tracked in docker/PLUGIN-NOTES.md).
            steps += [
                RepairStep(["claude", "plugin", "marketplace", "add", source], "", fatal=False),
                RepairStep(
                    ["claude", "plugin", "marketplace", "update", MARKETPLACE_NAME],
                    "",
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "update", PLUGIN_REF],
                    "",
                    fatal=True,
                    action="updated",
                    manual_fix=omc_fix,
                ),
            ]
        return steps
