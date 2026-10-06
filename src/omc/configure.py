"""`omc configure` — pick your LLM (and worktree naming); print plugin install hints."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from . import docsllm
from .agentsmd import ensure_global_section, seed_project_agents_md
from .config import store
from .config.schema import GlobalConfig, ProjectConfig, ProviderConfig, SecretsConfig
from .errors import ConfigError, OmcError, Refusal
from .plugin import ensure_plugin
from .providers.registry import get_provider, provider_names
from .toolctx import ToolContext
from .wtconfig import repo_root

_PLUGIN_HINTS = """\
omc's in-session skills install as a plugin — once per harness you use:

  Claude Code:  installed for you above (also by `omc design` / `omc update`);
                by hand: /plugin marketplace add chris-husse/oh-my-clanker
                         /plugin install omc@oh-my-clanker
  Codex:        codex plugin marketplace add chris-husse/oh-my-clanker
                then install 'omc' from /plugins

omc's start skill hands off to superpowers — install it too:

  Claude Code:  installed for you above; by hand:
                /plugin install superpowers@claude-plugins-official
  Codex:        install it from https://github.com/obra/superpowers
"""


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _docs_provider(gcfg: GlobalConfig) -> str:
    return gcfg.llm.docs.provider or gcfg.llm.default


def _docs_changed(
    before: GlobalConfig,
    after: GlobalConfig,
    before_keys: dict[str, str],
    after_keys: dict[str, str],
) -> tuple[bool, bool]:
    """(documentation tuple changed, docs provider's key changed) — the probe
    trigger of spec §3.2. The docs provider is evaluated on the POST-apply
    config FOR BOTH SIDES (deliberate, per spec: "changed" compares the same
    provider name's value before and after); a provider entry that did not
    exist before compares as ""."""
    name = _docs_provider(after)

    def model(cfg: GlobalConfig) -> str:
        p = cfg.llm.providers.get(name)
        return p.docs_model if p else ""

    tuple_changed = (before.llm.docs.provider, before.llm.docs.backend, model(before)) != (
        after.llm.docs.provider,
        after.llm.docs.backend,
        model(after),
    )
    return tuple_changed, before_keys.get(name, "") != after_keys.get(name, "")


def _probe_docs(
    ctx: ToolContext,
    gcfg: GlobalConfig,
    scfg: SecretsConfig,
    *,
    before: GlobalConfig,
    before_keys: dict[str, str],
) -> bool:
    """Refuse api-without-key, then run exactly the probes the change calls
    for. Returns True when docs_model was rewritten (api: the resolved full
    id). Raises ProbeFailed/ConfigError — nothing has been written yet."""
    name, backend = _docs_provider(gcfg), gcfg.llm.docs.backend
    key = scfg.api_keys.get(name, "")
    if backend == "api" and not key:
        # Unconditional (spec §3.2): also fires on an unrelated --set when a stored
        # api config lost its key (hand-deleted secrets.yaml). Escape hatch:
        # `--set llm.docs.backend=cli`.
        raise ConfigError(f"llm.providers.{name}.api_key is required for the api backend")
    tuple_changed, key_changed = _docs_changed(before, gcfg, before_keys, scfg.api_keys)
    if key_changed and backend != "api":
        # The key changed while the backend does not use it: prove it anyway.
        docsllm.validate_key_only(ctx, name, key, say=_say)
    if not tuple_changed and not (key_changed and backend == "api"):
        return False
    # The two triggers are independent: a backend/provider/model change always
    # runs the selected backend's probes, even when a key probe just ran.
    # setdefault may add a provider entry (e.g. docs.provider=codex with no codex
    # section) — the same thing `--set llm.providers.codex.model=` does today.
    pcfg = gcfg.llm.providers.setdefault(name, ProviderConfig())
    model = docsllm.validate_selection(
        ctx, docsllm.DocsSelection(name, backend, pcfg.docs_model, key), say=_say
    )
    if model != pcfg.docs_model:
        pcfg.docs_model = model
        return True
    return False


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
        scfg = store.load_secrets(ctx.home)
        before, before_keys = copy.deepcopy(gcfg), dict(scfg.api_keys)
        write_global = defaults
        # --defaults seeds a missing project file but never clobbers an
        # existing one: it is committed team truth, not personal state.
        write_project = bool(
            defaults and root is not None and not store.project_config_path(root).exists()
        )
        write_secrets = False
        for i, pair in enumerate(sets, 1):
            key, sep, value = pair.partition("=")
            if not sep:
                # Never echo the argument: a ':' typo on an api_key pair would
                # print the secret to stderr.
                raise Refusal(f"--set expects KEY=VALUE (argument {i} has no '=')")
            parts = key.split(".")
            if parts[0] == "worktree":
                if root is None:
                    raise Refusal("worktree.* is project config — run inside a git repository")
                store.set_key(pcfg, key, value)
                write_project = True
            elif len(parts) == 4 and parts[:2] == ["llm", "providers"] and parts[3] == "api_key":
                # Routing key, not a ProviderConfig field: secrets.yaml only.
                store.set_api_key(scfg, parts[2], value)
                write_secrets = True
            else:
                store.set_key(gcfg, key, value)
                write_global = True
        # Order (spec §3.2): consistency → refuse api-without-key → probes → write.
        store.validate_llm(gcfg.llm)
        if _probe_docs(ctx, gcfg, scfg, before=before, before_keys=before_keys):
            write_global = True  # the resolved full model id was written into docs_model
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
        # Secrets FIRST: if this write fails, config.yaml must not already say `api`
        # (that state trips the unconditional api-without-key refusal on every run).
        if write_secrets:
            store.save_secrets(ctx.home, scfg)
            print(f"Updated {store.secrets_path(ctx.home)} (mode 0600)")
            for name, k in scfg.api_keys.items():
                if before_keys.get(name) != k:
                    _say(f"✓ {name} API key stored ({docsllm.mask_key(k)})")
        if write_global:
            store.save_global(ctx.home, gcfg)
            label = "Wrote defaults to" if defaults and not sets else "Updated"
            print(f"{label} {store.global_config_path(ctx.home)}")
        if write_project and root is not None:
            store.save_project(root, pcfg)
            print(f"Updated {store.project_config_path(root)}")
        _migrate_legacy(ctx, migrated=write_global, carried=write_project)
        _ensure_instructions(ctx, gcfg, root)
        _ensure_plugins(ctx, gcfg)
        print(_PLUGIN_HINTS)
        return 0

    if not sys.stdin.isatty():
        raise Refusal("interactive configure needs a TTY (use --defaults or --set KEY=VALUE)")
    gcfg = store.load_global(ctx.home) or legacy_global or GlobalConfig()
    scfg = store.load_secrets(ctx.home)
    before_keys = dict(scfg.api_keys)
    _walkthrough_global(ctx, gcfg, scfg)  # probes inside; raises before any write
    pcfg = None
    if root is not None:
        pcfg = store.load_project(root) or legacy_project or ProjectConfig()
        _walkthrough_project(pcfg)
    # Secrets FIRST: if this write fails, config.yaml must not already say `api`
    # (that state trips the unconditional api-without-key refusal on every run).
    if scfg.api_keys != before_keys:
        store.save_secrets(ctx.home, scfg)
        print(f"Saved {store.secrets_path(ctx.home)} (mode 0600)")
    store.save_global(ctx.home, gcfg)
    print(f"Saved {store.global_config_path(ctx.home)}")
    if root is not None and pcfg is not None:
        store.save_project(root, pcfg)
        print(f"Saved {store.project_config_path(root)}")
    else:
        print("(not inside a git repository — worktree.* settings are configured per-repo)")
    _migrate_legacy(ctx, migrated=True, carried=root is not None)
    _ensure_instructions(ctx, gcfg, root)
    _ensure_plugins(ctx, gcfg)
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


def _ensure_plugins(ctx: ToolContext, cfg: GlobalConfig) -> None:
    """Install (or repair) the omc plugin for every configured provider that
    has a scriptable path. Configure has already saved the config, so a plugin
    failure is reported — with the manual commands — but never fails the run."""
    for name in cfg.llm.providers:
        try:
            status = ensure_plugin(ctx, name)
        except OmcError as exc:
            print(f"✗ {name}: {exc}", file=sys.stderr)
            continue
        mark = "·" if status.startswith("unverified") else "✓"
        print(f"{mark} {name}: omc plugin {status}", file=sys.stderr)


def _ensure_instructions(ctx: ToolContext, cfg: GlobalConfig, root: Path | None) -> None:
    """Refresh configured harnesses and the effective default, then seed repo guidance."""
    for name in dict.fromkeys((*cfg.llm.providers, cfg.llm.default)):
        try:
            ensure_global_section(ctx, name)
        except (OmcError, OSError) as exc:
            _say(f"✗ {name}: {exc}")
    if root is not None:
        try:
            seed_project_agents_md(root)
        except OSError as exc:
            _say(f"✗ project instructions: {exc}")


def _walkthrough_global(
    ctx: ToolContext, cfg: GlobalConfig, scfg: SecretsConfig
) -> None:  # pragma: no cover - PTY-driven, E2E territory
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
        enabled = questionary.confirm(
            f"Enable native notifications for {name}?", default=pcfg.notifications
        ).ask()
        if enabled is not None:
            pcfg.notifications = enabled

    if len(selected) == 1:
        cfg.llm.default = selected[0]
    else:
        cfg.llm.default = (
            questionary.select(
                "Default provider for `omc design`",
                choices=selected,
                default=cfg.llm.default if cfg.llm.default in selected else selected[0],
            ).ask()
            or selected[0]
        )

    # Documentation generation (spec 2026-10-01 §3.3). Nothing below is saved
    # unless BOTH probes pass; Ctrl-C/Esc (None) aborts with nothing written.
    def aborted() -> Refusal:
        return Refusal("configure aborted — nothing was saved")

    docs_provider = cfg.llm.docs.provider if cfg.llm.docs.provider in selected else ""
    if len(selected) > 1:
        picked = questionary.select(
            "Documentation provider",
            choices=selected,
            default=docs_provider or cfg.llm.default,
        ).ask()
        if picked is None:
            raise aborted()
        docs_provider = "" if picked == cfg.llm.default else picked
    name = docs_provider or cfg.llm.default
    provider = get_provider(name)
    backend_choices = [Choice(f"CLI (uses your {name} login)", "cli")]
    if provider.api_base_url():
        backend_choices.append(Choice("API key", "api"))
    current = next(
        (c for c in backend_choices if c.value == cfg.llm.docs.backend), backend_choices[0]
    )
    backend = questionary.select(
        "Documentation backend", choices=backend_choices, default=current
    ).ask()
    if backend is None:
        raise aborted()
    pcfg = cfg.llm.providers[name]
    other = "Other (type a model id)…"
    if backend == "api":
        key = scfg.api_keys.get(name, "")
        while True:
            hint = f" (blank keeps {docsllm.mask_key(key)})" if key else ""
            typed = questionary.password(f"{name} API key{hint}").ask()
            if typed is None or (not typed and not key):
                raise aborted()
            candidate = typed or key
            try:
                store.validate_api_key(candidate, f"llm.providers.{name}.api_key")
            except ConfigError as exc:
                print(exc)
                continue
            _say(f"→ checking the key against {provider.api_base_url().split('/')[2]}")
            ok, detail, models = docsllm.api_connection_probe(ctx, name, candidate)
            _say(("✓ " if ok else "✗ ") + detail)
            if ok:
                key = candidate
                break
            key = ""  # a failed probe never "keeps" the old key: retype or abort
        choices = docsllm.model_choices(models)
        try:
            default = (
                pcfg.docs_model
                if pcfg.docs_model in choices
                else docsllm.resolve_model(
                    name, pcfg.docs_model or provider.docs_model_default(), models
                )
            )
        except docsllm.ProbeFailed:
            # A stored alias with no family member for this key, or an unsafe stored
            # id: show the picker with no preselection instead of aborting configure.
            default = None
        if default not in choices:
            # resolve_model returns its input unchanged for non-family ids (retired,
            # non-claude-, or typed via "Other"); questionary raises on such a default.
            default = None
        picked = questionary.select(
            "Documentation model", choices=[*choices, other], default=default
        ).ask()
        if picked is None:
            raise aborted()
        model = questionary.text("model id").ask() if picked == other else picked
        if not model:
            raise aborted()
        _say(f"→ validating {model} via api")
        ok, detail = docsllm.api_model_probe(ctx, name, key, model)
        _say(("✓ " if ok else "✗ ") + detail)
        if not ok:
            raise docsllm.ProbeFailed(detail)
        scfg.api_keys[name] = key
    else:
        known = provider.models()
        if known:
            default = pcfg.docs_model if pcfg.docs_model in known else provider.docs_model_default()
            if default not in known:  # questionary raises on a default outside choices
                default = known[0]
            picked = questionary.select(
                "Documentation model", choices=[*known, other], default=default
            ).ask()
            if picked is None:
                raise aborted()
            model = questionary.text("model id").ask() if picked == other else picked
        else:
            model = questionary.text(
                "Documentation model (blank = CLI default)", default=pcfg.docs_model
            ).ask()
        if model is None:
            raise aborted()
        _say(f"→ checking {name} login")
        ok, detail = docsllm.cli_connection_probe(ctx, name)
        _say(("✓ " if ok else "✗ ") + detail)
        if not ok:
            raise docsllm.ProbeFailed(detail)
        _say(f"→ validating {model or 'default model'} via cli")
        ok, detail = docsllm.cli_model_probe(ctx, name, model)
        _say(("✓ " if ok else "✗ ") + detail)
        if not ok:
            raise docsllm.ProbeFailed(detail)
    pcfg.docs_model = model
    cfg.llm.docs.provider = docs_provider
    cfg.llm.docs.backend = backend


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
