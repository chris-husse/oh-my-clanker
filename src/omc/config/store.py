from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path

from ..errors import ConfigError
from .schema import Config, LLMConfig, ProviderConfig


def config_path(home: Path) -> Path:
    return home / "config.json"


def load(home: Path) -> Config | None:
    path = config_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected an object")
    return _hydrate(Config, data, str(path))


def save(home: Path, cfg: Config) -> None:
    home.mkdir(parents=True, exist_ok=True)
    config_path(home).write_text(json.dumps(asdict(cfg), indent=2) + "\n")


def set_key(cfg: object, dotted: str, value: str) -> None:
    """Set a dotted leaf key. `llm.providers.<name>.model` creates the provider entry."""
    head, _, tail = dotted.partition(".")
    if isinstance(cfg, LLMConfig) and head == "providers":
        name, _, leaf = tail.partition(".")
        if leaf != "model":
            raise ConfigError(f"unknown config key: providers.{tail}")
        cfg.providers.setdefault(name, ProviderConfig()).model = value
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
    setattr(cfg, head, value)


def _hydrate(cls: type, data: dict, path: str):
    field_map = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(field_map)
    if unknown:
        raise ConfigError(f"unknown config key(s) {sorted(unknown)} in {path}")
    kwargs = {}
    for name, value in data.items():
        f = field_map[name]
        if cls is LLMConfig and name == "providers" and isinstance(value, dict):
            kwargs[name] = {k: _hydrate(ProviderConfig, v, path) for k, v in value.items()}
        elif is_dataclass(f.type) and isinstance(value, dict):
            kwargs[name] = _hydrate(f.type, value, path)
        else:
            kwargs[name] = value
    return cls(**kwargs)
