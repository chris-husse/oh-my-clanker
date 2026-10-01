"""A private iTerm2 instance for the native tier (spec §7, native steps 1–6).

Everything here is test code: it may call subprocess directly. The instance is a
COPY of /Applications/iTerm.app launched under a random `-suite` with a private,
short HOME so the SDK socket fits sockaddr_un. Nothing in this module imports
`iterm2` at module level — Linux CI collects tests/local (deselected by marker).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_APP = Path("/Applications/iTerm.app")
SOCKADDR_UN_MAX = 103


class ProbeError(RuntimeError):
    """A precondition of the private instance did not hold; the message is the setup hint."""


@dataclass(frozen=True)
class Identity:
    checkout: Path
    suite: str
    root: Path
    copy: Path
    home: Path
    socket: Path


def new_identity(checkout: Path, token: str) -> Identity:
    suite = f"omc-{token}"
    root = Path("/private/tmp") / suite
    home = root / "home"
    socket = home / "Library" / "Application Support" / suite / "private" / "socket"
    if len(str(socket).encode()) > SOCKADDR_UN_MAX:
        raise ProbeError(f"socket path {socket} exceeds the sockaddr_un limit; shorten the token")
    return Identity(checkout, suite, root, root / "iTerm.app", home, socket)


PROFILE_GUID = "omc-private-fish"


def launch_env() -> dict[str, str]:
    """`open` hands ITS environment to the app (and `just` loads .env into the runner's), so
    it runs with nothing from the runner; only the explicit --env list reaches the app."""
    return {"PATH": "/usr/bin:/bin"}


def _plist_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def private_profiles(shell: str) -> str:
    """The only (default) profile, old-style plist for the argument domain: a custom command,
    never the login shell (login(1) would reset HOME to the real one)."""
    entries = {
        "Guid": PROFILE_GUID,
        "Name": "omc private fish",
        "Custom Command": "Yes",
        "Command": shlex.join([shell, "-i"]),
        "Run Command In Login Shell": "No",
    }
    body = "".join(f"{_plist_string(k)} = {_plist_string(v)}; " for k, v in entries.items())
    return "({" + body.rstrip() + "})"


def open_argv(identity: Identity, shell: str) -> list[str]:
    env = {
        "HOME": str(identity.home),
        # Foundation's NSHomeDirectory (hence Application Support, where the API socket
        # lives) reads CFFIXED_USER_HOME, then the passwd entry, never $HOME.
        "CFFIXED_USER_HOME": str(identity.home),
        "XDG_CONFIG_HOME": str(identity.home / ".config"),
        "OMC_HOME": str(identity.home / ".omc"),
        "PATH": f"{identity.checkout / '.venv' / 'bin'}:/usr/bin:/bin",
        "IT2_SUITE": identity.suite,
        "IT2_APP_PATH": str(identity.copy),
    }
    argv = ["/usr/bin/open", "-n", "-g", "-j", "-a", str(identity.copy)]
    for key, value in env.items():
        argv += ["--env", f"{key}={value}"]
    # Argument-domain preferences (NSUserDefaults keys are case-sensitive; advanced settings
    # are camelCase in iTerm2 3.7.3): no windows, no daemonized iTermServer (it would outlive
    # the app), no Sparkle prompts, no quit confirmation. The copy deliberately runs from
    # /private/tmp, so LetsMove's modal "Move to Applications?" alert (raised in
    # applicationWillFinishLaunching, before the API server starts) must be suppressed.
    argv += [
        "--args", "-suite", identity.suite, "-EnableAPIServer", "YES",
        "-OpenNoWindowsAtStartup", "YES", "-openNewWindowAtStartup", "NO",
        "-runJobsInServers", "NO", "-SUEnableAutomaticChecks", "NO",
        "-SUHasLaunchedBefore", "YES", "-PromptOnQuit", "NO",
        "-moveToApplicationsFolderAlertSuppress", "YES",
        "-New Bookmarks", private_profiles(shell), "-Default Bookmark Guid", PROFILE_GUID,
    ]  # fmt: skip
    return argv


def sdk_env(identity: Identity) -> dict[str, str]:
    """What the stock SDK reads in the CONNECTING process: socket dir and cookie target."""
    return {
        "HOME": str(identity.home),
        "IT2_SUITE": identity.suite,
        "IT2_APP_PATH": str(identity.copy),
    }


def parse_pids(text: str) -> list[int]:
    return [int(line) for line in text.split("\n") if line.strip()]


def instance_pid(commands: Mapping[int, str], copy: Path) -> int:
    """Exactly one candidate whose executable lives inside the copied bundle."""
    executable = str(copy / "Contents" / "MacOS" / "iTerm2")
    inside = [pid for pid, command in commands.items() if command.startswith(executable)]
    if not inside:
        raise ProbeError(f"no private iTerm2 process runs from {copy}")
    if len(inside) > 1:
        raise ProbeError(f"{len(inside)} processes run from {copy}: {sorted(inside)}")
    return inside[0]


def pids_with_env(ps_output: str, needle: str) -> list[int]:
    """`ps -E` lines are `<pid> <command> <ENV=...>`; match a whole `KEY=value` token."""
    found = []
    for line in ps_output.split("\n"):
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and needle in parts[1:]:
            found.append(int(parts[0]))
    return found


def server_processes(ps_output: str, identity: Identity) -> list[int]:
    """`ps -axo pid=,command=` lines. iTerm2 copies iTermServer to
    `<HOME>/Library/Application Support/<suite>/iTermServer-<version>` and daemonizes it
    (it re-parents to launchd), so match the private HOME path, never a ppid."""
    support = identity.home / "Library" / "Application Support" / identity.suite
    prefix = str(support / "iTermServer")
    found = []
    for line in ps_output.split("\n"):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith(prefix):
            found.append(int(parts[0]))
    return found


def leftover_domains(domains_output: str, suite: str) -> list[str]:
    present = {name.strip() for name in domains_output.replace("\n", ",").split(",")}
    return [name for name in (suite, f"{suite}.private") if name in present]


def preference_plists(identity: Identity) -> list[Path]:
    """The suite plists in the REAL ~/Library/Preferences (cfprefsd ignores the private HOME).
    `defaults delete` empties them but leaves the files, which keeps the domain listed."""
    prefs = Path.home() / "Library" / "Preferences"
    return [prefs / f"{identity.suite}.plist", prefs / f"{identity.suite}.private.plist"]


SDK_AUTH_VARS = ("ITERM2_COOKIE", "ITERM2_KEY")


@contextlib.contextmanager
def scoped_env(overrides: Mapping[str, str], *, scrub: tuple[str, ...] = ()) -> Iterator[None]:
    """Apply env overrides only around the SDK connect; never process-wide.

    `scrub` names variables the SDK WRITES inside the block (iterm2.auth.authenticate sets
    ITERM2_COOKIE/ITERM2_KEY and Connection.async_create never removes them on success);
    they return to their prior state - normally absent - on exit.
    """
    saved = {key: os.environ.get(key) for key in (*overrides, *scrub)}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def require_tool(name: str, hint: str) -> str:
    found = shutil.which(name)
    if found is None:
        pytest.fail(f"the native tier requires {name}: {hint}")
    return found


def require_sdk():
    if sys.platform != "darwin":
        pytest.fail("the native tier runs only on macOS (just iterm2-tests)")
    try:
        import iterm2
    except ImportError:
        pytest.fail("the native tier requires the iTerm2 Python SDK: uv sync")
    return iterm2


def _ps(*columns: str, pids: list[int] | None = None) -> str:
    argv = ["/bin/ps", "-axo", ",".join(f"{c}=" for c in columns)]
    if pids is not None:
        argv = ["/bin/ps", "-o", ",".join(f"{c}=" for c in columns), "-p", ",".join(map(str, pids))]
    return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False).stdout


def _pgrep(pattern: str) -> list[int]:
    cp = subprocess.run(
        ["/usr/bin/pgrep", "-f", "--", pattern],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return parse_pids(cp.stdout)


def _find_pid(identity: Identity) -> int | None:
    pids = _pgrep(f"-suite {identity.suite}")
    if not pids:
        return None
    commands = {}
    for line in _ps("pid", "command", pids=pids).split("\n"):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            commands[int(parts[0])] = parts[1]
    return instance_pid(commands, identity.copy)


def _wait_for(probe: Callable[[], object], description: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.2)
    raise ProbeError(f"timed out after {timeout:g}s waiting for {description}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)


@dataclass
class PrivateITerm:
    identity: Identity
    pid: int

    @classmethod
    def start(cls, checkout: Path, *, source: Path = SOURCE_APP) -> PrivateITerm:
        if sys.platform != "darwin":
            raise ProbeError("the native tier runs only on macOS")
        if not (source / "Contents" / "MacOS" / "iTerm2").is_file():
            raise ProbeError(f"install iTerm2 at {source}: brew install --cask iterm2")
        shell = shutil.which("fish")
        if shell is None:
            raise ProbeError("the private default profile runs fish: brew install fish")
        identity = new_identity(checkout, secrets.token_hex(6))
        identity.root.mkdir(mode=0o700)
        try:
            (identity.home / ".config" / "fish" / "conf.d").mkdir(parents=True)
            (identity.home / ".omc").mkdir()
            (identity.home / "Library" / "Application Support").mkdir(parents=True)
            # ditto keeps the code signature, bundle id and xattrs, so the existing
            # Automation consent for iTerm2 carries over to the copy (spec §7 step 1).
            subprocess.run(
                ["/usr/bin/ditto", str(source), str(identity.copy)], check=True, timeout=300
            )
            subprocess.run(open_argv(identity, shell), env=launch_env(), check=True, timeout=60)
            pid = _wait_for(lambda: _find_pid(identity), "the private iTerm2 process", 30)
            _wait_for(lambda: identity.socket.exists() or None, "the private API socket", 60)
        except BaseException:
            cls(identity, 0).close()
            raise
        return cls(identity, pid)

    def matching_pids(self) -> list[int]:
        by_argv = set(_pgrep(f"-suite {self.identity.suite}"))
        by_env = set(pids_with_env(_ps_env(), f"HOME={self.identity.home}"))
        return sorted((by_argv | by_env) - {os.getpid()})

    def close(self) -> list[str]:
        """Spec §7 step 6. Returns leftover notes; empty means a clean teardown."""
        if self.pid:
            _kill(self.pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while _alive(self.pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if _alive(self.pid):
                _kill(self.pid, signal.SIGKILL)
        for pid in server_processes(_ps("pid", "command"), self.identity):
            _kill(pid, signal.SIGKILL)
        for pid in self.matching_pids():
            _kill(pid, signal.SIGKILL)
        # Only after the PID is gone: cfprefsd could otherwise re-flush the domain.
        for domain in (self.identity.suite, f"{self.identity.suite}.private"):
            # macOS writes suite prefs to the REAL ~/Library/Preferences regardless of HOME.
            subprocess.run(
                ["/usr/bin/defaults", "delete", domain],
                capture_output=True,
                timeout=10,
                check=False,
            )
        for plist in preference_plists(self.identity):
            plist.unlink(missing_ok=True)
        shutil.rmtree(self.identity.root, ignore_errors=True)
        time.sleep(0.2)
        leftovers = []
        if self.identity.root.exists():
            leftovers.append(f"path {self.identity.root}")
        for pid in self.matching_pids():
            leftovers.append(f"pid {pid}")
        leftovers += [
            f"iTermServer {pid} (runJobsInServers ignored)"
            for pid in server_processes(_ps("pid", "command"), self.identity)
        ]
        domains = subprocess.run(
            ["/usr/bin/defaults", "domains"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
        leftovers += [f"domain {name}" for name in leftover_domains(domains, self.identity.suite)]
        return leftovers


def _ps_env() -> str:
    # `-E` appends each (own-user) process's environment to its command line.
    return subprocess.run(
        ["/bin/ps", "-E", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout


async def connect(iterm2, instance: PrivateITerm):
    """Stock SDK connect under the scoped env; a TCP fallback is a failure (§7 step 4)."""
    if not instance.identity.socket.exists():
        pytest.fail(
            f"private socket missing at {instance.identity.socket}; the SDK would fall back to "
            "TCP localhost:1912 (the user's iTerm2) — refusing"
        )
    iterm2.app.invalidate_app()  # SDK 2.24 caches App process-wide across closed loops
    with scoped_env(sdk_env(instance.identity), scrub=SDK_AUTH_VARS):
        connection = await asyncio.wait_for(iterm2.Connection.async_create(), 20)
    peer = getattr(connection.websocket, "remote_address", None)
    if isinstance(peer, tuple):
        pytest.fail(f"SDK connected over TCP {peer}, not the private socket — refusing")
    app = await asyncio.wait_for(iterm2.async_get_app(connection), 20)
    if app is None:
        pytest.fail("iTerm2 SDK returned no App for the private instance")
    return connection, app


async def ensure_window(iterm2, connection, app, fish: str):
    """The private instance starts with no window; create one running `fish -i`."""
    if app.terminal_windows:
        return app.terminal_windows[0]
    window = await asyncio.wait_for(
        iterm2.Window.async_create(connection, command=shlex.join([fish, "-i"])), 30
    )
    if window is None:
        pytest.fail("iTerm2 did not create the private window")
    return window


async def until(description: str, predicate, *, timeout: float = 30):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(0.35)
    pytest.fail(f"timed out waiting for {description}")


async def verify_launch(iterm2, instance: PrivateITerm) -> None:
    """Spec §7 step 3: the argument-domain flags took effect."""
    _connection, app = await connect(iterm2, instance)
    if app.terminal_windows:
        raise ProbeError(
            "-OpenNoWindowsAtStartup YES did not take effect: the instance has windows"
        )


def provision_hook(home: Path) -> Path:
    """The private HOME's conf.d receives the packaged hook (spec §7 step 2)."""
    from omc.fish_integration import fish_hook_path

    target = home / ".config" / "fish" / "conf.d" / "omc-title.fish"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fish_hook_path(), target)
    return target


def init_repo(path: Path, branch: str) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    git = ["git", "-C", str(path)]
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "t@example.com"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run([*git, "config", key, value], check=True)
    (path / "README").write_text("disposable\n")
    subprocess.run([*git, "add", "README"], check=True)
    subprocess.run([*git, "commit", "-qm", "initial"], check=True)
    return path


def _exclusive_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def claude_private_auth(home: Path, repo: Path, claude: str) -> Path | None:
    """~/.claude.json is HOME-relative: generate onboarding + trust in the private HOME.

    Returns None when the first-party Keychain login is still visible from that HOME;
    otherwise a 0600 fish file exporting CLAUDE_CODE_OAUTH_TOKEN (the caller sources it
    inside the tab and unlinks it on teardown). The token never appears in argv or logs.
    """
    import json

    profile = home / ".claude.json"
    profile.write_text(
        json.dumps(
            {
                "hasCompletedOnboarding": True,
                "projects": {str(repo): {"hasTrustDialogAccepted": True}},
            }
        )
    )
    profile.chmod(0o600)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
    }
    env["HOME"] = str(home)
    status = subprocess.run(
        [claude, "auth", "status", "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        parsed = json.loads(status.stdout)
    except json.JSONDecodeError:
        parsed = {}
    if (
        status.returncode == 0
        and parsed.get("loggedIn")
        and parsed.get("authMethod") == "claude.ai"
    ):
        return None
    keychain = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True, timeout=10, check=False,
    )  # fmt: skip
    try:
        token = json.loads(keychain.stdout)["claudeAiOauth"]["accessToken"]
    except (ValueError, KeyError, TypeError):
        token = ""
    if keychain.returncode or not isinstance(token, str) or not token:
        pytest.fail("native Claude profile needs first-party login: run claude auth login")
    auth_file = home / ".claude-auth.fish"
    _exclusive_text(auth_file, f"set -gx CLAUDE_CODE_OAUTH_TOKEN {shlex.quote(token)}\n")
    return auth_file


def close_reporting(instance: PrivateITerm, pending: BaseException | None) -> None:
    """Close `instance` without masking `pending`: its errors and leftovers become notes.

    With nothing pending, a teardown exception propagates and leftovers fail the session.
    """
    try:
        leftovers = instance.close()
    except Exception as exc:
        if pending is None:
            raise
        pending.add_note(f"private iTerm2 teardown also failed: {type(exc).__name__}: {exc}")
        return
    if not leftovers:
        return
    message = "private iTerm2 teardown left: " + "; ".join(leftovers)
    if pending is None:
        pytest.fail(message)
    pending.add_note(message)
