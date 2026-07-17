from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from pathlib import Path

import pytest

from .harness import TOKEN_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]


# docker-py's images.build() unconditionally resolves registry auth headers by
# shelling out to the configured credential store for EVERY entry `docker-credential-*
# list` returns (docker/api/build.py: _set_auth_headers, called even though our
# Dockerfile only pulls public images). Observed on this host: several
# Docker-Desktop-internal keychain entries (OAuth-shaped "access-token"/"refresh-token"
# items) hang `docker-credential-osxkeychain get` indefinitely when queried outside
# Docker Desktop's own process — hard-hanging the build with no timeout, no error.
# `docker build`/buildx doesn't hit this (different auth path), only docker-py's
# classic-builder HTTP API does. Fix: point docker-py at an empty, credstore-free
# config for this test session so it never shells out for creds it doesn't need.
# Respects an operator-supplied DOCKER_CONFIG (e.g. CI with real private-registry
# needs) by only filling in when unset.
def _isolate_docker_config() -> str | None:
    if os.environ.get("DOCKER_CONFIG"):
        return None
    cfg_dir = Path(tempfile.mkdtemp(prefix="omc-e2e-dockercfg-"))
    (cfg_dir / "config.json").write_text(json.dumps({"auths": {}}))
    os.environ["DOCKER_CONFIG"] = str(cfg_dir)
    return str(cfg_dir)


# docker-py's images.build() also goes through the classic (non-BuildKit) Engine
# API, which — unlike `docker build`/buildx — does NOT auto-populate the implicit
# TARGETARCH build arg. Dockerfile.e2e's worktrunk-download stage needs it (`case
# "${TARGETARCH}" in amd64|arm64|*) ... exit 1`); supply it explicitly so the build
# doesn't fall through to the "unsupported TARGETARCH" branch.
_DOCKER_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def _target_arch() -> str:
    machine = platform.machine()
    return _DOCKER_ARCH.get(machine, machine)


@pytest.fixture(scope="session")
def e2e_image():
    cfg_dir = _isolate_docker_config()
    from testcontainers.core.image import DockerImage

    try:
        with DockerImage(
            path=str(REPO_ROOT),
            dockerfile_path="docker/Dockerfile.e2e",
            tag="omc-e2e:test",
            buildargs={"TARGETARCH": _target_arch()},
        ) as image:
            yield str(image)
    finally:
        if cfg_dir is not None:
            shutil.rmtree(cfg_dir, ignore_errors=True)


@pytest.fixture
def container(e2e_image):
    from testcontainers.core.container import DockerContainer

    c = DockerContainer(e2e_image).with_command("sleep infinity")
    for var in TOKEN_ENV.values():
        if os.environ.get(var):
            c = c.with_env(var, os.environ[var])
    try:
        c.start()
        # finish plugin registration (needs network; baked layer may have been offline)
        c.get_wrapped_container().exec_run(["bash", "/repo/docker/setup-plugins.sh"])
        # codex >=0.144 doesn't use a bare OPENAI_API_KEY env — it needs an explicit
        # stdin login that writes ~/.codex/auth.json (real users run this themselves).
        if os.environ.get("OPENAI_API_KEY"):
            c.get_wrapped_container().exec_run(
                ["bash", "-c", "printenv OPENAI_API_KEY | codex login --with-api-key"]
            )
        yield c
    finally:
        c.stop()
