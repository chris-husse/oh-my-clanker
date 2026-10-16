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
