"""Independent Anthropic model-list assertion for the Claude E2E scenario."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_MODELS_URL = "https://api.anthropic.com/v1/models?limit=1000"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_model_ids(key: str, *, url: str = _MODELS_URL, bearer: bool = False) -> list[str]:
    # `bearer`: a `claude setup-token` OAuth token, which /v1/models accepts as
    # `Authorization: Bearer` (live-verified HTTP 200, 2026-11-16).
    credential = {"Authorization": f"Bearer {key}"} if bearer else {"x-api-key": key}
    request = urllib.request.Request(
        url,
        headers={**credential, "anthropic-version": "2023-06-01"},
    )
    # Match ToolContext.http_get: a 3xx is an error, never a second request
    # carrying the credential to the Location target.
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Anthropic model list answered HTTP {exc.code}") from exc
    except ValueError:
        # http.client's "Invalid header value b'Bearer <token>\\r'" carries the
        # credential: replace it, and `from None` keeps it out of the traceback.
        raise ValueError("Anthropic model list rejected the credential header") from None
    with response:
        models = json.load(response)["data"]
    return [entry["id"] for entry in models]


def main(argv: list[str] | None = None) -> None:
    """argv[0] is `claude auth status`'s authMethod: api_key or oauth_token."""
    (auth_method,) = sys.argv[1:] if argv is None else argv
    if auth_method == "oauth_token":
        ids = fetch_model_ids(
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"].strip(), url=_MODELS_URL, bearer=True
        )
    elif auth_method == "api_key":
        ids = fetch_model_ids(os.environ["ANTHROPIC_API_KEY"].strip(), url=_MODELS_URL)
    else:
        raise SystemExit(f"no independent model list for authMethod {auth_method!r}")
    print(json.dumps({model_id: [] for model_id in ids}))


if __name__ == "__main__":
    main()
