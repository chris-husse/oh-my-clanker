from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path

import yaml

from ..errors import ConfigError
from ..providers.registry import get_provider, provider_names
from .schema import (
    Config,
    GlobalConfig,
    LLMConfig,
    ProjectConfig,
    ProviderConfig,
    TaskModelsConfig,
    WorktreeConfig,
)


def global_config_path(home: Path) -> Path:
    return home / "config.yaml"


def project_config_path(root: Path) -> Path:
    return root / ".omc" / "config.yaml"


def legacy_config_path(home: Path) -> Path:
    return home / "config.json"


def _load_yaml(path: Path, cls: type):
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected a mapping")
    return _hydrate(cls, data, str(path))


def _save_yaml(path: Path, cfg) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(asdict(cfg), sort_keys=False))


def load_global(home: Path) -> GlobalConfig | None:
    return _load_yaml(global_config_path(home), GlobalConfig)


def save_global(home: Path, cfg: GlobalConfig) -> None:
    validate_llm(cfg.llm)
    _save_yaml(global_config_path(home), cfg)


def load_project(root: Path) -> ProjectConfig | None:
    return _load_yaml(project_config_path(root), ProjectConfig)


def save_project(root: Path, cfg: ProjectConfig) -> None:
    _save_yaml(project_config_path(root), cfg)


def load_legacy(home: Path) -> tuple[GlobalConfig, ProjectConfig] | None:
    """The old combined ~/.omc/config.json. Read ONLY by `omc configure`,
    which migrates it into the split YAML files and deletes it."""
    path = legacy_config_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected an object")
    combined = _hydrate(Config, data, str(path))
    return (
        GlobalConfig(llm=combined.llm),
        ProjectConfig(worktree=combined.worktree),
    )


def _validate_provider(name: object, location: str) -> None:
    names = provider_names()
    if not isinstance(name, str) or name not in names:
        raise ConfigError(
            f"unsupported provider {name!r} in {location}; choose {' or '.join(names)}"
        )


def validate_llm(cfg: LLMConfig) -> None:
    """Provider names and model leaves. Runs on LOAD (_hydrate) and on
    SET-then-SAVE (save_global), so a bad leaf never reaches disk."""
    _validate_provider(cfg.default, "llm.default")
    for name in cfg.providers:
        _validate_provider(name, "llm.providers")
        pcfg = cfg.providers[name]
        try:
            validate_task_model(name, pcfg.model)
        except ConfigError as exc:
            raise ConfigError(f"llm.providers.{name}.model: {exc}") from exc
        for task in fields(TaskModelsConfig):
            try:
                validate_task_model(name, getattr(pcfg.tasks, task.name))
            except ConfigError as exc:
                raise ConfigError(f"llm.providers.{name}.tasks.{task.name}: {exc}") from exc


def validate_worktree_value(name: str, value: object) -> str:
    """WorktreeConfig values flow from a repo-committed file straight into `git`
    argv, so they are an option-injection surface (a committed
    `base_branch: "--upload-pack=/x"` would be parsed by git as an option).
    Reject anything that isn't a clean single token. Shared by load and set
    paths so both entry points enforce it."""
    if not isinstance(value, str):
        raise ConfigError(f"invalid worktree.{name} {value!r}: expected a string")
    if value == "":
        # empty branch_prefix means "no prefix"; an empty base_branch is meaningless
        if name == "base_branch":
            raise ConfigError("invalid worktree.base_branch: must not be empty")
        return value
    if value.startswith("-"):
        raise ConfigError(
            f"invalid worktree.{name} {value!r}: must not start with '-' (looks like a git option)"
        )
    if any(c.isspace() or not c.isprintable() for c in value):
        raise ConfigError(
            f"invalid worktree.{name} {value!r}: must not contain whitespace or control characters"
        )
    return value


# Claude's long-context ids carry a bracketed suffix (`claude-sonnet-4-5[1m]`).
_MODEL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9]+\])?\Z")
_BANNED_FAMILIES = {"claude": {"haiku"}, "codex": {"luna"}}


def validate_task_model(provider: str, value: object) -> str:
    """Static family/effort validation; no provider I/O or live model lookup."""
    _validate_provider(provider, "llm.providers")
    if not isinstance(value, str):
        raise ConfigError(f"invalid {provider} task model {value!r}: expected a string")
    if value == "":
        return value
    model, separator, effort = value.partition(":")
    adapter = get_provider(provider)
    if not _MODEL_TOKEN.fullmatch(model) or (separator and not effort):
        raise ConfigError(f"invalid {provider} task model {value!r}: malformed model token")
    if separator and effort not in adapter.effort_levels():
        raise ConfigError(
            f"invalid {provider} task model {value!r}: effort must be one of "
            f"{', '.join(adapter.effort_levels())}"
        )
    if any(part.lower() in _BANNED_FAMILIES[provider] for part in re.split(r"[-_.]", model)):
        raise ConfigError(
            f"invalid {provider} task model {value!r}: cheap/fast family is not allowed"
        )
    # Codex also has compact full ids such as o3; do not mistake them for
    # unknown bare family aliases.
    compact_codex_id = provider == "codex" and re.fullmatch(r"o[0-9]+", model)
    if model not in adapter.families() and "-" not in model and not compact_codex_id:
        raise ConfigError(
            f"invalid {provider} task model {value!r}: choose "
            f"{', '.join(adapter.families())} or a full model id"
        )
    return value


def remove_provider(cfg: GlobalConfig, name: str) -> None:
    """Remove a configured provider; defaults may still name it."""
    _validate_provider(name, "llm.providers")
    cfg.llm.providers.pop(name, None)


def set_key(cfg: object, dotted: str, value: str) -> None:
    """Set a dotted leaf key. `llm.providers.<name>.model` creates the provider entry."""
    head, _, tail = dotted.partition(".")
    if isinstance(cfg, LLMConfig) and head == "providers":
        name, _, leaf = tail.partition(".")
        attr, separator, nested = leaf.partition(".")
        provider_fields = {f.name: f for f in fields(ProviderConfig)}
        if attr not in provider_fields or (separator and attr != "tasks"):
            raise ConfigError(f"unknown config key: providers.{tail}")
        _validate_provider(name, "llm.providers")
        if attr == "tasks":
            task_fields = {f.name for f in fields(TaskModelsConfig)}
            if not nested:
                raise ConfigError(f"llm.providers.{name}.tasks is a section, not a settable key")
            if nested not in task_fields:
                raise ConfigError(f"unknown config key: providers.{tail}")
            checked = validate_task_model(name, value)
            setattr(cfg.providers.setdefault(name, ProviderConfig()).tasks, nested, checked)
            return
        if attr == "notifications":
            if value not in ("true", "false"):
                raise ConfigError(f"llm.providers.{name}.notifications expects true or false")
            value = value == "true"
        elif not isinstance(value, str):
            raise ConfigError(f"llm.providers.{name}.{attr} expects a string")
        if attr == "model":
            validate_task_model(name, value)
        setattr(cfg.providers.setdefault(name, ProviderConfig()), attr, value)
        return
    if isinstance(cfg, LLMConfig) and head == "provider_selection_per_step":
        if tail:
            raise ConfigError(f"unknown config key: provider_selection_per_step.{tail}")
        if value not in ("true", "false"):
            raise ConfigError("llm.provider_selection_per_step expects true or false")
        cfg.provider_selection_per_step = value == "true"
        return
    if isinstance(cfg, WorktreeConfig):
        # values reach git argv — validate (option-injection surface) on the set
        # path too, exactly as the load path does via _hydrate.
        if tail:
            raise ConfigError(f"unknown config key: worktree.{head}.{tail}")
        if head not in ("branch_prefix", "base_branch"):
            raise ConfigError(f"unknown config key: worktree.{head}")
        setattr(cfg, head, validate_worktree_value(head, value))
        return
    field_names = {f.name for f in fields(cfg)}  # type: ignore[arg-type]
    if head not in field_names:
        raise ConfigError(f"unknown config key: {head}")
    current = getattr(cfg, head)
    if tail:
        if not is_dataclass(current):
            raise ConfigError(f"unknown config key: {dotted}")
        set_key(current, tail, value)
        return
    if is_dataclass(current):
        raise ConfigError(f"{dotted} is a section, not a settable key")
    if head == "schema_version":
        raise ConfigError("schema_version is not settable")
    if isinstance(cfg, LLMConfig) and head == "default":
        _validate_provider(value, "llm.default")
    setattr(cfg, head, value)


# Spec 2026-11-16 §5.1: saves since 2026-10-27 carry it; no general mechanism until a 2nd.
_RETIRED_GLOBAL_KEY = "gitnexus"


def _hydrate(cls: type, data: dict, path: str, unknown: list[str] | None = None, prefix: str = ""):
    outermost = unknown is None
    if unknown is None:
        unknown = []
    field_map = {f.name: f for f in fields(cls)}
    kwargs = {}
    for name, value in data.items():
        if outermost and cls is GlobalConfig and name == _RETIRED_GLOBAL_KEY:
            continue
        if name not in field_map:
            unknown.append(f"{prefix}{name}")
            continue
        f = field_map[name]
        if cls is LLMConfig and name == "providers":
            if not isinstance(value, dict):
                raise ConfigError(f"invalid value for {name!r} in {path}: expected an object")
            providers = {}
            for k, v in value.items():
                _validate_provider(k, "llm.providers")
                if not isinstance(v, dict):
                    raise ConfigError(
                        f"invalid value for llm.providers[{k!r}] in {path}: expected an object"
                    )
                providers[k] = _hydrate(ProviderConfig, v, path, unknown, f"{prefix}providers.{k}.")
            kwargs[name] = providers
        elif is_dataclass(f.type):
            if not isinstance(value, dict):
                raise ConfigError(f"invalid value for {name!r} in {path}: expected an object")
            kwargs[name] = _hydrate(f.type, value, path, unknown, f"{prefix}{name}.")
        else:
            kwargs[name] = value
    obj = cls(**kwargs)
    if cls is LLMConfig:
        if not isinstance(obj.provider_selection_per_step, bool):
            raise ConfigError(
                f"invalid llm.provider_selection_per_step in {path}: expected true/false"
            )
        validate_llm(obj)
    if cls is ProviderConfig:
        provider_path = prefix.rstrip(".")
        for fname in (f.name for f in fields(ProviderConfig) if f.type is str):
            if not isinstance(getattr(obj, fname), str):
                raise ConfigError(f"invalid {provider_path}.{fname} in {path}: expected a string")
        if not isinstance(obj.notifications, bool):
            raise ConfigError(
                f"invalid {provider_path}.notifications in {path}: expected true/false"
            )
    if cls is TaskModelsConfig:
        for f in fields(TaskModelsConfig):
            if not isinstance(getattr(obj, f.name), str):
                raise ConfigError(f"invalid {prefix}{f.name} in {path}: expected a string")
    if cls is WorktreeConfig:
        for fname in ("branch_prefix", "base_branch"):
            try:
                validate_worktree_value(fname, getattr(obj, fname))
            except ConfigError as exc:
                raise ConfigError(f"{exc} in {path}") from exc
    if outermost and unknown:
        print(
            f"· config: ignoring unknown key(s) {sorted(unknown)} in {path}",
            file=sys.stderr,
        )
    return obj
