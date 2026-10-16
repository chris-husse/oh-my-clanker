"""Independent Anthropic model-list assertion for the Claude E2E scenario."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_MODELS_URL = "https://api.anthropic.com/v1/models?limit=1000"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_model_ids(key: str, *, url: str = _MODELS_URL) -> list[str]:
    request = urllib.request.Request(
        url,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    # Match ToolContext.http_get: a 3xx is an error, never a second request
    # carrying x-api-key to the Location target.
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            models = json.load(response)["data"]
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Anthropic model list answered HTTP {exc.code}") from exc
    return [entry["id"] for entry in models]


def main() -> None:
    ids = fetch_model_ids(os.environ["ANTHROPIC_API_KEY"])
    print(json.dumps({model_id: [] for model_id in ids}))


if __name__ == "__main__":
    main()
