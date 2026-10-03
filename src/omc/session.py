"""The pure half of launching a provider session: argv, env, titles, shell
invocation. Both `omc design` and `omc implement` build this plan, print it
under --dry-run, and exec (or run headless) from it. No I/O here, except
`run_headless`, which runs the provider through `ctx.run`."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import notify
from .config.schema import Config
from .providers.registry import get_provider
from .shells.registry import detect_shell
from .slug import MCP_TOOL_PATTERNS
from .terminal_title import terminal_title_argv
from .terminals import detect_terminal
from .toolctx import ToolContext

START_ALLOWED_TOOLS = [*MCP_TOOL_PATTERNS, "Bash", "Read", "Glob", "Grep"]


@dataclass(frozen=True)
class SessionPlan:
    session_argv: list[str]
    env: dict[str, str]  # provider title suppression + OMC_SLUG
    title_seq: str
    title_argv: list[str]
    shell_argv: list[str]


def session_plan(
    ctx: ToolContext,
    cfg: Config,
    *,
    seed: str,
    slug: str,
    session_name: str,
    title: str,
    cwd: str,
) -> SessionPlan:
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    sink = notify.sink_argv(name) if cfg.notifications.enabled else None
    session_argv = provider.session_argv(
        session_name=session_name, model=model, seed=seed, notify_sink_argv=sink
    )
    title_seq = detect_terminal(ctx.env).title_sequence(title)
    title_argv = terminal_title_argv()
    shell_argv, _ = detect_shell(ctx.env).build_invocation(
        cwd=cwd,
        title=title,
        startup_argv=session_argv,
        title_seq=title_seq,
        title_argv=title_argv,
    )
    return SessionPlan(
        session_argv=session_argv,
        env={**provider.title_env(), "OMC_SLUG": slug},
        title_seq=title_seq,
        title_argv=title_argv,
        shell_argv=shell_argv,
    )


def run_headless(
    ctx: ToolContext,
    cfg: Config,
    seed: str,
    cwd: str,
    slug: str,
    *,
    session_name: str | None = None,
    allowed_tools: list[str] | None = None,
) -> int:
    """Print-mode run of a seeded session. The five positional parameters are a
    test contract (omc.start._run_headless is monkeypatched by shape); the
    keyword-only ones default to design's values so `omc implement` can name
    its session and widen the investigation allow-list without a second
    function."""
    name = cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = pcfg.model if pcfg else ""
    argv = provider.headless_argv(
        seed,
        model=model,
        session_name=session_name or slug,
        allowed_tools=START_ALLOWED_TOOLS if allowed_tools is None else allowed_tools,
    )
    try:
        cp = ctx.run(argv, cwd=cwd, extra_env={**provider.title_env(), "OMC_SLUG": slug})
    except OSError as exc:
        print(f"error: headless session failed to launch: {exc}", file=sys.stderr)
        return 1
    if cp.stdout:
        print(cp.stdout, end="" if cp.stdout.endswith("\n") else "\n")
    if cp.returncode != 0 and cp.stderr:
        print(cp.stderr, file=sys.stderr, end="")
    return cp.returncode
