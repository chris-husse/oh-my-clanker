"""herdr sidebar, live (spec 2026-11-14 §5): a real headless herdr server, real
fish/zsh/bash panes, the installed omc's launch path and hook command.

Every assertion reads herdr's own state (`pane get` cwd, `workspace get`
label) — never the shell's output. The launch tests enter at
`Shell.exec_interactive` with `true` where the provider would be; the
entry points' forwarding of `origin_cwd` is unit-tested. The hook test runs
`omc internal herdr-cwd` under `setsid` (no controlling terminal, like a
process Claude spawns). The token-gated test proves the installed plugin's
SessionStart hook fires inside a real Claude run.
"""

from __future__ import annotations

import json
import os
import shlex
import time

import pytest

from .harness import _e2e_model_id, require_token, run_in

pytestmark = pytest.mark.e2e

SESSION = "e2e"
LAUNCH = "/work/launch"
WORKTREE = "/work/wt dir"

# Runs under the installed omc's interpreter, in the pane's shell: the same
# exec handoff `omc start` makes, minus the provider (`true` stands there).
_LAUNCH_SCRIPT = """\
import os
import sys

from omc.shells.bash import BashShell
from omc.shells.fish import FishShell
from omc.shells.zsh import ZshShell

shell = {"fish": FishShell, "zsh": ZshShell, "bash": BashShell}[sys.argv[1]]()
shell.exec_interactive(
    cwd=sys.argv[2],
    title="e2e",
    startup_argv=["true"],
    title_seq="",
    origin_cwd=os.environ["PWD"],
)
"""


def _q(path: str) -> str:
    return shlex.quote(path)


def _write(container, path: str, text: str) -> None:
    rc, out = run_in(container, ["bash", "-c", f"printf %s {_q(text)} > {_q(path)}"])
    assert rc == 0, out


def _herdr(container, *args) -> dict:
    rc, out = run_in(container, ["herdr", "--session", SESSION, *args], stdout_only=True)
    assert rc == 0, f"herdr {' '.join(args)} failed (rc {rc}):\n{out}"
    return json.loads(out)["result"]


def _start(container, launch: str) -> tuple[str, str]:
    """A private headless herdr server and one workspace whose root pane's
    shell (bash) starts in `launch`. Returns (workspace_id, pane_id)."""
    rc, out = run_in(
        container, ["bash", "-c", f"mkdir -p {_q(launch)} && git init -q {_q(launch)}"]
    )
    assert rc == 0, out
    container.get_wrapped_container().exec_run(
        ["herdr", "--session", SESSION, "server"],
        detach=True,
        environment={"SHELL": "/bin/bash"},
    )
    deadline = time.monotonic() + 15
    while True:
        rc, out = run_in(container, ["herdr", "--session", SESSION, "workspace", "list"])
        if rc == 0:
            break
        if time.monotonic() > deadline:
            pytest.fail(
                "the image's herdr headless server did not answer `workspace list` "
                f"within 15 s — rebuild the E2E image (docker/Dockerfile.e2e herdr layer):\n{out}"
            )
        time.sleep(0.5)
    result = _herdr(container, "workspace", "create", "--cwd", launch, "--no-focus")
    return result["workspace"]["workspace_id"], result["root_pane"]["pane_id"]


def _type(container, pane: str, command: str) -> None:
    # `pane run` prints nothing on success (herdr 0.9.5): rc only.
    rc, out = run_in(container, ["herdr", "--session", SESSION, "pane", "run", pane, command])
    assert rc == 0, f"herdr pane run {command!r} failed (rc {rc}):\n{out}"


def _expect(container, workspace: str, pane: str, path: str) -> None:
    """Poll until herdr shows `path` as the pane's cwd and its folder name as
    the workspace label."""
    want_label = os.path.basename(path)
    deadline = time.monotonic() + 15
    while True:
        cwd = _herdr(container, "pane", "get", pane)["pane"].get("cwd")
        label = _herdr(container, "workspace", "get", workspace)["workspace"].get("label")
        if cwd == path and label == want_label:
            return
        if time.monotonic() > deadline:
            _, screen = run_in(container, ["herdr", "--session", SESSION, "pane", "read", pane])
            raise AssertionError(
                f"herdr never showed {path!r}: pane cwd={cwd!r}, workspace label={label!r} "
                f"(want {want_label!r})\npane screen:\n{screen}"
            )
        time.sleep(0.5)


def _mkdir(container, *paths: str) -> None:
    rc, out = run_in(container, ["mkdir", "-p", *paths])
    assert rc == 0, out


@pytest.mark.parametrize("shell", ["fish", "zsh", "bash"])
def test_launch_block_moves_sidebar_and_exit_restores_it(container, shell):
    workspace, pane = _start(container, LAUNCH)
    _mkdir(container, WORKTREE, "/work/other")
    _write(container, "/tmp/omc_launch.py", _LAUNCH_SCRIPT)

    _type(
        container,
        pane,
        f'"$(uv tool dir)/omc/bin/python" /tmp/omc_launch.py {shell} {_q(WORKTREE)}',
    )
    _expect(container, workspace, pane, WORKTREE)

    if shell == "fish":  # only fish follows `cd` inside the launched shell (D4)
        _type(container, pane, "cd /work/other")
        _expect(container, workspace, pane, "/work/other")

    _type(container, pane, "exit")
    _expect(container, workspace, pane, LAUNCH)


def test_hook_command_without_terminal_reports_session_cwd(container):
    workspace, pane = _start(container, LAUNCH)
    hook_dir = "/work/hook dir"
    _mkdir(container, hook_dir)
    _write(
        container,
        "/tmp/hook.json",
        json.dumps({"cwd": hook_dir, "hook_event_name": "SessionStart"}),
    )

    # `-w` keeps the setsid parent — still on the pane's terminal — alive, so
    # the ancestor walk from the terminal-less child finds it.
    _type(container, pane, "setsid -w omc internal herdr-cwd < /tmp/hook.json")
    _expect(container, workspace, pane, hook_dir)


def test_claude_session_start_hook_reports_session_cwd(container):
    require_token("claude")
    model = _e2e_model_id(container, "claude")
    workspace, pane = _start(container, LAUNCH)
    claude_dir = "/work/claude dir"
    _mkdir(container, claude_dir)

    _type(
        container,
        pane,
        f"env -C {_q(claude_dir)} claude -p 'Reply with OK' --model {_q(model)} "
        "--output-format text >/tmp/claude.out 2>&1; echo $? >/tmp/claude.done",
    )
    deadline = time.monotonic() + 180
    while run_in(container, ["test", "-s", "/tmp/claude.done"])[0] != 0:
        if time.monotonic() > deadline:
            _, out = run_in(container, ["cat", "/tmp/claude.out"])
            raise AssertionError(f"claude -p did not finish within 180 s:\n{out}")
        time.sleep(2)
    # run_in fails the test with the remediation if the output shows an auth failure.
    _, status = run_in(container, ["cat", "/tmp/claude.done"])
    _, out = run_in(container, ["cat", "/tmp/claude.out"])
    assert status.strip() == "0", f"claude -p exited {status.strip()}:\n{out}"
    # The pane's own shell never left LAUNCH: only the plugin hook can move it.
    _expect(container, workspace, pane, claude_dir)
