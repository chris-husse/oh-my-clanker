"""Resolve task model families to provider launch arguments.

Configuration is static; only this runtime module reads Codex's model list,
through ToolContext. Claude family aliases are understood by its CLI directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import docsllm
from .config.schema import Config, GlobalConfig
from .config.store import _BANNED_FAMILIES, validate_task_model
from .errors import ConfigError
from .providers.registry import get_provider
from .toolctx import ToolContext

TASKS = ("orchestrator", "design", "plan", "review", "simple", "medium", "high")
EXACT_ID_PROMPT = "Reply with only your exact model id"


@dataclass(frozen=True)
class ModelChoice:
    model_arg: str
    effort: str


@dataclass(frozen=True)
class CatalogModel:
    id: str
    family: str
    # None: the source lists no effort data (unknown), unlike () (none offered).
    efforts: tuple[str, ...] | None


class CatalogMissing(ConfigError):
    """A provider CLI has not written its model cache yet; one refresh may help."""


@dataclass(frozen=True)
class _ListedModel:
    slug: str
    display_name: str
    priority: int
    visibility: str
    efforts: tuple[str, ...]


def _model_list(ctx: ToolContext, *, missing_ok: bool = False) -> list[_ListedModel] | None:
    path = get_provider("codex").model_list_path(ctx.env)
    try:
        raw = ctx.read_text(path)
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise ConfigError(f"Codex model list missing at {path}; run omc configure") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read Codex model list {path}: {exc}") from exc
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            raise ValueError("expected a models list")
        models: list[_ListedModel] = []
        for entry in data["models"]:
            if not isinstance(entry, dict):
                raise ValueError("expected a model object")
            slug = entry.get("slug")
            display = entry.get("display_name")
            visibility = entry.get("visibility")
            priority = entry.get("priority")
            levels = entry.get("supported_reasoning_levels")
            if (
                not isinstance(slug, str)
                or not slug
                or not isinstance(display, str)
                or not display
                or not isinstance(visibility, str)
                or not isinstance(priority, int)
                or isinstance(priority, bool)
                or not isinstance(levels, list)
            ):
                raise ValueError("invalid model fields")
            efforts = []
            for level in levels:
                if not isinstance(level, dict) or not isinstance(level.get("effort"), str):
                    raise ValueError("invalid supported_reasoning_levels")
                efforts.append(level["effort"])
            models.append(_ListedModel(slug, display, priority, visibility, tuple(efforts)))
        return models
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"invalid Codex model list {path}: {exc}") from exc


def _matches_family(model: _ListedModel, family: str) -> bool:
    return bool(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(family)}(?![A-Za-z0-9])", model.display_name, re.I)
    )


def _cheap(provider: str, model_id: str) -> bool:
    return any(part.lower() in _BANNED_FAMILIES[provider] for part in re.split(r"[-_.]", model_id))


def _codex_catalog(ctx: ToolContext) -> list[CatalogModel]:
    path = get_provider("codex").model_list_path(ctx.env)
    models = _model_list(ctx, missing_ok=True)
    if models is None:
        raise CatalogMissing(
            f"Codex model list missing at {path}; open codex once to fetch its model list"
        )
    families = get_provider("codex").families()
    result: list[CatalogModel] = []
    # Codex's visible cache priority is the picker's preference order.
    for model in sorted(models, key=lambda item: item.priority):
        if model.visibility != "list" or _cheap("codex", model.slug):
            continue
        family = next((name for name in families if _matches_family(model, name)), "")
        result.append(CatalogModel(model.slug, family, model.efforts))
    return result


def _claude_catalog(ctx: ToolContext) -> list[CatalogModel]:
    status = docsllm.auth_status(ctx, "claude")
    org_id = status.get("orgId")
    config_directory = status.get("configDirectory")
    if status.get("authMethod") == "api_key" and not org_id:
        # Claude Code 2.1.286 reports no orgId and writes no model-catalog
        # cache for ANTHROPIC_API_KEY auth, even after a successful turn.
        # Query the same key's live model list instead of guessing an org.
        key = ctx.env.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ConfigError(
                "claude API-key auth has no local model catalog; "
                "set ANTHROPIC_API_KEY for model reconciliation"
            )
        return _api_catalog(ctx, "claude", key)
    # Claude Code 2.1.300 account auth reports the current org and resolved
    # configDirectory. Its catalog files are <orgId>-*-cc.json under
    # cache/model-catalog; other organizations' files can coexist there.
    if (
        not isinstance(org_id, str)
        or not org_id
        or not isinstance(config_directory, str)
        or not config_directory
    ):
        raise ConfigError("claude auth status missing orgId or configDirectory")
    directory = Path(config_directory) / "cache" / "model-catalog"
    try:
        entries = ctx.list_dir(directory)
    except FileNotFoundError as exc:
        raise CatalogMissing(
            f"Claude model catalog missing under {directory}; open claude once to fetch it"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"cannot list Claude model catalog {directory}: {exc}") from exc
    prefix = f"{org_id}-"
    paths = [
        path
        for path in entries
        if path.name.startswith(prefix)
        and path.name.endswith("-cc.json")
        and len(path.name) > len(prefix) + len("-cc.json")
    ]
    if not paths:
        raise CatalogMissing(
            f"Claude model catalog missing under {directory}; open claude once to fetch it"
        )
    dated: list[tuple[datetime, Path, dict[str, object]]] = []
    for path in paths:
        try:
            data = json.loads(ctx.read_text(path))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid Claude model catalog {path}: {exc}") from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("fetchedAt"), str)
            or not data["fetchedAt"]
        ):
            raise ConfigError(f"invalid Claude model catalog {path}: expected fetchedAt")
        try:
            fetched_at = datetime.fromisoformat(data["fetchedAt"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfigError(f"invalid Claude model catalog {path}: invalid fetchedAt") from exc
        if fetched_at.tzinfo is None:
            raise ConfigError(f"invalid Claude model catalog {path}: fetchedAt has no timezone")
        dated.append((fetched_at, path, data))
    _, path, data = max(dated, key=lambda entry: entry[0])
    try:
        models = data["catalog"]["config"]["models"]
        if not isinstance(models, list):
            raise ValueError("expected a models list")
        result: list[CatalogModel] = []
        families = get_provider("claude").families()
        # Claude's catalog offers both `main` and `overflow` models. Preserve
        # its order within each family instead of guessing from the ID.
        for entry in models:
            if not isinstance(entry, dict):
                raise ValueError("expected a model object")
            model_id = entry.get("id")
            short_name = entry.get("short_name")
            thinking = entry.get("thinking")
            if (
                not isinstance(model_id, str)
                or not model_id
                or not isinstance(short_name, str)
                or not short_name
                or not isinstance(thinking, dict)
                or not isinstance(thinking.get("effort_options"), list)
            ):
                raise ValueError("invalid model fields")
            efforts: list[str] = []
            for option in thinking["effort_options"]:
                if (
                    not isinstance(option, dict)
                    or not isinstance(option.get("id"), str)
                    or not option["id"]
                ):
                    raise ValueError("invalid thinking.effort_options")
                efforts.append(option["id"])
            if _cheap("claude", model_id):
                continue
            family = short_name.lower()
            result.append(
                CatalogModel(model_id, family if family in families else "", tuple(efforts))
            )
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid Claude model catalog {path}: {exc}") from exc


def _api_catalog(ctx: ToolContext, provider: str, key: str) -> list[CatalogModel]:
    ok, detail, models = docsllm.api_connection_probe(ctx, provider, key)
    if not ok:
        raise ConfigError(detail)  # api_connection_probe already redacts the key
    adapter = get_provider(provider)
    prefixes = [(family, adapter.api_model_family(family)) for family in adapter.families()]
    result: list[CatalogModel] = []
    for model in sorted(models, key=lambda item: item.created_at, reverse=True):
        if _cheap(provider, model.id):
            continue
        family = next(
            (name for name, prefix in prefixes if prefix and model.id.startswith(prefix)), ""
        )
        # The models endpoint has IDs but no model-specific effort options:
        # support is unknown, so a configured effort is not evidence of staleness.
        result.append(CatalogModel(model.id, family, None))
    return result


def catalog(ctx: ToolContext, provider: str, *, docs_api_key: str = "") -> list[CatalogModel]:
    """Return the current non-cheap models offered by this provider source."""
    if docs_api_key:
        return _api_catalog(ctx, provider, docs_api_key)
    if provider == "codex":
        return _codex_catalog(ctx)
    if provider == "claude":
        return _claude_catalog(ctx)
    raise ConfigError(f"unknown model catalog provider: {provider}")


def compatible(value: str, effort: str, models: list[CatalogModel]) -> bool:
    """Check a family or full ID against the listed model and its applied effort.

    A model whose efforts are unknown is judged on family / exact ID alone.
    """
    token = value.partition(":")[0]
    if not token:
        return False
    model = next((item for item in models if item.family == token), None)
    if model is None:
        # Claude's [1m] long-context selector modifies a listed ID at launch;
        # the catalog itself contains only the base model ID.
        model_id = token.removesuffix("[1m]")
        model = next((item for item in models if item.id == model_id), None)
    if model is None:
        return False
    return not effort or model.efforts is None or effort in model.efforts


def _family_model(models: list[_ListedModel], family: str, path: Path) -> _ListedModel:
    visible = [m for m in models if m.visibility == "list"]
    matches = [m for m in visible if _matches_family(m, family)]
    if not matches:
        available = [
            name
            for name in get_provider("codex").families()
            if any(_matches_family(m, name) for m in visible)
        ]
        offered = ", ".join(available) if available else "none"
        raise ConfigError(f"{family} is unavailable in {path}; available families: {offered}")
    return min(matches, key=lambda model: model.priority)


def resolve_choice(
    ctx: ToolContext, provider: str, value: str, *, validate_effort: bool = False
) -> ModelChoice:
    """Resolve one saved family[:effort] (or pass a full model id through)."""
    adapter = get_provider(provider)
    validate_task_model(provider, value)
    family, _, effort = value.partition(":")
    if not family:
        raise ConfigError(f"empty {provider} task model cannot be resolved without a task default")
    if provider != "codex" or family not in adapter.families():
        return ModelChoice(family, effort)
    path = adapter.model_list_path(ctx.env)
    model = _family_model(_model_list(ctx), family, path)
    if validate_effort and effort and effort not in model.efforts:
        offered = ", ".join(model.efforts) or "none"
        raise ConfigError(
            f"{family}:{effort} is unavailable for {model.slug}; supported efforts: {offered}"
        )
    # Live Codex agent dispatch accepted exact gpt-6-sol/high and
    # gpt-6-astra/high slugs; the verdict can pass the cache slug directly
    # without translating it to a preset name.
    return ModelChoice(model.slug, effort)


def task_choice(
    ctx: ToolContext, cfg: Config | GlobalConfig, task: str, *, provider: str | None = None
) -> ModelChoice:
    if task not in TASKS:
        raise ValueError(f"unknown task: {task}")
    name = provider or cfg.llm.default
    adapter = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    saved = (pcfg.model if task == "orchestrator" else getattr(pcfg.tasks, task)) if pcfg else ""
    return resolve_choice(ctx, name, saved or adapter.default_task_model(task))


def orchestrator(ctx: ToolContext, cfg: Config) -> ModelChoice:
    return task_choice(ctx, cfg, "orchestrator")


def model_options(
    ctx: ToolContext,
    provider: str,
    task: str,
    *,
    warn: Callable[[str], object] | None = None,
) -> dict[str, str]:
    """Picker labels to stored values, ordered default/families/Other.

    A missing or unreadable Codex list degrades to the families-only picker
    (reported once through ``warn``); the configure probe rewrites the list
    before any value is saved, so the menu itself never fails on it.
    """
    adapter = get_provider(provider)
    default = adapter.default_task_model(task)
    options = {f"Provider default ({default})": ""}
    # An explicitly isolated ToolContext may have no home at all (for example
    # a preview/menu test). In that case there is no cache to read and the
    # documented family-only picker is the available choice.
    models = None
    if provider == "codex" and (ctx.env.get("HOME") or ctx.env.get("CODEX_HOME")):
        try:
            models = _model_list(ctx, missing_ok=True)
        except ConfigError as exc:
            if warn is not None:
                warn(f"· {exc}; Codex task pickers show families only")
    for family in adapter.families():
        label = family.title()
        options[label] = family
        if provider == "claude" and task != "orchestrator":
            continue
        if models is None:
            if provider == "codex":
                continue
            efforts = adapter.effort_levels()
        else:
            visible = [m for m in models if m.visibility == "list" and _matches_family(m, family)]
            efforts = list(min(visible, key=lambda m: m.priority).efforts) if visible else []
        for effort in efforts:
            options[f"{label} ({effort})"] = f"{family}:{effort}"
    options["Other (type a model id)"] = "__other__"
    return options


def probe_key(provider: str, value: str, task: str) -> tuple[str, str]:
    """What a probe of this leaf actually checks: (effective value, applied effort).

    Two changed leaves with the same key need one live probe, not two.
    """
    if task not in TASKS:
        raise ValueError(f"unknown task: {task}")
    wanted = value or get_provider(provider).default_task_model(task)
    _, _, effort = wanted.partition(":")
    if provider == "claude" and task != "orchestrator":
        effort = ""  # a Claude subagent takes no effort level (§7)
    return wanted, effort


def validate_selection(
    ctx: ToolContext,
    provider: str,
    value: str,
    *,
    task: str,
    say: Callable[[str], object],
    refresh: bool = True,
) -> ModelChoice:
    """Probe a changed picker value before saving it; return the effective choice.

    The caller keeps ``value`` in config, including a blank default. Resolved
    Codex slugs and Claude's reported id are only shown to the user.
    ``refresh=False`` reuses a Codex model list refreshed earlier in the same
    configure run instead of spending another headless turn on it.
    """
    wanted, effort = probe_key(provider, value, task)
    family = wanted.partition(":")[0]
    if provider == "codex" and family not in get_provider(provider).families():
        # A full id needs no list (§5): prove the id itself works, once.
        say(f"→ validating {wanted} via codex")
        ok, detail = docsllm.cli_model_probe(ctx, provider, family, effort=effort)
        docsllm._step(say, ok, detail)
        return ModelChoice(family, effort)
    if provider == "codex":
        if refresh:
            # Codex 0.158: a no-model exec uses the account default and
            # refreshes the same models_cache.json shown by the interactive
            # model picker.
            say("→ refreshing Codex model list with the default model")
            ok, detail = docsllm.cli_model_probe(ctx, provider, "")
            docsllm._step(say, ok, detail)
        try:
            choice = resolve_choice(ctx, provider, wanted, validate_effort=True)
        except ConfigError as exc:
            path = get_provider(provider).model_list_path(ctx.env)
            if "model list missing" in str(exc):
                raise ConfigError(
                    f"Codex model list missing at {path}; open codex once to fetch its model list"
                ) from exc
            raise
        suffix = f" ({choice.effort})" if choice.effort else ""
        say(f"✓ {wanted} → {choice.model_arg}{suffix}")
        return choice
    say(f"→ validating {wanted} via claude")
    # Claude's CLI and Agent tool accept family aliases directly. Its answer
    # supplies the current full id for display only; no id is stored.
    ok, detail = docsllm.cli_model_probe(
        ctx, provider, family, prompt=EXACT_ID_PROMPT, effort=effort, return_output=True
    )
    docsllm._step(say, ok, detail if not ok else f"{wanted} → {detail}")
    return ModelChoice(family, effort)
