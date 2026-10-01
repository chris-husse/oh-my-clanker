"""The wheel, installed into disposable uv directories, provisions and runs the fish hook.

This is the ONE sanctioned `uv tool install` outside a user decision (.omc/config/AGENTS.md):
every install location is asserted to sit under pytest's tmp BEFORE uv runs.
"""

import json
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from ._fishpty import drive_fish

ROOT = Path(__file__).resolve().parents[2]
SESSION = "w0t1p0:38B11221-B7E1-4F36-8A3B-50D549172632"
STUB_OMC = """#!/bin/sh
printf '%s\\n' "$*" >> "$OMC_TEST_LOG"
if [ "$1" = title ] && [ "$2" = apply ] && [ -r "$3" ]; then
  IFS= read -r line < "$3"
  printf 'request: %s\\n' "$line" >> "$OMC_TEST_LOG"
fi
exit 0
"""


def _tool(name, hint):
    found = shutil.which(name)
    if found is None:
        pytest.fail(f"the installed-wheel test requires {name}: {hint}")
    return found


def _host_uv_dir(uv, verb):
    """Resolve a host uv directory BEFORE HOME/XDG_* are redirected (spec §7)."""
    cp = subprocess.run([uv, *verb], capture_output=True, text=True, timeout=30)
    assert cp.returncode == 0 and cp.stdout.strip(), (
        f"uv {' '.join(verb)} failed:\n{cp.stderr[-500:]}"
    )
    return cp.stdout.strip()


def _base_env():
    """Process essentials plus the host's uv cache and managed interpreters; no credentials.

    HOME and XDG_CACHE_HOME/XDG_DATA_HOME are redirected by `installed`, which would move
    uv's cache and managed-Python directories into tmp and re-download every wheel per
    session; pin both to the host's locations explicitly.
    """
    uv = _tool("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")
    keep = ("PATH", "TMPDIR", "LANG", "TERM", "USER", "LOGNAME", "UV_PYTHON")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR") or _host_uv_dir(uv, ["cache", "dir"])
    env["UV_PYTHON_INSTALL_DIR"] = os.environ.get("UV_PYTHON_INSTALL_DIR") or _host_uv_dir(
        uv, ["python", "dir"]
    )
    env["UV_NO_PROGRESS"] = "1"
    return env


def _run(argv, env, timeout=300):
    cp = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    assert cp.returncode == 0, f"{argv[:4]!r} failed:\n{cp.stderr[-2000:]}"
    return cp


@pytest.fixture(scope="session")
def wheel(tmp_path_factory):
    uv = _tool("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")
    out = tmp_path_factory.mktemp("omc-wheel")
    _run([uv, "build", "--wheel", "--out-dir", str(out), str(ROOT)], _base_env())
    wheels = list(out.glob("omc-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


_INSTALLS: list[Path] = []  # every `installed` setup, so its scope is pinned by a test


@dataclass(frozen=True)
class Installed:
    env: dict[str, str]
    omc: Path
    hook: Path


@pytest.fixture(scope="module")
def installed(tmp_path_factory, wheel):
    tmp_path = tmp_path_factory.mktemp("installed")
    uv = _tool("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")
    _tool("fish", "brew install fish (macOS) or sudo apt-get install fish (Ubuntu)")
    private = {
        "HOME": tmp_path / "home",
        "XDG_CONFIG_HOME": tmp_path / "config with $ and spaces",
        "XDG_DATA_HOME": tmp_path / "xdg-data",
        "XDG_CACHE_HOME": tmp_path / "xdg-cache",
        "XDG_STATE_HOME": tmp_path / "xdg-state",
        "OMC_HOME": tmp_path / "omc-home",
        "UV_TOOL_DIR": tmp_path / "uv-tools",
        "UV_TOOL_BIN_DIR": tmp_path / "uv-bin",
    }
    for name, path in private.items():
        assert path.is_relative_to(tmp_path), f"refusing a non-disposable {name}: {path}"
        path.mkdir(parents=True, exist_ok=True)
    env = {**_base_env(), **{name: str(path) for name, path in private.items()}}
    _run([uv, "tool", "install", "--reinstall", str(wheel)], env)
    omc = private["UV_TOOL_BIN_DIR"] / "omc"
    assert omc.is_file(), f"uv did not link {omc}"
    _run([str(omc), "shell-integration", "fish", "reconcile"], env)
    hook = private["XDG_CONFIG_HOME"] / "fish" / "conf.d" / "omc-title.fish"
    assert hook.is_file(), f"the installed CLI did not provision {hook}"
    _INSTALLS.append(omc)
    return Installed(env, omc, hook)


def test_wheel_ships_the_hook_next_to_the_skills(wheel):
    names = zipfile.ZipFile(wheel).namelist()
    assert "omc/assets/omc-title.fish" in names
    assert any(name.startswith("omc/assets/skills/") for name in names)


def test_installed_status_is_owned_and_enabled(installed):
    cp = _run([str(installed.omc), "shell-integration", "fish", "status"], installed.env)
    status = json.loads(cp.stdout)
    assert status["owned"] is True and status["enabled"] is True
    assert status["path"] == str(installed.hook)
    assert installed.hook.read_bytes().startswith(b"# omc-managed fish title integration")


def test_ordinary_fish_prompt_loads_the_installed_hook(installed, tmp_path):
    fish = _tool("fish", "brew install fish")
    git = _tool("git", "install git")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([git, "init", "-q", "-b", "feature/wheel", str(repo)], check=True)
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "t@example.com"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run([git, "-C", str(repo), "config", key, value], check=True)
    (repo / "f").write_text("x\n")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-qm", "init"], check=True)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "omc").write_text(STUB_OMC)
    (stubs / "omc").chmod(0o755)
    (stubs / "git").symlink_to(git)
    config = Path(installed.env["XDG_CONFIG_HOME"]) / "fish" / "config.fish"
    config.write_text(
        "set -g fish_greeting\n"
        'function fish_prompt; echo P >> "$OMC_PROMPT_LOG"; printf "READY> "; end\n'
        # The disowned helper dies with the PTY: stay at the prompt until it logged. Builtins
        # only (PATH holds just the stubs), plus the absolute /bin/sleep.
        "function wait_for_request\n"
        "    set -l tries 0\n"
        "    while test $tries -lt 200\n"
        '        test -e "$OMC_TEST_LOG"; and string match -q "request: *" <"$OMC_TEST_LOG"\n'
        "        and return 0\n"
        "        set tries (math $tries + 1)\n"
        "        /bin/sleep 0.05\n"
        "    end\n"
        "end\n"
    )
    original = config.read_bytes()
    log = tmp_path / "omc.log"
    prompt_log = tmp_path / "prompts"
    env = {
        **installed.env,
        "PATH": str(stubs),  # the installed omc is NOT here: the hook must resolve the stub
        "OMC_TEST_LOG": str(log),
        "OMC_PROMPT_LOG": str(prompt_log),
        "ITERM_SESSION_ID": SESSION,
        "TERM_PROGRAM": "iTerm.app",
        "TERM": "dumb",
    }
    drive_fish(fish, env, ["wait_for_request"], prompt_log, cwd=repo)
    lines = log.read_text().splitlines()
    applies = [line for line in lines if line.startswith("title apply ")]
    assert len(applies) == 1, lines  # the first prompt dispatched exactly once
    assert applies[0].split(" ", 2)[2].startswith(installed.env["OMC_HOME"] + "/title-request/")
    assert "request: set feature/wheel" in lines
    assert config.read_bytes() == original


def test_the_wheel_is_installed_once_per_module(installed):
    # `just check` must pay for ONE `uv tool install`, shared by every test above.
    assert _INSTALLS == [installed.omc]
