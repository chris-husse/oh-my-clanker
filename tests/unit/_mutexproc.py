"""Real-subprocess drivers for watch/start mutex tests, and the watch tests' repos and
stubbed GitNexus contexts. Contention MUST cross a process boundary: flock conflicts are
per open-file-description, and filelock within one process can behave reentrantly —
in-process 'contention' would pass vacuously."""

from __future__ import annotations

import fcntl
import json
import os
import select
import shlex
import stat
import subprocess
import sys
import time

from omc.toolctx import ToolContext

from ._stubs import host_bin

_WATCH_DRIVER = (
    "import sys\n"
    "from omc.config.schema import Config\n"
    "from omc.toolctx import ToolContext\n"
    "from omc.watch import run_watch\n"
    "sys.exit(run_watch(ToolContext.from_env(), Config(),\n"
    "    interval=float(sys.argv[1]), once=sys.argv[2] == '1',\n"
    "    clear_mutex=sys.argv[3] == '1'))\n"
)


def spawn_watch(repo, env, *, once=False, clear=False, interval=1, stderr_path):
    """Launch a REAL `run_watch` in a child process; stderr streams to a file
    the test polls (pipes would deadlock a long-lived loop)."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WATCH_DRIVER,
            str(interval),
            "1" if once else "0",
            "1" if clear else "0",
        ],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=open(stderr_path, "w"),
    )


def run_watch_once(repo, env, *, clear=False, timeout=None):
    """A single-tick watch run to completion; captures output."""
    return subprocess.run(
        [sys.executable, "-c", _WATCH_DRIVER, "1", "1", "1" if clear else "0"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_ALIVE_HOLDER = (
    "import fcntl, os, sys, time\n"
    "from omc.toolctx import ToolContext\n"
    "from omc.watchlock import alive_lock_path, hold_alive\n"
    "path = alive_lock_path(ToolContext.from_env(), cwd=sys.argv[1])\n"
    "if sys.argv[2] == '1':\n"
    "    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)\n"
    "    fcntl.flock(fd, fcntl.LOCK_SH)\n"
    "else:\n"
    "    fd = hold_alive(path, clear=False)\n"
    "print('ready', flush=True)\n"
    "time.sleep(3600)\n"
)


def hold_alive_lock(repo, *, shared=False, timeout=15.0):
    """A child process holding `repo`'s watch alive lock: exclusive via
    `hold_alive` ("a watch is running"), or SHARED as a probe in flight.
    Returns once the child printed `ready`; killing it releases the lock."""
    p = subprocess.Popen(
        [sys.executable, "-c", _ALIVE_HOLDER, str(repo), "1" if shared else "0"],
        stdout=subprocess.PIPE,
        text=True,
    )
    ready, _, _ = select.select([p.stdout], [], [], timeout)
    line = p.stdout.readline() if ready else ""
    p.stdout.close()  # the child writes nothing after `ready`
    if line.strip() != "ready":
        p.kill()
        p.wait()
        raise AssertionError(f"alive-lock holder not ready within {timeout}s: {line!r}")
    return p


def install_slow_hook(repo, marker, sleep_s):
    """post-watch hook that proves it started (marker file) then sleeps —
    keeps the watch BUSY deterministically after an action tick."""
    hook = repo / ".omc" / "hooks" / "post-watch.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!{host_bin('bash')}\ntouch {marker}\nsleep {sleep_s}\n")


def wait_for(pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s: {pred}")


def flock_granted(path) -> bool:
    """True when some process holds a flock on `path`, read from /proc/locks: a look
    that takes no lock, so it can never be the holder a starting watch bails on.
    Matched by inode alone (stat's device can differ from the kernel's on btrfs).
    Without /proc/locks (macOS) it falls back to the locking probe."""
    try:
        with open("/proc/locks") as f:
            table = f.read()
    except FileNotFoundError:
        return not flock_free(path)
    inode = f":{os.stat(path).st_ino}"
    for line in table.splitlines():
        fields = line.split()
        if "->" not in fields and "FLOCK" in fields and any(x.endswith(inode) for x in fields):
            return True
    return False


def flock_free(path) -> bool:
    """True when nothing holds an exclusive flock on `path` (probe from THIS
    process — flock conflicts apply across fds, so this sees other holders)."""
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(f, fcntl.LOCK_UN)
        return True


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def repo_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    # bare HEAD -> main so later clones check out main (init.defaultBranch-proof)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("one\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)  # independent of init.defaultBranch
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    # The generated dirs are gitignored in real projects; excluding them here
    # keeps the tests' `git add .` from ever committing a seeded snapshot.
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text(".gitnexus/\n.omc/docs/\n")
    _seed_fresh_index(repo)
    return origin, repo


def _seed_fresh_index(repo, branch="main"):
    """A metadata file that snapshot_freshness judges fresh at HEAD (index side)."""
    d = repo / ".gitnexus"
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "branch": branch,
                "lastCommit": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "repoPath": str(repo),
            }
        )
    )


def push_remote_commit(origin, tmp_path):
    """Advance origin/main from a second clone (simulates a teammate's push)."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("config", "user.email", "o@o", cwd=other)
    _git("config", "user.name", "o", cwd=other)
    (other / "new.txt").write_text("new\n")
    _git("add", ".", cwd=other)
    _git("commit", "-qm", "remote change", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)


def stub_tools():
    """Real git/mkdir/rm by resolved absolute path (stubs may run on a restricted PATH)."""
    return (shlex.quote(host_bin(t)) for t in ("git", "mkdir", "rm"))


def ctx_with_node_stub(tmp_path, home):
    """Real git on PATH + recording `node`/`wt`/`claude` stubs + a fake built CLI."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "node.calls"
    node = bindir / "node"
    # Side effects mirror what GitNexus really does, so refresh_knowledge's
    # recomputed verdict can flip to fresh. Restricted PATH: shell builtins and
    # absolute paths only; `echo ok` stays unconditional so ensure_gitnexus's
    # --version probe passes.
    git, mkdir, rm = stub_tools()
    node.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'case "$*" in\n'
        f'  *" clean --force") {rm} -rf .gitnexus ;;\n'
        f'  *" analyze --skip-agents-md --skip-skills"*) {mkdir} -p .gitnexus; '
        'printf \'{"branch":"main","lastCommit":"%s","repoPath":"%s"}\' '
        f'"$({git} rev-parse HEAD)" "$PWD" > .gitnexus/meta.json ;;\n'
        "esac\n"
        "echo ok\nexit 0\n"
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    for name in ("wt", "claude"):
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} 1.0"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake built CLI")
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "OMC_STAGE_ROOT": str(tmp_path / "stages"),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return ToolContext.from_env(env), calls


_LOCK_HOLDER = (
    "import sys, time\n"
    "from filelock import FileLock\n"
    # Bind the lock: an unreferenced FileLock is GC'd right after acquire() and
    # filelock's __del__ releases the flock before the sleep, so the child would
    # never actually hold it. The whole point (see module docstring) is a real
    # holder for the duration of `seconds`.
    "_lock = FileLock(sys.argv[1])\n"
    "_lock.acquire()\n"
    "print('held', flush=True)\n"
    "if sys.argv[2] == 'until-stdin':\n"
    "    sys.stdin.readline()\n"
    "else:\n"
    "    time.sleep(float(sys.argv[2]))\n"
)


def hold_in_subprocess(path, seconds):
    """Acquire `path` in a child process; None holds until stdin closes."""
    p = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _LOCK_HOLDER,
            str(path),
            "until-stdin" if seconds is None else str(seconds),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p.stdout.readline().strip() == "held"
    return p


def analyze_side_effects(*, move_to: str | None = None, rogue_stamp: str | None = None):
    """GitNexus refuses a mismatched explicit label before writing anything."""
    git, mkdir, _ = stub_tools()
    move = (
        "if [ ! -f .git/analyze-moved ]; then : > .git/analyze-moved; "
        f"{git} switch -q {shlex.quote(move_to)} || exit 90; fi; "
        if move_to
        else ""
    )
    return (
        move + 'label=""; while [ "$#" -gt 0 ]; do '
        'if [ "$1" = --branch ]; then shift; label="$1"; fi; shift; done; '
        f"actual=$({git} rev-parse --abbrev-ref HEAD); "
        'if [ -n "$label" ] && [ "$label" != "$actual" ]; then '
        'printf \'%s\\n\' "--branch \\"$label\\" does not match the checked-out branch '
        '\\"$actual\\". Check out \\"$label\\" before indexing it, or omit --branch '
        'to index the current branch." >&2; exit 1; fi; '
        "stamp=${label:-$actual}; "
        + (f"stamp={shlex.quote(rogue_stamp)}; " if rogue_stamp else "")
        + f"{mkdir} -p .gitnexus; "
        'printf \'{"branch":"%s","lastCommit":"%s","repoPath":"%s"}\' '
        f'"$stamp" "$({git} rev-parse HEAD)" "$PWD" > .gitnexus/meta.json'
    )


def ctx_with_healing_node_stub(
    tmp_path,
    home,
    *,
    clean_removes=True,
    move_to: str | None = None,
    rogue_stamp: str | None = None,
):
    """Faithful pinned analyze, with one-shot checkout movement or rogue writes."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "node.calls"
    _, _, rm = stub_tools()
    clean_cmd = f"{rm} -rf .gitnexus" if clean_removes else ":"
    node = bindir / "node"
    node.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'case "$*" in\n'
        f'  *" clean --force") {clean_cmd} ;;\n'
        '  *" analyze --skip-agents-md --skip-skills"*) '
        + analyze_side_effects(move_to=move_to, rogue_stamp=rogue_stamp)
        + " ;;\n"
        "esac\n"
        "echo ok\nexit 0\n"
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    for name in ("wt", "claude"):
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} 1.0"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake built CLI")
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "OMC_STAGE_ROOT": str(tmp_path / "stages"),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return ToolContext.from_env(env), calls


def ctx_with_shared_store_node_stub(
    tmp_path, home, *, gc_exit=0, gc_output="collected", analyze_fails=False
):
    """Simulate pointer routing and local registration on a restricted PATH."""
    ctx, calls = ctx_with_healing_node_stub(tmp_path, home)
    node = tmp_path / "bin" / "node"
    shared = tmp_path / "shared-store"
    git, mkdir, rm = stub_tools()
    node.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s\\n" "$GITNEXUS_SHARED_STORE" "$*" >> "{calls}"\n'
        'shift\ncase "$*" in\n'
        '  "clean --gc --force") '
        f'{rm} -rf "{shared}"; printf %s {shlex.quote(gc_output)}; exit {gc_exit} ;;\n'
        '  "clean --force") '
        'if [ "$GITNEXUS_SHARED_STORE" = off ] && [ ! -f .gitnexus/store.json ]; then '
        f"{rm} -rf .gitnexus; else "
        f'{rm} -rf "{shared}" .gitnexus/store.json; fi ;;\n'
        '  "analyze --skip-agents-md --skip-skills"*) '
        + ('echo "registration failed"; exit 9; ' if analyze_fails else "")
        + f"{mkdir} -p .gitnexus; "
        'if [ "$GITNEXUS_SHARED_STORE" = off ]; then '
        f"{rm} -f .gitnexus/store.json; target=.gitnexus/meta.json; else "
        f'{mkdir} -p "{shared}"; target="{shared}/meta.json"; '
        f"printf '%s' '{{\"storePath\":\"{shared}\"}}' > .gitnexus/store.json; fi; "
        'printf \'{"branch":"main","lastCommit":"%s","repoPath":"%s"}\' '
        f'"$({git} rev-parse HEAD)" "$PWD" > "$target" ;;\n'
        "esac\necho ok\n"
    )
    ctx.env = {**ctx.env, "PATH": str(tmp_path / "bin"), "GITNEXUS_SHARED_STORE": "on"}
    ctx.git_bin = host_bin("git")
    return ctx, calls
