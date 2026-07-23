"""`omc configure` — pick your LLM (and worktree naming); print plugin install hints."""

from __future__ import annotations

import sys
from pathlib import Path

from .agentsmd import ensure_agents_chain
from .config import store
from .config.schema import GlobalConfig, ProjectConfig, ProviderConfig
from .errors import ConfigError, Refusal
from .providers.registry import get_provider, provider_names
from .toolctx import ToolContext
from .wtconfig import repo_root

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
    root_str = repo_root(ctx)
    root = Path(root_str) if root_str else None
    legacy = store.load_legacy(ctx.home)
    legacy_global, legacy_project = legacy if legacy else (None, None)

    if defaults or sets:
        # --defaults establishes the starting point (fresh GlobalConfig rather
        # than whatever's on disk); --set pairs, if any, are then applied on
        # top of it. Passing both together must not silently drop --set.
        gcfg = (
            GlobalConfig()
            if defaults
            else (store.load_global(ctx.home) or legacy_global or GlobalConfig())
        )
        pcfg = (store.load_project(root) if root else None) or legacy_project or ProjectConfig()
        write_global = defaults
        # --defaults seeds a missing project file but never clobbers an
        # existing one: it is committed team truth, not personal state.
        write_project = bool(
            defaults and root is not None and not store.project_config_path(root).exists()
        )
        for pair in sets:
            key, sep, value = pair.partition("=")
            if not sep:
                raise Refusal(f"--set expects KEY=VALUE, got {pair!r}")
            if key.split(".", 1)[0] == "worktree":
                if root is None:
                    raise Refusal("worktree.* is project config — run inside a git repository")
                store.set_key(pcfg, key, value)
                write_project = True
            else:
                store.set_key(gcfg, key, value)
                write_global = True
        # Migration must not lose the legacy worktree section: when this run
        # writes the global YAML (which deletes the JSON afterwards) and the
        # repo has no project file yet, seed it from the legacy content.
        if (
            write_global
            and legacy is not None
            and root is not None
            and not store.project_config_path(root).exists()
        ):
            write_project = True
        if write_global:
            store.save_global(ctx.home, gcfg)
            label = "Wrote defaults to" if defaults and not sets else "Updated"
            print(f"{label} {store.global_config_path(ctx.home)}")
        if write_project and root is not None:
            store.save_project(root, pcfg)
            print(f"Updated {store.project_config_path(root)}")
        _migrate_legacy(ctx, migrated=write_global, carried=write_project)
        _ensure_repo_chain(ctx)
        print(_PLUGIN_HINTS)
        return 0

    if not sys.stdin.isatty():
        raise Refusal("interactive configure needs a TTY (use --defaults or --set KEY=VALUE)")
    gcfg = store.load_global(ctx.home) or legacy_global or GlobalConfig()
    _walkthrough_global(gcfg)
    pcfg = None
    if root is not None:
        pcfg = store.load_project(root) or legacy_project or ProjectConfig()
        _walkthrough_project(pcfg)
    store.save_global(ctx.home, gcfg)
    print(f"Saved {store.global_config_path(ctx.home)}")
    if root is not None and pcfg is not None:
        store.save_project(root, pcfg)
        print(f"Saved {store.project_config_path(root)}")
    else:
        print("(not inside a git repository — worktree.* settings are configured per-repo)")
    _migrate_legacy(ctx, migrated=True, carried=root is not None)
    _ensure_repo_chain(ctx)
    print(_PLUGIN_HINTS)
    return 0


def _migrate_legacy(ctx: ToolContext, *, migrated: bool, carried: bool) -> None:
    """Delete the legacy combined config.json — but only when this run wrote
    the global YAML (its content now lives there); a pure worktree.* update
    must leave it for a later global write to migrate. `carried` says whether a
    repo's project file was written this run; when it wasn't, the legacy
    worktree.* values land nowhere and the user must be told."""
    path = store.legacy_config_path(ctx.home)
    if migrated and path.exists():
        path.unlink()
        msg = (
            f"Migrated legacy {path} → {store.global_config_path(ctx.home)} "
            "(worktree.* now lives in each repo's .omc/config.yaml)"
        )
        if not carried:
            msg += (
                " — your legacy worktree settings were NOT migrated; "
                "run `omc configure` inside each repo"
            )
        print(msg)


def _ensure_repo_chain(ctx: ToolContext) -> None:
    """In a git repo, verify/create the AGENTS.md control chain; outside one,
    configure is global-only and the chain is skipped."""
    root = repo_root(ctx)
    if root is not None:
        ensure_agents_chain(ctx, root)


def _walkthrough_global(cfg: GlobalConfig) -> None:  # pragma: no cover - PTY-driven, E2E territory
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

    enable = questionary.confirm(
        "Notify when a session needs attention (macOS notification / log file)?",
        default=cfg.notifications.enabled,
    ).ask()
    cfg.notifications.enabled = bool(enable)
    if enable:
        while True:
            backend = (
                questionary.text(
                    "Notification backend: 'macos' or file:///absolute/path.log",
                    default=cfg.notifications.backend,
                ).ask()
                or cfg.notifications.backend
            )
            try:
                cfg.notifications.backend = store.validate_backend(backend)
                break
            except ConfigError as exc:
                print(exc)


def _walkthrough_project(cfg: ProjectConfig) -> None:  # pragma: no cover - PTY-driven E2E territory
    import questionary

    cfg.worktree.branch_prefix = (
        questionary.text("Branch prefix", default=cfg.worktree.branch_prefix).ask()
        or cfg.worktree.branch_prefix
    )
    cfg.worktree.base_branch = (
        questionary.text("Base branch", default=cfg.worktree.base_branch).ask()
        or cfg.worktree.base_branch
    )
