"""Slug resolution: inline the packaged slug skill into a headless provider call,
parse the OMC_SLUG verdict, sanitize. All tracker intelligence lives in the skill."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config.schema import Config
from .errors import OmcError, Refusal
from .providers.registry import get_provider
from .skills_source import skill_prompt
from .toolctx import ToolContext

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 50
_VERDICT_PREFIX = "OMC_SLUG "

# Server-scoped MCP grants for the headless call (claude only; other providers
# ignore allowed_tools). Verified live 2026-07-17 (spec §10.2 contingency fired):
# claude honors server-scoped grants — `mcp__<server>` covers all of that
# server's tools — but NOT a global `mcp__*` glob. Scoping to conventional
# tracker server names is also deliberately narrower: ticket text is untrusted
# input, and this keeps non-tracker tools (mail, chat, ...) out of the call's
# reach. A tracker under a nonconventional server name surfaces as the skill's
# `mcp-unauthenticated` diagnostic; a config knob for extra names is a tracked
# hardening/UX item.
MCP_TOOL_PATTERNS = ["mcp__jira", "mcp__atlassian", "mcp__linear", "mcp__github", "mcp__gitlab"]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    slug: str = ""
    reason: str = ""
    message: str = ""


def sanitize_slug(s: str) -> str:
    out = _NON_SLUG_RE.sub("-", s.replace("\n", " ").lower()).strip("-")
    return out[:_SLUG_MAX].rstrip("-")


def parse_verdict(text: str) -> Verdict | None:
    """Last parseable OMC_SLUG line wins; None when none parses.

    Tolerates markdown wrapping: models routinely emit the verdict as
    `` `OMC_SLUG {...}` `` despite instructions (observed live) — surrounding
    backticks are stripped before matching; the JSON itself never contains one.
    """
    verdict = None
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if not line.startswith(_VERDICT_PREFIX):
            continue
        try:
            data = json.loads(line[len(_VERDICT_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("ok"), bool):
            verdict = Verdict(
                ok=data["ok"],
                slug=str(data.get("slug", "")),
                reason=str(data.get("reason", "")),
                message=str(data.get("message", "")),
            )
    return verdict


def build_prompt(context: str) -> str:
    return skill_prompt("slug").replace("$ARGUMENTS", context)


def fetch_slug(ctx: ToolContext, cfg: Config, context: str) -> str:
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    argv = provider.headless_argv(
        build_prompt(context), model=model, allowed_tools=MCP_TOOL_PATTERNS
    )
    try:
        cp = ctx.run(argv, extra_env=provider.title_env())
    except OSError as exc:
        raise OmcError(f"could not launch {name}: {exc}") from exc
    output = (cp.stdout or "") + "\n" + (cp.stderr or "")
    verdict = parse_verdict(output)
    if verdict is None:
        raise OmcError(
            f"no OMC_SLUG verdict in {name} output (rc {cp.returncode}):\n{output.strip()}"
        )
    if not verdict.ok:
        raise Refusal(f"could not generate slug [{verdict.reason}]: {verdict.message}")
    slug = sanitize_slug(verdict.slug)
    if not slug:
        raise OmcError(f"provider returned an empty slug (raw: {verdict.slug!r})")
    return slug
