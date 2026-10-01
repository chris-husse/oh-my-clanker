from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path

import yaml

from ..errors import ConfigError
from ..providers.registry import get_provider, provider_names
from .schema import (
    Config,
    GlobalConfig,
    LLMConfig,
    NotificationsConfig,
    ProjectConfig,
    ProviderConfig,
    SecretsConfig,
    WorktreeConfig,
)


def global_config_path(home: Path) -> Path:
    return home / "config.yaml"


def project_config_path(root: Path) -> Path:
    return root / ".omc" / "config.yaml"


def legacy_config_path(home: Path) -> Path:
    return home / "config.json"


def secrets_path(home: Path) -> Path:
    return home / "secrets.yaml"


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
    if "secrets" in data:
        # Config is also the legacy hydration shape; a secrets key would hydrate
        # and then be silently dropped by the split below. Refuse instead.
        raise ConfigError(f"unexpected 'secrets' in {path}: API keys live in secrets.yaml")
    combined = _hydrate(Config, data, str(path))
    return (
        GlobalConfig(llm=combined.llm, notifications=combined.notifications),
        ProjectConfig(worktree=combined.worktree),
    )


def validate_backend(value: str) -> str:
    """'macos' or file:// + absolute path; shared by load and set paths."""
    if value == "macos":
        return value
    if value.startswith("file://") and value[len("file://") :].startswith("/"):
        return value
    raise ConfigError(
        f"invalid notifications.backend {value!r}: use 'macos' or 'file:///absolute/path'"
    )


def _validate_provider(name: object, location: str) -> None:
    names = provider_names()
    if not isinstance(name, str) or name not in names:
        raise ConfigError(
            f"unsupported provider {name!r} in {location}; choose {' or '.join(names)}"
        )


DOCS_BACKENDS = ("cli", "api")


def _validate_docs_leaf(name: str, value: object) -> str:
    """Type + closed-set checks for one llm.docs leaf; shared by load and set."""
    if not isinstance(value, str):
        raise ConfigError(f"invalid llm.docs.{name} {value!r}: expected a string")
    if name == "backend" and value not in DOCS_BACKENDS:
        raise ConfigError(f"invalid llm.docs.backend {value!r}: use 'cli' or 'api'")
    if name == "provider" and value:  # blank = follow llm.default
        _validate_provider(value, "llm.docs.provider")
    return value


def validate_llm(cfg: LLMConfig) -> None:
    """Provider names + the docs cross-field check. Runs on LOAD (_hydrate),
    SET-then-SAVE (save_global) — the save path matters because `--set` pairs
    apply in arbitrary order, so `backend=api` then `default=codex` passes every
    per-leaf check and must be refused before it reaches disk."""
    _validate_provider(cfg.default, "llm.default")
    for name in cfg.providers:
        _validate_provider(name, "llm.providers")
    _validate_docs_leaf("provider", cfg.docs.provider)
    _validate_docs_leaf("backend", cfg.docs.backend)
    if cfg.docs.backend == "api":
        name = cfg.docs.provider or cfg.default
        if not get_provider(name).api_base_url():
            raise ConfigError(f"{name}: API documentation backend not supported yet, use cli")


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


def validate_api_key(value: object, location: str) -> str:
    """Key rules shared by load, set and save. The message NEVER contains the
    value: it may be a real key with a typo in it."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"invalid {location}: expected a non-empty string")
    if value.startswith("op://"):
        raise ConfigError(
            f"invalid {location}: paste the key itself — omc does not resolve 1Password references"
        )
    if any(c.isspace() or not c.isprintable() or ord(c) > 126 for c in value):
        raise ConfigError(f"invalid {location}: must be printable ASCII without whitespace")
    return value


def _require_api_backend(name: str) -> None:
    """A key is only ever probed/used through an API backend; a provider without
    one (codex) has nothing to probe, so a stored key would be dead and unchecked."""
    if not get_provider(name).api_base_url():
        raise ConfigError(f"{name}: API documentation backend not supported yet, use cli")


def set_api_key(cfg: SecretsConfig, name: str, value: str) -> None:
    _validate_provider(name, "llm.providers")
    _require_api_backend(name)
    cfg.api_keys[name] = validate_api_key(value, f"llm.providers.{name}.api_key")


def load_secrets(home: Path) -> SecretsConfig:
    """Missing file → empty. Strict otherwise. Plain leaves are hydrated TYPED
    here (unlike _hydrate) because a hand-edited `claude: 0x1F` reads as int."""
    path = secrets_path(home)
    if not path.exists():
        return SecretsConfig()
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        # Deliberately NOT `{exc}`: PyYAML quotes a snippet of the offending line,
        # which for a short key is the whole key. The chained __cause__ still holds
        # it; that is acceptable only because cli.main prints str(exc), never the
        # traceback — keep it that way.
        raise ConfigError(f"invalid YAML in {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected a mapping")
    # Never echo a NAME from this file: a hand-edit can transpose a key into one.
    if set(data) - {"schema_version", "api_keys"}:
        raise ConfigError(
            f"unknown config key in {path}: only schema_version and api_keys are allowed"
        )
    keys = data.get("api_keys", {})
    if not isinstance(keys, dict):
        raise ConfigError(f"invalid value for 'api_keys' in {path}: expected a mapping")
    out: dict[str, str] = {}
    names = provider_names()
    for name, value in keys.items():
        if not isinstance(name, str) or name not in names:
            raise ConfigError(
                f"unsupported provider name in api_keys in {path}; choose {' or '.join(names)}"
            )
        if not get_provider(name).api_base_url():
            # Names the provider (a closed-set, validated name), never the value.
            raise ConfigError(
                f"api_keys in {path}: {name}: API documentation backend not supported yet, use cli"
            )
        out[name] = validate_api_key(value, f"api_keys.{name} in {path}")
    return SecretsConfig(schema_version=data.get("schema_version", 1), api_keys=out)


def save_secrets(home: Path, cfg: SecretsConfig) -> None:
    """Atomic 0600 write (the awscreds._store pattern): mkstemp in the
    destination dir, explicit fchmod, write, os.replace. The inode swap is why a
    pre-existing 0644 file ends 0600. The PARENT is not chmodded — <home> hosts
    the managed GitNexus clone and manifests."""
    for name, value in cfg.api_keys.items():
        _validate_provider(name, "api_keys")
        validate_api_key(value, f"api_keys.{name}")
    path = secrets_path(home)
    home.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=home, prefix="secrets.yaml.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(yaml.safe_dump(asdict(cfg), sort_keys=False))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def set_key(cfg: object, dotted: str, value: str) -> None:
    """Set a dotted leaf key. `llm.providers.<name>.model` creates the provider entry."""
    head, _, tail = dotted.partition(".")
    if isinstance(cfg, LLMConfig) and head == "providers":
        name, _, leaf = tail.partition(".")
        if leaf not in ("model", "docs_model"):
            raise ConfigError(f"unknown config key: providers.{tail}")
        _validate_provider(name, "llm.providers")
        setattr(cfg.providers.setdefault(name, ProviderConfig()), leaf, value)
        return
    if isinstance(cfg, LLMConfig) and head == "docs":
        if not tail:
            # The generic "is a section" message below would lose the "llm."
            # prefix (set_key recurses with the tail), so say it here in full.
            raise ConfigError("llm.docs is a section, not a settable key")
        leaf, _, rest = tail.partition(".")
        if rest or leaf not in ("provider", "backend"):
            raise ConfigError(f"unknown config key: docs.{tail}")
        setattr(cfg.docs, leaf, _validate_docs_leaf(leaf, value))
        # The backend/provider consistency check is the save path's job
        # (validate_llm): --set order is arbitrary, see that docstring.
        return
    if isinstance(cfg, NotificationsConfig):
        # set_key values arrive as strings; enabled is a bool ("true" would be
        # truthy as a string even when the user meant false) and backend has a
        # closed scheme set — both need explicit handling.
        if head == "enabled":
            if tail:
                raise ConfigError(f"unknown config key: notifications.{head}.{tail}")
            if value not in ("true", "false"):
                raise ConfigError(f"notifications.enabled expects true or false, got {value!r}")
            cfg.enabled = value == "true"
            return
        if head == "backend":
            if tail:
                raise ConfigError(f"unknown config key: notifications.{head}.{tail}")
            cfg.backend = validate_backend(value)
            return
        raise ConfigError(f"unknown config key: notifications.{head}")
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


def _hydrate(cls: type, data: dict, path: str):
    field_map = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(field_map)
    if unknown:
        raise ConfigError(f"unknown config key(s) {sorted(unknown)} in {path}")
    kwargs = {}
    for name, value in data.items():
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
                providers[k] = _hydrate(ProviderConfig, v, path)
            kwargs[name] = providers
        elif is_dataclass(f.type):
            if not isinstance(value, dict):
                raise ConfigError(f"invalid value for {name!r} in {path}: expected an object")
            kwargs[name] = _hydrate(f.type, value, path)
        else:
            kwargs[name] = value
    obj = cls(**kwargs)
    if cls is LLMConfig:
        validate_llm(obj)
    if cls is NotificationsConfig:
        if not isinstance(obj.enabled, bool):
            raise ConfigError(
                f"invalid value for 'notifications.enabled' in {path}: expected true/false"
            )
        if not isinstance(obj.backend, str):
            raise ConfigError(f"invalid value for 'notifications.backend' in {path}")
        validate_backend(obj.backend)
    if cls is WorktreeConfig:
        for fname in ("branch_prefix", "base_branch"):
            try:
                validate_worktree_value(fname, getattr(obj, fname))
            except ConfigError as exc:
                raise ConfigError(f"{exc} in {path}") from exc
    return obj
