"""`omc configure` — pick your LLM (and worktree naming); print plugin install hints."""

from __future__ import annotations

import sys

from .config import store
from .config.schema import Config, ProviderConfig
from .errors import Refusal
from .providers.registry import get_provider, provider_names
from .toolctx import ToolContext

_PLUGIN_HINTS = """\
omc's in-session skills install as a plugin — once per harness you use:

  Claude Code:  /plugin marketplace add chris-husse/oh-my-clanker
                /plugin install omc@oh-my-clanker
  Codex:        codex plugin marketplace add chris-husse/oh-my-clanker
                then install 'omc' from /plugins
  OpenCode:     add to opencode.json:
                "plugin": ["omc@git+https://github.com/chris-husse/oh-my-clanker.git"]

omc's start skill hands off to superpowers — install it too:

  Claude Code:  /plugin marketplace add obra/superpowers-marketplace
                /plugin install superpowers@superpowers-marketplace
  Codex/OpenCode: install it from https://github.com/obra/superpowers
"""


def run_configure(ctx: ToolContext, *, defaults: bool, sets: list[str]) -> int:
    if defaults or sets:
        # --defaults establishes the starting point (fresh Config() rather than
        # whatever's on disk); --set pairs, if any, are then applied on top of
        # it. Passing both together must not silently drop --set.
        cfg = Config() if defaults else (store.load(ctx.home) or Config())
        for pair in sets:
            key, sep, value = pair.partition("=")
            if not sep:
                raise Refusal(f"--set expects KEY=VALUE, got {pair!r}")
            store.set_key(cfg, key, value)
        store.save(ctx.home, cfg)
        label = "Wrote defaults to" if defaults and not sets else "Updated"
        print(f"{label} {store.config_path(ctx.home)}")
        print(_PLUGIN_HINTS)
        return 0
    if not sys.stdin.isatty():
        raise Refusal("interactive configure needs a TTY (use --defaults or --set KEY=VALUE)")
    cfg = store.load(ctx.home) or Config()
    _walkthrough(cfg)
    store.save(ctx.home, cfg)
    print(f"Saved {store.config_path(ctx.home)}")
    print(_PLUGIN_HINTS)
    return 0


def _walkthrough(cfg: Config) -> None:  # pragma: no cover - PTY-driven, E2E territory
    import questionary
    from questionary import Choice

    names = provider_names()
    selected = questionary.checkbox(
        "Which LLMs do you use?",
        choices=[Choice(n, checked=(n in cfg.llm.providers)) for n in names],
    ).ask()
    if not selected:
        selected = list(cfg.llm.providers) or ["claude"]
    cfg.llm.providers = {n: cfg.llm.providers.get(n, ProviderConfig()) for n in selected}

    for name in selected:
        pcfg = cfg.llm.providers[name]
        known = get_provider(name).models()
        if known:
            other = "Other (type a model id)…"
            default = pcfg.model if pcfg.model in known else known[0]
            picked = questionary.select(
                f"{name} model", choices=[*known, other], default=default
            ).ask()
            model = (
                questionary.text(f"{name} model id", default=pcfg.model).ask()
                if picked == other
                else picked
            )
        else:
            model = questionary.text(
                f"{name} model (blank = provider default)", default=pcfg.model
            ).ask()
        pcfg.model = model or ""

    if len(selected) == 1:
        cfg.llm.default = selected[0]
    else:
        cfg.llm.default = (
            questionary.select(
                "Default provider for `omc start`",
                choices=selected,
                default=cfg.llm.default if cfg.llm.default in selected else selected[0],
            ).ask()
            or selected[0]
        )

    cfg.worktree.branch_prefix = (
        questionary.text("Branch prefix", default=cfg.worktree.branch_prefix).ask()
        or cfg.worktree.branch_prefix
    )
    cfg.worktree.base_branch = (
        questionary.text("Base branch", default=cfg.worktree.base_branch).ask()
        or cfg.worktree.base_branch
    )
