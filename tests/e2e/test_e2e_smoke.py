import json
import re

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
    # version line shape: provenance optional (a worktree build context has a .git
    # POINTER file whose target doesn't exist in-container -> probes fail soft)
    assert re.search(r"omc \S+ (\(\S+@\S+\) )?from /repo", out)


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


def test_codex_plugin_is_registered_in_the_image(container):
    """`codex plugin add` actually installs — registration alone leaves the
    plugin 'not installed' and serving no skills. Plugin commands need no
    auth, so this runs in CI unlike the seeded-session tests."""
    rc, out = run_in(container, ["codex", "plugin", "list", "--json"])
    assert rc == 0, f"codex plugin list failed:\n{out}"
    # run_in hands back stdout and stderr COMBINED (exec_run without demux, run
    # under `bash -lc`), so any stray stderr line — a login-shell profile echo, a
    # node deprecation warning, a codex update notice — makes a whole-string
    # json.loads() raise while rc is still 0: a red test that says nothing about
    # plugin state. Slice the object out of the stream instead. Do NOT
    # "simplify" this back to json.loads(out).
    start, end = out.find("{"), out.rfind("}")
    assert start != -1 and end > start, f"no JSON object in codex plugin list output:\n{out}"
    installed = json.loads(out[start : end + 1])["installed"]
    names = {e["name"] for e in installed}
    assert "omc" in names, out
    omc = next(e for e in installed if e["name"] == "omc")
    assert omc["installed"] is True and omc["enabled"] is True, omc
