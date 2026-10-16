"""Catalog-bound answers for remapping stale configured model leaves."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, fields

from . import docsllm, taskmodels
from .config import store
from .config.schema import Config, ProviderConfig, TaskModelsConfig
from .config.store import validate_task_model
from .errors import ConfigError
from .providers.registry import get_provider
from .taskmodels import CatalogModel
from .toolctx import ToolContext


@dataclass(frozen=True)
class StaleLeaf:
    path: str
    provider: str
    value: str
    role: str
    effort: str
    source: str  # "cli" or "api"


class DryRunStale(Exception):
    """Successful dry run reported all stale leaves; stop before model resolution."""


def build_remap_prompt(stale: list[StaleLeaf], offered: dict[str, list[CatalogModel]]) -> str:
    """Ask the provider default model to choose once for every stale leaf."""
    lines = [
        "Reply with one JSON object and nothing else.",
        "Use exactly the leaf paths below as keys. Each value must be one listed full model ID.",
        "Task leaves may append :effort only when supported; docs_model uses no :effort.",
        'Efforts "unknown" means the catalog lists no effort data for that model.',
        "Do not use family aliases.",
        "Choose a suitable model for each role from that leaf's own allowed list:",
    ]
    for leaf in stale:
        choices = [
            {
                "id": model.id,
                "family": model.family,
                "efforts": "unknown" if model.efforts is None else list(model.efforts),
            }
            for model in offered.get(leaf.path, [])
        ]
        lines.append(
            f"{json.dumps(leaf.path)} — role: {leaf.role}; "
            f"effective value: {leaf.value}; applied effort: {leaf.effort or 'none'}; "
            f"source: {leaf.source}; allowed: {json.dumps(choices)}"
        )
    return "\n".join(lines)


def parse_remap_output(
    output: str, stale: list[StaleLeaf], offered: dict[str, list[CatalogModel]]
) -> dict[str, str]:
    """Parse one CLI-framed JSON answer and validate all leaves before returning."""
    paths = {leaf.path for leaf in stale}
    first_path = stale[0].path if stale else "model"

    def invalid(path: str, answer: object, reason: str, *, known_leaf: bool = True) -> ConfigError:
        choices = (
            offered.get(path, [])
            if known_leaf and path in paths
            else [model for leaf in stale for model in offered.get(leaf.path, [])]
        )
        ids = ", ".join(model.id for model in choices) or "none"
        return ConfigError(
            f"invalid remap for {path}: answer {answer!r}; {reason}; offered IDs: {ids}"
        )

    # Codex exec may print surrounding status lines; the answer is an object
    # beginning on its own line. A live Codex 0.156.1 remap of retired
    # gpt-5.1-sol returned gpt-6-sol:high through this parser. JSON may span
    # lines or sit inside a Markdown code fence.
    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key {key}")
            value[key] = item
        return value

    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    candidates: list[dict[str, object]] = []
    malformed: str | None = None
    for match in re.finditer(r"(?m)^[ \t]*\{", output):
        answer = output[match.start() :].lstrip()
        try:
            value, end = decoder.raw_decode(answer)
        except (json.JSONDecodeError, ValueError):
            malformed = answer.splitlines()[0]
            continue
        if answer[end:].split("\n", 1)[0].strip():
            malformed = answer.splitlines()[0]
            continue
        if not isinstance(value, dict):
            raise invalid(first_path, value, "expected a JSON object", known_leaf=False)
        candidates.append(value)
    if len(candidates) != 1 or malformed is not None:
        answer = malformed if malformed is not None else output.strip()[:200]
        raise invalid(
            first_path, answer, "expected exactly one well-formed JSON object", known_leaf=False
        )

    answers = candidates[0]
    for path in sorted(paths - answers.keys()):
        raise invalid(path, "<missing>", "missing key")
    for path in sorted(answers.keys() - paths):
        raise invalid(path, answers[path], "extra key", known_leaf=False)

    result: dict[str, str] = {}
    for leaf in stale:
        answer = answers[leaf.path]
        if not isinstance(answer, str):
            raise invalid(leaf.path, answer, "expected a string")
        model_id, separator, effort = answer.partition(":")
        if not model_id or (separator and not effort):
            raise invalid(leaf.path, answer, "malformed model choice")
        model = next((item for item in offered.get(leaf.path, []) if item.id == model_id), None)
        if model is None:
            raise invalid(leaf.path, answer, "ID is not listed for this leaf")
        if leaf.path == "docs_model" and effort:
            # docs_llm_for passes docs_model unchanged as --model; it has no
            # separate effort flag, even when the catalog lists efforts.
            raise invalid(leaf.path, answer, "documentation model does not accept :effort")
        # Unknown efforts (API model listings) leave the effort to the static
        # validate_task_model check below.
        if effort and model.efforts is not None and effort not in model.efforts:
            raise invalid(leaf.path, answer, "unsupported effort")
        try:
            if leaf.path == "docs_model":
                docsllm.check_model_id(model_id)
            else:
                validate_task_model(leaf.provider, answer)
        except (ConfigError, docsllm.ProbeFailed) as exc:
            raise invalid(leaf.path, answer, str(exc)) from exc
        result[leaf.path] = answer
    return result


def _selected_leaves(cfg: Config, groups: Collection[str]) -> list[StaleLeaf]:
    unknown = set(groups) - {"session", "docs", "orchestrator"}
    if unknown:
        raise ValueError(f"unknown reconciliation groups: {', '.join(sorted(unknown))}")
    selected: list[StaleLeaf] = []
    provider = cfg.llm.default
    pcfg = cfg.llm.providers.get(provider) or ProviderConfig()
    if "session" in groups or "orchestrator" in groups:
        wanted, effort = taskmodels.probe_key(provider, pcfg.model, "orchestrator")
        selected.append(StaleLeaf("model", provider, wanted, "Orchestrator model", effort, "cli"))
    if "session" in groups:
        for field in fields(TaskModelsConfig):
            saved = getattr(pcfg.tasks, field.name)
            wanted, effort = taskmodels.probe_key(provider, saved, field.name)
            selected.append(
                StaleLeaf(
                    f"tasks.{field.name}", provider, wanted, field.metadata["label"], effort, "cli"
                )
            )
    if "docs" in groups:
        docs_provider = cfg.llm.docs.provider or provider
        docs_pcfg = cfg.llm.providers.get(docs_provider) or ProviderConfig()
        wanted = docs_pcfg.docs_model or get_provider(docs_provider).docs_model_default()
        # A blank Codex docs default supplies no --model flag and has no
        # catalog membership to check.
        if wanted:
            _, _, effort = wanted.partition(":")
            selected.append(
                StaleLeaf(
                    "docs_model",
                    docs_provider,
                    wanted,
                    "Documentation model",
                    effort,
                    cfg.llm.docs.backend,
                )
            )
    return selected


def _set_leaf(pcfg: ProviderConfig, path: str, value: str) -> None:
    if path == "model":
        pcfg.model = value
    elif path == "docs_model":
        pcfg.docs_model = value
    else:
        setattr(pcfg.tasks, path.removeprefix("tasks."), value)


def reconcile(
    ctx: ToolContext,
    cfg: Config,
    leaves: Collection[str],
    *,
    say: Callable[[str], object],
    dry_run: bool,
) -> Config:
    """Check only selected model leaves, then remap stale values as one transaction.

    CLI catalogs are refreshed once per stale provider. API documentation
    catalogs are already live, but their remap still uses the provider CLI's
    default model. A successful dry run reports every stale leaf and signals
    the command dispatcher to stop before it resolves any model.
    """
    selected = _selected_leaves(cfg, leaves)
    if not selected:
        return cfg

    buckets = {(leaf.provider, leaf.source) for leaf in selected}
    catalogs: dict[tuple[str, str], list[CatalogModel] | None] = {}
    for provider, source in sorted(buckets):
        if source == "api":
            key = cfg.secrets.api_keys.get(provider, "")
            if not key:
                raise ConfigError(
                    f"api documentation backend configured for {provider} but no key is stored — "
                    "run omc configure"
                )
            catalogs[(provider, source)] = taskmodels.catalog(ctx, provider, docs_api_key=key)
        else:
            try:
                catalogs[(provider, source)] = taskmodels.catalog(ctx, provider)
            except taskmodels.CatalogMissing:
                catalogs[(provider, source)] = None

    def stale_leaves() -> list[StaleLeaf]:
        def stale(leaf: StaleLeaf) -> bool:
            models = catalogs[(leaf.provider, leaf.source)]
            if models is None:
                return True
            if leaf.path == "docs_model" and leaf.effort:
                # Documentation passes the saved value as one --model arg;
                # it has no separate effort setting to apply at launch.
                return True
            if leaf.path == "docs_model" and leaf.source == "api":
                # API docs launch requires a full ID. A blank saved leaf
                # resolves to the provider's family default, which can be
                # catalog-compatible yet still fail docs_llm_for.
                if get_provider(leaf.provider).api_model_family(leaf.value):
                    return True
            return not taskmodels.compatible(leaf.value, leaf.effort, models)

        return [leaf for leaf in selected if stale(leaf)]

    stale = stale_leaves()
    if dry_run and stale:
        for leaf in stale:
            say(f"· {leaf.path}: {leaf.value} is unavailable (would remap)")
        raise DryRunStale()

    refresh = {leaf.provider for leaf in stale if leaf.source == "cli"}
    for provider in sorted(refresh):
        say(f"→ refreshing {provider} model list with the default model")
        # Both CLIs refresh their picker cache during a no-model headless turn.
        # This also protects a newly released, hand-entered full ID that was
        # absent only from yesterday's catalog.
        ok, detail = docsllm.cli_model_probe(ctx, provider, "")
        docsllm._step(say, ok, detail)
        catalogs[(provider, "cli")] = taskmodels.catalog(ctx, provider)

    stale = stale_leaves()
    current_providers = {leaf.provider for leaf in selected} - {leaf.provider for leaf in stale}
    for provider in sorted(current_providers):
        ids = {
            model.id
            for (name, _), models in catalogs.items()
            if name == provider and models is not None
            for model in models
        }
        say(f"✓ {provider} models current ({len(ids)} listed)")
    if not stale:
        return cfg

    answers: dict[tuple[str, str], str] = {}
    for provider in sorted({leaf.provider for leaf in stale}):
        provider_leaves = [leaf for leaf in stale if leaf.provider == provider]
        offered = {leaf.path: catalogs[(provider, leaf.source)] or [] for leaf in provider_leaves}
        prompt = build_remap_prompt(provider_leaves, offered)
        ok, detail = docsllm.cli_model_probe(ctx, provider, "", prompt=prompt, return_output=True)
        if not ok:
            raise docsllm.ProbeFailed(detail)
        parsed = parse_remap_output(detail, provider_leaves, offered)
        answers.update(((provider, path), value) for path, value in parsed.items())

    # A watch process may hold cfg for days. Reload just before saving so a
    # newer configure edit in any unrelated leaf survives this transaction.
    global_cfg = store.load_global(ctx.home)
    if global_cfg is None:
        raise ConfigError(f"global config missing at {store.global_config_path(ctx.home)}")
    for (provider, path), value in answers.items():
        _set_leaf(global_cfg.llm.providers.setdefault(provider, ProviderConfig()), path, value)
    store.save_global(ctx.home, global_cfg)
    for leaf in stale:
        value = answers[(leaf.provider, leaf.path)]
        _set_leaf(cfg.llm.providers.setdefault(leaf.provider, ProviderConfig()), leaf.path, value)
        say(
            f"→ {leaf.path}: {leaf.value} is unavailable; {leaf.provider} "
            f"(default model) picked {value}; config updated"
        )
    return cfg
