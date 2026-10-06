"""`omc configure` — pick your LLM (and worktree naming); print plugin install hints."""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import docsllm, taskmodels
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
    removed_provider: str | None = None,
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
    # A configured toggle may remove the selected provider. Probe its fallback
    # model without recreating the entry that the user just removed.
    removed_selected = name == removed_provider and name not in gcfg.llm.providers
    pcfg = (
        ProviderConfig()
        if removed_selected
        else gcfg.llm.providers.setdefault(name, ProviderConfig())
    )
    model = docsllm.validate_selection(
        ctx, docsllm.DocsSelection(name, backend, pcfg.docs_model, key), say=_say
    )
    if model != pcfg.docs_model and not removed_selected:
        pcfg.docs_model = model
        return True
    return False


def _probe_task_models(ctx: ToolContext, gcfg: GlobalConfig, *, before: GlobalConfig) -> None:
    """Validate changed model leaves before any file write: one live probe per
    distinct effective value per provider, and one Codex list refresh per run
    (seven leaves pinned to one id cost one turn, not seven)."""
    for name, pcfg in gcfg.llm.providers.items():
        previous = before.llm.providers.get(name, ProviderConfig())
        probed: set[tuple[str, str]] = set()
        refreshed = False
        for task in taskmodels.TASKS:
            value = pcfg.model if task == "orchestrator" else getattr(pcfg.tasks, task)
            old = previous.model if task == "orchestrator" else getattr(previous.tasks, task)
            if value == old:
                continue
            key = taskmodels.probe_key(name, value, task)
            if key in probed:
                _say(f"· {name} {task}: {key[0]} already validated in this run")
                continue
            probed.add(key)
            taskmodels.validate_selection(
                ctx, name, value, task=task, say=_say, refresh=not refreshed
            )
            # Only a Codex *family* leaf refreshes the list; a full id is
            # probed directly and leaves the list untouched.
            if name == "codex" and key[0].partition(":")[0] in get_provider(name).families():
                refreshed = True


def _apply_settings(
    ctx: ToolContext,
    root: Path | None,
    gcfg: GlobalConfig,
    pcfg: ProjectConfig,
    scfg: SecretsConfig,
    sets: list[str],
    *,
    defaults: bool = False,
    legacy: bool = False,
    remove_provider: str | None = None,
) -> tuple[bool, bool, bool]:
    """Validate, probe, and persist candidates; return global/project/secrets write flags.

    Callers that need rollback after a failed edit supply copies. Migration and
    other post-steps remain with the caller.
    """
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
    if remove_provider is not None:
        store.remove_provider(gcfg, remove_provider)
        write_global = True
    # Order (spec §3.2): consistency → refuse api-without-key → probes → write.
    store.validate_llm(gcfg.llm)
    _probe_task_models(ctx, gcfg, before=before)
    if _probe_docs(
        ctx, gcfg, scfg, before=before, before_keys=before_keys, removed_provider=remove_provider
    ):
        write_global = True  # the resolved full model id was written into docs_model
    # Migration must not lose the legacy worktree section: when this run
    # writes the global YAML (which deletes the JSON afterwards) and the
    # repo has no project file yet, seed it from the legacy content.
    if (
        write_global
        and legacy
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
    return write_global, write_project, write_secrets


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
        write_global, write_project, _ = _apply_settings(
            ctx, root, gcfg, pcfg, scfg, sets, defaults=defaults, legacy=legacy is not None
        )
        _migrate_legacy(ctx, migrated=write_global, carried=write_project)
        _ensure_instructions(ctx, gcfg, root)
        _ensure_plugins(ctx, gcfg)
        print(_PLUGIN_HINTS)
        return 0

    if not sys.stdin.isatty():
        raise Refusal("interactive configure needs a TTY (use --defaults or --set KEY=VALUE)")
    gcfg = store.load_global(ctx.home) or legacy_global or GlobalConfig()
    scfg = store.load_secrets(ctx.home)
    pcfg = (store.load_project(root) if root else None) or legacy_project or ProjectConfig()
    write_global, write_project, _ = _run_menu(
        ctx, root, gcfg, pcfg, scfg, legacy=legacy is not None
    )
    _migrate_legacy(ctx, migrated=write_global, carried=write_project)
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


@dataclass
class _MenuSession:
    """Stable menu tree and the write state shared by its field callbacks."""

    root: dict[str, Any] = field(default_factory=dict)
    flags: tuple[bool, bool, bool] = (False, False, False)
    interface: Any = None


def _model_picker(
    ctx: ToolContext,
    provider: str,
    task: str,
    saved: str,
    *,
    warn: Callable[[str], object] | None = None,
) -> dict[str, str]:
    choices = taskmodels.model_options(ctx, provider, task, warn=warn)
    choices["Other (type a model id)"] = "__other_model__"
    if saved and saved not in choices.values():
        # Preserve a previously saved full id or effort omitted by a later
        # provider-list refresh as the currently selected menu entry.
        first, *rest = choices.items()
        choices = dict((first, (saved, saved), *rest))
    return choices


def _compose_menu(
    ctx: ToolContext,
    root: Path | None,
    gcfg: GlobalConfig,
    pcfg: ProjectConfig,
    scfg: SecretsConfig,
    *,
    legacy: bool = False,
) -> _MenuSession:
    """Compose schema leaves into mininterface's nested-dict form."""
    from mininterface.exceptions import Cancelled
    from mininterface.tag import SecretTag, SelectTag, Tag

    class SavedTag(Tag):
        # TextAdaptor calls _on_change_trigger(ui_val), then update(ui_val)
        # with the same original UI value. The first call already validates
        # and persists it, possibly replacing it with a prompted/resolved id.
        # Consume that one renderer replay without performing the edit again.
        def _on_change_trigger(self, ui_val):
            ran_update = self._last_ui_val != ui_val
            self._renderer_edit = True
            try:
                super()._on_change_trigger(ui_val)
            finally:
                self._renderer_edit = False
            # The library can skip its first update when _last_ui_val is stale
            # after another field changed this tag's accepted value. In that
            # case the renderer's next update is the actual edit, not a replay.
            self._renderer_replay = (ui_val, self.val, self._error_text) if ran_update else None

        def update(self, value):
            if not getattr(self, "_renderer_edit", False):
                replay = getattr(self, "_renderer_replay", None)
                self._renderer_replay = None
                if replay is not None and value == replay[0]:
                    _, accepted, error = replay
                    self.val = accepted
                    self._last_ui_val = accepted
                    if error:
                        self.set_error_text(error)
                        return False
                    self.remove_error_text()
                    return True
            return super().update(value)

    class SavedSelectTag(SavedTag, SelectTag):
        # mininterface 1.4's SelectTag._validate checks membership but skips
        # Tag._validate, including its persistence callback. Let the selected
        # option through, then run that callback; it may return a resolved ID.
        def _validate(self, value):
            if value not in self._build_options().values() and value != self.val:
                raise ValueError("Not one of the allowed values")
            return Tag._validate(self, value)

        def update(self, value):
            if value == self.val and value not in self._build_options().values():
                return Tag.update(self, value)
            return super().update(value)

        def _get_selected_key(self):
            return super()._get_selected_key() or (str(self.val) if self.val else None)

    class MaskedKeyTag(SavedTag, SecretTag):
        def _get_masked_val(self):
            return docsllm.mask_key(self.val) if self.val else ""

    session = _MenuSession()
    tags: list[tuple[Any, str, str | None]] = []

    def guarded_commit(key: str, tag: Any, *, provider: str | None = None):
        try:
            return commit(key, tag, provider=provider)
        except (KeyboardInterrupt, Cancelled):
            # Tag._validate temporarily installs the candidate before calling
            # us. mininterface catches the interruption and may later submit
            # this whole level, so restore its last accepted value now.
            tag.val = current(key, provider)
            raise

    def commit(key: str, tag: Any, *, provider: str | None = None):
        value = tag.val
        if value == "" and not isinstance(tag, SelectTag) and ".tasks." not in key:
            return True, current(key, provider)
        if value in ("__other_model__", "__other__"):
            if session.interface is None:
                return "Model input is unavailable"
            try:
                value = session.interface.ask("Model id")
            except Cancelled:
                return True, current(key, provider)
            if not value:
                return True, current(key, provider)
        old = current(key, provider)
        if value == old:
            return True, old
        candidate_g, candidate_p, candidate_s = (
            copy.deepcopy(gcfg),
            copy.deepcopy(pcfg),
            copy.deepcopy(scfg),
        )
        try:
            if key.endswith(".configured"):
                if value:
                    flags = _apply_settings(
                        ctx,
                        root,
                        candidate_g,
                        candidate_p,
                        candidate_s,
                        [f"llm.providers.{provider}.model="],
                        legacy=legacy,
                    )
                else:
                    flags = _apply_settings(
                        ctx,
                        root,
                        candidate_g,
                        candidate_p,
                        candidate_s,
                        [],
                        legacy=legacy,
                        remove_provider=provider,
                    )
            else:
                sets = [f"{key}={str(value).lower() if isinstance(value, bool) else value}"]
                if key.endswith(".api_key") and provider not in gcfg.llm.providers:
                    # A key edit on a disabled provider configures it in the
                    # same transaction. Existing provider key edits stay secrets-only.
                    sets.append(f"llm.providers.{provider}.model=")
                flags = _apply_settings(
                    ctx, root, candidate_g, candidate_p, candidate_s, sets, legacy=legacy
                )
        except OmcError as exc:
            message = str(exc)
            if key == "llm.docs.backend" and "api_key is required" in message:
                message += " — set the provider API key first"
            return message
        gcfg.llm = candidate_g.llm
        pcfg.worktree = candidate_p.worktree
        scfg.api_keys = candidate_s.api_keys
        session.flags = tuple(a or b for a, b in zip(session.flags, flags, strict=True))
        # mininterface revalidates every tag on level submit. A probe can
        # resolve another leaf, and removing a provider changes both model
        # leaves. Keep the stable tree aligned with the accepted config so a
        # later validation cannot replay a stale value as a new edit.
        for saved_tag, saved_key, saved_provider in tags:
            saved_tag.val = current(saved_key, saved_provider)
        return True, current(key, provider)

    def current(key: str, provider: str | None):
        parts = key.split(".")
        if key.endswith(".configured"):
            return provider in gcfg.llm.providers
        if key.endswith(".api_key"):
            return scfg.api_keys.get(provider, "")
        if provider is not None:
            obj: Any = gcfg.llm.providers.get(provider, ProviderConfig())
            for part in parts[3:]:
                obj = getattr(obj, part)
            return obj
        obj: Any = pcfg if parts[0] == "worktree" else gcfg
        for part in parts:
            obj = getattr(obj, part)
        return obj

    def leaf(
        obj: Any,
        name: str,
        key: str,
        *,
        provider: str | None = None,
        options: list[str] | dict[str, str] | None = None,
        secret: bool = False,
    ):
        meta = next(f.metadata for f in fields(obj) if f.name == name)
        value = current(key, provider)
        kwargs = {
            "val": value,
            "label": meta["label"],
            "description": meta.get("help", ""),
            "validation": lambda tag: guarded_commit(key, tag, provider=provider),
        }
        if secret:
            tag = MaskedKeyTag(**kwargs)
        elif options is not None:
            tag = SavedSelectTag(options=options, **kwargs)
        else:
            tag = SavedTag(**kwargs)
        tags.append((tag, key, provider))
        return tag

    configured = list(gcfg.llm.providers)
    llm = {}
    for config_field in fields(gcfg.llm):
        if config_field.name in ("docs", "providers"):
            continue  # nested sections, composed below
        options = configured or [gcfg.llm.default] if config_field.name == "default" else None
        llm[config_field.metadata["label"]] = leaf(
            gcfg.llm, config_field.name, f"llm.{config_field.name}", options=options
        )
    other = "__other_model__"
    warned: set[str] = set()

    def warn_once(msg: str) -> None:
        if msg not in warned:
            warned.add(msg)
            _say(msg)

    for name in provider_names():
        p = gcfg.llm.providers.get(name, ProviderConfig())
        toggle = SavedTag(
            val=name in gcfg.llm.providers,
            label="Configured",
            description="Enable this provider.",
            validation=lambda tag, n=name: guarded_commit(
                f"llm.providers.{n}.configured", tag, provider=n
            ),
        )
        tags.append((toggle, f"llm.providers.{name}.configured", name))
        models = list(get_provider(name).models())
        submenu = {"Configured": toggle}
        for config_field in fields(p):
            attr = config_field.name
            if is_dataclass(getattr(p, attr)):
                section = getattr(p, attr)
                submenu[config_field.metadata["label"]] = {
                    f.metadata["label"]: leaf(
                        section,
                        f.name,
                        f"llm.providers.{name}.{attr}.{f.name}",
                        provider=name,
                        options=(
                            _model_picker(
                                ctx, name, f.name, getattr(section, f.name), warn=warn_once
                            )
                            if attr == "tasks"
                            else None
                        ),
                    )
                    for f in fields(section)
                }
                continue
            choices = None
            if attr == "model":
                choices = _model_picker(ctx, name, "orchestrator", p.model, warn=warn_once)
            elif attr == "docs_model" and models:
                value = getattr(p, attr)
                choices = {m: m for m in models}
                choices["Other (type a model id)"] = other
                if value and value not in choices.values():
                    choices = {value: value, **choices}
            submenu[config_field.metadata["label"]] = leaf(
                p,
                attr,
                f"llm.providers.{name}.{attr}",
                provider=name,
                options=choices,
            )
        if get_provider(name).api_base_url():
            meta = next(f.metadata for f in fields(scfg) if f.name == "api_keys")
            key_tag = MaskedKeyTag(
                val=scfg.api_keys.get(name, ""),
                label=meta["label"],
                description=meta.get("help", ""),
                validation=lambda tag, n=name: guarded_commit(
                    f"llm.providers.{n}.api_key", tag, provider=n
                ),
            )
            submenu[meta["label"]] = key_tag
            tags.append((key_tag, f"llm.providers.{name}.api_key", name))
        llm[name] = submenu
    docs_name = _docs_provider(gcfg)
    docs = {}
    for config_field in fields(gcfg.llm.docs):
        options = None
        if config_field.name == "provider":
            options = {"Follow default provider": "", **{n: n for n in configured}}
        elif config_field.name == "backend":
            options = ["cli", "api"] if get_provider(docs_name).api_base_url() else ["cli"]
        docs[config_field.metadata["label"]] = leaf(
            gcfg.llm.docs, config_field.name, f"llm.docs.{config_field.name}", options=options
        )
    session.root = {}
    for config_field in fields(gcfg):
        if config_field.name == "llm":
            session.root[config_field.metadata["label"]] = llm
            docs_field = next(f for f in fields(gcfg.llm) if f.name == "docs")
            session.root[docs_field.metadata["label"]] = docs
    if root is not None:
        worktree_field = next(f for f in fields(pcfg) if f.name == "worktree")
        session.root[f"{worktree_field.metadata['label']} (project)"] = {
            f.metadata["label"]: leaf(pcfg.worktree, f.name, f"worktree.{f.name}")
            for f in fields(pcfg.worktree)
        }
    return session


def _text_interface(plain_menu: bool):
    """Use mininterface's text renderer with a no-echo secret input adaptor."""
    import getpass

    from mininterface._text_interface import TextInterface
    from mininterface._text_interface.adaptor import TextAdaptor
    from mininterface.exceptions import InterfaceNotAvailable
    from mininterface.interfaces import get_interface
    from mininterface.settings import MininterfaceSettings, TextSettings
    from mininterface.tag.secret_tag import SecretTag

    class MaskedTextAdaptor(TextAdaptor):
        def widgetize(self, tag, only_label=False):
            if isinstance(tag, SecretTag):
                if only_label:
                    return tag._get_masked_val()
                try:
                    return getpass.getpass(f"{tag.label}: ")
                except (EOFError, KeyboardInterrupt):
                    # TextAdaptor catches KeyboardInterrupt at the leaf and
                    # returns to its enclosing menu without losing saved edits.
                    raise KeyboardInterrupt from None
            return super().widgetize(tag, only_label=only_label)

    text_settings = TextSettings(plain_menu=plain_menu)
    interface = get_interface("text", settings=MininterfaceSettings(text=text_settings))
    # get_interface falls back to its noninteractive base implementation when
    # there is no controlling terminal, even with an explicit "text" request.
    if not isinstance(interface, TextInterface):
        raise InterfaceNotAvailable("configure needs a controlling terminal")
    interface._adaptor = MaskedTextAdaptor(interface, text_settings)
    return interface


def _run_menu(
    ctx: ToolContext,
    root: Path | None,
    gcfg: GlobalConfig,
    pcfg: ProjectConfig,
    scfg: SecretsConfig,
    *,
    legacy: bool = False,
    plain_menu: bool = False,
) -> tuple[bool, bool, bool]:
    """Run one immediate-save menu session and return accumulated write flags."""
    import codecs
    import errno
    import os

    from mininterface.exceptions import Cancelled, InterfaceNotAvailable

    class TerminalDisconnected(BaseException):
        """Do not let the dependency treat a lost terminal as menu dismissal."""

    class UnbufferedStdin:
        """Keep mininterface's select/read loop on the same file descriptor."""

        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.decoder = codecs.getincrementaldecoder(wrapped.encoding or "utf-8")()

        def fileno(self):
            return self.wrapped.fileno()

        def isatty(self):
            return self.wrapped.isatty()

        def read(self, size=1):
            if size != 1:
                return self.wrapped.read(size)
            while True:
                try:
                    raw = os.read(self.fileno(), 1)
                except OSError:
                    raise TerminalDisconnected from None
                if not raw:
                    raise TerminalDisconnected
                char = self.decoder.decode(raw)
                if char:
                    return char

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    session = _compose_menu(ctx, root, gcfg, pcfg, scfg, legacy=legacy)
    try:
        session.interface = _text_interface(plain_menu)
        original_stdin = sys.stdin
        try:
            sys.stdin = UnbufferedStdin(original_stdin)
            session.interface.form(session.root)
        finally:
            sys.stdin = original_stdin
    except InterfaceNotAvailable:
        raise Refusal(
            "interactive configure needs a TTY (use --defaults or --set KEY=VALUE)"
        ) from None
    except TerminalDisconnected:
        raise Refusal("interactive configure lost its terminal") from None
    except AssertionError as exc:
        # simple_term_menu 1.6 can mask an EIO from its terminal with an
        # assertion in _clear_menu during cleanup. Match that exact cleanup
        # frame and underlying I/O error, leaving other assertions visible.
        tb = exc.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            if (
                code.co_name == "_clear_menu"
                and Path(code.co_filename).name == "simple_term_menu.py"
                and isinstance(exc.__context__, OSError)
                and exc.__context__.errno == errno.EIO
            ):
                raise Refusal("interactive configure lost its terminal") from None
            tb = tb.tb_next
        raise
    except Cancelled:
        pass
    # First exit must establish usable defaults. Project config is seeded
    # independently, even when a prior edit only touched a personal file.
    if not store.global_config_path(ctx.home).exists():
        flags = _apply_settings(ctx, root, gcfg, pcfg, scfg, [], defaults=True, legacy=legacy)
        session.flags = tuple(a or b for a, b in zip(session.flags, flags, strict=True))
    elif root is not None and not store.project_config_path(root).exists():
        store.save_project(root, pcfg)
        print(f"Updated {store.project_config_path(root)}")
        session.flags = (session.flags[0], True, session.flags[2])
    return session.flags
