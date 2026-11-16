"""Model probes used by model reconciliation and the task-model pickers: the
provider CLI's login state, one headless model turn, and the HTTP model listing
for API-key and OAuth-token auth. Probes return ``(ok, detail)`` like
``toolctx.tool_version`` — never raising from the subprocess/HTTP layer. All I/O goes through
ToolContext; this module does no subprocess or network calls itself. It also
OWNS key redaction: ToolContext never knows the key.
"""

from __future__ import annotations

import json
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


class ProbeFailed(OmcError):
    """A model probe failed. The message is ALREADY redacted — cli.main
    prints it verbatim as `error: …` (rc 1)."""


def redact(text: str, key: str) -> str:
    """Replace the key and its repr-escaped forms. A key with a control character
    (a trailing \\r from a CRLF `.env`) reaches the text escaped: http.client's
    "Invalid header value b'Bearer …\\\\r'" carries the bytes repr, not the
    key. Callers redact BEFORE truncating."""
    if not key:
        return text
    forms = {key, repr(key)[1:-1], repr(key.encode("utf-8", "backslashreplace"))[2:-1]}
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, MASK)
    return text


@dataclass(frozen=True)
class ModelInfo:
    id: str
    created_at: str


Say = Callable[[str], object]


def _base(provider: str) -> str:
    return get_provider(provider).api_base_url()


def _host(base: str) -> str:
    return base.split("/")[2]


def _models_url(base: str) -> str:
    return base.rstrip("/") + "/models"  # strip a trailing slash: never "//models"


def _headers(key: str, *, bearer: bool = False) -> dict[str, str]:
    if bearer:
        return {"Authorization": f"Bearer {key}", "anthropic-version": ANTHROPIC_VERSION}
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}


def _error_message(body: str, key: str) -> str:
    """Anthropic's error.message, else the raw body — REDACTED before the cut."""
    body = redact(body, key)
    try:
        return str(json.loads(body)["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return body.strip()[:200]


def api_connection_probe(
    ctx: ToolContext, provider: str, key: str, *, bearer: bool = False
) -> tuple[bool, str, list[ModelInfo]]:
    """GET /models proves the key and yields the API catalog's list.

    ``bearer`` sends ``key`` as an OAuth bearer token instead of ``x-api-key``.
    """
    base = _base(provider)
    host = _host(base)
    status, body = ctx.http_get(
        f"{_models_url(base)}?limit={MODELS_PAGE_LIMIT}", headers=_headers(key, bearer=bearer)
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


def _auth_status_result(ctx: ToolContext, provider: str) -> tuple[bool, str, dict[str, object]]:
    """Parse the CLI's login JSON once for `auth_status`."""
    argv = get_provider(provider).auth_status_argv()
    if not argv:
        return True, f"no login check for {provider}", {}  # codex: model probe checks login
    try:
        cp = ctx.run_bounded(argv, timeout=AUTH_STATUS_TIMEOUT)
    except FileNotFoundError:
        return False, f"{provider} CLI not found on PATH", {}
    except OSError as exc:  # run_bounded's TimeoutError is an OSError subclass
        return False, f"{provider} auth status failed: {exc}", {}
    try:
        status = json.loads(cp.stdout or "")
    except ValueError:
        status = None
    if cp.returncode != 0 or not isinstance(status, dict):
        tail = (cp.stderr or cp.stdout or "").strip()
        return False, f"{provider} auth status failed (exit {cp.returncode}): {tail[:200]}", {}
    if status.get("loggedIn") is not True:
        return False, f"{provider} is not logged in — run {provider} auth login", {}
    return True, f"{provider} is logged in", status


def auth_status(ctx: ToolContext, provider: str) -> dict[str, object]:
    """Return the CLI's parsed login state, raising its existing probe detail."""
    ok, detail, status = _auth_status_result(ctx, provider)
    if not ok:
        raise ProbeFailed(detail)
    return status


def cli_model_probe(
    ctx: ToolContext,
    provider: str,
    model: str,
    *,
    prompt: str = HEADLESS_PROBE_PROMPT,
    effort: str = "",
    return_output: bool = False,
    served_catalog: bool = False,
) -> tuple[bool, str]:
    """One headless turn on the user's login. run_bounded, not run: `claude -p`
    spawns MCP grandchildren that a plain subprocess timeout would orphan.
    ``served_catalog`` is forwarded to the provider's argv builder (the Claude
    catalog refresh turn, reconcile.py)."""
    p = get_provider(provider)
    argv = p.headless_argv(prompt, model=model, effort=effort, served_catalog=served_catalog)
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
    return True, cp.stdout.strip() if return_output else f"{shown} works"


def step(say: Say, ok: bool, detail: str) -> None:
    say(("✓ " if ok else "✗ ") + detail)
    if not ok:
        raise ProbeFailed(detail)
