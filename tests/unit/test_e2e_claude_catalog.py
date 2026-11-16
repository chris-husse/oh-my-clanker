"""The E2E catalog check must not send its API key through a redirect."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def test_e2e_claude_catalog_rejects_redirect_without_following_it():
    from tests.e2e.claude_catalog import fetch_model_ids

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("x-api-key")))
            if self.path == "/redirect-target":
                body = b'{"data": []}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/redirect-target"
            )
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/models?limit=1000"
        with pytest.raises(ValueError, match="HTTP 302") as caught:
            fetch_model_ids("private-key", url=url)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert "private-key" not in str(caught.value)
    assert requests == [("/v1/models?limit=1000", "private-key")]


def _serve_models(seen):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.headers.get("x-api-key"), self.headers.get("Authorization")))
            body = b'{"data": [{"id": "claude-opus-4-1"}]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_e2e_claude_catalog_sends_oauth_token_as_bearer():
    from tests.e2e.claude_catalog import fetch_model_ids

    seen = []
    server, thread = _serve_models(seen)
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/models?limit=1000"
        ids = fetch_model_ids("oauth-token", url=url, bearer=True)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert ids == ["claude-opus-4-1"]
    assert seen == [(None, "Bearer oauth-token")]


@pytest.mark.parametrize(
    ("auth_method", "env_name", "expected"),
    [
        ("api_key", "ANTHROPIC_API_KEY", ("the-key", None)),
        ("oauth_token", "CLAUDE_CODE_OAUTH_TOKEN", (None, "Bearer the-key")),
    ],
)
def test_e2e_claude_catalog_main_picks_credential_by_auth_method(
    monkeypatch, capsys, auth_method, env_name, expected
):
    from tests.e2e import claude_catalog

    seen = []
    server, thread = _serve_models(seen)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv(env_name, "the-key")
    monkeypatch.setattr(
        claude_catalog, "_MODELS_URL", f"http://127.0.0.1:{server.server_port}/v1/models"
    )
    try:
        claude_catalog.main([auth_method])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert seen == [expected]
    assert capsys.readouterr().out.strip() == '{"claude-opus-4-1": []}'


@pytest.mark.parametrize("bearer", [True, False], ids=["bearer", "x-api-key"])
def test_e2e_claude_catalog_header_validation_failure_hides_the_token(monkeypatch, bearer):
    """A CRLF `.env` leaves "\\r" on the token: http.client's ValueError carries the
    header value, so neither the message nor the printed traceback may show it.
    Validation fails before any connection is opened."""
    import socket
    import traceback

    from tests.e2e.claude_catalog import fetch_model_ids

    def no_network(*_a, **_k):
        pytest.fail("header validation must fail before any connection")

    secret = "sk-ant-oat01-" + "SECRET" * 2  # built at runtime: never in a source line
    monkeypatch.setattr(socket, "create_connection", no_network)
    with pytest.raises(ValueError, match="Anthropic model list") as caught:
        fetch_model_ids(secret + "\r", bearer=bearer)
    printed = "".join(traceback.format_exception(caught.value))
    assert "SECRETSECRET" not in printed


@pytest.mark.parametrize(
    ("auth_method", "env_name", "expected"),
    [
        ("api_key", "ANTHROPIC_API_KEY", ("the-key", None)),
        ("oauth_token", "CLAUDE_CODE_OAUTH_TOKEN", (None, "Bearer the-key")),
    ],
)
def test_e2e_claude_catalog_main_strips_crlf_from_the_env_credential(
    monkeypatch, auth_method, env_name, expected
):
    from tests.e2e import claude_catalog

    seen = []
    server, thread = _serve_models(seen)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv(env_name, "the-key\r\n")
    monkeypatch.setattr(
        claude_catalog, "_MODELS_URL", f"http://127.0.0.1:{server.server_port}/v1/models"
    )
    try:
        claude_catalog.main([auth_method])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert seen == [expected]
