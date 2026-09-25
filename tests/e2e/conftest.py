from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from pathlib import Path

import pytest

from .codex_auth import codex_account, selected_volume
from .harness import ALL_TOKEN_VARS

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


def _forward_tokens(c, *, use_codex_account=False):
    """Forward whatever provider credentials the host has into the container.
    Absent vars are simply not forwarded — require_token does the gating."""
    for var in ALL_TOKEN_VARS:
        if var == "OPENAI_API_KEY" and os.environ.get("CODEX_AUTH_VOLUME"):
            if use_codex_account:
                selected_volume()
            continue
        if os.environ.get(var):
            c = c.with_env(var, os.environ[var])
    return c


def _finish_container_setup(c):
    """Provider setup waits until a test explicitly selects a provider."""


@pytest.fixture
def e2e_provider(request):
    """Account prerequisites follow the test's declared provider intent."""
    params = getattr(getattr(request.node, "callspec", None), "params", {})
    if "provider" in params:
        return params["provider"]
    marker = request.node.get_closest_marker("e2e_provider")
    return marker.args[0] if marker else None


@pytest.fixture(scope="session")
def e2e_image():
    cfg_dir = _isolate_docker_config()
    from testcontainers.core.image import DockerImage

    try:
        # Local development can reuse a known image while editing the test-only
        # conversation driver. The default always rebuilds from this checkout.
        if prebuilt := os.environ.get("OMC_E2E_PREBUILT_IMAGE"):
            import docker

            if not os.environ.get("OMC_E2E_PREBUILT_SOURCE"):
                pytest.fail("OMC_E2E_PREBUILT_SOURCE must name the prebuilt image's source commit")
            try:
                docker.from_env().images.get(prebuilt)
            except docker.errors.ImageNotFound:
                pytest.fail(
                    f"OMC_E2E_PREBUILT_IMAGE={prebuilt} is absent; build it or unset the override"
                )
            yield prebuilt
            return
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
def container(e2e_image, e2e_provider):
    from testcontainers.core.container import DockerContainer

    use_codex_account = e2e_provider == "codex"
    c = _forward_tokens(
        DockerContainer(e2e_image).with_command("sleep infinity"),
        use_codex_account=use_codex_account,
    )
    with codex_account(c, _finish_container_setup, use_account=use_codex_account):
        yield c


@pytest.fixture
def container_with_artifacts(e2e_image, e2e_provider):
    """A container with tests/e2e/artifacts mounted rw at /artifacts — the
    permanent-E2E-artifact channel (committed wiki docs sync back out)."""
    from testcontainers.core.container import DockerContainer

    artifacts = REPO_ROOT / "tests" / "e2e" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    use_codex_account = e2e_provider == "codex"
    c = _forward_tokens(
        DockerContainer(e2e_image)
        .with_command("sleep infinity")
        .with_volume_mapping(str(artifacts), "/artifacts", "rw"),
        use_codex_account=use_codex_account,
    )
    with codex_account(c, _finish_container_setup, use_account=use_codex_account):
        yield c
