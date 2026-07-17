import pytest

from .harness import configure_omc, make_work_repo, run_in

pytestmark = pytest.mark.e2e


def test_container_toolchain(container):
    for argv in (
        ["git", "--version"],
        ["wt", "--version"],
        ["omc", "version"],
        ["claude", "--version"],
        ["codex", "--version"],
        ["opencode", "--version"],
    ):
        rc, out = run_in(container, argv)
        assert rc == 0, f"{argv} failed:\n{out}"


def test_configure_and_gate(container):
    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--dry-run"])
    assert rc == 2 and "omc configure" in out  # unconfigured bail
    configure_omc(container, "claude")
    rc, out = run_in(container, ["omc", "version"])
    assert rc == 0 and "/repo" in out  # uv receipt: installed from /repo


def test_install_reroot(container):
    rc, out = run_in(container, ["bash", "-c", "cp -r /repo /repo2 && omc install /repo2"])
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "version"])
    assert rc == 0 and "/repo2" in out


def test_work_repo_and_wt(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["wt", "list", "--format=json"], cwd=repo)
    assert rc == 0, out
