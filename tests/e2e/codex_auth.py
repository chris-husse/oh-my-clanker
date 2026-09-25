"""Isolated Codex account auth for Docker E2E containers."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

CODEX_HOME = "/tmp/omc-codex-home"
AUTH_MOUNT = "/codex-auth"
DEFAULT_VOLUME = "omc-e2e-codex-auth"

# The volume contains only Codex's account cache. Never mount the host home or
# expose this mount under /artifacts, which is copied back into the repo.
_COPY_IN = """\
import json, os, shutil, sys
src = '/codex-auth/auth.json'
dst = '/tmp/omc-codex-home/auth.json'
if not os.path.isfile(src): sys.exit(21)
try:
    with open(src) as f: data = json.load(f)
    if not isinstance(data, dict): raise ValueError('auth must be an object')
except (OSError, ValueError): sys.exit(22)
os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
shutil.copyfile(src, dst)
os.chmod(dst, 0o600)
"""

_COPY_BACK = """\
import json, os, shutil, sys, tempfile
src = '/tmp/omc-codex-home/auth.json'
dst = '/codex-auth/auth.json'
try:
    with open(src) as f: data = json.load(f)
    if not isinstance(data, dict): raise ValueError('auth must be an object')
except (OSError, ValueError): sys.exit(22)
fd, tmp = tempfile.mkstemp(prefix='.auth-', dir='/codex-auth')
try:
    with os.fdopen(fd, 'wb') as out, open(src, 'rb') as inp: shutil.copyfileobj(inp, out)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
"""


def selected_volume() -> str | None:
    value = os.environ.get("CODEX_AUTH_VOLUME")
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        pytest.fail("CODEX_AUTH_VOLUME must be a Docker named volume, not a path")
    return value


def _exec(container, argv, **kwargs):
    return container.get_wrapped_container().exec_run(argv, **kwargs)


def _copy_in(container) -> None:
    result = _exec(container, ["python3", "-c", _COPY_IN])
    if result.exit_code == 21:
        pytest.fail("Codex account auth is missing in CODEX_AUTH_VOLUME; run `just codex-login`.")
    if result.exit_code != 0:
        pytest.fail("Codex account auth.json is corrupt or unreadable; run `just codex-login`.")


def _copy_back(container) -> None:
    result = _exec(container, ["python3", "-c", _COPY_BACK])
    if result.exit_code != 0:
        pytest.fail("Could not persist refreshed Codex account auth.json to CODEX_AUTH_VOLUME.")


def _status(container) -> None:
    result = _exec(container, ["codex", "login", "status"])
    if result.exit_code != 0:
        pytest.fail("Codex account credentials are unusable; run `just codex-login`.")


@contextmanager
def codex_account(container, setup, *, use_account=True):
    """Start/stop a container; serialize account auth through final copy-back.

    A generic smoke container with no account selection follows the ordinary
    setup path. In account mode, lock before the Docker mount and retain it until
    the refreshed JSON has been safely written back and the container stopped.
    """
    volume = selected_volume() if use_account else None
    if not volume:
        try:
            container.start()
            setup(container)
            yield container
        finally:
            container.stop()
        return

    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.fail("Codex account E2E cannot run under pytest-xdist parallel workers.")
    lock_dir = Path(os.environ.get("OMC_E2E_AUTH_LOCK_DIR", tempfile.gettempdir()))
    lock_path = lock_dir / f"omc-codex-auth-{volume}.lock"
    with lock_path.open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pytest.fail(f"Codex account volume {volume} is already in use by another E2E test.")
        container.with_volume_mapping(volume, AUTH_MOUNT, "rw").with_env("CODEX_HOME", CODEX_HOME)
        started = False
        copied = False
        try:
            container.start()
            started = True
            _copy_in(container)
            copied = True
            setup(container)
            _status(container)
            yield container
        finally:
            try:
                if copied:
                    _copy_back(container)
            finally:
                if started:
                    container.stop()


def require_codex_ready(container) -> dict:
    """Fail on incomplete Codex setup and return nonsecret CLI/plugin metadata."""
    version_result = _exec(container, ["codex", "--version"], demux=True)
    if version_result.exit_code != 0:
        pytest.fail("Codex CLI is unavailable in the E2E container.")
    version = (version_result.output[0] or b"").decode(errors="replace").strip()
    _status(container)
    # Codex 0.156.1 warns on stderr when CODEX_HOME is under /tmp. Docker's
    # default merged output would put that warning before the JSON on stdout.
    result = _exec(container, ["codex", "plugin", "list", "--json"], demux=True)
    if result.exit_code != 0:
        pytest.fail("Could not inspect Codex plugins in the E2E container.")
    try:
        plugin_stdout = result.output[0] or b""
        listing = json.loads(plugin_stdout)
        plugins = {p["pluginId"]: p for p in listing["installed"]}
    except (ValueError, TypeError, KeyError):
        pytest.fail("Codex plugin list --json returned invalid metadata.")
    required = ("omc@oh-my-clanker", "superpowers@superpowers-marketplace")
    for plugin_id in required:
        plugin = plugins.get(plugin_id)
        if not plugin or not plugin.get("installed") or not plugin.get("enabled"):
            pytest.fail(f"Codex plugin {plugin_id} is not installed and enabled.")
    skills = _exec(
        container,
        ["python3", "-m", "tests.e2e.codex_plugin_payload", "verify"],
        environment={"PYTHONPATH": "/repo", "OMC_E2E_CODEX_PLUGIN_LIST": plugin_stdout.decode()},
    )
    if skills.exit_code != 0:
        detail = skills.output.decode(errors="replace")[-1000:].strip()
        pytest.fail(f"Codex installed skill files are missing or stale: {detail}")
    return {
        "codex_version": version,
        "plugins": {
            key: {"version": plugins[key].get("version"), "enabled": True} for key in required
        },
    }
