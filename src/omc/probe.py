"""Parallel `--version` probes for omc start's boot check. Real runs, never
file-exists checks; no auto-install of anything."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .config.schema import Config
from .errors import OmcError
from .providers.registry import get_provider
from .toolctx import ToolContext, tool_version

_HINTS = {
    "git": "install git: https://git-scm.com/downloads",
    "wt": "install worktrunk: cargo install worktrunk (https://github.com/worktrunk)",
}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    present: bool
    detail: str
    hint: str


def run_probes(ctx: ToolContext, specs: list[tuple[str, list[str], str]]) -> list[ProbeResult]:
    def probe(spec: tuple[str, list[str], str]) -> ProbeResult:
        name, argv, hint = spec
        present, detail = tool_version(ctx, argv)
        return ProbeResult(name, present, detail, hint)

    with ThreadPoolExecutor(max_workers=max(len(specs), 1)) as pool:
        return list(pool.map(probe, specs))


def require_tools(ctx: ToolContext, cfg: Config) -> None:
    """Probe git, wt, and the configured provider CLI in parallel; raise on any miss."""
    provider = get_provider(cfg.llm.default)
    specs = [
        ("git", [ctx.git_bin, "--version"], _HINTS["git"]),
        ("wt", [ctx.wt_bin, "--version"], _HINTS["wt"]),
        (provider.name, [provider.name, "--version"], provider.install_hint()),
    ]
    misses = [r for r in run_probes(ctx, specs) if not r.present]
    if misses:
        lines = [f"  {r.name}: {r.detail}\n    fix: {r.hint}" for r in misses]
        raise OmcError("missing tools:\n" + "\n".join(lines))
