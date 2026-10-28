"""Disposable Codex update homes, source repositories, and foreground Git HTTP server."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager

import pytest

from .harness import run_in

ROOT = "/tmp/omc-codex-update"
ENV = {
    "HOME": f"{ROOT}/home",
    "CODEX_HOME": f"{ROOT}/codex",
    "OMC_HOME": f"{ROOT}/omc",
    "UV_TOOL_DIR": f"{ROOT}/uv",
    "XDG_CONFIG_HOME": f"{ROOT}/xdg/config",
    "XDG_DATA_HOME": f"{ROOT}/xdg/data",
    "XDG_CACHE_HOME": f"{ROOT}/xdg/cache",
    "GIT_CONFIG_GLOBAL": f"{ROOT}/gitconfig",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def run(container, argv):
    rc, out = run_in(container, argv, env=ENV)
    assert rc == 0, f"{' '.join(argv)} failed (exit {rc}):\n{out}"
    return out


def json_run(container, argv):
    # Codex in a temporary home may print a PATH-alias warning on stderr;
    # run_in combines streams so decode the successful JSON document itself.
    out = run(container, argv)
    assert "{" in out, out
    return json.loads(out[out.index("{") :])


def create_fixtures(container):
    script = """
import shutil
from pathlib import Path
root = Path('/tmp/omc-codex-update')
for name in ('A', 'B'):
    dest = root / name
    shutil.copytree('/repo/.claude-plugin', dest / '.claude-plugin')
    shutil.copytree('/repo/.codex-plugin', dest / '.codex-plugin')
    shutil.copytree('/repo/skills', dest / 'skills')
    (dest / 'skills/start/SKILL.md').write_text(name + ' CONTENT\\n')
for name in ('home', 'codex', 'omc', 'uv'):
    (root / name).mkdir(parents=True, exist_ok=True)
"""
    run(container, ["python3", "-c", script])


def install_sentinel(container):
    script = """
import json, shutil
from pathlib import Path
root = Path('/tmp/omc-codex-update')
dest = root / 'sentinel'
shutil.copytree('/repo/.claude-plugin', dest / '.claude-plugin')
shutil.copytree('/repo/.codex-plugin', dest / '.codex-plugin')
shutil.copytree('/repo/skills', dest / 'skills')
marketplace = dest / '.claude-plugin/marketplace.json'
data = json.loads(marketplace.read_text())
data['name'] = 'sentinel'
data['plugins'][0]['name'] = 'sentinel'
marketplace.write_text(json.dumps(data))
manifest = dest / '.codex-plugin/plugin.json'
data = json.loads(manifest.read_text())
data['name'] = 'sentinel'
manifest.write_text(json.dumps(data))
(dest / 'skills/start/SKILL.md').write_text('SENTINEL CONTENT\\n')
"""
    run(container, ["python3", "-c", script])
    json_run(container, ["codex", "plugin", "marketplace", "add", f"{ROOT}/sentinel", "--json"])
    result = json_run(container, ["codex", "plugin", "add", "sentinel@sentinel", "--json"])
    assert result["pluginId"] == "sentinel@sentinel", result
    return result["installedPath"]


def update(container, source: str) -> tuple[int, str]:
    """Run checkout code and its real receipt/Codex boundaries in private homes."""
    script = """
import json, sys
from pathlib import Path
from omc import installer, gitnexus
from omc.config import store
from omc.config.schema import GlobalConfig, ProviderConfig
from omc.toolctx import ToolContext
root = Path('/tmp/omc-codex-update')
receipt = root / 'uv/omc/uv-receipt.toml'
receipt.parent.mkdir(parents=True, exist_ok=True)
key = 'git' if sys.argv[1].startswith('https://') else 'directory'
receipt.write_text(
    '[tool]\\nrequirements = [{name = "omc", '
    + key + ' = ' + json.dumps(sys.argv[1]) + '}]\\n'
)
ctx = ToolContext.from_env()
cfg = GlobalConfig()
cfg.llm.default = 'codex'
cfg.llm.providers = {'codex': ProviderConfig()}
store.save_global(ctx.home, cfg)
installer._uv = lambda ctx, *args: 0
installer.post_install = lambda ctx: installer.PostInstall(None, None)
installer.require_tools = lambda ctx, cfg: None
installer._fresh_cli = lambda ctx: None
gitnexus.update_gitnexus = lambda ctx: 0
raise SystemExit(installer.run_update(ctx))
"""
    return run_in(
        container,
        ["/root/.local/share/uv/tools/omc/bin/python", "-c", script, source],
        env=ENV,
    )


def state(container):
    registration = json_run(container, ["codex", "plugin", "marketplace", "list", "--json"])
    listing = json_run(container, ["codex", "plugin", "list", "--json"])
    return registration, listing


@contextmanager
def git_http_server(container):
    # The HTTP server stays in a foreground Docker exec owned by this test.
    # Its stop endpoint shuts down that exec before the container is torn down.
    server = f"""
import http.server
import threading
from functools import partial

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/__stop':
            self.send_response(200)
            self.end_headers()
            threading.Thread(target=self.server.shutdown).start()
            return
        super().do_GET()

httpd = http.server.HTTPServer(
    ('127.0.0.1', 8765), partial(Handler, directory={ROOT!r})
)
httpd.serve_forever()
httpd.server_close()
"""
    outcome = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            container.get_wrapped_container().exec_run(["python3", "-c", server], environment=ENV)
        ),
        daemon=True,
    )
    worker.start()
    probe = [
        "python3",
        "-c",
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/', timeout=1).read()",
    ]
    deadline = time.monotonic() + 3.0
    delay = 0.1
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail("test-owned Git HTTP server did not start on port 8765")
        rc, _ = run_in(container, probe, env=ENV, timeout=min(1.0, remaining))
        if rc == 0:
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(delay, remaining))
        delay = min(delay * 2, 0.5)
    try:
        yield
    finally:
        run_in(
            container,
            [
                "python3",
                "-c",
                "import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:8765/__stop', timeout=2).read()",
            ],
            env=ENV,
        )
        worker.join(timeout=5)
        assert not worker.is_alive(), "test-owned Git HTTP server did not stop"
        assert outcome and outcome[0].exit_code == 0, outcome


def git_snapshot(container):
    work = f"{ROOT}/A"
    origin = f"{ROOT}/origin.git"
    run(container, ["git", "init", "--bare", origin])
    run(container, ["git", "-C", work, "init", "-b", "main"])
    run(container, ["git", "-C", work, "config", "user.name", "omc tests"])
    run(container, ["git", "-C", work, "config", "user.email", "tests@omc.invalid"])
    run(container, ["git", "-C", work, "remote", "add", "origin", origin])
    run(container, ["git", "-C", work, "add", "-A"])
    run(container, ["git", "-C", work, "commit", "-m", "version A"])
    run(container, ["git", "-C", work, "push", "-u", "origin", "HEAD:main"])
    run(container, ["git", "--git-dir", origin, "symbolic-ref", "HEAD", "refs/heads/main"])
    run(container, ["git", "--git-dir", origin, "update-server-info"])
    gitconfig = f"{ROOT}/gitconfig"
    for incoming in (
        "https://github.com/omc-test/update-fixture.git",
        "https://github.com/omc-test/update-fixture",
    ):
        run(
            container,
            [
                "git",
                "config",
                "--file",
                gitconfig,
                "--add",
                "url.http://127.0.0.1:8765/origin.git.insteadOf",
                incoming,
            ],
        )
