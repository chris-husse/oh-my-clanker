"""Resolve task model families to provider launch arguments.

Configuration is static; only this runtime module reads Codex's model list,
through ToolContext. Claude family aliases are understood by its CLI directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ClaudeCatalog:
    """One read of Claude's model list and what its on-disk file says about its age."""

    models: list[CatalogModel]
    # False when the file's staleAt is in the past, missing or malformed — a
    # refresh turn may change the list. True when Claude would still serve the
    # file from cache, and for the live API-key listing (no file, no age).
    fresh: bool
    # Whole minutes past staleAt when the stamp is usable and in the past;
    # None otherwise (fresh, unusable stamp, or the live listing).
    overdue_minutes: int | None


class CatalogMissing(ConfigError):
    """Claude has not written its model catalog. A refresh turn with nonessential
    traffic enabled can create it (reconcile.py); the message names the directory."""


@dataclass(frozen=True)
class _ListedModel:
    slug: str
    display_name: str
    priority: int
    visibility: str
    efforts: tuple[str, ...]


_CODEX_MODELS_KEY = "omc.taskmodels.codex_models"


def _parse_codex_models(raw: str) -> list[_ListedModel]:
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
        raise ConfigError(
            f"invalid codex debug models output: {exc}; output: {raw[-200:]!r}"
        ) from exc


def _codex_models(ctx: ToolContext, *, refresh: bool) -> list[_ListedModel]:
    """Read the launched Codex binary's catalog once per context, unless refreshed."""
    if not refresh and _CODEX_MODELS_KEY in ctx.runtime_cache:
        cached = ctx.runtime_cache[_CODEX_MODELS_KEY]
        if isinstance(cached, ConfigError):
            raise cached
        return cached  # type: ignore[return-value]
    try:
        # Codex 0.158 prints the cache entry shape as {"models": [...]} on stdout.
        # It refreshes by its own 300 s TTL and client_version rule; logged-out
        # clients print the bundled list. Warnings on stderr do not spoil stdout.
        cp = ctx.run_bounded(["codex", "debug", "models"], timeout=docsllm.AUTH_STATUS_TIMEOUT)
        if cp.returncode != 0:
            tail = (cp.stderr or cp.stdout or "").strip()[-200:]
            raise ConfigError(f"codex debug models failed (exit {cp.returncode}): {tail}")
        models = _parse_codex_models(cp.stdout or "")
    except FileNotFoundError as exc:
        error = ConfigError(
            f"codex debug models unavailable: Codex CLI not found on PATH; "
            f"{get_provider('codex').install_hint()}"
        )
        ctx.runtime_cache[_CODEX_MODELS_KEY] = error
        raise error from exc
    except TimeoutError as exc:
        error = ConfigError(f"codex debug models timed out: {exc}")
        ctx.runtime_cache[_CODEX_MODELS_KEY] = error
        raise error from exc
    except UnicodeError as exc:
        error = ConfigError(f"codex debug models returned undecodable output: {exc}")
        ctx.runtime_cache[_CODEX_MODELS_KEY] = error
        raise error from exc
    except OSError as exc:
        error = ConfigError(f"codex debug models failed: {exc}")
        ctx.runtime_cache[_CODEX_MODELS_KEY] = error
        raise error from exc
    except ConfigError as exc:
        ctx.runtime_cache[_CODEX_MODELS_KEY] = exc
        raise
    ctx.runtime_cache[_CODEX_MODELS_KEY] = models
    return models


def _matches_family(model: _ListedModel, family: str) -> bool:
    return bool(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(family)}(?![A-Za-z0-9])", model.display_name, re.I)
    )


def _cheap(provider: str, model_id: str) -> bool:
    return any(part.lower() in _BANNED_FAMILIES[provider] for part in re.split(r"[-_.]", model_id))


def _codex_catalog(ctx: ToolContext) -> list[CatalogModel]:
    models = _codex_models(ctx, refresh=True)
    families = get_provider("codex").families()
    result: list[CatalogModel] = []
    # Codex's visible catalog priority is the picker's preference order.
    for model in sorted(models, key=lambda item: item.priority):
        if model.visibility != "list" or _cheap("codex", model.slug):
            continue
        family = next((name for name in families if _matches_family(model, name)), "")
        result.append(CatalogModel(model.slug, family, model.efforts))
    return result


def claude_catalog(ctx: ToolContext) -> ClaudeCatalog:
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
        return ClaudeCatalog(_api_catalog(ctx, "claude", key), fresh=True, overdue_minutes=None)
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
        raise CatalogMissing(f"Claude model catalog missing under {directory}") from exc
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
        raise CatalogMissing(f"Claude model catalog missing under {directory}")
    dated: list[tuple[int, Path, dict[str, object]]] = []
    for path in paths:
        try:
            data = json.loads(ctx.read_text(path))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid Claude model catalog {path}: {exc}") from exc
        # Claude Code 2.1.301 writes fetchedAt as epoch milliseconds.
        fetched_at = data.get("fetchedAt") if isinstance(data, dict) else None
        if not isinstance(fetched_at, int) or isinstance(fetched_at, bool):
            raise ConfigError(f"invalid Claude model catalog {path}: expected fetchedAt")
        dated.append((fetched_at, path, data))
    _, path, data = max(dated, key=lambda entry: entry[0])
    # Claude Code writes staleAt beside fetchedAt, also epoch milliseconds (live
    # 2.1.312: staleAt − fetchedAt ≈ 62 min), and refetches past it when
    # nonessential traffic is allowed. A missing or malformed stamp cannot vouch
    # for the list's age, so it counts as stale: one extra refresh turn is the
    # cheap side of that doubt, never an error. The list below is still read.
    stale_at = data.get("staleAt")
    if not isinstance(stale_at, int) or isinstance(stale_at, bool):
        fresh, overdue = False, None
    else:
        now = ctx.now_ms()
        if now < stale_at:
            fresh, overdue = True, None
        else:
            fresh, overdue = False, (now - stale_at) // 60_000
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
            if isinstance(thinking, dict) and thinking.get("type") == "none":
                # A model without effort levels lists no effort_options.
                thinking = {"effort_options": []}
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
        return ClaudeCatalog(result, fresh=fresh, overdue_minutes=overdue)
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
        return claude_catalog(ctx).models
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


def _family_model(models: list[_ListedModel], family: str, source: str) -> _ListedModel:
    visible = [m for m in models if m.visibility == "list"]
    matches = [m for m in visible if _matches_family(m, family)]
    if not matches:
        available = [
            name
            for name in get_provider("codex").families()
            if any(_matches_family(m, name) for m in visible)
        ]
        offered = ", ".join(available) if available else "none"
        raise ConfigError(f"{family} is unavailable in {source}; available families: {offered}")
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
    model = _family_model(_codex_models(ctx, refresh=False), family, "codex debug models")
    if validate_effort and effort and effort not in model.efforts:
        offered = ", ".join(model.efforts) or "none"
        raise ConfigError(
            f"{family}:{effort} is unavailable for {model.slug}; supported efforts: {offered}"
        )
    # Live Codex agent dispatch accepted exact gpt-6-sol/high and
    # gpt-6-astra/high slugs; the verdict can pass the catalog slug directly
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

    A failing Codex CLI read degrades to families only. Repeated pickers
    reuse the context's memoized error; the menu's ``warn_once`` avoids
    repeating the warning.
    """
    adapter = get_provider(provider)
    default = adapter.default_task_model(task)
    options = {f"Provider default ({default})": ""}
    models = None
    if provider == "codex":
        try:
            models = _codex_models(ctx, refresh=False)
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
) -> ModelChoice:
    """Probe a changed picker value before saving it; return the effective choice.

    The caller keeps ``value`` in config, including a blank default. Resolved
    Codex slugs and Claude's reported id are only shown to the user.
    Codex family choices use the catalog already read on this context.
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
        choice = resolve_choice(ctx, provider, wanted, validate_effort=True)
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
