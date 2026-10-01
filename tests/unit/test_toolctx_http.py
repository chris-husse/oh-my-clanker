"""ToolContext.http_get — the ONLY network call in omc (spec 2026-10-01 §3.2).
A real in-process loopback HTTP server — deliberately the ONLY socket in
tests/unit (no precedent before this file): this is the one place the transport
itself is under test; everything above it fakes http_get. Hermetic: 127.0.0.1,
no DNS, no external network."""

import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omc.toolctx import ToolContext

_SEEN: list[str] = []  # every path the loopback server was asked for


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        _SEEN.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/v1/models/m1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/v1/models?"):
            body = json.dumps({"data": [{"id": "m1"}], "has_more": False}).encode()
            code = 200 if self.headers.get("x-api-key") == "good" else 401
        elif self.path == "/v1/models/m1":
            body, code = b'{"id":"m1"}', 200
        else:
            body, code = b'{"error":{"message":"nope"}}', 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the test log
        pass


@pytest.fixture
def server():
    _SEEN.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def raw_server():
    """Factory: serve ``reply`` verbatim (any bytes, valid HTTP or not) to every
    connection, then close it. For malformed/truncated responses http.server
    cannot be made to produce."""
    servers = []

    def start(reply: bytes) -> str:
        class _Raw(socketserver.StreamRequestHandler):
            def handle(self):
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass  # drain the request head
                self.wfile.write(reply)

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Raw)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _ctx(**env):
    return ToolContext(home=None, env={"HOME": "/nonexistent", **env})  # type: ignore[arg-type]


def test_http_get_returns_status_and_body_for_2xx_and_4xx(server):
    ctx = _ctx()
    status, body = ctx.http_get(f"{server}/v1/models?limit=1000", headers={"x-api-key": "good"})
    assert status == 200 and json.loads(body)["data"][0]["id"] == "m1"
    status, body = ctx.http_get(f"{server}/v1/models?limit=1000", headers={"x-api-key": "bad"})
    assert status == 401  # HTTPError is a RESULT here, never an exception
    status, body = ctx.http_get(f"{server}/v1/models/zzz")
    assert status == 404 and json.loads(body)["error"]["message"] == "nope"


def test_http_get_transport_failure_is_status_zero():
    status, body = _ctx().http_get("http://127.0.0.1:9/v1/models", timeout=2)
    assert status == 0 and body  # connection refused: text, not a traceback


def test_http_get_takes_proxies_from_ctx_env_not_os_environ(server, monkeypatch):
    # urllib's BYPASS check (no_proxy) still reads os.environ / system settings;
    # clear every proxy var so the host cannot make 127.0.0.1 bypass the proxy.
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    # A dead proxy in ctx.env must be honoured (→ transport failure) ...
    status, _ = _ctx(http_proxy="http://127.0.0.1:9").http_get(f"{server}/v1/models/m1", timeout=2)
    assert status == 0
    # ... and a dead proxy ONLY in os.environ must be ignored.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    status, _ = _ctx().http_get(f"{server}/v1/models/m1", timeout=2)
    assert status == 200


def test_http_get_non_http_reply_is_status_zero(raw_server):
    # http.client.BadStatusLine is not an OSError and urllib does not wrap it.
    status, body = _ctx().http_get(raw_server(b"GARBAGE\r\n\r\n"), timeout=2)
    assert status == 0 and body


@pytest.mark.parametrize("code", ["200 OK", "500 Internal Server Error"])
def test_http_get_truncated_body_is_status_zero(raw_server, code):
    # Content-Length promises 100 bytes, 5 arrive: IncompleteRead on resp.read()
    # (2xx path) and on exc.read() inside the HTTPError handler (4xx/5xx path).
    reply = f"HTTP/1.1 {code}\r\nContent-Length: 100\r\n\r\nhello".encode()
    status, body = _ctx().http_get(raw_server(reply), timeout=2)
    assert status == 0 and body


def test_http_get_malformed_url_is_status_zero():
    status, body = _ctx().http_get("not-a-url")  # Request() itself raises ValueError
    assert status == 0 and body


def test_http_get_never_follows_a_redirect(server):
    # urllib's default redirect handler would replay every header (x-api-key!) to
    # the Location target; a 3xx must be a RESULT, and no second request happens.
    status, _ = _ctx().http_get(f"{server}/redirect", headers={"x-api-key": "good"})
    assert status == 302
    assert _SEEN == ["/redirect"]
