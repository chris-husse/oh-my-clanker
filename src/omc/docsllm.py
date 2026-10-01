"""Configure-time validation of the documentation LLM backend (spec
2026-10-01 §3.2). Two probes per backend, each returning ``(ok, detail)`` like
``toolctx.tool_version`` — never raising from the subprocess/HTTP layer. All
I/O goes through ToolContext; this module does no subprocess or network calls
itself. It also OWNS key redaction: ToolContext never knows the key.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .errors import OmcError
from .providers.registry import get_provider
from .toolctx import ToolContext

ANTHROPIC_VERSION = "2023-06-01"
MODELS_PAGE_LIMIT = 1000  # the list endpoint is paginated; ask for the maximum page
HEADLESS_PROBE_PROMPT = "Reply with exactly OK."
HEADLESS_PROBE_TIMEOUT = 120.0
AUTH_STATUS_TIMEOUT = 30.0
MASK = "******"
# A model id goes into the per-id URL path verbatim (no urllib here: ToolContext
# is the only network boundary), so anything outside this set is refused.
_MODEL_ID = re.compile(r"[A-Za-z0-9._:-]+")


class ProbeFailed(OmcError):
    """A documentation probe failed. The message is ALREADY redacted — cli.main
    prints it verbatim as `error: …` (rc 1)."""


def mask_key(key: str) -> str:
    """`******` + the last four characters; all asterisks under eight."""
    return f"{MASK}{key[-4:]}" if len(key) >= 8 else "********"


def redact(text: str, key: str) -> str:
    """Exact-substring replacement — sufficient given the key rules (printable
    ASCII, no whitespace). Callers redact BEFORE truncating."""
    return text.replace(key, MASK) if key else text


@dataclass(frozen=True)
class ModelInfo:
    id: str
    created_at: str


@dataclass(frozen=True)
class DocsSelection:
    provider: str
    backend: str  # "cli" | "api"
    model: str  # cli: alias or id or ""; api: alias or full id or ""
    key: str  # "" on cli


Say = Callable[[str], object]


def _base(provider: str) -> str:
    return get_provider(provider).api_base_url()


def _host(base: str) -> str:
    return base.split("/")[2]


def _models_url(base: str) -> str:
    return base.rstrip("/") + "/models"  # never "//models": GitNexus-style join


def _headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}


def _error_message(body: str, key: str) -> str:
    """Anthropic's error.message, else the raw body — REDACTED before the cut."""
    body = redact(body, key)
    try:
        return str(json.loads(body)["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return body.strip()[:200]


def api_connection_probe(
    ctx: ToolContext, provider: str, key: str
) -> tuple[bool, str, list[ModelInfo]]:
    """GET /models proves the key and yields the picker's list."""
    base = _base(provider)
    host = _host(base)
    status, body = ctx.http_get(
        f"{_models_url(base)}?limit={MODELS_PAGE_LIMIT}", headers=_headers(key)
    )
    if status in (401, 403):
        return False, f"key rejected by {host} (HTTP {status})", []
    if status == 0:
        return False, redact(f"could not reach {host}: {body}", key), []
    if not 200 <= status < 300:
        return False, f"{host} answered HTTP {status}: {_error_message(body, key)}", []
    try:
        data = json.loads(body)["data"]
        if not isinstance(data, list):
            raise TypeError("data is not a list")
        models = [
            ModelInfo(str(m["id"]), str(m.get("created_at") or ""))
            for m in data
            if isinstance(m, dict) and "id" in m
        ]
    except (ValueError, KeyError, TypeError):
        return False, f"{host} returned an unexpected model list", []
    return True, f"key accepted ({len(models)} models)", models


def _newest_first(models: list[ModelInfo]) -> list[ModelInfo]:
    return sorted(models, key=lambda m: m.created_at, reverse=True)


def model_choices(models: list[ModelInfo]) -> list[str]:
    """Picker entries: claude-* only, Haiku dropped (the cheap tier is never
    used — AGENTS.md tier policy), newest first."""
    picked = [m for m in models if m.id.startswith("claude-") and "haiku" not in m.id]
    return [m.id for m in _newest_first(picked)]


def check_model_id(model: str) -> str:
    """Refuse an id that is unsafe in the per-id URL path (`?`, `/`, `#`, …)."""
    if not _MODEL_ID.fullmatch(model):
        raise ProbeFailed(f"invalid model id {model!r}")
    return model


def resolve_model(provider: str, model: str, models: list[ModelInfo]) -> str:
    """A family alias → the newest fetched id with that family's prefix (so the
    persisted docs_model is a full id and nothing rots); a full id passes through."""
    prefix = get_provider(provider).api_model_family(model)
    if not prefix:
        return check_model_id(model)
    family = [m for m in models if m.id.startswith(prefix) and "haiku" not in m.id]
    if not family:
        raise ProbeFailed(f"no {model} models available to this key")
    return _newest_first(family)[0].id


def api_model_probe(ctx: ToolContext, provider: str, key: str, model_id: str) -> tuple[bool, str]:
    """Always the per-id GET, never list membership (pagination-proof). The id
    is re-checked here: configure's typed "Other" id never passes resolve_model."""
    try:
        check_model_id(model_id)
    except ProbeFailed as exc:
        return False, str(exc)
    base = _base(provider)
    status, body = ctx.http_get(f"{_models_url(base)}/{model_id}", headers=_headers(key))
    if 200 <= status < 300:
        return True, f"{model_id} works"
    if status == 404:
        return False, f"model {model_id!r} is not available to this key"
    if status == 0:
        return False, redact(f"could not reach {_host(base)}: {body}", key)
    return False, f"{_host(base)} answered HTTP {status}: {_error_message(body, key)}"


def cli_connection_probe(ctx: ToolContext, provider: str) -> tuple[bool, str]:
    """`claude auth status` → JSON with loggedIn. Lowercase "not logged in" on
    purpose: the E2E harness fails any output containing `Not logged in`."""
    argv = get_provider(provider).auth_status_argv()
    if not argv:
        return True, f"no login check for {provider}"  # codex: the model probe is the check
    try:
        cp = ctx.run_bounded(argv, timeout=AUTH_STATUS_TIMEOUT)
    except FileNotFoundError:
        return False, f"{provider} CLI not found on PATH"
    except OSError as exc:  # run_bounded's TimeoutError is an OSError subclass
        return False, f"{provider} auth status failed: {exc}"
    try:
        status = json.loads(cp.stdout or "")
    except ValueError:
        status = None
    if cp.returncode != 0 or not isinstance(status, dict):
        tail = (cp.stderr or cp.stdout or "").strip()
        return False, f"{provider} auth status failed (exit {cp.returncode}): {tail[:200]}"
    if status.get("loggedIn") is not True:
        return False, f"{provider} is not logged in — run {provider} auth login"
    return True, f"{provider} is logged in"


def cli_model_probe(ctx: ToolContext, provider: str, model: str) -> tuple[bool, str]:
    """One headless turn on the user's login. run_bounded, not run: `claude -p`
    spawns MCP grandchildren that a plain subprocess timeout would orphan."""
    p = get_provider(provider)
    argv = p.headless_argv(HEADLESS_PROBE_PROMPT, model=model)
    shown = model or "default model"
    try:
        # cwd=ctx.home: run the probe OUTSIDE whatever project the user typed
        # `omc configure` in, so Claude loads no project MCP servers/hooks.
        # The probe runs before configure's first write, so on a fresh machine
        # the home may not exist yet — a missing cwd would surface as a
        # misleading FileNotFoundError ("CLI not found").
        ctx.home.mkdir(parents=True, exist_ok=True)
        cp = ctx.run_bounded(
            argv, timeout=HEADLESS_PROBE_TIMEOUT, cwd=str(ctx.home), extra_env=p.title_env()
        )
    except FileNotFoundError:
        return False, f"{provider} CLI not found on PATH"
    except TimeoutError:
        return False, f"{provider} did not answer within {int(HEADLESS_PROBE_TIMEOUT)}s"
    except OSError as exc:
        return False, f"{provider} failed to launch: {exc}"
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "").strip()[:200]
        return False, f"{provider} rejected model {shown!r}: {tail}"
    if not (cp.stdout or "").strip():
        return False, f"{provider} returned no output for model {shown!r}"
    return True, f"{shown} works"


def _step(say: Say, ok: bool, detail: str) -> None:
    say(("✓ " if ok else "✗ ") + detail)
    if not ok:
        raise ProbeFailed(detail)


def validate_key_only(ctx: ToolContext, provider: str, key: str, *, say: Say) -> None:
    """The key changed while the backend is not `api`: prove the key anyway."""
    say(f"→ checking the key against {_host(_base(provider))}")
    ok, detail, _ = api_connection_probe(ctx, provider, key)
    _step(say, ok, detail)


def validate_selection(ctx: ToolContext, sel: DocsSelection, *, say: Say) -> str:
    """Both probes for the selection. Returns the model to PERSIST: on api the
    resolved full id (alias → newest family member), on cli sel.model as is."""
    if sel.backend == "api":
        say(f"→ checking the key against {_host(_base(sel.provider))}")
        ok, detail, models = api_connection_probe(ctx, sel.provider, sel.key)
        _step(say, ok, detail)
        wanted = sel.model or get_provider(sel.provider).docs_model_default()
        model = resolve_model(sel.provider, wanted, models)
        say(f"→ validating {model} via api")
        ok, detail = api_model_probe(ctx, sel.provider, sel.key, model)
        _step(say, ok, detail)
        return model
    say(f"→ checking {sel.provider} login")
    ok, detail = cli_connection_probe(ctx, sel.provider)
    _step(say, ok, detail)
    say(f"→ validating {sel.model or 'default model'} via cli")
    ok, detail = cli_model_probe(ctx, sel.provider, sel.model)
    _step(say, ok, detail)
    return sel.model
